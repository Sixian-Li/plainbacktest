#!/usr/bin/env python3
"""Run the frozen full-history robust a/b selection experiment for QQQ."""

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
from quantkit.intraday_sma_threshold import prepare_intraday_threshold_data
from quantkit.intraday_sma_threshold_search import exhaustive_cases, screen_cases
from quantkit.intraday_sma_threshold_selection import (
    cscv_pbo,
    deflated_sharpe_probability,
    effective_trial_count,
    rolling_five_year_results,
    screen_continuous_cases,
    select_stable_plateau,
    start_sensitivity_results,
)
from quantkit.metrics import calculate_metrics
from scripts.run_intraday_sma200_threshold_grid import (
    FORMAL_LEDGER_TOLERANCE,
    benchmark_state,
    formal_case,
    inclusive_range,
)
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection"


def _single_match(frame: pd.DataFrame, a_pct: float, b_pct: float) -> pd.Series:
    match = frame[
        np.isclose(frame["a_pct"], float(a_pct))
        & np.isclose(frame["b_pct"], float(b_pct))
    ]
    if len(match) != 1:
        raise AssertionError(f"Expected exactly one row for a={a_pct}, b={b_pct}.")
    return match.iloc[0]


def select_formal_candidates(
    results: pd.DataFrame,
    plateau: dict[str, Any],
    anchor: dict[str, float],
) -> pd.DataFrame:
    representative = plateau["representative"]
    selections: list[tuple[str, pd.Series]] = [
        (
            "FINAL_STABLE_REPRESENTATIVE",
            _single_match(results, representative["a_pct"], representative["b_pct"]),
        ),
        ("FULL_HISTORY_CAGR_MAX", results.loc[results["cagr_pct"].idxmax()]),
        ("FULL_HISTORY_SHARPE_MAX", results.loc[results["sharpe"].idxmax()]),
        (
            "ROLLING_5Y_Q25_MAX",
            results.loc[results["rolling_5y_cagr_q25_pct"].idxmax()],
        ),
        (
            "RESTART_10Y_Q25_MAX",
            results.loc[results["restart_10y_cagr_q25_pct"].idxmax()],
        ),
        (
            "PREDECLARED_REFERENCE_ANCHOR",
            _single_match(results, anchor["a_pct"], anchor["b_pct"]),
        ),
    ]
    grouped: dict[str, dict[str, Any]] = {}
    reasons: dict[str, list[str]] = {}
    for reason, row in selections:
        screen_id = str(row["case_id"])
        grouped[screen_id] = row.to_dict()
        reasons.setdefault(screen_id, []).append(reason)
    rows: list[dict[str, Any]] = []
    for index, (screen_id, row) in enumerate(grouped.items(), start=1):
        row["screen_case_id"] = screen_id
        row["case_id"] = f"FORMAL_{index:02d}"
        row["selection_reason"] = "|".join(reasons[screen_id])
        rows.append(row)
    return pd.DataFrame(rows)


