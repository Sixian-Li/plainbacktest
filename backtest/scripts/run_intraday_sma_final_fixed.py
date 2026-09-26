#!/usr/bin/env python3
"""Run one configured frozen intraday-SMA strategy on approved daily data."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.intraday_sma import BUY_FORCED_REENTRY, IntradaySmaSpec, run_pybroker_intraday_sma, run_reference_intraday_sma
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_reentry_grid import benchmark_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history"
EXPECTED_PARAMETERS = {
    "A_negative_days_slow": 3,
    "B_slow_sma_window": 175,
    "C_fast_derivative_pct": -0.25,
    "D_negative_days_fast": 4,
    "E_fallback_sma_window": 305,
    "F_short_sma_center": 80,
    "F_short_sma_spacing": 10,
    "F_short_sma_windows": [70, 80, 90],
    "G_short_recovery_below_pct": 1.0,
    "H_reentry_sma_window": 270,
    "L_cost_stop_pct": 10.0,
    "R_forced_rebuy_pct": 0.0,
    "forced_reentry_enabled": True,
}
FIXED_PARAMETER_NAMES = tuple(EXPECTED_PARAMETERS)


def validate_frozen_parameters(parameters: dict[str, Any]) -> None:
    """Retain the original QQQ final-vector contract for its immutable experiment."""

    observed = {name: parameters.get(name) for name in EXPECTED_PARAMETERS}
    if observed != EXPECTED_PARAMETERS:
        raise ValueError(f"Final parameters changed: expected {EXPECTED_PARAMETERS}, observed {observed}")
    if parameters.get("C_fast_derivative_mode") != "all_short_smas":
        raise ValueError("Final strategy requires all_short_smas fast-derivative mode.")
    if parameters.get("evaluation_start") != "analysis_start":
        raise ValueError("Final strategy metrics must begin at analysis_start.")


def validate_configured_fixed_parameters(parameters: dict[str, Any]) -> None:
    """Validate the structural contract shared by fixed single-case experiments."""

    missing = [name for name in FIXED_PARAMETER_NAMES if name not in parameters]
    if missing:
        raise ValueError(f"Fixed strategy is missing parameters: {missing}")
    center = int(parameters["F_short_sma_center"])
    spacing = int(parameters["F_short_sma_spacing"])
    expected_windows = [center - spacing, center, center + spacing]
    if list(parameters["F_short_sma_windows"]) != expected_windows:
        raise ValueError(
            "F_short_sma_windows must equal center-spacing / center / center+spacing: "
            f"expected {expected_windows}, observed {parameters['F_short_sma_windows']}"
        )
    if parameters.get("C_fast_derivative_mode") != "all_short_smas":
        raise ValueError("Fixed strategy requires all_short_smas fast-derivative mode.")
    if parameters.get("evaluation_start") != "analysis_start":
        raise ValueError("Fixed strategy metrics must begin at analysis_start.")
    if parameters.get("initial_position") != "flat":
        raise ValueError("Fixed strategy must start flat and wait for an ordinary buy.")
    if int(parameters.get("max_trades_per_day", 0)) != 1:
        raise ValueError("Fixed strategy requires at most one trade per day.")
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    if start > end:
        raise ValueError(f"analysis_start {start.date()} is after analysis_end {end.date()}.")


def annual_returns(
    strategy: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    initial_cash: float,
) -> pd.DataFrame:
    """Calendar-year returns with the first partial year anchored to initial cash."""

    records: list[dict[str, Any]] = []
    prior_strategy = float(initial_cash)
    prior_benchmark = float(initial_cash)
    for year in sorted(strategy["date"].dt.year.unique()):
        strategy_year = strategy[strategy["date"].dt.year == year]
        benchmark_year = benchmark[benchmark["date"].dt.year == year]
        if strategy_year.empty or benchmark_year.empty:
            raise AssertionError(f"Strategy/benchmark year mismatch for {year}.")
        strategy_final = float(strategy_year.iloc[-1]["equity"])
        benchmark_final = float(benchmark_year.iloc[-1]["equity"])
        records.append(
            {
                "year": int(year),
                "start_date": pd.Timestamp(strategy_year.iloc[0]["date"]),
                "end_date": pd.Timestamp(strategy_year.iloc[-1]["date"]),
                "strategy_return_pct": (strategy_final / prior_strategy - 1.0) * 100.0,
                "benchmark_return_pct": (benchmark_final / prior_benchmark - 1.0) * 100.0,
                "delta_return_pct_points": (
                    strategy_final / prior_strategy - benchmark_final / prior_benchmark
                ) * 100.0,
                "strategy_end_equity": strategy_final,
                "benchmark_end_equity": benchmark_final,
            }
        )
        prior_strategy = strategy_final
        prior_benchmark = benchmark_final
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    symbols = list(context.config["symbols"])
    costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if (
        len(symbols) != 1
        or args.symbol != symbols[0]
        or costs != [0.0]
        or float(args.cost_bps) != 0.0
    ):
        raise ValueError(f"Expected the configured block {symbols} at costs {costs} bps.")
    parameters = context.config["parameters"]
    validate_configured_fixed_parameters(parameters)
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    symbol = args.symbol
    cost_bps = float(args.cost_bps)
    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    spec = IntradaySmaSpec.from_parameters(parameters)

    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
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
    )
    differences = cross_check(result, actual, reference, tolerance=1e-6)
    if reference.orders.empty:
        raise AssertionError("Final strategy never received an ordinary buy signal.")
    first = reference.orders.iloc[0]
    if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
        raise AssertionError("Final strategy did not begin with an ordinary buy.")
    if str(first["primary_signal"]) == BUY_FORCED_REENTRY:
        raise AssertionError("Forced re-entry cannot create the first position.")
    if reference.orders.groupby("date").size().max() > 1:
        raise AssertionError("Final strategy traded more than once on one day.")

    analysis = raw[(raw["date"] >= start) & (raw["date"] <= end)].reset_index(drop=True)
    benchmark = benchmark_state(
        analysis,
        first_entry_date=start,
        first_entry_fill=float(analysis.iloc[0]["open"]),
        initial_cash=initial_cash,
    )
    strategy_metrics = calculate_metrics(actual, reference.orders, reference.trades, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash)
    yearly = annual_returns(actual, benchmark, initial_cash=initial_cash)
    signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
    fill_counts = reference.orders["fill_source"].value_counts().to_dict()
    first_date = pd.Timestamp(first["date"]).normalize()
    summary: dict[str, Any] = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "evaluation_start": start.date().isoformat(),
        "initial_cash": initial_cash,
        "initial_position": "flat",
        "first_entry_date": first_date.date().isoformat(),
        "first_entry_fill": float(first["fill_price"]),
        "first_entry_signal": str(first["primary_signal"]),
        "parameters": parameters,
        "strategy": strategy_metrics,
        "benchmark": benchmark_metrics,
        "delta_cagr_pct_points": strategy_metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
        "delta_sharpe": strategy_metrics["sharpe"] - benchmark_metrics["sharpe"],
        "delta_max_drawdown_pct_points": strategy_metrics["max_drawdown_pct"] - benchmark_metrics["max_drawdown_pct"],
        "primary_signal_counts": signal_counts,
        "fill_source_counts": fill_counts,
        "years_strategy_outperformed": int((yearly["delta_return_pct_points"] > 0).sum()),
        "years_benchmark_outperformed_or_tied": int((yearly["delta_return_pct_points"] <= 0).sum()),
        "max_cross_check_differences": differences,
    }
    parameter_row = {
        "case_id": "FIXED",
        **{name: parameters[name] for name in FIXED_PARAMETER_NAMES if name != "F_short_sma_windows"},
        "F_short_sma_windows": "/".join(str(value) for value in parameters["F_short_sma_windows"]),
        **strategy_metrics,
        "benchmark_final_equity": benchmark_metrics["final_equity"],
        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
        "benchmark_sharpe": benchmark_metrics["sharpe"],
        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
        "delta_cagr_pct_points": summary["delta_cagr_pct_points"],
        "delta_sharpe": summary["delta_sharpe"],
    }
    outputs = {
        "parameter_results.csv": pd.DataFrame([parameter_row]),
        "daily.csv": actual,
        "reference_daily.csv": reference.daily,
        "buy_hold_daily.csv": benchmark,
        "orders.csv": reference.orders,
        "trades.csv": reference.trades,
        "signal_plans.csv": reference.signal_plans,
        "annual_returns.csv": yearly,
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
        "evaluation_start": start.date().isoformat(),
        "parameters": parameters,
        "strategy_metrics": strategy_metrics,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "signal_timing": "next-session conditional thresholds frozen from prior completed closes",
        "execution_timing": "regular-session Open after a gap; otherwise exact intraday threshold",
        "one_trade_max_per_day": True,
        "fractional_shares": True,
        "cash_interest": 0.0,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {symbol} FIXED; CAGR {strategy_metrics['cagr_pct']:.4f}%, "
        f"Sharpe {strategy_metrics['sharpe']:.6f}, orders {strategy_metrics['order_count']}"
    )


if __name__ == "__main__":
    main()
