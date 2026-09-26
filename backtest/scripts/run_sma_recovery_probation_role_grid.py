#!/usr/bin/env python3
"""Run the role-constrained QQQ recovery-probation period grid."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from quantkit.sma_recovery_probation_search import RecoveryProbationScreen
from quantkit.sma_recovery_role_selection import (
    analyze_role_constrained_surface,
    apply_long_buy_short_sell_gate,
)
from scripts import run_sma_recovery_probation_grid as base


DEFAULT_EXPERIMENT = (
    Path(__file__).resolve().parents[1]
    / "experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015"
)
_PRIMARY_SCREEN = base.screen_recovery_probation_cases
_REFERENCE_SCREEN = base.screen_recovery_probation_cases_reference
_DEFLATED_SHARPE = base.deflated_sharpe_probability


def _gate_screen(screen: RecoveryProbationScreen) -> RecoveryProbationScreen:
    screen.metrics = apply_long_buy_short_sell_gate(screen.metrics)
    return screen


def role_primary_screen(*args: Any, **kwargs: Any) -> RecoveryProbationScreen:
    return _gate_screen(_PRIMARY_SCREEN(*args, **kwargs))


def role_reference_screen(*args: Any, **kwargs: Any) -> RecoveryProbationScreen:
    return _gate_screen(_REFERENCE_SCREEN(*args, **kwargs))


def role_surface(results: pd.DataFrame, parameters: dict[str, Any]) -> dict[str, Any]:
    config = parameters["surface_selection"]
    return analyze_role_constrained_surface(
        results,
        metric="primary_metric",
        top_quantile=float(config["top_quantile"]),
        minimum_component_cells=int(config["minimum_component_cells"]),
        minimum_buy_span=int(config["minimum_buy_window_span"]),
        minimum_sell_span=int(config["minimum_sell_window_span"]),
    )


def role_deflated_sharpe_probability(
    returns: Any,
    trial_sharpes: Any,
    *,
    trial_count: float,
) -> dict[str, Any]:
    if int(round(float(trial_count))) == 1_444:
        result = _DEFLATED_SHARPE(
            returns,
            trial_sharpes,
            trial_count=float(len(trial_sharpes)),
        )
        result["trial_policy"] = "all_eligible_role_cases"
        return result
    return _DEFLATED_SHARPE(returns, trial_sharpes, trial_count=trial_count)


def main() -> None:
    base.DEFAULT_EXPERIMENT = DEFAULT_EXPERIMENT
    base.screen_recovery_probation_cases = role_primary_screen
    base.screen_recovery_probation_cases_reference = role_reference_screen
    base.analyze_surface = role_surface
    base.deflated_sharpe_probability = role_deflated_sharpe_probability
    base.main()


if __name__ == "__main__":
    main()
