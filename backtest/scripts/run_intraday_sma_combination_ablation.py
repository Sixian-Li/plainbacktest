#!/usr/bin/env python3
"""Run the full-history QQQ three-rule combination ablation."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.intraday_sma import (
    BUY_FORCED_REENTRY,
    SELL_COST_STOP,
    SELL_SLOW_TREND,
    IntradaySmaSpec,
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_reentry_grid import benchmark_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history"
)
EXPECTED_DISABLED = frozenset(
    (BUY_FORCED_REENTRY, SELL_COST_STOP, SELL_SLOW_TREND)
)


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
    if symbol not in context.config["symbols"] or cost_bps != 0:
        raise ValueError("This experiment requires configured QQQ at exactly 0 bps.")
    parameters = context.config["parameters"]
    disabled = frozenset(str(value) for value in parameters["disabled_signals"])
    if disabled != EXPECTED_DISABLED:
        raise ValueError(f"Disabled signals must be exactly {sorted(EXPECTED_DISABLED)}.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    spec = IntradaySmaSpec.from_parameters(parameters)
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    prepared = prepare_intraday_sma_data(raw, spec)
    analysis = prepared[(prepared["date"] >= start) & (prepared["date"] <= end)].reset_index(drop=True)

    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
        disabled_signals=disabled,
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
        disabled_signals=disabled,
    )
    differences = cross_check(result, actual, reference)
    if reference.orders.empty:
        raise AssertionError("The strategy never received a buy signal.")
    first = reference.orders.iloc[0]
    if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
        raise AssertionError("The strategy did not begin with an ordinary buy signal.")
    observed_signals = set(reference.orders["primary_signal"].astype(str))
    leaked = observed_signals & disabled
    if leaked:
        raise AssertionError(f"Disabled signals produced orders: {sorted(leaked)}")

    first_entry_date = pd.Timestamp(first["date"]).normalize()
    first_entry_fill = float(first["fill_price"])
    evaluation_daily = actual[actual["date"] >= first_entry_date].reset_index(drop=True)
    evaluation_reference = reference.daily[
        reference.daily["date"] >= first_entry_date
    ].reset_index(drop=True)
    evaluation_plans = reference.signal_plans[
        reference.signal_plans["date"] >= first_entry_date
    ].reset_index(drop=True)
    metrics = calculate_metrics(
        evaluation_daily,
        reference.orders,
        reference.trades,
        initial_cash=initial_cash,
    )
    benchmark = benchmark_state(
        analysis,
        first_entry_date=first_entry_date,
        first_entry_fill=first_entry_fill,
        initial_cash=initial_cash,
    )
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_cash,
    )
    signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
    fill_counts = reference.orders["fill_source"].value_counts().to_dict()
    summary: dict[str, Any] = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "initial_position": "flat",
        "initial_cash": initial_cash,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "first_entry_date": first_entry_date.date().isoformat(),
        "first_entry_fill": first_entry_fill,
        "first_entry_signal": str(first["primary_signal"]),
        "disabled_signals": sorted(disabled),
        "inactive_R_forced_rebuy_pct": float(parameters["R_forced_rebuy_pct"]),
        "strategy": metrics,
        "benchmark": benchmark_metrics,
        "primary_signal_counts": signal_counts,
        "fill_source_counts": fill_counts,
        "max_cross_check_differences": differences,
    }

    outputs = {
        "daily.csv": evaluation_daily,
        "reference_daily.csv": evaluation_reference,
        "buy_hold_daily.csv": benchmark,
        "orders.csv": reference.orders,
        "trades.csv": reference.trades,
        "signal_plans.csv": evaluation_plans,
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
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "evaluation_start": first_entry_date.date().isoformat(),
        "initial_cash": initial_cash,
        "initial_position": "flat",
        "parameters": parameters,
        "disabled_signals": sorted(disabled),
        "R_is_behaviorally_inactive": True,
        "signal_timing": "next-session trigger formulas frozen from completed closes through the preceding session",
        "execution_timing": "next regular-session Open if already crossed; exact threshold if touched intraday",
        "one_trade_max_per_day": True,
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "benchmark_metrics": benchmark_metrics,
        "strategy_metrics": metrics,
        "max_cross_check_differences": differences,
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
    print(
        f"Completed combined ablation; first entry {first_entry_date.date()} at {first_entry_fill:.6f}; "
        f"CAGR {metrics['cagr_pct']:.4f}%, Sharpe {metrics['sharpe']:.6f}, orders {metrics['order_count']}"
    )


if __name__ == "__main__":
    main()
