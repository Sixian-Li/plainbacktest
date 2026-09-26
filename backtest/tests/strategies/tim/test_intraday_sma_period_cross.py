from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.intraday_sma_period_cross import (
    BUY_SMA_CROSS,
    FORCED_REBUY,
    SELL_SMA_CROSS,
    STOP_LOSS,
    SmaPeriodCrossSpec,
    build_order_plan,
    dynamic_sma_cross_price,
    evaluate_order_plan,
    run_pybroker_sma_period_cross,
    run_reference_sma_period_cross,
)
from quantkit.intraday_sma_period_cross_search import (
    analyze_period_surface,
    build_grid_cases,
    screen_period_cross_cases,
    screen_period_cross_cases_reference,
)
from quantkit.metrics import pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015/experiment.json"
)


def toy_market(periods: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=periods)
    index = np.arange(periods, dtype=float)
    close = 100.0 + 5.5 * np.sin(index / 3.1) + 0.045 * index
    open_ = np.r_[close[0], close[:-1]] + 0.35 * np.sin(index / 2.3)
    high = np.maximum(open_, close) + 1.75
    low = np.minimum(open_, close) - 1.75
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "QQQ",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(periods, 1_000_000.0),
        }
    )


def plan_row(
    *,
    prior_close: float,
    buy_prior_sma: float = 100.0,
    buy_prior_sum: float = 200.0,
    sell_prior_sma: float = 100.0,
    sell_prior_sum: float = 300.0,
) -> pd.Series:
    return pd.Series(
        {
            "date": pd.Timestamp("2020-02-03"),
            "prior_close": prior_close,
            "prior_sma_3": buy_prior_sma,
            "prior_sum_3": buy_prior_sum,
            "prior_sma_4": sell_prior_sma,
            "prior_sum_4": sell_prior_sum,
        }
    )


class PeriodCrossTimingTest(unittest.TestCase):
    def test_dynamic_price_puts_price_exactly_on_provisional_sma(self) -> None:
        prior = [97.0, 101.0, 103.0, 99.0]
        trigger = dynamic_sma_cross_price(sum(prior), window=5)
        self.assertAlmostEqual(trigger, np.mean([*prior, trigger]))

    def test_ordinary_buy_and_sell_are_true_crossing_events(self) -> None:
        spec = SmaPeriodCrossSpec(
            buy_window=3,
            sell_window=4,
            forced_rebuy_pct=5.0,
            stop_loss_pct=None,
        )
        eligible_buy = build_order_plan(
            plan_row(prior_close=100.0),
            spec,
            is_long=False,
            cost_basis=None,
            last_sell_price=None,
        )
        blocked_buy = build_order_plan(
            plan_row(prior_close=100.01),
            spec,
            is_long=False,
            cost_basis=None,
            last_sell_price=None,
        )
        eligible_sell = build_order_plan(
            plan_row(prior_close=100.0),
            spec,
            is_long=True,
            cost_basis=105.0,
            last_sell_price=None,
        )
        blocked_sell = build_order_plan(
            plan_row(prior_close=99.99),
            spec,
            is_long=True,
            cost_basis=105.0,
            last_sell_price=None,
        )
        self.assertIn(BUY_SMA_CROSS, [item.signal for item in eligible_buy.triggers])
        self.assertNotIn(BUY_SMA_CROSS, [item.signal for item in blocked_buy.triggers])
        self.assertIn(SELL_SMA_CROSS, [item.signal for item in eligible_sell.triggers])
        self.assertNotIn(SELL_SMA_CROSS, [item.signal for item in blocked_sell.triggers])

    def test_rebuy_and_stop_compete_using_effective_fill_anchors(self) -> None:
        spec = SmaPeriodCrossSpec(
            buy_window=3,
            sell_window=4,
            forced_rebuy_pct=3.0,
            stop_loss_pct=5.0,
            cost_bps=5.0,
        )
        flat_plan = build_order_plan(
            plan_row(prior_close=99.0, buy_prior_sum=210.0),
            spec,
            is_long=False,
            cost_basis=None,
            last_sell_price=100.0,
        )
        buy = evaluate_order_plan(flat_plan, open_=102.0, high=104.0, low=101.0, close=103.0)
        self.assertIsNotNone(buy)
        assert buy is not None
        self.assertEqual(buy.primary_signal, FORCED_REBUY)
        self.assertEqual(buy.fill_source, "intraday_trigger")
        self.assertAlmostEqual(buy.raw_fill_price, 103.0)
        self.assertAlmostEqual(buy.fill_price, 103.0 * 1.0005)

        long_plan = build_order_plan(
            plan_row(prior_close=101.0, sell_prior_sum=270.0),
            spec,
            is_long=True,
            cost_basis=buy.fill_price,
            last_sell_price=100.0,
        )
        sell = evaluate_order_plan(long_plan, open_=100.0, high=101.0, low=97.0, close=98.0)
        self.assertIsNotNone(sell)
        assert sell is not None
        self.assertEqual(sell.primary_signal, STOP_LOSS)
        self.assertAlmostEqual(sell.raw_fill_price, buy.fill_price * 0.95)
        self.assertAlmostEqual(sell.fill_price, sell.raw_fill_price * 0.9995)

    def test_overnight_cross_fills_at_open(self) -> None:
        spec = SmaPeriodCrossSpec(
            buy_window=3,
            sell_window=4,
            forced_rebuy_pct=5.0,
            stop_loss_pct=None,
            cost_bps=5.0,
        )
        plan = build_order_plan(
            plan_row(prior_close=99.0),
            spec,
            is_long=False,
            cost_basis=None,
            last_sell_price=None,
        )
        event = evaluate_order_plan(plan, open_=102.0, high=103.0, low=101.0, close=102.5)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.primary_signal, BUY_SMA_CROSS)
        self.assertEqual(event.fill_source, "open_gap")
        self.assertAlmostEqual(event.raw_fill_price, 102.0)
        self.assertAlmostEqual(event.fill_price, 102.0 * 1.0005)


