#!/usr/bin/env python3
"""Train a peak drawdown threshold, freeze it, and run an OOS QQQ test."""

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
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference
from quantkit.sma_regime import SmaRegimeSpec
from quantkit.sma_regime_drawdown import (
    STOP_EXIT_REASON,
    PeakDrawdownSpec,
    prepare_peak_drawdown_data,
    run_pybroker_peak_drawdown,
    run_reference_peak_drawdown,
)
from scripts.run_sma_regime_ablation import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos"


def window_slice(
    prepared: pd.DataFrame,
    *,
    requested_start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    window = prepared[
        prepared["regime_ready"].astype(bool)
        & (prepared["date"] >= requested_start)
        & (prepared["date"] <= end)
    ].copy()
    if window.empty:
        raise ValueError(f"Window {requested_start.date()} through {end.date()} is empty.")
    return window.reset_index(drop=True)


def selection_key(row: pd.Series, anchor_pct: float) -> tuple[float, float, float, float, float]:
    threshold = float(row["stop_pct"])
    return (
        float(row["max_drawdown_pct"]),
        float(row["total_return_pct"]),
        -float(row["order_count"]),
        -abs(threshold - anchor_pct),
        -threshold,
    )


def select_training_threshold(results: pd.DataFrame, anchor_pct: float) -> pd.Series:
    if results.empty:
        raise ValueError("Training grid is empty.")
    return max((row for _, row in results.iterrows()), key=lambda row: selection_key(row, anchor_pct))


def execute_case(
    data: pd.DataFrame,
    *,
    window_id: str,
    case_id: str,
    stop_pct: float | None,
    policy: ExplicitFillPolicy,
    initial_cash: float,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    spec = PeakDrawdownSpec(stop_pct)
    result = run_pybroker_peak_drawdown(data, spec, policy, initial_cash=initial_cash)
    actual = pybroker_daily_state(result, data)
    reference = run_reference_peak_drawdown(
        data,
        spec,
        policy,
        initial_cash=initial_cash,
        case_id=case_id,
    )
    differences = cross_check(result, actual, reference)
    actual.insert(0, "case_id", case_id)
    actual.insert(0, "window_id", window_id)
    reference_daily = reference.daily.copy()
    reference_daily.insert(0, "window_id", window_id)
    orders = reference.orders.copy()
    orders.insert(0, "window_id", window_id)
    trades = reference.trades.copy()
    trades.insert(0, "window_id", window_id)
    metrics = calculate_metrics(
        actual,
        reference.orders,
        reference.trades,
        initial_cash=initial_cash,
    )
    metrics.update(
        {
            "window_id": window_id,
            "case_id": case_id,
            "stop_pct": stop_pct,
            "stop_exit_count": int(
                (reference.orders["reason"] == STOP_EXIT_REASON).sum()
            ),
            **differences,
        }
    )
    return metrics, actual, reference_daily, orders, trades, differences


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
    if symbol not in context.config["symbols"]:
        raise ValueError(f"Symbol {symbol!r} is not configured.")
    if cost_bps != 0 or cost_bps not in [
        float(value) for value in context.config["cost_scenarios_bps_per_side"]
    ]:
        raise ValueError("This experiment requires exactly zero formal transaction cost.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == symbol
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    sma_spec = SmaRegimeSpec.from_parameters(parameters)
    initial_cash = float(context.config["initial_cash"])
    policy = ExplicitFillPolicy("open", cost_bps)
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    prepared = prepare_peak_drawdown_data(raw, sma_spec)
    train = window_slice(
        prepared,
        requested_start=pd.Timestamp(parameters["requested_train_start"]),
        end=pd.Timestamp(parameters["train_end"]),
    )
    test = window_slice(
        prepared,
        requested_start=pd.Timestamp(parameters["test_start"]),
        end=pd.Timestamp(parameters["test_end"]),
    )
    if pd.Timestamp(train.iloc[-1]["date"]) >= pd.Timestamp(test.iloc[0]["date"]):
        raise AssertionError("Training and test windows overlap.")

    threshold_grid = [float(value) for value in parameters["stop_grid_pct"]]
    if threshold_grid != [float(value) for value in range(5, 16)]:
        raise ValueError("The formal grid must be integer 5% through 15% inclusive.")
    fixed_pct = float(parameters["fixed_ablation_stop_pct"])
    daily_frames: list[pd.DataFrame] = []
    reference_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    all_differences: dict[str, float] = {}
    train_rows: list[dict[str, Any]] = []

    train_cases: list[tuple[str, float | None]] = [("train_base_no_stop", None)]
    train_cases.extend((f"train_stop_{threshold:g}pct", threshold) for threshold in threshold_grid)
    for case_id, stop_pct in train_cases:
        metrics, daily, ref_daily, orders, trades, differences = execute_case(
            train,
            window_id="train",
            case_id=case_id,
            stop_pct=stop_pct,
            policy=policy,
            initial_cash=initial_cash,
        )
        train_rows.append(metrics)
        daily_frames.append(daily)
        reference_frames.append(ref_daily)
        order_frames.append(orders)
        trade_frames.append(trades)
        for key, value in differences.items():
            all_differences[f"{case_id}.{key}"] = value

    train_results = pd.DataFrame(train_rows)
    train_grid = train_results[train_results["stop_pct"].notna()].copy()
    selected = select_training_threshold(train_grid, fixed_pct)
    selected_pct = float(selected["stop_pct"])
    selection = {
        "selected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "selected_stop_pct": selected_pct,
        "selected_case_id": str(selected["case_id"]),
        "objective": "least severe training maximum drawdown",
        "tie_breakers": [
            "higher total return",
            "lower order count",
            "closer to fixed 8% ablation anchor",
            "lower threshold",
        ],
        "training_max_drawdown_pct": float(selected["max_drawdown_pct"]),
        "training_total_return_pct": float(selected["total_return_pct"]),
        "frozen_before_test_execution": True,
        "test_metrics_available_at_selection": False,
    }
    (output_root / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    test_cases: list[tuple[str, float | None]] = [
        ("test_base_no_stop", None),
        ("test_fixed_8pct", fixed_pct),
    ]
    if selected_pct != fixed_pct:
        test_cases.append((f"test_selected_{selected_pct:g}pct", selected_pct))
    else:
        test_cases.append(("test_selected_8pct", selected_pct))
    test_rows: list[dict[str, Any]] = []
    for case_id, stop_pct in test_cases:
        metrics, daily, ref_daily, orders, trades, differences = execute_case(
            test,
            window_id="test",
            case_id=case_id,
            stop_pct=stop_pct,
            policy=policy,
            initial_cash=initial_cash,
        )
        test_rows.append(metrics)
        daily_frames.append(daily)
        reference_frames.append(ref_daily)
        order_frames.append(orders)
        trade_frames.append(trades)
        for key, value in differences.items():
            all_differences[f"{case_id}.{key}"] = value

    test_results = pd.DataFrame(test_rows)
    base_test = test_results[test_results["case_id"] == "test_base_no_stop"].iloc[0]
    selected_test = test_results[
        test_results["case_id"] == f"test_selected_{selected_pct:g}pct"
    ].iloc[0]
    selection["test_helped_max_drawdown"] = bool(
        float(selected_test["max_drawdown_pct"]) > float(base_test["max_drawdown_pct"])
    )
    selection["test_max_drawdown_delta_pct_points"] = float(
        selected_test["max_drawdown_pct"] - base_test["max_drawdown_pct"]
    )
    (output_root / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    train_benchmark = run_buy_and_hold_reference(train, policy, initial_cash=initial_cash)
    test_benchmark = run_buy_and_hold_reference(test, policy, initial_cash=initial_cash)
    benchmark_frames = []
    benchmark_rows = []
    for window_id, window, benchmark in (
        ("train", train, train_benchmark),
        ("test", test, test_benchmark),
    ):
        frame = benchmark.daily.copy()
        frame.insert(0, "window_id", window_id)
        benchmark_frames.append(frame)
        metrics = calculate_metrics(
            benchmark.daily,
            benchmark.orders,
            benchmark.trades,
            initial_cash=initial_cash,
        )
        benchmark_rows.append({"window_id": window_id, **metrics})

    parameter_results = pd.concat([train_results, test_results], ignore_index=True)
    outputs = {
        "parameter_results.csv": parameter_results,
        "train_parameter_results.csv": train_results,
        "test_results.csv": test_results,
        "daily.csv": pd.concat(daily_frames, ignore_index=True),
        "reference_daily.csv": pd.concat(reference_frames, ignore_index=True),
        "orders.csv": pd.concat(order_frames, ignore_index=True),
        "trades.csv": pd.concat(trade_frames, ignore_index=True),
        "buy_hold_daily.csv": pd.concat(benchmark_frames, ignore_index=True),
        "benchmark_results.csv": pd.DataFrame(benchmark_rows),
        "indicators.csv": prepared[
            (prepared["date"] >= pd.Timestamp(train.iloc[0]["date"]))
            & (prepared["date"] <= pd.Timestamp(test.iloc[-1]["date"]))
        ][
            [
                "date", "symbol", "open", "high", "low", "close", "volume",
                *sma_spec.sma_columns, "price_above_sma200",
                "sma200_above_sma250_above_sma300", "regime_ready",
            ]
        ].copy(),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": symbol,
        "train_actual_start": pd.Timestamp(train.iloc[0]["date"]).date().isoformat(),
        "train_actual_end": pd.Timestamp(train.iloc[-1]["date"]).date().isoformat(),
        "test_actual_start": pd.Timestamp(test.iloc[0]["date"]).date().isoformat(),
        "test_actual_end": pd.Timestamp(test.iloc[-1]["date"]).date().isoformat(),
        "selection": selection,
        "training_cases": json_safe(train_rows),
        "test_cases": json_safe(test_rows),
        "benchmarks": json_safe(benchmark_rows),
        "max_cross_check_differences": all_differences,
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
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.sma_regime_drawdown.run_reference_peak_drawdown",
        "python": platform.python_version(),
        "initial_cash_per_independent_window": initial_cash,
        "parameters": parameters,
        "selection": selection,
        "signal_timing": "completed Close for base conditions and peak-to-Close drawdown",
        "execution_timing": "next regular-session adjusted Open",
        "window_independence": "test starts from fresh cash and no inherited peak or position state",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "max_cross_check_differences": all_differences,
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
        f"Selected {selected_pct:g}% on train drawdown {selected['max_drawdown_pct']:.4f}%; "
        f"test base {base_test['max_drawdown_pct']:.4f}% vs selected "
        f"{selected_test['max_drawdown_pct']:.4f}%; helped={selection['test_helped_max_drawdown']}; "
        f"max ledger diff={max(all_differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
