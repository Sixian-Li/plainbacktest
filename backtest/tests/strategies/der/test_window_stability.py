from __future__ import annotations

import unittest

import pandas as pd

from quantkit.window_stability import add_window_ranks, select_representatives, top_fraction_summary


class WindowStabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = ["p", "forced_reentry_enabled"]
        self.frame = pd.DataFrame(
            [
                {"window_id": "W1", "p": 1, "forced_reentry_enabled": True, "cagr_pct": 3.0, "sharpe": 0.2},
                {"window_id": "W1", "p": 2, "forced_reentry_enabled": False, "cagr_pct": 2.0, "sharpe": 0.4},
                {"window_id": "W2", "p": 1, "forced_reentry_enabled": True, "cagr_pct": 1.0, "sharpe": 0.6},
                {"window_id": "W2", "p": 2, "forced_reentry_enabled": False, "cagr_pct": 4.0, "sharpe": 0.3},
            ]
        )

    def test_window_ranks_are_high_is_good_percentiles(self) -> None:
        ranked = add_window_ranks(self.frame)
        best = ranked[(ranked.window_id == "W1") & (ranked.p == 1)].iloc[0]
        self.assertEqual(best.cagr_pct_rank, 1)
        self.assertEqual(best.cagr_pct_rank_percentile, 100)
        self.assertEqual(best.sharpe_rank_percentile, 0)

    def test_representatives_merge_duplicate_objective_winners_and_keep_anchor(self) -> None:
        representatives = select_representatives(
            self.frame,
            parameter_columns=self.parameters,
            window_order=["W1", "W2"],
            anchor={"p": 3, "forced_reentry_enabled": True},
        )
        self.assertEqual(len(representatives), 3)
        self.assertTrue(representatives.selection_reason.str.contains("full_history_global_best_anchor").any())

    def test_top_fraction_summary_is_grouped_by_window_and_objective(self) -> None:
        summary = top_fraction_summary(
            self.frame, parameter_columns=self.parameters, fraction=0.5
        )
        self.assertEqual(len(summary), 8)
        row = summary[(summary.window_id == "W1") & (summary.objective == "cagr_pct") & (summary.parameter == "p")].iloc[0]
        self.assertEqual(row["median"], 1)


if __name__ == "__main__":
    unittest.main()
