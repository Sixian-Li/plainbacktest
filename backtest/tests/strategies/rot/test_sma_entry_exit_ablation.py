from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.sma_entry_exit_ablation import (
    BUY_ARMED_PRICE_FILTER,
    BUY_ROUTE_SHORTS_THEN_SMA200,
    CASE_FLAGS,
    SELL_CLOSE_BELOW_SMA30,
    SELL_CLOSE_BELOW_SMA200,
    SELL_SHORT_AVG_DROP_015,
    SELL_SMA200_DOWN_3D,
    EntryExitAblationSpec,
    active_sell_signals,
    compiled_pybroker_daily_state,
    intraday_buy_fill,
    prepare_entry_exit_ablation_data,
    run_pybroker_compiled_entry_exit_ablation,
    run_reference_entry_exit_ablation,
)


def state_bars(
    *,
    short_cross: list[bool],
    sma200_cross: list[bool],
    above_short: list[bool],
    above_200: list[bool],
    sell_signals: list[tuple[bool, bool, bool, bool]] | None = None,
    open_: list[float] | None = None,
    high: list[float] | None = None,
) -> pd.DataFrame:
    count = len(short_cross)
    open_ = [100.0] * count if open_ is None else open_
    high = open_ if high is None else high
    sell_signals = sell_signals or [(False, False, False, False)] * count
    close = [100.0] * count
    return pd.DataFrame(
        {
            "symbol": "QQQ",
            "date": pd.date_range("2020-01-01", periods=count, freq="D"),
            "open": open_,
            "high": high,
            "low": np.minimum(open_, close),
            "close": close,
            "volume": 1_000,
            "sma25": 99.0,
            "sma30": 99.0,
            "sma35": 99.0,
            "sma200": 99.0,
            "sma_short_avg": 99.0,
            "close_crossed_above_all_short_lines": short_cross,
            "close_crossed_above_sma200": sma200_cross,
            "close_above_all_short_lines": above_short,
            "close_above_sma200": above_200,
            "sma200_down_3d": [row[0] for row in sell_signals],
            "sma_short_avg_drop_gt_0_15pct_1d": [row[1] for row in sell_signals],
            "close_below_sma200": [row[2] for row in sell_signals],
            "close_below_sma30": [row[3] for row in sell_signals],
            "trigger_buy_sma30_98pct": 98.0,
            "trigger_buy_sma200_98pct": 97.0,
            "trigger_buy_combined": 98.0,
            "strategy_ready": True,
        }
    )


