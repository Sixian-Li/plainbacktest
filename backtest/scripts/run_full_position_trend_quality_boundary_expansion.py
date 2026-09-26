#!/usr/bin/env python3
"""Run the 2000-2015 QQQ P24 four-dimensional boundary expansion."""

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
from quantkit.experiment import block_root, load_experiment, record_block_complete, reserve_block, sha256
from quantkit.full_position_trend_quality import (
    CASE_FACTORS,
    PARAMETER_COLUMNS,
    IndicatorRepository,
    TrendQualityParameters,
    prepare_common_market_data,
)
from quantkit.metrics import calculate_metrics
from quantkit.reference import run_buy_and_hold_reference
from quantkit.trend_quality_boundary import (
    COORDINATE_COLUMNS,
    EXPANDED_PARAMETER_COLUMNS,
    build_boundary_grid,
    select_boundary_representative,
)
from scripts.run_full_position_trend_quality_training import (
    evaluate_cases,
    formal_verify,
    parameter_key,
)
from scripts.run_sma_regime_ablation import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.20b.2__26-08-15__qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015"


def formal_case_plan(parent: Mapping[str, Any], representative: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "case_id": "PARENT_P24",
            "selection_scope": "PARENT_REPRESENTATIVE",
            "case_name": "P24",
            **{name: parent[name] for name in PARAMETER_COLUMNS},
        }
    )
    for case_name in ("B0", "B2", "B4", "P24"):
        rows.append(
            {
                "case_id": f"EXPANDED_{case_name}",
                "selection_scope": "EXPANDED_REPRESENTATIVE",
                "case_name": case_name,
                **{name: representative[name] for name in PARAMETER_COLUMNS},
            }
        )
    rows.append(
        {
            "case_id": "EXPANDED_P245_R120",
            "selection_scope": "FIXED_F5_ABLATION",
            "case_name": "P245",
            **{name: representative[name] for name in PARAMETER_COLUMNS},
            "relative_strength_lookback": 120,
        }
    )
    return pd.DataFrame(rows)


def approved_dataset(manifest: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    dataset = next(item for item in manifest["datasets"] if item["symbol"] == symbol)
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} data are not approved: {dataset['effective_status']}")
    return dataset


