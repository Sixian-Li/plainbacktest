from __future__ import annotations

import unittest

import pandas as pd

from quantkit.nasdaq100_strategy1_holding_attribution import run_single_security_gate


def price_frame(
    opens: list[float],
    closes: list[float],
    *,
    synthetic: list[int] | None = None,
    terminal: list[int] | None = None,
) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(opens))
    synthetic = synthetic or [0] * len(opens)
    terminal = terminal or [0] * len(opens)
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "TEST",
            "open": opens,
            "high": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
            "low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
            "close": closes,
            "synthetic_bar": synthetic,
            "terminal_settlement_proxy": terminal,
        }
    )


class Nasdaq100Strategy1HoldingAttributionTests(unittest.TestCase):
    def test_prior_close_signal_buys_and_sells_at_next_open(self) -> None:
        frame = price_frame(
            [10.0, 10.0, 11.0, 12.5, 13.0, 14.0, 15.0],
            [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
        )
        eligible = [frame.loc[0, "date"], frame.loc[1, "date"]]
        result = run_single_security_gate(
            frame,
            eligible,
            initial_cash=100_000,
            cost_bps=0,
            post_exit_horizons=(1, 2),
        )
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell"])
        self.assertEqual(result.orders["date"].tolist(), [frame.loc[1, "date"], frame.loc[3, "date"]])
        self.assertEqual(result.orders["signal_date"].tolist(), [frame.loc[0, "date"], frame.loc[2, "date"]])
        self.assertAlmostEqual(float(result.daily.iloc[-1]["equity"]), 125_000.0)
        self.assertEqual(result.exposed_returns["component"].tolist(), [
            "entry_open_to_close",
            "held_close_to_close",
            "exit_prior_close_to_open",
        ])
        self.assertLess(result.cross_checks["max_daily_equity_reconstruction_difference"], 1e-8)
        self.assertAlmostEqual(
            float(result.exits.iloc[0]["forward_close_return_1d_pct"]),
            (14.0 / 12.5 - 1.0) * 100.0,
        )

    def test_cost_is_applied_adversely_on_both_sides(self) -> None:
        frame = price_frame([10.0, 10.0, 10.0], [10.0, 10.0, 10.0])
        result = run_single_security_gate(
            frame,
            [frame.loc[0, "date"]],
            initial_cash=100_000,
            cost_bps=5,
            post_exit_horizons=(1,),
        )
        buy, sell = result.orders.to_dict("records")
        self.assertAlmostEqual(buy["fill_price"], 10.005)
        self.assertAlmostEqual(sell["fill_price"], 9.995)
        self.assertLess(float(result.daily.iloc[-1]["equity"]), 100_000)

    def test_missing_bar_freezes_then_requires_consecutive_real_bars(self) -> None:
        frame = price_frame(
            [10.0, 10.0, 11.0, 12.0, 13.0],
            [10.0, 10.0, 11.0, 12.0, 13.0],
            synthetic=[0, 1, 0, 0, 0],
        )
        eligible = [frame.loc[0, "date"], frame.loc[2, "date"]]
        result = run_single_security_gate(frame, eligible, post_exit_horizons=(1,))
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell"])
        self.assertEqual(result.orders.iloc[0]["date"], frame.loc[3, "date"])
        self.assertGreaterEqual(result.metrics["deferred_change_count"], 1)

    def test_terminal_proxy_can_sell_but_cannot_buy(self) -> None:
        frame = price_frame(
            [10.0, 10.0, 10.0, 10.0],
            [10.0, 10.0, 10.0, 10.0],
            synthetic=[0, 0, 1, 1],
            terminal=[0, 0, 1, 1],
        )
        result = run_single_security_gate(
            frame,
            [frame.loc[0, "date"]],
            post_exit_horizons=(1,),
        )
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell"])
        self.assertEqual(int(result.orders.iloc[-1]["terminal_settlement_proxy"]), 1)
        self.assertEqual(int(result.metrics["terminal_settlement_exit_count"]), 1)
        self.assertTrue(pd.isna(result.exits.iloc[0]["forward_close_return_1d_pct"]))

    def test_all_cash_has_no_holding_metrics(self) -> None:
        frame = price_frame([10.0, 10.0, 10.0], [10.0, 10.0, 10.0])
        result = run_single_security_gate(frame, [], post_exit_horizons=(1,))
        self.assertEqual(result.metrics["holding_sessions"], 0)
        self.assertIsNone(result.metrics["holding_period_cagr_pct"])
        self.assertTrue(pd.isna(result.metrics["holding_return_sharpe"]))


if __name__ == "__main__":
    unittest.main()
