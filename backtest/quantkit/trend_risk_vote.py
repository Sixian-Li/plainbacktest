"""Three-factor QQQ trend voting with a volatility-risk state."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from quantkit.full_position_trend_quality import (
    IndicatorRepository,
    TrendQualityParameters,
)


TOTAL_VOLATILITY = "TOTAL_VOLATILITY"
DOWNSIDE_VOLATILITY = "DOWNSIDE_VOLATILITY"
RISK_KINDS = (TOTAL_VOLATILITY, DOWNSIDE_VOLATILITY)

STRICT_3_OF_3 = "STRICT_3_OF_3"
MAJORITY_2_OF_3 = "MAJORITY_2_OF_3"
RISK_VETO = "RISK_VETO"
DECISION_STRUCTURES = (STRICT_3_OF_3, MAJORITY_2_OF_3, RISK_VETO)

RISK_PARAMETER_COLUMNS = ("short_window", "long_window", "danger_ratio")
RISK_COORDINATE_COLUMNS = tuple(f"grid_index_{name}" for name in RISK_PARAMETER_COLUMNS)


@dataclass(frozen=True)
class RiskVoteParameters:
    short_window: int
    long_window: int
    danger_ratio: float
    recovery_gap: float = 0.4

    def __post_init__(self) -> None:
        if self.short_window < 2 or self.long_window < 2:
            raise ValueError("Volatility windows must be at least 2.")
        if self.short_window >= self.long_window:
            raise ValueError("The short volatility window must be shorter than the long window.")
        if self.danger_ratio <= 0 or self.recovery_gap <= 0:
            raise ValueError("Risk thresholds must be positive.")
        if self.recovery_ratio <= 0 or self.recovery_ratio >= self.danger_ratio:
            raise ValueError("Recovery ratio must be positive and below the danger ratio.")

    @property
    def recovery_ratio(self) -> float:
        return self.danger_ratio - self.recovery_gap

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RiskVoteParameters":
        return cls(
            short_window=int(values["short_window"]),
            long_window=int(values["long_window"]),
            danger_ratio=float(values["danger_ratio"]),
            recovery_gap=float(values.get("recovery_gap", 0.4)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "recovery_ratio": self.recovery_ratio}


def rolling_volatility(log_returns: pd.Series, window: int, risk_kind: str) -> pd.Series:
    """Root mean square return; downside mode sets positive returns to zero."""
    if window < 2:
        raise ValueError("window must be at least 2")
    if risk_kind not in RISK_KINDS:
        raise ValueError(f"Unknown risk kind: {risk_kind!r}")
    values = log_returns.astype(float)
    if risk_kind == DOWNSIDE_VOLATILITY:
        values = values.clip(upper=0.0)
    return values.pow(2).rolling(window, min_periods=window).mean().pow(0.5)


def volatility_ratio(
    close: pd.Series,
    parameters: RiskVoteParameters,
    risk_kind: str,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    log_returns = np.log(close.astype(float) / close.astype(float).shift(1))
    short = rolling_volatility(log_returns, parameters.short_window, risk_kind)
    long = rolling_volatility(log_returns, parameters.long_window, risk_kind)
    ratio = short.div(long.where(long > np.finfo(float).eps))
    return short, long, ratio


def risk_safe_hysteresis(
    ratio: Iterable[float],
    *,
    danger_ratio: float,
    recovery_ratio: float,
) -> np.ndarray:
    """Risk is unsafe above danger and rearms only below recovery."""
    if recovery_ratio <= 0 or recovery_ratio >= danger_ratio:
        raise ValueError("recovery_ratio must be positive and below danger_ratio")
    values = np.asarray(list(ratio), dtype=float)
    result = np.zeros(len(values), dtype=bool)
    safe = False
    initialized = False
    for index, value in enumerate(values):
        if np.isfinite(value):
            if not initialized:
                safe = bool(value <= danger_ratio)
                initialized = True
            elif safe and value > danger_ratio:
                safe = False
            elif not safe and value < recovery_ratio:
                safe = True
        result[index] = safe if initialized else False
    return result


def build_three_factor_target(
    long_factor: Iterable[bool],
    short_factor: Iterable[bool],
    risk_safe: Iterable[bool],
    *,
    decision_structure: str,
    confirmation_sessions: int,
) -> np.ndarray:
    """Convert three close-known factors into a causal desired-position state."""
    if decision_structure not in DECISION_STRUCTURES:
        raise ValueError(f"Unknown decision structure: {decision_structure!r}")
    if confirmation_sessions < 1:
        raise ValueError("confirmation_sessions must be positive")
    long_values = np.asarray(list(long_factor), dtype=bool)
    short_values = np.asarray(list(short_factor), dtype=bool)
    risk_values = np.asarray(list(risk_safe), dtype=bool)
    if not (len(long_values) == len(short_values) == len(risk_values)):
        raise ValueError("Factor arrays must have the same length.")
    target = np.zeros(len(long_values), dtype=bool)
    desired = False
    streak = 0
    for index, (long_ok, short_ok, risk_ok) in enumerate(
        zip(long_values, short_values, risk_values, strict=True)
    ):
        votes = int(long_ok) + int(short_ok) + int(risk_ok)
        if decision_structure == MAJORITY_2_OF_3:
            entry_ok = votes >= 2
            hold_ok = votes >= 2
        elif decision_structure == STRICT_3_OF_3:
            entry_ok = votes == 3
            hold_ok = votes == 3
        else:
            entry_ok = votes == 3
            hold_ok = bool(risk_ok and (long_ok or short_ok))
        if desired:
            if not hold_ok:
                desired = False
                streak = 0
        else:
            streak = streak + 1 if entry_ok else 0
            if streak >= confirmation_sessions:
                desired = True
        target[index] = desired
    return target


def factor_frame(
    repository: IndicatorRepository,
    trend: TrendQualityParameters,
    risk: RiskVoteParameters,
    *,
    risk_kind: str,
    decision_structure: str,
    analysis_dates: Iterable[pd.Timestamp],
) -> pd.DataFrame:
    """Build completed-close factors and target state without future data."""
    common = repository.common.copy().reset_index(drop=True)
    common["long_sma"] = repository.long_sma(trend.long_sma_window)
    common["long_slope_daily_pct"] = repository.long_slope(
        trend.long_sma_window, trend.long_slope_lookback
    )
    common["short_sma"] = repository.short_sma(trend.short_sma_window)
    common["short_quality_daily_pct"] = repository.short_quality(
        trend.short_sma_window, trend.short_regression_window
    )
    common["long_factor"] = (
        (common["close"] > common["long_sma"])
        & (common["long_slope_daily_pct"] > trend.long_slope_threshold_daily_pct)
    ).fillna(False)
    common["short_factor"] = (
        common["short_quality_daily_pct"] > trend.short_quality_threshold_daily_pct
    ).fillna(False)
    short_vol, long_vol, ratio = volatility_ratio(common["close"], risk, risk_kind)
    common["short_risk_volatility"] = short_vol
    common["long_risk_volatility"] = long_vol
    common["risk_ratio"] = ratio
    common["risk_safe"] = risk_safe_hysteresis(
        ratio,
        danger_ratio=risk.danger_ratio,
        recovery_ratio=risk.recovery_ratio,
    )
    requested = pd.DatetimeIndex(pd.to_datetime(list(analysis_dates)))
    frame = common[common["date"].isin(requested)].copy().reset_index(drop=True)
    if len(frame) != len(requested) or not pd.DatetimeIndex(frame["date"]).equals(requested):
        raise ValueError("Analysis dates must be an ordered subset of the common calendar.")
    frame["factor_votes"] = (
        frame["long_factor"].astype(int)
        + frame["short_factor"].astype(int)
        + frame["risk_safe"].astype(int)
    )
    frame["entry_eligible"] = (
        frame["factor_votes"].ge(2)
        if decision_structure == MAJORITY_2_OF_3
        else frame["factor_votes"].eq(3)
    )
    frame["eligible"] = frame["entry_eligible"]
    frame["target_long"] = build_three_factor_target(
        frame["long_factor"],
        frame["short_factor"],
        frame["risk_safe"],
        decision_structure=decision_structure,
        confirmation_sessions=trend.entry_confirmation_sessions,
    )
    frame["risk_kind"] = risk_kind
    frame["decision_structure"] = decision_structure
    frame["danger_ratio"] = risk.danger_ratio
    frame["recovery_ratio"] = risk.recovery_ratio
    return frame


def build_risk_grid(parameters: Mapping[str, Any]) -> pd.DataFrame:
    grid = parameters["risk_grid"]
    rows: list[dict[str, Any]] = []
    value_lists = [list(grid[name]) for name in RISK_PARAMETER_COLUMNS]
    expected_each = int(grid["combination_count_per_structure_and_kind"])
    for structure in parameters["decision_structures"]:
        if structure not in DECISION_STRUCTURES:
            raise ValueError(f"Unknown frozen decision structure: {structure!r}")
        for risk_kind in parameters["risk_kinds"]:
            if risk_kind not in RISK_KINDS:
                raise ValueError(f"Unknown frozen risk kind: {risk_kind!r}")
            count = 0
            for coordinates in product(*(range(len(values)) for values in value_lists)):
                count += 1
                values = {
                    name: value_lists[index][coordinate]
                    for index, (name, coordinate) in enumerate(
                        zip(RISK_PARAMETER_COLUMNS, coordinates, strict=True)
                    )
                }
                rows.append(
                    {
                        "case_id": f"R{len(rows) + 1:04d}",
                        "decision_structure": structure,
                        "risk_kind": risk_kind,
                        **values,
                        "recovery_gap": float(grid["recovery_gap"]),
                        "recovery_ratio": float(values["danger_ratio"])
                        - float(grid["recovery_gap"]),
                        **{
                            name: coordinate
                            for name, coordinate in zip(
                                RISK_COORDINATE_COLUMNS, coordinates, strict=True
                            )
                        },
                    }
                )
            if count != expected_each:
                raise ValueError("Risk grid count per structure and kind does not match the frozen definition.")
    if len(rows) != int(grid["total_risk_cases_per_cost"]):
        raise ValueError("Total risk grid count does not match the frozen definition.")
    return pd.DataFrame(rows)


def _coordinate(record: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(int(record[name]) for name in RISK_COORDINATE_COLUMNS)


def _neighbors(coordinate: tuple[int, ...]) -> list[tuple[int, ...]]:
    result: list[tuple[int, ...]] = []
    for dimension in range(len(coordinate)):
        for offset in (-1, 1):
            candidate = list(coordinate)
            candidate[dimension] += offset
            result.append(tuple(candidate))
    return result


def diagnose_risk_plateau(
    plateau: pd.DataFrame,
    grid_shape: tuple[int, int, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if plateau.empty:
        empty = plateau.copy()
        empty["plateau_component_id"] = pd.Series(dtype="object")
        empty["plateau_neighbor_count"] = pd.Series(dtype="int64")
        empty["is_grid_boundary"] = pd.Series(dtype="bool")
        return empty, pd.DataFrame()
    coordinate_set = {_coordinate(row) for _, row in plateau.iterrows()}
    unvisited = set(coordinate_set)
    components: list[list[tuple[int, ...]]] = []
    while unvisited:
        start = min(unvisited)
        unvisited.remove(start)
        queue: deque[tuple[int, ...]] = deque([start])
        component: list[tuple[int, ...]] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in _neighbors(current):
                if neighbor in unvisited:
                    unvisited.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda item: item[0])
    component_by_coordinate = {
        coordinate: f"PC{number:03d}"
        for number, component in enumerate(components, start=1)
        for coordinate in component
    }
    diagnosed = plateau.copy().reset_index(drop=True)
    diagnosed["plateau_component_id"] = [
        component_by_coordinate[_coordinate(row)] for _, row in diagnosed.iterrows()
    ]
    diagnosed["plateau_neighbor_count"] = [
        sum(neighbor in coordinate_set for neighbor in _neighbors(_coordinate(row)))
        for _, row in diagnosed.iterrows()
    ]
    diagnosed["is_grid_boundary"] = [
        any(value == 0 or value == grid_shape[index] - 1 for index, value in enumerate(_coordinate(row)))
        for _, row in diagnosed.iterrows()
    ]
    summaries: list[dict[str, Any]] = []
    for component_id, frame in diagnosed.groupby("plateau_component_id", sort=True):
        summaries.append(
            {
                "plateau_component_id": component_id,
                "case_count": len(frame),
                "interior_case_count": int((~frame["is_grid_boundary"].astype(bool)).sum()),
                "maximum_direct_neighbor_count": int(frame["plateau_neighbor_count"].max()),
                "best_worst_subwindow_max_drawdown_pct": float(
                    frame["worst_subwindow_max_drawdown_pct"].max()
                ),
                "mean_ulcer_index_pct": float(frame["ulcer_index_pct"].mean()),
            }
        )
    return diagnosed, pd.DataFrame(summaries)


def select_principal_representative(
    results: pd.DataFrame,
    parameters: Mapping[str, Any],
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    principal = parameters["principal_candidate"]
    selection = parameters["selection"]
    candidates = results[
        results["decision_structure"].eq(principal["decision_structure"])
        & results["risk_kind"].eq(principal["risk_kind"])
        & results["passes_guard"].astype(bool)
    ].copy()
    if candidates.empty:
        raise ValueError("No principal downside-risk-veto case passes the frozen guards.")
    best = float(candidates["worst_subwindow_max_drawdown_pct"].max())
    raw_plateau = candidates[
        candidates["worst_subwindow_max_drawdown_pct"].ge(
            best - float(selection["plateau_drawdown_tolerance_pct_points"])
        )
    ].copy()
    grid = parameters["risk_grid"]
    shape = tuple(len(grid[name]) for name in RISK_PARAMETER_COLUMNS)
    plateau, components = diagnose_risk_plateau(raw_plateau, shape)  # type: ignore[arg-type]
    component = components.sort_values(
        ["case_count", "interior_case_count", "best_worst_subwindow_max_drawdown_pct", "mean_ulcer_index_pct", "plateau_component_id"],
        ascending=[False, False, False, True, True],
    ).iloc[0]
    selected = plateau[
        plateau["plateau_component_id"].eq(component["plateau_component_id"])
    ].copy()
    if bool(selection["prefer_non_boundary_candidate"]) and (~selected["is_grid_boundary"]).any():
        selected = selected[~selected["is_grid_boundary"]].copy()
    for name in RISK_PARAMETER_COLUMNS:
        selected[f"distance_{name}"] = abs(
            selected[name].astype(float) - float(selected[name].astype(float).median())
        )
    selected["plateau_center_distance"] = selected[
        [f"distance_{name}" for name in RISK_PARAMETER_COLUMNS]
    ].sum(axis=1)
    representative = selected.sort_values(
        [
            "ulcer_index_pct",
            "worst_63_session_return_pct",
            "max_drawdown_duration_days",
            "losing_month_avoidance_pct",
            "plateau_center_distance",
            "case_id",
        ],
        ascending=[True, False, True, False, True, True],
    ).iloc[0]
    return representative, plateau, components
