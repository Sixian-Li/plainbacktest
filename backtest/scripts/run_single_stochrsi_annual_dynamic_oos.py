#!/usr/bin/env python3
"""Run the QQQ annual walk-forward single-StochRSI schedule and fixed baselines."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_stochrsi_cross_threshold_grid import run_case


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70b.3__26-08-30__qqq_single_stochrsi_annual_dynamic_oos_2011_2021"


def validate_schedule(schedule: list[dict[str, Any]], start_year: int, end_year: int) -> dict[int, int]:
    years = [int(item["application_year"]) for item in schedule]
    expected = list(range(start_year, end_year + 1))
    if years != expected or len(set(years)) != len(years):
        raise ValueError("annual schedule must cover each application year exactly once in order")
    mapping: dict[int, int] = {}
    for item in schedule:
        year, period = int(item["application_year"]), int(item["period"])
        if period < 2:
            raise ValueError("StochRSI period must be at least 2")
        source_end = pd.Timestamp(item["source_end"])
        if source_end >= pd.Timestamp(year=year - 1, month=1, day=1):
            raise ValueError("each source window must end before the full gap calendar year")
        mapping[year] = period
    return mapping


def prepare_annual_schedule(
    raw: pd.DataFrame,
    schedule: list[dict[str, Any]],
    *,
    analysis_start: pd.Timestamp,
    analysis_end: pd.Timestamp,
    buy_threshold: float,
    sell_threshold: float,
    cost_bps: float,
) -> tuple[pd.DataFrame, TimingSpec]:
    mapping = validate_schedule(schedule, analysis_start.year, analysis_end.year)
    periods = sorted(set(mapping.values()))
    prepared_by_period = {
        period: prepare_dual_stochrsi_data(
            raw,
            TimingSpec(
                "CROSS", cost_bps=cost_bps, periods=(period,),
                buy_threshold=buy_threshold, sell_threshold=sell_threshold,
            ),
        )
        for period in periods
    }
    prepared = prepared_by_period[periods[0]].copy()
    prepared["buy_trigger"] = np.nan
    prepared["sell_trigger"] = np.nan
    prepared["buy_direction"] = "up"
    prepared["sell_direction"] = "down"
    prepared["buy_eligible"] = False
    prepared["sell_eligible"] = False
    prepared["buy_signal"] = "BUY_STOCHRSI_SCHEDULE_UNASSIGNED"
    prepared["sell_signal"] = "SELL_STOCHRSI_SCHEDULE_UNASSIGNED"
    prepared["selected_period"] = pd.Series(pd.NA, index=prepared.index, dtype="Int64")
    for year, period in mapping.items():
        mask = prepared["date"].dt.year.eq(year)
        source = prepared_by_period[period]
        for column in ("buy_trigger", "sell_trigger", "buy_direction", "sell_direction", "buy_eligible", "sell_eligible"):
            prepared.loc[mask, column] = source.loc[mask, column]
        prepared.loc[mask, "buy_signal"] = f"BUY_STOCHRSI_P{period:03d}_CROSS"
        prepared.loc[mask, "sell_signal"] = f"SELL_STOCHRSI_P{period:03d}_CROSS"
        prepared.loc[mask, "selected_period"] = period
    analysis_mask = prepared["date"].between(analysis_start, analysis_end)
    if prepared.loc[analysis_mask, "selected_period"].isna().any():
        raise RuntimeError("annual period schedule leaves analysis sessions unassigned")
    execution_spec = TimingSpec(
        "CROSS", cost_bps=cost_bps, periods=(periods[0],),
        buy_threshold=buy_threshold, sell_threshold=sell_threshold,
    )
    return prepared, execution_spec


def annual_returns(daily: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    rows = []
    previous = float(initial_cash)
    for year, group in daily.groupby(pd.to_datetime(daily["date"]).dt.year, sort=True):
        ending = float(group.iloc[-1]["equity"])
        rows.append({
            "year": int(year), "start_equity": previous, "end_equity": ending,
            "return_pct": (ending / previous - 1.0) * 100.0,
        })
        previous = ending
    return pd.DataFrame(rows)


def with_case(frame: pd.DataFrame, case_id: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    parameters = context.config["parameters"]
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This frozen experiment requires QQQ only")
    if float(args.cost_bps) != float(parameters["cost_bps"]):
        raise ValueError("Cost does not match the frozen experiment")
    output_root = reserve_block(context, args.run_id, "QQQ", float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest = json.loads((WORKSPACE_ROOT / "data/processed/manifest.json").read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    start, end = pd.Timestamp(parameters["analysis_start"]), pd.Timestamp(parameters["analysis_end"])
    analysis = raw[raw["date"].between(start, end)].reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen analysis boundaries do not match canonical QQQ sessions")

    cash = float(context.config["initial_cash"])
    buy_threshold, sell_threshold = float(parameters["buy_threshold"]), float(parameters["sell_threshold"])
    started = time.perf_counter()
    dynamic_prepared, dynamic_spec = prepare_annual_schedule(
        raw, parameters["dynamic_schedule"], analysis_start=start, analysis_end=end,
        buy_threshold=buy_threshold, sell_threshold=sell_threshold, cost_bps=float(args.cost_bps),
    )
    cases: list[tuple[str, str, pd.DataFrame, TimingSpec]] = [
        ("DYNAMIC_CAGR", "年度CAGR冠军换参", dynamic_prepared, dynamic_spec)
    ]
    for period in parameters["baseline_periods"]:
        period = int(period)
        spec = TimingSpec(
            "CROSS", cost_bps=float(args.cost_bps), periods=(period,),
            buy_threshold=buy_threshold, sell_threshold=sell_threshold,
        )
        cases.append((f"FIXED_{period:03d}", f"固定周期{period}", prepare_dual_stochrsi_data(raw, spec), spec))

    records, daily_frames, reference_frames, order_frames, trade_frames, plan_frames, annual_frames = [], [], [], [], [], [], []
    maximum_differences: dict[str, float] = {}
    schedule_map = {int(item["application_year"]): int(item["period"]) for item in parameters["dynamic_schedule"]}
    for case_id, label, prepared, spec in cases:
        metrics, actual, reference_daily, orders, trades, checked = run_case(
            prepared, spec, start=start, end=end, initial_cash=cash
        )
        plans = checked.pop("signal_plans")
        periods = actual["date"].dt.year.map(schedule_map) if case_id == "DYNAMIC_CAGR" else pd.Series(int(spec.periods[0]), index=actual.index)
        actual = actual.copy(); actual["selected_period"] = periods.to_numpy(int)
        reference_daily = reference_daily.copy(); reference_daily["selected_period"] = periods.to_numpy(int)
        plans = plans.copy(); plans["selected_period"] = periods.to_numpy(int)
        held_bars = int((actual["shares"] > 0).sum())
        holding_cagr = ((float(metrics["final_equity"]) / cash) ** (252.0 / held_bars) - 1.0) * 100.0 if held_bars else np.nan
        records.append({
            "case_id": case_id, "label": label, **metrics,
            "held_bars": held_bars, "holding_period_cagr_pct": holding_cagr,
            "first_entry_date": orders.iloc[0]["date"] if not orders.empty else None,
            **{name: float(value) for name, value in checked.items()},
        })
        daily_frames.append(with_case(actual, case_id))
        reference_frames.append(with_case(reference_daily, case_id))
        order_frames.append(with_case(orders, case_id))
        trade_frames.append(with_case(trades, case_id))
        plan_frames.append(with_case(plans, case_id))
        yearly = annual_returns(actual, cash)
        yearly.insert(0, "case_id", case_id); annual_frames.append(yearly)
        for name, value in checked.items():
            maximum_differences[name] = max(maximum_differences.get(name, 0.0), float(value))

    benchmark_daily, benchmark_orders, benchmark_metrics = buy_hold(
        analysis, initial_cash=cash, cost_bps=float(args.cost_bps)
    )
    benchmark_yearly = annual_returns(benchmark_daily, cash)
    benchmark_yearly.insert(0, "case_id", "BUY_HOLD")
    results = pd.DataFrame(records)
    normalize_frame(results).to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(order_frames, ignore_index=True)).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(trade_frames, ignore_index=True)).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(daily_frames, ignore_index=True)).to_csv(output_root / "daily.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(reference_frames, ignore_index=True)).to_csv(output_root / "reference_daily.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat(plan_frames, ignore_index=True)).to_csv(output_root / "signal_plans.csv", index=False, lineterminator="\n")
    normalize_frame(pd.concat([*annual_frames, benchmark_yearly], ignore_index=True)).to_csv(output_root / "calendar_year_returns.csv", index=False, lineterminator="\n")
    normalize_frame(pd.DataFrame(parameters["dynamic_schedule"])).to_csv(output_root / "period_schedule.csv", index=False, lineterminator="\n")
    normalize_frame(benchmark_daily).to_csv(output_root / "buy_hold_daily.csv", index=False, lineterminator="\n")
    normalize_frame(benchmark_orders).to_csv(output_root / "buy_hold_orders.csv", index=False, lineterminator="\n")

    summary = {
        "symbol": "QQQ", "cost_bps": float(args.cost_bps), "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(), "strategy_case_count": len(cases),
        "benchmark": benchmark_metrics, "max_cross_check_differences": maximum_differences,
        "parent_selection_artifact_sha256": parameters["parent_selection_artifact_sha256"],
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ", "cost_bps": float(args.cost_bps),
        "engine": "lib-pybroker 1.2.12 plus quantkit.dual_stochrsi_timing independent ledger for every strategy case",
        "python": platform.python_version(), "parameters": parameters, "summary": summary,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)), "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"], "source_manifest_entry": dataset,
        "selection_source": {
            "parent_experiment_id": parameters["parent_experiment_id"], "parent_run_id": parameters["parent_run_id"],
            "artifact": parameters["parent_selection_artifact"], "sha256": parameters["parent_selection_artifact_sha256"],
        },
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_block_complete(context, args.run_id, "QQQ", float(args.cost_bps), manifest_path)
    print(json.dumps({"cases": len(cases), "seconds": summary["elapsed_seconds"], "max_difference": max(maximum_differences.values(), default=0.0)}))


if __name__ == "__main__":
    main()
