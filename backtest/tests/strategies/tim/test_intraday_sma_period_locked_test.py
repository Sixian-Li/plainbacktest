from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from quantkit.intraday_sma_period_cross_search import build_grid_cases
from scripts.run_intraday_sma_period_locked_test import (
    assign_case_roles,
    evaluate_predeclared_gates,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026/experiment.json"
)


class LockedPeriodDefinitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        self.parameters = self.config["parameters"]

    def test_frozen_neighborhood_has_81_unique_cases_and_one_anchor(self) -> None:
        cases = assign_case_roles(build_grid_cases(self.parameters), self.parameters)
        self.assertEqual(len(cases), 81)
        self.assertEqual(
            sorted(cases["buy_window"].unique().tolist()), list(range(270, 351, 10))
        )
        self.assertEqual(
            sorted(cases["sell_window"].unique().tolist()), list(range(150, 231, 10))
        )
        self.assertEqual(cases["forced_rebuy_pct"].unique().tolist(), [3.0])
        self.assertTrue(cases["stop_loss_pct"].isna().all())
        anchor = cases[cases["selection_reason"].str.contains("LOCKED_ANCHOR")]
        self.assertEqual(len(anchor), 1)
        self.assertEqual(
            (int(anchor.iloc[0]["buy_window"]), int(anchor.iloc[0]["sell_window"])),
            (310, 190),
        )

    def test_holdout_champion_cannot_replace_locked_anchor(self) -> None:
        cases = assign_case_roles(build_grid_cases(self.parameters), self.parameters)
        cases["cagr_pct"] = 10.0
        cases["sharpe"] = 1.0
        cases["max_drawdown_pct"] = -20.0
        cases["order_count"] = 20
        cases["exposure_pct"] = 70.0
        cases["identifiable"] = True
        far = cases[(cases["buy_window"] == 270) & (cases["sell_window"] == 150)].index[0]
        cases.loc[far, ["cagr_pct", "sharpe", "max_drawdown_pct"]] = [50.0, 4.0, -5.0]
        result = evaluate_predeclared_gates(
            cases,
            {"cagr_pct": 10.0, "sharpe": 0.8, "max_drawdown_pct": -30.0},
            self.parameters,
        )
        anchor = cases[(cases["buy_window"] == 310) & (cases["sell_window"] == 190)]
        self.assertEqual(result["anchor_case_id"], str(anchor.iloc[0]["case_id"]))
        self.assertTrue(result["diagnostic_support_pass"])
        self.assertFalse(result["promotion_allowed"])

    def test_one_bad_local_case_fails_predeclared_stability_gate(self) -> None:
        cases = assign_case_roles(build_grid_cases(self.parameters), self.parameters)
        cases["cagr_pct"] = 10.0
        cases["sharpe"] = 1.0
        cases["max_drawdown_pct"] = -20.0
        cases["order_count"] = 20
        cases["exposure_pct"] = 70.0
        cases["identifiable"] = True
        bad = cases[(cases["buy_window"] == 300) & (cases["sell_window"] == 180)].index[0]
        cases.loc[bad, "cagr_pct"] = -0.01
        result = evaluate_predeclared_gates(
            cases,
            {"cagr_pct": 10.0, "sharpe": 0.8, "max_drawdown_pct": -30.0},
            self.parameters,
        )
        self.assertFalse(result["local_3x3_stability"]["all_positive_cagr"])
        self.assertFalse(result["local_3x3_stability"]["pass"])
        self.assertFalse(result["diagnostic_support_pass"])

    def test_subperiods_cover_the_locked_window_without_overlap(self) -> None:
        periods = self.parameters["fixed_subperiods"]
        self.assertEqual(periods[0]["start"], self.parameters["analysis_start"])
        self.assertEqual(periods[-1]["end"], self.parameters["analysis_end"])
        for left, right in zip(periods, periods[1:]):
            self.assertLess(left["end"], right["start"])
        self.assertEqual(self.config["research"]["selection_rule"].split(".")[0], "None")


if __name__ == "__main__":
    unittest.main()
