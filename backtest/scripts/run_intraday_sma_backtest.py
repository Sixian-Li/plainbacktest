#!/usr/bin/env python3
"""Run the frozen QQQ intraday SMA OR-rule experiment block."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.experiment import (
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.intraday_sma import (
    ALL_SIGNALS,
    IntradaySmaSpec,
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021"
LEDGER_TOLERANCE = 1e-8


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
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


def benchmark_state(analysis: pd.DataFrame, initial_shares: float) -> pd.DataFrame:
    result = analysis[["date", "symbol", "close"]].copy()
    result["cash"] = 0.0
    result["shares"] = float(initial_shares)
    result["equity"] = result["close"].astype(float) * float(initial_shares)
    result["is_long"] = 1
    return result


def cross_check(
    pybroker_result,
    actual: pd.DataFrame,
    reference,
    *,
    tolerance: float = LEDGER_TOLERANCE,
) -> dict[str, float]:
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        observed = actual[column].to_numpy(float)
        expected = reference.daily[column].to_numpy(float)
        if len(observed) != len(expected):
            raise AssertionError(f"Daily length mismatch for {column}: {len(observed)} != {len(expected)}")
        maximum = float(np.max(np.abs(observed - expected))) if len(observed) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")

    observed_orders = pybroker_result.orders.reset_index(drop=True)
    expected_orders = reference.orders.reset_index(drop=True)
    if len(observed_orders) != len(expected_orders):
        raise AssertionError(
            f"Order count mismatch: PyBroker={len(observed_orders)}, reference={len(expected_orders)}"
        )
    if observed_orders["type"].tolist() != expected_orders["type"].tolist():
        raise AssertionError("Order side sequence differs from independent ledger.")
    if pd.to_datetime(observed_orders["date"]).tolist() != pd.to_datetime(expected_orders["date"]).tolist():
        raise AssertionError("Order dates differ from independent ledger.")
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
        if maximum > tolerance:
            raise AssertionError(f"Order {column} differs from independent ledger: {maximum}")

    observed_trades = pybroker_result.trades.reset_index(drop=True)
    expected_trades = reference.trades.reset_index(drop=True)
    if len(observed_trades) != len(expected_trades):
        raise AssertionError(
            f"Trade count mismatch: PyBroker={len(observed_trades)}, reference={len(expected_trades)}"
        )
    for observed_column, expected_column in (
        ("entry_date", "entry_date"),
        ("exit_date", "exit_date"),
    ):
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
        maximum = float(
            np.max(
                np.abs(
                    observed_trades[observed_column].to_numpy(float)
                    - expected_trades[expected_column].to_numpy(float)
                )
            )
        ) if len(observed_trades) else 0.0
        differences[f"max_abs_trade_{observed_column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"Trade {observed_column} differs from independent ledger: {maximum}")
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
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"Cost {cost_bps:g} bps is not configured.")
    if cost_bps != 0:
        raise ValueError("The frozen first run requires exactly zero formal slippage/cost.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    spec = IntradaySmaSpec.from_parameters(parameters)
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_shares = float(parameters["initial_shares"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    analysis = prepare_intraday_sma_data(raw, spec)
    analysis = analysis[(analysis["date"] >= start) & (analysis["date"] <= end)].reset_index(drop=True)
    initial_open = float(analysis.iloc[0]["open"])
    initial_equity = initial_shares * initial_open
    if not np.isclose(initial_equity, float(context.config["initial_cash"]), rtol=0, atol=1e-9):
        raise ValueError("Configured initial_cash does not equal 100 shares at the initial Open.")

    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_shares=initial_shares,
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
        initial_shares=initial_shares,
    )
    differences = cross_check(result, actual, reference)
    user_orders = reference.orders[~reference.orders["is_initial_seed"].astype(bool)].reset_index(drop=True)
    metrics = calculate_metrics(
        actual,
        user_orders,
        result.trades.reset_index(drop=True),
        initial_cash=initial_equity,
    )
    benchmark = benchmark_state(analysis, initial_shares)
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_equity,
    )
    counts = user_orders["primary_signal"].value_counts().to_dict()
    fill_sources = user_orders["fill_source"].value_counts().to_dict()
    metrics.update(
        {
            "symbol": symbol,
            "cost_bps": cost_bps,
            "initial_shares": initial_shares,
            "initial_open": initial_open,
            "formal_fill_model": "next-session predeclared dynamic threshold; open if gapped, exact trigger if touched intraday",
            "primary_signal_counts": counts,
            "fill_source_counts": fill_sources,
            "final_cash": float(actual.iloc[-1]["cash"]),
            "final_shares": float(actual.iloc[-1]["shares"]),
            "benchmark_final_equity": benchmark_metrics["final_equity"],
            "benchmark_total_return_pct": benchmark_metrics["total_return_pct"],
            "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
            "benchmark_sharpe": benchmark_metrics["sharpe"],
            "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
            "excess_total_return_pct_points": metrics["total_return_pct"]
            - benchmark_metrics["total_return_pct"],
            "excess_cagr_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
            **differences,
        }
    )

    ablation_records: list[dict[str, Any]] = []
    for disabled in (None, *ALL_SIGNALS):
        ablation = reference if disabled is None else run_reference_intraday_sma(
            raw,
            spec,
            analysis_start=start,
            analysis_end=end,
            initial_shares=initial_shares,
            disabled_signals=(disabled,),
        )
        ablation_orders = ablation.orders[~ablation.orders["is_initial_seed"].astype(bool)]
        ablation_metrics = calculate_metrics(
            ablation.daily,
            ablation_orders,
            ablation.trades,
            initial_cash=initial_equity,
        )
        ablation_records.append(
            {
                "case": "all_rules" if disabled is None else f"without_{disabled}",
                "disabled_signal": disabled,
                **ablation_metrics,
            }
        )
    ablations = pd.DataFrame(ablation_records)
    parameter_results = pd.DataFrame(
        [
            {
                "case_id": "frozen_A7_B130_C-0.15_D3_E200_F25-30-35_G1_H200_L1.5_R1.5",
                **{key: value for key, value in parameters.items() if key not in {"analysis_start", "analysis_end"}},
                **metrics,
            }
        ]
    )

    outputs = {
        "daily.csv": actual,
        "reference_daily.csv": reference.daily,
        "buy_hold_daily.csv": benchmark,
        "orders.csv": reference.orders,
        "trades.csv": reference.trades,
        "signal_plans.csv": reference.signal_plans,
        "ablation_results.csv": ablations,
        "parameter_results.csv": parameter_results,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(metrics), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    block_manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "zero fees and zero regular-session trigger slippage; overnight overshoots fill at Open",
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "initial_shares": initial_shares,
        "initial_open": initial_open,
        "initial_equity": initial_equity,
        "parameters": parameters,
        "signal_timing": "all next-session trigger formulas frozen from completed closes through the preceding session",
        "execution_timing": "next regular-session Open if already crossed; otherwise exact threshold when regular-session OHLC touches",
        "one_trade_max_per_day": True,
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "artifacts": {},
    }
    for name in (*outputs, "metrics.json"):
        path = output_root / name
        block_manifest["artifacts"][name] = {
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
        f"Completed {symbol}: return={metrics['total_return_pct']:.6f}%, "
        f"benchmark={benchmark_metrics['total_return_pct']:.6f}%, "
        f"orders={len(user_orders)}, max equity diff={differences['max_abs_equity_difference']:.3g}"
    )


if __name__ == "__main__":
    main()
