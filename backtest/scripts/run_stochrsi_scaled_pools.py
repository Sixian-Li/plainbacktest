#!/usr/bin/env python3
"""Run the frozen QQQ StochRSI scaled-buy and virtual-pool state machine."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.stochrsi_scaled_pools import (
    ScaledPoolSpec,
    cash_flow_adjusted_metrics,
    compiled_daily_state,
    contribution_matched_buy_hold,
    prepare_scaled_pool_data,
    run_compiled_pybroker,
    run_reference_scaled_pools,
)
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80__26-08-25__qqq_stochrsi_scaled_pools_2015_2026"
TOLERANCE = 1e-6


def naive_buy_hold(analysis: pd.DataFrame, *, initial_cash: float):
    close = float(analysis.iloc[0]["close"])
    shares = initial_cash / close
    daily = analysis[["date", "symbol", "close"]].copy()
    daily["cash"] = 0.0
    daily["shares"] = shares
    daily["equity"] = shares * daily["close"].astype(float)
    daily["is_long"] = 1
    daily["external_contribution"] = 0.0
    orders = pd.DataFrame([{
        "symbol": str(analysis.iloc[0]["symbol"]), "type": "buy",
        "date": pd.Timestamp(analysis.iloc[0]["date"]), "shares": shares,
        "fill_price": close, "raw_fill_price": close,
        "primary_signal": "NAIVE_BUY_HOLD_ENTRY", "fill_source": "same_close",
        "notional": initial_cash,
    }])
    metrics = calculate_metrics(daily, orders, pd.DataFrame(), initial_cash=initial_cash)
    return daily, orders, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This experiment requires QQQ")
    if float(args.cost_bps) != 0 or context.config["cost_scenarios_bps_per_side"] != [0]:
        raise ValueError("This frozen experiment has only the zero-cost case")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    params = context.config["parameters"]
    start, end = pd.Timestamp(params["analysis_start"]), pd.Timestamp(params["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    analysis = raw[raw["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen boundaries do not match QQQ sessions")

    spec = ScaledPoolSpec()
    started = time.perf_counter()
    prepared = prepare_scaled_pool_data(raw, spec)
    reference = run_reference_scaled_pools(
        prepared, spec, analysis_start=start, analysis_end=end, initial_cash=initial_cash
    )
    broker, engine = run_compiled_pybroker(
        prepared, reference, analysis_start=start, analysis_end=end, initial_cash=initial_cash
    )
    actual = compiled_daily_state(broker, engine, analysis, reference.contributions)
    actual = actual[actual["date"].between(start, end)].reset_index(drop=True)
    differences = cross_check(broker, actual, reference, tolerance=TOLERANCE)
    strategy_metrics = cash_flow_adjusted_metrics(
        reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
    )

    modified_daily, modified_orders = contribution_matched_buy_hold(
        analysis, reference.contributions, initial_cash=initial_cash
    )
    modified_metrics = cash_flow_adjusted_metrics(
        modified_daily, modified_orders, pd.DataFrame(), initial_cash=initial_cash
    )
    naive_daily, naive_orders, naive_metrics = naive_buy_hold(analysis, initial_cash=initial_cash)

    indicator = prepared[prepared["date"].between(start, end)][
        ["date", "close", "stochrsi_42", "stochrsi_100", "prior_stochrsi_42",
         "prior_stochrsi_100", "fast_drop_trigger_100_030"]
    ].reset_index(drop=True)
    equality = {
        "stochrsi100_exact_0_60_count": int(indicator["stochrsi_100"].eq(0.60).sum()),
        "stochrsi100_exact_0_30_count": int(indicator["stochrsi_100"].eq(0.30).sum()),
        "closest_abs_distance_to_0_60": float((indicator["stochrsi_100"] - 0.60).abs().min()),
        "closest_abs_distance_to_0_30": float((indicator["stochrsi_100"] - 0.30).abs().min()),
    }
    event_counts = reference.events["action"].value_counts().to_dict()
    fast_events = reference.events[reference.events["fast_drop_sale"]].copy()
    outputs = {
        "parameter_results.csv": pd.DataFrame([
            {"case_id": "STRATEGY", "label": "StochRSI分档补仓与双池减仓", **strategy_metrics},
            {"case_id": "MODIFIED_BUY_HOLD", "label": "Modified Buy & Hold", **modified_metrics},
            {"case_id": "NAIVE_BUY_HOLD", "label": "Naive Buy & Hold", **naive_metrics},
        ]),
        "daily.csv": reference.daily,
        "pybroker_daily.csv": actual,
        "orders.csv": reference.orders,
        "trades.csv": reference.trades,
        "contributions.csv": reference.contributions,
        "events.csv": reference.events,
        "modified_buy_hold_daily.csv": modified_daily,
        "modified_buy_hold_orders.csv": modified_orders,
        "naive_buy_hold_daily.csv": naive_daily,
        "naive_buy_hold_orders.csv": naive_orders,
        "indicator_daily.csv": indicator,
        "fast_drop_events.csv": fast_events,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    summary = {
        "symbol": "QQQ", "cost_bps": 0.0,
        "analysis_start": start.date().isoformat(), "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis), "strategy_case_count": 1,
        "formal_case_count_including_benchmarks": 3,
        "strategy": strategy_metrics, "modified_buy_hold": modified_metrics,
        "naive_buy_hold": naive_metrics, "event_counts": event_counts,
        "fast_drop_event_count": len(fast_events), "strict_boundary_equality_audit": equality,
        "max_cross_check_differences": differences,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ", "cost_bps": 0.0,
        "cost_model": "zero cost; ordinary same-Close fills; strict fast-drop theoretical StochRSI100=0.30 fill without OHLC-touch gate",
        "engine": "lib-pybroker 1.2.12 compiled portfolio replay plus independent scaled-pool state and FIFO ledger",
        "python": platform.python_version(), "parameters": params, "summary": summary,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset, "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 0.0, manifest_path)
    print(
        f"Completed scaled-pool strategy in {summary['elapsed_seconds']:.2f}s; "
        f"orders={len(reference.orders)}, contributions={len(reference.contributions)}, "
        f"fast_drop={len(fast_events)}, max ledger difference={max(differences.values(), default=0):.3g}"
    )


if __name__ == "__main__":
    main()
