#!/usr/bin/env python3
"""Run the QQQ full-history two-objective global parameter search."""

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
from quantkit.intraday_sma import (
    IntradaySmaSpec,
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.intraday_sma_search import (
    PARAMETER_COLUMNS,
    sample_global_cases,
    sample_refined_cases,
    screen_cases,
    select_formal_candidates,
    select_frontier_parents,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_reentry_grid import benchmark_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history"
FORMAL_LEDGER_TOLERANCE = 1e-6


def spec_from_row(row: pd.Series | dict[str, Any]) -> IntradaySmaSpec:
    center = int(row["F_short_sma_center"])
    spacing = int(row["F_short_sma_spacing"])
    return IntradaySmaSpec(
        a_negative_days_slow=int(row["A_negative_days_slow"]),
        b_slow_sma_window=int(row["B_slow_sma_window"]),
        c_fast_derivative_pct=float(row["C_fast_derivative_pct"]),
        d_negative_days_fast=int(row["D_negative_days_fast"]),
        e_fallback_sma_window=int(row["E_fallback_sma_window"]),
        f_short_sma_windows=(center - spacing, center, center + spacing),
        g_short_recovery_below_pct=float(row["G_short_recovery_below_pct"]),
        h_reentry_sma_window=int(row["H_reentry_sma_window"]),
        l_cost_stop_pct=float(row["L_cost_stop_pct"]),
        r_forced_rebuy_pct=float(row["R_forced_rebuy_pct"]),
        fast_derivative_mode="all_short_smas",
        fast_drop_enabled=bool(row.get("fast_drop_enabled", True)),
    )


def with_case(frame: pd.DataFrame, case_id: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    return result


def formal_case(
    raw: pd.DataFrame,
    analysis: pd.DataFrame,
    candidate: pd.Series,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
    evaluation_start: str = "first_entry",
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float]]:
    if evaluation_start not in {"first_entry", "analysis_start"}:
        raise ValueError("evaluation_start must be 'first_entry' or 'analysis_start'.")
    case_id = str(candidate["case_id"])
    spec = spec_from_row(candidate)
    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
    )
    result = pybroker_run.pybroker_result
    engine = raw[(raw["date"] >= pybroker_run.engine_start) & (raw["date"] <= end)].copy()
    actual = pybroker_daily_state(result, engine)
    actual = actual[actual["date"] >= start].reset_index(drop=True)
    reference = run_reference_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
    )
    differences = cross_check(
        result,
        actual,
        reference,
        tolerance=FORMAL_LEDGER_TOLERANCE,
    )
    if reference.orders.empty:
        raise AssertionError(f"{case_id} has no normal buy.")
    first = reference.orders.iloc[0]
    if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
        raise AssertionError(f"{case_id} did not begin with a normal buy.")
    if reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{case_id} traded more than once on one day.")

    first_date = pd.Timestamp(first["date"]).normalize()
    first_fill = float(first["fill_price"])
    metric_start = start if evaluation_start == "analysis_start" else first_date
    daily = actual[actual["date"] >= metric_start].reset_index(drop=True)
    reference_daily = reference.daily[reference.daily["date"] >= metric_start].reset_index(drop=True)
    benchmark_entry_date = start if evaluation_start == "analysis_start" else first_date
    benchmark_fill = (
        float(analysis.iloc[0]["open"])
        if evaluation_start == "analysis_start"
        else first_fill
    )
    benchmark = benchmark_state(
        analysis,
        first_entry_date=benchmark_entry_date,
        first_entry_fill=benchmark_fill,
        initial_cash=initial_cash,
    )
    metrics = calculate_metrics(daily, reference.orders, reference.trades, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    for field in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
        screened = float(candidate[field])
        formal = float(metrics[field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance):
            raise AssertionError(f"{case_id} screening/formal {field} differs: {screened} vs {formal}")
    if int(candidate["order_count"]) != int(metrics["order_count"]):
        raise AssertionError(f"{case_id} screening/formal order count differs.")
    if pd.Timestamp(candidate["first_entry_date"]).normalize() != first_date:
        raise AssertionError(f"{case_id} screening/formal first date differs.")

    signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
    record = {
        "case_id": case_id,
        "selection_reason": str(candidate["selection_reason"]),
        "evaluation_start": evaluation_start,
        **{name: float(candidate[name]) for name in PARAMETER_COLUMNS},
        "forced_reentry_enabled": bool(candidate.get("forced_reentry_enabled", True)),
        "fast_drop_enabled": bool(candidate.get("fast_drop_enabled", True)),
        **metrics,
        "first_entry_date": first_date,
        "first_entry_fill": first_fill,
        "first_entry_signal": str(first["primary_signal"]),
        "benchmark_final_equity": benchmark_metrics["final_equity"],
        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
        "benchmark_sharpe": benchmark_metrics["sharpe"],
        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
        "delta_cagr_vs_buy_hold_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
        "delta_sharpe_vs_buy_hold": metrics["sharpe"] - benchmark_metrics["sharpe"],
        "primary_signal_counts": json.dumps(signal_counts, ensure_ascii=False, sort_keys=True),
        **differences,
    }
    frames = {
        "daily": with_case(daily, case_id),
        "reference_daily": with_case(reference_daily, case_id),
        "buy_hold_daily": with_case(benchmark, case_id),
        "orders": with_case(reference.orders, case_id),
        "trades": with_case(reference.trades, case_id),
        "signal_plans": with_case(reference.signal_plans, case_id),
    }
    return record, frames, differences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    symbol = args.symbol
    cost_bps = float(args.cost_bps)
    if symbol != "QQQ" or symbol not in context.config["symbols"] or cost_bps != 0:
        raise ValueError("This frozen experiment requires QQQ at 0 bps.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    search_space = parameters["search_space"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    analysis = raw[(raw["date"] >= start) & (raw["date"] <= end)].reset_index(drop=True)

    started = time.perf_counter()
    global_cases = sample_global_cases(
        search_space,
        count=int(parameters["global_sample_count"]),
        seed=int(parameters["random_seed"]),
        anchors=parameters["anchors"],
    )
    global_results = screen_cases(analysis, global_cases, initial_cash=initial_cash)
    global_results.insert(0, "search_stage", "global")
    parents = select_frontier_parents(
        global_results,
        per_objective=int(parameters["frontier_parent_count_per_objective"]),
    )
    refined_cases = sample_refined_cases(
        search_space,
        parents,
        global_results,
        count=int(parameters["refined_sample_count"]),
        seed=int(parameters["random_seed"]) + 1,
    )
    refined_results = screen_cases(analysis, refined_cases, initial_cash=initial_cash)
    refined_results.insert(0, "search_stage", "refined")
    screening = pd.concat((global_results, refined_results), ignore_index=True)
    screening.insert(0, "screen_case_id", [f"SCREEN_{index:06d}" for index in range(1, len(screening) + 1)])
    if screening[list(PARAMETER_COLUMNS)].duplicated().any():
        raise AssertionError("Search contains duplicate parameter cases.")
    if len(screening) != int(parameters["global_sample_count"]) + int(parameters["refined_sample_count"]):
        raise AssertionError("Search result count differs from the frozen contract.")

    candidates = select_formal_candidates(
        screening,
        per_objective=int(parameters["formal_candidate_count_per_objective"]),
    )
    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "buy_hold_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for _, candidate in candidates.iterrows():
        record, frames, differences = formal_case(
            raw,
            analysis,
            candidate,
            start=start,
            end=end,
            initial_cash=initial_cash,
        )
        formal_records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal_results = pd.DataFrame(formal_records)
    best_cagr = formal_results.loc[formal_results["cagr_pct"].idxmax()]
    best_sharpe = formal_results.loc[formal_results["sharpe"].idxmax()]
    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "symbol": symbol,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "initial_cash": initial_cash,
        "search_case_count": len(screening),
        "global_case_count": len(global_results),
        "refined_case_count": len(refined_results),
        "formal_candidate_count": len(formal_results),
        "search_elapsed_seconds": elapsed,
        "best_cagr_case_id": str(best_cagr["case_id"]),
        "best_cagr_pct": float(best_cagr["cagr_pct"]),
        "best_cagr_benchmark_pct": float(best_cagr["benchmark_cagr_pct"]),
        "best_sharpe_case_id": str(best_sharpe["case_id"]),
        "best_sharpe": float(best_sharpe["sharpe"]),
        "best_sharpe_benchmark": float(best_sharpe["benchmark_sharpe"]),
        "screen_cases_beating_buy_hold_cagr": int((screening["delta_cagr_vs_buy_hold_pct_points"] > 0).sum()),
        "screen_cases_beating_buy_hold_sharpe": int((screening["delta_sharpe_vs_buy_hold"] > 0).sum()),
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "parameter_results.csv": screening,
        "formal_candidate_results.csv": formal_results,
        **{f"{name}.csv": pd.concat(frames, ignore_index=True) for name, frames in frame_groups.items()},
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
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "quantkit.intraday_sma_search numba compiled ledger",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "parameters": parameters,
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
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {len(screening)} screened and {len(formal_results)} formal cases in {elapsed:.1f}s; "
        f"best CAGR {best_cagr['cagr_pct']:.4f}%, best Sharpe {best_sharpe['sharpe']:.6f}"
    )


if __name__ == "__main__":
    main()
