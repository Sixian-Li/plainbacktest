from __future__ import annotations

import unittest

import pandas as pd

from quantkit.stochrsi_position_gates import (
    FourStateSpec,
    run_reference_and_position_gate,
    run_reference_four_state,
    run_reference_position_gate,
)


class FourStateTests(unittest.TestCase):
    def test_strict_crossings_set_four_target_weights(self) -> None:
        dates = pd.date_range("2020-01-01", periods=7, freq="D")
        short = [0.15, 0.21, 0.15, 0.09, 0.09, 0.21, 0.09]
        long = [0.15, 0.15, 0.21, 0.15, 0.09, 0.21, 0.21]
        prepared = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": 100.0,
            "stochrsi_42": short, "stochrsi_100": long,
            "prior_stochrsi_42": [float("nan"), *short[:-1]],
            "prior_stochrsi_100": [float("nan"), *long[:-1]],
        })
        result = run_reference_four_state(
            prepared, FourStateSpec(), analysis_start=dates[0], analysis_end=dates[-1],
        )
        self.assertEqual(
            result.daily["target_weight"].round(8).tolist(),
            [0.0, 0.4, 1.0, 0.6, 0.0, 1.0, 0.6],
        )
        self.assertLess(float((result.daily["qqq_weight"] - result.daily["target_weight"]).abs().max()), 1e-10)

    def test_equality_does_not_cross(self) -> None:
        dates = pd.date_range("2020-01-01", periods=4, freq="D")
        prepared = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": 100.0,
            "stochrsi_42": [0.10, 0.20, 0.21, 0.10],
            "stochrsi_100": [0.10, 0.10, 0.10, 0.10],
            "prior_stochrsi_42": [float("nan"), 0.10, 0.20, 0.21],
            "prior_stochrsi_100": [float("nan"), 0.10, 0.10, 0.10],
        })
        result = run_reference_four_state(
            prepared, FourStateSpec(), analysis_start=dates[0], analysis_end=dates[-1],
        )
        self.assertEqual(result.daily["target_weight"].tolist(), [0.0, 0.0, 0.4, 0.4])


class PositionGateTests(unittest.TestCase):
    def test_gate_is_strict_and_has_no_external_contributions(self) -> None:
        dates = pd.date_range("2020-01-01", periods=4, freq="D")
        mother = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": [100.0, 110.0, 90.0, 95.0],
            "qqq_weight": [0.70, 0.71, 0.70, 0.90],
        })
        result = run_reference_position_gate(
            mother, 0.70, initial_cash=100_000.0, mother_id="M1",
        )
        self.assertEqual(result.daily["is_long"].tolist(), [0, 1, 0, 1])
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell", "buy"])
        self.assertTrue(result.contributions.empty)
        self.assertEqual(float(result.daily["external_contribution"].sum()), 0.0)

    def test_and_gate_requires_every_strict_signal(self) -> None:
        dates = pd.date_range("2020-01-01", periods=4, freq="D")
        source_1 = pd.DataFrame({
            "date": dates,
            "symbol": "QQQ",
            "close": [100.0, 110.0, 90.0, 95.0],
            "qqq_weight": [0.91, 0.91, 0.89, 0.91],
        })
        source_2 = pd.DataFrame({
            "date": dates,
            "symbol": "QQQ",
            "close": [100.0, 110.0, 90.0, 95.0],
            "qqq_weight": [0.91, 0.90, 0.91, 0.92],
        })
        result = run_reference_and_position_gate(
            {"S1": source_1, "S2": source_2},
            0.90,
            composite_id="S1_AND_S2_90",
        )
        self.assertEqual(result.daily["is_long"].tolist(), [1, 0, 0, 1])
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell", "buy"])
        self.assertEqual(result.daily["S1_signal"].tolist(), [1, 1, 0, 1])
        self.assertEqual(result.daily["S2_signal"].tolist(), [1, 0, 1, 1])
        self.assertTrue(result.contributions.empty)

    def test_and_gate_rejects_misaligned_closes(self) -> None:
        dates = pd.date_range("2020-01-01", periods=2, freq="D")
        source_1 = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": [100.0, 101.0],
            "qqq_weight": [0.91, 0.91],
        })
        source_2 = source_1.copy()
        source_2.loc[1, "close"] = 101.5
        with self.assertRaisesRegex(ValueError, "closes do not align"):
            run_reference_and_position_gate(
                {"S1": source_1, "S2": source_2},
                0.90,
                composite_id="S1_AND_S2_90",
            )

    def test_and_gate_keeps_zero_trade_intersection_visible(self) -> None:
        dates = pd.date_range("2020-01-01", periods=3, freq="D")
        source_1 = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": [100.0, 101.0, 102.0],
            "qqq_weight": [0.91, 0.10, 0.91],
        })
        source_2 = pd.DataFrame({
            "date": dates, "symbol": "QQQ", "close": [100.0, 101.0, 102.0],
            "qqq_weight": [0.10, 0.91, 0.10],
        })
        result = run_reference_and_position_gate(
            {"S1": source_1, "S2": source_2},
            0.90,
            composite_id="S1_AND_S2_90",
        )
        self.assertTrue(result.orders.empty)
        self.assertEqual(result.daily["is_long"].tolist(), [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
