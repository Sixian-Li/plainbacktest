from __future__ import annotations

import unittest

import pandas as pd

from quantkit.nasdaq100_strategy1_gate_factorial import (
    GateFactorSpec,
    factorial_specs,
    run_factorial_gate,
)


def state_frame(
    scores: list[float],
    *,
    opens: list[float] | None = None,
    closes: list[float] | None = None,
    lows: list[float] | None = None,
    s42: list[float] | None = None,
    s100: list[float] | None = None,
) -> pd.DataFrame:
    count = len(scores)
    opens = opens or [100.0] * count
    closes = closes or opens
    lows = lows or [min(open_, close) * 0.99 for open_, close in zip(opens, closes)]
    s42 = s42 or [0.5] * count
    s100 = s100 or [0.5] * count
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=count),
        "symbol": "TEST",
        "open": opens,
        "high": [max(open_, close) * 1.01 for open_, close in zip(opens, closes)],
        "low": lows,
        "close": closes,
        "synthetic_bar": 0,
        "terminal_settlement_proxy": 0,
        "strategy1_weight": scores,
        "stochrsi_42": s42,
        "stochrsi_100": s100,
    })


class Nasdaq100Strategy1GateFactorialTests(unittest.TestCase):
    def test_factorial_has_all_eight_unique_cases(self) -> None:
        cases = factorial_specs()
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({case.case_id for case in cases}), 8)
        self.assertEqual(cases[0].case_id, "STOP0_STRICT0_FAST0")
        self.assertEqual(cases[-1].case_id, "STOP1_STRICT1_FAST1")

    def test_baseline_gate_uses_prior_close_and_current_open(self) -> None:
        frame = state_frame([0.95, 0.95, 0.50, 0.50], opens=[10, 11, 12, 13])
        result = run_factorial_gate(frame, GateFactorSpec())
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell"])
        self.assertEqual(result.orders["date"].tolist(), [frame.loc[1, "date"], frame.loc[3, "date"]])
        self.assertEqual(result.orders["fill_price"].tolist(), [11.0, 13.0])

    def test_strict_entry_is_an_entry_filter_not_an_exit_rule(self) -> None:
        frame = state_frame(
            [0.95, 0.95, 0.95, 0.50],
            s42=[0.10, 0.30, 0.10, 0.10],
            s100=[0.30, 0.30, 0.10, 0.10],
        )
        result = run_factorial_gate(frame, GateFactorSpec(strict_entry=True))
        self.assertEqual(result.orders["type"].tolist(), ["buy"])
        self.assertEqual(result.orders.iloc[0]["date"], frame.loc[2, "date"])
        self.assertEqual(int(result.daily.loc[2, "is_long"]), 1)

    def test_stop_starts_after_entry_and_rearms_only_below_ten_percent(self) -> None:
        frame = state_frame(
            [0.95, 0.95, 0.95, 0.05, 0.95, 0.95],
            opens=[100, 100, 100, 100, 100, 100],
            lows=[94, 94, 94, 99, 99, 99],
        )
        result = run_factorial_gate(frame, GateFactorSpec(mechanical_stop=True))
        self.assertEqual(result.orders["reason"].tolist(), [
            "GATE_ENTRY", "STOP_INTRADAY", "GATE_ENTRY",
        ])
        self.assertEqual(result.orders.iloc[1]["date"], frame.loc[2, "date"])
        self.assertAlmostEqual(float(result.orders.iloc[1]["fill_price"]), 95.0)
        self.assertEqual(result.orders.iloc[2]["date"], frame.loc[5, "date"])

    def test_stop_gap_fills_at_open_not_at_the_stop_level(self) -> None:
        frame = state_frame(
            [0.95, 0.95, 0.95],
            opens=[100, 100, 90],
            lows=[99, 99, 89],
        )
        result = run_factorial_gate(frame, GateFactorSpec(mechanical_stop=True))
        sell = result.orders[result.orders["type"].eq("sell")].iloc[0]
        self.assertEqual(sell["reason"], "STOP_GAP_OPEN")
        self.assertAlmostEqual(float(sell["fill_price"]), 90.0)

    def test_fast_exit_requires_a_downcross_and_rearms_below_twenty_percent(self) -> None:
        frame = state_frame(
            [0.95, 0.95, 0.95, 0.10, 0.95, 0.95],
            s100=[0.70, 0.90, 0.70, 0.70, 0.70, 0.70],
        )
        result = run_factorial_gate(frame, GateFactorSpec(fast_exit=True))
        self.assertEqual(result.orders["reason"].tolist(), [
            "GATE_ENTRY", "FAST_DOWNCROSS_080", "GATE_ENTRY",
        ])
        self.assertEqual(result.orders.iloc[1]["date"], frame.loc[2, "date"])
        self.assertEqual(result.orders.iloc[2]["date"], frame.loc[5, "date"])
        self.assertEqual(int(result.metrics["fast_exit_count"]), 1)

    def test_cost_and_order_replay_are_reconciled(self) -> None:
        frame = state_frame([0.95, 0.95, 0.50, 0.50], opens=[10, 10, 10, 10])
        result = run_factorial_gate(frame, GateFactorSpec(), cost_bps=5)
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 10.005)
        self.assertAlmostEqual(float(result.orders.iloc[1]["fill_price"]), 9.995)
        self.assertLess(max(result.cross_checks.values()), 1e-8)


if __name__ == "__main__":
    unittest.main()
