from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import pybroker

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import pybroker_daily_state
from quantkit.scheduled import (
    ScheduledOrder,
    run_pybroker_schedule,
    run_reference_schedule,
    validate_schedule,
)


def bars() -> pd.DataFrame:
    close = [9.0, 10.0, 12.0, 8.0, 9.0, 11.0]
    return pd.DataFrame(
        {
            "symbol": ["AAA"] * len(close),
            "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
            "volume": [1_000] * len(close),
        }
    )


class ScheduledBacktestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pybroker.disable_logging()
        pybroker.disable_progress_bar()

    def test_seeded_position_and_schedule_match_independent_ledger(self) -> None:
        frame = bars()
        schedule = (
            ScheduledOrder("2020-01-03", "sell"),
            ScheduledOrder("2020-01-04", "buy"),
            ScheduledOrder("2020-01-06", "sell"),
        )
        policy = ExplicitFillPolicy("close", 5)
        result = run_pybroker_schedule(
            frame,
            schedule,
            initial_date="2020-01-02",
            initial_shares=10,
            policy=policy,
        )
        analysis = frame[frame["date"] >= pd.Timestamp("2020-01-02")].reset_index(drop=True)
        actual = pybroker_daily_state(result, frame)
        actual = actual[actual["date"] >= pd.Timestamp("2020-01-02")].reset_index(drop=True)
        reference = run_reference_schedule(
            analysis,
            schedule,
            initial_date="2020-01-02",
            initial_shares=10,
            policy=policy,
            seed_signal_date="2020-01-01",
        )
        np.testing.assert_allclose(actual["cash"], reference.daily["cash"], atol=1e-10)
        np.testing.assert_allclose(actual["shares"], reference.daily["shares"], atol=1e-10)
        np.testing.assert_allclose(actual["equity"], reference.daily["equity"], atol=1e-10)
        self.assertEqual(result.orders["type"].tolist(), ["buy", "sell", "buy", "sell"])
        self.assertAlmostEqual(float(result.orders.iloc[0]["fill_price"]), 10.0)
        self.assertAlmostEqual(float(result.orders.iloc[1]["fill_price"]), 12.0 * 0.9995)
        self.assertAlmostEqual(float(result.orders.iloc[2]["fill_price"]), 8.0 * 1.0005)
        self.assertAlmostEqual(float(result.orders.iloc[3]["fill_price"]), 11.0 * 0.9995)

    def test_schedule_requires_existing_dates_and_alternating_sides(self) -> None:
        frame = bars()
        with self.assertRaisesRegex(ValueError, "not trading dates"):
            validate_schedule(
                frame,
                (ScheduledOrder("2020-01-10", "sell"),),
                initial_date="2020-01-02",
                initial_position="long",
            )
        with self.assertRaisesRegex(ValueError, "alternate"):
            validate_schedule(
                frame,
                (
                    ScheduledOrder("2020-01-03", "sell"),
                    ScheduledOrder("2020-01-04", "sell"),
                ),
                initial_date="2020-01-02",
                initial_position="long",
            )


if __name__ == "__main__":
    unittest.main()
