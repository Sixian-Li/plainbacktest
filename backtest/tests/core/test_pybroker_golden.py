from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import pybroker
from pybroker import FeeMode, PositionMode, PriceType, Strategy, StrategyConfig

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import pybroker_daily_state
from quantkit.reference import run_reference_threshold
from quantkit.sma_threshold import (
    SmaThresholdSpec,
    analysis_slice,
    prepare_sma_data,
    run_pybroker_threshold,
)


def bars(
    close: list[float],
    *,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
    symbol: str = "AAA",
) -> pd.DataFrame:
    count = len(close)
    open_ = close if open_ is None else open_
    high = [max(o, c) + 10 for o, c in zip(open_, close)] if high is None else high
    low = [min(o, c) - 10 for o, c in zip(open_, close)] if low is None else low
    return pd.DataFrame(
        {
            "symbol": [symbol] * count,
            "date": pd.date_range("2020-01-01", periods=count, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1_000] * count,
        }
    )


class PyBrokerGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pybroker.disable_logging()
        pybroker.disable_progress_bar()

    def run_threshold(
        self,
        frame: pd.DataFrame,
        *,
        policy: ExplicitFillPolicy | None = None,
        a_pct: float = 0,
        b_pct: float = 0,
        initial_cash: float = 100,
    ):
        prepared = prepare_sma_data(frame, window=2)
        ready = analysis_slice(prepared, 2)
        spec = SmaThresholdSpec(a_pct=a_pct, b_pct=b_pct, window=2)
        policy = policy or ExplicitFillPolicy("open", 0)
        result = run_pybroker_threshold(
            ready, spec, policy, initial_cash=initial_cash
        )
        return ready, spec, policy, result

    def test_signal_from_close_executes_at_next_open_not_middle(self) -> None:
        frame = bars(
            [10, 8, 13, 14],
            open_=[10, 11, 12, 20],
            high=[30, 31, 101, 41],
            low=[1, 2, 1, 4],
        )
        _, _, _, result = self.run_threshold(frame)
        first = result.orders.iloc[0]
        self.assertEqual(pd.Timestamp(first["date"]), pd.Timestamp("2020-01-04"))
        self.assertAlmostEqual(float(first["fill_price"]), 20.0)
        self.assertNotAlmostEqual(float(first["fill_price"]), 22.5)

    def test_explicit_close_fill_is_supported_but_not_primary(self) -> None:
        frame = bars(
            [10, 8, 13, 8],
            open_=[10, 11, 20, 21],
            high=[30, 31, 101, 41],
            low=[1, 2, 1, 4],
        )
        _, _, _, result = self.run_threshold(
            frame, policy=ExplicitFillPolicy("close", 0)
        )
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 8.0)

    def test_middle_or_missing_fill_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExplicitFillPolicy("middle", 0)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ExplicitFillPolicy("open", -1)

    def test_no_future_close_is_used_for_same_day_execution(self) -> None:
        frame = bars(
            [10, 8, 20, 21, 22],
            open_=[10, 10, 11, 30, 31],
        )
        _, _, _, result = self.run_threshold(frame)
        self.assertEqual(pd.Timestamp(result.orders.iloc[0]["date"]), pd.Timestamp("2020-01-04"))
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 30.0)

    def test_adverse_cost_is_applied_on_both_sides_without_negative_cash(self) -> None:
        frame = bars(
            [10, 8, 12, 5, 5],
            open_=[10, 10, 10, 10, 10],
        )
        ready, _, _, result = self.run_threshold(
            frame,
            policy=ExplicitFillPolicy("open", 1_000),
            initial_cash=100,
        )
        self.assertEqual(list(result.orders["type"]), ["buy", "sell"])
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 11.0)
        self.assertAlmostEqual(float(result.orders.iloc[1]["fill_price"]), 9.0)
        self.assertAlmostEqual(float(result.portfolio.iloc[-1]["cash"]), 100 / 11 * 9)
        self.assertGreaterEqual(float(result.portfolio["cash"].min()), -1e-10)
        self.assertEqual(len(ready), len(result.portfolio))

    def test_all_affordable_buy_and_final_mark_to_market(self) -> None:
        frame = bars([10, 8, 13, 15], open_=[10, 11, 12, 20])
        _, _, _, result = self.run_threshold(frame, initial_cash=100)
        order = result.orders.iloc[0]
        self.assertAlmostEqual(float(order["shares"]), 5.0)
        self.assertAlmostEqual(float(result.portfolio.iloc[2]["cash"]), 0.0)
        self.assertAlmostEqual(float(result.portfolio.iloc[-1]["equity"]), 75.0)
        self.assertTrue(result.trades.empty)

    def test_reference_ledger_matches_pybroker_daily_and_orders(self) -> None:
        frame = bars(
            [10, 8, 12, 5, 6, 13, 14, 4, 5],
            open_=[10, 11, 10, 9, 8, 12, 11, 10, 9],
        )
        ready, spec, policy, result = self.run_threshold(
            frame, policy=ExplicitFillPolicy("open", 5), initial_cash=1_000
        )
        reference = run_reference_threshold(
            ready, spec, policy, initial_cash=1_000
        )
        actual = pybroker_daily_state(result, ready)
        np.testing.assert_allclose(actual["cash"], reference.daily["cash"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["shares"], reference.daily["shares"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], rtol=0, atol=1e-8)
        np.testing.assert_allclose(
            result.orders["fill_price"].to_numpy(dtype=float),
            reference.orders["fill_price"].to_numpy(dtype=float),
            rtol=0,
            atol=1e-9,
        )

    def test_first_valid_sma_bar_cannot_trigger_an_entry(self) -> None:
        frame = bars([10, 12, 13, 14])
        _, _, _, result = self.run_threshold(frame)
        self.assertTrue(result.orders.empty)

    def test_waits_for_first_cross_after_warmup(self) -> None:
        frame = bars(
            [10, 12, 13, 9, 14, 15],
            open_=[10, 11, 12, 13, 14, 25],
        )
        _, _, _, result = self.run_threshold(frame)
        self.assertEqual(pd.Timestamp(result.orders.iloc[0]["date"]), pd.Timestamp("2020-01-06"))
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 25.0)

    def test_negative_buffers_buy_below_and_sell_above_sma(self) -> None:
        frame = bars(
            [100, 90, 95, 94, 96],
            open_=[100, 90, 95, 94, 96],
        )
        ready, spec, policy, result = self.run_threshold(
            frame,
            a_pct=-3,
            b_pct=-3,
            initial_cash=1_000,
        )
        self.assertAlmostEqual(spec.buy_multiplier, 0.97)
        self.assertAlmostEqual(spec.sell_multiplier, 1.03)
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell"])
        reference = run_reference_threshold(ready, spec, policy, initial_cash=1_000)
        actual = pybroker_daily_state(result, ready)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], atol=1e-8)

    def test_threshold_multipliers_must_remain_positive(self) -> None:
        with self.assertRaises(ValueError):
            SmaThresholdSpec(a_pct=100, b_pct=0)
        with self.assertRaises(ValueError):
            SmaThresholdSpec(a_pct=0, b_pct=-100)

    def test_200_bar_warmup_then_crossing_then_next_open(self) -> None:
        closes = [100.0] * 199 + [110.0, 90.0, 120.0, 121.0]
        opens = [100.0] * 202 + [130.0]
        prepared = prepare_sma_data(bars(closes, open_=opens), window=200)
        ready = analysis_slice(prepared, 200)
        self.assertEqual(len(ready), 4)
        result = run_pybroker_threshold(
            ready,
            SmaThresholdSpec(a_pct=0, b_pct=0, window=200),
            ExplicitFillPolicy("open", 0),
            initial_cash=1_000,
        )
        self.assertEqual(len(result.orders), 1)
        first = result.orders.iloc[0]
        self.assertEqual(pd.Timestamp(first["date"]), pd.Timestamp("2020-07-21"))
        self.assertAlmostEqual(float(first["fill_price"]), 130.0)

    def test_builtin_order_percent_fee_is_calculated(self) -> None:
        frame = bars([10, 10, 10])

        def execute(ctx):
            if ctx.dt == pd.Timestamp("2020-01-01"):
                ctx.buy_shares = 10
                ctx.buy_fill_price = PriceType.OPEN

        config = StrategyConfig(
            initial_cash=1_000,
            fee_mode=FeeMode.ORDER_PERCENT,
            fee_amount=0.05,
            enable_fractional_shares=True,
            round_fill_price=False,
            position_mode=PositionMode.LONG_ONLY,
            buy_delay=1,
            sell_delay=1,
            exit_cover_fill_price=PriceType.OPEN,
            exit_sell_fill_price=PriceType.OPEN,
            round_test_result=False,
        )
        strategy = Strategy(frame, "2020-01-01", "2020-01-03", config)
        strategy.add_execution(execute, "AAA")
        result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
        self.assertAlmostEqual(float(result.orders.iloc[0]["fees"]), 0.05)
        self.assertAlmostEqual(float(result.portfolio.iloc[-1]["cash"]), 899.95)

    def test_two_symbols_share_one_cash_account(self) -> None:
        first = bars([10, 10, 10], symbol="AAA")
        second = bars([20, 20, 20], symbol="BBB")
        frame = pd.concat([first, second], ignore_index=True)

        def execute(ctx):
            if ctx.dt == pd.Timestamp("2020-01-01"):
                ctx.buy_shares = 10**9
                ctx.buy_fill_price = PriceType.OPEN

        config = StrategyConfig(
            initial_cash=100,
            enable_fractional_shares=True,
            round_fill_price=False,
            position_mode=PositionMode.LONG_ONLY,
            max_long_positions=2,
            buy_delay=1,
            sell_delay=1,
            exit_cover_fill_price=PriceType.OPEN,
            exit_sell_fill_price=PriceType.OPEN,
            round_test_result=False,
        )
        strategy = Strategy(frame, "2020-01-01", "2020-01-03", config)
        strategy.add_execution(execute, ["AAA", "BBB"])
        result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
        spent = float((result.orders["shares"] * result.orders["fill_price"]).sum())
        self.assertLessEqual(spent, 100 + 1e-9)
        self.assertAlmostEqual(float(result.portfolio.iloc[-1]["cash"]), 0.0)


if __name__ == "__main__":
    unittest.main()
