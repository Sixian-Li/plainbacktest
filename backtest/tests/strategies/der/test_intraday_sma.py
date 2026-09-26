from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.intraday_sma import (
    BUY_FORCED_REENTRY,
    BUY_SHORT_RECOVERY,
    SELL_COST_STOP,
    SELL_FAST_DROP,
    SELL_SMA200_CROSS,
    SELL_SLOW_TREND,
    IntradaySmaSpec,
    OrderPlan,
    Trigger,
    build_order_plan,
    evaluate_order_plan,
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.metrics import pybroker_daily_state


def bars(count: int = 520, *, symbol: str = "AAA") -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-01", periods=count)
    close = np.linspace(90.0, 130.0, count) + np.sin(np.arange(count) / 9.0) * 4.0
    open_ = close + np.cos(np.arange(count) / 7.0)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates,
            "open": open_,
            "high": np.maximum(open_, close) + 2.0,
            "low": np.minimum(open_, close) - 2.0,
            "close": close,
            "volume": np.full(count, 1_000_000.0),
        }
    )


class TriggerEvaluationTest(unittest.TestCase):
    def test_disabled_fast_drop_never_enters_the_order_plan(self) -> None:
        spec = IntradaySmaSpec(fast_drop_enabled=False)
        row = pd.Series(
            {
                "date": pd.Timestamp("2024-01-02"),
                "eligible_sell_fast_streak": True,
                "trigger_sell_fast_drop": 99.0,
            }
        )
        plan = build_order_plan(
            row,
            spec,
            is_long=True,
            cost_basis=100.0,
            last_sell_price=None,
            disabled_signals=frozenset(
                {SELL_COST_STOP, SELL_SLOW_TREND, SELL_SMA200_CROSS}
            ),
        )
        self.assertEqual(plan.triggers, ())

    def test_sell_gap_uses_open_and_records_every_crossed_rule(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="sell",
            triggers=(
                Trigger(SELL_COST_STOP, 99.0, "cost basis × 98.5%"),
                Trigger(SELL_SLOW_TREND, 97.0, "slow trend"),
            ),
        )
        event = evaluate_order_plan(plan, open_=95, high=101, low=94, close=100)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.fill_source, "open_gap")
        self.assertEqual(event.fill_price, 95.0)
        self.assertEqual(event.primary_signal, SELL_COST_STOP)
        self.assertEqual(event.matched_signals, (SELL_COST_STOP, SELL_SLOW_TREND))

    def test_sell_intraday_uses_highest_touched_threshold(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="sell",
            triggers=(
                Trigger(SELL_FAST_DROP, 98.0, "fast"),
                Trigger(SELL_COST_STOP, 99.0, "stop"),
            ),
        )
        event = evaluate_order_plan(plan, open_=101, high=103, low=96, close=97)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.fill_source, "intraday_trigger")
        self.assertEqual(event.fill_price, 99.0)
        self.assertEqual(event.primary_signal, SELL_COST_STOP)
        self.assertEqual(event.matched_signals, (SELL_COST_STOP,))

    def test_buy_gap_and_intraday_choose_lowest_upward_threshold(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="buy",
            triggers=(
                Trigger(BUY_FORCED_REENTRY, 101.5, "forced"),
                Trigger(BUY_SHORT_RECOVERY, 103.0, "short"),
            ),
        )
        gap = evaluate_order_plan(plan, open_=104, high=105, low=100, close=102)
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.fill_price, 104.0)
        self.assertEqual(gap.matched_signals, (BUY_FORCED_REENTRY, BUY_SHORT_RECOVERY))
        intraday = evaluate_order_plan(plan, open_=100, high=104, low=99, close=103)
        self.assertIsNotNone(intraday)
        assert intraday is not None
        self.assertEqual(intraday.fill_price, 101.5)
        self.assertEqual(intraday.primary_signal, BUY_FORCED_REENTRY)

    def test_untouched_trigger_does_not_fill(self) -> None:
        plan = OrderPlan(
            date=pd.Timestamp("2024-01-02"),
            side="sell",
            triggers=(Trigger(SELL_COST_STOP, 90.0, "stop"),),
        )
        self.assertIsNone(evaluate_order_plan(plan, open_=100, high=101, low=91, close=95))


class IndicatorCausalityTest(unittest.TestCase):
    def test_current_bar_ohlc_cannot_change_predeclared_trigger_prices(self) -> None:
        spec = IntradaySmaSpec()
        original = bars()
        target = original.index[-1]
        first = prepare_intraday_sma_data(original, spec)
        changed = original.copy()
        changed.loc[target, ["open", "high", "low", "close"]] = [250, 270, 40, 230]
        second = prepare_intraday_sma_data(changed, spec)
        trigger_columns = [
            column
            for column in first
            if column.startswith("trigger_") or column.startswith("eligible_")
        ]
        for column in trigger_columns:
            left = first.loc[target, column]
            right = second.loc[target, column]
            if pd.isna(left):
                self.assertTrue(pd.isna(right), column)
            else:
                self.assertAlmostEqual(float(left), float(right), places=12, msg=column)

    def test_dynamic_sma_crossing_formula_equals_prior_sum_over_n_minus_one(self) -> None:
        spec = IntradaySmaSpec()
        frame = bars()
        prepared = prepare_intraday_sma_data(frame, spec)
        target = len(frame) - 1
        expected = frame.loc[target - 199 : target - 1, "close"].sum() / 199
        self.assertAlmostEqual(
            float(prepared.loc[target, "trigger_sell_sma200_cross"]),
            float(expected),
            places=12,
        )

    def test_fast_drop_price_requires_all_three_short_smas_below_threshold(self) -> None:
        spec = IntradaySmaSpec(
            c_fast_derivative_pct=-0.20,
            fast_derivative_mode="all_short_smas",
        )
        frame = bars()
        prepared = prepare_intraday_sma_data(frame, spec)
        target = len(frame) - 1
        individual_triggers = []
        for window in spec.f_short_sma_windows:
            prior_sma = float(prepared.loc[target - 1, f"sma{window}"])
            prior_sum = float(frame.loc[target - window + 1 : target - 1, "close"].sum())
            expected = window * prior_sma * 0.998 - prior_sum
            observed = float(prepared.loc[target, f"trigger_sell_fast_drop_sma{window}"])
            self.assertAlmostEqual(observed, expected, places=12)
            provisional_sma = (prior_sum + observed) / window
            derivative = (provisional_sma / prior_sma - 1.0) * 100.0
            self.assertAlmostEqual(derivative, -0.20, places=10)
            individual_triggers.append(observed)
        self.assertAlmostEqual(
            float(prepared.loc[target, "trigger_sell_fast_drop"]),
            min(individual_triggers),
            places=12,
        )


