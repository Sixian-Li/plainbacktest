"""Stable-neighborhood selection helpers for a broad parameter training search."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from quantkit.intraday_sma_search import PARAMETER_COLUMNS
from quantkit.oat_sensitivity import sweep_values


def expand_search_space(definition: Mapping[str, Mapping[str, Any]]) -> dict[str, list[float]]:
    """Expand compact inclusive range definitions into an ordered discrete grid."""

    missing = set(PARAMETER_COLUMNS) - set(definition)
    if missing:
        raise ValueError(f"Search-space definition misses: {sorted(missing)}")
    return {
        name: [float(value) for value in sweep_values(definition[name])]
        for name in PARAMETER_COLUMNS
    }


def select_stability_parents(
    results: pd.DataFrame,
    *,
    per_objective: int,
) -> pd.DataFrame:
    """Keep diverse high performers before expensive local-neighborhood scoring."""

    required = {
        "screen_case_id",
        "cagr_pct",
        "sharpe",
        "delta_cagr_vs_buy_hold_pct_points",
        "delta_sharpe_vs_buy_hold",
        *PARAMETER_COLUMNS,
    }
    missing = required - set(results)
    if missing:
        raise ValueError(f"Screening results miss: {sorted(missing)}")
    valid = results.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["cagr_pct", "sharpe"]
    ).copy()
    if valid.empty:
        raise ValueError("No finite screening result can seed stability analysis.")
    valid["cagr_percentile"] = valid["cagr_pct"].rank(method="average", pct=True)
    valid["sharpe_percentile"] = valid["sharpe"].rank(method="average", pct=True)
    valid["joint_percentile"] = valid[["cagr_percentile", "sharpe_percentile"]].min(axis=1)
    objectives = (
        "cagr_pct",
        "sharpe",
        "delta_cagr_vs_buy_hold_pct_points",
        "delta_sharpe_vs_buy_hold",
        "joint_percentile",
    )
    selected = pd.concat(
        [valid.nlargest(per_objective, objective) for objective in objectives],
        ignore_index=True,
    ).drop_duplicates(subset=list(PARAMETER_COLUMNS), ignore_index=True)
    selected.insert(0, "parent_id", [f"PARENT_{index:04d}" for index in range(1, len(selected) + 1)])
    return selected


def build_one_step_neighborhoods(
    parents: pd.DataFrame,
    search_space: Mapping[str, list[float]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build each parent's center plus every available lower/upper grid neighbor."""

    rows: list[dict[str, Any]] = []
    for parent in parents.to_dict("records"):
        parent_id = str(parent["parent_id"])
        center = {name: float(parent[name]) for name in PARAMETER_COLUMNS}
        rows.append(
            {
                "parent_id": parent_id,
                "changed_parameter": "__center__",
                "direction": "center",
                **center,
            }
        )
        for name in PARAMETER_COLUMNS:
            values = np.asarray(search_space[name], dtype=float)
            matches = np.flatnonzero(np.isclose(values, center[name], rtol=0, atol=1e-12))
            if len(matches) != 1:
                raise ValueError(f"Parent {parent_id} has off-grid {name}={center[name]}")
            location = int(matches[0])
            for direction, neighbor_index in (("lower", location - 1), ("upper", location + 1)):
                if 0 <= neighbor_index < len(values):
                    neighbor = dict(center)
                    neighbor[name] = float(values[neighbor_index])
                    rows.append(
                        {
                            "parent_id": parent_id,
                            "changed_parameter": name,
                            "direction": direction,
                            **neighbor,
                        }
                    )
    mapping = pd.DataFrame(rows)
    cases = mapping[list(PARAMETER_COLUMNS)].drop_duplicates(ignore_index=True)
    cases.insert(0, "neighbor_case_id", [f"NEIGHBOR_{index:05d}" for index in range(1, len(cases) + 1)])
    mapping = mapping.merge(cases, on=list(PARAMETER_COLUMNS), how="left", validate="many_to_one")
    return cases, mapping


