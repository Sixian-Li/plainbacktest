#!/usr/bin/env python3
"""Run one symbol/cost block of the fixed trio SMA200 flat-spell study."""

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
from quantkit.paths import BACKTEST_ROOT
from quantkit.reference import run_buy_and_hold_reference, run_reference_threshold
from quantkit.sma_flat_spells import attribute_flat_spells, summarize_flat_spells
from quantkit.sma_threshold import (
    SmaThresholdSpec,
    analysis_slice,
    prepare_sma_data,
    run_pybroker_threshold,
)
from scripts.run_bear_event_sma_portfolios import load_candidate_frames
from scripts.run_sma_threshold_grid import cross_check


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution"
)
EXPECTED_SYMBOLS = ("MO", "AZO", "TLT")
LEDGER_TOLERANCE = 1e-6


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
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


def _tag_actual_orders(result, reference) -> pd.DataFrame:
    if result.orders.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "symbol",
                "type",
                "shares",
                "fill_price",
                "signal_date",
                "raw_price",
                "implicit_cost",
            ]
        )
    actual = result.orders.reset_index().rename(columns={"id": "pybroker_order_id"})
    expected = reference.orders.reset_index(drop=True)
    actual["signal_date"] = pd.to_datetime(expected["signal_date"])
    actual["raw_price"] = expected["raw_price"].to_numpy(float)
    actual["implicit_cost"] = expected["implicit_cost"].to_numpy(float)
    return actual


