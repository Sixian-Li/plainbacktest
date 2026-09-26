from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import pybroker_daily_state
from quantkit.reference import run_reference_stochrsi
from quantkit.stochrsi import (
    STOCHRSI_COLUMN,
    StochRsiSpec,
    prepare_stochrsi_data,
    run_pybroker_stochrsi,
    wilder_rsi,
)


class StochRsiTest(unittest.TestCase):
    def test_wilder_initial_average_and_recursion(self) -> None:
        close = pd.Series([1.0, 2.0, 3.0, 2.0, 4.0])
        actual = wilder_rsi(close, 3)
        self.assertTrue(actual.iloc[:3].isna().all())
        self.assertAlmostEqual(actual.iloc[3], 66.6666666667, places=9)
        self.assertAlmostEqual(actual.iloc[4], 83.3333333333, places=9)

    def test_stochrsi_warmup_and_flat_range_are_explicit(self) -> None:
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=10, freq="D"),
                "symbol": "TEST",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 100.0,
            }
        )
        actual = prepare_stochrsi_data(frame, 3)
        self.assertTrue(actual[STOCHRSI_COLUMN].iloc[:5].isna().all())
        self.assertEqual(actual[STOCHRSI_COLUMN].iloc[5:].tolist(), [0.5] * 5)

    def test_inclusive_levels_signal_at_close_and_fill_next_open(self) -> None:
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-02", periods=5, freq="D"),
                "symbol": "TEST",
                "open": [10.0, 11.0, 12.0, 13.0, 14.0],
                "high": [10.5, 11.5, 12.5, 13.5, 14.5],
                "low": [9.5, 10.5, 11.5, 12.5, 13.5],
                "close": [10.0, 11.0, 12.0, 13.0, 14.0],
                "volume": 100.0,
                "rsi": [50.0] * 5,
                "stochrsi": [0.5, 0.2, 0.8, 0.9, 0.1],
            }
        )
        spec = StochRsiSpec(period=10, buy_threshold=0.2, sell_threshold=0.8)
        policy = ExplicitFillPolicy("open", 5.0)
        expected = run_reference_stochrsi(frame, spec, policy)
        actual_result = run_pybroker_stochrsi(frame, spec, policy)
        actual = pybroker_daily_state(actual_result, frame)

        self.assertEqual(expected.orders["signal_date"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-03", "2024-01-04"])
        self.assertEqual(expected.orders["date"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-04", "2024-01-05"])
        np.testing.assert_allclose(actual["cash"], expected.daily["cash"], atol=1e-8)
        np.testing.assert_allclose(actual["shares"], expected.daily["shares"], atol=1e-8)
        np.testing.assert_allclose(actual["equity"], expected.daily["equity"], atol=1e-8)

    def test_thresholds_must_form_hysteresis(self) -> None:
        with self.assertRaises(ValueError):
            StochRsiSpec(period=10, buy_threshold=0.8, sell_threshold=0.8)


if __name__ == "__main__":
    unittest.main()
