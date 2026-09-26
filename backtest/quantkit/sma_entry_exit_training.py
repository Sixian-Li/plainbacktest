"""Fine-grained parameter screening for the R1+R3 SMA strategy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from quantkit.metrics import calculate_metrics
from quantkit.sma_entry_exit_ablation import (
    EntryExitAblationSpec,
    analysis_slice,
    prepare_entry_exit_ablation_data,
    run_reference_entry_exit_ablation,
)


PARAMETER_COLUMNS = (
    "short_center",
    "short_spacing",
    "long_window",
    "buy_short_buffer_pct",
    "buy_long_buffer_pct",
    "r1_decline_days",
    "r1_min_daily_decline_pct",
    "r3_sell_buffer_pct",
)


def parameter_key(parameters: Mapping[str, Any]) -> tuple[float, ...]:
    return tuple(float(parameters[name]) for name in PARAMETER_COLUMNS)


def build_staged_cases(config: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a de-duplicated baseline plus one-at-a-time fine search."""
    baseline = dict(config["baseline"])
    missing = set(PARAMETER_COLUMNS) - set(baseline)
    if missing:
        raise ValueError(f"Baseline misses parameters: {sorted(missing)}")
    case_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    key_to_case: dict[tuple[float, ...], str] = {}
    for sweep_order, sweep in enumerate(config["stage_1_oat_sweeps"], start=1):
        parameter = str(sweep["parameter"])
        if parameter not in PARAMETER_COLUMNS:
            raise ValueError(f"Unknown training parameter: {parameter}")
        values = list(sweep["values"])
        if baseline[parameter] not in values:
            raise ValueError(f"Sweep {parameter} omits baseline {baseline[parameter]}")
        for point_order, value in enumerate(values, start=1):
            parameters = dict(baseline)
            parameters[parameter] = value
            key = parameter_key(parameters)
            case_id = key_to_case.get(key)
            if case_id is None:
                case_id = f"S1_{len(key_to_case) + 1:04d}"
                key_to_case[key] = case_id
                case_rows.append({"case_id": case_id, **parameters})
            point_rows.append(
                {
                    "sweep_order": sweep_order,
                    "sweep_id": str(sweep["sweep_id"]),
                    "parameter": parameter,
                    "parameter_label": str(sweep["label"]),
                    "point_order": point_order,
                    "value": value,
                    "is_baseline": value == baseline[parameter],
                    "case_id": case_id,
                }
            )
    return pd.DataFrame(case_rows), pd.DataFrame(point_rows)


def add_cases(
    existing: pd.DataFrame,
    candidates: Iterable[Mapping[str, Any]],
    *,
    prefix: str,
) -> pd.DataFrame:
    """Append unique candidates while preserving deterministic identifiers."""
    rows = existing.to_dict("records")
    keys = {parameter_key(row) for row in rows}
    added = 0
    for candidate in candidates:
        key = parameter_key(candidate)
        if key in keys:
            continue
        added += 1
        rows.append({"case_id": f"{prefix}_{added:05d}", **candidate})
        keys.add(key)
    return pd.DataFrame(rows)


def run_training_case(
    raw: pd.DataFrame,
    parameters: Mapping[str, Any],
    *,
    start: str,
    end: str,
    initial_cash: float,
    case_id: str,
) -> tuple[dict[str, Any], Any, pd.DataFrame]:
    center = int(parameters["short_center"])
    spacing = int(parameters["short_spacing"])
    prepared = prepare_entry_exit_ablation_data(
        raw,
        short_windows=(center - spacing, center, center + spacing),
        long_window=int(parameters["long_window"]),
        buy_short_buffer_pct=float(parameters["buy_short_buffer_pct"]),
        buy_long_buffer_pct=float(parameters["buy_long_buffer_pct"]),
        r1_decline_days=int(parameters["r1_decline_days"]),
        r1_min_daily_decline_pct=float(parameters["r1_min_daily_decline_pct"]),
        r3_sell_buffer_pct=float(parameters["r3_sell_buffer_pct"]),
    )
    window = analysis_slice(prepared, start=start, end=end)
    spec = EntryExitAblationSpec(True, False, True, False)
    reference = run_reference_entry_exit_ablation(
        window, spec, initial_cash=initial_cash, case_id=case_id
    )
    metrics = calculate_metrics(
        reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
    )
    return metrics, reference, prepared


def drawdown_guard(results: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    baseline = results[results["is_baseline"].astype(bool)]
    if baseline.empty:
        raise ValueError("Training results do not identify a baseline case.")
    baseline = baseline.iloc[0]
    return_guard = float(baseline["total_return_pct"]) * float(
        config["minimum_return_fraction_of_baseline"]
    )
    return (
        results["total_return_pct"].astype(float).ge(return_guard)
        & results["order_count"].astype(int).le(int(config["maximum_orders"]))
        & results["closed_trade_count"].astype(int).ge(int(config["minimum_closed_trades"]))
    )


def select_representative(
    results: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    parameter_columns: Sequence[str] = PARAMETER_COLUMNS,
) -> pd.Series:
    """Select the median point of the drawdown plateau after return/activity gates."""
    candidates = results[results["passes_guard"].astype(bool)].copy()
    if candidates.empty:
        raise ValueError("No case passes the predeclared training guard.")
    best_drawdown = float(candidates["max_drawdown_pct"].max())
    candidates = candidates[
        candidates["max_drawdown_pct"].astype(float).ge(
            best_drawdown - float(config["plateau_drawdown_tolerance_pct_points"])
        )
    ].copy()
    best_return = float(candidates["total_return_pct"].max())
    candidates = candidates[
        candidates["total_return_pct"].astype(float).ge(
            best_return - float(config["plateau_return_tolerance_pct_points"])
        )
    ].copy()
    for column in parameter_columns:
        median = float(candidates[column].astype(float).median())
        candidates[f"distance_{column}"] = np.abs(candidates[column].astype(float) - median)
    distance_columns = [f"distance_{column}" for column in parameter_columns]
    candidates["plateau_center_distance"] = candidates[distance_columns].sum(axis=1)
    return candidates.sort_values(
        ["plateau_center_distance", "max_drawdown_pct", "total_return_pct", "sharpe", "case_id"],
        ascending=[True, False, False, False, True],
    ).iloc[0]
