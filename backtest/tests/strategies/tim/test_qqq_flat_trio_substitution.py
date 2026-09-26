from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.qqq_flat_substitution import (
    HysteresisSpec,
    attribute_spell_overlays,
    build_case_decisions,
    build_master_flat_spells,
    build_price_panel,
    compile_exact_target_shares,
    prepare_hysteresis_state,
    run_pybroker_substitution,
    run_reference_substitution,
)
from quantkit.trend_score_portfolio import cross_check_portfolios


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json"
)


def frame(symbol: str, closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(closes))
    open_values = closes if opens is None else opens
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": open_values,
            "high": np.asarray(open_values, dtype=float) + 1.0,
            "low": np.asarray(open_values, dtype=float) - 1.0,
            "close": closes,
            "volume": 1000,
        }
    )


class HysteresisStateTest(unittest.TestCase):
    def test_master_first_valid_bar_never_enters_and_later_true_cross_does(self) -> None:
        data = frame("QQQ", [100, 100, 110, 110, 80, 80, 110, 110])
        state = prepare_hysteresis_state(
            data, "QQQ", HysteresisSpec(2, 3, 3, first_valid_level_entry=False)
        )
        ready = state[state["ready"]].reset_index(drop=True)
        self.assertEqual(ready.iloc[0]["transition"], "initial_flat")
        self.assertFalse(bool(ready.iloc[0]["is_long"]))
        self.assertIn("enter", ready["transition"].tolist())

    def test_substitute_first_valid_bar_can_be_level_eligible(self) -> None:
        data = frame("AZO", [100, 120, 121, 80, 80, 120])
        state = prepare_hysteresis_state(
            data, "AZO", HysteresisSpec(2, 3, 3, first_valid_level_entry=True)
        )
        ready = state[state["ready"]].reset_index(drop=True)
        self.assertEqual(ready.iloc[0]["transition"], "initial_enter")
        self.assertTrue(bool(ready.iloc[0]["is_long"]))

    def test_future_prices_cannot_rewrite_prior_state(self) -> None:
        data = frame("QQQ", [100, 100, 110, 110, 80, 80, 110, 110])
        base = prepare_hysteresis_state(data, "QQQ", HysteresisSpec(2))
        changed = data.copy()
        changed.loc[7, ["open", "high", "low", "close"]] = [999, 1000, 998, 999]
        mutated = prepare_hysteresis_state(changed, "QQQ", HysteresisSpec(2))
        pd.testing.assert_series_equal(
            base.loc[:6, "is_long"].reset_index(drop=True),
            mutated.loc[:6, "is_long"].reset_index(drop=True),
        )


class PortfolioExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.qqq = frame(
            "QQQ",
            [100, 100, 110, 110, 80, 80, 110, 110, 80, 80],
            [100, 101, 102, 111, 109, 81, 82, 111, 109, 81],
        )
        self.azo = frame(
            "AZO",
            [50, 55, 56, 57, 58, 59, 60, 61, 62, 63],
            [50, 51, 55, 56, 57, 58, 59, 60, 61, 62],
        )
        self.master = prepare_hysteresis_state(
            self.qqq, "QQQ", HysteresisSpec(2, 3, 3, False)
        )
        self.alt = prepare_hysteresis_state(
            self.azo, "AZO", HysteresisSpec(2, 3, 3, True)
        )

    def test_qqq_has_priority_and_substitute_is_only_used_while_master_flat(self) -> None:
        decisions = build_case_decisions(
            self.master, substitute_symbol="AZO", substitute=self.alt
        )
        self.assertTrue((decisions.loc[decisions["master_state"] == "long", "target_asset"] == "QQQ").all())
        used_alt = decisions[decisions["target_asset"] == "AZO"]
        self.assertFalse(used_alt.empty)
        self.assertTrue((used_alt["master_state"] == "flat").all())

    def test_sell_first_rotation_and_independent_ledger_match_pybroker(self) -> None:
        decisions = build_case_decisions(
            self.master, substitute_symbol="AZO", substitute=self.alt
        )
        ready_dates = self.master.loc[self.master["ready"], "date"]
        panel = build_price_panel({"QQQ": self.qqq, "AZO": self.azo}, ready_dates)
        policy = ExplicitFillPolicy("open", 5.0)
        plan = compile_exact_target_shares(
            panel, decisions, initial_cash=100_000, policy=policy
        )
        result, positions = run_pybroker_substitution(
            panel, plan.target_shares, initial_cash=100_000, policy=policy
        )
        reference = run_reference_substitution(
            panel, decisions, initial_cash=100_000, policy=policy
        )
        differences = cross_check_portfolios(
            result, positions, reference, tolerance=1e-6
        )
        self.assertLessEqual(max(differences.values()), 1e-6)
        rotations = plan.executions[
            (plan.executions["sold_asset"] != "")
            & (plan.executions["bought_asset"] != "")
        ]
        self.assertFalse(rotations.empty)
        self.assertTrue(
            (rotations["post_equity_at_raw_open"] < rotations["pre_equity_at_raw_open"]).all()
        )


class FlatSpellAttributionTest(unittest.TestCase):
    def test_initial_wait_and_post_sell_are_separate_and_dotcom_is_attribution_only(self) -> None:
        qqq = frame("QQQ", [100, 100, 110, 110, 80, 80, 110, 110, 80, 80])
        azo = frame("AZO", [50, 55, 56, 57, 58, 59, 60, 61, 62, 63])
        master = prepare_hysteresis_state(qqq, "QQQ", HysteresisSpec(2))
        alt = prepare_hysteresis_state(azo, "AZO", HysteresisSpec(2, 3, 3, True))
        decisions = build_case_decisions(master, substitute_symbol="AZO", substitute=alt)
        spells = build_master_flat_spells(master)
        self.assertIn("initial_wait", spells["spell_type"].tolist())
        self.assertIn("post_sell", spells["spell_type"].tolist())
        events = attribute_spell_overlays(
            spells,
            decisions,
            azo,
            substitute_symbol="AZO",
            policy=ExplicitFillPolicy("open", 0),
            dotcom_start=pd.Timestamp("2024-01-01"),
            dotcom_end=pd.Timestamp("2024-12-31"),
        )
        self.assertEqual(len(events), len(spells))
        self.assertTrue(events["overlaps_dotcom_bear"].all())
        # Attribution labels do not alter the already-frozen target sequence.
        pd.testing.assert_series_equal(
            decisions["target_asset"],
            build_case_decisions(master, substitute_symbol="AZO", substitute=alt)["target_asset"],
        )


class FrozenExperimentContractTest(unittest.TestCase):
    def test_fixed_master_and_selected_substitute_windows(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        parameters = config["parameters"]
        self.assertEqual(parameters["master_symbol"], "QQQ")
        self.assertEqual(parameters["master_sma_window"], 200)
        self.assertEqual(parameters["substitute_windows"], {"AZO": 237, "TLT": 160, "MO": 110})
        self.assertEqual(parameters["entry_buffer_pct"], 3.0)
        self.assertEqual(parameters["exit_buffer_pct"], 3.0)
        self.assertTrue(parameters["substitute_state_updates_while_qqq_long"])
        self.assertEqual(parameters["formal_path_count"], 8)
        self.assertNotIn("window_scan", parameters)


if __name__ == "__main__":
    unittest.main()
