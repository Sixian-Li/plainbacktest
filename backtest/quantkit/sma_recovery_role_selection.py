"""Role-consistent selection for the recovery-probation SMA grid.

The execution ledger is unchanged: all 38x38 cells are still calculated.  This
module only limits research selection to the intended economic ordering, where
the recovery-entry SMA is longer than the confirmed-exit SMA.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from quantkit.surface import connected_components


def apply_long_buy_short_sell_gate(frame: pd.DataFrame) -> pd.DataFrame:
    """Preserve event identifiability and add the strict buy-window role gate."""

    required = {"buy_window", "sell_window", "identifiable"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing role-gate columns: {sorted(missing)}")
    result = frame.copy()
    result["event_identifiable"] = result["identifiable"].astype(bool)
    result["role_consistent"] = result["buy_window"].astype(int) > result[
        "sell_window"
    ].astype(int)
    result["identifiable"] = result["event_identifiable"] & result["role_consistent"]
    return result


def analyze_role_constrained_surface(
    frame: pd.DataFrame,
    *,
    metric: str,
    top_quantile: float,
    minimum_component_cells: int,
    minimum_buy_span: int,
    minimum_sell_span: int,
) -> dict[str, Any]:
    """Analyze a complete grid while excluding reversed/equal SMA roles."""

    required = {
        "buy_window",
        "sell_window",
        metric,
        "sharpe",
        "cagr_pct",
        "order_count",
        "identifiable",
        "event_identifiable",
        "role_consistent",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing role-constrained surface columns: {sorted(missing)}")
    if not 0 < top_quantile < 1:
        raise ValueError("top_quantile must be between zero and one.")
    metric_pivot = (
        frame.pivot(index="buy_window", columns="sell_window", values=metric)
        .sort_index()
        .sort_index(axis=1)
    )
    if metric_pivot.isna().any().any():
        raise ValueError("Period surface must be a complete rectangle before role gating.")
    gate = (
        frame.pivot(index="buy_window", columns="sell_window", values="identifiable")
        .reindex_like(metric_pivot)
        .to_numpy(bool)
    )
    role_gate = (
        frame.pivot(index="buy_window", columns="sell_window", values="role_consistent")
        .reindex_like(metric_pivot)
        .to_numpy(bool)
    )
    event_gate = (
        frame.pivot(index="buy_window", columns="sell_window", values="event_identifiable")
        .reindex_like(metric_pivot)
        .to_numpy(bool)
    )
    values = metric_pivot.to_numpy(float)
    eligible_values = values[gate]
    if not len(eligible_values):
        raise ValueError("No role-consistent identifiable cells remain.")
    threshold = float(np.quantile(eligible_values, top_quantile))
    mask = gate & (values >= threshold)
    components = connected_components(mask)
    if not components:
        raise ValueError("No role-consistent cells passed the surface threshold.")
    component = components[0]
    buy_axis = metric_pivot.index.to_numpy(int)
    sell_axis = metric_pivot.columns.to_numpy(int)
    if len(buy_axis) < 2 or len(sell_axis) < 2:
        raise ValueError("Role-constrained surface requires at least two values per axis.")
    buy_step = int(np.min(np.diff(buy_axis)))
    sell_step = int(np.min(np.diff(sell_axis)))
    buy_min = int(min(buy_axis[row] for row, _ in component))
    buy_max = int(max(buy_axis[row] for row, _ in component))
    sell_min = int(min(sell_axis[column] for _, column in component))
    sell_max = int(max(sell_axis[column] for _, column in component))
    boundary_sides: list[str] = []
    if any(buy_axis[row] == buy_axis[-1] for row, _ in component):
        boundary_sides.append("buy_max")
    if any(sell_axis[column] == sell_axis[0] for _, column in component):
        boundary_sides.append("sell_min")
    if any(
        buy_axis[row] - sell_axis[column] == min(buy_step, sell_step)
        for row, column in component
    ):
        boundary_sides.append("role_diagonal")
    structural_pass = bool(
        len(component) >= int(minimum_component_cells)
        and buy_max - buy_min >= int(minimum_buy_span)
        and sell_max - sell_min >= int(minimum_sell_span)
        and not boundary_sides
    )
    sharpe = (
        frame.pivot(index="buy_window", columns="sell_window", values="sharpe")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    cagr = (
        frame.pivot(index="buy_window", columns="sell_window", values="cagr_pct")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    orders = (
        frame.pivot(index="buy_window", columns="sell_window", values="order_count")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    centroid = (
        float(np.mean([buy_axis[row] for row, _ in component])),
        float(np.mean([sell_axis[column] for _, column in component])),
    )
    candidates: list[tuple[Any, ...]] = []
    for row, column in component:
        row_start, row_end = row - 1, row + 2
        col_start, col_end = column - 1, column + 2
        if row_start < 0 or col_start < 0 or row_end > values.shape[0] or col_end > values.shape[1]:
            continue
        local_gate = gate[row_start:row_end, col_start:col_end]
        if local_gate.shape != (3, 3) or not local_gate.all():
            continue
        local = values[row_start:row_end, col_start:col_end]
        local_score = float(np.median(local) - 0.5 * np.std(local))
        distance = math.hypot(
            float(buy_axis[row]) - centroid[0],
            float(sell_axis[column]) - centroid[1],
        )
        record = {
            "buy_window": int(buy_axis[row]),
            "sell_window": int(sell_axis[column]),
            "primary_metric": float(values[row, column]),
            "local_score": local_score,
            "local_median": float(np.median(local)),
            "local_std": float(np.std(local)),
            "full_history_sharpe": float(sharpe[row, column]),
            "full_history_cagr_pct": float(cagr[row, column]),
            "order_count": int(orders[row, column]),
            "distance_to_component_centroid": float(distance),
        }
        candidates.append(
            (
                local_score,
                float(sharpe[row, column]),
                float(cagr[row, column]),
                -distance,
                -float(orders[row, column]),
                record,
            )
        )
    representative = max(candidates, key=lambda item: item[:-1])[-1] if candidates else None
    return {
        "metric": metric,
        "top_quantile": float(top_quantile),
        "top_threshold": threshold,
        "role_constraint": "buy_window > sell_window",
        "role_consistent_cell_count": int(role_gate.sum()),
        "event_identifiable_cell_count": int(event_gate.sum()),
        "eligible_cell_count": int(gate.sum()),
        "top_cell_count": int(mask.sum()),
        "component_count": len(components),
        "component_sizes": [len(item) for item in components],
        "largest_component": {
            "cell_count": len(component),
            "buy_min": buy_min,
            "buy_max": buy_max,
            "buy_span": buy_max - buy_min,
            "sell_min": sell_min,
            "sell_max": sell_max,
            "sell_span": sell_max - sell_min,
            "touches_search_boundary": bool(boundary_sides),
            "boundary_sides": boundary_sides,
            "structural_pass": structural_pass,
            "cells": [
                {
                    "buy_window": int(buy_axis[row]),
                    "sell_window": int(sell_axis[column]),
                }
                for row, column in component
            ],
        },
        "representative": representative,
    }


__all__ = [
    "analyze_role_constrained_surface",
    "apply_long_buy_short_sell_gate",
]
