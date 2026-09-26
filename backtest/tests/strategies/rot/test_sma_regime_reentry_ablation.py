from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.sma_regime import CONDITION_PRICE_ABOVE_SMA200, CONDITION_SMA_ORDERING
from quantkit.sma_regime_reentry_ablation import (
    CONDITION_SHORT_SMAS_RISING_1D,
    EXIT_INTRADAY_DRAWDOWN,
    REENTRY_RECOVER_SELL_PRICE,
    REENTRY_SMA200_RESET,
    ReentryAblationSpec,
    advance_reset_stage,
    compiled_pybroker_daily_state,
    intraday_stop_fill,
    main_conditions,
    prepare_reentry_ablation_data,
    run_pybroker_compiled_reentry_ablation,
    run_reference_reentry_ablation,
)


def state_bars(
    close: list[float],
    *,
    open_: list[float] | None = None,
    low: list[float] | None = None,
    eligible: list[bool] | None = None,
    sma200: list[float] | None = None,
) -> pd.DataFrame:
    open_ = close if open_ is None else open_
    low = np.minimum(open_, close).tolist() if low is None else low
    eligible = [True] * len(close) if eligible is None else eligible
    sma200 = [100.0] * len(close) if sma200 is None else sma200
    return pd.DataFrame(
        {
            "symbol": "QQQ",
            "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
            "open": open_,
            "high": np.maximum(open_, close),
            "low": low,
            "close": close,
            "volume": 1_000,
            "sma25": close,
            "sma30": close,
            "sma35": close,
            "sma200": sma200,
            "sma250": np.asarray(sma200) - 1,
            "sma300": np.asarray(sma200) - 2,
            CONDITION_PRICE_ABOVE_SMA200: eligible,
            CONDITION_SMA_ORDERING: eligible,
            CONDITION_SHORT_SMAS_RISING_1D: eligible,
            "regime_ready": True,
        }
    )


