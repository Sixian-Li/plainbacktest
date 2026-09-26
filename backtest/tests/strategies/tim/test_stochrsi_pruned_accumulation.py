from __future__ import annotations

import unittest

import pandas as pd

from quantkit.stochrsi_position_gates import holding_period_cagr_pct
from quantkit.stochrsi_pruned_accumulation import (
    PrunedAccumulationSpec,
    run_reference_pruned_accumulation,
)


def prepared(short: list[float], long: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=len(short), freq="D")
    return pd.DataFrame({
        "date": dates,
        "symbol": "QQQ",
        "close": 100.0,
        "stochrsi_42": short,
        "stochrsi_100": long,
        "prior_stochrsi_42": [float("nan"), *short[:-1]],
        "prior_stochrsi_100": [float("nan"), *long[:-1]],
    })


def run(frame: pd.DataFrame):
    return run_reference_pruned_accumulation(
        frame,
        PrunedAccumulationSpec(),
        analysis_start=frame.iloc[0]["date"],
        analysis_end=frame.iloc[-1]["date"],
    )


class PrunedAccumulationTests(unittest.TestCase):
    def test_low_entries_respect_15_and_30_percent_caps(self) -> None:
        frame = prepared(
            [0.10, 0.10, 0.10, 0.10, 0.10, 0.00],
            [0.10, 0.10, 0.10, 0.10, 0.10, 0.03],
        )
        result = run(frame)
        self.assertAlmostEqual(float(result.daily.iloc[3]["qqq_weight"]), 0.15, places=12)
        self.assertAlmostEqual(float(result.daily.iloc[4]["qqq_weight"]), 0.15, places=12)
        self.assertAlmostEqual(float(result.daily.iloc[5]["qqq_weight"]), 0.30, places=12)
        self.assertEqual(
            result.orders.iloc[-1]["primary_signal"], "BUY_EXTREME_LOW_CAPPED_030"
        )
        self.assertTrue(result.contributions.empty)

    def test_long_recovery_supersedes_short_and_ignores_sparse_weight(self) -> None:
        frame = prepared(
            [0.10, 0.10, 0.21],
            [0.10, 0.10, 0.21],
        )
        result = run(frame)
        signals = result.orders["primary_signal"].tolist()
        self.assertEqual(signals[-1], "BUY_LONG_RECOVERY_S100_UPCROSS_020")
        self.assertNotIn("BUY_SHORT_RECOVERY_S42_UPCROSS_020", signals)
        self.assertGreater(float(result.daily.iloc[-1]["qqq_weight"]), 0.15)

    def test_pool_a_uses_posttrade_shares_and_sells_only_pool(self) -> None:
        frame = prepared(
            [0.10, 0.21, 0.30, 0.30],
            [0.10, 0.81, 0.85, 0.49],
        )
        result = run(frame)
        day_two_shares = float(result.daily.iloc[1]["shares"])
        self.assertAlmostEqual(
            float(result.daily.iloc[1]["pool_a_shares"]), day_two_shares * 0.01, places=10
        )
        shares_before_exit = float(result.daily.iloc[2]["shares"])
        pool_before_exit = float(result.daily.iloc[2]["pool_a_shares"])
        self.assertAlmostEqual(
            float(result.daily.iloc[3]["shares"]), shares_before_exit - pool_before_exit, places=10
        )
        self.assertEqual(result.orders.iloc[-1]["primary_signal"], "SELL_POOL_A_S100_DOWNCROSS_050")
        self.assertEqual(float(result.daily.iloc[-1]["pool_a_shares"]), 0.0)

    def test_deep_failure_has_priority_and_blocks_same_close_rebuy(self) -> None:
        frame = prepared(
            [0.10, 0.21, 0.10],
            [0.10, 0.81, 0.10],
        )
        result = run(frame)
        day_three_orders = result.orders[pd.to_datetime(result.orders["date"]).eq(frame.iloc[2]["date"])]
        self.assertEqual(len(day_three_orders), 1)
        self.assertEqual(
            day_three_orders.iloc[0]["primary_signal"], "SELL_DEEP_FAILURE_S100_DOWNCROSS_020"
        )
        self.assertEqual(float(result.daily.iloc[-1]["shares"]), 0.0)
        self.assertEqual(float(result.daily.iloc[-1]["pool_a_shares"]), 0.0)


class HoldingPeriodCagrTests(unittest.TestCase):
    def test_geometric_time_compression_example(self) -> None:
        self.assertAlmostEqual(holding_period_cagr_pct(10.0, 50.0), 21.0, places=12)

    def test_full_exposure_equals_calendar_cagr(self) -> None:
        self.assertAlmostEqual(holding_period_cagr_pct(13.25, 100.0), 13.25, places=12)

    def test_zero_exposure_is_undefined(self) -> None:
        self.assertIsNone(holding_period_cagr_pct(0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
