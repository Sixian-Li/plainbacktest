from __future__ import annotations

import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

import pandas as pd

from quantkit.experiment import load_experiment
from quantkit.intraday_sma import (
    BUY_FORCED_REENTRY,
    BUY_SMA200_CROSS,
    SELL_COST_STOP,
    SELL_SLOW_TREND,
    IntradaySmaSpec,
    build_order_plan,
)


EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history"
)
DISABLED = frozenset((BUY_FORCED_REENTRY, SELL_COST_STOP, SELL_SLOW_TREND))


class IntradayCombinationAblationContractTest(unittest.TestCase):
    def test_experiment_freezes_exact_combination_and_no_r_grid(self) -> None:
        context = load_experiment(EXPERIMENT)
        parameters = context.config["parameters"]
        self.assertEqual(frozenset(parameters["disabled_signals"]), DISABLED)
        self.assertNotIn("R_forced_rebuy_pct_grid", parameters)
        self.assertEqual(parameters["R_forced_rebuy_pct"], 0.0)

    def test_forced_reentry_threshold_is_inert_when_disabled(self) -> None:
        row = pd.Series(
            {
                "date": pd.Timestamp("2026-01-02"),
                "prior_date": pd.Timestamp("2025-12-31"),
                "eligible_buy_sma200_cross": True,
                "trigger_buy_sma200_cross": 100.0,
                "eligible_buy_short_recovery": False,
            }
        )
        plans = []
        for r_pct in (0.0, 10.0):
            plan = build_order_plan(
                row,
                IntradaySmaSpec(r_forced_rebuy_pct=r_pct),
                is_long=False,
                cost_basis=None,
                last_sell_price=95.0,
                disabled_signals=DISABLED,
            )
            plans.append([(trigger.signal, trigger.price) for trigger in plan.triggers])
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[0], [(BUY_SMA200_CROSS, 100.0)])


if __name__ == "__main__":
    unittest.main()