class SmaRegimeReentryAblationTest(unittest.TestCase):
    def test_measure_one_requires_each_short_sma_to_rise_strictly(self) -> None:
        raw = pd.DataFrame(
            {
                "symbol": "QQQ",
                "date": pd.date_range("2020-01-01", periods=310, freq="D"),
                "open": np.arange(1, 311, dtype=float),
                "high": np.arange(1, 311, dtype=float),
                "low": np.arange(1, 311, dtype=float),
                "close": np.arange(1, 311, dtype=float),
                "volume": 1_000,
            }
        )
        prepared = prepare_reentry_ablation_data(raw)
        last = prepared.iloc[-1].copy()
        self.assertTrue(bool(last[CONDITION_SHORT_SMAS_RISING_1D]))
        spec = ReentryAblationSpec(measure_1=True, measure_2=False)
        self.assertTrue(main_conditions(last, spec))
        last[CONDITION_SHORT_SMAS_RISING_1D] = False
        self.assertFalse(main_conditions(last, spec))

    def test_intraday_stop_gaps_at_open_otherwise_fills_exact_threshold(self) -> None:
        self.assertEqual(
            intraday_stop_fill(open_=95, low=94, known_peak=100, stop_pct=3),
            (95.0, "open_gap"),
        )
        self.assertEqual(
            intraday_stop_fill(open_=99, low=96, known_peak=100, stop_pct=3),
            (97.0, "intraday_trigger"),
        )
        self.assertIsNone(intraday_stop_fill(open_=99, low=97.01, known_peak=100, stop_pct=3))

    def test_reset_path_must_happen_in_order(self) -> None:
        stage = advance_reset_stage(
            0, previous_close=101, previous_sma200=100, close=99, sma200=100, deep_pct=5
        )
        self.assertEqual(stage, 1)
        # An upward cross before the deep 5% touch does not unlock.
        stage = advance_reset_stage(
            stage, previous_close=99, previous_sma200=100, close=101, sma200=100, deep_pct=5
        )
        self.assertEqual(stage, 1)
        stage = advance_reset_stage(
            stage, previous_close=101, previous_sma200=100, close=95, sma200=100, deep_pct=5
        )
        self.assertEqual(stage, 2)
        stage = advance_reset_stage(
            stage, previous_close=95, previous_sma200=100, close=101, sma200=100, deep_pct=5
        )
        self.assertEqual(stage, 3)

    def test_stop_lock_reenters_at_sell_price_when_main_conditions_hold(self) -> None:
        frame = state_bars(
            [110, 110, 108, 106, 107, 108],
            open_=[110, 110, 109, 106, 107, 108],
            low=[110, 110, 106, 105, 106, 107],
            sma200=[100] * 6,
        )
        result = run_reference_reentry_ablation(
            frame, ReentryAblationSpec(False, True), initial_cash=1_000
        )
        self.assertEqual(
            result.orders["reason"].tolist(),
            ["ENTRY_MAIN_CONDITIONS", EXIT_INTRADAY_DRAWDOWN, REENTRY_RECOVER_SELL_PRICE],
        )
        stop = result.orders.iloc[1]
        self.assertAlmostEqual(float(stop["fill_price"]), 110 * 0.97)

    def test_stop_lock_can_reenter_only_after_full_sma200_reset_sequence(self) -> None:
        frame = state_bars(
            [110, 110, 106, 99, 95, 96, 101, 102],
            open_=[110, 110, 107, 99, 95, 96, 101, 102],
            low=[110, 110, 106, 98, 94, 95, 100, 101],
            eligible=[True, True, True, False, False, False, True, True],
            sma200=[100] * 8,
        )
        result = run_reference_reentry_ablation(
            frame, ReentryAblationSpec(False, True), initial_cash=1_000
        )
        self.assertEqual(result.orders.iloc[-1]["reason"], REENTRY_SMA200_RESET)
        self.assertEqual(pd.Timestamp(result.orders.iloc[-1]["date"]), pd.Timestamp("2020-01-08"))

    def test_compiled_pybroker_and_independent_ledger_match(self) -> None:
        frame = state_bars(
            [110, 110, 106, 99, 95, 96, 101, 102, 105, 100, 102],
            open_=[110, 110, 107, 99, 95, 96, 101, 102, 105, 100, 102],
            low=[110, 110, 106, 98, 94, 95, 100, 101, 104, 99, 101],
            eligible=[True, True, True, False, False, False, True, True, True, True, True],
            sma200=[100] * 11,
        )
        spec = ReentryAblationSpec(False, True)
        pybroker_result, reference, engine = run_pybroker_compiled_reentry_ablation(
            frame, spec, initial_cash=1_000
        )
        actual = compiled_pybroker_daily_state(pybroker_result, engine, frame)
        expected = reference.daily[reference.daily["date"].isin(actual["date"])].reset_index(drop=True)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(actual[column], expected[column], rtol=0, atol=1e-9)
        self.assertEqual(pybroker_result.orders["type"].tolist(), reference.orders["type"].tolist())
        np.testing.assert_allclose(
            pybroker_result.orders["fill_price"], reference.orders["fill_price"], rtol=0, atol=1e-9
        )

    def test_same_day_buy_then_stop_is_reconciled_in_order(self) -> None:
        frame = state_bars(
            [110, 110, 106, 99, 95, 96, 101, 102, 105, 104, 105],
            open_=[110, 110, 107, 99, 95, 96, 101, 102, 105, 104, 105],
            low=[110, 110, 106, 98, 94, 95, 100, 101, 104, 100, 104],
            eligible=[True, True, True, False, False, False, True, True, True, True, True],
            sma200=[100] * 11,
        )
        spec = ReentryAblationSpec(False, True)
        pybroker_result, reference, engine = run_pybroker_compiled_reentry_ablation(
            frame, spec, initial_cash=1_000
        )
        actual = compiled_pybroker_daily_state(pybroker_result, engine, frame)
        expected = reference.daily[reference.daily["date"].isin(actual["date"])].reset_index(drop=True)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(actual[column], expected[column], rtol=0, atol=1e-9)


if __name__ == "__main__":
    unittest.main()
