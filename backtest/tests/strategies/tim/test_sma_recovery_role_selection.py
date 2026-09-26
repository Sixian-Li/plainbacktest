from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.sma_recovery_probation_search import build_recovery_grid_cases
from quantkit.sma_recovery_role_selection import (
    analyze_role_constrained_surface,
    apply_long_buy_short_sell_gate,
)
from scripts.run_sma_recovery_probation_role_grid import role_deflated_sharpe_probability


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015/experiment.json"
)


def synthetic_surface(metric) -> pd.DataFrame:
    rows = []
    for buy in range(80, 161, 10):
        for sell in range(80, 161, 10):
            rows.append(
                {
                    "buy_window": buy,
                    "sell_window": sell,
                    "primary_metric": float(metric(buy, sell)),
                    "cagr_pct": float(metric(buy, sell)) + 5.0,
                    "sharpe": float(metric(buy, sell)) / 10.0,
                    "order_count": 50 + abs(buy - sell) // 10,
                    "identifiable": True,
                }
            )
    return apply_long_buy_short_sell_gate(pd.DataFrame(rows))


class RecoveryRoleDefinitionTest(unittest.TestCase):
    def test_frozen_grid_calculates_1444_and_selects_only_703_role_pairs(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        cases = build_recovery_grid_cases(config["parameters"])
        cases["identifiable"] = True
        gated = apply_long_buy_short_sell_gate(cases)
        self.assertEqual(len(gated), 1_444)
        self.assertEqual(int(gated["role_consistent"].sum()), 703)
        self.assertEqual(int(gated["identifiable"].sum()), 703)
        self.assertTrue((gated.loc[gated["identifiable"], "buy_window"] > gated.loc[gated["identifiable"], "sell_window"]).all())

    def test_invalid_high_scores_cannot_enter_surface_or_local_neighborhood(self) -> None:
        frame = synthetic_surface(
            lambda buy, sell: (
                1_000.0
                if buy <= sell
                else 20.0 - abs(buy - 140) / 10.0 - abs(sell - 100) / 10.0
            )
        )
        surface = analyze_role_constrained_surface(
            frame,
            metric="primary_metric",
            top_quantile=0.75,
            minimum_component_cells=1,
            minimum_buy_span=0,
            minimum_sell_span=0,
        )
        representative = surface["representative"]
        self.assertIsNotNone(representative)
        self.assertGreater(representative["buy_window"], representative["sell_window"])
        self.assertEqual(surface["role_consistent_cell_count"], 36)
        self.assertTrue(
            all(
                item["buy_window"] > item["sell_window"]
                for item in surface["largest_component"]["cells"]
            )
        )

    def test_semantic_diagonal_is_a_selection_boundary(self) -> None:
        frame = synthetic_surface(
            lambda buy, sell: 100.0 - abs((buy - sell) - 10.0) if buy > sell else 1_000.0
        )
        surface = analyze_role_constrained_surface(
            frame,
            metric="primary_metric",
            top_quantile=0.8,
            minimum_component_cells=1,
            minimum_buy_span=0,
            minimum_sell_span=0,
        )
        self.assertIn("role_diagonal", surface["largest_component"]["boundary_sides"])
        self.assertFalse(surface["largest_component"]["structural_pass"])

    def test_conservative_dsr_uses_all_eligible_roles_not_all_scanned_cells(self) -> None:
        returns = np.linspace(-0.01, 0.012, 300)
        trial_sharpes = np.linspace(0.1, 0.8, 703)
        result = role_deflated_sharpe_probability(
            returns,
            trial_sharpes,
            trial_count=1_444.0,
        )
        self.assertEqual(result["trial_policy"], "all_eligible_role_cases")
        self.assertEqual(result["trial_count"], 703.0)


if __name__ == "__main__":
    unittest.main()
