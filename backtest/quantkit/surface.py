"""Reusable diagnostics for two-dimensional parameter surfaces."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


FOUR_NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def connected_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Return four-neighbor components, largest first."""
    if mask.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    seen: set[tuple[int, int]] = set()
    components: list[list[tuple[int, int]]] = []
    rows, columns = mask.shape
    for start in zip(*np.where(mask), strict=True):
        start = (int(start[0]), int(start[1]))
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        component: list[tuple[int, int]] = []
        while stack:
            row, column = stack.pop()
            component.append((row, column))
            for row_delta, column_delta in FOUR_NEIGHBORS:
                neighbor = (row + row_delta, column + column_delta)
                if (
                    0 <= neighbor[0] < rows
                    and 0 <= neighbor[1] < columns
                    and bool(mask[neighbor])
                    and neighbor not in seen
                ):
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return sorted(components, key=len, reverse=True)


def _neighborhood(values: np.ndarray, row: int, column: int) -> np.ndarray:
    return values[
        max(0, row - 1) : min(values.shape[0], row + 2),
        max(0, column - 1) : min(values.shape[1], column + 2),
    ]


def _point(
    values: np.ndarray,
    a_axis: np.ndarray,
    b_axis: np.ndarray,
    row: int,
    column: int,
) -> dict[str, Any]:
    row_start = max(0, row - 1)
    row_end = min(values.shape[0], row + 2)
    column_start = max(0, column - 1)
    column_end = min(values.shape[1], column + 2)
    local = values[row_start:row_end, column_start:column_end]
    center = float(values[row, column])
    center_in_local = (row - row_start, column - column_start)
    neighbor_only = [
        float(local[local_row, local_column])
        for local_row in range(local.shape[0])
        for local_column in range(local.shape[1])
        if (local_row, local_column) != center_in_local
    ]
    neighbor_std = float(np.std(neighbor_only)) if neighbor_only else 0.0
    neighbor_median = float(np.median(neighbor_only)) if neighbor_only else center
    prominence = center - neighbor_median
    spike_threshold = max(0.25, 2.0 * neighbor_std)
    return {
        "a_pct": float(a_axis[row]),
        "b_pct": float(b_axis[column]),
        "value": center,
        "on_a_boundary": bool(row in (0, values.shape[0] - 1)),
        "on_b_boundary": bool(column in (0, values.shape[1] - 1)),
        "neighborhood_cell_count": int(local.size),
        "neighborhood_median": float(np.median(local)),
        "neighborhood_mean": float(np.mean(local)),
        "neighborhood_std": float(np.std(local)),
        "neighborhood_min": float(np.min(local)),
        "neighborhood_max": float(np.max(local)),
        "prominence_vs_neighbor_median": float(prominence),
        "spike_threshold": float(spike_threshold),
        "locally_isolated_spike": bool(prominence > spike_threshold),
    }


def _coords(
    component: Iterable[tuple[int, int]], a_axis: np.ndarray, b_axis: np.ndarray
) -> list[dict[str, float]]:
    return [
        {"a_pct": float(a_axis[row]), "b_pct": float(b_axis[column])}
        for row, column in component
    ]


def analyze_surface(
    frame: pd.DataFrame,
    *,
    metric: str = "cagr_pct",
    top_quantile: float = 0.90,
) -> dict[str, Any]:
    """Analyze a rectangular a/b surface and select a stable representative.

    The representative is chosen only from the largest four-neighbor component
    of top-decile cells. Within that component it maximizes the 3x3 local median
    minus half the local standard deviation. This favors a plateau over a peak.
    """
    required = {"a_pct", "b_pct", metric}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing surface columns: {sorted(missing)}")
    if not 0 < top_quantile < 1:
        raise ValueError("top_quantile must be between zero and one")

    pivot = frame.pivot(index="a_pct", columns="b_pct", values=metric).sort_index().sort_index(axis=1)
    if pivot.isna().any().any():
        raise ValueError("Parameter surface must be a complete rectangle")
    values = pivot.to_numpy(dtype=float)
    a_axis = pivot.index.to_numpy(dtype=float)
    b_axis = pivot.columns.to_numpy(dtype=float)
    threshold = float(np.quantile(values, top_quantile))
    components = connected_components(values >= threshold)
    if not components:
        raise ValueError("No cells passed the surface threshold")
    largest = components[0]

    global_row, global_column = np.unravel_index(np.nanargmax(values), values.shape)
    global_point = _point(values, a_axis, b_axis, int(global_row), int(global_column))
    global_point["in_largest_plateau"] = bool((global_row, global_column) in largest)

    candidates: list[tuple[float, float, int, int, dict[str, Any]]] = []
    for row, column in largest:
        local = _neighborhood(values, row, column)
        local_score = float(np.median(local) - 0.5 * np.std(local))
        full_neighborhood = float(local.size == 9)
        point = _point(values, a_axis, b_axis, row, column)
        point["stability_score"] = local_score
        candidates.append((full_neighborhood, local_score, row, column, point))
    full_candidates = [item for item in candidates if item[0] == 1.0]
    representative = max(full_candidates or candidates, key=lambda item: item[1])[4]

    largest_values = np.array([values[row, column] for row, column in largest])
    largest_coords = _coords(largest, a_axis, b_axis)
    boundary_sides: list[str] = []
    if any(row == 0 for row, _ in largest):
        boundary_sides.append("a_min")
    if any(row == values.shape[0] - 1 for row, _ in largest):
        boundary_sides.append("a_max")
    if any(column == 0 for _, column in largest):
        boundary_sides.append("b_min")
    if any(column == values.shape[1] - 1 for _, column in largest):
        boundary_sides.append("b_max")

    horizontal = np.abs(np.diff(values, axis=1)).ravel()
    vertical = np.abs(np.diff(values, axis=0)).ravel()
    all_steps = np.concatenate((horizontal, vertical))
    value_range = float(np.max(values) - np.min(values))
    return {
        "metric": metric,
        "top_quantile": float(top_quantile),
        "top_threshold": threshold,
        "top_cell_count": int(np.count_nonzero(values >= threshold)),
        "component_count": len(components),
        "component_sizes": [len(component) for component in components],
        "global_best": global_point,
        "stable_representative": representative,
        "largest_plateau": {
            "cell_count": len(largest),
            "share_of_top_cells": len(largest) / int(np.count_nonzero(values >= threshold)),
            "a_min": min(item["a_pct"] for item in largest_coords),
            "a_max": max(item["a_pct"] for item in largest_coords),
            "b_min": min(item["b_pct"] for item in largest_coords),
            "b_max": max(item["b_pct"] for item in largest_coords),
            "value_min": float(np.min(largest_values)),
            "value_median": float(np.median(largest_values)),
            "value_max": float(np.max(largest_values)),
            "touches_search_boundary": bool(boundary_sides),
            "boundary_sides": boundary_sides,
            "cells": largest_coords,
        },
        "surface_roughness": {
            "value_min": float(np.min(values)),
            "value_max": float(np.max(values)),
            "value_range": value_range,
            "median_abs_adjacent_step": float(np.median(all_steps)),
            "mean_abs_adjacent_step": float(np.mean(all_steps)),
            "max_abs_adjacent_step": float(np.max(all_steps)),
            "median_step_over_range": (
                float(np.median(all_steps) / value_range) if value_range else 0.0
            ),
        },
    }
