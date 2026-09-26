from __future__ import annotations

import unittest

import pandas as pd

from scripts.run_p24_stricter_threshold_grid import build_grid, holding_diagnostics


class P24StricterThresholdGridTests(unittest.TestCase):
    def test_frozen_grid_has_16_unique_cases_and_original_p24(self) -> None:
        parameters = {
            "fixed_parameters": {
                "long_sma_window": 180,
                "long_slope_lookback": 10,
                "short_sma_window": 20,
                "short_regression_window": 10,
                "entry_confirmation_sessions": 2,
                "relative_strength_lookback": 120,
            },
            "threshold_grid": {
                "long_slope_threshold_daily_pct": [0.02, 0.03, 0.04, 0.05],
                "short_quality_threshold_daily_pct": [0.0, 0.02, 0.05, 0.1],
                "combination_count_per_cost": 16,
            },
        }
        grid = build_grid(parameters)
        self.assertEqual(len(grid), 16)
        self.assertFalse(grid["case_id"].duplicated().any())
        original = grid[
            grid["long_slope_threshold_daily_pct"].eq(0.02)
            & grid["short_quality_threshold_daily_pct"].eq(0.0)
        ]
        self.assertEqual(original["case_id"].tolist(), ["F2_020_F4_000"])

    def test_holding_cagr_annualizes_only_held_sessions(self) -> None:
        daily = pd.DataFrame(
            {
                "is_long": [1] * 126 + [0] * 126,
                "equity": [100_000.0] * 251 + [110_000.0],
            }
        )
        result = holding_diagnostics(daily, 100_000.0)
        self.assertEqual(result["holding_sessions"], 126)
        self.assertAlmostEqual(float(result["holding_years_252"]), 0.5)
        self.assertAlmostEqual(float(result["holding_cagr_pct"]), 21.0, places=10)

    def test_no_holding_has_no_holding_cagr(self) -> None:
        daily = pd.DataFrame({"is_long": [0, 0], "equity": [100_000.0, 100_000.0]})
        result = holding_diagnostics(daily, 100_000.0)
        self.assertEqual(result["holding_sessions"], 0)
        self.assertTrue(pd.isna(result["holding_cagr_pct"]))


if __name__ == "__main__":
    unittest.main()
