from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.stochrsi_scaled_pools import (
    ScaledPoolSpec,
    cash_flow_adjusted_metrics,
    compiled_daily_state,
    run_compiled_pybroker,
    run_reference_scaled_pools,
)
from scripts.run_intraday_sma_backtest import cross_check
from scripts.run_stochrsi_sparse_entry_factorial import CASE_FLAGS, CASE_SPECS


def toy_frame(values42: list[float], values100: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2014-12-31", periods=len(values42))
    close = np.linspace(100.0, 90.0, len(dates))
    frame = pd.DataFrame({
        "symbol": "QQQ", "date": dates, "open": close, "high": close,
        "low": close, "close": close, "volume": 1_000,
        "stochrsi_42": values42, "stochrsi_100": values100,
        "fast_drop_trigger_100_030": 95.0,
    })
    frame["prior_stochrsi_42"] = frame["stochrsi_42"].shift(1)
    frame["prior_stochrsi_100"] = frame["stochrsi_100"].shift(1)
    return frame


class ScaledPoolStateMachineTest(unittest.TestCase):
    def test_sparse_entry_factorial_has_all_eight_unique_combinations(self) -> None:
        self.assertEqual(len(CASE_SPECS), 8)
        self.assertEqual(
            {tuple(flags[key] for key in "DFS") for flags in CASE_FLAGS.values()},
            {
                (False, False, False), (False, False, True),
                (False, True, False), (False, True, True),
                (True, False, False), (True, False, True),
                (True, True, False), (True, True, True),
            },
        )

    def test_b2_supersedes_b1_and_a_priority_clears_pool_b(self) -> None:
        frame = toy_frame(
            [0.5, 0.005, 0.005, 0.005, 0.005, 0.005, 0.9, 0.9, 0.1],
            [0.5, 0.005, 0.005, 0.005, 0.005, 0.005, 0.4, 0.9, 0.1],
        )
        result = run_reference_scaled_pools(
            frame, ScaledPoolSpec(), analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date, initial_cash=100_000,
        )
        self.assertEqual(result.orders.iloc[0]["primary_signal"], "BUY_B2_EXTREME_LOW")
        self.assertEqual(result.daily.iloc[4]["b2_count"], 5)
        self.assertGreater(result.daily.iloc[5]["pool_b_shares"], 0)
        self.assertEqual(result.daily.iloc[6]["pool_b_shares"], 0)
        self.assertGreater(result.daily.iloc[6]["pool_a_shares"], 0)
        last_orders = result.orders[result.orders["date"].eq(frame.iloc[-1].date)]
        self.assertEqual(last_orders["type"].tolist(), ["sell", "buy"])
        self.assertEqual(last_orders.iloc[0]["fill_source"], "theoretical_stochrsi_030_no_ohlc_gate")
        self.assertEqual(last_orders.iloc[1]["primary_signal"], "BUY_B1_LOW")
        self.assertEqual(result.daily.iloc[-1]["b2_count"], 0)
        self.assertIsNotNone(result.daily.iloc[-1]["buy_a"])

    def test_strict_fast_drop_boundaries_do_not_use_override(self) -> None:
        frame = toy_frame(
            [0.5, 0.5, 0.5, 0.5],
            [0.5, 0.9, 0.6, 0.2],
        )
        result = run_reference_scaled_pools(
            frame, ScaledPoolSpec(), analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date, initial_cash=100_000,
        )
        event = result.events.iloc[-1]
        self.assertFalse(bool(event["fast_drop_sale"]))
        self.assertNotIn("SELL_A_FAST_030", result.orders["primary_signal"].tolist())

    def test_compiled_pybroker_matches_reference_with_same_day_sell_buy(self) -> None:
        frame = toy_frame(
            [0.5, 0.005, 0.005, 0.005, 0.005, 0.005, 0.9, 0.9, 0.1],
            [0.5, 0.005, 0.005, 0.005, 0.005, 0.005, 0.4, 0.9, 0.1],
        )
        start, end = frame.iloc[1].date, frame.iloc[-1].date
        reference = run_reference_scaled_pools(
            frame, ScaledPoolSpec(), analysis_start=start, analysis_end=end,
            initial_cash=100_000,
        )
        broker, engine = run_compiled_pybroker(
            frame, reference, analysis_start=start, analysis_end=end,
            initial_cash=100_000,
        )
        actual = compiled_daily_state(broker, engine, frame, reference.contributions)
        actual = actual[actual["date"].between(start, end)].reset_index(drop=True)
        cross_check(broker, actual, reference, tolerance=1e-6)

    def test_cash_flow_adjustment_removes_contribution_return(self) -> None:
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2020-01-02", "2020-01-03"]),
            "equity": [100.0, 150.0], "external_contribution": [0.0, 50.0],
            "is_long": [1, 1],
        })
        metrics = cash_flow_adjusted_metrics(
            daily, pd.DataFrame(), pd.DataFrame(), initial_cash=100.0
        )
        self.assertAlmostEqual(float(metrics["cash_flow_adjusted_total_return_pct"]), 0.0)
        self.assertAlmostEqual(float(metrics["net_profit"]), 0.0)

    def test_ablation_flags_disable_pool_b_daily_decay_and_buy_floors(self) -> None:
        frame = toy_frame(
            [0.5, 0.005, 0.9, 0.9, 0.5, 0.005, 0.005, 0.005, 0.005, 0.005],
            [0.5, 0.005, 0.4, 0.9, 0.7, 0.005, 0.005, 0.005, 0.005, 0.005],
        )
        result = run_reference_scaled_pools(
            frame,
            ScaledPoolSpec(
                enable_pool_b=False,
                enable_a_daily_decay=False,
                enable_b1_floor=False,
                enable_b2_floor=False,
            ),
            analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date,
            initial_cash=100_000,
        )
        self.assertFalse(result.events["action"].str.contains("POOL_B|SELL_B").any())
        self.assertNotIn("SELL_A_DAILY_DECAY", result.orders["primary_signal"].tolist())
        self.assertTrue(result.contributions.empty)

    def test_deferred_buy_queues_virtual_tranches_and_fills_on_100_cross_040(self) -> None:
        frame = toy_frame(
            [0.5, 0.1, 0.1, 0.1, 0.5],
            [0.5, 0.1, 0.1, 0.1, 0.5],
        )
        result = run_reference_scaled_pools(
            frame,
            ScaledPoolSpec(deferred_buy_cross=0.40),
            analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date,
            initial_cash=100_000,
        )
        self.assertEqual(result.orders["primary_signal"].tolist(), ["BUY_DEFERRED_S100_CROSS"])
        self.assertEqual(pd.Timestamp(result.orders.iloc[0]["date"]), pd.Timestamp(frame.iloc[-1].date))
        expected = 5_000 + 4_750 + 4_512.50
        self.assertAlmostEqual(float(result.orders.iloc[0]["notional"]), expected)
        self.assertAlmostEqual(float(result.daily.iloc[-1]["pending_buy"]), 0.0)
        self.assertAlmostEqual(
            float(result.daily.iloc[-1]["position_value"]),
            float(result.daily.iloc[-1]["shares"] * result.daily.iloc[-1]["close"]),
        )

    def test_sparse_42_buy_requires_downcross_then_recovery_and_uses_40_percent(self) -> None:
        frame = toy_frame(
            [0.5, 0.3, 0.1, 0.1, 0.3],
            [0.5, 0.3, 0.3, 0.3, 0.3],
        )
        result = run_reference_scaled_pools(
            frame,
            ScaledPoolSpec(enable_sparse_42_recovery_buy=True),
            analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date,
            initial_cash=100_000,
        )
        self.assertEqual(result.orders["primary_signal"].tolist(), ["BUY_SPARSE_S42_RECOVERY_020"])
        self.assertAlmostEqual(float(result.orders.iloc[0]["notional"]), 40_000.0)
        self.assertFalse(bool(result.daily.iloc[-1]["sparse_42_armed"]))

    def test_deferred_020_fills_before_sparse_100_and_suppresses_sparse_42(self) -> None:
        frame = toy_frame(
            [0.5, 0.1, 0.1, 0.3],
            [0.5, 0.1, 0.1, 0.3],
        )
        result = run_reference_scaled_pools(
            frame,
            ScaledPoolSpec(
                deferred_buy_cross=0.20,
                enable_sparse_42_recovery_buy=True,
                enable_sparse_100_recovery_buy=True,
            ),
            analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date,
            initial_cash=100_000,
        )
        self.assertEqual(
            result.orders["primary_signal"].tolist(),
            ["BUY_DEFERRED_S100_CROSS", "BUY_SPARSE_S100_UPCROSS_020"],
        )
        self.assertAlmostEqual(float(result.orders.iloc[0]["notional"]), 9_750.0)
        self.assertAlmostEqual(float(result.orders.iloc[1]["notional"]), 45_125.0)
        self.assertNotIn("BUY_SPARSE_S42_RECOVERY_020", result.orders["primary_signal"].tolist())
        broker, engine = run_compiled_pybroker(
            frame, result, analysis_start=frame.iloc[1].date,
            analysis_end=frame.iloc[-1].date, initial_cash=100_000,
        )
        actual = compiled_daily_state(broker, engine, frame, result.contributions)
        actual = actual[actual["date"].between(frame.iloc[1].date, frame.iloc[-1].date)].reset_index(drop=True)
        cross_check(broker, actual, result, tolerance=1e-6)


if __name__ == "__main__":
    unittest.main()
