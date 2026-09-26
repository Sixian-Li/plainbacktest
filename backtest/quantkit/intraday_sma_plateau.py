"""Joint-neighborhood selection for the intraday SMA parameter family.

The global search only proposes high-performing anchors.  This module then
perturbs every active parameter together and chooses the interior anchor whose
lower-quartile CAGR and Sharpe remain strongest.  It deliberately does not use
the locked out-of-sample window during selection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
)


FREE_PARAMETER_COLUMNS = (
    "A_negative_days_slow",
    "B_slow_sma_window",
    "E_fallback_sma_window",
    "F_short_sma_center",
    "F_short_sma_spacing",
    "G_short_recovery_below_pct",
    "H_reentry_sma_window",
    "L_cost_stop_pct",
    "R_forced_rebuy_pct",
)


def add_balanced_training_score(results: pd.DataFrame) -> pd.DataFrame:
    """Rank cases by the weaker of their in-sample CAGR and Sharpe ranks."""

    required = {"cagr_pct", "sharpe", *FREE_PARAMETER_COLUMNS}
    missing = required - set(results)
    if missing:
        raise ValueError(f"Missing training result columns: {sorted(missing)}")
    frame = results.copy()
    frame["cagr_percentile"] = frame["cagr_pct"].rank(method="average", pct=True)
    frame["sharpe_percentile"] = frame["sharpe"].rank(method="average", pct=True)
    frame["balanced_training_score"] = frame[
        ["cagr_percentile", "sharpe_percentile"]
    ].min(axis=1)
    return frame


def select_joint_plateau_anchors(results: pd.DataFrame, *, count: int) -> pd.DataFrame:
    """Return deterministic high-performing anchors for joint perturbation."""

    if count < 1:
        raise ValueError("Anchor count must be positive.")
    scored = add_balanced_training_score(results)
    finite = np.isfinite(scored["cagr_pct"]) & np.isfinite(scored["sharpe"])
    scored = scored.loc[finite].sort_values(
        ["balanced_training_score", "cagr_pct", "sharpe"],
        ascending=False,
        kind="stable",
    )
    anchors = scored.head(count).copy().reset_index(drop=True)
    if len(anchors) < count:
        raise RuntimeError(f"Only {len(anchors)} finite anchors available; expected {count}.")
    anchors.insert(0, "anchor_id", [f"ANCHOR_{index:03d}" for index in range(1, count + 1)])
    return anchors


def _local_options(
    search_space: Mapping[str, Sequence[float]],
    parameter: str,
    center: float,
    radius: float,
) -> np.ndarray:
    values = np.asarray(search_space[parameter], dtype=float)
    options = values[(values >= center - radius - 1e-12) & (values <= center + radius + 1e-12)]
    if options.size == 0:
        raise ValueError(f"No local options for {parameter} around {center}.")
    return options


def build_joint_neighborhood_cases(
    search_space: Mapping[str, Sequence[float]],
    anchors: pd.DataFrame,
    *,
    radii: Mapping[str, float],
    cases_per_anchor: int,
    seed: int,
) -> pd.DataFrame:
    """Generate deterministic joint perturbations, including each anchor."""

    if cases_per_anchor < 2:
        raise ValueError("Each joint neighborhood must contain at least two cases.")
    missing_radii = set(FREE_PARAMETER_COLUMNS) - set(radii)
    if missing_radii:
        raise ValueError(f"Missing local radii: {sorted(missing_radii)}")
    rng = np.random.default_rng(seed)
    records: list[dict[str, Any]] = []
    for anchor in anchors.to_dict("records"):
        anchor_id = str(anchor["anchor_id"])
        base = {name: float(anchor[name]) for name in PARAMETER_COLUMNS}
        options = {
            name: _local_options(search_space, name, float(anchor[name]), float(radii[name]))
            for name in FREE_PARAMETER_COLUMNS
        }
        keys: set[tuple[float, ...]] = set()

        def append_case(values: Mapping[str, float]) -> bool:
            key = tuple(float(values[name]) for name in PARAMETER_COLUMNS)
            if key in keys:
                return False
            keys.add(key)
            record: dict[str, Any] = {
                "anchor_id": anchor_id,
                "neighbor_id": f"{anchor_id}_N{len(keys):04d}",
                **{name: float(values[name]) for name in PARAMETER_COLUMNS},
                FORCED_REENTRY_COLUMN: True,
                FAST_DROP_ENABLED_COLUMN: False,
            }
            records.append(record)
            return True

        append_case(base)
        attempts = 0
        while len(keys) < cases_per_anchor and attempts < cases_per_anchor * 100:
            attempts += 1
            candidate = dict(base)
            for name in FREE_PARAMETER_COLUMNS:
                candidate[name] = float(rng.choice(options[name]))
            append_case(candidate)
        if len(keys) != cases_per_anchor:
            raise RuntimeError(
                f"Could only generate {len(keys)} of {cases_per_anchor} neighbors for {anchor_id}."
            )
    return pd.DataFrame.from_records(records)


def summarize_joint_plateaus(
    anchors: pd.DataFrame,
    neighborhood_results: pd.DataFrame,
    *,
    search_space: Mapping[str, Sequence[float]],
) -> pd.DataFrame:
    """Summarize lower-quartile neighborhood quality and chooseable interiors."""

    anchor_lookup = anchors.set_index("anchor_id")
    records: list[dict[str, Any]] = []
    for anchor_id, group in neighborhood_results.groupby("anchor_id", sort=False):
        anchor = anchor_lookup.loc[anchor_id]
        boundary_dimensions: list[str] = []
        for name in FREE_PARAMETER_COLUMNS:
            values = np.asarray(search_space[name], dtype=float)
            value = float(anchor[name])
            if np.isclose(value, values.min()) or np.isclose(value, values.max()):
                boundary_dimensions.append(name)
        records.append(
            {
                "anchor_id": str(anchor_id),
                "neighbor_count": int(len(group)),
                "anchor_cagr_pct": float(anchor["cagr_pct"]),
                "anchor_sharpe": float(anchor["sharpe"]),
                "anchor_max_drawdown_pct": float(anchor["max_drawdown_pct"]),
                "anchor_order_count": int(anchor["order_count"]),
                "neighbor_cagr_q25_pct": float(group["cagr_pct"].quantile(0.25)),
                "neighbor_cagr_median_pct": float(group["cagr_pct"].median()),
                "neighbor_cagr_q75_pct": float(group["cagr_pct"].quantile(0.75)),
                "neighbor_sharpe_q25": float(group["sharpe"].quantile(0.25)),
                "neighbor_sharpe_median": float(group["sharpe"].median()),
                "neighbor_sharpe_q75": float(group["sharpe"].quantile(0.75)),
                "neighbor_cagr_iqr_pct_points": float(
                    group["cagr_pct"].quantile(0.75) - group["cagr_pct"].quantile(0.25)
                ),
                "neighbor_sharpe_iqr": float(
                    group["sharpe"].quantile(0.75) - group["sharpe"].quantile(0.25)
                ),
                "boundary_dimension_count": len(boundary_dimensions),
                "boundary_dimensions": "|".join(boundary_dimensions),
            }
        )
    summary = pd.DataFrame.from_records(records)
    summary["q25_cagr_percentile"] = summary["neighbor_cagr_q25_pct"].rank(
        method="average", pct=True
    )
    summary["q25_sharpe_percentile"] = summary["neighbor_sharpe_q25"].rank(
        method="average", pct=True
    )
    summary["joint_plateau_score"] = summary[
        ["q25_cagr_percentile", "q25_sharpe_percentile"]
    ].min(axis=1)
    return summary.sort_values(
        [
            "boundary_dimension_count",
            "joint_plateau_score",
            "neighbor_cagr_q25_pct",
            "neighbor_sharpe_q25",
            "neighbor_cagr_iqr_pct_points",
            "neighbor_sharpe_iqr",
        ],
        ascending=[True, False, False, False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def select_plateau_representative(
    anchors: pd.DataFrame,
    plateau_summary: pd.DataFrame,
) -> pd.Series:
    """Select the best interior joint plateau, falling back transparently."""

    if plateau_summary.empty:
        raise ValueError("No plateau summaries are available.")
    interior = plateau_summary[plateau_summary["boundary_dimension_count"] == 0]
    selected_summary = (interior if not interior.empty else plateau_summary).iloc[0]
    selected = anchors[anchors["anchor_id"] == selected_summary["anchor_id"]]
    if len(selected) != 1:
        raise AssertionError("Selected plateau anchor is not unique.")
    result = selected.iloc[0].copy()
    result["selection_reason"] = "JOINT_PLATEAU_REPRESENTATIVE"
    result["joint_plateau_score"] = float(selected_summary["joint_plateau_score"])
    result["neighbor_cagr_q25_pct"] = float(selected_summary["neighbor_cagr_q25_pct"])
    result["neighbor_sharpe_q25"] = float(selected_summary["neighbor_sharpe_q25"])
    result["boundary_dimension_count"] = int(selected_summary["boundary_dimension_count"])
    result["boundary_dimensions"] = str(selected_summary["boundary_dimensions"])
    return result


def local_parameter_band(
    selected_anchor: pd.Series,
    neighborhood_results: pd.DataFrame,
    *,
    cagr_tolerance_pct_points: float,
    sharpe_tolerance: float,
) -> pd.DataFrame:
    """Describe nearby cases that remain close to the selected anchor metrics."""

    group = neighborhood_results[
        neighborhood_results["anchor_id"] == selected_anchor["anchor_id"]
    ].copy()
    qualified = group[
        (group["cagr_pct"] >= float(selected_anchor["cagr_pct"]) - cagr_tolerance_pct_points)
        & (group["sharpe"] >= float(selected_anchor["sharpe"]) - sharpe_tolerance)
    ]
    records = []
    for name in FREE_PARAMETER_COLUMNS:
        series = qualified[name].astype(float)
        records.append(
            {
                "parameter": name,
                "qualified_case_count": int(len(qualified)),
                "min": float(series.min()) if len(series) else np.nan,
                "q25": float(series.quantile(0.25)) if len(series) else np.nan,
                "median": float(series.median()) if len(series) else np.nan,
                "q75": float(series.quantile(0.75)) if len(series) else np.nan,
                "max": float(series.max()) if len(series) else np.nan,
            }
        )
    return pd.DataFrame.from_records(records)
