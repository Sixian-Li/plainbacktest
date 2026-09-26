#!/usr/bin/env python3
"""Run the frozen 2000-2015 P24 F2/F4 stricter-threshold grid."""

from __future__ import annotations

import argparse
import json
import platform
from collections import deque
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import block_root, load_experiment, record_block_complete, reserve_block, sha256
from quantkit.full_position_trend_quality import (
    CASE_FACTORS,
    IndicatorRepository,
    TrendQualityParameters,
    extended_metrics,
    prepare_common_market_data,
    run_pybroker_trend_quality,
    run_reference_trend_quality,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference
from scripts.run_sma_regime_ablation import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.40b.1__26-08-28__qqq_p24_stricter_threshold_grid_2000_2015"


def build_grid(parameters: Mapping[str, Any]) -> pd.DataFrame:
    fixed = parameters["fixed_parameters"]
    grid = parameters["threshold_grid"]
    rows = []
    for f2, f4 in product(
        grid["long_slope_threshold_daily_pct"],
        grid["short_quality_threshold_daily_pct"],
    ):
        rows.append(
            {
                "case_id": f"F2_{int(round(float(f2) * 1000)):03d}_F4_{int(round(float(f4) * 1000)):03d}",
                "case_name": "P24",
                **fixed,
                "long_slope_threshold_daily_pct": float(f2),
                "short_quality_threshold_daily_pct": float(f4),
            }
        )
    result = pd.DataFrame(rows)
    expected = int(grid["combination_count_per_cost"])
    if len(result) != expected or result["case_id"].duplicated().any():
        raise ValueError(f"Frozen grid must contain {expected} unique cases.")
    return result


def holding_diagnostics(daily: pd.DataFrame, initial_cash: float) -> dict[str, float | int]:
    sessions = int(daily["is_long"].astype(bool).sum())
    years = sessions / 252.0
    final_equity = float(daily.iloc[-1]["equity"])
    holding_cagr = (
        ((final_equity / float(initial_cash)) ** (1.0 / years) - 1.0) * 100.0
        if sessions > 0
        else float("nan")
    )
    return {
        "holding_sessions": sessions,
        "holding_years_252": years,
        "holding_cagr_pct": holding_cagr,
    }


def passes_guard(row: Mapping[str, Any], parameters: Mapping[str, Any]) -> bool:
    selection = parameters["selection"]
    windows = parameters["robustness_subwindows"]
    return (
        float(row["cagr_pct"]) >= float(selection["minimum_cagr_pct"])
        and int(row["closed_trade_count"]) >= int(selection["minimum_closed_trades"])
        and float(row["exposure_pct"]) >= float(selection["minimum_full_period_exposure_pct"])
        and (
            not bool(selection["require_positive_exposure_in_every_subwindow"])
            or all(float(row[f"{item['window_id']}_exposure_pct"]) > 0.0 for item in windows)
        )
    )


def _neighbors(case_id: str, lookup: Mapping[str, tuple[int, int]]) -> list[str]:
    x, y = lookup[case_id]
    return [other for other, point in lookup.items() if abs(point[0] - x) + abs(point[1] - y) == 1]


def select_representative(
    results: pd.DataFrame, parameters: Mapping[str, Any]
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    selection = parameters["selection"]
    eligible = results[results["passes_guard"].astype(bool)].copy()
    if eligible.empty:
        raise ValueError("No threshold case passes the frozen activity guard.")
    best = float(eligible["worst_subwindow_max_drawdown_pct"].max())
    plateau = eligible[
        eligible["worst_subwindow_max_drawdown_pct"].astype(float).ge(
            best - float(selection["plateau_drawdown_tolerance_pct_points"])
        )
    ].copy()
    f2_values = sorted(results["long_slope_threshold_daily_pct"].astype(float).unique())
    f4_values = sorted(results["short_quality_threshold_daily_pct"].astype(float).unique())
    all_lookup = {
        str(row.case_id): (
            f2_values.index(float(row.long_slope_threshold_daily_pct)),
            f4_values.index(float(row.short_quality_threshold_daily_pct)),
        )
        for row in results.itertuples(index=False)
    }
    plateau_ids = set(plateau["case_id"].astype(str))
    unseen = set(plateau_ids)
    components: list[list[str]] = []
    while unseen:
        start = min(unseen)
        queue = deque([start])
        unseen.remove(start)
        component = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in _neighbors(current, all_lookup):
                if neighbor in unseen and neighbor in plateau_ids:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda values: (-len(values), values))
    component_rows = []
    for number, values in enumerate(components, start=1):
        component_id = f"C{number:02d}"
        for case_id in values:
            component_rows.append(
                {
                    "case_id": case_id,
                    "plateau_component_id": component_id,
                    "plateau_component_cases": len(values),
                    "plateau_neighbor_count": sum(
                        neighbor in plateau_ids for neighbor in _neighbors(case_id, all_lookup)
                    ),
                }
            )
    plateau = plateau.merge(pd.DataFrame(component_rows), on="case_id", validate="one_to_one")
    largest_id = str(plateau.sort_values(["plateau_component_cases", "plateau_component_id"], ascending=[False, True]).iloc[0]["plateau_component_id"])
    candidates = plateau[plateau["plateau_component_id"].eq(largest_id)].copy()
    candidates["on_search_boundary"] = (
        candidates["long_slope_threshold_daily_pct"].isin([min(f2_values), max(f2_values)])
        | candidates["short_quality_threshold_daily_pct"].isin([min(f4_values), max(f4_values)])
    )
    if bool(selection["prefer_non_boundary_candidate"]) and (~candidates["on_search_boundary"]).any():
        candidates = candidates[~candidates["on_search_boundary"]]
    chosen = candidates.sort_values(
        [
            "ulcer_index_pct",
            "worst_63_session_return_pct",
            "max_drawdown_duration_days",
            "losing_month_avoidance_pct",
            "cagr_pct",
            "case_id",
        ],
        ascending=[True, False, True, False, False, True],
    ).iloc[0]
    components_table = (
        plateau[["plateau_component_id", "plateau_component_cases"]]
        .drop_duplicates()
        .sort_values(["plateau_component_cases", "plateau_component_id"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return chosen, plateau, components_table


def approved_dataset(manifest: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    dataset = next(item for item in manifest["datasets"] if item["symbol"] == symbol)
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} data are not approved: {dataset['effective_status']}")
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or args.symbol not in context.config["symbols"]:
        raise ValueError("This experiment only trades QQQ.")
    cost_bps = float(args.cost_bps)
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("Cost scenario is not frozen in the experiment.")
    output_root = reserve_block(context, args.run_id, "QQQ", cost_bps)
    parameters = context.config["parameters"]
    initial_cash = float(context.config["initial_cash"])
    primary_cost = float(context.config["primary_cost_bps_per_side"])

    manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    qqq_dataset = approved_dataset(canonical_manifest, "QQQ")
    spy_dataset = approved_dataset(canonical_manifest, "SPY")
    qqq_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    spy_path = WORKSPACE_ROOT / "data/processed/daily/SPY.csv"
    qqq = pd.read_csv(qqq_path, parse_dates=["date"])
    spy = pd.read_csv(spy_path, parse_dates=["date"])
    start = pd.Timestamp(parameters["training_window"]["start"])
    end = pd.Timestamp(parameters["training_window"]["end"])
    warmup = pd.Timestamp(parameters["warmup_start"])
    qqq = qqq[qqq["date"].between(warmup, end)].copy()
    spy = spy[spy["date"].between(warmup, end)].copy()
    common = prepare_common_market_data(qqq, spy)
    analysis = common[common["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or pd.Timestamp(analysis.iloc[0]["date"]) != start:
        raise ValueError("The frozen analysis must start exactly on 2000-03-17.")
    dates = analysis["date"]
    repository = IndicatorRepository(common)
    grid = build_grid(parameters)
    policy = ExplicitFillPolicy("open", cost_bps)
    metric_rows, daily_frames, reference_frames = [], [], []
    order_frames, trade_frames, indicator_frames = [], [], []
    differences: dict[str, float] = {}
    for record in grid.to_dict("records"):
        trend = TrendQualityParameters.from_mapping(record)
        frame = repository.case_frame(trend, "P24", dates)
        reference = run_reference_trend_quality(
            frame, policy, initial_cash=initial_cash, case_id=str(record["case_id"])
        )
        result = run_pybroker_trend_quality(frame, policy, initial_cash=initial_cash)
        actual = pybroker_daily_state(result, frame)
        check = cross_check(result, actual, reference)
        differences.update({f"{record['case_id']}.{name}": value for name, value in check.items()})
        metrics = extended_metrics(
            reference,
            initial_cash=initial_cash,
            subwindows=parameters["robustness_subwindows"],
        )
        hold = holding_diagnostics(reference.daily, initial_cash)
        row = {
            **record,
            "symbol": "QQQ",
            "enabled_factors": "|".join(CASE_FACTORS["P24"]),
            "cost_bps": cost_bps,
            **metrics,
            **hold,
            **check,
        }
        row["passes_guard"] = passes_guard(row, parameters)
        metric_rows.append(row)
        actual.insert(0, "case_id", record["case_id"])
        daily_frames.append(actual)
        reference_frames.append(reference.daily)
        order_frames.append(reference.orders)
        trade_frames.append(reference.trades)
        tagged = frame.copy()
        tagged.insert(0, "case_id", record["case_id"])
        indicator_frames.append(tagged)
    results = pd.DataFrame(metric_rows)
    daily = pd.concat(daily_frames, ignore_index=True)
    reference_daily = pd.concat(reference_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    all_indicators = pd.concat(indicator_frames, ignore_index=True)
    primary_root = block_root(context, args.run_id, "QQQ", primary_cost)
    if cost_bps == primary_cost:
        representative, plateau, components = select_representative(results, parameters)
        selection_status = "SELECTED_FROM_5BPS"
    else:
        primary_summary_path = primary_root / "summary.json"
        if not primary_summary_path.is_file():
            raise RuntimeError("Run the frozen primary 5 bps block before 0 bps.")
        primary_summary = json.loads(primary_summary_path.read_text(encoding="utf-8"))
        representative = results[results["case_id"].eq(primary_summary["selection_source_case_id"])].iloc[0]
        _, plateau, components = select_representative(results, parameters)
        selection_status = "FROZEN_FROM_5BPS"
    representative_case = str(representative["case_id"])
    representative_indicators = all_indicators[all_indicators["case_id"].eq(representative_case)].copy()

    benchmark = run_buy_and_hold_reference(analysis, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    benchmark_equity = benchmark.daily["equity"].astype(float)
    benchmark_metrics.update(
        {
            "case_id": "BUY_HOLD",
            "symbol": "QQQ",
            "cost_bps": cost_bps,
            "ulcer_index_pct": float(
                np.sqrt(np.mean(np.square((benchmark_equity / benchmark_equity.cummax() - 1.0) * 100.0)))
            ),
        }
    )
    outputs = {
        "parameter_results.csv": results,
        "formal_cases.csv": results,
        "plateau_results.csv": plateau,
        "component_summary.csv": components,
        "daily.csv": daily,
        "reference_daily.csv": reference_daily,
        "orders.csv": orders,
        "trades.csv": trades,
        "representative_indicators.csv": representative_indicators,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
        "benchmark_results.csv": pd.DataFrame([benchmark_metrics]),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    f2_values = parameters["threshold_grid"]["long_slope_threshold_daily_pct"]
    f4_values = parameters["threshold_grid"]["short_quality_threshold_daily_pct"]
    boundary_parameters = []
    if float(representative["long_slope_threshold_daily_pct"]) in (min(f2_values), max(f2_values)):
        boundary_parameters.append("long_slope_threshold_daily_pct")
    if float(representative["short_quality_threshold_daily_pct"]) in (min(f4_values), max(f4_values)):
        boundary_parameters.append("short_quality_threshold_daily_pct")
    plateau_row = plateau[plateau["case_id"].eq(representative_case)]
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": "QQQ",
        "cost_bps": cost_bps,
        "training_window": {
            "start": pd.Timestamp(dates.iloc[0]).date().isoformat(),
            "end": pd.Timestamp(dates.iloc[-1]).date().isoformat(),
            "bars": len(dates),
        },
        "grid_case_count": len(results),
        "selection_status": selection_status,
        "selection_source_case_id": representative_case,
        "representative": json_safe(representative.to_dict()),
        "original_p24": json_safe(
            results[
                results["long_slope_threshold_daily_pct"].eq(parameters["original_p24"]["long_slope_threshold_daily_pct"])
                & results["short_quality_threshold_daily_pct"].eq(parameters["original_p24"]["short_quality_threshold_daily_pct"])
            ].iloc[0].to_dict()
        ),
        "representative_boundary_parameters": boundary_parameters,
        "representative_on_search_boundary": bool(boundary_parameters),
        "selected_component_cases": int(plateau_row.iloc[0]["plateau_component_cases"]) if not plateau_row.empty else 0,
        "selected_direct_plateau_neighbors": int(plateau_row.iloc[0]["plateau_neighbor_count"]) if not plateau_row.empty else 0,
        "benchmark": json_safe(benchmark_metrics),
        "max_cross_check_differences": differences,
    }
    (output_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": cost_bps,
        "engine": "causal P24 reference ledger plus lib-pybroker 1.2.12 formal verification for every grid case",
        "python": platform.python_version(),
        "analysis_start": summary["training_window"]["start"],
        "analysis_end": summary["training_window"]["end"],
        "analysis_bars": len(dates),
        "parameters": parameters,
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entries": {"QQQ": qqq_dataset, "SPY": spy_dataset},
        "source_files": {
            "data/processed/daily/QQQ.csv": {"bytes": qqq_path.stat().st_size, "sha256": sha256(qqq_path)},
            "data/processed/daily/SPY.csv": {"bytes": spy_path.stat().st_size, "sha256": sha256(spy_path)},
        },
        "max_cross_check_differences": differences,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    block_manifest_path = output_root / "manifest.json"
    block_manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", cost_bps, block_manifest_path)
    print(
        f"cost={cost_bps:g}bps cases={len(results)} representative={representative_case} "
        f"CAGR={representative['cagr_pct']:.2f}% maxDD={representative['max_drawdown_pct']:.2f}% "
        f"held={int(representative['holding_sessions'])} holdingCAGR={representative['holding_cagr_pct']:.2f}% "
        f"max_ledger_diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