def _daily_returns(daily: pd.DataFrame, case_id: str) -> np.ndarray:
    frame = daily[daily["case_id"] == case_id].sort_values("date")
    if frame.empty:
        raise AssertionError(f"Missing daily state for formal case {case_id}.")
    return frame["equity"].astype(float).pct_change().fillna(0.0).to_numpy(float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    symbol = str(args.symbol)
    cost_bps = float(args.cost_bps)
    if symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This frozen experiment requires QQQ only.")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if configured_costs != [5.0] or cost_bps != 5.0:
        raise ValueError("This frozen selection experiment requires exactly 5 bps per side.")
    output_root = reserve_block(context, args.run_id, symbol, cost_bps)

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == symbol
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"QQQ is not approved: {dataset_manifest['effective_status']}")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    window = int(context.config["strategy"]["sma_window"])
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    prepared = prepare_intraday_threshold_data(raw, window)
    analysis = prepared[
        (prepared["date"] >= start) & (prepared["date"] <= end)
    ].reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen normalized analysis dates do not match canonical QQQ sessions.")

    a_values = inclusive_range(parameters["a_pct_range"])
    b_values = inclusive_range(parameters["b_pct_range"])
    correction_pct = float(parameters["correction_pct"])
    cases = exhaustive_cases(a_values, b_values, [correction_pct])
    declared = int(parameters["combination_count"])
    if len(cases) != declared:
        raise AssertionError(f"Expected {declared} cases, generated {len(cases)}.")

    started = time.perf_counter()
    continuous = screen_continuous_cases(
        analysis,
        cases,
        window=window,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
        pbo_block_count=int(parameters["pbo"]["block_count"]),
    )
    rolling_summary, rolling_long = rolling_five_year_results(
        continuous,
        analysis["date"],
        continuous.metrics["case_id"],
    )

    restart_years = [int(value) for value in parameters["restart_sensitivity"]["start_years"]]
    restart_cagrs: list[np.ndarray] = []
    restart_windows: list[dict[str, Any]] = []
    for start_year in restart_years:
        requested_start = pd.Timestamp(year=start_year, month=1, day=1)
        requested_end = pd.Timestamp(year=start_year + 9, month=12, day=31)
        restart = prepared[
            (prepared["date"] >= requested_start) & (prepared["date"] <= requested_end)
        ].reset_index(drop=True)
        if restart.empty or restart["prior_sum"].isna().any():
            raise ValueError(f"Restart window {start_year} has incomplete analysis data.")
        screened = screen_cases(
            restart,
            cases,
            window=window,
            cost_bps=cost_bps,
            initial_cash=initial_cash,
        )
        restart_cagrs.append(screened["cagr_pct"].to_numpy(float))
        restart_windows.append(
            {
                "start_year": start_year,
                "analysis_start": pd.Timestamp(restart.iloc[0]["date"]).date().isoformat(),
                "analysis_end": pd.Timestamp(restart.iloc[-1]["date"]).date().isoformat(),
                "bars": len(restart),
            }
        )
    restart_matrix = np.column_stack(restart_cagrs)
    restart_summary, restart_long = start_sensitivity_results(
        continuous.metrics["case_id"], restart_years, restart_matrix
    )

    results = continuous.metrics.merge(rolling_summary, on="case_id", validate="one_to_one")
    results = results.merge(restart_summary, on="case_id", validate="one_to_one")
    ident = parameters["identifiability_gate"]
    results["passes_identifiability"] = (
        (results["order_count"] >= int(ident["minimum_order_count"]))
        & (results["buy_sma_count"] >= int(ident["minimum_buy_sma_count"]))
        & (results["sell_sma_count"] >= int(ident["minimum_sell_sma_count"]))
    )
    identifiable = results["passes_identifiability"].to_numpy(bool)
    if not identifiable.any():
        raise RuntimeError("No parameter combination passes the identifiability gate.")
    restart_quantile = float(parameters["restart_sensitivity"]["gate_quantile"])
    restart_threshold = float(
        np.quantile(
            results.loc[results["passes_identifiability"], "restart_10y_cagr_q25_pct"],
            restart_quantile,
        )
    )
    results["passes_restart_gate"] = (
        results["passes_identifiability"]
        & (results["restart_10y_cagr_q25_pct"] > 0.0)
        & (results["restart_10y_cagr_q25_pct"] >= restart_threshold)
    )

    plateau_config = parameters["stable_plateau"]
    plateau = select_stable_plateau(
        results,
        metric="rolling_5y_cagr_q25_pct",
        gate_column="passes_restart_gate",
        top_quantile=float(plateau_config["top_quantile"]),
        a_step=float(parameters["a_pct_range"]["step"]),
        b_step=float(parameters["b_pct_range"]["step"]),
        local_a_radius=float(plateau_config["local_a_radius_pct"]),
        local_b_radius=float(plateau_config["local_b_radius_pct"]),
        minimum_a_span=float(plateau_config["minimum_a_span_pct"]),
        minimum_b_span=float(plateau_config["minimum_b_span_pct"]),
    )

    pbo_summary, pbo_splits = cscv_pbo(
        continuous.pbo_return_sum,
        continuous.pbo_return_sumsq,
        continuous.pbo_counts,
        identifiable,
    )
    effective_trials = effective_trial_count(continuous.pbo_log_returns, identifiable)
    candidates = select_formal_candidates(
        results,
        plateau,
        parameters["reference_anchor"],
    )

    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for _, candidate in candidates.iterrows():
        record, frames, differences = formal_case(
            raw,
            analysis,
            candidate,
            start=start,
            end=end,
            window=window,
            cost_bps=cost_bps,
            initial_cash=initial_cash,
        )
        record.update(
            {
                "screen_case_id": str(candidate["screen_case_id"]),
                "rolling_5y_cagr_q25_pct": float(candidate["rolling_5y_cagr_q25_pct"]),
                "rolling_5y_cagr_median_pct": float(candidate["rolling_5y_cagr_median_pct"]),
                "rolling_5y_cagr_min_pct": float(candidate["rolling_5y_cagr_min_pct"]),
                "rolling_5y_sharpe_q25": float(candidate["rolling_5y_sharpe_q25"]),
                "restart_10y_cagr_q25_pct": float(candidate["restart_10y_cagr_q25_pct"]),
                "restart_10y_cagr_median_pct": float(candidate["restart_10y_cagr_median_pct"]),
                "restart_10y_cagr_min_pct": float(candidate["restart_10y_cagr_min_pct"]),
                "passes_identifiability": bool(candidate["passes_identifiability"]),
                "passes_restart_gate": bool(candidate["passes_restart_gate"]),
                "buy_sma_count": int(candidate["buy_sma_count"]),
                "sell_sma_count": int(candidate["sell_sma_count"]),
                "buy_correction_count": int(candidate["buy_correction_count"]),
                "sell_correction_count": int(candidate["sell_correction_count"]),
            }
        )
        formal_records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal = pd.DataFrame(formal_records)

    final_row = formal[
        formal["selection_reason"].str.contains("FINAL_STABLE_REPRESENTATIVE")
    ]
    if len(final_row) != 1:
        raise AssertionError("Expected exactly one formal final representative.")
    final_row = final_row.iloc[0]
    daily = pd.concat(frame_groups["daily"], ignore_index=True)
    candidate_returns = _daily_returns(daily, str(final_row["case_id"]))
    trial_sharpes = results.loc[results["passes_identifiability"], "sharpe"].to_numpy(float)
    dsr_config = parameters["dsr"]
    dsr = {
        "effective_trials": deflated_sharpe_probability(
            candidate_returns,
            trial_sharpes,
            trial_count=float(effective_trials["effective_trial_count"]),
        ),
        "current_grid_trials": deflated_sharpe_probability(
            candidate_returns,
            trial_sharpes,
            trial_count=float(len(results)),
        ),
        "conservative_historical_upper_bound": deflated_sharpe_probability(
            candidate_returns,
            trial_sharpes,
            trial_count=float(dsr_config["conservative_historical_trial_upper_bound"]),
        ),
    }
    final_gate = {
        "plateau_structural_pass": bool(plateau["largest_component"]["structural_pass"]),
        "pbo_pass": bool(pbo_summary["pbo"] <= float(parameters["pbo"]["maximum_pbo"])),
        "dsr_effective_trials_pass": bool(
            dsr["effective_trials"]["probability"] >= float(dsr_config["minimum_probability"])
        ),
    }
    final_gate["overall_pass"] = bool(all(final_gate.values()))

    benchmark = benchmark_state(analysis, initial_cash=initial_cash, cost_bps=cost_bps)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    elapsed = time.perf_counter() - started
    selection_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "restart_gate_threshold_pct": restart_threshold,
        "identifiable_case_count": int(results["passes_identifiability"].sum()),
        "restart_gate_case_count": int(results["passes_restart_gate"].sum()),
        "plateau": plateau,
        "final_formal_case_id": str(final_row["case_id"]),
        "final_screen_case_id": str(final_row["screen_case_id"]),
        "final_representative": json_safe(final_row.to_dict()),
        "pbo": pbo_summary,
        "effective_trials": effective_trials,
        "dsr": dsr,
        "final_gate": final_gate,
        "benchmark_metrics": benchmark_metrics,
        "restart_windows": restart_windows,
        "search_elapsed_seconds": elapsed,
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "parameter_results.csv": results,
        "rolling_5y_results.csv": rolling_long,
        "restart_10y_results.csv": restart_long,
        "pbo_splits.csv": pbo_splits,
        "formal_candidate_results.csv": formal,
        "buy_hold_daily.csv": benchmark,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frame_groups.items()
        },
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "diagnostic_moments.npz",
        year_start_equity=continuous.year_start_equity,
        year_end_equity=continuous.year_end_equity,
        year_return_sum=continuous.year_return_sum,
        year_return_sumsq=continuous.year_return_sumsq,
        year_counts=continuous.year_counts,
        pbo_return_sum=continuous.pbo_return_sum,
        pbo_return_sumsq=continuous.pbo_return_sumsq,
        pbo_log_returns=continuous.pbo_log_returns,
        pbo_counts=continuous.pbo_counts,
        restart_cagr_matrix=restart_matrix,
    )
    (output_root / "selection_summary.json").write_text(
        json.dumps(json_safe(selection_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    metrics_summary = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "screen_case_count": len(results),
        "formal_candidate_count": len(formal),
        "search_elapsed_seconds": elapsed,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": maximum_differences,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(metrics_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    block_manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "cost_model": "raw threshold/open-gap fill with symmetric adverse fill adjustment",
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "Numba continuous ledger plus independent flat-restart windows",
        "reference_engine": "quantkit.intraday_sma_threshold.run_reference_intraday_threshold",
        "python": platform.python_version(),
        "parameters": parameters,
        "screening_case_count": len(results),
        "formal_candidate_count": len(formal),
        "selection_summary": selection_summary,
        "max_cross_check_differences": maximum_differences,
        "formal_ledger_tolerance": FORMAL_LEDGER_TOLERANCE,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            block_manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    block_manifest_path = output_root / "manifest.json"
    block_manifest_path.write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, symbol, cost_bps, block_manifest_path)
    print(
        f"Completed {len(results):,} full-history cases, {len(restart_years)} flat restarts, "
        f"{len(pbo_splits)} CSCV splits and {len(formal)} formal candidates in {elapsed:.1f}s; "
        f"final a={final_row['a_pct']:.2f}%, b={final_row['b_pct']:.2f}%, "
        f"S5={final_row['rolling_5y_cagr_q25_pct']:.4f}%"
    )


if __name__ == "__main__":
    main()