def _combined_daily(
    actual: pd.DataFrame,
    reference,
    analysis: pd.DataFrame,
) -> pd.DataFrame:
    ref = reference.daily.reset_index(drop=True)
    result = analysis[
        ["date", "symbol", "open", "high", "low", "close", "sma200"]
    ].copy().reset_index(drop=True)
    for column in ("buy_threshold", "sell_threshold", "signal", "executed"):
        result[column] = ref[column].to_numpy()
    for column in ("cash", "shares", "equity", "is_long"):
        result[column] = actual[column].to_numpy()
        result[f"reference_{column}"] = ref[column].to_numpy()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    configured_symbols = tuple(str(value) for value in context.config["symbols"])
    if configured_symbols != EXPECTED_SYMBOLS:
        raise ValueError("formal target order must remain MO/AZO/TLT")
    symbol = str(args.symbol)
    if symbol not in EXPECTED_SYMBOLS:
        raise ValueError(f"symbol must be one of {EXPECTED_SYMBOLS}")
    cost_bps = float(args.cost_bps)
    configured_costs = [
        float(value) for value in context.config["cost_scenarios_bps_per_side"]
    ]
    if cost_bps not in configured_costs or configured_costs != [0.0, 5.0]:
        raise ValueError("formal costs must remain 0 and 5 bps")

    parameters = context.config["parameters"]
    if [str(value) for value in parameters["targets"]] != list(EXPECTED_SYMBOLS):
        raise AssertionError("formal target list changed")
    window = int(parameters["sma_window"])
    entry_buffer = float(parameters["entry_buffer_pct"])
    exit_buffer = float(parameters["exit_buffer_pct"])
    if (window, entry_buffer, exit_buffer) != (200, 3.0, 3.0):
        raise AssertionError("formal SMA200±3% rule changed")
    horizons = [int(value) for value in parameters["forward_sessions_after_sell"]]
    if horizons != [5, 10, 20, 60]:
        raise AssertionError("formal forward horizons changed")
    analysis_end = pd.Timestamp(parameters["analysis_end"])

    candidates, source_paths = load_candidate_frames([symbol])
    raw = candidates[symbol].copy()
    raw = raw[raw["date"] <= analysis_end].sort_values("date").reset_index(drop=True)
    if raw.empty or pd.Timestamp(raw.iloc[-1]["date"]) != analysis_end:
        raise ValueError(f"{symbol} does not end on frozen date {analysis_end.date()}")
    prepared = prepare_sma_data(raw, window=window)
    analysis = analysis_slice(prepared, window)
    if pd.Timestamp(analysis.iloc[-1]["date"]) != analysis_end:
        raise AssertionError("analysis end drifted after SMA preparation")

    interval_path = WORKSPACE_ROOT / str(parameters["bear_interval_source"])
    intervals = pd.DataFrame(
        json.loads(interval_path.read_text(encoding="utf-8"))["intervals"]
    )
    if len(intervals) != 12 or set(intervals["severity"]) != {"major", "minor"}:
        raise ValueError("bear attribution source must retain 12 major/minor intervals")

    output_root = reserve_block(context, args.run_id, symbol, cost_bps)
    fill_policy = ExplicitFillPolicy("open", cost_bps)
    spec = SmaThresholdSpec(a_pct=exit_buffer, b_pct=entry_buffer, window=window)
    initial_cash = float(context.config["initial_cash"])
    result = run_pybroker_threshold(
        analysis,
        spec,
        fill_policy,
        initial_cash=initial_cash,
    )
    reference = run_reference_threshold(
        analysis,
        spec,
        fill_policy,
        initial_cash=initial_cash,
    )
    actual_daily = pybroker_daily_state(result, analysis)
    differences = cross_check(
        result,
        actual_daily,
        reference,
        tolerance=LEDGER_TOLERANCE,
    )
    strategy_metrics = calculate_metrics(
        actual_daily,
        result.orders.reset_index(drop=True),
        result.trades.reset_index(drop=True),
        initial_cash=initial_cash,
    )

    benchmark = run_buy_and_hold_reference(
        analysis,
        fill_policy,
        initial_cash=initial_cash,
    )
    benchmark_metrics = calculate_metrics(
        benchmark.daily,
        benchmark.orders,
        benchmark.trades,
        initial_cash=initial_cash,
    )
    events = attribute_flat_spells(
        analysis,
        reference.orders,
        horizons=horizons,
        bear_intervals=intervals,
    )
    event_summary = summarize_flat_spells(events)
    event_summary.update(
        {
            "symbol": symbol,
            "cost_bps": cost_bps,
            "analysis_start": pd.Timestamp(analysis.iloc[0]["date"]).date().isoformat(),
            "analysis_end": analysis_end.date().isoformat(),
            "first_buy_signal_date": (
                None
                if reference.orders.empty
                else pd.Timestamp(reference.orders.iloc[0]["signal_date"]).date().isoformat()
            ),
            "first_buy_fill_date": (
                None
                if reference.orders.empty
                else pd.Timestamp(reference.orders.iloc[0]["date"]).date().isoformat()
            ),
            "strategy_total_return_pct": strategy_metrics["total_return_pct"],
            "strategy_cagr_pct": strategy_metrics["cagr_pct"],
            "strategy_sharpe": strategy_metrics["sharpe"],
            "strategy_max_drawdown_pct": strategy_metrics["max_drawdown_pct"],
            "strategy_exposure_pct": strategy_metrics["exposure_pct"],
            "strategy_order_count": strategy_metrics["order_count"],
            "benchmark_total_return_pct": benchmark_metrics["total_return_pct"],
            "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
            "benchmark_sharpe": benchmark_metrics["sharpe"],
            "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
            "excess_total_return_pp": (
                strategy_metrics["total_return_pct"]
                - benchmark_metrics["total_return_pct"]
            ),
            "max_drawdown_improvement_pp": (
                strategy_metrics["max_drawdown_pct"]
                - benchmark_metrics["max_drawdown_pct"]
            ),
        }
    )

    actual_orders = _tag_actual_orders(result, reference)
    daily = _combined_daily(actual_daily, reference, analysis)
    artifacts: dict[str, pd.DataFrame] = {
        "daily_state.csv": daily,
        "orders.csv": actual_orders,
        "reference_orders.csv": reference.orders,
        "trades.csv": result.trades.reset_index(),
        "reference_trades.csv": reference.trades,
        "flat_spells.csv": events,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
        "parameter_results.csv": pd.DataFrame(
            [
                {
                    "case_id": f"{symbol}__sma200_pm3",
                    "case_index": 0,
                    "symbol": symbol,
                    "cost_bps": cost_bps,
                    "sma_window": window,
                    "entry_buffer_pct": entry_buffer,
                    "exit_buffer_pct": exit_buffer,
                    **strategy_metrics,
                    "benchmark_total_return_pct": benchmark_metrics["total_return_pct"],
                    "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
                    "benchmark_sharpe": benchmark_metrics["sharpe"],
                    "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
                    "flat_spell_count": event_summary["spell_count"],
                    "closed_negative_share_pct": event_summary[
                        "closed_negative_share_pct"
                    ],
                    **differences,
                }
            ]
        ),
    }
    for name, frame in artifacts.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "metrics.json").write_text(
        json.dumps(
            json_safe(
                {
                    "schema_version": 1,
                    "symbol": symbol,
                    "cost_bps": cost_bps,
                    "strategy_metrics": strategy_metrics,
                    "benchmark_metrics": benchmark_metrics,
                    "flat_spell_summary": event_summary,
                    "max_cross_check_differences": differences,
                }
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    completed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_path = source_paths[symbol]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": completed,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "case_count": 1,
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.reference.run_reference_threshold",
        "attribution_engine": "quantkit.sma_flat_spells.attribute_flat_spells",
        "python": platform.python_version(),
        "signal_timing": "completed adjusted close",
        "execution_timing": "next symbol-session adjusted open",
        "cost_model": "buy=open*(1+cost), sell=open*(1-cost)",
        "parameters": {
            "sma_window": window,
            "entry_buffer_pct": entry_buffer,
            "exit_buffer_pct": exit_buffer,
            "forward_sessions_after_sell": horizons,
        },
        "analysis_start": pd.Timestamp(analysis.iloc[0]["date"]).date().isoformat(),
        "analysis_end": analysis_end.date().isoformat(),
        "analysis_bars": int(len(analysis)),
        "source_file": str(source_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(source_path),
        "bear_interval_source": str(interval_path.relative_to(WORKSPACE_ROOT)),
        "bear_interval_source_sha256": sha256(interval_path),
        "strategy_metrics": strategy_metrics,
        "benchmark_metrics": benchmark_metrics,
        "flat_spell_summary": event_summary,
        "max_cross_check_differences": differences,
        "formal_ledger_tolerance": LEDGER_TOLERANCE,
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
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, manifest_path)
    print(
        f"Completed {symbol} at {cost_bps:g} bps: "
        f"orders={strategy_metrics['order_count']}, flat spells={event_summary['spell_count']}, "
        f"max ledger diff={max(differences.values()):.3g}",
        flush=True,
    )


if __name__ == "__main__":
    main()
