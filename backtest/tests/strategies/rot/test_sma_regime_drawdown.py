from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import pybroker

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import pybroker_daily_state
from quantkit.sma_regime import (
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_SMA_ORDERING,
    SmaRegimeSpec,
)
from quantkit.sma_regime_drawdown import (
    BASE_CONDITIONS,
    STOP_EXIT_REASON,
    PeakDrawdownSpec,
    prepare_peak_drawdown_data,
    run_pybroker_peak_drawdown,
    run_reference_peak_drawdown,
)
from scripts.run_sma_regime_drawdown_oos import (
    select_training_threshold,
    window_slice,
)


def bars(close: list[float], *, open_: list[float] | None = None) -> pd.DataFrame:
    open_ = close if open_ is None else open_
    return pd.DataFrame(
        {
            "symbol": "QQQ",
            "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
            "open": open_,
            "high": np.maximum(open_, close),
            "low": np.minimum(open_, close),
            "close": close,
            "volume": 1_000,
            "regime_ready": True,
            CONDITION_PRICE_ABOVE_SMA200: True,
            CONDITION_SMA_ORDERING: True,
        }
    )


class PeakDrawdownOverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pybroker.disable_logging()
        pybroker.disable_progress_bar()

    def test_base_strategy_really_uses_only_conditions_one_and_three(self) -> None:
        self.assertEqual(
            BASE_CONDITIONS,
            (CONDITION_PRICE_ABOVE_SMA200, CONDITION_SMA_ORDERING),
        )

    def test_disabled_condition_two_does_not_delay_readiness(self) -> None:
        raw = bars(list(range(1, 12)))
        prepared = prepare_peak_drawdown_data(
            raw.drop(columns=[
                "regime_ready",
                CONDITION_PRICE_ABOVE_SMA200,
                CONDITION_SMA_ORDERING,
            ]),
            SmaRegimeSpec(
                sma_windows=(2, 3, 4, 5),
                price_sma_window=3,
                ordering_windows=(3, 4, 5),
                rising_sessions=3,
            ),
        )
        # SMA5 becomes available on the fifth bar. The removed all-SMAs-rising
        # condition must not impose another three sessions of warmup.
        ready_dates = prepared.loc[prepared["regime_ready"], "date"]
        self.assertEqual(ready_dates.iloc[0], pd.Timestamp("2020-01-05"))

    def test_stop_is_strictly_greater_than_threshold_and_fills_next_open(self) -> None:
        frame = bars(
            [100, 100, 120, 110.4, 109, 111],
            open_=[100, 101, 121, 111, 108, 112],
        )
        reference = run_reference_peak_drawdown(
            frame,
            PeakDrawdownSpec(8),
            ExplicitFillPolicy("open", 0),
            initial_cash=1_000,
        )
        # 110.4 is exactly 8% below the 120 peak and must not stop. The next
        # 109 close is a 9.1667% drawdown and therefore sells one session later.
        stop_orders = reference.orders[reference.orders["reason"] == STOP_EXIT_REASON]
        self.assertEqual(len(stop_orders), 1)
        stop = stop_orders.iloc[0]
        self.assertEqual(pd.Timestamp(stop["signal_date"]), pd.Timestamp("2020-01-05"))
        self.assertEqual(pd.Timestamp(stop["date"]), pd.Timestamp("2020-01-06"))
        self.assertAlmostEqual(float(stop["fill_price"]), 112.0)

    def test_peak_resets_after_stop_and_base_eligible_state_can_reenter(self) -> None:
        frame = bars(
            [100, 100, 120, 109, 108, 100, 105, 96, 95],
            open_=[100, 101, 120, 110, 107, 99, 104, 97, 94],
        )
        reference = run_reference_peak_drawdown(
            frame,
            PeakDrawdownSpec(8),
            ExplicitFillPolicy("open", 0),
            initial_cash=1_000,
        )
        # Buy, stop-sell, re-buy because both base conditions remain true,
        # then a second stop based on the new position's own peak.
        self.assertEqual(
            reference.orders["type"].tolist(),
            ["buy", "sell", "buy", "sell"],
        )
        self.assertEqual(
            reference.orders["reason"].tolist(),
            [
                "BOTH_BASE_CONDITIONS_TRUE",
                STOP_EXIT_REASON,
                "BOTH_BASE_CONDITIONS_TRUE",
                STOP_EXIT_REASON,
            ],
        )
        second_stop = reference.orders[reference.orders["reason"] == STOP_EXIT_REASON].iloc[1]
        self.assertAlmostEqual(float(second_stop["signal_peak_close_or_entry"]), 105.0)
        self.assertAlmostEqual(float(second_stop["signal_drawdown_pct"]), (105 - 96) / 105 * 100)

    def test_pybroker_matches_independent_reference_with_stop_and_reentry(self) -> None:
        frame = bars(
            [100, 100, 120, 109, 108, 100, 105, 96, 95, 110],
            open_=[100, 101, 120, 110, 107, 99, 104, 97, 94, 111],
        )
        spec = PeakDrawdownSpec(8)
        policy = ExplicitFillPolicy("open", 0)
        result = run_pybroker_peak_drawdown(frame, spec, policy, initial_cash=1_000)
        reference = run_reference_peak_drawdown(frame, spec, policy, initial_cash=1_000)
        actual = pybroker_daily_state(result, frame)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(
                actual[column].to_numpy(float),
                reference.daily[column].to_numpy(float),
                rtol=0,
                atol=1e-9,
            )
        self.assertEqual(result.orders["type"].tolist(), reference.orders["type"].tolist())
        self.assertEqual(
            pd.to_datetime(result.orders["date"]).tolist(),
            pd.to_datetime(reference.orders["date"]).tolist(),
        )
        np.testing.assert_allclose(
            result.orders["fill_price"].to_numpy(float),
            reference.orders["fill_price"].to_numpy(float),
            rtol=0,
            atol=1e-9,
        )

    def test_none_disables_drawdown_overlay(self) -> None:
        frame = bars([100, 100, 120, 50, 40])
        reference = run_reference_peak_drawdown(
            frame,
            PeakDrawdownSpec(None),
            ExplicitFillPolicy("open", 0),
            initial_cash=1_000,
        )
        self.assertEqual(reference.orders["type"].tolist(), ["buy"])

    def test_training_selection_uses_drawdown_then_declared_tie_breakers(self) -> None:
        results = pd.DataFrame(
            [
                {"stop_pct": 7, "max_drawdown_pct": -10, "total_return_pct": 20, "order_count": 8},
                {"stop_pct": 8, "max_drawdown_pct": -9, "total_return_pct": 15, "order_count": 10},
                {"stop_pct": 9, "max_drawdown_pct": -9, "total_return_pct": 15, "order_count": 9},
                {"stop_pct": 10, "max_drawdown_pct": -9, "total_return_pct": 15, "order_count": 9},
            ]
        )
        # Thresholds 9 and 10 tie on drawdown, return, and orders. Nine is
        # closer to the predeclared 8% anchor and therefore wins.
        selected = select_training_threshold(results, anchor_pct=8)
        self.assertEqual(float(selected["stop_pct"]), 9.0)

    def test_train_and_test_slices_are_non_overlapping_and_ready_only(self) -> None:
        frame = bars([100, 101, 102, 103, 104])
        frame.loc[0, "regime_ready"] = False
        train = window_slice(
            frame,
            requested_start=pd.Timestamp("2020-01-01"),
            end=pd.Timestamp("2020-01-03"),
        )
        test = window_slice(
            frame,
            requested_start=pd.Timestamp("2020-01-04"),
            end=pd.Timestamp("2020-01-05"),
        )
        self.assertEqual(train["date"].dt.strftime("%Y-%m-%d").tolist(), ["2020-01-02", "2020-01-03"])
        self.assertEqual(test["date"].dt.strftime("%Y-%m-%d").tolist(), ["2020-01-04", "2020-01-05"])
        self.assertLess(train["date"].max(), test["date"].min())


if __name__ == "__main__":
    unittest.main()
