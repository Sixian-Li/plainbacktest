#!/usr/bin/env python3
"""Run the QQQ single-StochRSI five-year rolling drift grid."""

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

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_stochrsi_cross_threshold_grid import run_case


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70b.2__26-08-29__qqq_single_stochrsi_rolling_drift_2005_2020"
_STATE: dict[str, Any] = {}


def period_values(start: int, end: int, step: int) -> list[int]:
    if step <= 0 or end < start or (end - start) % step:
        raise ValueError("period grid must have positive, exactly divisible bounds")
    return list(range(start, end + 1, step))


def rolling_windows(
    raw: pd.DataFrame, *, start_year: int, last_start_year: int, interval_years: int
) -> list[dict[str, object]]:
    dates = pd.DatetimeIndex(pd.to_datetime(raw["date"]).sort_values().unique())
    windows: list[dict[str, object]] = []
    for year in range(start_year, last_start_year + 1):
        start_candidates = dates[dates >= pd.Timestamp(year=year, month=1, day=1)]
        end_boundary = pd.Timestamp(year=year + interval_years, month=1, day=1)
        end_candidates = dates[dates < end_boundary]
        if start_candidates.empty or end_candidates.empty:
            raise ValueError(f"QQQ sessions do not cover rolling window {year}")
        start, end = pd.Timestamp(start_candidates[0]), pd.Timestamp(end_candidates[-1])
        if end < start:
            raise ValueError(f"invalid rolling window {year}")
        windows.append({
            "window_id": f"W{year}_{year + interval_years}",
            "label": f"{year}–{year + interval_years}",
            "start_year": year,
            "end_label_year": year + interval_years,
            "start": start,
            "end": end,
        })
    return windows


def configure_worker(raw: pd.DataFrame, windows: list[dict[str, object]], parameters: dict, cash: float) -> None:
    global _STATE
    _STATE = {"raw": raw, "windows": windows, "parameters": parameters, "cash": cash}


def _identified(frame: pd.DataFrame, case_id: str, window_id: str, period: int) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    result.insert(1, "window_id", window_id)
    result.insert(2, "period", period)
    return result


