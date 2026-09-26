from __future__ import annotations

import unittest

import pandas as pd

from quantkit.nasdaq100_p24_portfolio import (
    FACTORIAL_CASES,
    ORIGINAL_NOT_STRICT,
    ORIGINAL_P24,
    STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
    STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL,
    STRICT_LEVEL_CHANGE_EQUAL,
    STRICT_LEVEL_DAILY_EQUAL,
    STRICT_P24,
    prepare_security_state,
    run_p24_factorial_portfolio,
    run_p24_portfolio,
    selected_ids,
)


class Nasdaq100P24PortfolioTest(unittest.TestCase):
    @staticmethod
    def _factorial_frames(
        original: list[int], strict: list[int], *, two_symbols: bool = False
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
        dates = pd.bdate_range("2020-01-02", periods=len(original))
        states: list[dict[str, object]] = []
        prices: list[dict[str, object]] = []
        symbols = ("A", "B") if two_symbols else ("A",)
        for symbol in symbols:
            for index, date in enumerate(dates):
                original_value = int(original[index])
                strict_value = int(strict[index])
                states.append({
                    "date": date,
                    "security_id": symbol,
                    "display_ticker": symbol,
                    "in_universe": 1,
                    "real_bar": 1,
                    "original_target_long": original_value,
                    "strict_target_long": strict_value,
                    "difference_target_long": original_value and not strict_value,
                })
                if symbol == "A":
                    close = 100.0 + index * 10.0
                else:
                    close = 100.0 - index * 8.0
                prices.append({
                    "date": date,
                    "symbol": symbol,
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "synthetic_bar": 0,
                    "terminal_settlement_proxy": 0,
                })
        return pd.DataFrame(states), pd.DataFrame(prices), dates

    def test_factorial_cases_are_exact_cartesian_product(self) -> None:
        self.assertEqual(
            list(FACTORIAL_CASES),
            [
                STRICT_LEVEL_DAILY_EQUAL,
                STRICT_LEVEL_CHANGE_EQUAL,
                STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL,
                STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
            ],
        )
        policies = {
            (item["holding_policy"], item["rebalance_policy"])
            for item in FACTORIAL_CASES.values()
        }
        self.assertEqual(len(policies), 4)

    def test_hysteresis_enters_strict_holds_original_and_reenters_only_strict(self) -> None:
        state, panel, dates = self._factorial_frames(
            original=[1, 1, 1, 0, 1, 1, 1, 1],
            strict=[0, 1, 0, 0, 0, 1, 0, 0],
        )
        result = run_p24_factorial_portfolio(
            state,
            panel,
            STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
            initial_cash=100_000.0,
            cost_bps=0.0,
        )
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell", "buy"])
        self.assertEqual(
            result.orders["date"].tolist(),
            [dates[2], dates[4], dates[6]],
        )
        self.assertEqual(
            result.daily.set_index("date")["is_long"].tolist(),
            [0, 0, 1, 1, 0, 0, 1, 1],
        )

    def test_selection_change_policy_skips_unchanged_weight_drift(self) -> None:
        state, panel, dates = self._factorial_frames(
            original=[1, 1, 1, 1, 1],
            strict=[1, 1, 1, 1, 1],
            two_symbols=True,
        )
        daily = run_p24_factorial_portfolio(
            state, panel, STRICT_LEVEL_DAILY_EQUAL, initial_cash=100_000.0, cost_bps=0.0
        )
        change_only = run_p24_factorial_portfolio(
            state, panel, STRICT_LEVEL_CHANGE_EQUAL, initial_cash=100_000.0, cost_bps=0.0
        )
        self.assertGreater(len(daily.orders), len(change_only.orders))
        self.assertEqual(change_only.orders["date"].drop_duplicates().tolist(), [dates[1]])
        self.assertEqual(change_only.orders["type"].tolist(), ["buy", "buy"])
        self.assertEqual(int(change_only.metrics["skipped_unchanged_selection_count"]), 3)

    def test_selection_change_policy_retries_an_unavailable_target(self) -> None:
        state, panel, dates = self._factorial_frames(
            original=[1, 1, 1, 1, 1],
            strict=[1, 1, 1, 1, 1],
        )
        panel.loc[panel["date"].eq(dates[1]), "synthetic_bar"] = 1
        result = run_p24_factorial_portfolio(
            state,
            panel,
            STRICT_LEVEL_CHANGE_EQUAL,
            initial_cash=100_000.0,
            cost_bps=0.0,
        )
        self.assertEqual(result.orders["type"].tolist(), ["buy"])
        self.assertEqual(result.orders["date"].tolist(), [dates[3]])
        self.assertGreaterEqual(int(result.metrics["unavailable_target_deferral_count"]), 2)
        self.assertGreaterEqual(int(result.metrics["rebalance_execution_count"]), 3)

    def test_factorial_strict_daily_exactly_preserves_parent_strict_case(self) -> None:
        state, panel, _ = self._factorial_frames(
            original=[1, 1, 1, 1, 1],
            strict=[0, 1, 1, 0, 1],
            two_symbols=True,
        )
        parent = run_p24_portfolio(
            state, panel, STRICT_P24, initial_cash=100_000.0, cost_bps=10.0
        )
        factorial = run_p24_factorial_portfolio(
            state, panel, STRICT_LEVEL_DAILY_EQUAL, initial_cash=100_000.0, cost_bps=10.0
        )
        pd.testing.assert_frame_equal(
            parent.daily.reset_index(drop=True),
            factorial.daily.reset_index(drop=True),
            check_exact=True,
        )
        pd.testing.assert_frame_equal(
            parent.orders.drop(columns="reason").reset_index(drop=True),
            factorial.orders.drop(columns="reason").reset_index(drop=True),
            check_exact=True,
        )

    def test_strict_state_is_subset_and_difference_partitions_original(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=16)
        close = pd.Series([100 + value for value in range(16)], dtype=float)
        raw = pd.DataFrame({
            "date": dates, "symbol": "A", "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 1000,
        })
        parameters = {
            "fixed_parameters": {
                "long_sma_window": 3, "long_slope_lookback": 2,
                "short_sma_window": 2, "short_regression_window": 3,
                "entry_confirmation_sessions": 2,
            },
            "original_thresholds": {
                "long_slope_threshold_daily_pct": 0.0,
                "short_quality_threshold_daily_pct": 0.0,
            },
            "strict_thresholds": {
                "long_slope_threshold_daily_pct": 0.1,
                "short_quality_threshold_daily_pct": 0.1,
            },
        }
        state = prepare_security_state(raw, dates, parameters)
        self.assertFalse((state["strict_target_long"] & ~state["original_target_long"]).any())
        partition = state["strict_target_long"] | state["difference_target_long"]
        self.assertTrue(partition.equals(state["original_target_long"]))
        first_eligible = state.index[state["original_eligible"]][0]
        self.assertFalse(bool(state.loc[first_eligible, "original_target_long"]))
        self.assertTrue(bool(state.loc[first_eligible + 1, "original_target_long"]))

    def test_selection_has_no_ranking_cap(self) -> None:
        snapshot = pd.DataFrame({
            "security_id": ["C", "A", "B"],
            "in_universe": [1, 1, 1], "real_bar": [1, 1, 1],
            "original_target_long": [1, 1, 1],
            "strict_target_long": [0, 1, 0],
            "difference_target_long": [1, 0, 1],
        })
        self.assertEqual(selected_ids(snapshot, ORIGINAL_P24), ["A", "B", "C"])
        self.assertEqual(selected_ids(snapshot, STRICT_P24), ["A"])
        self.assertEqual(selected_ids(snapshot, ORIGINAL_NOT_STRICT), ["B", "C"])

    def test_close_selection_executes_equal_weight_at_next_open(self) -> None:
        dates = pd.bdate_range("2020-01-02", periods=4)
        states = []
        prices = []
        for symbol, strict in (("A", 1), ("B", 0)):
            for date in dates:
                states.append({
                    "date": date, "security_id": symbol, "display_ticker": symbol,
                    "in_universe": 1, "real_bar": 1,
                    "original_target_long": 1, "strict_target_long": strict,
                    "difference_target_long": 1 - strict,
                })
                prices.append({
                    "date": date, "symbol": symbol, "open": 100.0, "high": 101.0,
                    "low": 99.0, "close": 100.0, "synthetic_bar": 0,
                    "terminal_settlement_proxy": 0,
                })
        result = run_p24_portfolio(
            pd.DataFrame(states), pd.DataFrame(prices), ORIGINAL_P24,
            initial_cash=100_000.0, cost_bps=0.0,
        )
        first_orders = result.orders[result.orders["date"].eq(dates[1])]
        self.assertEqual(first_orders["type"].tolist(), ["buy", "buy"])
        self.assertEqual(first_orders["symbol"].tolist(), ["A", "B"])
        self.assertAlmostEqual(float(first_orders.iloc[0]["shares"]), 500.0)
        self.assertAlmostEqual(float(first_orders.iloc[1]["shares"]), 500.0)
        self.assertEqual(int(result.daily.iloc[1]["holdings_count"]), 2)
        self.assertTrue(
            (result.orders["shares"].abs() * result.orders["fill_price"].abs()).gt(1e-9).all()
        )
        self.assertLessEqual(max(result.replay_checks.values()), 1e-6)


if __name__ == "__main__":
    unittest.main()
