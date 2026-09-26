#!/usr/bin/env python3
"""Run a broad, stable-neighborhood intraday-SMA training search."""

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

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.intraday_sma_search import (
    PARAMETER_COLUMNS,
    sample_global_cases,
    sample_refined_cases,
    screen_cases,
    select_frontier_parents,
)
from quantkit.oat_sensitivity import (
    baseline_plateau_diagnostics,
    build_oat_cases,
    select_formal_oat_cases,
)
from quantkit.training_search import (
    build_one_step_neighborhoods,
    expand_search_space,
    select_stability_parents,
    select_stable_representative,
    summarize_neighborhoods,
)
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_intraday_sma_global_search import formal_case


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002"


def configured_block(context: Any, symbol: str, cost_bps: float) -> None:
    symbols = list(context.config["symbols"])
    costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if len(symbols) != 1 or symbol != symbols[0] or costs != [0.0] or cost_bps != 0.0:
        raise ValueError(f"Expected one configured zero-cost block: {symbols} / {costs}")


def selected_baseline(row: pd.Series) -> dict[str, float | bool]:
    return {
        **{name: float(row[name]) for name in PARAMETER_COLUMNS},
        "forced_reentry_enabled": True,
    }


def oat_sweeps(
    baseline: dict[str, float | bool],
    search_definition: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    labels = {
        "A_negative_days_slow": "A · 慢速转弱连续日",
        "B_slow_sma_window": "B · 慢速卖出 SMA",
        "C_fast_derivative_pct": "C · 三短均线急跌阈值 (%)",
        "D_negative_days_fast": "D · 急跌前连续转弱日",
        "E_fallback_sma_window": "E · 兜底卖出 SMA",
        "F_short_sma_center": "F 中心 · 三短均线中心",
        "F_short_sma_spacing": "F 间距 · 三短均线间隔",
        "G_short_recovery_below_pct": "G · 短均线恢复折让 (%)",
        "H_reentry_sma_window": "H · 长均线买回 SMA",
        "L_cost_stop_pct": "L · 成本止损 (%)",
        "R_forced_rebuy_pct": "R · 强制买回涨幅 (%)",
    }
    sweep_ids = {
        "A_negative_days_slow": "A",
        "B_slow_sma_window": "B",
        "C_fast_derivative_pct": "C",
        "D_negative_days_fast": "D",
        "E_fallback_sma_window": "E",
        "F_short_sma_center": "F_CENTER",
        "F_short_sma_spacing": "F_SPACING",
        "G_short_recovery_below_pct": "G",
        "H_reentry_sma_window": "H",
        "L_cost_stop_pct": "L",
        "R_forced_rebuy_pct": "R",
    }
    sweeps = []
    for name in PARAMETER_COLUMNS:
        sweep = {
            "sweep_id": sweep_ids[name],
            "parameter": name,
            "label": labels[name],
            **search_definition[name],
        }
        values = sweep.get("values")
        if values is not None and baseline[name] not in values:
            raise ValueError(f"Selected baseline {name}={baseline[name]} is off OAT grid")
        sweeps.append(sweep)
    return sweeps


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
    configured_block(context, symbol, cost_bps)
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == symbol)
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"{symbol} is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    held_out_start = pd.Timestamp(parameters["held_out_start"])
    if end >= held_out_start:
        raise ValueError("Training window overlaps the declared held-out start.")
    initial_cash = float(context.config["initial_cash"])
    raw_all = pd.read_csv(canonical_path, parse_dates=["date"])
    raw_all = raw_all[raw_all["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    raw = raw_all[raw_all["date"] <= end].reset_index(drop=True)
    if raw["date"].max() != end or raw["date"].min() != start:
        raise ValueError("Training start/end must match available first/last dates exactly.")
    if (raw["date"] >= held_out_start).any():
        raise AssertionError("Held-out rows entered the training frame.")
    analysis = raw[(raw["date"] >= start) & (raw["date"] <= end)].reset_index(drop=True)

    search_space = expand_search_space(parameters["search_space_definition"])
    started = time.perf_counter()
    global_cases = sample_global_cases(
        search_space,
        count=int(parameters["global_sample_count"]),
        seed=int(parameters["random_seed"]),
        anchors=parameters["anchors"],
    )
    global_results = screen_cases(
        raw,
        global_cases,
        initial_cash=initial_cash,
        analysis_start=start,
        analysis_end=end,
        evaluation_start="analysis_start",
    )
    global_results.insert(0, "search_stage", "global")
    parents = select_frontier_parents(
        global_results,
        per_objective=int(parameters["frontier_parent_count_per_objective"]),
    )
    refined_cases = sample_refined_cases(
        search_space,
        parents,
        global_results,
        count=int(parameters["refined_sample_count"]),
        seed=int(parameters["random_seed"]) + 1,
    )
    refined_results = screen_cases(
        raw,
        refined_cases,
        initial_cash=initial_cash,
        analysis_start=start,
        analysis_end=end,
        evaluation_start="analysis_start",
    )
    refined_results.insert(0, "search_stage", "refined")
    screening = pd.concat((global_results, refined_results), ignore_index=True)
    screening.insert(
        0,
        "screen_case_id",
        [f"SCREEN_{index:06d}" for index in range(1, len(screening) + 1)],
    )
    if screening[list(PARAMETER_COLUMNS)].duplicated().any():
        raise AssertionError("Search contains duplicate parameter cases.")
    expected = int(parameters["global_sample_count"]) + int(parameters["refined_sample_count"])
    if len(screening) != expected:
        raise AssertionError(f"Expected {expected} screened cases, observed {len(screening)}")

    stability_parents = select_stability_parents(
        screening,
        per_objective=int(parameters["stability_parent_count_per_objective"]),
    )
    neighbor_cases, neighbor_mapping = build_one_step_neighborhoods(stability_parents, search_space)
    neighbor_results = screen_cases(
        raw,
        neighbor_cases,
        initial_cash=initial_cash,
        analysis_start=start,
        analysis_end=end,
        evaluation_start="analysis_start",
    )
    neighborhood_summary = summarize_neighborhoods(
        stability_parents,
        neighbor_mapping,
        neighbor_results,
    )
    representative = select_stable_representative(neighborhood_summary)
    baseline = selected_baseline(representative)

    sweeps = oat_sweeps(baseline, parameters["search_space_definition"])
    oat_cases, oat_points = build_oat_cases(baseline, sweeps)
    oat_results = screen_cases(
        raw,
        oat_cases,
        initial_cash=initial_cash,
        analysis_start=start,
        analysis_end=end,
        evaluation_start="analysis_start",
    )
    oat_sensitivity = oat_points.merge(
        oat_results,
        on="case_id",
        how="left",
        validate="many_to_one",
    )
    oat_sensitivity.insert(
        0,
        "window_id",
        f"TRAIN_{start.year}_{end.year}",
    )
    diagnostic = parameters["oat_diagnostic"]
    plateau = baseline_plateau_diagnostics(
        oat_sensitivity,
        cagr_tolerance_pct_points=float(diagnostic["plateau_cagr_tolerance_pct_points"]),
        sharpe_tolerance=float(diagnostic["plateau_sharpe_tolerance"]),
    )

    objective_rows: list[dict[str, str]] = []
    for metric, prefix in (
        ("cagr_pct", "CAGR"),
        ("sharpe", "SHARPE"),
        ("delta_cagr_vs_buy_hold_pct_points", "DELTA_CAGR"),
        ("delta_sharpe_vs_buy_hold", "DELTA_SHARPE"),
    ):
        for rank, (_, row) in enumerate(
            screening.nlargest(int(parameters["formal_candidate_count_per_objective"]), metric).iterrows(),
            start=1,
        ):
            objective_rows.append(
                {"screen_case_id": str(row["screen_case_id"]), "selection_reason": f"{prefix}_RANK_{rank}"}
            )
    objective_rows.append(
        {
            "screen_case_id": str(representative["screen_case_id"]),
            "selection_reason": "STABLE_NEIGHBORHOOD_REPRESENTATIVE",
        }
    )
    oat_selection = select_formal_oat_cases(oat_results, oat_points)
    oat_lookup = oat_results.merge(
        oat_selection,
        on="case_id",
        how="inner",
        validate="one_to_one",
    )
    formal_candidates = screening.merge(
        pd.DataFrame(objective_rows),
        on="screen_case_id",
        how="inner",
        validate="one_to_many",
    )
    selected_rows = []
    for key, group in formal_candidates.groupby(list(PARAMETER_COLUMNS), sort=False):
        row = group.iloc[0].copy()
        row["selection_reason"] = "|".join(dict.fromkeys(group["selection_reason"].astype(str)))
        selected_rows.append(row)
    formal_candidates = pd.DataFrame(selected_rows)
    for oat in oat_lookup.itertuples(index=False):
        key = tuple(float(getattr(oat, name)) for name in PARAMETER_COLUMNS)
        exists = (
            formal_candidates[list(PARAMETER_COLUMNS)].astype(float).apply(tuple, axis=1) == key
        ).any()
        if not exists:
            row = pd.Series(oat._asdict())
            row = row.drop(labels=["case_id"])
            row["screen_case_id"] = f"OAT::{oat.case_id}"
            formal_candidates = pd.concat((formal_candidates, row.to_frame().T), ignore_index=True)
        else:
            mask = formal_candidates[list(PARAMETER_COLUMNS)].astype(float).apply(tuple, axis=1) == key
            formal_candidates.loc[mask, "selection_reason"] = formal_candidates.loc[
                mask, "selection_reason"
            ].astype(str) + f"|OAT::{oat.selection_reason}"
    formal_candidates.insert(
        0,
        "case_id",
        [f"CANDIDATE_{index:02d}" for index in range(1, len(formal_candidates) + 1)],
    )

    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in ("daily", "reference_daily", "buy_hold_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for _, candidate in formal_candidates.iterrows():
        record, frames, differences = formal_case(
            raw,
            analysis,
            candidate,
            start=start,
            end=end,
            initial_cash=initial_cash,
            evaluation_start="analysis_start",
        )
        formal_records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal_results = pd.DataFrame(formal_records)
    selected_formal = formal_results[
        formal_results["selection_reason"].str.contains("STABLE_NEIGHBORHOOD_REPRESENTATIVE", regex=False)
    ]
    if len(selected_formal) != 1:
        raise AssertionError("Stable representative was not formally verified exactly once.")

    elapsed = time.perf_counter() - started
    selected = selected_formal.iloc[0]
    summary = {
        "symbol": symbol,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "held_out_start": held_out_start.date().isoformat(),
        "held_out_rows_used": 0,
        "search_case_count": len(screening),
        "global_case_count": len(global_results),
        "refined_case_count": len(refined_results),
        "stability_parent_count": len(stability_parents),
        "unique_neighbor_case_count": len(neighbor_cases),
        "oat_case_count": len(oat_cases),
        "oat_point_count": len(oat_points),
        "formal_candidate_count": len(formal_results),
        "elapsed_seconds": elapsed,
        "selected_case_id": str(selected["case_id"]),
        "selected_parameters": {name: float(selected[name]) for name in PARAMETER_COLUMNS},
        "selected_metrics": {
            name: float(selected[name])
            for name in (
                "final_equity",
                "total_return_pct",
                "cagr_pct",
                "sharpe",
                "max_drawdown_pct",
                "exposure_pct",
                "order_count",
                "benchmark_final_equity",
                "benchmark_cagr_pct",
                "benchmark_sharpe",
                "benchmark_max_drawdown_pct",
            )
        },
        "selected_neighborhood": json_safe(representative.to_dict()),
        "max_cross_check_differences": maximum_differences,
    }
    outputs = {
        "parameter_results.csv": screening,
        "stability_parents.csv": stability_parents,
        "neighbor_cases.csv": neighbor_cases,
        "neighbor_mapping.csv": neighbor_mapping,
        "neighbor_results.csv": neighbor_results,
        "neighborhood_summary.csv": neighborhood_summary,
        "oat_cases.csv": oat_cases,
        "sweep_definition.csv": oat_points,
        "sensitivity_results.csv": oat_sensitivity,
        "baseline_plateau_diagnostics.csv": plateau,
        "formal_candidate_results.csv": formal_results,
        **{f"{name}.csv": pd.concat(frames, ignore_index=True) for name, frames in frame_groups.items()},
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
        "screening_engine": "quantkit.intraday_sma_search numba ledger; analysis-start metrics",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "parameters": parameters,
        "search_summary": summary,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "training_data_max_date": raw["date"].max().date().isoformat(),
        "held_out_rows_used": 0,
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
        f"Completed {len(screening):,} screens, {len(neighbor_cases):,} unique neighbors, "
        f"{len(oat_cases):,} OAT cases and {len(formal_results)} formal cases in {elapsed:.1f}s; "
        f"selected CAGR {selected['cagr_pct']:.4f}%, Sharpe {selected['sharpe']:.6f}"
    )


if __name__ == "__main__":
    main()
