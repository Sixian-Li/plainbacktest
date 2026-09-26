#!/usr/bin/env python3
"""Train a joint QQQ parameter plateau without C/D, then run one locked OOS test."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.intraday_sma import SELL_FAST_DROP
from quantkit.intraday_sma_plateau import (
    FREE_PARAMETER_COLUMNS,
    build_joint_neighborhood_cases,
    local_parameter_band,
    select_joint_plateau_anchors,
    select_plateau_representative,
    summarize_joint_plateaus,
)
from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
    sample_global_cases,
    sample_refined_cases,
    screen_cases,
)
from quantkit.oat_sensitivity import sweep_values
from quantkit.paths import BACKTEST_ROOT
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_intraday_sma_global_search import formal_case


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos"
)


def build_search_space(parameters: dict[str, Any]) -> dict[str, list[float]]:
    ranges = parameters["search_ranges"]
    space = {name: sweep_values(ranges[name]) for name in FREE_PARAMETER_COLUMNS}
    placeholders = parameters["legacy_disabled_rule_placeholders"]
    space["C_fast_derivative_pct"] = [float(placeholders["C_fast_derivative_pct"])]
    space["D_negative_days_fast"] = [float(placeholders["D_negative_days_fast"])]
    return {name: space[name] for name in PARAMETER_COLUMNS}


def anchor_rows(parameters: dict[str, Any]) -> list[dict[str, float]]:
    placeholders = parameters["legacy_disabled_rule_placeholders"]
    rows = []
    for source in parameters.get("search_anchors", []):
        row = {name: float(source[name]) for name in FREE_PARAMETER_COLUMNS}
        row["C_fast_derivative_pct"] = float(placeholders["C_fast_derivative_pct"])
        row["D_negative_days_fast"] = float(placeholders["D_negative_days_fast"])
        rows.append({name: row[name] for name in PARAMETER_COLUMNS})
    return rows


def enable_modes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result[FORCED_REENTRY_COLUMN] = True
    result[FAST_DROP_ENABLED_COLUMN] = False
    return result


def identity(row: pd.Series) -> tuple[float | bool, ...]:
    return tuple(float(row[name]) for name in PARAMETER_COLUMNS) + (
        bool(row[FORCED_REENTRY_COLUMN]),
        bool(row[FAST_DROP_ENABLED_COLUMN]),
    )


def formal_training_candidates(
    screening: pd.DataFrame,
    selected: pd.Series,
) -> pd.DataFrame:
    proposals = [
        (selected, "JOINT_PLATEAU_REPRESENTATIVE"),
        (screening.loc[screening["cagr_pct"].idxmax()], "TRAIN_MAX_CAGR"),
        (screening.loc[screening["sharpe"].idxmax()], "TRAIN_MAX_SHARPE"),
    ]
    grouped: dict[tuple[float | bool, ...], dict[str, Any]] = {}
    for row, reason in proposals:
        key = identity(row)
        if key not in grouped:
            grouped[key] = {"row": row.copy(), "reasons": []}
        grouped[key]["reasons"].append(reason)
    records = []
    for index, item in enumerate(grouped.values(), start=1):
        row = item["row"].to_dict()
        row["case_id"] = f"TRAIN_{index:02d}"
        row["selection_reason"] = "|".join(item["reasons"])
        records.append(row)
    return pd.DataFrame.from_records(records)


def with_window(frames: dict[str, pd.DataFrame], window_id: str) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for name, frame in frames.items():
        copy = frame.copy()
        copy.insert(0, "window_id", window_id)
        result[name] = copy
    return result


def active_parameters(row: pd.Series | dict[str, Any]) -> dict[str, float | int]:
    return {
        "A": int(row["A_negative_days_slow"]),
        "B": int(row["B_slow_sma_window"]),
        "E": int(row["E_fallback_sma_window"]),
        "F_center": int(row["F_short_sma_center"]),
        "F_spacing": int(row["F_short_sma_spacing"]),
        "G_pct": float(row["G_short_recovery_below_pct"]),
        "H": int(row["H_reentry_sma_window"]),
        "L_pct": float(row["L_cost_stop_pct"]),
        "R_pct": float(row["R_forced_rebuy_pct"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or args.symbol not in context.config["symbols"]:
        raise ValueError("This experiment is frozen to QQQ.")
    if float(args.cost_bps) != 0.0:
        raise ValueError("This first train/OOS experiment is frozen to 0 bps.")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ"
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"QQQ is not approved: {dataset_manifest['effective_status']}")

    config_parameters = context.config["parameters"]
    search_space = build_search_space(config_parameters)
    train_start = pd.Timestamp(config_parameters["train_start"])
    train_end = pd.Timestamp(config_parameters["train_end"])
    test_start = pd.Timestamp(config_parameters["locked_test_start"])
    test_end = pd.Timestamp(config_parameters["locked_test_end"])
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)
    train_analysis = raw[(raw["date"] >= train_start) & (raw["date"] <= train_end)].reset_index(drop=True)
    test_analysis = raw[(raw["date"] >= test_start) & (raw["date"] <= test_end)].reset_index(drop=True)
    if train_analysis.empty or test_analysis.empty:
        raise RuntimeError("Frozen training or locked-test window is empty.")

    started = time.perf_counter()
    global_cases = enable_modes(
        sample_global_cases(
            search_space,
            count=int(config_parameters["global_sample_count"]),
            seed=int(config_parameters["random_seed"]),
            anchors=anchor_rows(config_parameters),
        )
    )
    global_results = screen_cases(
        raw,
        global_cases,
        analysis_start=train_start,
        analysis_end=train_end,
        initial_cash=initial_cash,
        evaluation_start="analysis_start",
    )
    global_results.insert(0, "search_stage", "global")

    refinement_parents = select_joint_plateau_anchors(
        global_results,
        count=int(config_parameters["refinement_parent_count"]),
    )
    refined_cases = enable_modes(
        sample_refined_cases(
            search_space,
            refinement_parents,
            global_results,
            count=int(config_parameters["refined_sample_count"]),
            seed=int(config_parameters["random_seed"]) + 1,
        )
    )
    refined_results = screen_cases(
        raw,
        refined_cases,
        analysis_start=train_start,
        analysis_end=train_end,
        initial_cash=initial_cash,
        evaluation_start="analysis_start",
    )
    refined_results.insert(0, "search_stage", "refined")
    screening = pd.concat((global_results, refined_results), ignore_index=True)
    screening.insert(
        0,
        "screen_case_id",
        [f"SCREEN_{index:06d}" for index in range(1, len(screening) + 1)],
    )
    identity_columns = [*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN, FAST_DROP_ENABLED_COLUMN]
    if screening[identity_columns].duplicated().any():
        raise AssertionError("Global/refined search contains duplicate parameter cases.")
    expected_screening = int(config_parameters["global_sample_count"]) + int(
        config_parameters["refined_sample_count"]
    )
    if len(screening) != expected_screening:
        raise AssertionError(f"Expected {expected_screening} screened cases; found {len(screening)}.")

    plateau_anchors = select_joint_plateau_anchors(
        screening,
        count=int(config_parameters["joint_anchor_count"]),
    )
    neighborhood_cases = build_joint_neighborhood_cases(
        search_space,
        plateau_anchors,
        radii=config_parameters["joint_neighborhood_radii"],
        cases_per_anchor=int(config_parameters["joint_cases_per_anchor"]),
        seed=int(config_parameters["random_seed"]) + 2,
    )
    neighborhood_results = screen_cases(
        raw,
        neighborhood_cases,
        analysis_start=train_start,
        analysis_end=train_end,
        initial_cash=initial_cash,
        evaluation_start="analysis_start",
    )
    plateau_summary = summarize_joint_plateaus(
        plateau_anchors,
        neighborhood_results,
        search_space=search_space,
    )
    selected = select_plateau_representative(plateau_anchors, plateau_summary)
    band_config = config_parameters["local_parameter_band"]
    parameter_band = local_parameter_band(
        selected,
        neighborhood_results,
        cagr_tolerance_pct_points=float(band_config["cagr_tolerance_pct_points"]),
        sharpe_tolerance=float(band_config["sharpe_tolerance"]),
    )

    training_candidates = formal_training_candidates(screening, selected)
    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "daily",
            "reference_daily",
            "buy_hold_daily",
            "orders",
            "trades",
            "signal_plans",
        )
    }
    maximum_differences: dict[str, float] = {}
    for _, candidate in training_candidates.iterrows():
        record, frames, differences = formal_case(
            raw,
            train_analysis,
            candidate,
            start=train_start,
            end=train_end,
            initial_cash=initial_cash,
            evaluation_start="analysis_start",
        )
        record["window_id"] = "TRAIN"
        formal_records.append(record)
        for name, frame in with_window(frames, "TRAIN").items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))

    oos_cases = enable_modes(
        pd.DataFrame([{name: float(selected[name]) for name in PARAMETER_COLUMNS}])
    )
    oos_screen = screen_cases(
        raw,
        oos_cases,
        analysis_start=test_start,
        analysis_end=test_end,
        initial_cash=initial_cash,
        evaluation_start="analysis_start",
    )
    oos_candidate = oos_screen.iloc[0].copy()
    oos_candidate["case_id"] = "LOCKED_OOS"
    oos_candidate["selection_reason"] = "LOCKED_JOINT_PLATEAU_REPRESENTATIVE"
    oos_record, oos_frames, oos_differences = formal_case(
        raw,
        test_analysis,
        oos_candidate,
        start=test_start,
        end=test_end,
        initial_cash=initial_cash,
        evaluation_start="analysis_start",
    )
    oos_record["window_id"] = "LOCKED_OOS"
    formal_records.append(oos_record)
    for name, frame in with_window(oos_frames, "LOCKED_OOS").items():
        frame_groups[name].append(frame)
    for key, value in oos_differences.items():
        maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))

    formal_results = pd.DataFrame.from_records(formal_records)
    orders = pd.concat(frame_groups["orders"], ignore_index=True)
    if SELL_FAST_DROP in set(orders.get("primary_signal", pd.Series(dtype=str)).astype(str)):
        raise AssertionError("C/D fast-drop sell unexpectedly produced a primary order.")
    if (
        orders.get("matched_signals", pd.Series(dtype=str))
        .astype(str)
        .str.contains(SELL_FAST_DROP, regex=False)
        .any()
    ):
        raise AssertionError("C/D fast-drop sell unexpectedly appeared in matched signals.")

    elapsed = time.perf_counter() - started
    train_plateau = formal_results[
        formal_results["selection_reason"].str.contains("JOINT_PLATEAU_REPRESENTATIVE")
        & (formal_results["window_id"] == "TRAIN")
    ].iloc[0]
    locked_oos = formal_results[formal_results["window_id"] == "LOCKED_OOS"].iloc[0]
    summary: dict[str, Any] = {
        "symbol": "QQQ",
        "indicator_history_start": config_parameters["indicator_history_start"],
        "train_start": train_start.date().isoformat(),
        "train_end": train_end.date().isoformat(),
        "locked_test_start": test_start.date().isoformat(),
        "locked_test_end": test_end.date().isoformat(),
        "search_case_count": len(screening),
        "global_case_count": len(global_results),
        "refined_case_count": len(refined_results),
        "joint_anchor_count": len(plateau_anchors),
        "joint_neighborhood_case_count": len(neighborhood_results),
        "formal_case_count": len(formal_results),
        "search_elapsed_seconds": elapsed,
        "selected_anchor_id": str(selected["anchor_id"]),
        "selected_parameters": active_parameters(selected),
        "selected_boundary_dimension_count": int(selected["boundary_dimension_count"]),
        "selected_boundary_dimensions": str(selected["boundary_dimensions"]),
        "selected_joint_plateau_score": float(selected["joint_plateau_score"]),
        "selected_neighbor_cagr_q25_pct": float(selected["neighbor_cagr_q25_pct"]),
        "selected_neighbor_sharpe_q25": float(selected["neighbor_sharpe_q25"]),
        "train_selected": json_safe(train_plateau.to_dict()),
        "locked_oos": json_safe(locked_oos.to_dict()),
        "max_cross_check_differences": maximum_differences,
        "fast_drop_enabled": False,
    }

    outputs = {
        "parameter_results.csv": screening,
        "joint_neighborhood_results.csv": neighborhood_results,
        "joint_plateau_summary.csv": plateau_summary,
        "local_parameter_band.csv": parameter_band,
        "formal_candidate_results.csv": formal_results,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frame_groups.items()
        },
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 0.0,
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "quantkit.intraday_sma_search numba compiled ledger",
        "plateau_engine": "quantkit.intraday_sma_plateau joint neighborhoods",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "parameters": config_parameters,
        "search_summary": summary,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 0.0, manifest_path)
    print(
        f"Completed {len(screening):,} search cases + {len(neighborhood_results):,} joint neighbors "
        f"in {elapsed:.1f}s; selected {summary['selected_parameters']}; "
        f"OOS CAGR {locked_oos['cagr_pct']:.4f}% / Sharpe {locked_oos['sharpe']:.4f}"
    )


if __name__ == "__main__":
    main()
