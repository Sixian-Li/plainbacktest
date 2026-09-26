from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.full_position_trend_quality import (
    IndicatorRepository,
    TrendQualityParameters,
    build_target_long,
    prepare_common_market_data,
    rolling_log_slope_r2,
    run_pybroker_trend_quality,
    run_reference_trend_quality,
)
from quantkit.metrics import pybroker_daily_state
from quantkit.trend_quality_boundary import (
    COORDINATE_COLUMNS,
    build_boundary_grid,
    select_boundary_representative,
)


def bars(symbol: str, closes: list[float], dates: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    index = dates if dates is not None else pd.date_range("2020-01-01", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": index,
            "open": np.asarray(closes, dtype=float) + 0.25,
            "high": np.asarray(closes, dtype=float) + 1.0,
            "low": np.asarray(closes, dtype=float) - 1.0,
            "close": closes,
            "volume": 1_000,
        }
    )


class FullPositionTrendQualityTests(unittest.TestCase):
    def test_expanded_boundary_grid_count_and_parent_anchor(self) -> None:
        parameters = {
            "expanded_grid": {
                "long_sma_window": [120, 180, 200],
                "long_slope_lookback": [3, 10],
                "long_slope_threshold_daily_pct": [0.02, 0.05],
                "short_sma_window": [8, 20],
                "combination_count": 24,
            },
            "fixed_parameters": {
                "short_regression_window": 10,
                "short_quality_threshold_daily_pct": 0.0,
                "entry_confirmation_sessions": 2,
                "relative_strength_lookback": 120,
            },
        }
        grid = build_boundary_grid(parameters)
        self.assertEqual(len(grid), 24)
        parent = grid[
            grid["long_sma_window"].eq(180)
            & grid["long_slope_lookback"].eq(10)
            & grid["long_slope_threshold_daily_pct"].eq(0.02)
            & grid["short_sma_window"].eq(20)
        ]
        self.assertEqual(len(parent), 1)

    def test_connected_plateau_prefers_an_interior_representative(self) -> None:
        rows = []
        coordinates = [
            (0, 1, 1, 1),
            (1, 1, 1, 1),
            (2, 1, 1, 1),
            (1, 0, 1, 1),
            (1, 2, 1, 1),
            (1, 1, 0, 1),
            (1, 1, 2, 1),
            (1, 1, 1, 0),
            (1, 1, 1, 2),
        ]
        for number, coordinate in enumerate(coordinates):
            rows.append(
                {
                    "case_id": f"C{number}",
                    **dict(zip(COORDINATE_COLUMNS, coordinate)),
                    "passes_guard": True,
                    "worst_subwindow_max_drawdown_pct": -10.0 - number * 0.01,
                    "ulcer_index_pct": 1.0 if number == 0 else 2.0 + number * 0.01,
                    "worst_63_session_return_pct": -5.0,
                    "max_drawdown_duration_days": 20,
                    "losing_month_avoidance_pct": 90.0,
                }
            )
        parameters = {
            "expanded_grid": {
                "long_sma_window": [1, 2, 3],
                "long_slope_lookback": [1, 2, 3],
                "long_slope_threshold_daily_pct": [1, 2, 3],
                "short_sma_window": [1, 2, 3],
            },
            "selection": {
                "plateau_drawdown_tolerance_pct_points": 1.0,
                "prefer_non_boundary_candidate": True,
            },
        }
        representative, diagnosed, components = select_boundary_representative(
            pd.DataFrame(rows), parameters
        )
        self.assertEqual(representative["case_id"], "C1")
        self.assertFalse(bool(representative["is_grid_boundary"]))
        self.assertEqual(int(representative["plateau_neighbor_count"]), 8)
        self.assertEqual(int(components.iloc[0]["case_count"]), 9)
        self.assertEqual(len(diagnosed), 9)

    def test_common_calendar_is_an_inner_join(self) -> None:
        q_dates = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
        s_dates = pd.to_datetime(["2020-01-01", "2020-01-03"])
        common = prepare_common_market_data(
            bars("QQQ", [10, 11, 12], q_dates), bars("SPY", [5, 6], s_dates)
        )
        self.assertEqual(common["date"].dt.strftime("%Y-%m-%d").tolist(), ["2020-01-01", "2020-01-03"])
        self.assertEqual(common["relative_price"].tolist(), [2.0, 2.0])

    def test_rolling_log_slope_times_r_squared(self) -> None:
        slope = 0.01
        values = pd.Series(np.exp(2.0 + slope * np.arange(8)))
        score = rolling_log_slope_r2(values, 5)
        self.assertTrue(score.iloc[:4].isna().all())
        self.assertAlmostEqual(float(score.iloc[-1]), slope * 100.0, places=12)

    def test_confirmation_exit_and_state_reentry(self) -> None:
        target = build_target_long(
            np.array([True, True, True, False, True, True, False]), 2
        )
        self.assertEqual(target.tolist(), [False, True, True, False, False, True, False])

    def test_thresholds_are_strict_and_future_values_do_not_leak(self) -> None:
        qqq = bars("QQQ", list(np.linspace(10.0, 25.0, 40)))
        spy = bars("SPY", list(np.linspace(8.0, 16.0, 40)))
        common = prepare_common_market_data(qqq, spy)
        params = TrendQualityParameters(
            long_sma_window=3,
            long_slope_lookback=2,
            long_slope_threshold_daily_pct=0.0,
            short_sma_window=3,
            short_regression_window=3,
            short_quality_threshold_daily_pct=0.0,
            entry_confirmation_sessions=1,
            relative_strength_lookback=2,
        )
        dates = common["date"].iloc[6:20]
        original = IndicatorRepository(common).case_frame(params, "P24", dates)
        changed = common.copy()
        changed.loc[changed.index >= 20, "close"] = 1_000_000.0
        mutated = IndicatorRepository(changed).case_frame(params, "P24", dates)
        pd.testing.assert_series_equal(
            original["short_quality_daily_pct"], mutated["short_quality_daily_pct"]
        )
        threshold_params = TrendQualityParameters(
            **{**params.to_dict(), "long_slope_threshold_daily_pct": float(original.iloc[-1]["long_slope_daily_pct"])}
        )
        strict = IndicatorRepository(common).case_frame(threshold_params, "B2", dates)
        self.assertFalse(bool(strict.iloc[-1]["f2_condition"]))

    def test_next_open_full_position_and_pybroker_reference_match(self) -> None:
        closes = [10.0, 10.2, 10.4, 10.1, 9.9, 10.5, 10.7, 10.8]
        frame = bars("QQQ", closes)
        frame["eligible"] = [False, True, True, False, False, True, True, True]
        frame["target_long"] = build_target_long(frame["eligible"].to_numpy(bool), 2)
        policy = ExplicitFillPolicy("open", 5)
        reference = run_reference_trend_quality(frame, policy, initial_cash=100_000.0)
        result = run_pybroker_trend_quality(frame, policy, initial_cash=100_000.0)
        actual = pybroker_daily_state(result, frame)
        self.assertEqual(reference.orders["date"].dt.strftime("%Y-%m-%d").tolist(), [
            frame.iloc[3]["date"].strftime("%Y-%m-%d"),
            frame.iloc[4]["date"].strftime("%Y-%m-%d"),
            frame.iloc[7]["date"].strftime("%Y-%m-%d"),
        ])
        first_fill = policy.expected_fill("buy", float(frame.iloc[3]["open"]))
        self.assertAlmostEqual(float(reference.orders.iloc[0]["shares"]), 100_000.0 / first_fill, places=10)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(actual[column], reference.daily[column], rtol=0.0, atol=1e-8)
        np.testing.assert_allclose(
            result.orders.reset_index()["fill_price"], reference.orders["fill_price"], rtol=0.0, atol=1e-8
        )


if __name__ == "__main__":
    unittest.main()
