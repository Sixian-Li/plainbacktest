from __future__ import annotations

import json
import unittest

import pandas as pd

from quantkit.paths import BACKTEST_ROOT
from scripts.analyze_bear6_sma_fine_windows import (
    DEFAULT_OBSERVATION,
    build_plateaus,
    performance_figure,
    plateau_summary,
    window_curve_figure,
)
from scripts.run_bear6_sma_fine_windows import (
    EXPECTED_RANGES,
    EXPECTED_TARGETS,
    STRATEGY_HOLD,
    build_case_specs,
    build_windows_by_target,
)


EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json"
)


class Bear6SmaFineWindowScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = json.loads(EXPERIMENT.read_text(encoding="utf-8"))["parameters"]
        cls.windows = build_windows_by_target(cls.parameters)

    def test_frozen_target_grids_and_case_count_are_complete(self) -> None:
        self.assertEqual(tuple(self.windows), EXPECTED_TARGETS)
        for target, (start, end, step) in EXPECTED_RANGES.items():
            self.assertEqual(self.windows[target], list(range(start, end + 1, step)))
        self.assertEqual(self.windows["SO"][0], 1)
        self.assertEqual(self.windows["ED"][0], 1)
        self.assertNotIn(0, self.windows["SO"])
        cases = build_case_specs(self.parameters)
        self.assertEqual(len(cases), 323)
        self.assertEqual(len({case.case_id for case in cases}), 323)
        self.assertEqual(sum(case.sma_window is None for case in cases), 6)

    def test_sma1_is_an_explicit_no_entry_boundary_under_three_percent_buffer(self) -> None:
        close = pd.Series([10.0, 11.0, 9.0, 12.0])
        sma1 = close.rolling(1).mean()
        self.assertTrue((sma1 == close).all())
        self.assertFalse((close > sma1 * 1.03).any())

    def _curve(self, target: str, high_run: dict[int, float]) -> pd.DataFrame:
        windows = self.windows[target]
        returns = [-20.0] * len(windows)
        for window, value in high_run.items():
            returns[windows.index(window)] = value
        return pd.DataFrame(
            {
                "target": target,
                "target_type": "single",
                "grid_step": EXPECTED_RANGES[target][2],
                "observation_order": 15,
                "observation_id": DEFAULT_OBSERVATION,
                "label": "总熊（不包含2000–2002）",
                "kind": "scope",
                "key": "all_ex_2000_2002",
                "sma_window": windows,
                "sma_return_pct": returns,
                "hold_return_pct": [0.0] * len(windows),
                "sma_minus_hold_pp": returns,
            }
        )

    def test_plateau_uses_each_targets_frozen_grid_step(self) -> None:
        azo = plateau_summary(self._curve("AZO", {220: 9.0, 221: 10.0, 222: 8.0}))
        gis = plateau_summary(self._curve("GIS", {350: 9.0, 360: 10.0, 370: 8.0}))
        self.assertTrue(azo["plateau_found"])
        self.assertEqual((azo["plateau_start"], azo["plateau_end"]), (220, 222))
        self.assertTrue(gis["plateau_found"])
        self.assertEqual((gis["plateau_start"], gis["plateau_end"]), (350, 370))

    def test_chart_uses_target_axis_solid_sma_and_horizontal_dashed_hold(self) -> None:
        points = self._curve("AZO", {230: 10.0, 231: 9.0, 232: 8.0})
        plateaus = build_plateaus(points)
        figure = window_curve_figure(points, plateaus, "AZO")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].line.dash, None)
        self.assertEqual(figure.data[1].line.dash, "dash")
        self.assertEqual(len(set(figure.data[1].y)), 1)
        self.assertEqual(list(figure.data[0].x), self.windows["AZO"])

    def test_performance_context_has_one_benchmark_and_midpoint_pairs(self) -> None:
        rows = []
        values = []
        for target in EXPECTED_TARGETS:
            windows = self.windows[target]
            midpoint = windows[(len(windows) - 1) // 2]
            for strategy_id in (STRATEGY_HOLD, f"sma_{midpoint:03d}"):
                rows.append(
                    {
                        "case_index": len(rows),
                        "case_id": f"{target}__{strategy_id}",
                        "target": target,
                        "strategy_id": strategy_id,
                    }
                )
                values.append([100.0, 101.0, 99.0])
        block = {
            "case_index": pd.DataFrame(rows),
            "date_index": pd.DataFrame({"date": pd.date_range("2020-01-01", periods=3)}),
            "daily": {"equity": values},
        }
        figure = performance_figure(block, self.windows)
        self.assertEqual(len(figure.data), 24)
        self.assertEqual(
            sum(
                trace.meta["is_benchmark"]
                for trace in figure.data
                if trace.meta["panel"] == "equity"
            ),
            1,
        )
        self.assertEqual({trace.meta["panel"] for trace in figure.data}, {"equity", "drawdown"})


if __name__ == "__main__":
    unittest.main()
