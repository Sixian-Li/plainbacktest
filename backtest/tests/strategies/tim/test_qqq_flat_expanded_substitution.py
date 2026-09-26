from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.analyze_qqq_flat_expanded_substitution import FORMAL_CASES, SUBSTITUTE_WINDOWS


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json"
)


class ExpandedFrozenExperimentContractTest(unittest.TestCase):
    def test_fixed_master_and_ordered_candidate_windows(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        parameters = config["parameters"]
        self.assertEqual(parameters["master_symbol"], "QQQ")
        self.assertEqual(parameters["master_sma_window"], 200)
        self.assertEqual(parameters["entry_buffer_pct"], 3.0)
        self.assertEqual(parameters["exit_buffer_pct"], 3.0)
        self.assertEqual(parameters["substitute_windows"], SUBSTITUTE_WINDOWS)
        self.assertEqual(tuple(parameters["formal_cases"]), FORMAL_CASES)
        self.assertEqual(parameters["formal_case_count_per_cost"], 8)
        self.assertEqual(parameters["formal_path_count"], 16)
        self.assertTrue(parameters["substitute_state_updates_while_qqq_long"])
        self.assertNotIn("window_scan", parameters)
        self.assertNotIn("stop_loss", parameters)

    def test_new_candidates_are_independent_paths_not_a_blended_portfolio(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        strategy = config["strategy"]
        self.assertIn("EQT", strategy["description"])
        self.assertIn("WMT", strategy["description"])
        self.assertIn("ORLY", strategy["description"])
        self.assertIn("LMT", strategy["description"])
        self.assertIn("彼此独立", strategy["plain_language"]["position"])


if __name__ == "__main__":
    unittest.main()