class IntradayLedgerGoldenTest(unittest.TestCase):
    def test_flat_start_can_observe_from_first_bar_without_trading_during_warmup(self) -> None:
        frame = bars(620)
        start = frame.loc[0, "date"]
        end = frame.loc[600, "date"]
        initial_cash = 100_000.0
        spec = IntradaySmaSpec(
            c_fast_derivative_pct=-0.20,
            fast_derivative_mode="all_short_smas",
        )
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_position="flat",
            initial_cash=initial_cash,
        )
        first = reference.orders.iloc[0]
        first_date = pd.Timestamp(first["date"])
        self.assertGreaterEqual(first_date, frame.loc[max(spec.f_short_sma_windows), "date"])
        before = reference.daily[reference.daily["date"] < first_date]
        self.assertEqual(pd.Timestamp(before.iloc[0]["date"]), pd.Timestamp(start))
        self.assertTrue((before["shares"] == 0).all())
        self.assertTrue((before["cash"] == initial_cash).all())

        result = run_pybroker_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_position="flat",
            initial_cash=initial_cash,
        )
        engine = frame[(frame["date"] >= result.engine_start) & (frame["date"] <= end)].copy()
        actual = pybroker_daily_state(result.pybroker_result, engine)
        actual = actual[actual["date"] >= pd.Timestamp(start)].reset_index(drop=True)
        np.testing.assert_allclose(actual["cash"], reference.daily["cash"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["shares"], reference.daily["shares"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], rtol=0, atol=1e-8)

    def test_pybroker_and_independent_ledger_match(self) -> None:
        frame = bars(560)
        start = frame.loc[300, "date"]
        end = frame.loc[540, "date"]
        initial_shares = 100.0
        spec = IntradaySmaSpec()
        result = run_pybroker_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_shares=initial_shares,
        )
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_shares=initial_shares,
        )
        engine = frame[(frame["date"] >= result.engine_start) & (frame["date"] <= end)].copy()
        actual = pybroker_daily_state(result.pybroker_result, engine)
        actual = actual[actual["date"] >= pd.Timestamp(start)].reset_index(drop=True)
        np.testing.assert_allclose(actual["cash"], reference.daily["cash"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["shares"], reference.daily["shares"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], rtol=0, atol=1e-8)
        observed_orders = result.pybroker_result.orders.reset_index(drop=True)
        expected_orders = reference.orders.reset_index(drop=True)
        self.assertEqual(observed_orders["type"].tolist(), expected_orders["type"].tolist())
        self.assertEqual(
            pd.to_datetime(observed_orders["date"]).tolist(),
            pd.to_datetime(expected_orders["date"]).tolist(),
        )
        np.testing.assert_allclose(
            observed_orders["fill_price"].to_numpy(float),
            expected_orders["fill_price"].to_numpy(float),
            rtol=0,
            atol=1e-9,
        )
        self.assertLessEqual(reference.daily.groupby("date")["executed"].apply(lambda x: (x != 0).sum()).max(), 1)

    def test_flat_start_waits_for_first_ordinary_buy_and_matches_pybroker(self) -> None:
        frame = bars(620)
        start = frame.loc[300, "date"]
        end = frame.loc[600, "date"]
        initial_cash = 100_000.0
        spec = IntradaySmaSpec(
            c_fast_derivative_pct=-0.20,
            fast_derivative_mode="all_short_smas",
        )
        reference = run_reference_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_position="flat",
            initial_cash=initial_cash,
        )
        self.assertGreater(len(reference.orders), 0)
        first = reference.orders.iloc[0]
        self.assertEqual(first["type"], "buy")
        self.assertNotEqual(first["primary_signal"], "INITIAL_SEED")
        first_date = pd.Timestamp(first["date"])
        before = reference.daily[reference.daily["date"] < first_date]
        self.assertTrue((before["shares"] == 0).all())
        self.assertTrue((before["cash"] == initial_cash).all())

        result = run_pybroker_intraday_sma(
            frame,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_position="flat",
            initial_cash=initial_cash,
        )
        engine = frame[(frame["date"] >= result.engine_start) & (frame["date"] <= end)].copy()
        actual = pybroker_daily_state(result.pybroker_result, engine)
        actual = actual[actual["date"] >= pd.Timestamp(start)].reset_index(drop=True)
        np.testing.assert_allclose(actual["cash"], reference.daily["cash"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["shares"], reference.daily["shares"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], rtol=0, atol=1e-8)


if __name__ == "__main__":
    unittest.main()
