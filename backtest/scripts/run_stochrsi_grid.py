#!/usr/bin/env python3
"""Run the RKLB raw Stochastic RSI threshold/period grid."""

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
from quantkit.experiment import load_experiment, record_block_complete, reserve_block
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference, run_reference_stochrsi
from quantkit.stochrsi import StochRsiSpec, prepare_stochrsi_data, run_pybroker_stochrsi


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.60a.1__26-08-16__rklb_stochrsi_threshold_grid"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
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


def cross_check(result, actual: pd.DataFrame, reference, tolerance: float = 1e-6) -> dict[str, float]:
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        left = actual[column].to_numpy(dtype=float)
        right = reference.daily[column].to_numpy(dtype=float)
        maximum = float(np.max(np.abs(left - right))) if len(left) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if len(left) != len(right) or maximum > tolerance:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")
    orders = result.orders.reset_index()
    expected = reference.orders.reset_index(drop=True)
    if len(orders) != len(expected):
        raise AssertionError(f"Order count mismatch: {len(orders)} != {len(expected)}")
    if len(orders):
        if orders["type"].tolist() != expected["type"].tolist():
            raise AssertionError("Order side sequence differs from reference")
        if pd.to_datetime(orders["date"]).tolist() != pd.to_datetime(expected["date"]).tolist():
            raise AssertionError("Order execution dates differ from reference")
        for column in ("shares", "fill_price"):
            maximum = float(
                np.max(
                    np.abs(
                        orders[column].to_numpy(dtype=float)
                        - expected[column].to_numpy(dtype=float)
                    )
                )
            )
            differences[f"max_abs_order_{column}_difference"] = maximum
            if maximum > tolerance:
                raise AssertionError(f"Order {column} mismatch: {maximum}")
    else:
        differences["max_abs_order_shares_difference"] = 0.0
        differences["max_abs_order_fill_price_difference"] = 0.0
    return differences


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
        raise ValueError(f"Unconfigured symbol: {symbol}")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"Unconfigured cost: {cost_bps:g}")
    periods = tuple(int(value) for value in context.config["parameters"]["period"])
    buys = tuple(float(value) for value in context.config["parameters"]["buy_threshold"])
    sells = tuple(float(value) for value in context.config["parameters"]["sell_threshold"])
    initial_cash = float(context.config["initial_cash"])
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest = json.loads(
        (WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8")
    )
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved")
    raw = pd.read_csv(canonical_path)
    maximum_period = max(periods)
    maximum_prepared = prepare_stochrsi_data(raw, maximum_period)
    common_ready = maximum_prepared[maximum_prepared["stochrsi"].notna()]
    if common_ready.empty:
        raise RuntimeError("Maximum period has no complete StochRSI values")
    common_start = pd.Timestamp(common_ready.iloc[0]["date"])
    common_end = pd.Timestamp(maximum_prepared.iloc[-1]["date"])
    policy = ExplicitFillPolicy("open", cost_bps)
    benchmark_data = maximum_prepared[
        (maximum_prepared["date"] >= common_start) & (maximum_prepared["date"] <= common_end)
    ].reset_index(drop=True)
    benchmark = run_buy_and_hold_reference(benchmark_data, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    normalize_frame(benchmark.daily).to_csv(output_root / "buy_hold_daily.csv", index=False, lineterminator="\n")
    normalize_frame(benchmark.orders).to_csv(output_root / "buy_hold_orders.csv", index=False, lineterminator="\n")

    count = len(periods) * len(buys) * len(sells)
    bars = len(benchmark_data)
    equity_matrix = np.empty((count, bars), dtype=np.float64)
    cash_matrix = np.empty_like(equity_matrix)
    shares_matrix = np.empty_like(equity_matrix)
    signal_matrix = np.empty((count, bars), dtype=np.int8)
    stochrsi_matrix = np.empty((count, bars), dtype=np.float64)
    records: list[dict[str, Any]] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    max_differences = {
        "max_abs_cash_difference": 0.0,
        "max_abs_shares_difference": 0.0,
        "max_abs_equity_difference": 0.0,
        "max_abs_order_shares_difference": 0.0,
        "max_abs_order_fill_price_difference": 0.0,
    }
    started = time.perf_counter()
    case_index = 0
    for sell_threshold in sells:
        for buy_threshold in buys:
            for period in periods:
                prepared = prepare_stochrsi_data(raw, period)
                data = prepared[
                    (prepared["date"] >= common_start) & (prepared["date"] <= common_end)
                ].reset_index(drop=True)
                if len(data) != bars or data["stochrsi"].isna().any():
                    raise AssertionError(f"Period {period} does not match the common analysis window")
                spec = StochRsiSpec(period, buy_threshold, sell_threshold)
                result = run_pybroker_stochrsi(data, spec, policy, initial_cash=initial_cash)
                reference = run_reference_stochrsi(data, spec, policy, initial_cash=initial_cash)
                daily = pybroker_daily_state(result, data)
                differences = cross_check(result, daily, reference)
                for key, value in differences.items():
                    max_differences[key] = max(max_differences[key], value)
                metrics = calculate_metrics(
                    daily,
                    result.orders.reset_index(drop=True),
                    result.trades.reset_index(drop=True),
                    initial_cash=initial_cash,
                )
                case_id = (
                    f"{symbol}_c{int(cost_bps):02d}_p{period:03d}_"
                    f"buy{int(round(buy_threshold * 10)):02d}_sell{int(round(sell_threshold * 10)):02d}"
                )
                buy_orders = (
                    reference.orders[reference.orders["type"] == "buy"]
                    if not reference.orders.empty
                    else reference.orders
                )
                metrics.update(
                    {
                        "case_id": case_id,
                        "case_index": case_index,
                        "symbol": symbol,
                        "period": period,
                        "buy_threshold": buy_threshold,
                        "sell_threshold": sell_threshold,
                        "cost_bps": cost_bps,
                        "first_buy_signal_date": (
                            pd.Timestamp(buy_orders.iloc[0]["signal_date"]).date().isoformat()
                            if not buy_orders.empty else None
                        ),
                        "first_buy_fill_date": (
                            pd.Timestamp(buy_orders.iloc[0]["date"]).date().isoformat()
                            if not buy_orders.empty else None
                        ),
                        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
                        "benchmark_sharpe": benchmark_metrics["sharpe"],
                        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
                        "excess_cagr_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
                        **differences,
                    }
                )
                records.append(metrics)
                metadata = {
                    "case_id": case_id,
                    "case_index": case_index,
                    "period": period,
                    "buy_threshold": buy_threshold,
                    "sell_threshold": sell_threshold,
                    "cost_bps": cost_bps,
                }
                if not result.orders.empty:
                    orders = result.orders.reset_index().rename(columns={"id": "pybroker_order_id"})
                    expected_orders = reference.orders.reset_index(drop=True)
                    for offset, (key, value) in enumerate(metadata.items()):
                        orders.insert(offset, key, value)
                    orders.insert(len(metadata), "signal_date", pd.to_datetime(expected_orders["signal_date"]).dt.strftime("%Y-%m-%d"))
                    orders["date"] = pd.to_datetime(orders["date"]).dt.strftime("%Y-%m-%d")
                    orders["raw_price"] = expected_orders["raw_price"].to_numpy()
                    orders["implicit_cost"] = expected_orders["implicit_cost"].to_numpy()
                    order_frames.append(orders)
                if not result.trades.empty:
                    trades = result.trades.reset_index().rename(columns={"id": "pybroker_trade_id"})
                    for offset, (key, value) in enumerate(metadata.items()):
                        trades.insert(offset, key, value)
                    for column in ("entry_date", "exit_date"):
                        trades[column] = pd.to_datetime(trades[column]).dt.strftime("%Y-%m-%d")
                    trade_frames.append(trades)
                equity_matrix[case_index] = daily["equity"].to_numpy(dtype=float)
                cash_matrix[case_index] = daily["cash"].to_numpy(dtype=float)
                shares_matrix[case_index] = daily["shares"].to_numpy(dtype=float)
                signal_matrix[case_index] = reference.daily["signal"].to_numpy(dtype=np.int8)
                stochrsi_matrix[case_index] = data["stochrsi"].to_numpy(dtype=float)
                case_index += 1
                if case_index % 20 == 0 or case_index == count:
                    elapsed = time.perf_counter() - started
                    print(f"{symbol}: {case_index}/{count} cases ({elapsed:.1f}s)", flush=True)

    pd.DataFrame(records).sort_values("case_index").to_csv(
        output_root / "parameter_results.csv", index=False, lineterminator="\n"
    )
    (pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()).to_csv(
        output_root / "orders.csv", index=False, lineterminator="\n"
    )
    (pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()).to_csv(
        output_root / "trades.csv", index=False, lineterminator="\n"
    )
    np.savez_compressed(
        output_root / "daily_state.npz",
        dates=pd.to_datetime(benchmark_data["date"]).to_numpy(dtype="datetime64[D]"),
        equity=equity_matrix,
        cash=cash_matrix,
        shares=shares_matrix,
        signal=signal_matrix,
        stochrsi=stochrsi_matrix,
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "adverse fill: buy=open*(1+cost), sell=open*(1-cost)",
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.reference.run_reference_stochrsi",
        "initial_cash": initial_cash,
        "indicator": "raw StochRSI using Wilder RSI; RSI period equals stochastic range period; zero RSI range maps to 0.5",
        "signal_timing": "completed close; inclusive level condition",
        "execution_timing": "next regular-session open",
        "common_warmup_period": maximum_period,
        "analysis_start": common_start.date().isoformat(),
        "analysis_end": common_end.date().isoformat(),
        "analysis_bars": bars,
        "case_count": count,
        "parameter_values": {
            "period": list(periods),
            "buy_threshold": list(buys),
            "sell_threshold": list(sells),
        },
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": max_differences,
        "runtime_seconds": time.perf_counter() - started,
        "artifacts": {},
    }
    for name in (
        "parameter_results.csv", "orders.csv", "trades.csv", "daily_state.npz",
        "buy_hold_daily.csv", "buy_hold_orders.csv",
    ):
        path = output_root / name
        manifest["artifacts"][name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {count} cases; max equity difference "
        f"{max_differences['max_abs_equity_difference']:.3g}"
    )


if __name__ == "__main__":
    main()
