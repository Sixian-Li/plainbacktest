#!/usr/bin/env python3
"""Run the flat-start QQQ intraday SMA forced-reentry grid."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.intraday_sma import (
    ALL_SIGNALS,
    BUY_FORCED_REENTRY,
    IntradaySmaSpec,
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021"


def benchmark_state(
    analysis: pd.DataFrame,
    *,
    first_entry_date: pd.Timestamp,
    first_entry_fill: float,
    initial_cash: float,
) -> pd.DataFrame:
    result = analysis[analysis["date"] >= first_entry_date][["date", "symbol", "close"]].copy()
    result["cash"] = 0.0
    result["shares"] = initial_cash / first_entry_fill
    result["equity"] = result["close"].astype(float) * result["shares"]
    result["is_long"] = 1
    return result.reset_index(drop=True)


def with_case(frame: pd.DataFrame, case_id: str, r_pct: float) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    result.insert(1, "R_forced_rebuy_pct", r_pct)
    return result


def ablation_case_id(r_pct: float, disabled_signal: str | None) -> str:
    suffix = "all_rules" if disabled_signal is None else f"without_{disabled_signal}"
    return f"R{int(r_pct):02d}_{suffix}"


def ablation_matrix(grid: list[float]) -> list[tuple[float, str | None]]:
    return [(r_pct, disabled) for r_pct in grid for disabled in (None, *ALL_SIGNALS)]


def with_ablation_case(
    frame: pd.DataFrame,
    *,
    case_id: str,
    r_pct: float,
    disabled_signal: str,
) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "ablation_case_id", case_id)
    result.insert(1, "R_forced_rebuy_pct", r_pct)
    result.insert(2, "disabled_signal", disabled_signal)
    return result


def run_ablation_worker(payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    parameters = payload["parameters"]
    spec_parameters = dict(parameters)
    spec_parameters["R_forced_rebuy_pct"] = float(payload["R_forced_rebuy_pct"])
    spec = IntradaySmaSpec.from_parameters(spec_parameters)
    start = pd.Timestamp(payload["analysis_start"])
    end = pd.Timestamp(payload["analysis_end"])
    common_entry = pd.Timestamp(payload["common_entry_date"])
    initial_cash = float(payload["initial_cash"])
    disabled = str(payload["disabled_signal"])
    case_id = str(payload["ablation_case_id"])
    case_root = Path(payload["case_root"])
    raw = pd.read_csv(Path(payload["canonical_path"]), parse_dates=["date"])
    raw = raw[raw["symbol"] == payload["symbol"]].sort_values("date").reset_index(drop=True)

    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
        disabled_signals=(disabled,),
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
        disabled_signals=(disabled,),
    )
    differences = cross_check(result, actual, reference)
    if not reference.orders.empty:
        first = reference.orders.iloc[0]
        if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
            raise AssertionError(f"{case_id} did not begin with an ordinary buy signal.")
        if pd.Timestamp(first["date"]).normalize() < common_entry:
            raise AssertionError(f"{case_id} entered before the all-rules common start.")

    evaluation_daily = actual[actual["date"] >= common_entry].reset_index(drop=True)
    evaluation_reference = reference.daily[reference.daily["date"] >= common_entry].reset_index(drop=True)
    evaluation_orders = reference.orders[
        pd.to_datetime(reference.orders["date"]) >= common_entry
    ].reset_index(drop=True) if not reference.orders.empty else reference.orders.copy()
    evaluation_trades = reference.trades[
        pd.to_datetime(reference.trades["exit_date"]) >= common_entry
    ].reset_index(drop=True) if not reference.trades.empty else reference.trades.copy()
    evaluation_plans = reference.signal_plans[
        pd.to_datetime(reference.signal_plans["date"]) >= common_entry
    ].reset_index(drop=True) if not reference.signal_plans.empty else reference.signal_plans.copy()
    metrics = calculate_metrics(
        evaluation_daily,
        evaluation_orders,
        evaluation_trades,
        initial_cash=initial_cash,
    )
    signal_counts = evaluation_orders["primary_signal"].value_counts().to_dict() if not evaluation_orders.empty else {}
    fill_counts = evaluation_orders["fill_source"].value_counts().to_dict() if not evaluation_orders.empty else {}
    record = {
        "ablation_case_id": case_id,
        "R_forced_rebuy_pct": float(payload["R_forced_rebuy_pct"]),
        "disabled_signal": disabled,
        **metrics,
        "evaluation_start": common_entry,
        "first_entry_date": None if evaluation_orders.empty else pd.Timestamp(evaluation_orders.iloc[0]["date"]),
        "first_entry_fill": None if evaluation_orders.empty else float(evaluation_orders.iloc[0]["fill_price"]),
        "first_entry_signal": None if evaluation_orders.empty else str(evaluation_orders.iloc[0]["primary_signal"]),
        "forced_reentry_count": int(signal_counts.get(BUY_FORCED_REENTRY, 0)),
        "primary_signal_counts": json.dumps(signal_counts, ensure_ascii=False, sort_keys=True),
        "fill_source_counts": json.dumps(fill_counts, ensure_ascii=False, sort_keys=True),
        **differences,
    }
    case_outputs = {
        "daily.csv": evaluation_daily,
        "reference_daily.csv": evaluation_reference,
        "orders.csv": evaluation_orders,
        "trades.csv": evaluation_trades,
        "signal_plans.csv": evaluation_plans,
    }
    for name, frame in case_outputs.items():
        normalize_frame(frame).to_csv(case_root / name, index=False, lineterminator="\n")
    case_manifest = {
        "schema_version": 1,
        "ablation_case_id": case_id,
        "R_forced_rebuy_pct": float(payload["R_forced_rebuy_pct"]),
        "disabled_signal": disabled,
        "evaluation_start": common_entry.date().isoformat(),
        "metrics": json_safe(metrics),
        "max_cross_check_differences": differences,
        "artifacts": {
            name: {"bytes": (case_root / name).stat().st_size, "sha256": sha256(case_root / name)}
            for name in case_outputs
        },
    }
    (case_root / "manifest.json").write_text(
        json.dumps(json_safe(case_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (case_root / "result.json").write_text(
        json.dumps(json_safe(record), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--ablation-worker-payload", type=Path)
    args = parser.parse_args()

    if args.ablation_worker_payload is not None:
        run_ablation_worker(args.ablation_worker_payload)
        return

    context = load_experiment(args.experiment)
    symbol = args.symbol
    cost_bps = float(args.cost_bps)
    if symbol not in context.config["symbols"] or cost_bps != 0:
        raise ValueError("This experiment requires configured QQQ at exactly 0 bps.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    grid = [float(value) for value in parameters["R_forced_rebuy_pct_grid"]]
    if grid != [float(value) for value in range(11)]:
        raise ValueError("R grid must be exactly 0%, 1%, ..., 10%.")
    base_parameters = dict(parameters)
    base_parameters["R_forced_rebuy_pct"] = grid[0]
    base_spec = IntradaySmaSpec.from_parameters(base_parameters)

    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    prepared = prepare_intraday_sma_data(raw, base_spec)
    analysis = prepared[(prepared["date"] >= start) & (prepared["date"] <= end)].reset_index(drop=True)

    daily_frames: list[pd.DataFrame] = []
    reference_daily_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    plan_frames: list[pd.DataFrame] = []
    metric_records: list[dict[str, Any]] = []
    common_entry: tuple[pd.Timestamp, float, str] | None = None
    maximum_differences: dict[str, float] = {}

    for r_pct in grid:
        case_id = f"R{int(r_pct):02d}"
        spec = replace(base_spec, r_forced_rebuy_pct=r_pct)
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
        differences = cross_check(result, actual, reference)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))

        if reference.orders.empty:
            raise AssertionError(f"{case_id} never received a buy signal.")
        first = reference.orders.iloc[0]
        first_entry = (
            pd.Timestamp(first["date"]).normalize(),
            float(first["fill_price"]),
            str(first["primary_signal"]),
        )
        if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
            raise AssertionError(f"{case_id} did not begin with an ordinary buy signal.")
        if first_entry[2] == BUY_FORCED_REENTRY:
            raise AssertionError("Forced re-entry cannot create the first position without a prior sell.")
        if common_entry is None:
            common_entry = first_entry
        elif first_entry != common_entry:
            raise AssertionError(f"First entry differs across R cases: {case_id}={first_entry}, common={common_entry}")

        evaluation_daily = actual[actual["date"] >= first_entry[0]].reset_index(drop=True)
        evaluation_reference = reference.daily[reference.daily["date"] >= first_entry[0]].reset_index(drop=True)
        metrics = calculate_metrics(
            evaluation_daily,
            reference.orders,
            result.trades.reset_index(drop=True),
            initial_cash=initial_cash,
        )
        signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
        fill_counts = reference.orders["fill_source"].value_counts().to_dict()
        metric_records.append(
            {
                "case_id": case_id,
                "R_forced_rebuy_pct": r_pct,
                **metrics,
                "first_entry_date": first_entry[0],
                "first_entry_fill": first_entry[1],
                "first_entry_signal": first_entry[2],
                "forced_reentry_count": int(signal_counts.get(BUY_FORCED_REENTRY, 0)),
                "primary_signal_counts": json.dumps(signal_counts, ensure_ascii=False, sort_keys=True),
                "fill_source_counts": json.dumps(fill_counts, ensure_ascii=False, sort_keys=True),
                **differences,
            }
        )
        daily_frames.append(with_case(evaluation_daily, case_id, r_pct))
        reference_daily_frames.append(with_case(evaluation_reference, case_id, r_pct))
        order_frames.append(with_case(reference.orders, case_id, r_pct))
        trade_frames.append(with_case(reference.trades, case_id, r_pct))
        plan_frames.append(with_case(reference.signal_plans, case_id, r_pct))

    assert common_entry is not None

    ablation_records: list[dict[str, Any]] = []
    baseline_by_r = {float(row["R_forced_rebuy_pct"]): row for row in metric_records}
    for r_pct, disabled in ablation_matrix(grid):
        case_id = ablation_case_id(r_pct, disabled)
        if disabled is None:
            baseline = dict(baseline_by_r[r_pct])
            ablation_records.append(
                {
                    "ablation_case_id": case_id,
                    "R_forced_rebuy_pct": r_pct,
                    "disabled_signal": None,
                    **baseline,
                }
            )
            continue

        case_root = output_root / "ablations" / case_id
        case_root.mkdir(parents=True)
        worker_payload = {
            "ablation_case_id": case_id,
            "R_forced_rebuy_pct": r_pct,
            "disabled_signal": disabled,
            "parameters": parameters,
            "analysis_start": start.date().isoformat(),
            "analysis_end": end.date().isoformat(),
            "common_entry_date": common_entry[0].date().isoformat(),
            "initial_cash": initial_cash,
            "symbol": symbol,
            "canonical_path": str(canonical_path),
            "case_root": str(case_root),
        }
        payload_path = case_root / "worker_payload.json"
        payload_path.write_text(
            json.dumps(worker_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.run_intraday_sma_reentry_grid",
                "--run-id",
                args.run_id,
                "--symbol",
                symbol,
                "--cost-bps",
                str(cost_bps),
                "--ablation-worker-payload",
                str(payload_path),
            ],
            cwd=BACKTEST_ROOT,
            check=True,
        )
        record = json.loads((case_root / "result.json").read_text(encoding="utf-8"))
        differences = {
            key: float(value)
            for key, value in record.items()
            if key.startswith("max_abs_")
        }
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
        ablation_records.append(record)

    benchmark = benchmark_state(
        analysis,
        first_entry_date=common_entry[0],
        first_entry_fill=common_entry[1],
        initial_cash=initial_cash,
    )
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_cash,
    )
    parameter_results = pd.DataFrame(metric_records)
    ablation_results = pd.DataFrame(ablation_records)
    baseline_metrics = parameter_results[
        ["R_forced_rebuy_pct", "cagr_pct", "sharpe", "max_drawdown_pct", "order_count"]
    ].rename(
        columns={
            "cagr_pct": "baseline_cagr_pct",
            "sharpe": "baseline_sharpe",
            "max_drawdown_pct": "baseline_max_drawdown_pct",
            "order_count": "baseline_order_count",
        }
    )
    ablation_results = ablation_results.merge(
        baseline_metrics,
        on="R_forced_rebuy_pct",
        how="left",
        validate="many_to_one",
    )
    ablation_results["delta_cagr_pct_points"] = (
        ablation_results["cagr_pct"] - ablation_results["baseline_cagr_pct"]
    )
    ablation_results["delta_sharpe"] = (
        ablation_results["sharpe"] - ablation_results["baseline_sharpe"]
    )
    ablation_results["delta_max_drawdown_pct_points"] = (
        ablation_results["max_drawdown_pct"] - ablation_results["baseline_max_drawdown_pct"]
    )
    ablation_results["delta_order_count"] = (
        ablation_results["order_count"] - ablation_results["baseline_order_count"]
    )
    best_cagr = parameter_results.loc[parameter_results["cagr_pct"].idxmax()]
    best_sharpe = parameter_results.loc[parameter_results["sharpe"].idxmax()]
    summary_metrics = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "grid_values": grid,
        "case_count": len(grid),
        "ablation_case_count": len(ablation_results),
        "ablation_disabled_signals": list(ALL_SIGNALS),
        "initial_position": "flat",
        "initial_cash": initial_cash,
        "first_entry_date": common_entry[0].date().isoformat(),
        "first_entry_fill": common_entry[1],
        "first_entry_signal": common_entry[2],
        "best_sample_cagr_case": str(best_cagr["case_id"]),
        "best_sample_cagr_pct": float(best_cagr["cagr_pct"]),
        "best_sample_sharpe_case": str(best_sharpe["case_id"]),
        "best_sample_sharpe": float(best_sharpe["sharpe"]),
        "benchmark": benchmark_metrics,
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "daily.csv": pd.concat(daily_frames, ignore_index=True),
        "reference_daily.csv": pd.concat(reference_daily_frames, ignore_index=True),
        "buy_hold_daily.csv": benchmark,
        "orders.csv": pd.concat(order_frames, ignore_index=True),
        "trades.csv": pd.concat(trade_frames, ignore_index=True),
        "signal_plans.csv": pd.concat(plan_frames, ignore_index=True),
        "parameter_results.csv": parameter_results,
        "ablation_results.csv": ablation_results,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary_metrics), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "evaluation_start": common_entry[0].date().isoformat(),
        "analysis_bars_before_entry": int((analysis["date"] < common_entry[0]).sum()),
        "initial_cash": initial_cash,
        "initial_position": "flat",
        "parameters": parameters,
        "grid_case_count": len(grid),
        "ablation_case_count": len(ablation_results),
        "ablation_design": "for every R, compare all rules with each of the seven signals disabled one at a time; all metrics use the all-rules common first-entry date",
        "signal_timing": "next-session trigger formulas frozen from completed closes through the preceding session",
        "execution_timing": "next regular-session Open if already crossed; exact threshold if touched intraday",
        "one_trade_max_per_day": True,
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "benchmark_metrics": benchmark_metrics,
        "grid_summary": summary_metrics,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "artifacts": {},
    }
    for path in sorted(output_root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            name = str(path.relative_to(output_root))
            manifest["artifacts"][name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for path in sorted((output_root / "ablations").glob("*/manifest.json")):
        name = str(path.relative_to(output_root))
        manifest["artifacts"][name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {len(grid)} R cases and {len(ablation_results)} ablation cases; first entry {common_entry[0].date()} "
        f"at {common_entry[1]:.6f}; best CAGR {best_cagr['case_id']}={best_cagr['cagr_pct']:.4f}%"
    )


if __name__ == "__main__":
    main()
