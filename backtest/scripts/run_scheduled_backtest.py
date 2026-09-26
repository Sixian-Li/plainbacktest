#!/usr/bin/env python3
"""Run one symbol/cost block for an externally supplied close schedule."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.scheduled import ScheduledOrder, run_pybroker_schedule, run_reference_schedule


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1"
LEDGER_TOLERANCE = 1e-8


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


def benchmark_state(analysis: pd.DataFrame, initial_shares: float) -> pd.DataFrame:
    result = analysis[["date", "symbol", "close"]].copy()
    result["cash"] = 0.0
    result["shares"] = float(initial_shares)
    result["equity"] = result["close"].astype(float) * float(initial_shares)
    result["is_long"] = 1
    return result


def cross_check(pybroker_result, actual: pd.DataFrame, reference) -> dict[str, float]:
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        observed = actual[column].to_numpy(dtype=float)
        expected = reference.daily[column].to_numpy(dtype=float)
        if len(observed) != len(expected):
            raise AssertionError(f"Daily length mismatch for {column}: {len(observed)} != {len(expected)}")
        maximum = float(np.max(np.abs(observed - expected))) if len(observed) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")

    actual_orders = pybroker_result.orders.reset_index(drop=True)
    expected_orders = reference.orders.reset_index(drop=True)
    if len(actual_orders) != len(expected_orders):
        raise AssertionError(
            f"Order count mismatch: PyBroker={len(actual_orders)}, reference={len(expected_orders)}"
        )
    if actual_orders["type"].tolist() != expected_orders["type"].tolist():
        raise AssertionError("Order side sequence differs from independent ledger.")
    if pd.to_datetime(actual_orders["date"]).tolist() != pd.to_datetime(
        expected_orders["date"]
    ).tolist():
        raise AssertionError("Order dates differ from independent ledger.")
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
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(f"Order {column} differs from independent ledger: {maximum}")

    actual_trades = pybroker_result.trades.reset_index(drop=True)
    expected_trades = reference.trades.reset_index(drop=True)
    if len(actual_trades) != len(expected_trades):
        raise AssertionError(
            f"Trade count mismatch: PyBroker={len(actual_trades)}, reference={len(expected_trades)}"
        )
    for actual_date, expected_date in (
        (actual_trades["entry_date"], expected_trades["entry_date"]),
        (actual_trades["exit_date"], expected_trades["exit_date"]),
    ):
        if pd.to_datetime(actual_date).tolist() != pd.to_datetime(expected_date).tolist():
            raise AssertionError("Trade entry/exit dates differ from independent ledger.")
    for actual_column, expected_column in (
        ("entry", "entry_price"),
        ("exit", "exit_price"),
        ("shares", "shares"),
        ("pnl", "pnl"),
    ):
        maximum = float(
            np.max(
                np.abs(
                    actual_trades[actual_column].to_numpy(dtype=float)
                    - expected_trades[expected_column].to_numpy(dtype=float)
                )
            )
        )
        differences[f"max_abs_trade_{actual_column}_difference"] = maximum
        if maximum > LEDGER_TOLERANCE:
            raise AssertionError(
                f"Trade {actual_column} differs from independent ledger: {maximum}"
            )
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
        raise ValueError(f"Symbol {symbol!r} is not configured.")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"Cost {cost_bps:g} bps is not configured.")
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
    initial_date = pd.Timestamp(parameters["initial_date"]).normalize()
    end_date = pd.Timestamp(parameters["end_date"]).normalize()
    initial_shares = float(parameters["initial_shares"])
    schedule = tuple(
        ScheduledOrder(item["date"], item["side"]) for item in parameters["trade_schedule"]
    )
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    initial_matches = raw.index[raw["date"] == initial_date].tolist()
    end_matches = raw.index[raw["date"] == end_date].tolist()
    if len(initial_matches) != 1 or len(end_matches) != 1:
        raise ValueError("Initial or end date is absent or duplicated in canonical data.")
    start_index = initial_matches[0]
    end_index = end_matches[0]
    if start_index == 0 or end_index < start_index:
        raise ValueError("Date range cannot supply the pre-start bar required by PyBroker.")
    engine_data = raw.iloc[start_index - 1 : end_index + 1].copy().reset_index(drop=True)
    analysis = engine_data[engine_data["date"] >= initial_date].copy().reset_index(drop=True)

    initial_equity = initial_shares * float(analysis.iloc[0]["close"])
    configured_equity = float(context.config["initial_cash"])
    if not np.isclose(initial_equity, configured_equity, rtol=0.0, atol=1e-6):
        raise ValueError(
            f"Configured initial_cash {configured_equity} does not equal the marked initial position {initial_equity}."
        )

    policy = ExplicitFillPolicy("close", cost_bps)
    pybroker_result = run_pybroker_schedule(
        engine_data,
        schedule,
        initial_date=initial_date,
        initial_shares=initial_shares,
        policy=policy,
    )
    actual = pybroker_daily_state(pybroker_result, engine_data)
    actual = actual[actual["date"] >= initial_date].reset_index(drop=True)
    reference = run_reference_schedule(
        analysis,
        schedule,
        initial_date=initial_date,
        initial_shares=initial_shares,
        policy=policy,
        seed_signal_date=engine_data.iloc[0]["date"],
    )
    differences = cross_check(pybroker_result, actual, reference)

    user_orders = reference.orders[~reference.orders["is_initial_seed"]].reset_index(drop=True)
    metrics = calculate_metrics(
        actual,
        user_orders,
        pybroker_result.trades.reset_index(drop=True),
        initial_cash=initial_equity,
    )
    benchmark = benchmark_state(analysis, initial_shares)
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_equity,
    )
    metrics.update(
        {
            "symbol": symbol,
            "cost_bps": cost_bps,
            "fill_timing": "scheduled_same_day_close",
            "initial_shares": initial_shares,
            "scheduled_action_count": len(schedule),
            "final_cash": float(actual.iloc[-1]["cash"]),
            "final_shares": float(actual.iloc[-1]["shares"]),
            "benchmark_final_equity": benchmark_metrics["final_equity"],
            "benchmark_total_return_pct": benchmark_metrics["total_return_pct"],
            "benchmark_sharpe": benchmark_metrics["sharpe"],
            "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
            "excess_total_return_pct_points": metrics["total_return_pct"]
            - benchmark_metrics["total_return_pct"],
            **differences,
        }
    )

    orders_output = reference.orders.copy()
    orders_output.insert(0, "cost_bps", cost_bps)
    trades_output = reference.trades.copy()
    trades_output.insert(0, "cost_bps", cost_bps)
    normalize_frame(actual).to_csv(output_root / "daily.csv", index=False, lineterminator="\n")
    normalize_frame(benchmark).to_csv(
        output_root / "buy_hold_daily.csv", index=False, lineterminator="\n"
    )
    normalize_frame(orders_output).to_csv(
        output_root / "orders.csv", index=False, lineterminator="\n"
    )
    normalize_frame(trades_output).to_csv(
        output_root / "trades.csv", index=False, lineterminator="\n"
    )
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
        "cost_model": "initial position has no acquisition cost; later buys=close*(1+cost), sells=close*(1-cost)",
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.scheduled.run_reference_schedule",
        "python": platform.python_version(),
        "initial_date": initial_date.date().isoformat(),
        "analysis_end": end_date.date().isoformat(),
        "analysis_bars": len(analysis),
        "initial_shares": initial_shares,
        "initial_equity": initial_equity,
        "schedule": [{"date": item.date.date().isoformat(), "side": item.side} for item in schedule],
        "signal_timing": "external predeclared instruction submitted on prior trading bar",
        "execution_timing": "listed regular-session adjusted close",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": "same 100 initial shares marked to adjusted close; no artificial final liquidation",
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "artifacts": {},
    }
    for name in ("daily.csv", "buy_hold_daily.csv", "orders.csv", "trades.csv", "metrics.json"):
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
        f"Completed {symbol} {cost_bps:g}bps: return={metrics['total_return_pct']:.6f}%, "
        f"benchmark={benchmark_metrics['total_return_pct']:.6f}%, "
        f"max equity diff={differences['max_abs_equity_difference']:.3g}"
    )


if __name__ == "__main__":
    main()
