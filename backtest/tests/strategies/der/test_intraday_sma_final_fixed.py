from __future__ import annotations

import json
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import pandas as pd

from scripts.run_intraday_sma_final_fixed import (
    EXPECTED_PARAMETERS,
    annual_returns,
    validate_configured_fixed_parameters,
    validate_frozen_parameters,
)
from scripts.audit_workspace import is_sparse_checkout




class FinalFixedStrategyContractTest(unittest.TestCase):
    def test_experiment_freezes_exact_user_parameters(self) -> None:
        config = json.loads(
            (BACKTEST_ROOT / "experiments/DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history/experiment.json").read_text()
        )
        parameters = config["parameters"]
        validate_frozen_parameters(parameters)
        self.assertEqual(
            {name: parameters[name] for name in EXPECTED_PARAMETERS}, EXPECTED_PARAMETERS
        )
        self.assertEqual(parameters["analysis_start"], "1999-03-10")
        self.assertEqual(parameters["analysis_end"], "2026-08-04")

    def test_annual_returns_chain_from_initial_cash(self) -> None:
        dates = pd.to_datetime(["2020-06-01", "2020-12-31", "2021-12-31"])
        strategy = pd.DataFrame({"date": dates, "equity": [100.0, 110.0, 99.0]})
        benchmark = pd.DataFrame({"date": dates, "equity": [100.0, 120.0, 132.0]})
        result = annual_returns(strategy, benchmark, initial_cash=100.0)
        self.assertAlmostEqual(result.iloc[0]["strategy_return_pct"], 10.0)
        self.assertAlmostEqual(result.iloc[1]["strategy_return_pct"], -10.0)
        self.assertAlmostEqual(result.iloc[0]["benchmark_return_pct"], 20.0)
        self.assertAlmostEqual(result.iloc[1]["benchmark_return_pct"], 10.0)

    def test_configured_fixed_contract_accepts_recentered_cross_asset_vector(self) -> None:
        config = json.loads(
            (BACKTEST_ROOT / "experiments/DER/DER-v0.41a.1__26-08-14__spy_intraday_sma_recentered_fixed_2000_2020/experiment.json").read_text()
        )
        parameters = config["parameters"]
        validate_configured_fixed_parameters(parameters)
        self.assertEqual(config["symbols"], ["SPY"])
        self.assertEqual(parameters["analysis_start"], "2000-01-03")
        self.assertEqual(parameters["analysis_end"], "2020-12-31")
        self.assertEqual(parameters["B_slow_sma_window"], 176)
        self.assertEqual(parameters["E_fallback_sma_window"], 301)
        self.assertEqual(parameters["G_short_recovery_below_pct"], 0.95)
        self.assertEqual(parameters["H_reentry_sma_window"], 264)

    def test_configured_fixed_contract_rejects_inconsistent_short_windows(self) -> None:
        parameters = {
            **EXPECTED_PARAMETERS,
            "F_short_sma_windows": [69, 80, 90],
            "C_fast_derivative_mode": "all_short_smas",
            "evaluation_start": "analysis_start",
            "initial_position": "flat",
            "max_trades_per_day": 1,
            "analysis_start": "2000-01-03",
            "analysis_end": "2020-12-31",
        }
        with self.assertRaisesRegex(ValueError, "center-spacing"):
            validate_configured_fixed_parameters(parameters)

    def test_spy_locked_test_freezes_the_training_representative(self) -> None:
        training_path = (
            BACKTEST_ROOT
            / "tests/fixtures/spy_training_metrics.json"
        )
        if not training_path.is_file() and is_sparse_checkout(BACKTEST_ROOT.parent):
            self.skipTest("historical training run is intentionally unmaterialized")
        training = json.loads(training_path.read_text())
        locked = json.loads(
            (
                BACKTEST_ROOT
                / "experiments/DER/DER-v0.50a.2__26-08-14__spy_intraday_sma_trained_fixed_2003_2013/experiment.json"
            ).read_text()
        )
        parameters = locked["parameters"]
        validate_configured_fixed_parameters(parameters)
        self.assertEqual(parameters["analysis_start"], "2003-01-02")
        self.assertEqual(parameters["analysis_end"], "2013-12-31")
        self.assertEqual(parameters["remaining_unopened_start"], "2014-01-02")
        self.assertEqual(parameters["training_run_id"], "run_20260813T183932Z_ee00baab")
        for name in EXPECTED_PARAMETERS:
            if name in {"F_short_sma_windows", "forced_reentry_enabled"}:
                continue
            self.assertEqual(parameters[name], training["selected_parameters"][name])
        self.assertTrue(parameters["forced_reentry_enabled"])
        center = int(training["selected_parameters"]["F_short_sma_center"])
        spacing = int(training["selected_parameters"]["F_short_sma_spacing"])
        self.assertEqual(parameters["F_short_sma_windows"], [center - spacing, center, center + spacing])


if __name__ == "__main__":
    unittest.main()