def run_period(period: int) -> list[dict[str, Any]]:
    state = _STATE
    parameters, cash = state["parameters"], state["cash"]
    spec = TimingSpec(
        "CROSS",
        cost_bps=float(parameters["screen_cost_bps"]),
        periods=(period,),
        buy_threshold=float(parameters["buy_threshold"]),
        sell_threshold=float(parameters["sell_threshold"]),
    )
    prepared = prepare_dual_stochrsi_data(state["raw"], spec)
    outputs: list[dict[str, Any]] = []
    for window in state["windows"]:
        metrics, actual, reference_daily, orders, trades, checked = run_case(
            prepared,
            spec,
            start=pd.Timestamp(window["start"]),
            end=pd.Timestamp(window["end"]),
            initial_cash=cash,
        )
        plans = checked.pop("signal_plans")
        case_id = f"S{period:03d}__{window['window_id']}"
        held_bars = int((actual["shares"] > 0).sum())
        holding_cagr = (
            (float(metrics["final_equity"]) / cash) ** (252.0 / held_bars) - 1.0
        ) * 100.0 if held_bars else np.nan
        outputs.append({
            "case_id": case_id,
            "window_id": str(window["window_id"]),
            "window_label": str(window["label"]),
            "window_start": pd.Timestamp(window["start"]).date().isoformat(),
            "window_end": pd.Timestamp(window["end"]).date().isoformat(),
            "start_year": int(window["start_year"]),
            "period": period,
            "metrics": {**metrics, "held_bars": held_bars, "holding_period_cagr_pct": holding_cagr},
            "dates": actual["date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
            "actual_cash": actual["cash"].to_numpy(float),
            "actual_shares": actual["shares"].to_numpy(float),
            "actual_equity": actual["equity"].to_numpy(float),
            "reference_equity": reference_daily["equity"].to_numpy(float),
            "plan_trigger": pd.to_numeric(plans["trigger"], errors="coerce").to_numpy(float),
            "plan_side": plans["side"].eq("sell").to_numpy(np.int8),
            "plan_eligible": plans["eligible"].to_numpy(bool),
            "plan_filled": plans["filled"].to_numpy(bool),
            "orders": _identified(orders, case_id, str(window["window_id"]), period),
            "trades": _identified(trades, case_id, str(window["window_id"]), period),
            "differences": {name: float(value) for name, value in checked.items()},
        })
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This frozen experiment requires QQQ only")
    if float(args.cost_bps) != float(context.config["parameters"]["screen_cost_bps"]):
        raise ValueError("Cost does not match the frozen 5 bps experiment")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest = json.loads((WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    parameters = context.config["parameters"]
    periods = period_values(
        int(parameters["period_start"]), int(parameters["period_end"]), int(parameters["period_step"])
    )
    windows = rolling_windows(
        raw,
        start_year=int(parameters["window_start_year"]),
        last_start_year=int(parameters["window_last_start_year"]),
        interval_years=int(parameters["window_interval_years"]),
    )
    if len(periods) != int(parameters["period_count"]) or len(windows) != int(parameters["window_count"]):
        raise RuntimeError("Frozen period or window count is inconsistent")
    if len(periods) * len(windows) != int(parameters["combination_count"]):
        raise RuntimeError("Frozen combination count is inconsistent")

    started = time.perf_counter()
    configure_worker(raw, windows, parameters, float(context.config["initial_cash"]))
    executor = None
    if args.workers == 1:
        batches = map(run_period, periods)
    else:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("fork")
        )
        batches = executor.map(run_period, periods, chunksize=1)
    outputs: list[dict[str, Any]] = []
    try:
        for batch in batches:
            outputs.extend(batch)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    outputs.sort(key=lambda row: (row["start_year"], row["period"]))
    if len(outputs) != int(parameters["combination_count"]):
        raise RuntimeError("Grid output is incomplete")

    records, order_frames, trade_frames = [], [], []
    maximum_differences: dict[str, float] = {}
    offsets = [0]
    flat: dict[str, list[np.ndarray]] = {
        name: [] for name in (
            "dates", "actual_cash", "actual_shares", "actual_equity", "reference_equity",
            "plan_trigger", "plan_side", "plan_eligible", "plan_filled",
        )
    }
    for output in outputs:
        metrics = output["metrics"]
        records.append({
            "case_id": output["case_id"], "window_id": output["window_id"],
            "window_label": output["window_label"], "window_start": output["window_start"],
            "window_end": output["window_end"], "start_year": output["start_year"],
            "period": output["period"], **metrics, **output["differences"],
        })
        if not output["orders"].empty:
            order_frames.append(output["orders"])
        if not output["trades"].empty:
            trade_frames.append(output["trades"])
        length = len(output["dates"])
        offsets.append(offsets[-1] + length)
        for name in flat:
            flat[name].append(output[name])
        for name, value in output["differences"].items():
            maximum_differences[name] = max(maximum_differences.get(name, 0.0), float(value))

    results = pd.DataFrame(records)
    normalize_frame(results).to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(order_frames, ignore_index=True)).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(trade_frames, ignore_index=True)).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_signal_state.npz",
        case_ids=np.asarray([row["case_id"] for row in outputs], dtype="U24"),
        window_ids=np.asarray([row["window_id"] for row in outputs], dtype="U12"),
        periods=np.asarray([row["period"] for row in outputs], dtype=np.int16),
        offsets=np.asarray(offsets, dtype=np.int64),
        **{name: np.concatenate(values) for name, values in flat.items()},
    )

    benchmark_rows, benchmark_daily = [], []
    for window in windows:
        analysis = raw[raw["date"].between(window["start"], window["end"])].reset_index(drop=True)
        daily, orders, metrics = buy_hold(
            analysis, initial_cash=float(context.config["initial_cash"]), cost_bps=float(args.cost_bps)
        )
        benchmark_rows.append({
            "window_id": window["window_id"], "window_label": window["label"],
            "window_start": pd.Timestamp(window["start"]).date().isoformat(),
            "window_end": pd.Timestamp(window["end"]).date().isoformat(),
            "start_year": window["start_year"], **metrics,
        })
        current = daily.copy()
        current.insert(0, "window_id", window["window_id"])
        benchmark_daily.append(current)
    normalize_frame(pd.DataFrame(benchmark_rows)).to_csv(output_root / "buy_hold_results.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(benchmark_daily, ignore_index=True)).to_csv(output_root / "buy_hold_daily.csv", index=False, lineterminator="\n")

    summary = {
        "symbol": "QQQ", "cost_bps": float(args.cost_bps),
        "period_count": len(periods), "window_count": len(windows),
        "strategy_case_count": len(outputs), "parallel_workers": args.workers,
        "windows": [{**window, "start": pd.Timestamp(window["start"]).date().isoformat(), "end": pd.Timestamp(window["end"]).date().isoformat()} for window in windows],
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
        "symbol": "QQQ", "cost_bps": float(args.cost_bps),
        "engine": "lib-pybroker 1.2.12 plus quantkit.dual_stochrsi_timing independent ledger for every case",
        "python": platform.python_version(), "parameters": parameters,
        "summary": summary, "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset, "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_block_complete(context, args.run_id, "QQQ", float(args.cost_bps), manifest_path)
    print(json.dumps({"cases": len(outputs), "seconds": summary["elapsed_seconds"], "max_difference": max(maximum_differences.values(), default=0.0)}))


if __name__ == "__main__":
    main()
