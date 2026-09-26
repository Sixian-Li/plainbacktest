from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.intraday_sma_threshold import (
    BUY_CORRECTION,
    BUY_SMA200_THRESHOLD,
    SELL_CORRECTION,
    IntradaySmaThresholdSpec,
    OrderPlan,
    Trigger,
    build_order_plan,
    dynamic_sma_threshold,
    evaluate_order_plan,
    prepare_intraday_threshold_data,
    run_pybroker_intraday_threshold,
    run_reference_intraday_threshold,
)
from quantkit.intraday_sma_threshold_search import exhaustive_cases, screen_cases
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check
from scripts.analyze_intraday_sma200_threshold_grid import performance_figure


def bars(count: int = 180, *, window: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=count)
    close = 100.0 + np.sin(np.arange(count) / 4.0) * 12.0 + np.arange(count) * 0.025
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 3.0
    low = np.minimum(open_, close) - 3.0
    return pd.DataFrame(
        {
            "symbol": "AAA",
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


class DynamicThresholdTest(unittest.TestCase):
    def test_solved_price_puts_provisional_sma_exactly_on_buffer(self) -> None:
        prior = 19_900.0
        price = dynamic_sma_threshold(prior, window=200, multiplier=1.02)
        provisional_sma = (prior + price) / 200.0
        self.assertAlmostEqual(price, provisional_sma * 1.02, places=12)

    def test_current_bar_cannot_change_predeclared_trigger(self) -> None:
        frame = bars()
        first = prepare_intraday_threshold_data(frame, 5)
        changed = frame.copy()
        changed.loc[len(changed) - 1, ["open", "high", "low", "close"]] = [200, 230, 20, 180]
        second = prepare_intraday_threshold_data(changed, 5)
        target = len(frame) - 1
        for column in ("prior_close", "prior_sma", "prior_sum"):
            self.assertAlmostEqual(float(first.loc[target, column]), float(second.loc[target, column]))
        spec = IntradaySmaThresholdSpec(a_pct=5, b_pct=2, window=5)
        left = build_order_plan(first.loc[target], spec, is_long=False, cost_basis=None, last_sell_price=None)
        right = build_order_plan(second.loc[target], spec, is_long=False, cost_basis=None, last_sell_price=None)
        self.assertEqual(left.triggers, right.triggers)

    def test_ordinary_buy_requires_prior_below_boundary(self) -> None:
        row = pd.Series(
            {
                "date": pd.Timestamp("2024-01-02"),
                "prior_sum": 400.0,
                "prior_close": 120.0,
                "prior_sma": 100.0,
            }
        )
        plan = build_order_plan(
            row,
            IntradaySmaThresholdSpec(a_pct=0, b_pct=2, correction_pct=None, window=5),
            is_long=False,
            cost_basis=None,
            last_sell_price=None,
        )
        self.assertEqual(plan.triggers, ())


class TriggerExecutionTest(unittest.TestCase):
    def test_intraday_buy_uses_first_upward_line_and_applies_cost(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="buy",
            triggers=(
                Trigger(BUY_SMA200_THRESHOLD, 110.0, "sma"),
                Trigger(BUY_CORRECTION, 105.0, "correction"),
            ),
            cost_bps=5.0,
            cost_basis_before=None,
            last_sell_price_before=100.0,
        )
        event = evaluate_order_plan(plan, open_=100, high=112, low=99, close=111)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.primary_signal, BUY_CORRECTION)
        self.assertEqual(event.raw_fill_price, 105.0)
        self.assertAlmostEqual(event.fill_price, 105.0 * 1.0005)

    def test_gap_fills_at_open_not_threshold(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="buy",
            triggers=(Trigger(BUY_SMA200_THRESHOLD, 102.0, "sma"),),
            cost_bps=0.0,
            cost_basis_before=None,
            last_sell_price_before=None,
        )
        event = evaluate_order_plan(plan, open_=105, high=108, low=104, close=107)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.fill_source, "open_gap")
        self.assertEqual(event.theoretical_trigger, 102.0)
        self.assertEqual(event.raw_fill_price, 105.0)

    def test_sell_correction_is_anchored_to_effective_buy_fill(self) -> None:
        row = pd.Series(
            {
                "date": pd.Timestamp("2024-01-02"),
                "prior_sum": 400.0,
                "prior_close": 100.0,
                "prior_sma": 100.0,
            }
        )
        effective_buy = 105.0 * 1.0005
        plan = build_order_plan(
            row,
            IntradaySmaThresholdSpec(a_pct=20, b_pct=0, correction_pct=5, window=5, cost_bps=5),
            is_long=True,
            cost_basis=effective_buy,
            last_sell_price=None,
        )
        correction = next(trigger for trigger in plan.triggers if trigger.signal == SELL_CORRECTION)
        self.assertAlmostEqual(correction.price, effective_buy * 0.95)

    def test_asymmetric_corrections_can_be_enabled_independently(self) -> None:
        row = pd.Series(
            {
                "date": pd.Timestamp("2024-01-02"),
                "prior_sum": 400.0,
                "prior_close": 100.0,
                "prior_sma": 100.0,
            }
        )
        buy_only = IntradaySmaThresholdSpec(
            a_pct=20,
            b_pct=0,
            correction_buy_pct=5,
            window=5,
        )
        long_plan = build_order_plan(
            row,
            buy_only,
            is_long=True,
            cost_basis=105.0,
            last_sell_price=100.0,
        )
        flat_plan = build_order_plan(
            row,
            buy_only,
            is_long=False,
            cost_basis=None,
            last_sell_price=100.0,
        )
        self.assertNotIn(SELL_CORRECTION, [item.signal for item in long_plan.triggers])
        self.assertIn(BUY_CORRECTION, [item.signal for item in flat_plan.triggers])

        sell_only = IntradaySmaThresholdSpec(
            a_pct=20,
            b_pct=0,
            correction_sell_pct=7.5,
            window=5,
        )
        long_plan = build_order_plan(
            row,
            sell_only,
            is_long=True,
            cost_basis=105.0,
            last_sell_price=100.0,
        )
        flat_plan = build_order_plan(
            row,
            sell_only,
            is_long=False,
            cost_basis=None,
            last_sell_price=100.0,
        )
        self.assertIn(SELL_CORRECTION, [item.signal for item in long_plan.triggers])
        self.assertNotIn(BUY_CORRECTION, [item.signal for item in flat_plan.triggers])

    def test_symmetric_and_asymmetric_arguments_cannot_be_mixed(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            IntradaySmaThresholdSpec(
                a_pct=2,
                b_pct=-10,
                correction_pct=5,
                correction_sell_pct=7.5,
            )


class LedgerAndScreenTest(unittest.TestCase):
    def test_empty_order_ledgers_still_cross_check(self) -> None:
        class Result:
            orders = pd.DataFrame(columns=["type", "date", "shares", "fill_price"])
            trades = pd.DataFrame(
                columns=["entry_date", "exit_date", "entry", "exit", "shares", "pnl"]
            )

        class Reference:
            daily = pd.DataFrame({"cash": [100.0], "shares": [0.0], "equity": [100.0]})
            orders = pd.DataFrame(columns=["type", "date", "shares", "fill_price"])
            trades = pd.DataFrame(
                columns=["entry_date", "exit_date", "entry_price", "exit_price", "shares", "pnl"]
            )

        differences = cross_check(
            Result(),
            pd.DataFrame({"cash": [100.0], "shares": [0.0], "equity": [100.0]}),
            Reference(),
        )
        self.assertTrue(all(value == 0.0 for value in differences.values()))

    def test_pybroker_and_independent_ledger_match(self) -> None:
        frame = bars()
        start = frame.loc[20, "date"]
        end = frame.loc[160, "date"]
        spec = IntradaySmaThresholdSpec(
            a_pct=2.0,
            b_pct=1.0,
            correction_pct=5.0,
            window=5,
            cost_bps=5.0,
        )
        reference = run_reference_intraday_threshold(
            frame, spec, analysis_start=start, analysis_end=end, initial_cash=100_000
        )
        pybroker_run = run_pybroker_intraday_threshold(
            frame, spec, analysis_start=start, analysis_end=end, initial_cash=100_000
        )
        engine = frame[
            (frame["date"] >= pybroker_run.engine_start) & (frame["date"] <= end)
        ]
        actual = pybroker_daily_state(pybroker_run.pybroker_result, engine)
        actual = actual[actual["date"] >= pd.Timestamp(start)].reset_index(drop=True)
        differences = cross_check(
            pybroker_run.pybroker_result, actual, reference, tolerance=1e-6
        )
        self.assertLessEqual(max(differences.values()), 1e-6)
        self.assertGreater(len(reference.orders), 2)
        self.assertLessEqual(reference.orders.groupby("date").size().max(), 1)

    def test_compiled_screen_matches_reference_metrics(self) -> None:
        frame = bars()
        prepared = prepare_intraday_threshold_data(frame, 5)
        analysis = prepared.iloc[20:161].reset_index(drop=True)
        cases = exhaustive_cases([0.0, 2.0], [0.0, 1.0], [None, 5.0])
        screened = screen_cases(
            analysis,
            cases,
            window=5,
            cost_bps=5.0,
            initial_cash=100_000,
        )
        for _, observed in screened.iterrows():
            correction = None if pd.isna(observed["correction_pct"]) else float(observed["correction_pct"])
            spec = IntradaySmaThresholdSpec(
                a_pct=float(observed["a_pct"]),
                b_pct=float(observed["b_pct"]),
                correction_pct=correction,
                window=5,
                cost_bps=5.0,
            )
            reference = run_reference_intraday_threshold(
                frame,
                spec,
                analysis_start=analysis.iloc[0]["date"],
                analysis_end=analysis.iloc[-1]["date"],
                initial_cash=100_000,
            )
            metrics = calculate_metrics(
                reference.daily,
                reference.orders,
                reference.trades,
                initial_cash=100_000,
            )
            for name in (
                "final_equity", "total_return_pct", "cagr_pct", "sharpe",
                "max_drawdown_pct", "exposure_pct",
            ):
                self.assertAlmostEqual(float(observed[name]), float(metrics[name]), places=8, msg=name)
            self.assertEqual(int(observed["order_count"]), int(metrics["order_count"]))


class ReportPeriodTest(unittest.TestCase):
    def test_performance_benchmark_label_uses_configured_entry_date(self) -> None:
        dates = pd.to_datetime(["2000-01-03", "2000-01-04"])
        stable = pd.DataFrame(
            [
                {
                    "case_id": "CASE_1",
                    "correction_mode": "disabled",
                    "a_pct": 1.0,
                    "b_pct": 2.0,
                }
            ]
        )
        daily = pd.DataFrame(
            {"case_id": "CASE_1", "date": dates, "equity": [100_000.0, 101_000.0]}
        )
        benchmark = pd.DataFrame({"date": dates, "equity": [99_950.0, 100_500.0]})

        figure = performance_figure(
            daily,
            benchmark,
            stable,
            benchmark_entry_date="2000-01-03",
        )

        benchmark_traces = [
            trace for trace in figure.data if trace.meta and trace.meta.get("is_benchmark")
        ]
        self.assertEqual(len(benchmark_traces), 1)
        self.assertEqual(
            benchmark_traces[0].name,
            "QQQ Buy & Hold（2000-01-03 Open）",
        )


if __name__ == "__main__":
    unittest.main()
