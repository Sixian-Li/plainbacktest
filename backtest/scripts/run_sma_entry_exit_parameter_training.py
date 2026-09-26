#!/usr/bin/env python3
"""Run staged 2000-2015 QQQ parameter training for the R1+R3 strategy."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.reference import run_buy_and_hold_reference
from quantkit.sma_entry_exit_ablation import (
    compiled_pybroker_daily_state,
    run_pybroker_compiled_entry_exit_ablation,
)
from quantkit.sma_entry_exit_training import (
    PARAMETER_COLUMNS,
    add_cases,
    build_staged_cases,
    drawdown_guard,
    parameter_key,
    run_training_case,
    select_representative,
)
from scripts.run_sma_entry_four_exit_ablation import cross_check
from scripts.run_sma_regime_ablation import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.20a.1__26-08-14__qqq_sma_r1_r3_parameter_training_2000_2015"


def bounded_parameter(name: str, value: float) -> float | int:
    if name in {"short_center", "short_spacing", "long_window", "r1_decline_days"}:
        return int(round(value))
    return round(float(value), 6)


def metrics_row(
    raw: pd.DataFrame,
    row: dict[str, Any],
    *,
    start: str,
    end: str,
    initial_cash: float,
) -> dict[str, Any]:
    parameters = {name: row[name] for name in PARAMETER_COLUMNS}
    metrics, reference, _ = run_training_case(
        raw,
        parameters,
        start=start,
        end=end,
        initial_cash=initial_cash,
        case_id=str(row["case_id"]),
    )
    return {
        **row,
        **metrics,
        "intraday_entry_count": int(reference.orders["fill_source"].eq("intraday_trigger").sum()),
    }


def evaluate_new_cases(
    raw: pd.DataFrame,
    cases: pd.DataFrame,
    existing: pd.DataFrame,
    *,
    start: str,
    end: str,
    initial_cash: float,
    stage: str,
    baseline_key: tuple[float, ...],
) -> pd.DataFrame:
    done = set(existing["case_id"].astype(str)) if not existing.empty else set()
    rows = existing.to_dict("records") if not existing.empty else []
    for record in cases.to_dict("records"):
        if str(record["case_id"]) in done:
            continue
        result = metrics_row(
            raw, record, start=start, end=end, initial_cash=initial_cash
        )
        result["stage"] = stage
        result["is_baseline"] = parameter_key(record) == baseline_key
        rows.append(result)
    return pd.DataFrame(rows)


def stage_one_ranked_anchors(
    results: pd.DataFrame,
    points: pd.DataFrame,
    selection: dict[str, Any],
    count: int,
) -> pd.DataFrame:
    expanded = points.merge(results, on="case_id", how="left", validate="many_to_one")
    anchors: list[pd.Series] = []
    for _, group in expanded.groupby("sweep_id", sort=False):
        eligible = group[group["passes_guard"].astype(bool)].copy()
        if eligible.empty:
            continue
        eligible = eligible.sort_values(
            ["max_drawdown_pct", "total_return_pct", "sharpe"],
            ascending=[False, False, False],
        )
        anchors.append(eligible.iloc[0])
    if not anchors:
        raise RuntimeError("No OAT sweep has an eligible anchor.")
    ranked = pd.DataFrame(anchors).sort_values(
        ["max_drawdown_pct", "total_return_pct", "sharpe"],
        ascending=[False, False, False],
    )
    return ranked.drop_duplicates("case_id").head(count)


def neighborhood_values(anchor: pd.Series, config: dict[str, Any], parameter: str) -> list[Any]:
    values = {
        bounded_parameter(parameter, float(anchor[parameter]) + float(offset))
        for offset in config["neighborhood"][parameter]
    }
    limits = {
        "short_center": (15, 50),
        "short_spacing": (1, 12),
        "long_window": (150, 250),
        "buy_short_buffer_pct": (0, 6),
        "buy_long_buffer_pct": (0, 6),
        "r1_decline_days": (1, 10),
        "r1_min_daily_decline_pct": (0, 0.1),
        "r3_sell_buffer_pct": (0, 6),
    }
    low, high = limits[parameter]
    return sorted(value for value in values if low <= float(value) <= high)


def cartesian_candidates(
    anchor: pd.Series,
    stage_two: dict[str, Any],
    dimensions: tuple[str, ...],
) -> list[dict[str, Any]]:
    grids = [neighborhood_values(anchor, stage_two, name) for name in dimensions]
    rows: list[dict[str, Any]] = []
    for values in product(*grids):
        candidate = {name: anchor[name] for name in PARAMETER_COLUMNS}
        candidate.update(dict(zip(dimensions, values)))
        if int(candidate["short_center"]) - int(candidate["short_spacing"]) <= 1:
            continue
        rows.append(candidate)
    return rows


def formal_verify(
    raw: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    start: str,
    end: str,
    initial_cash: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    daily_frames: list[pd.DataFrame] = []
    reference_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    differences: dict[str, float] = {}
    for record in selected.to_dict("records"):
        parameters = {name: record[name] for name in PARAMETER_COLUMNS}
        _, _, prepared = run_training_case(
            raw,
            parameters,
            start=start,
            end=end,
            initial_cash=initial_cash,
            case_id=str(record["case_id"]),
        )
        from quantkit.sma_entry_exit_ablation import EntryExitAblationSpec, analysis_slice
        window = analysis_slice(prepared, start=start, end=end)
        result, reference, engine = run_pybroker_compiled_entry_exit_ablation(
            window,
            EntryExitAblationSpec(True, False, True, False),
            initial_cash=initial_cash,
            case_id=str(record["case_id"]),
        )
        actual = compiled_pybroker_daily_state(result, engine)
        check = cross_check(result, actual, reference)
        for key, value in check.items():
            differences[f"{record['case_id']}.{key}"] = value
        actual.insert(0, "case_id", record["case_id"])
        ref_daily = reference.daily.copy()
        ref_daily.insert(0, "formal_case_id", record["case_id"])
        orders = reference.orders.copy()
        orders.insert(0, "formal_case_id", record["case_id"])
        trades = reference.trades.copy()
        trades.insert(0, "formal_case_id", record["case_id"])
        daily_frames.append(actual)
        reference_frames.append(ref_daily)
        order_frames.append(orders)
        trade_frames.append(trades)
    return (
        pd.concat(daily_frames, ignore_index=True),
        pd.concat(reference_frames, ignore_index=True),
        pd.concat(order_frames, ignore_index=True),
        pd.concat(trade_frames, ignore_index=True),
        differences,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or float(args.cost_bps) != 0:
        raise ValueError("This experiment is frozen to QQQ and zero costs.")
    output_root = reserve_block(context, args.run_id, args.symbol, args.cost_bps)
    config = context.config["parameters"]
    selection = config["selection"]
    start = config["training_window"]["requested_start"]
    end = config["training_window"]["end"]
    initial_cash = float(context.config["initial_cash"])
    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    baseline_key = parameter_key(config["baseline"])

    cases, points = build_staged_cases(config)
    results = evaluate_new_cases(
        raw, cases, pd.DataFrame(), start=start, end=end, initial_cash=initial_cash,
        stage="stage_1_oat", baseline_key=baseline_key,
    )
    results["passes_guard"] = drawdown_guard(results, selection)
    anchors = stage_one_ranked_anchors(
        results, points, selection, int(config["stage_2"]["top_oat_anchors"])
    )

    buy_dimensions = (
        "short_center", "short_spacing", "long_window",
        "buy_short_buffer_pct", "buy_long_buffer_pct",
    )
    sell_dimensions = (
        "long_window", "r1_decline_days", "r1_min_daily_decline_pct",
        "r3_sell_buffer_pct",
    )
    stage_two_candidates: list[dict[str, Any]] = []
    for anchor in anchors.itertuples(index=False):
        anchor_series = pd.Series(anchor._asdict())
        stage_two_candidates.extend(
            cartesian_candidates(anchor_series, config["stage_2"], buy_dimensions)
        )
        stage_two_candidates.extend(
            cartesian_candidates(anchor_series, config["stage_2"], sell_dimensions)
        )
    all_cases = add_cases(cases, stage_two_candidates, prefix="S2")
    results = evaluate_new_cases(
        raw, all_cases, results, start=start, end=end, initial_cash=initial_cash,
        stage="stage_2_local", baseline_key=baseline_key,
    )
    results["passes_guard"] = drawdown_guard(results, selection)
    stage_two_results = results[results["stage"].eq("stage_2_local")].copy()
    stage_two_rep = select_representative(stage_two_results, selection)

    joint_dimensions = (
        "long_window", "buy_short_buffer_pct", "buy_long_buffer_pct",
        "r1_decline_days", "r1_min_daily_decline_pct", "r3_sell_buffer_pct",
    )
    joint_offsets = {
        **config["stage_2"],
        "neighborhood": {
            **config["stage_2"]["neighborhood"],
            "long_window": [-2, 0, 2],
            "buy_short_buffer_pct": [-0.25, 0.0, 0.25],
            "buy_long_buffer_pct": [-0.25, 0.0, 0.25],
            "r1_decline_days": [-1, 0, 1],
            "r1_min_daily_decline_pct": [-0.005, 0.0, 0.005],
            "r3_sell_buffer_pct": [-0.25, 0.0, 0.25],
        },
    }
    joint_candidates = cartesian_candidates(stage_two_rep, joint_offsets, joint_dimensions)
    all_cases = add_cases(all_cases, joint_candidates, prefix="S3")
    results = evaluate_new_cases(
        raw, all_cases, results, start=start, end=end, initial_cash=initial_cash,
        stage="stage_3_joint", baseline_key=baseline_key,
    )
    results["passes_guard"] = drawdown_guard(results, selection)
    final_pool = results[results["stage"].isin(["stage_2_local", "stage_3_joint"])].copy()
    representative = select_representative(final_pool, selection)
    baseline = results[results["is_baseline"].astype(bool)].iloc[0]
    representative_id = str(representative["case_id"])
    baseline_id = str(baseline["case_id"])
    formal = results[results["case_id"].isin([baseline_id, representative_id])].copy()
    formal["selection_reason"] = formal["case_id"].map(
        {baseline_id: "FROZEN_BASELINE", representative_id: "TRAINING_PLATEAU_REPRESENTATIVE"}
    )
    formal_daily, reference_daily, orders, trades, differences = formal_verify(
        raw, formal, start=start, end=end, initial_cash=initial_cash
    )

    baseline_prepared_metrics, _, baseline_prepared = run_training_case(
        raw, config["baseline"], start=start, end=end, initial_cash=initial_cash,
        case_id=baseline_id,
    )
    from quantkit.sma_entry_exit_ablation import analysis_slice
    benchmark_window = analysis_slice(baseline_prepared, start=start, end=end)
    benchmark = run_buy_and_hold_reference(
        benchmark_window, ExplicitFillPolicy("open", 0), initial_cash=initial_cash
    )
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )

    point_results = points.merge(
        results, on="case_id", how="left", validate="many_to_one"
    )
    outputs = {
        "parameter_results.csv": results,
        "stage_1_oat_points.csv": point_results,
        "stage_1_anchors.csv": anchors,
        "formal_cases.csv": formal,
        "daily.csv": formal_daily,
        "reference_daily.csv": reference_daily,
        "orders.csv": orders,
        "trades.csv": trades,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
        "benchmark_results.csv": pd.DataFrame([benchmark_metrics]),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "training_window": {
            "actual_start": str(formal_daily["date"].min().date()),
            "actual_end": str(formal_daily["date"].max().date()),
            "bars": int(formal_daily[formal_daily["case_id"].eq(baseline_id)].shape[0]),
        },
        "case_counts": results.groupby("stage")["case_id"].nunique().to_dict(),
        "baseline": json_safe(baseline.to_dict()),
        "representative": json_safe(representative.to_dict()),
        "benchmark": json_safe(benchmark_metrics),
        "max_cross_check_differences": differences,
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 0,
        "engine": "independent stateful parameter screening plus lib-pybroker 1.2.12 formal verification",
        "python": platform.python_version(),
        "parameters": config,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset,
        "max_cross_check_differences": differences,
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
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 0, manifest_path)
    print(f"cases={len(results):,}")
    print(f"baseline={baseline_id} return={baseline['total_return_pct']:.2f}% dd={baseline['max_drawdown_pct']:.2f}%")
    print(f"representative={representative_id} return={representative['total_return_pct']:.2f}% dd={representative['max_drawdown_pct']:.2f}%")
    print({name: representative[name] for name in PARAMETER_COLUMNS})
    print(f"max ledger difference={max(differences.values()):.3g}")


if __name__ == "__main__":
    main()
