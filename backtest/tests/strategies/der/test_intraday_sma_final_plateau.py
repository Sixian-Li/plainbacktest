from __future__ import annotations

import json
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

from quantkit.oat_sensitivity import build_oat_cases, sweep_values


EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.40a.1__26-08-14__qqq_intraday_sma_final_local_plateau_full_history/experiment.json"
)


class FinalLocalPlateauContractTest(unittest.TestCase):
    def test_experiment_freezes_requested_baseline_and_oat_ranges(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        parameters = config["parameters"]
        baseline = parameters["baseline"]
        self.assertEqual(
            baseline,
            {
                "A_negative_days_slow": 3,
                "B_slow_sma_window": 175,
                "C_fast_derivative_pct": -0.25,
                "D_negative_days_fast": 4,
                "E_fallback_sma_window": 305,
                "F_short_sma_center": 80,
                "F_short_sma_spacing": 10,
                "G_short_recovery_below_pct": 1.0,
                "H_reentry_sma_window": 270,
                "L_cost_stop_pct": 10.0,
                "R_forced_rebuy_pct": 0.0,
                "forced_reentry_enabled": True,
            },
        )
        sweeps = {sweep["sweep_id"]: sweep_values(sweep) for sweep in parameters["sweeps"]}
        self.assertEqual((sweeps["B"][0], sweeps["B"][-1], len(sweeps["B"])), (165.0, 185.0, 21))
        self.assertEqual((sweeps["E"][0], sweeps["E"][-1], len(sweeps["E"])), (285.0, 325.0, 41))
        self.assertEqual((sweeps["F_CENTER"][0], sweeps["F_CENTER"][-1], len(sweeps["F_CENTER"])), (65.0, 95.0, 31))
        self.assertEqual((sweeps["G"][0], sweeps["G"][-1], len(sweeps["G"])), (0.4, 1.6, 25))
        self.assertEqual((sweeps["H"][0], sweeps["H"][-1], len(sweeps["H"])), (240.0, 300.0, 21))
        self.assertEqual((sweeps["L"][0], sweeps["L"][-1], len(sweeps["L"])), (5.0, 15.0, 101))
        cases, points = build_oat_cases(baseline, parameters["sweeps"])
        self.assertEqual(len(points), 240)
        self.assertEqual(len(cases), 235)


if __name__ == "__main__":
    unittest.main()
