#!/usr/bin/env python3
"""Run the full-history QQQ three-condition SMA regime ablation."""

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
from quantkit.sma_regime import (
    ALL_CONDITIONS,
    CASE_CONDITIONS,
    SmaRegimeSpec,
    analysis_slice,
    prepare_sma_regime_data,
    run_pybroker_sma_regime,
    run_reference_sma_regime,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history"
LEDGER_TOLERANCE = 1e-8


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


def cross_check(pybroker_result, actual: pd.DataFrame, reference) -> dict[str, float]:
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        observed = actual[column].to_numpy(float)
        expected = reference.daily[column].to_numpy(float)
        if len(observed) != len(expected):
            raise AssertionError(f"Daily length mismatch for {column}: {len(observed)} != {len(expected)}")
        maximum = float(np.max(np.abs(observed - expected))) if len(observed) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")

    observed_orders = pybroker_result.orders.reset_index(drop=True)
    expected_orders = reference.orders.reset_index(drop=True)
    if len(observed_orders) != len(expected_orders):
        raise AssertionError(
            f"Order count mismatch: PyBroker={len(observed_orders)}, reference={len(expected_orders)}"
        )
    if observed_orders["type"].tolist() != expected_orders["type"].tolist():
        raise AssertionError("Order side sequence differs from the independent ledger.")
    if pd.to_datetime(observed_orders["date"]).tolist() != pd.to_datetime(
        expected_orders["date"]
    ).tolist():
        raise AssertionError("Order dates differ from the independent ledger.")
    for column in ("shares", "fill_price"):
        maximum = (
            float(
                np.max(
                    np.abs(
                        observed_orders[column].to_numpy(float)
                        - expected_orders[column].to_numpy(float)
                    )
                )
            )
            if len(observed_orders)
            else 0.0
        )
        differences[f"max_abs_order_{column}_difference"] = maximum
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(f"Order {column} differs from independent ledger: {maximum}")

    observed_trades = pybroker_result.trades.reset_index(drop=True)
    expected_trades = reference.trades.reset_index(drop=True)
    if len(observed_trades) != len(expected_trades):
        raise AssertionError(
            f"Trade count mismatch: PyBroker={len(observed_trades)}, reference={len(expected_trades)}"
        )
    for observed_column, expected_column in (("entry_date", "entry_date"), ("exit_date", "exit_date")):
        if pd.to_datetime(observed_trades[observed_column]).tolist() != pd.to_datetime(
            expected_trades[expected_column]
        ).tolist():
            raise AssertionError(f"Trade {observed_column} differs from independent ledger.")
    for observed_column, expected_column in (
        ("entry", "entry_price"),
        ("exit", "exit_price"),
        ("shares", "shares"),
        ("pnl", "pnl"),
    ):
        maximum = (
            float(
                np.max(
                    np.abs(
                        observed_trades[observed_column].to_numpy(float)
                        - expected_trades[expected_column].to_numpy(float)
                    )
                )
            )
            if len(observed_trades)
            else 0.0
        )
        differences[f"max_abs_trade_{observed_column}_difference"] = maximum
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(
                f"Trade {observed_column} differs from independent ledger: {maximum}"
            )
    return differences


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
    spec = SmaRegimeSpec.from_parameters(parameters)
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    policy = ExplicitFillPolicy("open", cost_bps)
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[(raw["symbol"] == symbol) & (raw["date"] <= end)].sort_values("date").reset_index(drop=True)
    prepared = prepare_sma_regime_data(raw, spec)
    analysis = analysis_slice(prepared)
    if pd.Timestamp(analysis.iloc[-1]["date"]) != end:
        raise ValueError("Configured analysis_end is not the last approved QQQ row.")
    analysis_start = pd.Timestamp(analysis.iloc[0]["date"])

    daily_frames: list[pd.DataFrame] = []
    reference_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    result_rows: list[dict[str, Any]] = []
    all_differences: dict[str, float] = {}
    for case_id in CASE_CONDITIONS:
        pybroker_result = run_pybroker_sma_regime(
            analysis, case_id, policy, initial_cash=initial_cash
        )
        actual = pybroker_daily_state(pybroker_result, analysis)
        reference = run_reference_sma_regime(
            analysis, case_id, policy, initial_cash=initial_cash
        )
        differences = cross_check(pybroker_result, actual, reference)
        for key, value in differences.items():
            all_differences[f"{case_id}.{key}"] = value
        actual.insert(0, "case_id", case_id)
        reference_daily = reference.daily.copy()
        metrics = calculate_metrics(
            actual,
            reference.orders,
            reference.trades,
            initial_cash=initial_cash,
        )
        result_rows.append(
            {
                "case_id": case_id,
                "enabled_conditions": "|".join(CASE_CONDITIONS[case_id]),
                "disabled_condition": next(
                    (condition for condition in ALL_CONDITIONS if condition not in CASE_CONDITIONS[case_id]),
                    "",
                ),
                **metrics,
                **differences,
            }
        )
        daily_frames.append(actual)
        reference_frames.append(reference_daily)
        order_frames.append(reference.orders)
        trade_frames.append(reference.trades)

    benchmark = run_buy_and_hold_reference(analysis, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily,
        benchmark.orders,
        benchmark.trades,
        initial_cash=initial_cash,
    )
    parameter_results = pd.DataFrame(result_rows)
    parameter_results["max_drawdown_rank"] = parameter_results["max_drawdown_pct"].rank(
        ascending=False, method="min"
    ).astype(int)
    daily = pd.concat(daily_frames, ignore_index=True)
    reference_daily = pd.concat(reference_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    indicators = analysis[
        [
            "date", "symbol", "open", "high", "low", "close", "volume",
            *spec.sma_columns, *ALL_CONDITIONS, "regime_ready",
        ]
    ].copy()

    outputs = {
        "parameter_results.csv": parameter_results,
        "daily.csv": daily,
        "reference_daily.csv": reference_daily,
        "orders.csv": orders,
        "trades.csv": trades,
        "indicators.csv": indicators,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": symbol,
        "analysis_start": analysis_start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "bars": len(analysis),
        "cases": json_safe(result_rows),
        "benchmark": json_safe(benchmark_metrics),
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
        "reference_engine": "quantkit.sma_regime.run_reference_sma_regime",
        "python": platform.python_version(),
        "analysis_start": analysis_start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "initial_cash": initial_cash,
        "initial_position": "flat",
        "parameters": parameters,
        "case_conditions": CASE_CONDITIONS,
        "signal_timing": "completed regular-session Close, including that Close in every SMA",
        "execution_timing": "next regular-session adjusted Open",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "benchmark_metrics": benchmark_metrics,
        "case_metrics": json_safe(result_rows),
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
    winner = parameter_results.sort_values("max_drawdown_pct", ascending=False).iloc[0]
    print(
        f"Completed {len(parameter_results)} cases from {analysis_start.date()} through {end.date()}; "
        f"best drawdown={winner['case_id']} {winner['max_drawdown_pct']:.4f}%; "
        f"max ledger diff={max(all_differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
