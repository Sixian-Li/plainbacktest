from __future__ import annotations

import json
import unittest

import pandas as pd

from quantkit.paths import BACKTEST_ROOT
from scripts.analyze_bear24_sma_window_plateau import (
    DEFAULT_OBSERVATION,
    build_plateaus,
    performance_figure,
    plateau_summary,
    window_curve_figure,
)
from scripts.run_bear24_sma_window_plateau import (
    STRATEGY_HOLD,
    build_case_specs,
    build_strategy_specs,
)


EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json"
)


class Bear24SmaWindowPlateauTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parameters = json.loads(EXPERIMENT.read_text(encoding="utf-8"))["parameters"]

    def test_frozen_grid_and_case_count_are_complete(self) -> None:
        strategies = build_strategy_specs(self.parameters)
        cases = build_case_specs(self.parameters)
        self.assertEqual(strategies[0].strategy_id, STRATEGY_HOLD)
        self.assertIsNone(strategies[0].sma_window)
        self.assertEqual([item.sma_window for item in strategies[1:]], list(range(30, 301, 10)))
        self.assertEqual(len(strategies), 29)
        self.assertEqual(len(cases), 27 * 29)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))

    def test_plateau_rule_prefers_the_longest_stable_run(self) -> None:
        windows = list(range(30, 301, 10))
        returns = [-20.0] * len(windows)
        for window, value in {50: 9.0, 60: 10.0, 70: 8.0, 150: 7.0, 160: 10.0, 170: 9.0, 180: 8.0}.items():
            returns[windows.index(window)] = value
        curve = pd.DataFrame(
            {
                "target": "AZO",
                "observation_order": 15,
                "observation_id": DEFAULT_OBSERVATION,
                "label": "总熊（不包含2000–2002）",
                "sma_window": windows,
                "sma_return_pct": returns,
                "hold_return_pct": [0.0] * len(windows),
            }
        )
        summary = plateau_summary(curve)
        self.assertTrue(summary["plateau_found"])
        self.assertEqual(summary["plateau_start"], 150)
        self.assertEqual(summary["plateau_end"], 180)
        self.assertEqual(summary["plateau_window_count"], 4)

    def test_chart_uses_solid_sma_and_horizontal_dashed_hold(self) -> None:
        windows = list(range(30, 301, 10))
        points = pd.DataFrame(
            {
                "target": "AZO",
                "target_type": "single",
                "observation_order": 15,
                "observation_id": DEFAULT_OBSERVATION,
                "label": "总熊（不包含2000–2002）",
                "kind": "scope",
                "key": "all_ex_2000_2002",
                "sma_window": windows,
                "sma_return_pct": [float(index) for index in range(len(windows))],
                "hold_return_pct": [3.0] * len(windows),
                "sma_minus_hold_pp": [float(index) - 3.0 for index in range(len(windows))],
            }
        )
        plateaus = build_plateaus(points)
        figure = window_curve_figure(points, plateaus, "AZO")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].line.dash, None)
        self.assertEqual(figure.data[1].line.dash, "dash")
        self.assertEqual(len(set(figure.data[1].y)), 1)
        self.assertEqual(list(figure.data[0].x), windows)

    def test_performance_context_has_one_benchmark_and_equity_drawdown_pairs(self) -> None:
        targets = ["GROUP_CORE12", "GROUP_NEAR8", "GROUP_RETAIL4"]
        rows = []
        values = []
        for target in targets:
            for strategy_id in (STRATEGY_HOLD, "sma_200"):
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
        figure = performance_figure(block)
        self.assertEqual(len(figure.data), 12)
        self.assertEqual(
            sum(trace.meta["is_benchmark"] for trace in figure.data if trace.meta["panel"] == "equity"),
            1,
        )
        self.assertEqual(
            {trace.meta["panel"] for trace in figure.data}, {"equity", "drawdown"}
        )


if __name__ == "__main__":
    unittest.main()
