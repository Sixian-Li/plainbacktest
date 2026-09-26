"""Reusable cross-window parameter stability analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd


OBJECTIVES = ("cagr_pct", "sharpe")


def add_window_ranks(results: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic rank and percentile columns inside each window."""

    ranked = results.copy()
    for objective in OBJECTIVES:
        rank_column = f"{objective}_rank"
        percentile_column = f"{objective}_rank_percentile"
        ranked[rank_column] = ranked.groupby("window_id")[objective].rank(
            method="min", ascending=False
        )
        sizes = ranked.groupby("window_id")[objective].transform("count")
        denominator = (sizes - 1).clip(lower=1)
        ranked[percentile_column] = (sizes - ranked[rank_column]) / denominator * 100.0
    return ranked


def select_representatives(
    results: pd.DataFrame,
    *,
    parameter_columns: Sequence[str],
    window_order: Sequence[str],
    anchor: Mapping[str, Any],
) -> pd.DataFrame:
    """Select each window's CAGR/Sharpe winner plus one declared anchor."""

    selections: list[dict[str, Any]] = []
    reasons_by_key: dict[tuple[Any, ...], list[str]] = {}
    row_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for window_id in window_order:
        window = results[results["window_id"] == window_id]
        if window.empty:
            raise ValueError(f"Window has no screening results: {window_id}")
        for objective in OBJECTIVES:
            row = window.loc[window[objective].idxmax()].to_dict()
            key = tuple(row[name] for name in parameter_columns)
            reasons_by_key.setdefault(key, []).append(f"{window_id}:{objective}")
            row_by_key.setdefault(key, row)
    anchor_key = tuple(anchor[name] for name in parameter_columns)
    reasons_by_key.setdefault(anchor_key, []).append("full_history_global_best_anchor")
    if anchor_key not in row_by_key:
        row_by_key[anchor_key] = dict(anchor)
    for index, (key, reasons) in enumerate(reasons_by_key.items(), start=1):
        source = row_by_key[key]
        selections.append(
            {
                "representative_id": f"REP_{index:02d}",
                "selection_reason": "|".join(reasons),
                **{name: source[name] for name in parameter_columns},
            }
        )
    return pd.DataFrame(selections)


def top_fraction_summary(
    results: pd.DataFrame,
    *,
    parameter_columns: Sequence[str],
    fraction: float = 0.01,
) -> pd.DataFrame:
    """Summarize parameter distributions among each objective's top fraction."""

    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1].")
    records: list[dict[str, Any]] = []
    for window_id, window in results.groupby("window_id", sort=False):
        count = max(int(np.ceil(len(window) * fraction)), 1)
        for objective in OBJECTIVES:
            top = window.nlargest(count, objective)
            for parameter in parameter_columns:
                numeric = top[parameter].astype(float)
                modes = numeric.mode().sort_values()
                records.append(
                    {
                        "window_id": window_id,
                        "objective": objective,
                        "top_fraction": fraction,
                        "top_case_count": len(top),
                        "parameter": parameter,
                        "q25": float(numeric.quantile(0.25)),
                        "median": float(numeric.median()),
                        "q75": float(numeric.quantile(0.75)),
                        "mode": float(modes.iloc[0]),
                        "mean": float(numeric.mean()),
                    }
                )
    return pd.DataFrame(records)
