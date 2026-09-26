"""Deterministic one-at-a-time parameter sensitivity helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
)


IDENTITY_COLUMNS = (*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN)
OPTIONAL_IDENTITY_COLUMNS = (FAST_DROP_ENABLED_COLUMN,)


def identity_columns(baseline: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the strategy identity carried by this baseline.

    Optional semantic switches are included only when explicitly frozen in a
    newer experiment. This keeps older OAT definitions byte-for-byte
    compatible while preventing a disabled rule from being silently enabled
    by the compiled screening ledger.
    """

    return (*IDENTITY_COLUMNS, *(name for name in OPTIONAL_IDENTITY_COLUMNS if name in baseline))


def sweep_values(sweep: Mapping[str, Any]) -> list[Any]:
    """Return explicit or inclusive decimal range values for one OAT sweep."""

    if "values" in sweep:
        values = list(sweep["values"])
        if not values:
            raise ValueError("OAT sweep values are empty")
        return values
    required = {"start", "stop", "step"}
    missing = required - set(sweep)
    if missing:
        raise ValueError(f"OAT sweep misses range fields: {sorted(missing)}")
    start = Decimal(str(sweep["start"]))
    stop = Decimal(str(sweep["stop"]))
    step = Decimal(str(sweep["step"]))
    if step <= 0 or stop < start:
        raise ValueError("OAT sweep requires positive step and stop >= start")
    quotient = (stop - start) / step
    if quotient != quotient.to_integral_value():
        raise ValueError("OAT sweep range must end exactly on an inclusive step")
    return [float(start + step * index) for index in range(int(quotient) + 1)]


def metric_winner(group: pd.DataFrame, metric: str) -> pd.Series:
    """Choose a deterministic maximum, preferring the frozen baseline on ties."""

    valid = group.dropna(subset=[metric])
    if valid.empty:
        raise ValueError(f"No finite values for {metric}")
    maximum = float(valid[metric].max())
    tolerance = 1e-12 * max(1.0, abs(maximum))
    winners = valid[np.isclose(valid[metric].astype(float), maximum, rtol=0, atol=tolerance)]
    baseline = winners[winners["is_baseline"].astype(bool)]
    return (baseline if not baseline.empty else winners).sort_values("point_order").iloc[0]


