from __future__ import annotations

import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import pandas as pd

from quantkit.experiment import load_experiment
from scripts.analyze_intraday_sma_reentry_grid import markdown_report
from scripts.run_intraday_sma_reentry_grid import ablation_matrix
from quantkit.intraday_sma import ALL_SIGNALS
from scripts.analyze_intraday_sma_backtest import json_safe


EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021"
FULL_HISTORY_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.20a.3__26-08-13__qqq_intraday_sma_reentry_grid_full_history"
)


class IntradayReentryGridContractTest(unittest.TestCase):
    def test_grid_and_common_start_contract_are_frozen(self) -> None:
        context = load_experiment(EXPERIMENT)
        parameters = context.config["parameters"]
        self.assertEqual(parameters["initial_position"], "flat")
        self.assertEqual(parameters["R_forced_rebuy_pct_grid"], list(range(11)))
        self.assertEqual(parameters["C_fast_derivative_pct"], -0.20)
        self.assertEqual(parameters["C_fast_derivative_mode"], "all_short_smas")
        self.assertEqual(parameters["display_R_forced_rebuy_pct"], 5)
        self.assertIn("first actual buy fill", context.config["benchmark"])
        self.assertEqual(context.config["reporting"]["template_id"], "interactive_research_v3")

    def test_full_history_ablation_contract_is_frozen(self) -> None:
        context = load_experiment(FULL_HISTORY_EXPERIMENT)
        parameters = context.config["parameters"]
        self.assertEqual(parameters["single_rule_ablation"]["disabled_signals"], list(ALL_SIGNALS))
        self.assertEqual(parameters["single_rule_ablation"]["metric_start"], (
            "Use the all-rules common first-entry date for every ablation; later entry caused by "
            "removing a buy rule remains cash and is included in performance."
        ))

    def test_json_safe_parameter_rows_keep_first_entry_date_serializable(self) -> None:
        rows = pd.DataFrame({"first_entry_date": ["2021-02-24"], "cagr_pct": [12.3]})
        payload = json_safe(rows.to_dict("records"))
        self.assertEqual(payload[0]["first_entry_date"], "2021-02-24")

    def test_json_safe_serializes_timestamp_objects(self) -> None:
        payload = json_safe({"first_entry_date": pd.Timestamp("2021-02-24")})
        self.assertEqual(payload["first_entry_date"], "2021-02-24T00:00:00")

    def test_markdown_uses_configured_observation_start(self) -> None:
        summary = {
            "analysis_start": "2010-01-04",
            "first_entry_date": "2010-02-01",
            "first_entry_fill": 50.0,
            "benchmark": {
                "total_return_pct": 1.0,
                "cagr_pct": 2.0,
                "sharpe": 0.5,
                "max_drawdown_pct": -3.0,
            },
        }
        results = pd.DataFrame([
            {
                "R_forced_rebuy_pct": 0,
                "total_return_pct": 1.0,
                "cagr_pct": 2.0,
                "sharpe": 0.5,
                "max_drawdown_pct": -3.0,
                "exposure_pct": 80.0,
                "order_count": 2,
                "forced_reentry_count": 0,
            }
        ])
        ablations = pd.DataFrame([
            {
                "R_forced_rebuy_pct": 5,
                "disabled_signal": None,
                "cagr_pct": 2.0,
                "delta_cagr_pct_points": 0.0,
                "sharpe": 0.5,
                "delta_sharpe": 0.0,
                "max_drawdown_pct": -3.0,
                "order_count": 2,
            }
        ])
        rendered = markdown_report(summary, results, ablations, 5, "run_test")
        self.assertIn("观察起点：2010-01-04", rendered)
        self.assertNotIn("观察起点：2021-01-04", rendered)

    def test_ablation_matrix_covers_all_rules_for_every_r(self) -> None:
        matrix = ablation_matrix([0.0, 1.0, 2.0])
        self.assertEqual(len(matrix), 3 * (1 + len(ALL_SIGNALS)))
        for r_pct in (0.0, 1.0, 2.0):
            disabled = [signal for observed_r, signal in matrix if observed_r == r_pct]
            self.assertEqual(disabled, [None, *ALL_SIGNALS])


if __name__ == "__main__":
    unittest.main()
