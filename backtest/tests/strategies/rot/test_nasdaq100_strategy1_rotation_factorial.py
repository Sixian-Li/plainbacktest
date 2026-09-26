from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.nasdaq100_strategy1_rotation_factorial import (
    RotationFactorSpec,
    factor_specs,
    cross_check_pybroker_daily_state,
    cross_check_pybroker_order_plan,
    run_rotation_factor,
    run_pybroker_order_plan,
    select_all_members,
    select_snapshot,
)


def toy_inputs(days: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2024-01-02", periods=days)
    symbols = ["A", "B", "C"]
    panel_rows = []
    state_rows = []
    for offset, date in enumerate(dates):
        for symbol_index, symbol in enumerate(symbols):
            close = 100.0 + 10 * symbol_index + offset * (symbol_index + 1)
            panel_rows.append({
                "date": date, "symbol": symbol, "open": close - 0.5,
                "high": close + 1.0, "low": close - 1.0, "close": close,
                "synthetic_bar": 0, "terminal_settlement_proxy": 0,
            })
            state_rows.append({
                "date": date, "security_id": symbol, "display_ticker": symbol,
                "strategy1_weight": 0.95 if symbol in {"A", "B"} else 0.50,
                "stochrsi_100": 0.50, "in_universe": True,
            })
    return pd.DataFrame(state_rows), pd.DataFrame(panel_rows)


class RotationFactorDefinitionTests(unittest.TestCase):
    def test_factorial_has_exactly_twelve_unique_cases(self) -> None:
        cases = factor_specs()
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({case.case_id for case in cases}), 12)
        self.assertEqual(cases[0].case_id, "FAST0_TOP20_90_REBAL")
        self.assertEqual(cases[-1].case_id, "FAST1_ALL_80_DRIFT")

    def test_selection_is_strict_ranked_and_locked(self) -> None:
        rows = pd.DataFrame({
            "security_id": [f"S{i:02d}" for i in range(23)],
            "display_ticker": [f"T{i:02d}" for i in range(23)],
            "strategy1_weight": [0.99] * 21 + [0.90, 0.80],
            "in_universe": True,
            "real_bar": True,
        })
        top = select_snapshot(rows, selection_mode="TOP20_90", locked={"S00"})
        self.assertEqual(len(top), 20)
        self.assertNotIn("S00", top["security_id"].tolist())
        self.assertNotIn("S21", top["security_id"].tolist())
        all_90 = select_snapshot(rows, selection_mode="ALL_90")
        self.assertEqual(len(all_90), 21)
        all_80 = select_snapshot(rows, selection_mode="ALL_80")
        self.assertEqual(len(all_80), 22)

    def test_all_member_baseline_does_not_require_a_ready_score(self) -> None:
        rows = pd.DataFrame({
            "security_id": ["A", "B", "C"], "display_ticker": ["A", "B", "C"],
            "strategy1_weight": [np.nan, 0.1, 1.0], "in_universe": [True, True, False],
            "real_bar": [True, True, True],
        })
        selected = select_all_members(rows)
        self.assertEqual(selected["security_id"].tolist(), ["A", "B"])