def boundary_parameters(record: Mapping[str, Any], parameters: Mapping[str, Any]) -> list[str]:
    expanded = parameters["expanded_grid"]
    return [
        name for name in EXPANDED_PARAMETER_COLUMNS
        if float(record[name]) in (float(min(expanded[name])), float(max(expanded[name])))
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
    spy_dataset = approved_dataset(canonical_manifest, "SPY")
    qqq_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    spy_path = WORKSPACE_ROOT / "data/processed/daily/SPY.csv"
    qqq = pd.read_csv(qqq_path, parse_dates=["date"])
    spy = pd.read_csv(spy_path, parse_dates=["date"])
    warmup_start = pd.Timestamp(parameters["warmup_start"])
    end = pd.Timestamp(parameters["training_window"]["end"])
    qqq = qqq[qqq["date"].between(warmup_start, end)].copy()
    spy = spy[spy["date"].between(warmup_start, end)].copy()
    common = prepare_common_market_data(qqq, spy)
    analysis = common[
        common["date"].between(
            pd.Timestamp(parameters["training_window"]["start"]), end
        )
    ].copy().reset_index(drop=True)
    if analysis.empty or pd.Timestamp(analysis.iloc[0]["date"]) != pd.Timestamp("2000-03-17"):
        raise ValueError("The child experiment must preserve the parent's 2000-03-17 actual start.")
    dates = analysis["date"]
    repository = IndicatorRepository(common)
    subwindows = list(parameters["robustness_subwindows"])
    grid = build_boundary_grid(parameters)
    results = evaluate_cases(
        grid,
        repository,
        dates,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        subwindows=subwindows,
        selection=parameters["selection"],
        stage="expanded_boundary_grid",
    )
    descriptive, plateau, components = select_boundary_representative(results, parameters)
    primary_block = block_root(context, args.run_id, "QQQ", primary_cost)
    if cost_bps == primary_cost:
        representative = descriptive
        selection_status = "EXPANDED_TRAINING_REPRESENTATIVE"
        selection_source_case_id = str(representative["case_id"])
    else:
        primary_summary_path = primary_block / "summary.json"
        if not primary_summary_path.is_file():
            raise RuntimeError("Run the frozen primary 5 bps block before the 0 bps sensitivity block.")
        primary_summary = json.loads(primary_summary_path.read_text(encoding="utf-8"))
        selection_source_case_id = str(primary_summary["selection_source_case_id"])
        representative = results[results["case_id"].eq(selection_source_case_id)].iloc[0]
        selection_status = "FROZEN_FROM_5BPS"

    parent_parameters = parameters["parent_representative"]
    parent_key = parameter_key(parent_parameters)
    parent_grid = results[results.apply(lambda row: parameter_key(row) == parent_key, axis=1)]
    if len(parent_grid) != 1:
        raise AssertionError("The parent representative must appear exactly once in the expanded grid.")
    formal_plan = formal_case_plan(parent_parameters, representative)
    formal, daily, reference_daily, orders, trades, differences = formal_verify(
        formal_plan,
        repository,
        dates,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        subwindows=subwindows,
    )
    indicators = repository.case_frame(
        TrendQualityParameters.from_mapping(representative), "P24", dates
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
                        np.square(
                            (benchmark_equity / benchmark_equity.cummax() - 1.0) * 100.0
                        )
                    )
                )
            ),
        }
    )
    outputs = {
        "parameter_results.csv": results,
        "plateau_results.csv": plateau,
        "component_summary.csv": components,
        "formal_cases.csv": formal,
        "daily.csv": daily,
        "reference_daily.csv": reference_daily,
        "orders.csv": orders,
        "trades.csv": trades,
        "representative_indicators.csv": indicators,
        "buy_hold_daily.csv": benchmark.daily,
        "buy_hold_orders.csv": benchmark.orders,
        "benchmark_results.csv": pd.DataFrame([benchmark_metrics]),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    parent_formal = formal[formal["case_id"].eq("PARENT_P24")].iloc[0]
    expanded_formal = formal[formal["case_id"].eq("EXPANDED_P24")].iloc[0]
    f5_formal = formal[formal["case_id"].eq("EXPANDED_P245_R120")].iloc[0]
    boundary = boundary_parameters(expanded_formal, parameters)
    selected_plateau = plateau[plateau["case_id"].eq(selection_source_case_id)]
    if selected_plateau.empty:
        selected_component_id = ""
        selected_component_cases = 0
        selected_neighbors = 0
    else:
        selected_record = selected_plateau.iloc[0]
        selected_component_id = str(selected_record["plateau_component_id"])
        selected_component_cases = int(
            components[
                components["plateau_component_id"].eq(selected_component_id)
            ].iloc[0]["case_count"]
        )
        selected_neighbors = int(selected_record["plateau_neighbor_count"])
    selected_in_descriptive_plateau = not selected_plateau.empty
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
        "eligible_case_count": int(results["passes_guard"].astype(bool).sum()),
        "one_pp_plateau_case_count": len(plateau),
        "component_count": len(components),
        "selection_status": selection_status,
        "selection_source_case_id": selection_source_case_id,
        "selected_in_cost_specific_one_pp_plateau": selected_in_descriptive_plateau,
        "selected_component_id": selected_component_id,
        "selected_component_cases": selected_component_cases,
        "selected_direct_plateau_neighbors": selected_neighbors,
        "representative_boundary_parameters": boundary,
        "representative_on_search_boundary": bool(boundary),
        "parent": json_safe(parent_formal.to_dict()),
        "representative": json_safe(expanded_formal.to_dict()),
        "fixed_f5_ablation": json_safe(f5_formal.to_dict()),
        "worst_subwindow_drawdown_improvement_vs_parent_pct_points": float(
            expanded_formal["worst_subwindow_max_drawdown_pct"]
            - parent_formal["worst_subwindow_max_drawdown_pct"]
        ),
        "ulcer_index_improvement_vs_parent_pct_points": float(
            parent_formal["ulcer_index_pct"] - expanded_formal["ulcer_index_pct"]
        ),
        "f5_worst_subwindow_drawdown_improvement_vs_p24_pct_points": float(
            f5_formal["worst_subwindow_max_drawdown_pct"]
            - expanded_formal["worst_subwindow_max_drawdown_pct"]
        ),
        "f5_ulcer_index_improvement_vs_p24_pct_points": float(
            expanded_formal["ulcer_index_pct"] - f5_formal["ulcer_index_pct"]
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
        "factor_only_symbol": "SPY",
        "cost_bps": cost_bps,
        "engine": "causal independent P24 boundary-grid state machine plus lib-pybroker 1.2.12 formal verification",
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
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", cost_bps, manifest_path)
    print(
        f"cost={cost_bps:g}bps cases={len(results):,} eligible={summary['eligible_case_count']:,} "
        f"plateau={len(plateau):,} components={len(components):,} representative={selection_source_case_id} "
        f"dd={expanded_formal['worst_subwindow_max_drawdown_pct']:.2f}% "
        f"CAGR={expanded_formal['cagr_pct']:.2f}% boundary={boundary} "
        f"neighbors={selected_neighbors} max_ledger_diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