def build_oat_cases(
    baseline: Mapping[str, float | bool],
    sweeps: Sequence[Mapping[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return de-duplicated cases and a complete sweep-point mapping.

    Every sweep changes exactly one declared parameter while all other values
    remain at the frozen baseline. Baseline points intentionally appear once
    in every sweep mapping but only once in the executable case universe.
    """

    active_identity = identity_columns(baseline)
    missing = set(IDENTITY_COLUMNS) - set(baseline)
    if missing:
        raise ValueError(f"Baseline misses parameters: {sorted(missing)}")
    case_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    key_to_case: dict[tuple[Any, ...], str] = {}
    for order, sweep in enumerate(sweeps, start=1):
        parameter = str(sweep["parameter"])
        if parameter not in active_identity:
            raise ValueError(f"Unknown OAT parameter: {parameter}")
        values = sweep_values(sweep)
        baseline_value = baseline[parameter]
        if baseline_value not in values:
            raise ValueError(f"OAT sweep {parameter} omits baseline {baseline_value}")
        for point_order, value in enumerate(values, start=1):
            row = dict(baseline)
            row[parameter] = value
            key = tuple(row[name] for name in active_identity)
            case_id = key_to_case.get(key)
            if case_id is None:
                case_id = f"OAT_{len(key_to_case) + 1:03d}"
                key_to_case[key] = case_id
                case_rows.append({"case_id": case_id, **row})
            point_rows.append(
                {
                    "sweep_order": order,
                    "sweep_id": str(sweep.get("sweep_id", parameter)),
                    "parameter": parameter,
                    "parameter_label": str(sweep.get("label", parameter)),
                    "point_order": point_order,
                    "value": value,
                    "baseline_value": baseline_value,
                    "is_baseline": value == baseline_value,
                    "case_id": case_id,
                }
            )
    cases = pd.DataFrame(case_rows)
    points = pd.DataFrame(point_rows)
    return cases, points


def baseline_plateau_diagnostics(
    sensitivity: pd.DataFrame,
    *,
    cagr_tolerance_pct_points: float = 0.50,
    sharpe_tolerance: float = 0.03,
    reference_mode: str = "baseline",
) -> pd.DataFrame:
    """Measure the connected two-metric neighborhood around each baseline.

    A point belongs to the joint band when neither CAGR nor Sharpe falls more
    than the declared absolute tolerance below the selected reference. The
    default reference is the frozen baseline for backward compatibility;
    ``curve_maximum`` uses each metric's (possibly different) curve maximum.
    The reported interval is the connected component containing the baseline,
    so a distant second peak cannot make a narrow local spike look robust.
    """

    if reference_mode not in {"baseline", "curve_maximum"}:
        raise ValueError("reference_mode must be 'baseline' or 'curve_maximum'")

    records: list[dict[str, Any]] = []
    for (window_id, sweep_id), raw_group in sensitivity.groupby(
        ["window_id", "sweep_id"], sort=False
    ):
        group = raw_group.sort_values("point_order").reset_index(drop=True)
        baseline_indices = group.index[group["is_baseline"].astype(bool)].tolist()
        if len(baseline_indices) != 1:
            raise ValueError(f"{window_id}/{sweep_id} must have exactly one baseline point")
        baseline_index = baseline_indices[0]
        baseline = group.iloc[baseline_index]
        reference_cagr = (
            float(baseline["cagr_pct"])
            if reference_mode == "baseline"
            else float(group["cagr_pct"].max())
        )
        reference_sharpe = (
            float(baseline["sharpe"])
            if reference_mode == "baseline"
            else float(group["sharpe"].max())
        )
        qualifies = (
            group["cagr_pct"].astype(float)
            >= reference_cagr - float(cagr_tolerance_pct_points)
        ) & (
            group["sharpe"].astype(float)
            >= reference_sharpe - float(sharpe_tolerance)
        )
        baseline_qualifies = bool(qualifies.iloc[baseline_index])
        if baseline_qualifies:
            left = baseline_index
            right = baseline_index
            while left > 0 and bool(qualifies.iloc[left - 1]):
                left -= 1
            while right + 1 < len(group) and bool(qualifies.iloc[right + 1]):
                right += 1
            component = group.iloc[left : right + 1]
        else:
            component = group.iloc[0:0]
        has_lower = bool((component["value"].astype(float) < float(baseline["value"])).any())
        has_higher = bool((component["value"].astype(float) > float(baseline["value"])).any())
        point_count = len(component)
        if not baseline_qualifies:
            classification = "baseline_outside_joint_band"
        elif point_count >= 3 and has_lower and has_higher:
            classification = "two_sided_plateau"
        elif point_count >= 3:
            classification = "one_sided_plateau"
        else:
            classification = "narrow_or_spike"
        records.append(
            {
                "window_id": window_id,
                "sweep_id": sweep_id,
                "parameter": group.iloc[0]["parameter"],
                "parameter_label": group.iloc[0]["parameter_label"],
                "baseline_value": baseline["value"],
                "baseline_cagr_pct": float(baseline["cagr_pct"]),
                "baseline_sharpe": float(baseline["sharpe"]),
                "plateau_min_value": component["value"].iloc[0] if point_count else None,
                "plateau_max_value": component["value"].iloc[-1] if point_count else None,
                "plateau_point_count": point_count,
                "has_lower_neighbor": has_lower,
                "has_higher_neighbor": has_higher,
                "classification": classification,
                "baseline_qualifies": baseline_qualifies,
                "reference_mode": reference_mode,
                "reference_cagr_pct": reference_cagr,
                "reference_sharpe": reference_sharpe,
                "cagr_tolerance_pct_points": float(cagr_tolerance_pct_points),
                "sharpe_tolerance": float(sharpe_tolerance),
            }
        )
    return pd.DataFrame(records)


def select_plateau_endpoint_cases(plateau: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Select connected plateau endpoints for independent-ledger verification."""

    selected: list[dict[str, Any]] = []
    for row in plateau.itertuples(index=False):
        if not bool(row.baseline_qualifies):
            continue
        sweep = points[points["sweep_id"].eq(row.sweep_id)]
        for label, value in (("minimum", row.plateau_min_value), ("maximum", row.plateau_max_value)):
            matches = sweep[np.isclose(sweep["value"].astype(float), float(value), rtol=0, atol=1e-12)]
            if len(matches) != 1:
                raise ValueError(f"Could not resolve {row.sweep_id} plateau {label} endpoint")
            selected.append(
                {
                    "window_id": row.window_id,
                    "case_id": matches.iloc[0]["case_id"],
                    "selection_reason": f"{row.sweep_id}:plateau_{label}",
                }
            )
    if not selected:
        return pd.DataFrame(columns=["window_id", "case_id", "selection_reason"])
    frame = pd.DataFrame(selected)
    return (
        frame.groupby(["window_id", "case_id"], sort=False)["selection_reason"]
        .agg(lambda values: "|".join(dict.fromkeys(values)))
        .reset_index()
    )


def cross_window_plateau_intersections(plateau: pd.DataFrame) -> pd.DataFrame:
    """Return the shared baseline-connected plateau across configured windows."""

    records: list[dict[str, Any]] = []
    for sweep_id, group in plateau.groupby("sweep_id", sort=False):
        all_qualify = bool(group["baseline_qualifies"].astype(bool).all())
        minimum = (
            float(group["plateau_min_value"].astype(float).max()) if all_qualify else None
        )
        maximum = (
            float(group["plateau_max_value"].astype(float).min()) if all_qualify else None
        )
        has_intersection = bool(all_qualify and minimum is not None and minimum <= maximum)
        records.append(
            {
                "sweep_id": sweep_id,
                "parameter": group.iloc[0]["parameter"],
                "parameter_label": group.iloc[0]["parameter_label"],
                "baseline_value": group.iloc[0]["baseline_value"],
                "window_count": len(group),
                "all_windows_baseline_qualifies": all_qualify,
                "intersection_min_value": minimum if has_intersection else None,
                "intersection_max_value": maximum if has_intersection else None,
                "has_intersection": has_intersection,
            }
        )
    return pd.DataFrame(records)


def select_formal_oat_cases(results: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Predeclared formal set: baseline plus every curve's CAGR/Sharpe maximum."""

    expanded = points.merge(results, on="case_id", how="left", validate="many_to_one")
    selected: list[dict[str, Any]] = []
    for row in expanded[expanded["is_baseline"]].itertuples(index=False):
        selected.append({"case_id": row.case_id, "selection_reason": "baseline"})
    for sweep_id, group in expanded.groupby("sweep_id", sort=False):
        for metric in ("cagr_pct", "sharpe"):
            if group.dropna(subset=[metric]).empty:
                continue
            winner = metric_winner(group, metric)
            selected.append(
                {
                    "case_id": winner["case_id"],
                    "selection_reason": f"{sweep_id}:{metric}_maximum",
                }
            )
    selection = pd.DataFrame(selected)
    return (
        selection.groupby("case_id", sort=False)["selection_reason"]
        .agg(lambda values: "|".join(dict.fromkeys(values)))
        .reset_index()
    )
