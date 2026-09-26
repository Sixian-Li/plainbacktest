#!/usr/bin/env python3
"""Run 2000-2015 QQQ full-position F2/F4 training and F5 ablation."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import (
    block_root,
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.full_position_trend_quality import (
    CASE_FACTORS,
    PARAMETER_COLUMNS,
    IndicatorRepository,
    TrendQualityParameters,
    extended_metrics,
    prepare_common_market_data,
    run_pybroker_trend_quality,
    run_reference_trend_quality,
    shared_analysis_slice,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference
from scripts.run_sma_regime_ablation import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.20b.1__26-08-15__qqq_full_position_sma_trend_quality_training_2000_2015"


def parameter_key(values: Mapping[str, Any]) -> tuple[float, ...]:
    return tuple(float(values[name]) for name in PARAMETER_COLUMNS)


def build_oat_cases(config: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = dict(config["baseline"])
    rows: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    keys: dict[tuple[float, ...], str] = {}
    for sweep_order, sweep in enumerate(config["stage_1_oat_sweeps"], start=1):
        parameter = str(sweep["parameter"])
        values = list(sweep["values"])
        if parameter not in PARAMETER_COLUMNS:
            raise ValueError(f"Unknown OAT parameter: {parameter}")
        if baseline[parameter] not in values:
            raise ValueError(f"OAT sweep {parameter} omits its center value.")
        for point_order, value in enumerate(values, start=1):
            params = {**baseline, parameter: value}
            key = parameter_key(params)
            case_id = keys.get(key)
            if case_id is None:
                case_id = f"S1_{len(keys) + 1:04d}"
                keys[key] = case_id
                rows.append({"case_id": case_id, **params})
            points.append(
                {
                    "sweep_order": sweep_order,
                    "sweep_id": f"OAT_{sweep_order:02d}_{parameter}",
                    "parameter": parameter,
                    "point_order": point_order,
                    "value": value,
                    "is_center": value == baseline[parameter],
                    "case_id": case_id,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(points)


def activity_guard(row: Mapping[str, Any], selection: Mapping[str, Any], subwindows: Iterable[Mapping[str, str]]) -> bool:
    if float(row["cagr_pct"]) < float(selection["minimum_cagr_pct"]):
        return False
    if int(row["closed_trade_count"]) < int(selection["minimum_closed_trades"]):
        return False
    if bool(selection["require_positive_exposure_in_every_subwindow"]):
        return all(float(row[f"{item['window_id']}_exposure_pct"]) > 0.0 for item in subwindows)
    return True


def evaluate_cases(
    cases: pd.DataFrame,
    repository: IndicatorRepository,
    analysis_dates: pd.Series,
    *,
    cost_bps: float,
    initial_cash: float,
    subwindows: list[Mapping[str, str]],
    selection: Mapping[str, Any],
    case_name: str = "P24",
    stage: str,
    apply_cagr_guard: bool = True,
) -> pd.DataFrame:
    policy = ExplicitFillPolicy("open", cost_bps)
    rows: list[dict[str, Any]] = []
    for record in cases.to_dict("records"):
        params = TrendQualityParameters.from_mapping(record)
        frame = repository.case_frame(params, case_name, analysis_dates)
        reference = run_reference_trend_quality(
            frame, policy, initial_cash=initial_cash, case_id=str(record["case_id"])
        )
        metrics = extended_metrics(
            reference, initial_cash=initial_cash, subwindows=subwindows
        )
        row = {
            **record,
            "case_name": case_name,
            "enabled_factors": "|".join(CASE_FACTORS[case_name]),
            "stage": stage,
            "cost_bps": cost_bps,
            **metrics,
        }
        row["passes_guard"] = activity_guard(row, selection, subwindows)
        if not apply_cagr_guard:
            row["passes_guard"] = (
                int(row["closed_trade_count"]) >= int(selection["minimum_closed_trades"])
                and all(float(row[f"{item['window_id']}_exposure_pct"]) > 0.0 for item in subwindows)
            )
        rows.append(row)
    return pd.DataFrame(rows)


def contiguous_true_runs(values: list[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    return runs


def select_stable_values(
    point_results: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[dict[str, list[Any]], pd.DataFrame, bool]:
    tolerance = float(config["stage_2"]["stable_band_drawdown_tolerance_pct_points"])
    required = int(config["stage_2"]["connected_values_required"])
    maximum = int(config["stage_2"]["stable_values_per_parameter"])
    center = config["baseline"]
    selected: dict[str, list[Any]] = {}
    records: list[dict[str, Any]] = []
    all_connected = True
    for _, group in point_results.groupby("sweep_id", sort=False):
        group = group.sort_values("point_order").reset_index(drop=True)
        parameter = str(group.iloc[0]["parameter"])
        eligible = group[group["passes_guard"].astype(bool)]
        best_drawdown = float(eligible["worst_subwindow_max_drawdown_pct"].max()) if not eligible.empty else float("nan")
        within_band = (
            group["passes_guard"].astype(bool)
            & group["worst_subwindow_max_drawdown_pct"].astype(float).ge(best_drawdown - tolerance)
        ) if np.isfinite(best_drawdown) else pd.Series(False, index=group.index)
        runs = [run for run in contiguous_true_runs(within_band.tolist()) if run[1] - run[0] >= required]
        connected = bool(runs)
        all_connected &= connected
        ranked = group[group["passes_guard"].astype(bool)].copy()
        ranked["center_distance"] = abs(ranked["value"].astype(float) - float(center[parameter]))
        ranked = ranked.sort_values(
            ["worst_subwindow_max_drawdown_pct", "ulcer_index_pct", "center_distance", "point_order"],
            ascending=[False, True, True, True],
        )
        chosen_indices = ranked.head(maximum).index.tolist()
        if len(chosen_indices) == maximum:
            chosen_indices = sorted(chosen_indices)
            selected[parameter] = [group.iloc[index]["value"] for index in chosen_indices]
        for index, row in group.iterrows():
            records.append(
                {
                    "sweep_id": row["sweep_id"],
                    "parameter": parameter,
                    "point_order": int(row["point_order"]),
                    "value": row["value"],
                    "case_id": row["case_id"],
                    "passes_guard": bool(row["passes_guard"]),
                    "best_drawdown_pct": best_drawdown,
                    "within_stable_band": bool(within_band.iloc[index]),
                    "connected_gate_pass": connected,
                    "selected_for_joint": index in chosen_indices,
                }
            )
    return selected, pd.DataFrame(records), all_connected


def build_joint_cases(selected: Mapping[str, list[Any]], baseline: Mapping[str, Any], maximum: int) -> pd.DataFrame:
    dimensions = [str(name) for name in selected]
    if not dimensions:
        return pd.DataFrame(columns=("case_id", *PARAMETER_COLUMNS))
    rows: list[dict[str, Any]] = []
    for case_number, values in enumerate(product(*(selected[name] for name in dimensions)), start=1):
        parameters = {**baseline, **dict(zip(dimensions, values))}
        rows.append({"case_id": f"S2_{case_number:05d}", **parameters})
    if len(rows) > maximum:
        raise RuntimeError(f"Joint grid has {len(rows)} cases, above frozen maximum {maximum}.")
    return pd.DataFrame(rows)


def choose_representative(results: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    candidates = results[results["passes_guard"].astype(bool)].copy()
    if candidates.empty:
        raise ValueError("No P24 joint case passes the frozen activity guard.")
    best = float(candidates["worst_subwindow_max_drawdown_pct"].max())
    candidates = candidates[
        candidates["worst_subwindow_max_drawdown_pct"].astype(float).ge(
            best - float(config["selection"]["plateau_drawdown_tolerance_pct_points"])
        )
    ].copy()
    for parameter in PARAMETER_COLUMNS[:-1]:
        values = candidates[parameter].astype(float)
        spread = max(float(values.max() - values.min()), 1.0)
        candidates[f"distance_{parameter}"] = abs(values - float(values.median())) / spread
    candidates["plateau_center_distance"] = candidates[
        [f"distance_{parameter}" for parameter in PARAMETER_COLUMNS[:-1]]
    ].sum(axis=1)
    return candidates.sort_values(
        [
            "ulcer_index_pct",
            "worst_63_session_return_pct",
            "max_drawdown_duration_days",
            "losing_month_avoidance_pct",
            "plateau_center_distance",
            "case_id",
        ],
        ascending=[True, False, True, False, True, True],
    ).iloc[0]


def formal_case_plan(center: Mapping[str, Any], representative: Mapping[str, Any], rs_lookbacks: list[int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scope, parameters in (("CENTER", center), ("REPRESENTATIVE", representative)):
        names = ("B0", "B2", "B4", "P24", "P245") if scope == "CENTER" else ("B0", "B2", "B4", "P24")
        for case_name in names:
            rows.append(
                {
                    "case_id": f"{scope}_{case_name}",
                    "selection_scope": scope,
                    "case_name": case_name,
                    **{name: parameters[name] for name in PARAMETER_COLUMNS},
                }
            )
    for lookback in rs_lookbacks:
        rows.append(
            {
                "case_id": f"REPRESENTATIVE_P245_R{lookback}",
                "selection_scope": "REPRESENTATIVE_F5_ABLATION",
                "case_name": "P245",
                **{name: representative[name] for name in PARAMETER_COLUMNS},
                "relative_strength_lookback": lookback,
            }
        )
    return pd.DataFrame(rows).drop_duplicates("case_id")


def formal_verify(
    plan: pd.DataFrame,
    repository: IndicatorRepository,
    analysis_dates: pd.Series,
    *,
    cost_bps: float,
    initial_cash: float,
    subwindows: list[Mapping[str, str]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    policy = ExplicitFillPolicy("open", cost_bps)
    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    reference_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    differences: dict[str, float] = {}
    for record in plan.to_dict("records"):
        params = TrendQualityParameters.from_mapping(record)
        frame = repository.case_frame(params, str(record["case_name"]), analysis_dates)
        reference = run_reference_trend_quality(
            frame, policy, initial_cash=initial_cash, case_id=str(record["case_id"])
        )
        result = run_pybroker_trend_quality(frame, policy, initial_cash=initial_cash)
        actual = pybroker_daily_state(result, frame)
        check = cross_check(result, actual, reference)
        for name, value in check.items():
            differences[f"{record['case_id']}.{name}"] = value
        metrics = extended_metrics(reference, initial_cash=initial_cash, subwindows=subwindows)
        metric_rows.append(
            {
                **record,
                "symbol": "QQQ",
                "enabled_factors": "|".join(CASE_FACTORS[str(record["case_name"])]),
                "cost_bps": cost_bps,
                **metrics,
                **check,
            }
        )
        actual.insert(0, "case_id", record["case_id"])
        actual.insert(1, "case_name", record["case_name"])
        reference_frames.append(reference.daily)
        daily_frames.append(actual)
        order_frames.append(reference.orders)
        trade_frames.append(reference.trades)
    return (
        pd.DataFrame(metric_rows),
        pd.concat(daily_frames, ignore_index=True),
        pd.concat(reference_frames, ignore_index=True),
        pd.concat(order_frames, ignore_index=True),
        pd.concat(trade_frames, ignore_index=True),
        differences,
    )


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
    cost_bps = float(args.cost_bps)
    if args.symbol != "QQQ" or args.symbol not in context.config["symbols"]:
        raise ValueError("This experiment only trades QQQ.")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("Cost scenario is not frozen in the experiment.")
    output_root = reserve_block(context, args.run_id, args.symbol, cost_bps)
    config = context.config["parameters"]
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
    end = pd.Timestamp(config["training_window"]["end"])
    warmup_start = pd.Timestamp(config["warmup_start"])
    qqq = qqq[qqq["date"].between(warmup_start, end)].copy()
    spy = spy[spy["date"].between(warmup_start, end)].copy()
    common = prepare_common_market_data(qqq, spy)
    sweeps = {item["parameter"]: item["values"] for item in config["stage_1_oat_sweeps"]}
    analysis = shared_analysis_slice(
        common,
        requested_start=config["training_window"]["requested_start"],
        end=end,
        maximum_long_sma_window=max(sweeps["long_sma_window"]),
        maximum_long_slope_lookback=max(sweeps["long_slope_lookback"]),
        maximum_short_sma_window=max(sweeps["short_sma_window"]),
        maximum_short_regression_window=max(sweeps["short_regression_window"]),
        maximum_relative_strength_lookback=max(config["relative_strength_ablation"]["lookbacks"]),
    )
    dates = analysis["date"]
    repository = IndicatorRepository(common)
    subwindows = list(config["robustness_subwindows"])
    oat_cases, oat_points = build_oat_cases(config)
    oat_results = evaluate_cases(
        oat_cases, repository, dates, cost_bps=cost_bps, initial_cash=initial_cash,
        subwindows=subwindows, selection=config["selection"], stage="stage_1_oat",
        apply_cagr_guard=False,
    )
    point_results = oat_points.merge(oat_results, on="case_id", how="left", validate="many_to_one")

    primary_block = block_root(context, args.run_id, "QQQ", primary_cost)
    if cost_bps == primary_cost:
        stable_values, stable_records, stable_gate = select_stable_values(point_results, config)
        search_ready = len(stable_values) == len(config["stage_1_oat_sweeps"])
        joint_cases = build_joint_cases(
            stable_values if search_ready else {}, config["baseline"], int(config["stage_2"]["maximum_joint_cases"])
        )
        if joint_cases.empty:
            representative = oat_results[
                oat_results.apply(lambda row: parameter_key(row) == parameter_key(config["baseline"]), axis=1)
            ].iloc[0]
            selection_status = "OAT_RETENTION_INCOMPLETE"
            joint_results = pd.DataFrame(columns=oat_results.columns)
        else:
            joint_results = evaluate_cases(
                joint_cases, repository, dates, cost_bps=cost_bps, initial_cash=initial_cash,
                subwindows=subwindows, selection=config["selection"], stage="stage_2_joint",
            )
            representative = choose_representative(joint_results, config)
            selection_status = "TRAINING_PLATEAU_REPRESENTATIVE"
    else:
        if not (primary_block / "stable_value_selection.csv").is_file():
            raise RuntimeError("Run the frozen primary 5 bps block before the 0 bps sensitivity block.")
        stable_records = pd.read_csv(primary_block / "stable_value_selection.csv")
        stable_gate = bool(stable_records["connected_gate_pass"].all())
        joint_source = pd.read_csv(primary_block / "joint_grid.csv")
        joint_cases = joint_source[["case_id", *PARAMETER_COLUMNS]].drop_duplicates("case_id")
        joint_results = evaluate_cases(
            joint_cases, repository, dates, cost_bps=cost_bps, initial_cash=initial_cash,
            subwindows=subwindows, selection=config["selection"], stage="stage_2_joint",
        ) if not joint_cases.empty else pd.DataFrame(columns=oat_results.columns)
        primary_formal = pd.read_csv(primary_block / "formal_cases.csv")
        selected = primary_formal[
            primary_formal["case_id"].eq("REPRESENTATIVE_P24")
        ].iloc[0]
        if joint_results.empty:
            representative = oat_results[
                oat_results.apply(lambda row: parameter_key(row) == parameter_key(selected), axis=1)
            ].iloc[0]
            selection_status = "OAT_RETENTION_INCOMPLETE"
        else:
            representative = joint_results[
                joint_results.apply(lambda row: parameter_key(row) == parameter_key(selected), axis=1)
            ].iloc[0]
            selection_status = "FROZEN_FROM_5BPS"

    parameter_results = pd.concat([oat_results, joint_results], ignore_index=True)
    center = config["baseline"]
    formal_plan = formal_case_plan(
        center, representative, [int(value) for value in config["relative_strength_ablation"]["lookbacks"]]
    )
    formal, daily, reference_daily, orders, trades, differences = formal_verify(
        formal_plan, repository, dates, cost_bps=cost_bps, initial_cash=initial_cash,
        subwindows=subwindows,
    )
    ablations = formal.copy()
    f5_ablation = formal[formal["selection_scope"].eq("REPRESENTATIVE_F5_ABLATION")].copy()
    representative_indicators = repository.case_frame(
        TrendQualityParameters.from_mapping(representative), "P24", dates
    )
    benchmark = run_buy_and_hold_reference(
        analysis, ExplicitFillPolicy("open", cost_bps), initial_cash=initial_cash
    )
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    benchmark_metrics.update({"case_id": "BUY_HOLD", "symbol": "QQQ", "cost_bps": cost_bps})
    benchmark_equity = benchmark.daily["equity"].astype(float)
    benchmark_metrics["ulcer_index_pct"] = float(
        np.sqrt(np.mean(np.square((benchmark_equity / benchmark_equity.cummax() - 1.0) * 100.0)))
    )

    outputs = {
        "parameter_results.csv": parameter_results,
        "stage_1_oat_points.csv": point_results,
        "stable_value_selection.csv": stable_records,
        "joint_grid.csv": joint_results,
        "ablation_results.csv": ablations,
        "f5_ablation_results.csv": f5_ablation,
        "formal_cases.csv": formal,
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

    center_p24 = formal[formal["case_id"].eq("CENTER_P24")].iloc[0]
    rep_p24 = formal[formal["case_id"].eq("REPRESENTATIVE_P24")].iloc[0]
    rep_b0 = formal[formal["case_id"].eq("REPRESENTATIVE_B0")].iloc[0]
    eligible_joint = joint_results[joint_results["passes_guard"].astype(bool)].copy()
    best_joint_drawdown = (
        float(eligible_joint["worst_subwindow_max_drawdown_pct"].max())
        if not eligible_joint.empty else float("nan")
    )
    plateau_cases = (
        eligible_joint[
            eligible_joint["worst_subwindow_max_drawdown_pct"].astype(float).ge(
                best_joint_drawdown - float(config["selection"]["plateau_drawdown_tolerance_pct_points"])
            )
        ] if np.isfinite(best_joint_drawdown) else eligible_joint.iloc[0:0]
    )
    sweep_bounds = {
        item["parameter"]: (min(item["values"]), max(item["values"]))
        for item in config["stage_1_oat_sweeps"]
    }
    boundary_parameters = [
        name for name, (low, high) in sweep_bounds.items()
        if float(rep_p24[name]) in (float(low), float(high))
    ]
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": "QQQ",
        "cost_bps": cost_bps,
        "training_window": {
            "requested_start": config["training_window"]["requested_start"],
            "actual_start": pd.Timestamp(dates.iloc[0]).date().isoformat(),
            "end": pd.Timestamp(dates.iloc[-1]).date().isoformat(),
            "bars": len(dates),
        },
        "case_counts": parameter_results.groupby("stage")["case_id"].nunique().to_dict(),
        "oat_connected_1pp_diagnostic_pass": stable_gate,
        "selection_status": selection_status,
        "joint_eligible_case_count": len(eligible_joint),
        "joint_one_pp_plateau_case_count": len(plateau_cases),
        "representative_boundary_parameters": boundary_parameters,
        "representative_on_search_boundary": bool(boundary_parameters),
        "center_p24": json_safe(center_p24.to_dict()),
        "representative": json_safe(rep_p24.to_dict()),
        "representative_b0": json_safe(rep_b0.to_dict()),
        "worst_subwindow_drawdown_improvement_vs_b0_pct_points": float(
            rep_p24["worst_subwindow_max_drawdown_pct"] - rep_b0["worst_subwindow_max_drawdown_pct"]
        ),
        "benchmark": json_safe(benchmark_metrics),
        "max_cross_check_differences": differences,
    }
    (output_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    block_manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "factor_only_symbol": "SPY",
        "cost_bps": cost_bps,
        "engine": "causal independent full-position state machine plus lib-pybroker 1.2.12 formal verification",
        "python": platform.python_version(),
        "analysis_start": summary["training_window"]["actual_start"],
        "analysis_end": summary["training_window"]["end"],
        "analysis_bars": len(dates),
        "parameters": config,
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
            block_manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    output_manifest = output_root / "manifest.json"
    output_manifest.write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", cost_bps, output_manifest)
    print(
        f"cost={cost_bps:g}bps cases={len(parameter_results):,} stable={stable_gate} "
        f"representative={rep_p24['case_id']} worst_subwindow_dd={rep_p24['worst_subwindow_max_drawdown_pct']:.2f}% "
        f"CAGR={rep_p24['cagr_pct']:.2f}% max_ledger_diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
