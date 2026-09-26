from __future__ import annotations

import unittest
import json
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import pandas as pd

from quantkit.intraday_sma_search import PARAMETER_COLUMNS
from quantkit.training_search import (
    build_one_step_neighborhoods,
    expand_search_space,
    select_stability_parents,
    select_stable_representative,
    summarize_neighborhoods,
)


def row(value: float, *, case_id: str, cagr: float, sharpe: float) -> dict[str, float | str]:
    return {
        "screen_case_id": case_id,
        **{name: value for name in PARAMETER_COLUMNS},
        "cagr_pct": cagr,
        "sharpe": sharpe,
        "delta_cagr_vs_buy_hold_pct_points": cagr - 1.0,
        "delta_sharpe_vs_buy_hold": sharpe - 0.1,
    }


class TrainingSearchTest(unittest.TestCase):
    def test_spy_training_contract_keeps_held_out_period_unseen(self) -> None:
        root = BACKTEST_ROOT
        config = json.loads(
            (root / "experiments/DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json").read_text()
        )
        parameters = config["parameters"]
        self.assertEqual(config["symbols"], ["SPY"])
        self.assertEqual(parameters["analysis_start"], "1993-01-29")
        self.assertEqual(parameters["analysis_end"], "2002-12-31")
        self.assertEqual(parameters["held_out_start"], "2003-01-02")
        self.assertEqual(parameters["held_out_access"], "forbidden_in_this_experiment")
        self.assertEqual(parameters["global_sample_count"], 100000)
        self.assertEqual(parameters["refined_sample_count"], 100000)
        space = expand_search_space(parameters["search_space_definition"])
        self.assertEqual(space["A_negative_days_slow"], list(map(float, range(2, 16))))
        self.assertEqual(space["B_slow_sma_window"][1] - space["B_slow_sma_window"][0], 2.0)
        self.assertAlmostEqual(
            space["C_fast_derivative_pct"][1] - space["C_fast_derivative_pct"][0],
            0.025,
            places=12,
        )

    def test_long_spy_training_contract_reuses_protocol_and_holds_out_2021_plus(self) -> None:
        root = BACKTEST_ROOT
        short = json.loads(
            (root / "experiments/DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json").read_text()
        )
        long = json.loads(
            (root / "experiments/DER/DER-v0.50b.1__26-08-14__spy_intraday_sma_training_1993_2020/experiment.json").read_text()
        )
        self.assertEqual(long["symbols"], ["SPY"])
        self.assertEqual(long["parameters"]["analysis_start"], "1993-01-29")
        self.assertEqual(long["parameters"]["analysis_end"], "2020-12-31")
        self.assertEqual(long["parameters"]["held_out_start"], "2021-01-04")
        self.assertEqual(long["parameters"]["held_out_access"], "forbidden_in_this_experiment")
        for field in (
            "search_method",
            "global_sample_count",
            "refined_sample_count",
            "frontier_parent_count_per_objective",
            "stability_parent_count_per_objective",
            "formal_candidate_count_per_objective",
            "search_space_definition",
            "stability_selection",
            "oat_diagnostic",
        ):
            self.assertEqual(long["parameters"][field], short["parameters"][field])

    def test_compact_ranges_expand_inclusively_without_float_drift(self) -> None:
        definition = {
            name: {"start": 0.0, "stop": 0.2, "step": 0.1}
            for name in PARAMETER_COLUMNS
        }
        expanded = expand_search_space(definition)
        self.assertEqual(expanded["G_short_recovery_below_pct"], [0.0, 0.1, 0.2])

    def test_one_step_neighborhood_deduplicates_shared_cases_and_marks_boundary(self) -> None:
        parents = pd.DataFrame(
            [
                {"parent_id": "P1", **{name: 1.0 for name in PARAMETER_COLUMNS}},
                {"parent_id": "P2", **{name: 2.0 for name in PARAMETER_COLUMNS}},
            ]
        )
        space = {name: [1.0, 2.0, 3.0] for name in PARAMETER_COLUMNS}
        cases, mapping = build_one_step_neighborhoods(parents, space)
        self.assertLessEqual(len(cases), len(mapping))
        self.assertFalse(cases[list(PARAMETER_COLUMNS)].duplicated().any())
        p1 = mapping[mapping["parent_id"] == "P1"]
        self.assertEqual(len(p1), 1 + len(PARAMETER_COLUMNS))
        self.assertFalse((p1["direction"] == "lower").any())

    def test_stable_representative_uses_neighborhood_not_single_peak(self) -> None:
        screening = pd.DataFrame(
            [
                row(1.0, case_id="SPIKE", cagr=20.0, sharpe=2.0),
                row(2.0, case_id="PLATEAU", cagr=15.0, sharpe=1.5),
                row(3.0, case_id="LOW", cagr=5.0, sharpe=0.5),
            ]
        )
        parents = select_stability_parents(screening, per_objective=3)
        space = {name: [1.0, 2.0, 3.0] for name in PARAMETER_COLUMNS}
        cases, mapping = build_one_step_neighborhoods(parents, space)
        results = cases.copy()
        results["cagr_pct"] = results["A_negative_days_slow"].map({1.0: 2.0, 2.0: 14.0, 3.0: 13.0})
        results["sharpe"] = results["A_negative_days_slow"].map({1.0: 0.2, 2.0: 1.4, 3.0: 1.3})
        summary = summarize_neighborhoods(parents, mapping, results)
        selected = select_stable_representative(summary)
        self.assertEqual(selected["screen_case_id"], "PLATEAU")

    def test_stable_representative_rejects_boundary_even_if_its_score_is_higher(self) -> None:
        summary = pd.DataFrame(
            [
                {
                    "screen_case_id": "BOUNDARY",
                    "one_sided_dimension_count": 1,
                    "finite_neighbor_count": 12,
                    "neighbor_count": 12,
                    "robust_joint_score": 1.0,
                },
                {
                    "screen_case_id": "TWO_SIDED",
                    "one_sided_dimension_count": 0,
                    "finite_neighbor_count": 23,
                    "neighbor_count": 23,
                    "robust_joint_score": 0.8,
                },
            ]
        )
        selected = select_stable_representative(summary)
        self.assertEqual(selected["screen_case_id"], "TWO_SIDED")


if __name__ == "__main__":
    unittest.main()
