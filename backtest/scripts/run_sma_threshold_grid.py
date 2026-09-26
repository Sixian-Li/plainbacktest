#!/usr/bin/env python3
"""Run one symbol/cost block of the SMA200 asymmetric threshold grid."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import (
    load_experiment,
    record_block_complete,
    reserve_block,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference, run_reference_threshold
from quantkit.sma_threshold import (
    SmaThresholdSpec,
    analysis_slice,
    prepare_sma_data,
    run_pybroker_threshold,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    """Convert numpy values and non-finite floats to strict JSON values."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = result[column].dt.strftime("%Y-%m-%d")
    return result


def cross_check(
    pybroker_result,
    pybroker_daily: pd.DataFrame,
    reference,
    *,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    differences = {}
    for column in ("cash", "shares", "equity"):
        actual = pybroker_daily[column].to_numpy(dtype=float)
        expected = reference.daily[column].to_numpy(dtype=float)
        if len(actual) != len(expected):
            raise AssertionError(f"Daily length mismatch for {column}: {len(actual)} != {len(expected)}")
        maximum = float(np.max(np.abs(actual - expected))) if len(actual) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")

    actual_orders = pybroker_result.orders.reset_index()
    expected_orders = reference.orders.reset_index(drop=True)
    if len(actual_orders) != len(expected_orders):
        raise AssertionError(
            f"Order count mismatch: PyBroker={len(actual_orders)}, reference={len(expected_orders)}"
        )
    if len(actual_orders):
        if actual_orders["type"].tolist() != expected_orders["type"].tolist():
            raise AssertionError("Order side sequence differs from reference.")
        actual_dates = pd.to_datetime(actual_orders["date"]).tolist()
        expected_dates = pd.to_datetime(expected_orders["date"]).tolist()
        if actual_dates != expected_dates:
            raise AssertionError("Order execution dates differ from reference.")
        for column in ("shares", "fill_price"):
            maximum = float(
                np.max(
                    np.abs(
                        actual_orders[column].to_numpy(dtype=float)
                        - expected_orders[column].to_numpy(dtype=float)
                    )
                )
            )
            differences[f"max_abs_order_{column}_difference"] = maximum
            if maximum > tolerance:
                raise AssertionError(f"Order {column} differs from reference: {maximum}")
    else:
        differences["max_abs_order_shares_difference"] = 0.0
        differences["max_abs_order_fill_price_difference"] = 0.0
    return differences


def append_orders(
    target: list[pd.DataFrame],
    pybroker_result,
    reference,
    metadata: dict[str, Any],
) -> None:
    if pybroker_result.orders.empty:
        return
    actual = pybroker_result.orders.reset_index().rename(columns={"id": "pybroker_order_id"})
    ref = reference.orders.reset_index(drop=True)
    actual.insert(0, "case_id", metadata["case_id"])
    actual.insert(1, "case_index", metadata["case_index"])
    actual.insert(2, "a_pct", metadata["a_pct"])
    actual.insert(3, "b_pct", metadata["b_pct"])
    actual.insert(4, "cost_bps", metadata["cost_bps"])
    actual.insert(5, "signal_date", pd.to_datetime(ref["signal_date"]).dt.strftime("%Y-%m-%d"))
    actual["date"] = pd.to_datetime(actual["date"]).dt.strftime("%Y-%m-%d")
    actual["raw_price"] = ref["raw_price"].to_numpy()
    actual["implicit_cost"] = ref["implicit_cost"].to_numpy()
    target.append(actual)


def append_trades(
    target: list[pd.DataFrame],
    pybroker_result,
    metadata: dict[str, Any],
) -> None:
    if pybroker_result.trades.empty:
        return
    trades = pybroker_result.trades.reset_index().rename(columns={"id": "pybroker_trade_id"})
    trades.insert(0, "case_id", metadata["case_id"])
    trades.insert(1, "case_index", metadata["case_index"])
    trades.insert(2, "a_pct", metadata["a_pct"])
    trades.insert(3, "b_pct", metadata["b_pct"])
    trades.insert(4, "cost_bps", metadata["cost_bps"])
    for column in ("entry_date", "exit_date"):
        trades[column] = pd.to_datetime(trades[column]).dt.strftime("%Y-%m-%d")
    target.append(trades)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    symbol = args.symbol
    cost_bps = float(args.cost_bps)
    if symbol not in context.config["symbols"]:
        raise ValueError(f"Symbol {symbol!r} is not configured: {context.config['symbols']}")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if cost_bps not in configured_costs:
        raise ValueError(f"Cost {cost_bps:g} bps is not configured: {configured_costs}")
    initial_cash = float(context.config["initial_cash"])
    sma_window = int(context.config["strategy"]["sma_window"])
    a_values_config = tuple(float(value) for value in context.config["parameters"]["a_pct"])
    b_values_config = tuple(float(value) for value in context.config["parameters"]["b_pct"])
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == symbol
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    raw = pd.read_csv(canonical_path)
    prepared = prepare_sma_data(raw, window=sma_window)
    data = analysis_slice(prepared, sma_window)
    policy = ExplicitFillPolicy("open", cost_bps)
    benchmark = run_buy_and_hold_reference(data, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily,
        benchmark.orders,
        benchmark.trades,
        initial_cash=initial_cash,
    )
    normalize_frame(benchmark.daily).to_csv(
        output_root / "buy_hold_daily.csv", index=False, lineterminator="\n"
    )
    normalize_frame(benchmark.orders).to_csv(
        output_root / "buy_hold_orders.csv", index=False, lineterminator="\n"
    )

    count = len(a_values_config) * len(b_values_config)
    dates = pd.to_datetime(data["date"]).to_numpy(dtype="datetime64[D]")
    equity_matrix = np.empty((count, len(data)), dtype=np.float64)
    cash_matrix = np.empty_like(equity_matrix)
    shares_matrix = np.empty_like(equity_matrix)
    signal_matrix = np.empty((count, len(data)), dtype=np.int8)
    a_values = np.empty(count, dtype=np.float64)
    b_values = np.empty(count, dtype=np.float64)

    metric_records: list[dict[str, Any]] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    max_cross_check = {
        "max_abs_cash_difference": 0.0,
        "max_abs_shares_difference": 0.0,
        "max_abs_equity_difference": 0.0,
        "max_abs_order_shares_difference": 0.0,
        "max_abs_order_fill_price_difference": 0.0,
    }
    started = time.perf_counter()
    case_index = 0
    for a_pct in a_values_config:
        for b_pct in b_values_config:
            a_tick = int(round(a_pct * 100))
            b_tick = int(round(b_pct * 100))
            case_id = f"{symbol}_c{int(cost_bps):02d}_a{a_tick:03d}_b{b_tick:03d}"
            spec = SmaThresholdSpec(a_pct=a_pct, b_pct=b_pct, window=sma_window)
            result = run_pybroker_threshold(
                data,
                spec,
                policy,
                initial_cash=initial_cash,
            )
            reference = run_reference_threshold(
                data,
                spec,
                policy,
                initial_cash=initial_cash,
            )
            daily = pybroker_daily_state(result, data)
            differences = cross_check(result, daily, reference)
            for key, value in differences.items():
                max_cross_check[key] = max(max_cross_check[key], value)

            metrics = calculate_metrics(
                daily,
                result.orders.reset_index(drop=True),
                result.trades.reset_index(drop=True),
                initial_cash=initial_cash,
            )
            buy_orders = (
                reference.orders[reference.orders["type"] == "buy"]
                if not reference.orders.empty
                else reference.orders
            )
            first_buy_signal_date = (
                pd.Timestamp(buy_orders.iloc[0]["signal_date"]).date().isoformat()
                if not buy_orders.empty
                else None
            )
            first_buy_fill_date = (
                pd.Timestamp(buy_orders.iloc[0]["date"]).date().isoformat()
                if not buy_orders.empty
                else None
            )
            metrics.update(
                {
                    "case_id": case_id,
                    "case_index": case_index,
                    "symbol": symbol,
                    "a_pct": a_pct,
                    "b_pct": b_pct,
                    "cost_bps": cost_bps,
                    "fill_timing": "next_open",
                    "entry_rule": "cross_above_buy_threshold",
                    "first_buy_signal_date": first_buy_signal_date,
                    "first_buy_fill_date": first_buy_fill_date,
                    "benchmark_final_equity": benchmark_metrics["final_equity"],
                    "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
                    "benchmark_sharpe": benchmark_metrics["sharpe"],
                    "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
                    "excess_cagr_pct_points": metrics["cagr_pct"]
                    - benchmark_metrics["cagr_pct"],
                    **differences,
                }
            )
            metric_records.append(metrics)
            metadata = {
                "case_id": case_id,
                "case_index": case_index,
                "a_pct": a_pct,
                "b_pct": b_pct,
                "cost_bps": cost_bps,
            }
            append_orders(order_frames, result, reference, metadata)
            append_trades(trade_frames, result, metadata)

            equity_matrix[case_index] = daily["equity"].to_numpy(dtype=float)
            cash_matrix[case_index] = daily["cash"].to_numpy(dtype=float)
            shares_matrix[case_index] = daily["shares"].to_numpy(dtype=float)
            signal_matrix[case_index] = reference.daily["signal"].to_numpy(dtype=np.int8)
            a_values[case_index] = a_pct
            b_values[case_index] = b_pct
            case_index += 1
            if case_index % 25 == 0 or case_index == count:
                elapsed = time.perf_counter() - started
                print(
                    f"{symbol} {cost_bps:g}bps: {case_index}/{count} "
                    f"({elapsed:.1f}s, {elapsed / case_index:.3f}s/case)",
                    flush=True,
                )

    metrics_frame = pd.DataFrame(metric_records).sort_values("case_index")
    metrics_frame.to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    orders_frame = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades_frame = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    orders_frame.to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    trades_frame.to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_state.npz",
        dates=dates,
        a_pct=a_values,
        b_pct=b_values,
        equity=equity_matrix,
        cash=cash_matrix,
        shares=shares_matrix,
        signal=signal_matrix,
    )

    completed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    block_manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": completed,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "adverse fill: buy=open*(1+cost), sell=open*(1-cost)",
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.reference",
        "python": "3.13.9",
        "initial_cash": initial_cash,
        "sma_window": sma_window,
        "entry_rule": "previous close <= previous SMA200*(1+b%), current close > current SMA200*(1+b%)",
        "entry_timing": "crossing confirmed at close; fill at next regular-session open",
        "first_valid_sma_bar_cannot_enter": True,
        "warmup_bars_required": sma_window,
        "source_start": pd.Timestamp(prepared["date"].min()).date().isoformat(),
        "first_valid_sma_date": pd.Timestamp(data.iloc[0]["date"]).date().isoformat(),
        "first_crossing_evaluation_date": pd.Timestamp(data.iloc[1]["date"]).date().isoformat(),
        "parameter_values_pct": {"a_pct": list(a_values_config), "b_pct": list(b_values_config)},
        "case_count": count,
        "analysis_start": pd.Timestamp(data["date"].min()).date().isoformat(),
        "analysis_end": pd.Timestamp(data["date"].max()).date().isoformat(),
        "analysis_bars": len(data),
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": max_cross_check,
        "runtime_seconds": time.perf_counter() - started,
        "artifacts": {},
    }
    for name in (
        "parameter_results.csv",
        "orders.csv",
        "trades.csv",
        "daily_state.npz",
        "buy_hold_daily.csv",
        "buy_hold_orders.csv",
    ):
        artifact = output_root / name
        block_manifest["artifacts"][name] = {
            "bytes": artifact.stat().st_size,
            "sha256": sha256(artifact),
        }
    (output_root / "manifest.json").write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    record_block_complete(
        context,
        args.run_id,
        symbol,
        cost_bps,
        output_root / "manifest.json",
    )
    print(
        f"Completed {symbol} {cost_bps:g}bps in {block_manifest['runtime_seconds']:.1f}s; "
        f"max equity diff={max_cross_check['max_abs_equity_difference']:.3g}",
        flush=True,
    )


if __name__ == "__main__":
    main()
