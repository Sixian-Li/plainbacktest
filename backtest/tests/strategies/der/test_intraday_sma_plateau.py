from __future__ import annotations

import unittest

import pandas as pd

from quantkit.intraday_sma_plateau import (
    FREE_PARAMETER_COLUMNS,
    build_joint_neighborhood_cases,
    select_joint_plateau_anchors,
    select_plateau_representative,
    summarize_joint_plateaus,
)
from quantkit.intraday_sma_search import PARAMETER_COLUMNS


def base_case(**overrides: float) -> dict[str, float]:
    values = {
        "A_negative_days_slow": 3.0,
        "B_slow_sma_window": 200.0,
        "C_fast_derivative_pct": -0.1,
        "D_negative_days_fast": 3.0,
        "E_fallback_sma_window": 200.0,
        "F_short_sma_center": 80.0,
        "F_short_sma_spacing": 10.0,
        "G_short_recovery_below_pct": 1.0,
        "H_reentry_sma_window": 250.0,
        "L_cost_stop_pct": 10.0,
        "R_forced_rebuy_pct": 10.0,
    }
    values.update(overrides)
    return values


class JointPlateauTest(unittest.TestCase):
    def setUp(self) -> None:
        self.space = {
            "A_negative_days_slow": [2, 3, 4],
            "B_slow_sma_window": [195, 200, 205],
            "C_fast_derivative_pct": [-0.1],
            "D_negative_days_fast": [3],
            "E_fallback_sma_window": [195, 200, 205],
            "F_short_sma_center": [75, 80, 85],
            "F_short_sma_spacing": [9, 10, 11],
            "G_short_recovery_below_pct": [0.75, 1.0, 1.25],
            "H_reentry_sma_window": [245, 250, 255],
            "L_cost_stop_pct": [9, 10, 11],
            "R_forced_rebuy_pct": [9, 10, 11],
        }
        self.radii = {
            "A_negative_days_slow": 1,
            "B_slow_sma_window": 5,
            "E_fallback_sma_window": 5,
            "F_short_sma_center": 5,
            "F_short_sma_spacing": 1,
            "G_short_recovery_below_pct": 0.25,
            "H_reentry_sma_window": 5,
            "L_cost_stop_pct": 1,
            "R_forced_rebuy_pct": 1,
        }

    def test_joint_neighborhood_generation_is_seeded_and_exact(self) -> None:
        anchors = pd.DataFrame(
            [{"anchor_id": "ANCHOR_001", **base_case(), "cagr_pct": 12, "sharpe": 1}]
        )
        first = build_joint_neighborhood_cases(
            self.space, anchors, radii=self.radii, cases_per_anchor=25, seed=42
        )
        second = build_joint_neighborhood_cases(
            self.space, anchors, radii=self.radii, cases_per_anchor=25, seed=42
        )
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(first), 25)
        self.assertFalse(first[list(PARAMETER_COLUMNS)].duplicated().any())
        self.assertTrue((first["fast_drop_enabled"] == False).all())  # noqa: E712

    def test_selection_prefers_an_interior_plateau_over_boundary_peak(self) -> None:
        results = pd.DataFrame(
            [
                {**base_case(A_negative_days_slow=2), "cagr_pct": 15.0, "sharpe": 1.2, "max_drawdown_pct": -20, "order_count": 20},
                {**base_case(A_negative_days_slow=3), "cagr_pct": 14.8, "sharpe": 1.18, "max_drawdown_pct": -19, "order_count": 18},
            ]
        )
        anchors = select_joint_plateau_anchors(results, count=2)
        rows = []
        for anchor in anchors.itertuples(index=False):
            for index in range(8):
                rows.append(
                    {
                        "anchor_id": anchor.anchor_id,
                        "cagr_pct": anchor.cagr_pct - index * 0.01,
                        "sharpe": anchor.sharpe - index * 0.001,
                    }
                )
        neighborhoods = pd.DataFrame(rows)
        summary = summarize_joint_plateaus(anchors, neighborhoods, search_space=self.space)
        selected = select_plateau_representative(anchors, summary)
        self.assertEqual(float(selected["A_negative_days_slow"]), 3.0)
        self.assertEqual(int(selected["boundary_dimension_count"]), 0)
        self.assertEqual(selected["selection_reason"], "JOINT_PLATEAU_REPRESENTATIVE")

    def test_free_parameter_contract_excludes_c_and_d(self) -> None:
        self.assertNotIn("C_fast_derivative_pct", FREE_PARAMETER_COLUMNS)
        self.assertNotIn("D_negative_days_fast", FREE_PARAMETER_COLUMNS)


if __name__ == "__main__":
    unittest.main()
