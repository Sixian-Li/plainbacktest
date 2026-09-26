#!/usr/bin/env python3
"""Run every RKLB dynamic intraday SMA-window/threshold case with two ledgers."""

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
from quantkit.intraday_sma_threshold import (
    IntradaySmaThresholdSpec,
    prepare_intraday_threshold_data,
    run_pybroker_intraday_threshold,
    run_reference_intraday_threshold,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma200_threshold_grid import benchmark_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid"
)
LEDGER_TOLERANCE = 1e-6


def _case_id(symbol: str, cost_bps: float, window: int, sell_pct: float, buy_pct: float) -> str:
    return (
        f"{symbol}_c{int(cost_bps):02d}_n{window:03d}_"
        f"s{int(round(sell_pct)):02d}_b{int(round(buy_pct)):02d}"
    )


def _metadata(frame: pd.DataFrame, values: dict[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    for index, (name, value) in enumerate(values.items()):
        result.insert(index, name, value)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    symbol = str(args.symbol)
    cost_bps = float(args.cost_bps)
    if symbol not in context.config["symbols"]:
        raise ValueError(f"Unconfigured symbol: {symbol}")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"Unconfigured cost: {cost_bps:g}")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)
    initial_cash = float(context.config["initial_cash"])
    parameters = context.config["parameters"]
    windows = tuple(int(value) for value in parameters["sma_windows"]["values"])
    sell_values = tuple(float(value) for value in parameters["sell_below_sma_pct"])
    buy_values = tuple(float(value) for value in parameters["buy_above_sma_pct"])
    expected_count = len(windows) * len(sell_values) * len(buy_values)
    if expected_count != int(parameters["total_case_count_per_cost"]):
        raise ValueError("Configured case count does not match the parameter grid.")

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == symbol
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved.")
    raw = pd.read_csv(canonical_path)
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    max_window = max(windows)
    prepared_max = prepare_intraday_threshold_data(raw, max_window)
    first_index = max_window - 1
    start = pd.Timestamp(prepared_max.iloc[first_index]["date"])
    end = pd.Timestamp(parameters["analysis_end"])
    if end not in set(raw["date"]):
        raise ValueError(f"Configured end is not a canonical session: {end.date()}")
    analysis_prices = raw[(raw["date"] >= start) & (raw["date"] <= end)].copy().reset_index(drop=True)
    benchmark = benchmark_state(analysis_prices, initial_cash=initial_cash, cost_bps=cost_bps)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    normalize_frame(benchmark).to_csv(
        output_root / "buy_hold_daily.csv", index=False, lineterminator="\n"
    )

    case_count = expected_count
    dates = analysis_prices["date"].to_numpy(dtype="datetime64[D]")
    equity = np.empty((case_count, len(dates)), dtype=np.float64)
    cash = np.empty_like(equity)
    shares = np.empty_like(equity)
    case_ids = np.empty(case_count, dtype="U40")
    metric_rows: list[dict[str, Any]] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    max_differences: dict[str, float] = {}
    started = time.perf_counter()
    index = 0
    for window in windows:
        for sell_pct in sell_values:
            for buy_pct in buy_values:
                case_id = _case_id(symbol, cost_bps, window, sell_pct, buy_pct)
                spec = IntradaySmaThresholdSpec(
                    a_pct=sell_pct,
                    b_pct=buy_pct,
                    window=window,
                    cost_bps=cost_bps,
                )
                pybroker_run = run_pybroker_intraday_threshold(
                    raw,
                    spec,
                    analysis_start=start,
                    analysis_end=end,
                    initial_cash=initial_cash,
                )
                result = pybroker_run.pybroker_result
                engine = raw[
                    (raw["date"] >= pybroker_run.engine_start) & (raw["date"] <= end)
                ].copy()
                actual = pybroker_daily_state(result, engine)
                actual = actual[actual["date"] >= start].reset_index(drop=True)
                reference = run_reference_intraday_threshold(
                    raw,
                    spec,
                    analysis_start=start,
                    analysis_end=end,
                    initial_cash=initial_cash,
                )
                differences = cross_check(
                    result, actual, reference, tolerance=LEDGER_TOLERANCE
                )
                for name, value in differences.items():
                    max_differences[name] = max(max_differences.get(name, 0.0), float(value))
                if not reference.orders.empty and reference.orders.groupby("date").size().max() > 1:
                    raise AssertionError(f"{case_id} traded more than once in a session.")
                metrics = calculate_metrics(
                    actual, reference.orders, reference.trades, initial_cash=initial_cash
                )
                signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
                fill_counts = reference.orders["fill_source"].value_counts().to_dict()
                metric_rows.append(
                    {
                        "case_id": case_id,
                        "case_index": index,
                        "symbol": symbol,
                        "cost_bps": cost_bps,
                        "sma_window": window,
                        "sell_below_sma_pct": sell_pct,
                        "buy_above_sma_pct": buy_pct,
                        **metrics,
                        "gap_fill_count": int(fill_counts.get("open_gap", 0)),
                        "intraday_trigger_count": int(fill_counts.get("intraday_trigger", 0)),
                        "buy_signal_count": int(signal_counts.get("BUY_SMA200_THRESHOLD", 0)),
                        "sell_signal_count": int(signal_counts.get("SELL_SMA200_THRESHOLD", 0)),
                        "benchmark_final_equity": benchmark_metrics["final_equity"],
                        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
                        "benchmark_sharpe": benchmark_metrics["sharpe"],
                        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
                        "excess_cagr_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
                        **differences,
                    }
                )
                metadata = {
                    "case_id": case_id,
                    "case_index": index,
                    "sma_window": window,
                    "sell_below_sma_pct": sell_pct,
                    "buy_above_sma_pct": buy_pct,
                    "cost_bps": cost_bps,
                }
                if not reference.orders.empty:
                    order_frames.append(_metadata(reference.orders, metadata))
                if not reference.trades.empty:
                    trade_frames.append(_metadata(reference.trades, metadata))
                case_ids[index] = case_id
                equity[index] = actual["equity"].to_numpy(float)
                cash[index] = actual["cash"].to_numpy(float)
                shares[index] = actual["shares"].to_numpy(float)
                index += 1
                if index % 25 == 0 or index == case_count:
                    elapsed = time.perf_counter() - started
                    print(
                        f"{symbol} {cost_bps:g}bps: {index}/{case_count} "
                        f"({elapsed:.1f}s, {elapsed/index:.3f}s/case)",
                        flush=True,
                    )

    results = pd.DataFrame(metric_rows).sort_values("case_index")
    results.to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    normalize_frame(orders).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    normalize_frame(trades).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_state.npz",
        dates=dates,
        case_id=case_ids,
        equity=equity,
        cash=cash,
        shares=shares,
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "gap-through at Open or exact intraday dynamic-SMA trigger; symmetric adverse fill adjustment",
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.intraday_sma_threshold independent ledger",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(dates),
        "warmup_bars": first_index,
        "case_count": case_count,
        "parameters": parameters,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": max_differences,
        "runtime_seconds": time.perf_counter() - started,
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
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    best = results.loc[results["cagr_pct"].idxmax()]
    print(
        f"Completed {case_count} cases in {manifest['runtime_seconds']:.1f}s; "
        f"best n={int(best['sma_window'])}, sell={best['sell_below_sma_pct']:g}%, "
        f"buy={best['buy_above_sma_pct']:g}%, CAGR={best['cagr_pct']:.4f}%"
    )


if __name__ == "__main__":
    main()
