#!/usr/bin/env python3
"""Run the 2000-2015 QQQ three-factor downside-risk voting experiment."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
    IndicatorRepository,
    TrendQualityParameters,
    extended_metrics,
    prepare_common_market_data,
    run_pybroker_trend_quality,
    run_reference_trend_quality,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.reference import run_buy_and_hold_reference
from quantkit.trend_risk_vote import (
    DOWNSIDE_VOLATILITY,
    MAJORITY_2_OF_3,
    RISK_PARAMETER_COLUMNS,
    RISK_VETO,
    STRICT_3_OF_3,
    TOTAL_VOLATILITY,
    RiskVoteParameters,
    build_risk_grid,
    factor_frame,
    select_principal_representative,
)
from scripts.run_sma_regime_ablation import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/ROT/ROT-v0.40a.2__26-08-21__qqq_three_factor_downside_risk_training_2000_2015"
)


def approved_dataset(manifest: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    dataset = next(item for item in manifest["datasets"] if item["symbol"] == symbol)
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} data are not approved: {dataset['effective_status']}")
    return dataset


def activity_guard(
    row: Mapping[str, Any],
    selection: Mapping[str, Any],
    subwindows: list[Mapping[str, str]],
) -> bool:
    if float(row["cagr_pct"]) < float(selection["minimum_cagr_pct"]):
        return False
    if int(row["closed_trade_count"]) < int(selection["minimum_closed_trades"]):
        return False
    if float(row["exposure_pct"]) < float(selection["minimum_full_period_exposure_pct"]):
        return False
    if bool(selection["require_positive_exposure_in_every_subwindow"]):
        return all(float(row[f"{item['window_id']}_exposure_pct"]) > 0.0 for item in subwindows)
    return True


def risk_state_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    safe = frame["risk_safe"].astype(bool)
    previous = safe.shift(1, fill_value=False)
    return {
        "risk_alarm_count": int((previous & ~safe).sum()),
        "risk_recovery_count": int((~previous & safe).sum()),
        "risk_unsafe_session_pct": float((~safe).mean() * 100.0),
    }


def evaluate_risk_cases(
    cases: pd.DataFrame,
    repository: IndicatorRepository,
    trend: TrendQualityParameters,
    dates: pd.Series,
    *,
    cost_bps: float,
    initial_cash: float,
    subwindows: list[Mapping[str, str]],
    selection: Mapping[str, Any],
) -> pd.DataFrame:
    policy = ExplicitFillPolicy("open", cost_bps)
    rows: list[dict[str, Any]] = []
    for record in cases.to_dict("records"):
        risk = RiskVoteParameters.from_mapping(record)
        frame = factor_frame(
            repository,
            trend,
            risk,
            risk_kind=str(record["risk_kind"]),
            decision_structure=str(record["decision_structure"]),
            analysis_dates=dates,
        )
        reference = run_reference_trend_quality(
            frame, policy, initial_cash=initial_cash, case_id=str(record["case_id"])
        )
        metrics = extended_metrics(reference, initial_cash=initial_cash, subwindows=subwindows)
        row = {
            **record,
            "stage": "risk_factor_grid",
            "cost_bps": cost_bps,
            **metrics,
            **risk_state_metrics(frame),
        }
        row["passes_guard"] = activity_guard(row, selection, subwindows)
        rows.append(row)
    return pd.DataFrame(rows)


def baseline_metrics(
    repository: IndicatorRepository,
    trend: TrendQualityParameters,
    dates: pd.Series,
    *,
    cost_bps: float,
    initial_cash: float,
    subwindows: list[Mapping[str, str]],
    selection: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = repository.case_frame(trend, "P24", dates)
    frame["long_factor"] = (frame["price_condition"] & frame["f2_condition"]).astype(bool)
    frame["short_factor"] = frame["f4_condition"].astype(bool)
    frame["risk_safe"] = True
    frame["risk_ratio"] = np.nan
    frame["factor_votes"] = frame["long_factor"].astype(int) + frame["short_factor"].astype(int) + 1
    reference = run_reference_trend_quality(
        frame,
        ExplicitFillPolicy("open", cost_bps),
        initial_cash=initial_cash,
        case_id="BASELINE_P24",
    )
    metrics = extended_metrics(reference, initial_cash=initial_cash, subwindows=subwindows)
    row = {
        "case_id": "BASELINE_P24",
        "decision_structure": "BASELINE_P24",
        "risk_kind": "NONE",
        "short_window": np.nan,
        "long_window": np.nan,
        "danger_ratio": np.nan,
        "recovery_gap": np.nan,
        "recovery_ratio": np.nan,
        "stage": "baseline",
        "cost_bps": cost_bps,
        **metrics,
        "risk_alarm_count": 0,
        "risk_recovery_count": 0,
        "risk_unsafe_session_pct": 0.0,
    }
    row["passes_guard"] = activity_guard(row, selection, subwindows)
    return pd.DataFrame([row]), frame


def formal_case_plan(selected: Mapping[str, Any], center: Mapping[str, Any]) -> pd.DataFrame:
    selected_risk = {name: selected[name] for name in (*RISK_PARAMETER_COLUMNS, "recovery_gap")}
    center_risk = {
        "short_window": center["short_window"],
        "long_window": center["long_window"],
        "danger_ratio": center["danger_ratio"],
        "recovery_gap": float(center["danger_ratio"]) - float(center["recovery_ratio"]),
    }
    return pd.DataFrame(
        [
            {
                "case_id": "BASELINE_P24",
                "decision_structure": "BASELINE_P24",
                "risk_kind": "NONE",
                **selected_risk,
            },
            {
                "case_id": "CENTER_RISK_VETO_DOWNSIDE",
                "decision_structure": RISK_VETO,
                "risk_kind": DOWNSIDE_VOLATILITY,
                **center_risk,
            },
            {
                "case_id": "SELECTED_RISK_VETO_DOWNSIDE",
                "decision_structure": RISK_VETO,
                "risk_kind": DOWNSIDE_VOLATILITY,
                **selected_risk,
            },
            {
                "case_id": "SELECTED_RISK_VETO_TOTAL",
                "decision_structure": RISK_VETO,
                "risk_kind": TOTAL_VOLATILITY,
                **selected_risk,
            },
            {
                "case_id": "SELECTED_STRICT_DOWNSIDE",
                "decision_structure": STRICT_3_OF_3,
                "risk_kind": DOWNSIDE_VOLATILITY,
                **selected_risk,
            },
            {
                "case_id": "SELECTED_MAJORITY_DOWNSIDE",
                "decision_structure": MAJORITY_2_OF_3,
                "risk_kind": DOWNSIDE_VOLATILITY,
                **selected_risk,
            },
        ]
    )


def formal_verify(
    plan: pd.DataFrame,
    repository: IndicatorRepository,
    trend: TrendQualityParameters,
    dates: pd.Series,
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
        if record["case_id"] == "BASELINE_P24":
            frame = repository.case_frame(trend, "P24", dates)
            frame["long_factor"] = (frame["price_condition"] & frame["f2_condition"]).astype(bool)
            frame["short_factor"] = frame["f4_condition"].astype(bool)
            frame["risk_safe"] = True
            frame["risk_ratio"] = np.nan
            risk_metrics = {
                "risk_alarm_count": 0,
                "risk_recovery_count": 0,
                "risk_unsafe_session_pct": 0.0,
            }
        else:
            risk = RiskVoteParameters.from_mapping(record)
            frame = factor_frame(
                repository,
                trend,
                risk,
                risk_kind=str(record["risk_kind"]),
                decision_structure=str(record["decision_structure"]),
                analysis_dates=dates,
            )
            risk_metrics = risk_state_metrics(frame)
        reference = run_reference_trend_quality(
            frame, policy, initial_cash=initial_cash, case_id=str(record["case_id"])
        )
        result = run_pybroker_trend_quality(frame, policy, initial_cash=initial_cash)
        actual = pybroker_daily_state(result, frame)
        check = cross_check(result, actual, reference)
        differences.update({f"{record['case_id']}.{name}": value for name, value in check.items()})
        metrics = extended_metrics(reference, initial_cash=initial_cash, subwindows=subwindows)
        metric_rows.append(
            {
                **record,
                "recovery_ratio": float(record["danger_ratio"]) - float(record["recovery_gap"]),
                "symbol": "QQQ",
                "cost_bps": cost_bps,
                **metrics,
                **risk_metrics,
                **check,
            }
        )
        actual.insert(0, "case_id", record["case_id"])
        actual.insert(1, "decision_structure", record["decision_structure"])
        daily_frames.append(actual)
        reference_frames.append(reference.daily)
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


def boundary_parameters(record: Mapping[str, Any], parameters: Mapping[str, Any]) -> list[str]:
    grid = parameters["risk_grid"]
    return [
        name
        for name in RISK_PARAMETER_COLUMNS
        if float(record[name]) in (float(min(grid[name])), float(max(grid[name])))
    ]


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
    output_root = reserve_block(context, args.run_id, "QQQ", cost_bps)
    parameters = context.config["parameters"]
    initial_cash = float(context.config["initial_cash"])
    primary_cost = float(context.config["primary_cost_bps_per_side"])

    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    qqq_dataset = approved_dataset(canonical_manifest, "QQQ")
    qqq_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    qqq = pd.read_csv(qqq_path, parse_dates=["date"])
    qqq = qqq[
        qqq["date"].between(
            pd.Timestamp(parameters["warmup_start"]),
            pd.Timestamp(parameters["training_window"]["end"]),
        )
    ].copy()
    common = prepare_common_market_data(qqq, qqq.assign(symbol="SPY"))
    analysis = common[
        common["date"].between(
            pd.Timestamp(parameters["training_window"]["start"]),
            pd.Timestamp(parameters["training_window"]["end"]),
        )
    ].copy().reset_index(drop=True)
    if analysis.empty or pd.Timestamp(analysis.iloc[0]["date"]) != pd.Timestamp("2000-03-17"):
        raise ValueError("The experiment must preserve the parent's 2000-03-17 actual start.")
    dates = analysis["date"]
    repository = IndicatorRepository(common)
    trend = TrendQualityParameters.from_mapping(parameters["frozen_trend_parameters"])
    subwindows = list(parameters["robustness_subwindows"])
    grid = build_risk_grid(parameters)
    results = evaluate_risk_cases(
        grid,
        repository,
        trend,
        dates,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        subwindows=subwindows,
        selection=parameters["selection"],
    )
    descriptive, plateau, components = select_principal_representative(results, parameters)
    primary_root = block_root(context, args.run_id, "QQQ", primary_cost)
    if cost_bps == primary_cost:
        selected = descriptive
        selection_status = "SELECTED_FROM_5BPS"
        selection_source_case_id = str(selected["case_id"])
    else:
        primary_summary_path = primary_root / "summary.json"
        if not primary_summary_path.is_file():
            raise RuntimeError("Run the frozen primary 5 bps block before 0 bps.")
        primary_summary = json.loads(primary_summary_path.read_text(encoding="utf-8"))
        selection_source_case_id = str(primary_summary["selection_source_case_id"])
        selected = results[results["case_id"].eq(selection_source_case_id)].iloc[0]
        selection_status = "FROZEN_FROM_5BPS"

    baseline, baseline_frame = baseline_metrics(
        repository,
        trend,
        dates,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        subwindows=subwindows,
        selection=parameters["selection"],
    )
    all_results = pd.concat([baseline, results], ignore_index=True, sort=False)
    plan = formal_case_plan(selected, parameters["center_risk_parameters"])
    formal, daily, reference_daily, orders, trades, differences = formal_verify(
        plan,
        repository,
        trend,
        dates,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        subwindows=subwindows,
    )
    selected_risk = RiskVoteParameters.from_mapping(selected)
    indicators = factor_frame(
        repository,
        trend,
        selected_risk,
        risk_kind=DOWNSIDE_VOLATILITY,
        decision_structure=RISK_VETO,
        analysis_dates=dates,
    )
    benchmark = run_buy_and_hold_reference(
        analysis, ExplicitFillPolicy("open", cost_bps), initial_cash=initial_cash
    )
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
                np.sqrt(
                    np.mean(
                        np.square((benchmark_equity / benchmark_equity.cummax() - 1.0) * 100.0)
                    )
                )
            ),
        }
    )
    outputs = {
        "parameter_results.csv": all_results,
        "risk_grid_results.csv": results,
        "plateau_results.csv": plateau,
        "component_summary.csv": components,
        "formal_cases.csv": formal,
        "daily.csv": daily,
        "reference_daily.csv": reference_daily,
        "orders.csv": orders,
        "trades.csv": trades,
        "representative_indicators.csv": indicators,
        "baseline_indicators.csv": baseline_frame,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
        "benchmark_results.csv": pd.DataFrame([benchmark_metrics]),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    baseline_formal = formal[formal["case_id"].eq("BASELINE_P24")].iloc[0]
    representative_formal = formal[
        formal["case_id"].eq("SELECTED_RISK_VETO_DOWNSIDE")
    ].iloc[0]
    selected_plateau = plateau[plateau["case_id"].eq(selection_source_case_id)]
    if selected_plateau.empty:
        component_id = ""
        component_cases = 0
        neighbors = 0
    else:
        plateau_record = selected_plateau.iloc[0]
        component_id = str(plateau_record["plateau_component_id"])
        component_cases = int(
            components[components["plateau_component_id"].eq(component_id)].iloc[0]["case_count"]
        )
        neighbors = int(plateau_record["plateau_neighbor_count"])
    boundary = boundary_parameters(representative_formal, parameters)
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
        "risk_grid_case_count": len(results),
        "total_case_count": len(all_results),
        "principal_eligible_case_count": int(
            (
                results["decision_structure"].eq(RISK_VETO)
                & results["risk_kind"].eq(DOWNSIDE_VOLATILITY)
                & results["passes_guard"].astype(bool)
            ).sum()
        ),
        "selection_status": selection_status,
        "selection_source_case_id": selection_source_case_id,
        "selected_in_cost_specific_one_pp_plateau": not selected_plateau.empty,
        "selected_component_id": component_id,
        "selected_component_cases": component_cases,
        "selected_direct_plateau_neighbors": neighbors,
        "representative_boundary_parameters": boundary,
        "representative_on_search_boundary": bool(boundary),
        "baseline": json_safe(baseline_formal.to_dict()),
        "representative": json_safe(representative_formal.to_dict()),
        "formal_ablation": {
            row["case_id"]: json_safe(row.to_dict()) for _, row in formal.iterrows()
        },
        "worst_subwindow_drawdown_improvement_vs_baseline_pct_points": float(
            representative_formal["worst_subwindow_max_drawdown_pct"]
            - baseline_formal["worst_subwindow_max_drawdown_pct"]
        ),
        "full_max_drawdown_improvement_vs_baseline_pct_points": float(
            representative_formal["max_drawdown_pct"] - baseline_formal["max_drawdown_pct"]
        ),
        "ulcer_index_improvement_vs_baseline_pct_points": float(
            baseline_formal["ulcer_index_pct"] - representative_formal["ulcer_index_pct"]
        ),
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
        "engine": "causal three-factor reference state machine plus lib-pybroker 1.2.12 formal verification",
        "python": platform.python_version(),
        "analysis_start": summary["training_window"]["start"],
        "analysis_end": summary["training_window"]["end"],
        "analysis_bars": len(dates),
        "parameters": parameters,
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entries": {"QQQ": qqq_dataset},
        "source_files": {
            "data/processed/daily/QQQ.csv": {
                "bytes": qqq_path.stat().st_size,
                "sha256": sha256(qqq_path),
            }
        },
        "max_cross_check_differences": differences,
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
    record_block_complete(context, args.run_id, "QQQ", cost_bps, manifest_path)
    print(
        f"cost={cost_bps:g}bps risk_cases={len(results)} representative={selection_source_case_id} "
        f"worst_dd={representative_formal['worst_subwindow_max_drawdown_pct']:.2f}% "
        f"CAGR={representative_formal['cagr_pct']:.2f}% boundary={boundary} "
        f"component={component_cases} neighbors={neighbors} "
        f"max_ledger_diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
