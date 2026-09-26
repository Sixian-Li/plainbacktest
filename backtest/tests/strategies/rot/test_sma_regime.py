from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
import pybroker

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import pybroker_daily_state
from quantkit.sma_regime import (
    ALL_CONDITIONS,
    CASE_CONDITIONS,
    CONDITION_ALL_SMAS_RISING_3D,
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_SMA_ORDERING,
    SmaRegimeSpec,
    analysis_slice,
    prepare_sma_regime_data,
    run_pybroker_sma_regime,
    run_reference_sma_regime,
)
from scripts.analyze_sma_regime_ablation import build_market_figure, json_safe, ordered_rows


def bars(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": "QQQ",
            "date": pd.date_range("2020-01-01", periods=len(close), freq="D"),
            "open": np.asarray(close, dtype=float) + 0.25,
            "high": np.asarray(close, dtype=float) + 1.0,
            "low": np.asarray(close, dtype=float) - 1.0,
            "close": close,
            "volume": 1_000,
        }
    )


class SmaRegimeIndicatorTest(unittest.TestCase):
    def test_report_json_safe_converts_pandas_nan_to_null(self) -> None:
        self.assertIsNone(json_safe(float("nan")))
        self.assertEqual(json_safe({"disabled_condition": float("nan")}), {"disabled_condition": None})

    def test_report_comparison_rows_retain_every_case_identity(self) -> None:
        results = pd.DataFrame(
            [
                {"case_id": case_id, "final_equity": 1.0}
                for case_id in CASE_CONDITIONS
            ]
        )
        benchmark = {"final_equity": 1.0}
        rows = ordered_rows(results, benchmark)
        self.assertEqual(
            [item["case_id"] for item in rows],
            [*CASE_CONDITIONS, "buy_hold"],
        )

    def test_market_figure_supplies_v3_price_and_derivative_axes(self) -> None:
        indicators = bars(list(range(10, 30)))
        for window in (30, 200, 250, 300):
            indicators[f"sma{window}"] = indicators["close"].rolling(2).mean()
        orders = pd.DataFrame(
            columns=["case_id", "type", "date", "raw_price", "signal_date", "reason"]
        )
        figure = build_market_figure(indicators, orders)
        self.assertIsNotNone(figure.layout.xaxis2)
        self.assertIsNotNone(figure.layout.yaxis2)
        derivative_traces = [trace for trace in figure.data if trace.yaxis == "y2"]
        self.assertEqual(len(derivative_traces), 4)

    def test_three_sessions_rising_means_three_positive_differences_for_all_four_smas(self) -> None:
        spec = SmaRegimeSpec(
            sma_windows=(2, 3, 4, 5),
            price_sma_window=3,
            ordering_windows=(3, 4, 5),
            rising_sessions=3,
        )
        prepared = prepare_sma_regime_data(
            bars([20, 18, 16, 14, 12, 10, 11, 12, 13, 14, 15, 14, 16]), spec
        )
        for index, row in prepared.iterrows():
            expected = all(
                index >= spec.rising_sessions
                and prepared.loc[index - spec.rising_sessions : index, f"sma{window}"]
                .diff()
                .iloc[1:]
                .gt(0)
                .all()
                for window in spec.sma_windows
            )
            self.assertEqual(bool(row[CONDITION_ALL_SMAS_RISING_3D]), expected)

    def test_level_and_strict_ordering_conditions_use_the_current_completed_close(self) -> None:
        spec = SmaRegimeSpec(
            sma_windows=(2, 3, 4, 5),
            price_sma_window=3,
            ordering_windows=(3, 4, 5),
            rising_sessions=1,
        )
        prepared = prepare_sma_regime_data(bars([10, 9, 8, 7, 8, 9, 10, 11]), spec)
        expected_price = prepared["close"] > prepared["sma3"]
        expected_order = (prepared["sma3"] > prepared["sma4"]) & (
            prepared["sma4"] > prepared["sma5"]
        )
        self.assertEqual(
            prepared[CONDITION_PRICE_ABOVE_SMA200].tolist(), expected_price.tolist()
        )
        self.assertEqual(prepared[CONDITION_SMA_ORDERING].tolist(), expected_order.tolist())

    def test_four_cases_are_the_full_rule_and_each_single_condition_ablation(self) -> None:
        self.assertEqual(CASE_CONDITIONS["all_conditions"], ALL_CONDITIONS)
        self.assertEqual(
            set(CASE_CONDITIONS["without_condition_1"]),
            set(ALL_CONDITIONS) - {CONDITION_PRICE_ABOVE_SMA200},
        )
        self.assertEqual(
            set(CASE_CONDITIONS["without_condition_2"]),
            set(ALL_CONDITIONS) - {CONDITION_ALL_SMAS_RISING_3D},
        )
        self.assertEqual(
            set(CASE_CONDITIONS["without_condition_3"]),
            set(ALL_CONDITIONS) - {CONDITION_SMA_ORDERING},
        )


class SmaRegimeLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pybroker.disable_logging()
        pybroker.disable_progress_bar()

    def test_close_confirmed_state_switches_at_the_next_open_and_matches_reference(self) -> None:
        frame = bars([10, 20, 30, 40, 50, 60])
        frame["regime_ready"] = True
        truth = [False, True, True, False, True, True]
        for condition in ALL_CONDITIONS:
            frame[condition] = truth
        policy = ExplicitFillPolicy("open", 0)
        actual_result = run_pybroker_sma_regime(
            frame, "all_conditions", policy, initial_cash=1_000
        )
        reference = run_reference_sma_regime(
            frame, "all_conditions", policy, initial_cash=1_000
        )
        actual = pybroker_daily_state(actual_result, frame)

        self.assertEqual(
            pd.to_datetime(reference.orders["date"]).dt.strftime("%Y-%m-%d").tolist(),
            ["2020-01-03", "2020-01-05", "2020-01-06"],
        )
        self.assertEqual(reference.orders["type"].tolist(), ["buy", "sell", "buy"])
        self.assertAlmostEqual(float(reference.orders.iloc[0]["fill_price"]), 30.25)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(
                actual[column].to_numpy(float),
                reference.daily[column].to_numpy(float),
                rtol=0,
                atol=1e-9,
            )
        self.assertEqual(
            actual_result.orders["type"].tolist(), reference.orders["type"].tolist()
        )
        np.testing.assert_allclose(
            actual_result.orders["fill_price"].to_numpy(float),
            reference.orders["fill_price"].to_numpy(float),
            rtol=0,
            atol=1e-9,
        )

    def test_shared_analysis_start_waits_for_longest_sma_and_rising_history(self) -> None:
        spec = SmaRegimeSpec(
            sma_windows=(2, 3, 4, 5),
            price_sma_window=3,
            ordering_windows=(3, 4, 5),
            rising_sessions=3,
        )
        prepared = prepare_sma_regime_data(bars(list(range(1, 13))), spec)
        ready = analysis_slice(prepared)
        self.assertEqual(pd.Timestamp(ready.iloc[0]["date"]), pd.Timestamp("2020-01-08"))
        self.assertTrue(ready["regime_ready"].all())


if __name__ == "__main__":
    unittest.main()
