#!/usr/bin/env python3
"""Run the two-window QQQ 2^4 SMA exit-rule ablation."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.reference import run_buy_and_hold_reference
from quantkit.sma_entry_exit_ablation import (
    CASE_FLAGS,
    SELL_RULES,
    EntryExitAblationSpec,
    analysis_slice,
    compiled_pybroker_daily_state,
    prepare_entry_exit_ablation_data,
    run_pybroker_compiled_entry_exit_ablation,
)
from scripts.run_sma_regime_ablation import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.20__26-08-14__qqq_sma_entry_four_exit_ablation_two_periods"
TOLERANCE = 1e-8


def cross_check(result, actual: pd.DataFrame, reference: Any) -> dict[str, float]:
    differences: dict[str, float] = {}
    expected = reference.daily[reference.daily["date"].isin(actual["date"])].reset_index(drop=True)
    if actual["date"].tolist() != expected["date"].tolist():
        raise AssertionError("Daily canonical dates differ.")
    for column in ("cash", "shares", "equity"):
        maximum = float(np.max(np.abs(
            actual[column].to_numpy(float) - expected[column].to_numpy(float)
        )))
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > TOLERANCE:
            raise AssertionError(f"Daily {column} differs: {maximum}")

    observed_orders = result.orders.reset_index(drop=True)
    expected_orders = reference.orders.reset_index(drop=True)
    if len(observed_orders) != len(expected_orders):
        raise AssertionError("Order counts differ.")
    if observed_orders["type"].tolist() != expected_orders["type"].tolist():
        raise AssertionError("Order sides differ.")
    if pd.to_datetime(observed_orders["date"]).tolist() != pd.to_datetime(expected_orders["date"]).tolist():
        raise AssertionError("Order dates differ.")
    for column in ("shares", "fill_price"):
        maximum = float(np.max(np.abs(
            observed_orders[column].to_numpy(float)
            - expected_orders[column].to_numpy(float)
        ))) if len(observed_orders) else 0.0
        differences[f"max_abs_order_{column}_difference"] = maximum
        if maximum > TOLERANCE:
            raise AssertionError(f"Order {column} differs: {maximum}")

    observed_trades = result.trades.reset_index(drop=True)
    expected_trades = reference.trades.reset_index(drop=True)
    if len(observed_trades) != len(expected_trades):
        raise AssertionError("Closed trade counts differ.")
    for column in ("entry_date", "exit_date"):
        if pd.to_datetime(observed_trades[column]).tolist() != pd.to_datetime(expected_trades[column]).tolist():
            raise AssertionError(f"Trade {column} differs.")
    for observed_column, expected_column in (
        ("entry", "entry_price"),
        ("exit", "exit_price"),
        ("shares", "shares"),
        ("pnl", "pnl"),
    ):
        maximum = float(np.max(np.abs(
            observed_trades[observed_column].to_numpy(float)
            - expected_trades[expected_column].to_numpy(float)
        ))) if len(observed_trades) else 0.0
        differences[f"max_abs_trade_{observed_column}_difference"] = maximum
        if maximum > TOLERANCE:
            raise AssertionError(f"Trade {observed_column} differs: {maximum}")
    return differences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol not in context.config["symbols"] or float(args.cost_bps) != 0:
        raise ValueError("Formal experiment requires configured QQQ at zero cost.")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{args.symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == args.symbol
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{args.symbol} is not approved.")
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq(args.symbol)].sort_values("date").reset_index(drop=True)
    prepared = prepare_entry_exit_ablation_data(raw)

    result_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    reference_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    benchmark_daily_frames: list[pd.DataFrame] = []
    benchmark_order_frames: list[pd.DataFrame] = []
    benchmark_rows: list[dict[str, Any]] = []
    differences_all: dict[str, float] = {}
    actual_windows: dict[str, dict[str, Any]] = {}

    for window_id, config in context.config["parameters"]["windows"].items():
        window = analysis_slice(prepared, start=config["requested_start"], end=config["end"])
        actual_windows[window_id] = {
            "requested_start": config["requested_start"],
            "actual_start": pd.Timestamp(window.iloc[0]["date"]).date().isoformat(),
            "actual_end": pd.Timestamp(window.iloc[-1]["date"]).date().isoformat(),
            "bars": len(window),
        }
        for case_id, flags in CASE_FLAGS.items():
            spec = EntryExitAblationSpec.from_case_id(case_id)
            result, reference, engine = run_pybroker_compiled_entry_exit_ablation(
                window, spec, initial_cash=initial_cash, case_id=case_id
            )
            actual = compiled_pybroker_daily_state(result, engine)
            differences = cross_check(result, actual, reference)
            for key, value in differences.items():
                differences_all[f"{window_id}.{case_id}.{key}"] = value
            actual.insert(0, "case_id", case_id)
            actual.insert(0, "window_id", window_id)
            ref_daily = reference.daily.copy()
            ref_daily.insert(0, "window_id", window_id)
            orders = reference.orders.copy()
            orders.insert(0, "window_id", window_id)
            trades = reference.trades.copy()
            trades.insert(0, "window_id", window_id)
            metrics = calculate_metrics(
                reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
            )
            sell_orders = reference.orders[reference.orders["type"].eq("sell")]
            rule_counts = {
                f"{rule.lower()}_matched_count": int(
                    sell_orders["matched_signals"].fillna("").str.split("|").apply(
                        lambda values, rule=rule: rule in values
                    ).sum()
                )
                for rule in SELL_RULES
            }
            result_rows.append(
                {
                    "window_id": window_id,
                    "case_id": case_id,
                    **{f"r{index + 1}_enabled": bool(flag) for index, flag in enumerate(flags)},
                    "enabled_rule_count": int(sum(flags)),
                    "intraday_entry_count": int(
                        reference.orders["fill_source"].eq("intraday_trigger").sum()
                    ),
                    **rule_counts,
                    **metrics,
                    **differences,
                }
            )
            daily_frames.append(actual)
            reference_frames.append(ref_daily)
            order_frames.append(orders)
            trade_frames.append(trades)

        benchmark = run_buy_and_hold_reference(
            window, ExplicitFillPolicy("open", 0), initial_cash=initial_cash
        )
        benchmark_daily = benchmark.daily.copy()
        benchmark_daily.insert(0, "window_id", window_id)
        benchmark_orders = benchmark.orders.copy()
        benchmark_orders.insert(0, "window_id", window_id)
        benchmark_metrics = calculate_metrics(
            benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
        )
        benchmark_rows.append({"window_id": window_id, **benchmark_metrics})
        benchmark_daily_frames.append(benchmark_daily)
        benchmark_order_frames.append(benchmark_orders)

    parameter_results = pd.DataFrame(result_rows)
    outputs = {
        "parameter_results.csv": parameter_results,
        "daily.csv": pd.concat(daily_frames, ignore_index=True),
        "reference_daily.csv": pd.concat(reference_frames, ignore_index=True),
        "orders.csv": pd.concat(order_frames, ignore_index=True),
        "trades.csv": pd.concat(trade_frames, ignore_index=True),
        "benchmark_results.csv": pd.DataFrame(benchmark_rows),
        "buy_hold_daily.csv": pd.concat(benchmark_daily_frames, ignore_index=True),
        "buy_hold_orders.csv": pd.concat(benchmark_order_frames, ignore_index=True),
        "indicators.csv": prepared[
            prepared["date"].between(
                min(pd.Timestamp(item["actual_start"]) for item in actual_windows.values()),
                max(pd.Timestamp(item["actual_end"]) for item in actual_windows.values()),
            )
        ][[
            "date", "symbol", "open", "high", "low", "close", "volume",
            "sma25", "sma30", "sma35", "sma_short_avg", "sma200",
            "close_crossed_above_all_short_lines", "close_above_all_short_lines",
            "close_crossed_above_sma200", "close_above_sma200", "sma200_down_3d",
            "sma_short_avg_change_pct", "sma_short_avg_drop_gt_0_15pct_1d",
            "close_below_sma200", "close_below_sma30", "trigger_buy_sma30_98pct",
            "trigger_buy_sma200_98pct", "trigger_buy_combined", "strategy_ready",
        ]].copy(),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "actual_windows": actual_windows,
        "cases": json_safe(result_rows),
        "benchmarks": json_safe(benchmark_rows),
        "max_cross_check_differences": differences_all,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": args.symbol,
        "cost_bps": 0,
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "independent stateful qualification and dynamic-threshold ledger",
        "python": platform.python_version(),
        "initial_cash_per_case_window": initial_cash,
        "parameters": context.config["parameters"],
        "signal_timing": context.config["strategy"]["signal_time"],
        "execution_timing": context.config["strategy"]["execution_time"],
        "fractional_shares": True,
        "cash_interest": 0,
        "benchmark": context.config["benchmark"],
        "max_cross_check_differences": differences_all,
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
    record_block_complete(context, args.run_id, args.symbol, 0, manifest_path)
    print(parameter_results[[
        "window_id", "case_id", "total_return_pct", "max_drawdown_pct",
        "sharpe", "order_count",
    ]].to_string(index=False))
    print(f"max ledger difference={max(differences_all.values()):.3g}")


if __name__ == "__main__":
    main()