class RotationFactorLedgerTests(unittest.TestCase):
    def test_restore_equal_and_entry_exit_only_have_distinct_sizing(self) -> None:
        state, panel = toy_inputs()
        # At the second decision C replaces B while A continues.
        decision = sorted(state["date"].unique())[2]
        state.loc[(state["date"] == decision) & state["security_id"].eq("B"), "strategy1_weight"] = 0.2
        state.loc[(state["date"] == decision) & state["security_id"].eq("C"), "strategy1_weight"] = 0.98
        rebalanced = run_rotation_factor(
            state, panel, RotationFactorSpec(False, "TOP20_90", "RESTORE_EQUAL")
        )
        drift = run_rotation_factor(
            state, panel, RotationFactorSpec(False, "TOP20_90", "ENTRY_EXIT_ONLY")
        )
        execution = sorted(state["date"].unique())[3]
        prior = sorted(state["date"].unique())[2]
        drift_pos = drift.positions.pivot(index="date", columns="symbol", values="shares")
        self.assertAlmostEqual(float(drift_pos.at[execution, "A"]), float(drift_pos.at[prior, "A"]))
        rebal_pos = rebalanced.positions.pivot(index="date", columns="symbol", values="shares")
        self.assertNotAlmostEqual(float(rebal_pos.at[execution, "A"]), float(rebal_pos.at[prior, "A"]))
        self.assertIn("SCHEDULED_NEW_ENTRY", drift.orders["reason"].tolist())
        self.assertLess(max(drift.replay_checks.values()), 1e-8)
        self.assertLess(max(rebalanced.replay_checks.values()), 1e-8)

    def test_fast_exit_is_true_cross_for_prior_close_position_and_rearms_below_twenty(self) -> None:
        state, panel = toy_inputs(days=9)
        dates = sorted(state["date"].unique())
        state.loc[state["security_id"].eq("A"), "stochrsi_100"] = [0.7, 0.7, 0.9, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7]
        # The day-3 downcross exits; day-4 score below 20% clears the lock.
        state.loc[(state["date"] == dates[4]) & state["security_id"].eq("A"), "strategy1_weight"] = 0.1
        result = run_rotation_factor(
            state, panel, RotationFactorSpec(True, "TOP20_90", "ENTRY_EXIT_ONLY")
        )
        fast = result.orders[result.orders["reason"].eq("FAST_DOWNCROSS_080")]
        self.assertEqual(len(fast), 1)
        self.assertEqual(pd.Timestamp(fast.iloc[0]["date"]), pd.Timestamp(dates[3]))
        self.assertEqual(fast.iloc[0]["fill_timing"], "close")
        self.assertEqual(int(result.metrics["fast_exit_count"]), 1)

    def test_same_day_new_open_position_is_not_fast_exited(self) -> None:
        state, panel = toy_inputs(days=4)
        dates = sorted(state["date"].unique())
        state.loc[state["security_id"].eq("A"), "stochrsi_100"] = [0.9, 0.7, 0.7, 0.7]
        result = run_rotation_factor(
            state, panel, RotationFactorSpec(True, "TOP20_90", "RESTORE_EQUAL")
        )
        first_buy_date = pd.Timestamp(
            result.orders[result.orders["type"].eq("buy")].iloc[0]["date"]
        )
        self.assertEqual(first_buy_date, pd.Timestamp(dates[1]))
        self.assertFalse(
            ((result.orders["date"] == first_buy_date)
             & result.orders["reason"].eq("FAST_DOWNCROSS_080")).any()
        )

    def test_all_member_baseline_holds_all_members(self) -> None:
        state, panel = toy_inputs(days=5)
        result = run_rotation_factor(
            state,
            panel,
            RotationFactorSpec(False, "TOP20_90", "RESTORE_EQUAL"),
            all_member_baseline=True,
        )
        self.assertEqual(result.metrics["case_id"], "NDX100_ALL_REBAL")
        self.assertEqual(int(result.daily["holdings_count"].max()), 3)

    def test_pybroker_replays_mixed_open_and_close_plan(self) -> None:
        state, panel = toy_inputs(days=9)
        dates = sorted(state["date"].unique())
        state.loc[state["security_id"].eq("A"), "stochrsi_100"] = [
            0.7, 0.7, 0.9, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7
        ]
        result = run_rotation_factor(
            state, panel, RotationFactorSpec(True, "TOP20_90", "RESTORE_EQUAL"),
            cost_bps=5,
        )
        broker, broker_positions = run_pybroker_order_plan(
            panel, result.orders, initial_cash=100_000, cost_bps=5
        )
        checks = cross_check_pybroker_order_plan(broker, result.orders)
        self.assertLess(max(checks.values()), 1e-7)
        state_checks = cross_check_pybroker_daily_state(
            broker, broker_positions, result.daily, result.positions
        )
        self.assertLess(max(state_checks.values()), 1e-7)


if __name__ == "__main__":
    unittest.main()