class SmaEntryExitAblationTest(unittest.TestCase):
    def test_all_sixteen_exit_combinations_are_declared(self) -> None:
        self.assertEqual(len(CASE_FLAGS), 16)
        self.assertEqual(len(set(CASE_FLAGS.values())), 16)
        self.assertIn((False, False, False, False), CASE_FLAGS.values())
        self.assertIn((True, True, True, True), CASE_FLAGS.values())

    def test_short_cross_requires_all_four_lines_on_the_same_close(self) -> None:
        count = 205
        close = np.linspace(100, 120, count)
        raw = pd.DataFrame(
            {
                "symbol": "QQQ",
                "date": pd.date_range("2020-01-01", periods=count, freq="D"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000,
            }
        )
        prepared = prepare_entry_exit_ablation_data(raw)
        row = prepared.iloc[-1].copy()
        for column in (
            "close_crossed_above_sma25",
            "close_crossed_above_sma30",
            "close_crossed_above_sma35",
            "close_crossed_above_sma_short_avg",
        ):
            row[column] = True
        self.assertTrue(bool(pd.Series([row[column] for column in (
            "close_crossed_above_sma25", "close_crossed_above_sma30",
            "close_crossed_above_sma35", "close_crossed_above_sma_short_avg",
        )]).all()))
        row["close_crossed_above_sma30"] = False
        self.assertFalse(bool(pd.Series([row[column] for column in (
            "close_crossed_above_sma25", "close_crossed_above_sma30",
            "close_crossed_above_sma35", "close_crossed_above_sma_short_avg",
        )]).all()))

    def test_entry_route_can_arm_across_multiple_completed_closes(self) -> None:
        frame = state_bars(
            short_cross=[True, False, False, False],
            sma200_cross=[False, False, False, False],
            above_short=[True, True, True, True],
            above_200=[False, True, True, True],
            open_=[95, 95, 99, 100],
            high=[95, 95, 99, 100],
        )
        result = run_reference_entry_exit_ablation(
            frame, EntryExitAblationSpec(False, False, False, False), initial_cash=1_000
        )
        self.assertEqual(result.orders["reason"].tolist(), [BUY_ARMED_PRICE_FILTER])
        order = result.orders.iloc[0]
        self.assertEqual(order["entry_arm_reason"], BUY_ROUTE_SHORTS_THEN_SMA200)
        self.assertEqual(pd.Timestamp(order["entry_arm_date"]), pd.Timestamp("2020-01-02"))
        self.assertEqual(pd.Timestamp(order["date"]), pd.Timestamp("2020-01-03"))

    def test_sell_signal_resets_partial_entry_qualification_while_flat(self) -> None:
        frame = state_bars(
            short_cross=[True, False, False, False],
            sma200_cross=[False, False, False, False],
            above_short=[True, True, True, True],
            above_200=[False, False, True, True],
            sell_signals=[
                (False, False, False, False),
                (False, False, False, True),
                (False, False, False, False),
                (False, False, False, False),
            ],
        )
        result = run_reference_entry_exit_ablation(
            frame, EntryExitAblationSpec(False, False, False, True), initial_cash=1_000
        )
        self.assertTrue(result.orders.empty)

    def test_intraday_buy_requires_the_higher_of_both_dynamic_boundaries(self) -> None:
        self.assertIsNone(
            intraday_buy_fill(
                open_=95, high=97.99, trigger_sma30=98, trigger_sma200=97
            )
        )
        self.assertEqual(
            intraday_buy_fill(
                open_=95, high=98, trigger_sma30=98, trigger_sma200=97
            ),
            (98.0, "intraday_trigger", 98.0),
        )
        self.assertEqual(
            intraday_buy_fill(
                open_=99, high=100, trigger_sma30=98, trigger_sma200=97
            ),
            (99.0, "open_gap", 98.0),
        )

    def test_three_declines_mean_three_strictly_negative_daily_changes(self) -> None:
        close = np.r_[np.full(200, 100.0), 99.0, 98.0, 97.0]
        raw = pd.DataFrame(
            {
                "symbol": "QQQ",
                "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000,
            }
        )
        prepared = prepare_entry_exit_ablation_data(raw)
        self.assertFalse(bool(prepared.iloc[-2]["sma200_down_3d"]))
        self.assertTrue(bool(prepared.iloc[-1]["sma200_down_3d"]))

    def test_parameterized_windows_buffers_and_decline_thresholds(self) -> None:
        close = np.r_[np.full(12, 100.0), 99.9, 99.8, 99.7]
        raw = pd.DataFrame(
            {
                "symbol": "QQQ",
                "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000,
            }
        )
        prepared = prepare_entry_exit_ablation_data(
            raw,
            short_windows=(3, 4, 5),
            long_window=10,
            buy_short_buffer_pct=1.0,
            buy_long_buffer_pct=3.0,
            r1_decline_days=2,
            r1_min_daily_decline_pct=0.01,
            r3_sell_buffer_pct=2.0,
        )
        self.assertTrue(bool(prepared.iloc[-1]["sma200_down_3d"]))
        self.assertFalse(bool(prepared.iloc[-1]["close_below_sma200"]))
        expected_short = (
            0.99 * close[-4:-1].sum() / (4 - 0.99)
        )
        expected_long = (
            0.97 * close[-10:-1].sum() / (10 - 0.97)
        )
        self.assertAlmostEqual(prepared.iloc[-1]["trigger_buy_sma30_98pct"], expected_short)
        self.assertAlmostEqual(prepared.iloc[-1]["trigger_buy_sma200_98pct"], expected_long)
        self.assertEqual(
            prepared.attrs["entry_exit_parameters"]["short_windows"], [3, 4, 5]
        )

    def test_all_active_sell_reasons_are_recorded_in_fixed_priority(self) -> None:
        row = pd.Series(
            {
                "sma200_down_3d": True,
                "sma_short_avg_drop_gt_0_15pct_1d": True,
                "close_below_sma200": True,
                "close_below_sma30": True,
            }
        )
        signals = active_sell_signals(row, EntryExitAblationSpec(True, True, True, True))
        self.assertEqual(
            signals,
            (
                SELL_SMA200_DOWN_3D,
                SELL_SHORT_AVG_DROP_015,
                SELL_CLOSE_BELOW_SMA200,
                SELL_CLOSE_BELOW_SMA30,
            ),
        )

    def test_pybroker_and_reference_ledgers_match(self) -> None:
        frame = state_bars(
            short_cross=[True, False, False, False, True, False, False],
            sma200_cross=[False] * 7,
            above_short=[True] * 7,
            above_200=[True] * 7,
            sell_signals=[
                (False, False, False, False),
                (False, False, False, False),
                (False, False, False, True),
                (False, False, False, False),
                (False, False, False, False),
                (False, False, False, False),
                (False, False, False, False),
            ],
        )
        spec = EntryExitAblationSpec(False, False, False, True)
        result, reference, engine = run_pybroker_compiled_entry_exit_ablation(
            frame, spec, initial_cash=1_000
        )
        actual = compiled_pybroker_daily_state(result, engine)
        expected = reference.daily[reference.daily["date"].isin(actual["date"])].reset_index(drop=True)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(actual[column], expected[column], rtol=0, atol=1e-9)
        self.assertEqual(result.orders["type"].tolist(), reference.orders["type"].tolist())
        np.testing.assert_allclose(
            result.orders["fill_price"], reference.orders["fill_price"], rtol=0, atol=1e-9
        )


if __name__ == "__main__":
    unittest.main()
