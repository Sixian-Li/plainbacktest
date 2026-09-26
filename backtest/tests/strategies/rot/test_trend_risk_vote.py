from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.full_position_trend_quality import (
    IndicatorRepository,
    TrendQualityParameters,
    prepare_common_market_data,
    run_pybroker_trend_quality,
    run_reference_trend_quality,
)
from quantkit.metrics import pybroker_daily_state
from quantkit.trend_risk_vote import (
    DOWNSIDE_VOLATILITY,
    MAJORITY_2_OF_3,
    RISK_VETO,
    STRICT_3_OF_3,
    TOTAL_VOLATILITY,
    RiskVoteParameters,
    build_risk_grid,
    build_three_factor_target,
    factor_frame,
    risk_safe_hysteresis,
    rolling_volatility,
)


def bars(symbol: str, closes: list[float]) -> pd.DataFrame:
    values = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.date_range("2020-01-01", periods=len(values), freq="B"),
            "open": values + 0.25,
            "high": values + 1.0,
            "low": values - 1.0,
            "close": values,
            "volume": 1_000,
        }
    )


class TrendRiskVoteTests(unittest.TestCase):
    def test_downside_volatility_ignores_positive_returns(self) -> None:
        returns = pd.Series([np.nan, 0.02, -0.01, 0.03, -0.02])
        total = rolling_volatility(returns, 4, TOTAL_VOLATILITY)
        downside = rolling_volatility(returns, 4, DOWNSIDE_VOLATILITY)
        self.assertGreater(float(total.iloc[-1]), float(downside.iloc[-1]))
        self.assertAlmostEqual(
            float(downside.iloc[-1]),
            np.sqrt((0.0 + 0.01**2 + 0.0 + 0.02**2) / 4.0),
            places=12,
        )

    def test_risk_hysteresis_uses_strict_alarm_and_recovery(self) -> None:
        safe = risk_safe_hysteresis(
            [np.nan, 1.2, 1.6, 1.5, 1.1, 1.09, 1.4],
            danger_ratio=1.5,
            recovery_ratio=1.1,
        )
        self.assertEqual(safe.tolist(), [False, True, False, False, False, True, True])

    def test_three_decision_structures_have_distinct_hold_semantics(self) -> None:
        long_factor = [True, True, True, True, False, False, True, True]
        short_factor = [True, True, False, True, True, False, True, True]
        risk_safe = [True, True, True, False, True, True, True, True]
        strict = build_three_factor_target(
            long_factor,
            short_factor,
            risk_safe,
            decision_structure=STRICT_3_OF_3,
            confirmation_sessions=2,
        )
        majority = build_three_factor_target(
            long_factor,
            short_factor,
            risk_safe,
            decision_structure=MAJORITY_2_OF_3,
            confirmation_sessions=2,
        )
        veto = build_three_factor_target(
            long_factor,
            short_factor,
            risk_safe,
            decision_structure=RISK_VETO,
            confirmation_sessions=2,
        )
        self.assertEqual(strict.tolist(), [False, True, False, False, False, False, False, True])
        self.assertEqual(majority.tolist(), [False, True, True, True, True, False, False, True])
        self.assertEqual(veto.tolist(), [False, True, True, False, False, False, False, True])

    def test_frozen_grid_has_162_unique_risk_cases(self) -> None:
        parameters = {
            "decision_structures": [STRICT_3_OF_3, MAJORITY_2_OF_3, RISK_VETO],
            "risk_kinds": [TOTAL_VOLATILITY, DOWNSIDE_VOLATILITY],
            "risk_grid": {
                "short_window": [10, 20, 30],
                "long_window": [90, 120, 180],
                "danger_ratio": [1.3, 1.5, 1.7],
                "recovery_gap": 0.4,
                "combination_count_per_structure_and_kind": 27,
                "total_risk_cases_per_cost": 162,
            },
        }
        grid = build_risk_grid(parameters)
        self.assertEqual(len(grid), 162)
        self.assertFalse(
            grid.duplicated(
                ["decision_structure", "risk_kind", "short_window", "long_window", "danger_ratio"]
            ).any()
        )
        center = grid[
            grid["decision_structure"].eq(RISK_VETO)
            & grid["risk_kind"].eq(DOWNSIDE_VOLATILITY)
            & grid["short_window"].eq(20)
            & grid["long_window"].eq(120)
            & grid["danger_ratio"].eq(1.5)
        ]
        self.assertEqual(len(center), 1)
        self.assertAlmostEqual(float(center.iloc[0]["recovery_ratio"]), 1.1)

    def test_factor_frame_is_causal_and_risk_exit_is_same_close_state(self) -> None:
        closes = (100.0 * np.exp(np.linspace(0.0, 0.4, 260))).tolist()
        qqq = bars("QQQ", closes)
        spy = bars("SPY", (80.0 * np.exp(np.linspace(0.0, 0.2, 260))).tolist())
        common = prepare_common_market_data(qqq, spy)
        trend = TrendQualityParameters(
            long_sma_window=20,
            long_slope_lookback=3,
            long_slope_threshold_daily_pct=0.0,
            short_sma_window=10,
            short_regression_window=5,
            short_quality_threshold_daily_pct=0.0,
            entry_confirmation_sessions=2,
            relative_strength_lookback=20,
        )
        risk = RiskVoteParameters(short_window=10, long_window=30, danger_ratio=1.5)
        dates = common["date"].iloc[80:180]
        original = factor_frame(
            IndicatorRepository(common),
            trend,
            risk,
            risk_kind=DOWNSIDE_VOLATILITY,
            decision_structure=RISK_VETO,
            analysis_dates=dates,
        )
        changed = common.copy()
        changed.loc[changed.index >= 180, "close"] = 1.0
        mutated = factor_frame(
            IndicatorRepository(changed),
            trend,
            risk,
            risk_kind=DOWNSIDE_VOLATILITY,
            decision_structure=RISK_VETO,
            analysis_dates=dates,
        )
        pd.testing.assert_series_equal(original["risk_ratio"], mutated["risk_ratio"])
        pd.testing.assert_series_equal(original["target_long"], mutated["target_long"])

    def test_full_position_next_open_matches_independent_ledger(self) -> None:
        frame = bars("QQQ", [10.0, 10.2, 10.4, 10.1, 9.9, 10.5, 10.7, 10.8])
        frame["eligible"] = [False, True, True, False, False, True, True, True]
        frame["target_long"] = [False, False, True, False, False, False, True, True]
        policy = ExplicitFillPolicy("open", 5)
        reference = run_reference_trend_quality(frame, policy, initial_cash=100_000.0)
        result = run_pybroker_trend_quality(frame, policy, initial_cash=100_000.0)
        actual = pybroker_daily_state(result, frame)
        for column in ("cash", "shares", "equity"):
            np.testing.assert_allclose(
                actual[column], reference.daily[column], rtol=0.0, atol=1e-8
            )


if __name__ == "__main__":
    unittest.main()
