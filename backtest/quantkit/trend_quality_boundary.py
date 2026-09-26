"""Four-dimensional boundary grid and connected-plateau selection helpers."""

from __future__ import annotations

from collections import deque
from itertools import product
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.full_position_trend_quality import PARAMETER_COLUMNS


EXPANDED_PARAMETER_COLUMNS = (
    "long_sma_window",
    "long_slope_lookback",
    "long_slope_threshold_daily_pct",
    "short_sma_window",
)
COORDINATE_COLUMNS = tuple(f"grid_index_{name}" for name in EXPANDED_PARAMETER_COLUMNS)


def build_boundary_grid(parameters: Mapping[str, Any]) -> pd.DataFrame:
    expanded = parameters["expanded_grid"]
    fixed = dict(parameters["fixed_parameters"])
    value_lists = [list(expanded[name]) for name in EXPANDED_PARAMETER_COLUMNS]
    expected = int(expanded["combination_count"])
    rows: list[dict[str, Any]] = []
    for number, coordinates in enumerate(product(*(range(len(values)) for values in value_lists)), start=1):
        varying = {
            name: value_lists[index][coordinate]
            for index, (name, coordinate) in enumerate(zip(EXPANDED_PARAMETER_COLUMNS, coordinates))
        }
        row = {"case_id": f"E{number:05d}", **varying, **fixed}
        row.update({name: coordinate for name, coordinate in zip(COORDINATE_COLUMNS, coordinates)})
        rows.append(row)
    if len(rows) != expected:
        raise ValueError(f"Expanded grid count {len(rows)} does not match frozen {expected}.")
    result = pd.DataFrame(rows)
    if result.duplicated(list(PARAMETER_COLUMNS)).any():
        raise ValueError("Expanded grid contains duplicate strategy parameters.")
    return result


def coordinate_tuple(row: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(int(row[name]) for name in COORDINATE_COLUMNS)


def adjacent_coordinates(coordinate: tuple[int, ...]) -> list[tuple[int, ...]]:
    neighbors: list[tuple[int, ...]] = []
    for dimension in range(len(coordinate)):
        for offset in (-1, 1):
            candidate = list(coordinate)
            candidate[dimension] += offset
            neighbors.append(tuple(candidate))
    return neighbors


def connected_plateau_diagnostics(
    plateau: pd.DataFrame,
    grid_shape: Sequence[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Label Manhattan-connected components and direct neighbors."""
    if plateau.empty:
        empty = plateau.copy()
        empty["plateau_component_id"] = pd.Series(dtype="object")
        empty["plateau_neighbor_count"] = pd.Series(dtype="int64")
        empty["is_grid_boundary"] = pd.Series(dtype="bool")
        return empty, pd.DataFrame()
    coordinate_to_index = {
        coordinate_tuple(record): index
        for index, record in plateau.reset_index(drop=True).iterrows()
    }
    unvisited = set(coordinate_to_index)
    component_by_coordinate: dict[tuple[int, ...], str] = {}
    components: list[list[tuple[int, ...]]] = []
    while unvisited:
        start = min(unvisited)
        queue: deque[tuple[int, ...]] = deque([start])
        unvisited.remove(start)
        component: list[tuple[int, ...]] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacent_coordinates(current):
                if neighbor in unvisited:
                    unvisited.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda item: item[0])
    for number, component in enumerate(components, start=1):
        component_id = f"PC{number:03d}"
        for coordinate in component:
            component_by_coordinate[coordinate] = component_id
    result = plateau.copy().reset_index(drop=True)
    result["plateau_component_id"] = [component_by_coordinate[coordinate_tuple(row)] for _, row in result.iterrows()]
    coordinate_set = set(coordinate_to_index)
    result["plateau_neighbor_count"] = [
        sum(neighbor in coordinate_set for neighbor in adjacent_coordinates(coordinate_tuple(row)))
        for _, row in result.iterrows()
    ]
    result["is_grid_boundary"] = [
        any(coordinate == 0 or coordinate == int(grid_shape[index]) - 1 for index, coordinate in enumerate(coordinate_tuple(row)))
        for _, row in result.iterrows()
    ]
    summaries: list[dict[str, Any]] = []
    for component_id, frame in result.groupby("plateau_component_id", sort=True):
        summaries.append(
            {
                "plateau_component_id": component_id,
                "case_count": len(frame),
                "interior_case_count": int((~frame["is_grid_boundary"].astype(bool)).sum()),
                "boundary_case_count": int(frame["is_grid_boundary"].astype(bool).sum()),
                "best_worst_subwindow_max_drawdown_pct": float(frame["worst_subwindow_max_drawdown_pct"].max()),
                "median_worst_subwindow_max_drawdown_pct": float(frame["worst_subwindow_max_drawdown_pct"].median()),
                "mean_ulcer_index_pct": float(frame["ulcer_index_pct"].mean()),
                "maximum_direct_neighbor_count": int(frame["plateau_neighbor_count"].max()),
            }
        )
    return result, pd.DataFrame(summaries)


def select_boundary_representative(
    results: pd.DataFrame,
    parameters: Mapping[str, Any],
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    selection = parameters["selection"]
    candidates = results[results["passes_guard"].astype(bool)].copy()
    if candidates.empty:
        raise ValueError("No expanded-grid case passes the frozen activity and CAGR guards.")
    best = float(candidates["worst_subwindow_max_drawdown_pct"].max())
    plateau = candidates[
        candidates["worst_subwindow_max_drawdown_pct"].astype(float).ge(
            best - float(selection["plateau_drawdown_tolerance_pct_points"])
        )
    ].copy()
    expanded = parameters["expanded_grid"]
    shape = [len(expanded[name]) for name in EXPANDED_PARAMETER_COLUMNS]
    diagnosed, components = connected_plateau_diagnostics(plateau, shape)
    chosen_component = components.sort_values(
        [
            "case_count",
            "interior_case_count",
            "best_worst_subwindow_max_drawdown_pct",
            "median_worst_subwindow_max_drawdown_pct",
            "mean_ulcer_index_pct",
            "plateau_component_id",
        ],
        ascending=[False, False, False, False, True, True],
    ).iloc[0]
    pool = diagnosed[
        diagnosed["plateau_component_id"].eq(chosen_component["plateau_component_id"])
    ].copy()
    if bool(selection["prefer_non_boundary_candidate"]) and (~pool["is_grid_boundary"].astype(bool)).any():
        pool = pool[~pool["is_grid_boundary"].astype(bool)].copy()
    for column in COORDINATE_COLUMNS:
        values = pool[column].astype(float)
        scale = max(float(values.max() - values.min()), 1.0)
        pool[f"distance_{column}"] = abs(values - float(values.median())) / scale
    pool["component_center_distance"] = pool[
        [f"distance_{column}" for column in COORDINATE_COLUMNS]
    ].sum(axis=1)
    representative = pool.sort_values(
        [
            "ulcer_index_pct",
            "worst_63_session_return_pct",
            "max_drawdown_duration_days",
            "losing_month_avoidance_pct",
            "component_center_distance",
            "worst_subwindow_max_drawdown_pct",
            "case_id",
        ],
        ascending=[True, False, True, False, True, False, True],
    ).iloc[0]
    return representative, diagnosed, components
