from __future__ import annotations

import unittest

import pandas as pd

from scripts.analyze_stochrsi_cross_period_grid import analyze_surface
from scripts.run_stochrsi_cross_period_grid import case_id, period_values


PARAMETERS = {
    "short_period_start": 14,
    "short_period_end": 50,
    "long_period_start": 70,
    "long_period_end": 160,
    "period_step": 2,
    "parent_anchor_short_period": 42,
    "parent_anchor_long_period": 100,
}


class PeriodGridDefinitionTest(unittest.TestCase):
    def test_frozen_cartesian_grid_has_874_unique_cases(self) -> None:
        short_periods = period_values(14, 50, 2)
        long_periods = period_values(70, 160, 2)
        cases = {case_id(short, long) for short in short_periods for long in long_periods}
        self.assertEqual(len(short_periods), 19)
        self.assertEqual(len(long_periods), 46)
        self.assertEqual(len(cases), 874)
        self.assertIn("P014_070", cases)
        self.assertIn("P042_100", cases)
        self.assertIn("P050_160", cases)

    def test_boundary_high_performance_set_is_not_called_stable(self) -> None:
        rows = []
        for short in period_values(14, 50, 2):
            for long in period_values(70, 160, 2):
                high = short in {14, 16, 18} and long in {70, 72, 74}
                rows.append({
                    "case_id": case_id(short, long),
                    "short_period": short,
                    "long_period": long,
                    "cagr_pct": 10.0 if high else 5.0,
                    "sharpe": 1.0 if high else 0.5,
                    "max_drawdown_pct": -20.0,
                })
        surface = analyze_surface(pd.DataFrame(rows), PARAMETERS)
        self.assertEqual(surface["high_performance_case_count"], 9)
        self.assertIsNone(surface["stable_representative"])
        self.assertTrue(surface["components"][0]["touches_boundary"])


if __name__ == "__main__":
    unittest.main()
