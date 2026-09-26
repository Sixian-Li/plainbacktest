from __future__ import annotations

import json
import unittest
from pathlib import Path

import pandas as pd

from quantkit.qqq_flat_substitution import build_case_decisions
from scripts.run_qqq_flat_bear24_sma_ablation import (
    direct_hold_state,
    expected_cases,
    ordered_assets,
    shared_execution_calendar,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json"
)


def prices(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": 1000,
        }
    )


class Bear24AblationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))

    def test_frozen_groups_windows_and_cases(self) -> None:
        parameters = self.config["parameters"]
        assets, families = ordered_assets(parameters)
        windows = parameters["substitute_windows"]
        self.assertEqual(len(assets), 24)
        self.assertEqual([sum(value == group for value in families.values()) for group in ("core12", "near_core8", "retail4")], [12, 8, 4])
        self.assertEqual(list(windows), assets)
        self.assertEqual(windows["AZO"], 237)
        self.assertEqual(windows["TLT"], 160)
        self.assertEqual(windows["SO"], 13)
        self.assertEqual(windows["ED"], 7)
        self.assertEqual(windows["GIS"], 500)
        self.assertEqual(tuple(parameters["formal_cases"]), expected_cases(assets, windows))
        self.assertEqual(parameters["formal_case_count_per_cost"], 49)
        self.assertEqual(parameters["formal_path_count"], 98)
        self.assertNotIn("stop_loss", parameters)
        self.assertNotIn("window_scan", parameters)

    def test_direct_mode_is_eligible_after_listing_without_sma(self) -> None:
        dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        master = prices(dates, [100, 99, 98, 97])
        master["symbol"] = "QQQ"
        master["ready"] = True
        master["is_long"] = False
        master["transition"] = ["initial_flat", "hold_flat", "hold_flat", "hold_flat"]
        substitute = direct_hold_state(prices(dates[2:], [50, 51]), "AZO")
        decisions = build_case_decisions(
            master, substitute_symbol="AZO", substitute=substitute
        )
        self.assertEqual(
            decisions["target_asset"].tolist(), ["CASH", "CASH", "AZO", "AZO"]
        )
        self.assertTrue(substitute["upper_rail"].isna().all())
        self.assertTrue(substitute["is_long"].all())

    def test_common_calendar_ignores_prelisting_but_removes_postlisting_gap(self) -> None:
        master_dates = pd.Series(pd.to_datetime([
            "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"
        ]))
        substitutes = {
            "OLD": prices(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"], [1, 1, 1, 1]),
            "NEW": prices(["2020-01-06"], [1]),
        }
        common, exclusions = shared_execution_calendar(master_dates, substitutes)
        self.assertEqual([date.strftime("%Y-%m-%d") for date in common], [
            "2020-01-02", "2020-01-03", "2020-01-06"
        ])
        self.assertEqual(exclusions, [{"date": "2020-01-07", "missing_symbol": "NEW"}])

    def test_strategy_text_freezes_independent_direct_and_sma_paths(self) -> None:
        strategy = self.config["strategy"]
        self.assertIn("DIRECT", strategy["description"])
        self.assertIn("SMA", strategy["description"])
        self.assertIn("49条路径彼此独立", strategy["plain_language"]["position"])
        self.assertEqual(self.config["reporting"]["template_id"], "interactive_research_v5")


if __name__ == "__main__":
    unittest.main()
