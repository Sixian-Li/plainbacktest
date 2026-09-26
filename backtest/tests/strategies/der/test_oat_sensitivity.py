from __future__ import annotations

import unittest

from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
)
import pandas as pd

from quantkit.oat_sensitivity import (
    baseline_plateau_diagnostics,
    build_oat_cases,
    cross_window_plateau_intersections,
    metric_winner,
    select_formal_oat_cases,
    select_plateau_endpoint_cases,
    sweep_values,
)


class OatSensitivityTest(unittest.TestCase):
    def test_decimal_range_is_inclusive_without_binary_step_drift(self) -> None:
        values = sweep_values({"start": 0.4, "stop": 1.6, "step": 0.05})
        self.assertEqual(len(values), 25)
        self.assertEqual(values[0], 0.4)
        self.assertEqual(values[12], 1.0)
        self.assertEqual(values[-1], 1.6)

    def test_build_deduplicates_shared_baseline_and_changes_one_dimension(self) -> None:
        baseline = {name: float(index + 1) for index, name in enumerate(PARAMETER_COLUMNS)}
        baseline["R_forced_rebuy_pct"] = 0.0
        baseline[FORCED_REENTRY_COLUMN] = True
        sweeps = [
            {"sweep_id": "A", "parameter": PARAMETER_COLUMNS[0], "values": [0.0, 1.0, 2.0]},
            {"sweep_id": "B", "parameter": PARAMETER_COLUMNS[1], "values": [1.0, 2.0, 3.0]},
            {"sweep_id": "MODE", "parameter": FORCED_REENTRY_COLUMN, "values": [False, True]},
        ]
        cases, points = build_oat_cases(baseline, sweeps)
        self.assertEqual(len(points), 8)
        self.assertEqual(len(cases), 6)
        self.assertEqual(int(points["is_baseline"].sum()), 3)
        for point in points.itertuples(index=False):
            case = cases[cases["case_id"] == point.case_id].iloc[0]
            changed = [
                name for name in (*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN)
                if case[name] != baseline[name]
            ]
            self.assertLessEqual(len(changed), 1)

    def test_metric_ties_prefer_the_frozen_baseline(self) -> None:
        points = pd.DataFrame(
            [
                {"sweep_id": "A", "case_id": "low", "point_order": 1, "is_baseline": False},
                {"sweep_id": "A", "case_id": "base", "point_order": 2, "is_baseline": True},
                {"sweep_id": "A", "case_id": "high", "point_order": 3, "is_baseline": False},
            ]
        )
        results = pd.DataFrame(
            [
                {"case_id": "low", "cagr_pct": 2.0, "sharpe": 1.0},
                {"case_id": "base", "cagr_pct": 2.0, "sharpe": 1.0},
                {"case_id": "high", "cagr_pct": 2.0, "sharpe": 1.0},
            ]
        )
        expanded = points.merge(results, on="case_id")
        self.assertEqual(metric_winner(expanded, "cagr_pct")["case_id"], "base")
        selected = select_formal_oat_cases(results, points)
        self.assertEqual(selected["case_id"].tolist(), ["base"])

    def test_build_carries_explicit_disabled_fast_drop_identity(self) -> None:
        baseline = {name: float(index + 1) for index, name in enumerate(PARAMETER_COLUMNS)}
        baseline[FORCED_REENTRY_COLUMN] = True
        baseline[FAST_DROP_ENABLED_COLUMN] = False
        cases, _ = build_oat_cases(
            baseline,
            [{"parameter": PARAMETER_COLUMNS[0], "values": [0.0, 1.0, 2.0]}],
        )
        self.assertIn(FAST_DROP_ENABLED_COLUMN, cases)
        self.assertFalse(cases[FAST_DROP_ENABLED_COLUMN].astype(bool).any())

    def test_plateau_diagnostic_uses_connected_component_around_baseline(self) -> None:
        sensitivity = pd.DataFrame(
            {
                "window_id": ["FULL"] * 7,
                "sweep_id": ["B"] * 7,
                "parameter": ["B_slow_sma_window"] * 7,
                "parameter_label": ["B"] * 7,
                "point_order": range(1, 8),
                "value": [172, 173, 174, 175, 176, 177, 178],
                "is_baseline": [False, False, False, True, False, False, False],
                "cagr_pct": [15.7, 14.0, 15.4, 15.6, 15.3, 14.0, 15.8],
                "sharpe": [0.90, 0.70, 0.87, 0.88, 0.86, 0.70, 0.91],
            }
        )
        row = baseline_plateau_diagnostics(sensitivity).iloc[0]
        self.assertEqual(row["plateau_min_value"], 174)
        self.assertEqual(row["plateau_max_value"], 176)
        self.assertEqual(row["plateau_point_count"], 3)
        self.assertEqual(row["classification"], "two_sided_plateau")

    def test_curve_maximum_plateau_can_reject_baseline(self) -> None:
        sensitivity = pd.DataFrame(
            {
                "window_id": ["FULL"] * 5,
                "sweep_id": ["B"] * 5,
                "parameter": ["B_slow_sma_window"] * 5,
                "parameter_label": ["B"] * 5,
                "point_order": range(1, 6),
                "value": [173, 174, 175, 176, 177],
                "is_baseline": [False, False, True, False, False],
                "cagr_pct": [14.0, 14.1, 14.2, 15.0, 15.1],
                "sharpe": [0.70, 0.71, 0.72, 0.90, 0.91],
            }
        )
        plateau = baseline_plateau_diagnostics(
            sensitivity,
            cagr_tolerance_pct_points=0.75,
            sharpe_tolerance=0.05,
            reference_mode="curve_maximum",
        )
        row = plateau.iloc[0]
        self.assertFalse(row["baseline_qualifies"])
        self.assertEqual(row["plateau_point_count"], 0)
        self.assertEqual(row["classification"], "baseline_outside_joint_band")
        self.assertTrue(pd.isna(row["plateau_min_value"]))
        self.assertTrue(select_plateau_endpoint_cases(plateau, sensitivity).empty)

    def test_selects_connected_plateau_endpoints(self) -> None:
        points = pd.DataFrame(
            {
                "sweep_id": ["B"] * 5,
                "case_id": ["c1", "c2", "base", "c4", "c5"],
                "value": [173, 174, 175, 176, 177],
            }
        )
        plateau = pd.DataFrame(
            [{
                "window_id": "FULL",
                "sweep_id": "B",
                "baseline_qualifies": True,
                "plateau_min_value": 174,
                "plateau_max_value": 176,
            }]
        )
        selected = select_plateau_endpoint_cases(plateau, points)
        self.assertEqual(selected["case_id"].tolist(), ["c2", "c4"])

    def test_cross_window_plateau_intersection(self) -> None:
        plateau = pd.DataFrame(
            [
                {"window_id": "one", "sweep_id": "B", "parameter": "B", "parameter_label": "B", "baseline_value": 175, "baseline_qualifies": True, "plateau_min_value": 170, "plateau_max_value": 180},
                {"window_id": "two", "sweep_id": "B", "parameter": "B", "parameter_label": "B", "baseline_value": 175, "baseline_qualifies": True, "plateau_min_value": 173, "plateau_max_value": 182},
            ]
        )
        row = cross_window_plateau_intersections(plateau).iloc[0]
        self.assertTrue(row["has_intersection"])
        self.assertEqual(row["intersection_min_value"], 173)
        self.assertEqual(row["intersection_max_value"], 180)


if __name__ == "__main__":
    unittest.main()
