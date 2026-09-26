from __future__ import annotations

import unittest

import pandas as pd

from scripts.run_stochrsi_multistart_robustness import quarterly_windows, select_training
from scripts.run_stochrsi_multistart_full_2010_2026 import all_windows
from scripts.run_stochrsi_cross_period_grid import case_id


PARAMETERS = {
    "window_years": 5,
    "training_start_first": "2010-01-01",
    "training_start_last": "2014-12-31",
    "validation_start_first": "2020-01-01",
    "validation_start_last": "2021-09-30",
    "expected_training_windows": 20,
    "expected_validation_windows": 7,
    "short_period_start": 14,
    "short_period_end": 50,
    "long_period_start": 70,
    "long_period_end": 160,
    "period_step": 2,
}


class MultiStartRobustnessTest(unittest.TestCase):
    def test_full_2010_2026_scope_uses_all_47_observable_windows(self) -> None:
        raw = pd.DataFrame({"date": pd.bdate_range("2009-01-01", "2026-08-04")})
        parameters = {
            **PARAMETERS,
            "all_start_first": "2010-01-01",
            "all_start_last": "2021-09-30",
            "expected_all_windows": 47,
        }
        windows = all_windows(raw, parameters)
        self.assertEqual(len(windows), 47)
        self.assertEqual(windows[0]["window_id"], "ALL_2010Q1")
        self.assertEqual(windows[-1]["window_id"], "ALL_2021Q3")
        self.assertLessEqual(windows[-1]["end"], pd.Timestamp("2026-08-04"))

    def test_quarterly_five_year_windows_are_counted_and_time_isolated(self) -> None:
        raw = pd.DataFrame({"date": pd.bdate_range("2009-01-01", "2026-08-04")})
        training = quarterly_windows(raw, PARAMETERS, "training")
        validation = quarterly_windows(raw, PARAMETERS, "validation")
        self.assertEqual(len(training), 20)
        self.assertEqual(len(validation), 7)
        self.assertLess(training[-1]["end"], validation[0]["start"])
        self.assertEqual(training[0]["window_id"], "TR_2010Q1")
        self.assertEqual(validation[-1]["window_id"], "VA_2021Q3")

    def test_nine_point_interior_plateau_can_name_champion(self) -> None:
        rows = []
        for short in (28, 30, 32):
            for long in (98, 100, 102):
                for window in range(20):
                    rows.append({
                        "case_id": case_id(short, long), "short_period": short, "long_period": long,
                        "window_id": f"TR_{window}", "cagr_pct": 10,
                        "sharpe": 1, "max_drawdown_pct": -20,
                        "closed_trade_count": 3,
                    })
        _, selection = select_training(pd.DataFrame(rows), PARAMETERS)
        self.assertIsNotNone(selection["robust_champion"])
        self.assertEqual(selection["robust_champion"]["case_id"], "P030_100")
        self.assertEqual(selection["robust_champion"]["component_size"], 9)


if __name__ == "__main__":
    unittest.main()
