#!/usr/bin/env python3
"""Run the QQQ dual-StochRSI CROSS buy/sell threshold grid."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import platform
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import (
    TimingSpec,
    prepare_dual_stochrsi_data,
    prepare_sma200_data,
    run_pybroker,
    run_reference,
)
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.70a.1__26-08-24__qqq_stochrsi_cross_grid_full_history"
)
TOLERANCE = 1e-6
_GRID_WORKER_STATE: dict[str, Any] = {}


def threshold_values(start: float, end: float, step: float) -> list[float]:
    first, last, increment = Decimal(str(start)), Decimal(str(end)), Decimal(str(step))
    if increment <= 0 or last < first:
        raise ValueError("threshold grid requires positive step and end >= start")
    count = int((last - first) / increment)
    values = [float(first + increment * index) for index in range(count + 1)]
    if Decimal(str(values[-1])) != last:
        raise ValueError("threshold bounds must be exactly divisible by step")
    return values


def case_id(buy_threshold: float, sell_threshold: float) -> str:
    return f"B{int(round(buy_threshold * 100)):03d}_S{int(round(sell_threshold * 100)):03d}"


def execution_columns(prepared: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "symbol", "date", "open", "high", "low", "close", "volume",
        "buy_trigger", "sell_trigger", "buy_direction", "sell_direction",
        "buy_eligible", "sell_eligible", "buy_signal", "sell_signal",
    ]
    return prepared[columns].copy()


def run_case(
    prepared: pd.DataFrame,
    spec: TimingSpec,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    reference = run_reference(
        prepared, spec, analysis_start=start, analysis_end=end, initial_cash=initial_cash
    )
    broker = run_pybroker(
        prepared, spec, analysis_start=start, analysis_end=end, initial_cash=initial_cash
    )
    engine = prepared[
        (prepared["date"] >= broker.engine_start) & (prepared["date"] <= end)
    ].copy()
    actual = pybroker_daily_state(broker.pybroker_result, engine)
    actual = actual[actual["date"] >= start].reset_index(drop=True)
    differences = cross_check(
        broker.pybroker_result, actual, reference, tolerance=TOLERANCE
    )
    metrics = calculate_metrics(
        actual, reference.orders, reference.trades, initial_cash=initial_cash
    )
    return metrics, actual, reference.daily, reference.orders, reference.trades, {
        **differences,
        "signal_plans": reference.signal_plans,
    }


def prepare_threshold_cache(
    raw: pd.DataFrame,
    periods: tuple[int, int],
    buy_values: list[float],
    sell_values: list[float],
) -> tuple[pd.DataFrame, dict[float, tuple[np.ndarray, np.ndarray]], dict[float, tuple[np.ndarray, np.ndarray]]]:
    anchor = prepare_dual_stochrsi_data(
        raw,
        TimingSpec("CROSS", periods=periods, buy_threshold=0.2, sell_threshold=0.8),
    )
    buy_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for threshold in buy_values:
        prepared = prepare_dual_stochrsi_data(
            raw,
            TimingSpec("CROSS", periods=periods, buy_threshold=threshold, sell_threshold=0.8),
        )
        buy_cache[threshold] = (
            prepared["buy_trigger"].to_numpy(float),
            prepared["buy_eligible"].to_numpy(bool),
        )
    sell_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for threshold in sell_values:
        prepared = prepare_dual_stochrsi_data(
            raw,
            TimingSpec("CROSS", periods=periods, buy_threshold=0.2, sell_threshold=threshold),
        )
        sell_cache[threshold] = (
            prepared["sell_trigger"].to_numpy(float),
            prepared["sell_eligible"].to_numpy(bool),
        )
    return execution_columns(anchor), buy_cache, sell_cache


def configure_grid_worker(
    prepared: pd.DataFrame,
    buy_cache: dict[float, tuple[np.ndarray, np.ndarray]],
    sell_cache: dict[float, tuple[np.ndarray, np.ndarray]],
    *,
    periods: tuple[int, int],
    cost_bps: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> None:
    global _GRID_WORKER_STATE
    _GRID_WORKER_STATE = {
        "prepared": prepared,
        "buy_cache": buy_cache,
        "sell_cache": sell_cache,
        "periods": periods,
        "cost_bps": cost_bps,
        "start": start,
        "end": end,
        "initial_cash": initial_cash,
    }


def run_grid_worker(pair: tuple[float, float]) -> dict[str, Any]:
    buy_threshold, sell_threshold = pair
    state = _GRID_WORKER_STATE
    prepared = state["prepared"]
    prepared["buy_trigger"] = state["buy_cache"][buy_threshold][0]
    prepared["buy_eligible"] = state["buy_cache"][buy_threshold][1]
    prepared["sell_trigger"] = state["sell_cache"][sell_threshold][0]
    prepared["sell_eligible"] = state["sell_cache"][sell_threshold][1]
    current_id = case_id(buy_threshold, sell_threshold)
    spec = TimingSpec(
        "CROSS",
        cost_bps=state["cost_bps"],
        periods=state["periods"],
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
    )
    metrics, actual, reference_daily, orders, trades, checked = run_case(
        prepared,
        spec,
        start=state["start"],
        end=state["end"],
        initial_cash=state["initial_cash"],
    )
    plans = checked.pop("signal_plans")
    differences = {name: float(value) for name, value in checked.items()}
    return {
        "case_id": current_id,
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
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
        "differences": differences,
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
    initial_cash = float(context.config["initial_cash"])
    periods = tuple(int(value) for value in parameters["stochrsi_periods"])
    buy_values = threshold_values(
        parameters["buy_threshold_start"], parameters["buy_threshold_end"], parameters["threshold_step"]
    )
    sell_values = threshold_values(
        parameters["sell_threshold_start"], parameters["sell_threshold_end"], parameters["threshold_step"]
    )
    expected_count = int(parameters["combination_count_per_cost"])
    if len(buy_values) * len(sell_values) != expected_count:
        raise RuntimeError("Frozen threshold grid count is inconsistent")

    started = time.perf_counter()
    prepared, buy_cache, sell_cache = prepare_threshold_cache(
        raw, periods, buy_values, sell_values
    )
    analysis_bars = len(analysis)
    case_ids = np.empty(expected_count, dtype="U9")
    actual_cash = np.empty((expected_count, analysis_bars), dtype=np.float64)
    actual_shares = np.empty_like(actual_cash)
    actual_equity = np.empty_like(actual_cash)
    reference_equity = np.empty_like(actual_cash)
    plan_trigger = np.empty_like(actual_cash)
    plan_side = np.empty((expected_count, analysis_bars), dtype=np.int8)
    plan_eligible = np.empty((expected_count, analysis_bars), dtype=np.bool_)
    plan_filled = np.empty((expected_count, analysis_bars), dtype=np.bool_)
    records: list[dict] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    maximum_differences: dict[str, float] = {}

    configure_grid_worker(
        prepared, buy_cache, sell_cache, periods=periods, cost_bps=cost_bps,
        start=start, end=end, initial_cash=initial_cash,
    )
    pairs = [(buy, sell) for buy in buy_values for sell in sell_values]
    if args.workers == 1:
        case_results = map(run_grid_worker, pairs)
        executor = None
    else:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers,
            mp_context=multiprocessing.get_context("fork"),
        )
        case_results = executor.map(run_grid_worker, pairs, chunksize=1)
    try:
        for row_index, output in enumerate(case_results):
            current_id = str(output["case_id"])
            buy_threshold = float(output["buy_threshold"])
            sell_threshold = float(output["sell_threshold"])
            metrics = output["metrics"]
            orders = output["orders"]
            trades = output["trades"]
            differences = output["differences"]
            arrays = (
                output["actual_cash"], output["actual_shares"], output["actual_equity"],
                output["reference_equity"], output["plan_trigger"], output["plan_side"],
                output["plan_eligible"], output["plan_filled"],
            )
            if any(len(array) != analysis_bars for array in arrays):
                raise AssertionError(f"Daily state length mismatch for {current_id}")
            case_ids[row_index] = current_id
            actual_cash[row_index] = output["actual_cash"]
            actual_shares[row_index] = output["actual_shares"]
            actual_equity[row_index] = output["actual_equity"]
            reference_equity[row_index] = output["reference_equity"]
            plan_trigger[row_index] = output["plan_trigger"]
            plan_side[row_index] = output["plan_side"]
            plan_eligible[row_index] = output["plan_eligible"]
            plan_filled[row_index] = output["plan_filled"]
            records.append({
                "case_id": current_id,
                "buy_threshold": buy_threshold,
                "sell_threshold": sell_threshold,
                **metrics,
                "fill_source_counts": json.dumps(
                    orders["fill_source"].value_counts().to_dict(), sort_keys=True
                ),
                **differences,
            })
            if not orders.empty:
                current = orders.copy()
                current.insert(0, "case_id", current_id)
                current.insert(1, "buy_threshold", buy_threshold)
                current.insert(2, "sell_threshold", sell_threshold)
                order_frames.append(current)
            if not trades.empty:
                current = trades.copy()
                current.insert(0, "case_id", current_id)
                current.insert(1, "buy_threshold", buy_threshold)
                current.insert(2, "sell_threshold", sell_threshold)
                trade_frames.append(current)
            for name, value in differences.items():
                maximum_differences[name] = max(maximum_differences.get(name, 0.0), value)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    results = pd.DataFrame(records)
    if len(results) != expected_count or results["case_id"].nunique() != expected_count:
        raise RuntimeError("Grid output is incomplete or has duplicate case IDs")
    normalize_frame(results).to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    normalize_frame(orders).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    normalize_frame(trades).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_signal_state.npz",
        dates=analysis["date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
        case_ids=case_ids,
        actual_cash=actual_cash,
        actual_shares=actual_shares,
        actual_equity=actual_equity,
        reference_equity=reference_equity,
        plan_trigger=plan_trigger,
        plan_side=plan_side,
        plan_eligible=plan_eligible,
        plan_filled=plan_filled,
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
        "sma200_daily.csv": sma_daily,
        "sma200_reference_daily.csv": sma_reference,
        "sma200_orders.csv": sma_orders,
        "sma200_trades.csv": sma_trades,
        "sma200_signal_plans.csv": sma_plans,
    }.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    benchmark_daily, benchmark_orders, benchmark_metrics = buy_hold(
        analysis, initial_cash=initial_cash, cost_bps=cost_bps
    )
    normalize_frame(benchmark_daily).to_csv(
        output_root / "buy_hold_daily.csv", index=False, lineterminator="\n"
    )
    normalize_frame(benchmark_orders).to_csv(
        output_root / "buy_hold_orders.csv", index=False, lineterminator="\n"
    )
    indicator = prepare_dual_stochrsi_data(
        raw, TimingSpec("CROSS", periods=periods, buy_threshold=0.2, sell_threshold=0.8)
    )
    indicator = indicator[indicator["date"].between(start, end)][
        ["date", "close", *[f"stochrsi_{period}" for period in periods]]
    ].reset_index(drop=True)
    normalize_frame(indicator).to_csv(
        output_root / "indicator_daily.csv", index=False, lineterminator="\n"
    )

    summary = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": analysis_bars,
        "strategy_case_count": expected_count,
        "formal_case_count_including_sma": expected_count + 1,
        "parallel_workers": args.workers,
        "benchmark": benchmark_metrics,
        "sma200": sma_metrics,
        "max_cross_check_differences": maximum_differences,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    block_manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "predeclared trigger; opening gap at Open, otherwise directed Open-to-Close exact trigger; symmetric adverse cost adjustment",
        "engine": "lib-pybroker 1.2.12 plus quantkit.dual_stochrsi_timing independent ledger for every case",
        "python": platform.python_version(),
        "parameters": parameters,
        "summary": summary,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            block_manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {expected_count} CROSS cases + SMA200 at {cost_bps:g} bps in "
        f"{summary['elapsed_seconds']:.2f}s; max ledger difference "
        f"{max(maximum_differences.values(), default=0):.3g}"
    )


if __name__ == "__main__":
    main()
