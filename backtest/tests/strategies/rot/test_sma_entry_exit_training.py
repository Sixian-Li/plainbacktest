from __future__ import annotations

import unittest

import pandas as pd

from quantkit.sma_entry_exit_training import (
    PARAMETER_COLUMNS,
    build_staged_cases,
    select_representative,
)


BASELINE = {
    "short_center": 30,
    "short_spacing": 5,
    "long_window": 200,
    "buy_short_buffer_pct": 2.0,
    "buy_long_buffer_pct": 2.0,
    "r1_decline_days": 3,
    "r1_min_daily_decline_pct": 0.0,
    "r3_sell_buffer_pct": 0.0,
}


class SmaEntryExitTrainingTest(unittest.TestCase):
    def test_oat_cases_are_deduplicated_and_change_one_parameter(self) -> None:
        cases, points = build_staged_cases(
            {
                "baseline": BASELINE,
                "stage_1_oat_sweeps": [
                    {
                        "sweep_id": "long",
                        "parameter": "long_window",
                        "label": "long",
                        "values": [190, 200, 210],
                    },
                    {
                        "sweep_id": "buffer",
                        "parameter": "buy_short_buffer_pct",
                        "label": "buffer",
                        "values": [1.5, 2.0, 2.5],
                    },
                ],
            }
        )
        self.assertEqual(len(cases), 5)
        self.assertEqual(points["is_baseline"].sum(), 2)
        baseline = cases[cases["long_window"].eq(200) & cases["buy_short_buffer_pct"].eq(2.0)].iloc[0]
        for row in cases.itertuples(index=False):
            changed = sum(getattr(row, name) != baseline[name] for name in PARAMETER_COLUMNS)
            self.assertLessEqual(changed, 1)

    def test_selection_is_drawdown_first_inside_return_guard(self) -> None:
        rows = []
        for index, (drawdown, total_return, passes) in enumerate(
            [(-20.0, 100.0, True), (-15.0, 70.0, True), (-10.0, 30.0, False)]
        ):
            rows.append(
                {
                    "case_id": f"c{index}",
                    **BASELINE,
                    "long_window": 190 + index * 10,
                    "max_drawdown_pct": drawdown,
                    "total_return_pct": total_return,
                    "sharpe": 0.5,
                    "passes_guard": passes,
                }
            )
        winner = select_representative(
            pd.DataFrame(rows),
            {
                "plateau_drawdown_tolerance_pct_points": 0.5,
                "plateau_return_tolerance_pct_points": 5.0,
            },
        )
        self.assertEqual(winner["case_id"], "c1")


if __name__ == "__main__":
    unittest.main()