def summarize_neighborhoods(
    parents: pd.DataFrame,
    mapping: pd.DataFrame,
    neighbor_results: pd.DataFrame,
) -> pd.DataFrame:
    """Score local smoothness from one-grid-step perturbations around each parent."""

    merged = mapping.merge(
        neighbor_results,
        on=["neighbor_case_id", *PARAMETER_COLUMNS],
        how="left",
        validate="many_to_one",
    )
    records: list[dict[str, Any]] = []
    parent_lookup = parents.set_index("parent_id")
    for parent_id, group in merged.groupby("parent_id", sort=False):
        finite = group.replace([np.inf, -np.inf], np.nan).dropna(subset=["cagr_pct", "sharpe"])
        if finite.empty:
            continue
        center_rows = finite[finite["direction"] == "center"]
        if len(center_rows) != 1:
            raise ValueError(f"{parent_id} does not have one finite center row.")
        center = parent_lookup.loc[parent_id]
        directions = group[group["changed_parameter"] != "__center__"].groupby(
            "changed_parameter"
        )["direction"].nunique()
        records.append(
            {
                "parent_id": parent_id,
                "screen_case_id": str(center["screen_case_id"]),
                **{name: float(center[name]) for name in PARAMETER_COLUMNS},
                "neighbor_count": int(len(group)),
                "finite_neighbor_count": int(len(finite)),
                "one_sided_dimension_count": int((directions < 2).sum()),
                "center_cagr_pct": float(center_rows.iloc[0]["cagr_pct"]),
                "center_sharpe": float(center_rows.iloc[0]["sharpe"]),
                "neighbor_cagr_q25_pct": float(finite["cagr_pct"].quantile(0.25)),
                "neighbor_cagr_median_pct": float(finite["cagr_pct"].median()),
                "neighbor_cagr_min_pct": float(finite["cagr_pct"].min()),
                "neighbor_sharpe_q25": float(finite["sharpe"].quantile(0.25)),
                "neighbor_sharpe_median": float(finite["sharpe"].median()),
                "neighbor_sharpe_min": float(finite["sharpe"].min()),
            }
        )
    summary = pd.DataFrame(records)
    if summary.empty:
        raise ValueError("No finite neighborhood summaries were produced.")
    summary["q25_cagr_percentile"] = summary["neighbor_cagr_q25_pct"].rank(
        method="average", pct=True
    )
    summary["q25_sharpe_percentile"] = summary["neighbor_sharpe_q25"].rank(
        method="average", pct=True
    )
    summary["min_cagr_percentile"] = summary["neighbor_cagr_min_pct"].rank(
        method="average", pct=True
    )
    summary["min_sharpe_percentile"] = summary["neighbor_sharpe_min"].rank(
        method="average", pct=True
    )
    summary["robust_joint_score"] = summary[
        ["min_cagr_percentile", "min_sharpe_percentile"]
    ].min(axis=1)
    summary["robust_rank_sum"] = summary[
        ["min_cagr_percentile", "min_sharpe_percentile"]
    ].sum(axis=1)
    return summary.sort_values(
        [
            "robust_joint_score",
            "robust_rank_sum",
            "neighbor_cagr_min_pct",
            "neighbor_sharpe_min",
            "center_cagr_pct",
            "parent_id",
        ],
        ascending=[False, False, False, False, False, True],
        ignore_index=True,
    )


def select_stable_representative(summary: pd.DataFrame) -> pd.Series:
    """Return the best fully finite, two-sided worst-neighbor representative."""

    if summary.empty:
        raise ValueError("Neighborhood summary is empty.")
    eligible = summary[
        (summary["one_sided_dimension_count"] == 0)
        & (summary["finite_neighbor_count"] == summary["neighbor_count"])
    ]
    if eligible.empty:
        raise ValueError("No complete two-sided neighborhood can represent a smooth region.")
    return eligible.iloc[0]
