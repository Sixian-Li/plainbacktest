#!/usr/bin/env python3
"""Run the fixed QQQ dual-StochRSI comparison and its SMA200 baseline."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

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
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing"
TOLERANCE = 1e-6


def tagged(frame: pd.DataFrame, case_id: str, label: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    result.insert(1, "label", label)
    return result


def buy_hold(
    analysis: pd.DataFrame, *, initial_cash: float, cost_bps: float
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    entry_open = float(analysis.iloc[0]["open"])
    entry_fill = entry_open * (1 + cost_bps / 10_000)
    shares = initial_cash / entry_fill
    daily = analysis[["date", "symbol", "open", "close"]].copy()
    daily["cash"] = 0.0
    daily["shares"] = shares
    daily["equity"] = shares * daily["close"].astype(float)
    daily["is_long"] = 1
    orders = pd.DataFrame(
        [{
            "symbol": str(analysis.iloc[0]["symbol"]), "type": "buy",
            "date": pd.Timestamp(analysis.iloc[0]["date"]), "shares": shares,
            "fill_price": entry_fill, "raw_fill_price": entry_open,
            "primary_signal": "BUY_HOLD_ENTRY", "theoretical_trigger": entry_open,
            "fill_source": "analysis_start_open",
        }]
    )
    metrics = calculate_metrics(daily, orders, pd.DataFrame(), initial_cash=initial_cash)
    return daily, orders, metrics


def formal_case(
    raw: pd.DataFrame,
    spec: TimingSpec,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> tuple[dict, dict[str, pd.DataFrame], dict[str, float]]:
    prepared = (
        prepare_sma200_data(raw, spec)
        if spec.mode == "SMA200"
        else prepare_dual_stochrsi_data(raw, spec)
    )
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
    if not reference.orders.empty and reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{spec.mode} traded more than once in one session")
    metrics = calculate_metrics(
        actual, reference.orders, reference.trades, initial_cash=initial_cash
    )
    label = {
        "LEVEL": "双周期低于0.2/高于0.8",
        "EXTREME": "双周期等于0/等于1",
        "CROSS": "双周期上穿0.2/下穿0.8",
        "SMA200": "SMA200 +3%/-3%",
    }[spec.mode]
    case_id = spec.mode
    record = {
        "case_id": case_id,
        "label": label,
        "mode": spec.mode,
        **metrics,
        "fill_source_counts": json.dumps(
            reference.orders["fill_source"].value_counts().to_dict(),
            ensure_ascii=False, sort_keys=True,
        ),
        **differences,
    }
    frames = {
        "daily": tagged(actual, case_id, label),
        "reference_daily": tagged(reference.daily, case_id, label),
        "orders": tagged(reference.orders, case_id, label),
        "trades": tagged(reference.trades, case_id, label),
        "signal_plans": tagged(reference.signal_plans, case_id, label),
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
    symbol, cost_bps = str(args.symbol), float(args.cost_bps)
    if symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This experiment requires QQQ only")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("Cost scenario is not frozen in experiment.json")
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

    started = time.perf_counter()
    records: list[dict] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for mode in ("LEVEL", "EXTREME", "CROSS", "SMA200"):
        spec = TimingSpec(
            mode, cost_bps=cost_bps,
            periods=tuple(int(value) for value in parameters["stochrsi_periods"]),
            buy_threshold=float(parameters["buy_threshold"]),
            sell_threshold=float(parameters["sell_threshold"]),
            sma_window=int(parameters["sma_baseline_window"]),
            sma_buy_buffer_pct=float(parameters["sma_baseline_buy_buffer_pct"]),
            sma_sell_buffer_pct=float(parameters["sma_baseline_sell_buffer_pct"]),
        )
        record, frames, differences = formal_case(
            raw, spec, start=start, end=end, initial_cash=initial_cash
        )
        records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for name, value in differences.items():
            maximum_differences[name] = max(maximum_differences.get(name, 0), float(value))

    benchmark_daily, benchmark_orders, benchmark_metrics = buy_hold(
        analysis, initial_cash=initial_cash, cost_bps=cost_bps
    )
    results = pd.DataFrame(records)
    indicator = prepare_dual_stochrsi_data(raw, TimingSpec("LEVEL"))
    indicator = indicator[indicator["date"].between(start, end)][
        ["date", "close", "stochrsi_42", "stochrsi_100"]
    ].reset_index(drop=True)
    outputs = {
        "parameter_results.csv": results,
        "daily.csv": pd.concat(frame_groups["daily"], ignore_index=True),
        "reference_daily.csv": pd.concat(frame_groups["reference_daily"], ignore_index=True),
        "orders.csv": pd.concat(frame_groups["orders"], ignore_index=True),
        "trades.csv": pd.concat(frame_groups["trades"], ignore_index=True),
        "signal_plans.csv": pd.concat(frame_groups["signal_plans"], ignore_index=True),
        "buy_hold_daily.csv": benchmark_daily,
        "buy_hold_orders.csv": benchmark_orders,
        "indicator_daily.csv": indicator,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    summary = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "strategy_case_count": 3,
        "formal_case_count_including_sma": 4,
        "benchmark": benchmark_metrics,
        "cases": json_safe(results.to_dict("records")),
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
        "engine": "lib-pybroker 1.2.12 plus quantkit.dual_stochrsi_timing independent ledger",
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
                "bytes": path.stat().st_size, "sha256": sha256(path)
            }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed QQQ dual-StochRSI + SMA200 at {cost_bps:g} bps in "
        f"{summary['elapsed_seconds']:.2f}s; max ledger difference "
        f"{max(maximum_differences.values(), default=0):.3g}"
    )


if __name__ == "__main__":
    main()