class PeriodCrossGridTest(unittest.TestCase):
    def test_frozen_experiment_builds_28880_unique_cases(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        cases = build_grid_cases(config["parameters"])
        self.assertEqual(len(cases), 28_880)
        self.assertFalse(
            cases.duplicated(
                ["buy_window", "sell_window", "forced_rebuy_pct", "stop_loss_pct_key"]
            ).any()
        )
        self.assertEqual(sorted(cases["buy_window"].unique().tolist()), list(range(80, 451, 10)))
        self.assertEqual(sorted(cases["sell_window"].unique().tolist()), list(range(80, 451, 10)))
        self.assertEqual(sorted(cases["forced_rebuy_pct"].unique().tolist()), [3.0, 5.0, 8.0, 10.0])
        self.assertEqual(sorted(cases["stop_loss_pct_key"].unique().tolist()), [-1.0, 3.0, 5.0, 8.0, 10.0])

    def test_two_compiled_ledgers_match_every_toy_case(self) -> None:
        market = toy_market()
        cases = build_grid_cases(
            {
                "buy_sma_window_range": {"start": 3, "stop": 4, "step": 1, "count": 2},
                "sell_sma_window_range": {"start": 4, "stop": 5, "step": 1, "count": 2},
                "forced_rebuy_pct": [3.0],
                "stop_loss_pct": [None, 5.0],
                "combination_count": 8,
            }
        )
        start = market.iloc[5]["date"]
        end = market.iloc[-1]["date"]
        primary = screen_period_cross_cases(
            market,
            cases,
            analysis_start=start,
            analysis_end=end,
            cost_bps=5.0,
            initial_cash=100_000.0,
            pbo_block_count=4,
        )
        reference = screen_period_cross_cases_reference(
            market,
            cases,
            analysis_start=start,
            analysis_end=end,
            cost_bps=5.0,
            initial_cash=100_000.0,
            pbo_block_count=4,
        )
        columns = [
            "final_equity",
            "total_return_pct",
            "cagr_pct",
            "sharpe",
            "max_drawdown_pct",
            "exposure_pct",
            "order_count",
            "buy_sma_count",
            "sell_sma_count",
            "forced_rebuy_count",
            "stop_loss_count",
        ]
        np.testing.assert_allclose(
            primary.metrics[columns].to_numpy(float),
            reference.metrics[columns].to_numpy(float),
            rtol=0,
            atol=1e-9,
            equal_nan=True,
        )

    def test_period_surface_rejects_boundary_component(self) -> None:
        rows = []
        for buy in (80, 90, 100, 110):
            for sell in (80, 90, 100, 110):
                rows.append(
                    {
                        "buy_window": buy,
                        "sell_window": sell,
                        "primary_metric": 10.0 if buy <= 100 and sell <= 100 else 0.0,
                        "sharpe": 1.0,
                        "cagr_pct": 10.0,
                        "order_count": 20,
                        "identifiable": True,
                    }
                )
        result = analyze_period_surface(
            pd.DataFrame(rows),
            metric="primary_metric",
            top_quantile=0.5,
            minimum_component_cells=4,
            minimum_buy_span=20,
            minimum_sell_span=20,
        )
        self.assertTrue(result["largest_component"]["touches_search_boundary"])
        self.assertFalse(result["largest_component"]["structural_pass"])


class PeriodCrossLedgerTest(unittest.TestCase):
    def test_pybroker_matches_independent_reference(self) -> None:
        market = toy_market()
        start = pd.Timestamp(market.iloc[5]["date"])
        end = pd.Timestamp(market.iloc[-1]["date"])
        spec = SmaPeriodCrossSpec(
            buy_window=3,
            sell_window=5,
            forced_rebuy_pct=3.0,
            stop_loss_pct=5.0,
            cost_bps=5.0,
        )
        pybroker_run = run_pybroker_sma_period_cross(
            market,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_cash=100_000.0,
        )
        engine = market[
            (market["date"] >= pybroker_run.engine_start) & (market["date"] <= end)
        ].copy()
        actual = pybroker_daily_state(pybroker_run.pybroker_result, engine)
        actual = actual[actual["date"] >= start].reset_index(drop=True)
        reference = run_reference_sma_period_cross(
            market,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_cash=100_000.0,
        )
        differences = cross_check(
            pybroker_run.pybroker_result,
            actual,
            reference,
            tolerance=1e-6,
        )
        self.assertLessEqual(max(differences.values(), default=0.0), 1e-6)
        if not reference.orders.empty:
            self.assertLessEqual(reference.orders.groupby("date").size().max(), 1)


if __name__ == "__main__":
    unittest.main()
