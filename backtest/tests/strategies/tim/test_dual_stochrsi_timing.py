import unittest

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import (
    OrderPlan,
    TimingSpec,
    evaluate_order_plan,
    prepare_dual_stochrsi_data,
    prepare_sma200_data,
    run_pybroker,
    run_reference,
    solve_close_for_rsi,
    wilder_components,
)
from quantkit.metrics import pybroker_daily_state
from quantkit.stochrsi import wilder_rsi
from scripts.run_intraday_sma_backtest import cross_check


def sample(count: int = 420) -> pd.DataFrame:
    index = np.arange(count, dtype=float)
    close = 100 + 0.08 * index + 12 * np.sin(index / 13) + 4 * np.sin(index / 3.7)
    open_ = np.r_[close[0], close[:-1]] + 1.4 * np.sin(index / 5.3)
    return pd.DataFrame(
        {
            "symbol": "QQQ",
            "date": pd.bdate_range("2019-01-02", periods=count),
            "open": open_,
            "high": np.maximum(open_, close) + 5,
            "low": np.minimum(open_, close) - 5,
            "close": close,
            "volume": 1_000_000,
        }
    )


class IndicatorAndTriggerTest(unittest.TestCase):
    def test_single_period_cross_is_supported_without_duplicate_confirmation(self):
        spec = TimingSpec("CROSS", periods=(42,), buy_threshold=0.2, sell_threshold=0.8)
        self.assertEqual(spec.periods, (42,))

    def test_cross_grid_preserves_endpoint_thresholds_as_no_signal_edges(self) -> None:
        data = sample()
        zero_buy = prepare_dual_stochrsi_data(
            data, TimingSpec("CROSS", buy_threshold=0.0, sell_threshold=0.8)
        )
        one_sell = prepare_dual_stochrsi_data(
            data, TimingSpec("CROSS", buy_threshold=0.2, sell_threshold=1.0)
        )
        self.assertFalse(zero_buy["buy_eligible"].any())
        self.assertFalse(one_sell["sell_eligible"].any())

    def test_wilder_components_match_shared_indicator(self) -> None:
        data = sample(180)
        for period in (42, 100):
            actual = wilder_components(data["close"], period)["rsi"]
            expected = wilder_rsi(data["close"], period)
            np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12, equal_nan=True)

    def test_solved_price_reaches_requested_wilder_rsi(self) -> None:
        data = sample(180)
        period = 42
        components = wilder_components(data["close"], period)
        index = 140
        target = 63.25
        solved = solve_close_for_rsi(
            float(data.loc[index - 1, "close"]),
            float(components.loc[index - 1, "average_gain"]),
            float(components.loc[index - 1, "average_loss"]),
            period=period,
            target_rsi=target,
        )
        changed = data.loc[:index, "close"].copy()
        changed.loc[index] = solved
        self.assertAlmostEqual(float(wilder_rsi(changed, period).iloc[-1]), target, places=10)

    def test_current_bar_cannot_change_predeclared_trigger(self) -> None:
        data = sample()
        spec = TimingSpec("LEVEL")
        original = prepare_dual_stochrsi_data(data, spec)
        target = 330
        changed = data.copy()
        changed.loc[target, ["open", "high", "low", "close"]] = [180, 220, 175, 215]
        rebuilt = prepare_dual_stochrsi_data(changed, spec)
        for column in ("buy_trigger", "sell_trigger"):
            self.assertAlmostEqual(original.loc[target, column], rebuilt.loc[target, column], places=12)

    def test_joint_threshold_uses_last_period_to_confirm(self) -> None:
        data = sample()
        level = prepare_dual_stochrsi_data(data, TimingSpec("LEVEL"))
        valid = level.dropna(subset=["buy_trigger_42", "buy_trigger_100"]).iloc[-1]
        self.assertEqual(valid["buy_trigger"], min(valid["buy_trigger_42"], valid["buy_trigger_100"]))
        self.assertEqual(valid["sell_trigger"], max(valid["sell_trigger_42"], valid["sell_trigger_100"]))
        cross = prepare_dual_stochrsi_data(data, TimingSpec("CROSS"))
        valid = cross.dropna(subset=["buy_trigger_42", "buy_trigger_100"]).iloc[-1]
        self.assertEqual(valid["buy_trigger"], max(valid["buy_trigger_42"], valid["buy_trigger_100"]))
        self.assertEqual(valid["sell_trigger"], min(valid["sell_trigger_42"], valid["sell_trigger_100"]))


class ExecutionTest(unittest.TestCase):
    def plan(self, side: str, direction: str, trigger: float = 100, cost: float = 0) -> OrderPlan:
        return OrderPlan(
            date=pd.Timestamp("2026-01-02"), side=side, direction=direction,
            trigger=trigger, signal="TEST", eligible=True, cost_bps=cost,
        )

    def test_only_directed_open_to_close_path_can_touch(self) -> None:
        self.assertIsNone(evaluate_order_plan(self.plan("buy", "up"), open_=95, close=97))
        event = evaluate_order_plan(self.plan("buy", "up", cost=5), open_=95, close=102)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.fill_source, "open_close_trigger")
        self.assertAlmostEqual(event.fill_price, 100.05)
        self.assertIsNone(evaluate_order_plan(self.plan("sell", "down"), open_=105, close=103))

    def test_gap_uses_open(self) -> None:
        buy = evaluate_order_plan(self.plan("buy", "up"), open_=103, close=101)
        sell = evaluate_order_plan(self.plan("sell", "down"), open_=97, close=99)
        self.assertEqual(buy.raw_fill_price, 103)
        self.assertEqual(sell.raw_fill_price, 97)
        self.assertEqual(buy.fill_source, "open_gap")


class LedgerTest(unittest.TestCase):
    def assert_reconciles(self, prepared: pd.DataFrame, spec: TimingSpec) -> None:
        start = prepared.loc[260, "date"]
        end = prepared.iloc[-1]["date"]
        reference = run_reference(prepared, spec, analysis_start=start, analysis_end=end)
        broker = run_pybroker(prepared, spec, analysis_start=start, analysis_end=end)
        engine = prepared[
            (prepared["date"] >= broker.engine_start) & (prepared["date"] <= end)
        ]
        actual = pybroker_daily_state(broker.pybroker_result, engine)
        actual = actual[actual["date"] >= start].reset_index(drop=True)
        differences = cross_check(broker.pybroker_result, actual, reference, tolerance=1e-6)
        self.assertLessEqual(max(differences.values(), default=0), 1e-6)

    def test_all_three_stochrsi_modes_reconcile(self) -> None:
        data = sample()
        for mode in ("LEVEL", "EXTREME", "CROSS"):
            spec = TimingSpec(mode, cost_bps=5)
            with self.subTest(mode=mode):
                self.assert_reconciles(prepare_dual_stochrsi_data(data, spec), spec)

    def test_sma200_baseline_reconciles_under_same_path_model(self) -> None:
        spec = TimingSpec("SMA200", cost_bps=5)
        self.assert_reconciles(prepare_sma200_data(sample(), spec), spec)


if __name__ == "__main__":
    unittest.main()
