from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.trend_score_portfolio import (
    TrendScoreSpec,
    _capped_inverse_volatility,
    cross_check_portfolios,
    hysteresis_state,
    last_session_of_week,
    prepare_asset_indicators,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.run_trend_score_portfolio import truthy


def price_frame(symbol: str, closes: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes))
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000.0,
        }
    )


class TrendScoreIndicatorTest(unittest.TestCase):
    def test_vendor_boolean_strings_are_not_python_truthiness(self) -> None:
        self.assertTrue(truthy("True"))
        self.assertFalse(truthy("False"))
        self.assertFalse(truthy("0"))

    def test_atr_band_retains_the_previous_state(self) -> None:
        close = pd.Series([112.0, 105.0, 88.0, 95.0])
        sma = pd.Series([100.0] * 4)
        atr = pd.Series([10.0] * 4)
        observed = hysteresis_state(close, sma, atr, band_multiple=1.0)
        self.assertEqual(observed.tolist(), [100.0, 100.0, 0.0, 0.0])

    def test_future_price_changes_cannot_rewrite_prior_scores(self) -> None:
        values = 100.0 + np.arange(360) * 0.1 + np.sin(np.arange(360) / 7.0)
        original = price_frame("AAA", values.tolist())
        changed = original.copy()
        changed.loc[350:, ["open", "high", "low", "close"]] *= 10.0
        spec = TrendScoreSpec()
        first = prepare_asset_indicators(original, "AAA", spec)
        second = prepare_asset_indicators(changed, "AAA", spec)
        columns = ["sma200", "atr20", "hysteresis_score", "own_score", "realized_volatility"]
        pd.testing.assert_frame_equal(first.loc[:349, columns], second.loc[:349, columns])

    def test_inverse_volatility_weights_respect_cap_and_budget(self) -> None:
        volatility = pd.Series({f"S{i:02}": 0.1 + i * 0.01 for i in range(20)})
        weights = _capped_inverse_volatility(volatility, cap=0.10)
        self.assertLessEqual(float(weights.max()), 0.10 + 1e-12)
        self.assertAlmostEqual(float(weights.sum()), 1.0)

    def test_partial_final_week_is_not_mistaken_for_week_end(self) -> None:
        dates = pd.to_datetime(
            ["2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"]
        )
        observed = last_session_of_week(dates)
        self.assertEqual(observed.tolist(), [pd.Timestamp("2026-07-31")])


class TrendScorePortfolioGoldenTest(unittest.TestCase):
    def test_next_open_multi_asset_cost_ledger_matches_pybroker(self) -> None:
        dates = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
        first = pd.DataFrame(
            {
                "date": dates,
                "symbol": "AAA",
                "open": [10.0, 11.0, 12.0, 13.0, 14.0],
                "high": [11.0, 12.0, 13.0, 14.0, 15.0],
                "low": [9.0, 10.0, 11.0, 12.0, 13.0],
                "close": [10.5, 11.5, 12.5, 13.5, 14.5],
                "volume": 1_000.0,
            }
        )
        second = first.copy()
        second["symbol"] = "BBB"
        second[["open", "high", "low", "close"]] *= 2.0
        panel = pd.concat([first, second], ignore_index=True)
        targets = pd.DataFrame(
            [
                {"signal_date": dates[0], "execution_date": dates[1], "symbol": "AAA", "target_shares": 5.0},
                {"signal_date": dates[0], "execution_date": dates[1], "symbol": "BBB", "target_shares": 2.0},
                {"signal_date": dates[2], "execution_date": dates[3], "symbol": "AAA", "target_shares": 2.0},
                {"signal_date": dates[2], "execution_date": dates[3], "symbol": "BBB", "target_shares": 3.0},
            ]
        )
        policy = ExplicitFillPolicy("open", 5.0)
        result, positions = run_pybroker_portfolio(
            panel, targets, initial_cash=1_000.0, policy=policy
        )
        reference = run_reference_portfolio(
            panel, targets, initial_cash=1_000.0, policy=policy
        )
        differences = cross_check_portfolios(
            result,
            positions,
            reference,
            numerical_zero_notional=1e-9,
        )
        self.assertLessEqual(max(differences.values()), 1e-9)

        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            cross_check_portfolios(
                result,
                positions,
                reference,
                numerical_zero_notional=-1.0,
            )
        orders = result.orders.reset_index()
        self.assertEqual(pd.Timestamp(orders.iloc[0]["date"]), dates[1])
        buy = orders[orders["type"] == "buy"].iloc[0]
        raw_open = float(panel[(panel["date"] == buy["date"]) & (panel["symbol"] == buy["symbol"])]["open"].iloc[0])
        self.assertAlmostEqual(float(buy["fill_price"]), raw_open * 1.0005)
        daily = pybroker_daily_state(result)
        invested = (daily["equity"] - daily["cash"]) / daily["equity"]
        np.testing.assert_allclose(daily["gross_exposure"], invested, rtol=0, atol=1e-12)

    def test_sub_penny_numerical_fill_does_not_break_economic_order_check(self) -> None:
        dates = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
        panel = pd.DataFrame(
            {
                "date": dates,
                "symbol": "AAA",
                "open": [10.0, 10.0, 10.0],
                "high": [11.0, 11.0, 11.0],
                "low": [9.0, 9.0, 9.0],
                "close": [10.0, 10.0, 10.0],
                "volume": 1_000.0,
            }
        )
        targets = pd.DataFrame(
            [
                {
                    "signal_date": dates[0],
                    "execution_date": dates[1],
                    "symbol": "AAA",
                    "target_shares": 10.0 + 1e-25,
                }
            ]
        )
        policy = ExplicitFillPolicy("open", 0.0)
        result, positions = run_pybroker_portfolio(
            panel, targets, initial_cash=100.0, policy=policy
        )
        reference = run_reference_portfolio(
            panel, targets, initial_cash=100.0, policy=policy
        )
        differences = cross_check_portfolios(result, positions, reference)
        self.assertLessEqual(max(differences.values()), 1e-9)


if __name__ == "__main__":
    unittest.main()
