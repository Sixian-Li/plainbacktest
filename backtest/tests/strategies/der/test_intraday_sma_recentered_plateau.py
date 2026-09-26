from __future__ import annotations

import json
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

from quantkit.oat_sensitivity import build_oat_cases, sweep_values


EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.41__26-08-14__qqq_intraday_sma_recentered_local_plateau_full_history/experiment.json"
)


class RecenteredLocalPlateauContractTest(unittest.TestCase):
    def test_recentered_baseline_and_dense_h_sweep_are_exact(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        parameters = config["parameters"]
        baseline = parameters["baseline"]
        self.assertEqual(baseline["B_slow_sma_window"], 176)
        self.assertEqual(baseline["E_fallback_sma_window"], 301)
        self.assertEqual(baseline["G_short_recovery_below_pct"], 0.95)
        self.assertEqual(baseline["H_reentry_sma_window"], 264)
        self.assertEqual(baseline["A_negative_days_slow"], 3)
        self.assertEqual(baseline["C_fast_derivative_pct"], -0.25)
        self.assertEqual(baseline["D_negative_days_fast"], 4)
        self.assertEqual(baseline["F_short_sma_center"], 80)
        self.assertEqual(baseline["F_short_sma_spacing"], 10)
        self.assertEqual(baseline["L_cost_stop_pct"], 10.0)
        self.assertEqual(baseline["R_forced_rebuy_pct"], 0.0)
        self.assertTrue(baseline["forced_reentry_enabled"])

        sweeps = {sweep["sweep_id"]: sweep_values(sweep) for sweep in parameters["sweeps"]}
        self.assertEqual((sweeps["H"][0], sweeps["H"][-1], len(sweeps["H"])), (200.0, 330.0, 131))
        self.assertEqual((sweeps["B"][0], sweeps["B"][-1], len(sweeps["B"])), (165.0, 185.0, 21))
        self.assertEqual((sweeps["E"][0], sweeps["E"][-1], len(sweeps["E"])), (285.0, 325.0, 41))
        self.assertEqual((sweeps["F_CENTER"][0], sweeps["F_CENTER"][-1], len(sweeps["F_CENTER"])), (65.0, 95.0, 31))
        self.assertEqual((sweeps["G"][0], sweeps["G"][-1], len(sweeps["G"])), (0.4, 1.6, 25))
        self.assertEqual((sweeps["L"][0], sweeps["L"][-1], len(sweeps["L"])), (5.0, 15.0, 101))
        cases, points = build_oat_cases(baseline, parameters["sweeps"])
        self.assertEqual(len(points), 350)
        self.assertEqual(len(cases), 345)


if __name__ == "__main__":
    unittest.main()
