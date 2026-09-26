#!/usr/bin/env python3
"""Run the QQQ dual-StochRSI CROSS short/long period grid."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, prepare_sma200_data
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_stochrsi_cross_threshold_grid import run_case


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.70a.2__26-08-25__qqq_stochrsi_period_grid_full_history"
)
_WORKER_STATE: dict[str, Any] = {}


def period_values(start: int, end: int, step: int) -> list[int]:
    if step <= 0 or end < start or (end - start) % step:
        raise ValueError("period bounds require a positive exactly divisible step")
    return list(range(start, end + 1, step))


def case_id(short_period: int, long_period: int) -> str:
    return f"P{short_period:03d}_{long_period:03d}"


def configure_worker(
    raw: pd.DataFrame,
    *,
    cost_bps: float,
    buy_threshold: float,
    sell_threshold: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> None:
    global _WORKER_STATE
    _WORKER_STATE = {
        "raw": raw,
        "cost_bps": cost_bps,
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
        "start": start,
        "end": end,
        "initial_cash": initial_cash,
    }


def worker(pair: tuple[int, int]) -> dict[str, Any]:
    short_period, long_period = pair
    state = _WORKER_STATE
    spec = TimingSpec(
        "CROSS",
        cost_bps=state["cost_bps"],
        periods=(short_period, long_period),
        buy_threshold=state["buy_threshold"],
        sell_threshold=state["sell_threshold"],
    )
    prepared = prepare_dual_stochrsi_data(state["raw"], spec)
    metrics, actual, reference_daily, orders, trades, checked = run_case(
        prepared,
        spec,
        start=state["start"],
        end=state["end"],
        initial_cash=state["initial_cash"],
    )
    plans = checked.pop("signal_plans")
    return {
        "case_id": case_id(short_period, long_period),
        "short_period": short_period,
        "long_period": long_period,
        "metrics": metrics,
        "actual_cash": actual["cash"].to_numpy(float),
        "actual_shares": actual["shares"].to_numpy(float),
        "actual_equity": actual["equity"].to_numpy(float),
        "reference_equity": reference_daily["equity"].to_numpy(float),
        "plan_trigger": pd.to_numeric(plans["trigger"], errors="coerce").to_numpy(float),
        "plan_side": plans["side"].eq("sell").to_numpy(np.int8),
        "plan_eligible": plans["eligible"].to_numpy(bool),
        "plan_filled": plans["filled"].to_numpy(bool),
        "orders": orders,
        "trades": trades,
        "differences": {name: float(value) for name, value in checked.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    symbol, cost_bps = str(args.symbol), float(args.cost_bps)
    if symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This experiment requires QQQ only")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("Cost scenario is not frozen in experiment.json")
    if args.workers < 1:
        raise ValueError("workers must be positive")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq(symbol)].sort_values("date").reset_index(drop=True)
    parameters = context.config["parameters"]
    start, end = pd.Timestamp(parameters["analysis_start"]), pd.Timestamp(parameters["analysis_end"])
    analysis = raw[raw["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen analysis boundaries do not match QQQ sessions")
    short_values = period_values(
        int(parameters["short_period_start"]), int(parameters["short_period_end"]),
        int(parameters["period_step"]),
    )
    long_values = period_values(
        int(parameters["long_period_start"]), int(parameters["long_period_end"]),
        int(parameters["period_step"]),
    )
    pairs = [(short, long) for short in short_values for long in long_values]
    expected_count = int(parameters["combination_count_per_cost"])
    if len(pairs) != expected_count:
        raise RuntimeError("Frozen period grid count is inconsistent")
    initial_cash = float(context.config["initial_cash"])
    started = time.perf_counter()
    configure_worker(
        raw, cost_bps=cost_bps,
        buy_threshold=float(parameters["buy_threshold"]),
        sell_threshold=float(parameters["sell_threshold"]),
        start=start, end=end, initial_cash=initial_cash,
    )

    bars = len(analysis)
    case_ids = np.empty(expected_count, dtype="U9")
    actual_cash = np.empty((expected_count, bars), dtype=np.float64)
    actual_shares = np.empty_like(actual_cash)
    actual_equity = np.empty_like(actual_cash)
    reference_equity = np.empty_like(actual_cash)
    plan_trigger = np.empty_like(actual_cash)
    plan_side = np.empty((expected_count, bars), dtype=np.int8)
    plan_eligible = np.empty((expected_count, bars), dtype=np.bool_)
    plan_filled = np.empty((expected_count, bars), dtype=np.bool_)
    records: list[dict[str, Any]] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    maximum_differences: dict[str, float] = {}
    if args.workers == 1:
        outputs = map(worker, pairs)
        executor = None
    else:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers,
            mp_context=multiprocessing.get_context("fork"),
        )
        outputs = executor.map(worker, pairs, chunksize=1)
    try:
        for index, output in enumerate(outputs):
            arrays = [output[name] for name in (
                "actual_cash", "actual_shares", "actual_equity", "reference_equity",
                "plan_trigger", "plan_side", "plan_eligible", "plan_filled",
            )]
            if any(len(array) != bars for array in arrays):
                raise AssertionError(f"Daily state length mismatch for {output['case_id']}")
            case_ids[index] = output["case_id"]
            actual_cash[index], actual_shares[index] = output["actual_cash"], output["actual_shares"]
            actual_equity[index], reference_equity[index] = output["actual_equity"], output["reference_equity"]
            plan_trigger[index], plan_side[index] = output["plan_trigger"], output["plan_side"]
            plan_eligible[index], plan_filled[index] = output["plan_eligible"], output["plan_filled"]
            orders, trades = output["orders"], output["trades"]
            records.append({
                "case_id": output["case_id"],
                "short_period": output["short_period"],
                "long_period": output["long_period"],
                **output["metrics"],
                "fill_source_counts": json.dumps(
                    orders["fill_source"].value_counts().to_dict(), sort_keys=True
                ),
                **output["differences"],
            })
            for frame, target in ((orders, order_frames), (trades, trade_frames)):
                if frame.empty:
                    continue
                current = frame.copy()
                current.insert(0, "case_id", output["case_id"])
                current.insert(1, "short_period", output["short_period"])
                current.insert(2, "long_period", output["long_period"])
                target.append(current)
            for name, value in output["differences"].items():
                maximum_differences[name] = max(maximum_differences.get(name, 0.0), value)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    results = pd.DataFrame(records)
    if len(results) != expected_count or results["case_id"].nunique() != expected_count:
        raise RuntimeError("Period grid output is incomplete or duplicated")
    normalize_frame(results).to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    normalize_frame(orders).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    normalize_frame(trades).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_signal_state.npz",
        dates=analysis["date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
        case_ids=case_ids,
        actual_cash=actual_cash, actual_shares=actual_shares, actual_equity=actual_equity,
        reference_equity=reference_equity, plan_trigger=plan_trigger, plan_side=plan_side,
        plan_eligible=plan_eligible, plan_filled=plan_filled,
    )

    sma_spec = TimingSpec(
        "SMA200", cost_bps=cost_bps,
        sma_window=int(parameters["sma_baseline_window"]),
        sma_buy_buffer_pct=float(parameters["sma_baseline_buy_buffer_pct"]),
        sma_sell_buffer_pct=float(parameters["sma_baseline_sell_buffer_pct"]),
    )
    sma_prepared = prepare_sma200_data(raw, sma_spec)
    sma_metrics, sma_daily, sma_reference, sma_orders, sma_trades, sma_checked = run_case(
        sma_prepared, sma_spec, start=start, end=end, initial_cash=initial_cash
    )
    sma_plans = sma_checked.pop("signal_plans")
    for name, value in sma_checked.items():
        maximum_differences[name] = max(maximum_differences.get(name, 0.0), float(value))
    for name, frame in {
        "sma200_daily.csv": sma_daily, "sma200_reference_daily.csv": sma_reference,
        "sma200_orders.csv": sma_orders, "sma200_trades.csv": sma_trades,
        "sma200_signal_plans.csv": sma_plans,
    }.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    benchmark_daily, benchmark_orders, benchmark_metrics = buy_hold(
        analysis, initial_cash=initial_cash, cost_bps=cost_bps
    )
    normalize_frame(benchmark_daily).to_csv(output_root / "buy_hold_daily.csv", index=False, lineterminator="\n")
    normalize_frame(benchmark_orders).to_csv(output_root / "buy_hold_orders.csv", index=False, lineterminator="\n")
    anchor_spec = TimingSpec(
        "CROSS", periods=(int(parameters["parent_anchor_short_period"]), int(parameters["parent_anchor_long_period"])),
        buy_threshold=float(parameters["buy_threshold"]), sell_threshold=float(parameters["sell_threshold"]),
    )
    indicator = prepare_dual_stochrsi_data(raw, anchor_spec)
    indicator = indicator[indicator["date"].between(start, end)][
        ["date", "close", "stochrsi_42", "stochrsi_100"]
    ].reset_index(drop=True)
    normalize_frame(indicator).to_csv(output_root / "indicator_daily.csv", index=False, lineterminator="\n")

    summary = {
        "symbol": symbol, "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(), "analysis_end": end.date().isoformat(),
        "analysis_bars": bars, "strategy_case_count": expected_count,
        "formal_case_count_including_sma": expected_count + 1,
        "parallel_workers": args.workers, "benchmark": benchmark_metrics, "sma200": sma_metrics,
        "max_cross_check_differences": maximum_differences,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol, "cost_bps": cost_bps,
        "cost_model": "predeclared trigger; opening gap at Open, otherwise directed Open-to-Close exact trigger; symmetric adverse cost adjustment",
        "engine": "lib-pybroker 1.2.12 plus independent ledger for every period pair",
        "python": platform.python_version(), "parameters": parameters, "summary": summary,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset, "artifacts": {},
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
        f"Completed {expected_count} period cases + SMA200 at {cost_bps:g} bps in "
        f"{summary['elapsed_seconds']:.2f}s; max ledger difference "
        f"{max(maximum_differences.values(), default=0):.3g}"
    )


if __name__ == "__main__":
    main()
