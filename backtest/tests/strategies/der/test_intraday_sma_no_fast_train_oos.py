from __future__ import annotations

import unittest

import pandas as pd

from quantkit.experiment import load_experiment
from quantkit.intraday_sma_plateau import FREE_PARAMETER_COLUMNS
from quantkit.paths import BACKTEST_ROOT
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_intraday_sma_no_fast_drop_train_oos import build_search_space


EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos"
)


class NoFastDropTrainOosContractTest(unittest.TestCase):
    def test_experiment_freezes_split_ranges_and_removed_rule(self) -> None:
        config = load_experiment(EXPERIMENT).config
        parameters = config["parameters"]
        self.assertFalse(parameters["fast_drop_enabled"])
        self.assertEqual(parameters["train_start"], "2000-07-27")
        self.assertEqual(parameters["train_end"], "2015-12-31")
        self.assertEqual(parameters["locked_test_start"], "2016-01-04")
        self.assertEqual(parameters["locked_test_end"], "2026-08-04")
        self.assertEqual(set(parameters["search_ranges"]), set(FREE_PARAMETER_COLUMNS))
        expected = {
            "A_negative_days_slow": (2, 10, 1),
            "B_slow_sma_window": (150, 300, 1),
            "E_fallback_sma_window": (100, 330, 1),
            "F_short_sma_center": (30, 200, 1),
            "F_short_sma_spacing": (5, 20, 1),
            "G_short_recovery_below_pct": (0, 5, 0.05),
            "H_reentry_sma_window": (200, 350, 1),
            "L_cost_stop_pct": (5, 30, 0.25),
            "R_forced_rebuy_pct": (0, 30, 0.25),
        }
        for name, values in expected.items():
            spec = parameters["search_ranges"][name]
            self.assertEqual((spec["start"], spec["stop"], spec["step"]), values)

    def test_search_space_uses_c_d_only_as_single_disabled_placeholders(self) -> None:
        config = load_experiment(EXPERIMENT).config
        space = build_search_space(config["parameters"])
        self.assertEqual(space["C_fast_derivative_pct"], [-0.1])
        self.assertEqual(space["D_negative_days_fast"], [3.0])
        self.assertEqual(space["A_negative_days_slow"][0], 2.0)
        self.assertEqual(space["A_negative_days_slow"][-1], 10.0)
        self.assertEqual(space["G_short_recovery_below_pct"][1], 0.05)
        self.assertEqual(space["R_forced_rebuy_pct"][-1], 30.0)

    def test_shared_json_safe_serializes_timestamp(self) -> None:
        payload = json_safe({"first_entry_date": pd.Timestamp("2016-01-04")})
        self.assertEqual(payload, {"first_entry_date": "2016-01-04T00:00:00"})


if __name__ == "__main__":
    unittest.main()
