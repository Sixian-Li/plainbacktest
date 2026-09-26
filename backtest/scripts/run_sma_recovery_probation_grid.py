#!/usr/bin/env python3
"""Run the frozen QQQ two-SMA recovery-probation period grid."""

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
from quantkit.intraday_sma_period_cross_search import analyze_period_surface
from quantkit.intraday_sma_threshold_selection import (
    cscv_pbo,
    deflated_sharpe_probability,
    effective_trial_count,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.sma_recovery_probation import (
    CONFIRMED_SMA_SELL,
    FORCED_PROBATION_FAILURE,
    FORCED_REBUY,
    ORDINARY_PROBATION_FAILURE,
    ORDINARY_SMA_BUY,
    SmaRecoveryProbationSpec,
    run_pybroker_sma_recovery_probation,
    run_reference_sma_recovery_probation,
)
from quantkit.sma_recovery_probation_search import (
    METRIC_COLUMNS,
    build_recovery_grid_cases,
    screen_recovery_probation_cases,
    screen_recovery_probation_cases_reference,
)
from scripts.run_intraday_sma200_threshold_grid import benchmark_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_period_cross_grid import subperiod_metrics


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015"
)
FORMAL_LEDGER_TOLERANCE = 1e-6
COMPILED_LEDGER_TOLERANCE = 1e-9


def analyze_surface(results: pd.DataFrame, parameters: dict[str, Any]) -> dict[str, Any]:
    config = parameters["surface_selection"]
    return analyze_period_surface(
        results,
        metric="primary_metric",
        top_quantile=float(config["top_quantile"]),
        minimum_component_cells=int(config["minimum_component_cells"]),
        minimum_buy_span=int(config["minimum_buy_window_span"]),
        minimum_sell_span=int(config["minimum_sell_window_span"]),
    )


def representative_row(results: pd.DataFrame, surface: dict[str, Any]) -> pd.Series:
    representative = surface["representative"]
    if representative is None:
        raise AssertionError("The complete period surface has no 3x3 representative.")
    match = results[
        (results["buy_window"] == int(representative["buy_window"]))
        & (results["sell_window"] == int(representative["sell_window"]))
    ]
    if len(match) != 1:
        raise AssertionError("Expected one deterministic surface representative.")
    return match.iloc[0]


def selected_formal_candidates(
    results: pd.DataFrame,
    representative: pd.Series,
) -> pd.DataFrame:
    reasons: dict[str, list[str]] = {}

    def add(row: pd.Series, reason: str) -> None:
        reasons.setdefault(str(row["case_id"]), []).append(reason)

    add(representative, "SURFACE_REPRESENTATIVE")
    for buy_window, sell_window, reason in (
        (200, 200, "SMA200_SMA200_CONTROL"),
        (310, 190, "INHERITED_310_190_ANCHOR"),
    ):
        match = results[
            (results["buy_window"] == buy_window)
            & (results["sell_window"] == sell_window)
        ]
        if len(match) != 1:
            raise AssertionError(f"Missing formal anchor {buy_window}/{sell_window}.")
        add(match.iloc[0], reason)
    identifiable = results[results["identifiable"]]
    if identifiable.empty:
        identifiable = results
    for reason, metric in (
        ("FULL_CAGR_CHAMPION", "cagr_pct"),
        ("FULL_SHARPE_CHAMPION", "sharpe"),
        ("MAX_DRAWDOWN_CHAMPION", "max_drawdown_pct"),
    ):
        add(identifiable.loc[identifiable[metric].astype(float).idxmax()], reason)
    selected = results[results["case_id"].isin(reasons)].copy()
    selected["selection_reason"] = selected["case_id"].map(
        lambda case_id: "|".join(reasons[str(case_id)])
    )
    return selected.sort_values("case_id").reset_index(drop=True)


def _case_metadata(frame: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", str(row["case_id"]))
    result.insert(1, "buy_window", int(row["buy_window"]))
    result.insert(2, "sell_window", int(row["sell_window"]))
    return result


def formal_case(
    raw: pd.DataFrame,
    candidate: pd.Series,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    subperiods: list[dict[str, Any]],
    forced_rebuy_pct: float,
    cost_bps: float,
    initial_cash: float,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float], np.ndarray]:
    spec = SmaRecoveryProbationSpec(
        buy_window=int(candidate["buy_window"]),
        sell_window=int(candidate["sell_window"]),
        forced_rebuy_pct=float(forced_rebuy_pct),
        cost_bps=float(cost_bps),
    )
    pybroker_run = run_pybroker_sma_recovery_probation(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_cash=initial_cash,
    )
    result = pybroker_run.pybroker_result
    engine = raw[(raw["date"] >= pybroker_run.engine_start) & (raw["date"] <= end)].copy()
    actual = pybroker_daily_state(result, engine)
    actual = actual[actual["date"] >= start].reset_index(drop=True)
    reference = run_reference_sma_recovery_probation(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_cash=initial_cash,
    )
    differences = cross_check(result, actual, reference, tolerance=FORMAL_LEDGER_TOLERANCE)
    if not reference.orders.empty and reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{candidate['case_id']} traded more than once in one session.")
    metrics = calculate_metrics(actual, reference.orders, reference.trades, initial_cash=initial_cash)
    period_metrics = subperiod_metrics(actual, subperiods, initial_cash=initial_cash)
    for field in (
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "exposure_pct",
        *period_metrics.keys(),
    ):
        screened = float(candidate[field])
        formal = float(metrics[field] if field in metrics else period_metrics[field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance, equal_nan=True):
            raise AssertionError(
                f"{candidate['case_id']} screening/formal {field} differs: "
                f"{screened} vs {formal}"
            )
    counts = reference.orders["primary_signal"].value_counts().to_dict()
    expected_counts = {
        "ordinary_buy_count": int(counts.get(ORDINARY_SMA_BUY, 0)),
        "confirmed_sell_count": int(counts.get(CONFIRMED_SMA_SELL, 0)),
        "forced_rebuy_count": int(counts.get(FORCED_REBUY, 0)),
        "ordinary_failure_count": int(counts.get(ORDINARY_PROBATION_FAILURE, 0)),
        "forced_failure_count": int(counts.get(FORCED_PROBATION_FAILURE, 0)),
        "probation_confirmation_count": int(
            (reference.daily["state_event"] == "probation_confirmed").sum()
        ),
        "forced_to_ordinary_count": int(
            (reference.daily["state_event"] == "forced_to_ordinary_probation").sum()
        ),
    }
    if int(candidate["order_count"]) != len(reference.orders):
        raise AssertionError(f"{candidate['case_id']} screening/formal order count differs.")
    for field, observed in expected_counts.items():
        if int(candidate[field]) != observed:
            raise AssertionError(
                f"{candidate['case_id']} screening/formal {field} differs: "
                f"{candidate[field]} vs {observed}"
            )
    record = {
        "case_id": str(candidate["case_id"]),
        "selection_reason": str(candidate["selection_reason"]),
        "buy_window": int(candidate["buy_window"]),
        "sell_window": int(candidate["sell_window"]),
        "forced_rebuy_pct": float(forced_rebuy_pct),
        **metrics,
        **period_metrics,
        **expected_counts,
        "primary_signal_counts": json.dumps(counts, ensure_ascii=False, sort_keys=True),
        "fill_source_counts": json.dumps(
            reference.orders["fill_source"].value_counts().to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        **differences,
    }
    frames = {
        "daily": _case_metadata(actual, candidate),
        "reference_daily": _case_metadata(reference.daily, candidate),
        "orders": _case_metadata(reference.orders, candidate),
        "trades": _case_metadata(reference.trades, candidate),
        "signal_plans": _case_metadata(reference.signal_plans, candidate),
    }
    returns = actual["equity"].astype(float).pct_change().fillna(0.0).to_numpy(float)
    return record, frames, differences, returns


def parent_comparison(
    results: pd.DataFrame,
    parent_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    parent_all = pd.read_csv(parent_path)
    parent = parent_all[parent_all["correction_mode"] == "c3_doff"].copy()
    if len(parent) != len(results):
        raise AssertionError(f"Expected {len(results):,} parent c3/d-off rows, found {len(parent):,}.")
    parent_columns = [
        "case_id",
        "buy_window",
        "sell_window",
        "primary_metric",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "exposure_pct",
        "order_count",
    ]
    parent = parent[parent_columns].rename(
        columns={
            "case_id": "parent_case_id",
            **{
                column: f"parent_{column}"
                for column in parent_columns
                if column not in {"case_id", "buy_window", "sell_window"}
            },
        }
    )
    comparison = results.merge(parent, on=["buy_window", "sell_window"], validate="one_to_one")
    for metric in (
        "primary_metric",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "exposure_pct",
        "order_count",
    ):
        comparison[f"delta_{metric}"] = (
            comparison[metric].astype(float) - comparison[f"parent_{metric}"].astype(float)
        )
    anchor = comparison[
        (comparison["buy_window"] == 310) & (comparison["sell_window"] == 190)
    ]
    if len(anchor) != 1:
        raise AssertionError("Parent comparison is missing the inherited 310/190 anchor.")
    return comparison, json_safe(anchor.iloc[0].to_dict())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--parent-results", type=Path, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This frozen experiment requires QQQ only.")
    if float(args.cost_bps) != 5.0 or context.config["cost_scenarios_bps_per_side"] != [5]:
        raise ValueError("This frozen experiment requires exactly 5 bps per side.")
    if not args.parent_results.is_file():
        raise FileNotFoundError(f"Missing validated parent results: {args.parent_results}")
    output_root = reserve_block(context, args.run_id, "QQQ", 5.0)

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ"
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError("QQQ canonical data is not approved.")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    forced_rebuy_pct = float(parameters["forced_rebuy_pct"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)
    if raw.iloc[0]["date"] > pd.Timestamp(parameters["requested_data_start"]):
        raise ValueError("Canonical QQQ does not contain the frozen warmup start.")
    cases = build_recovery_grid_cases(parameters)
    gate = parameters["identifiability_gate"]
    common = {
        "analysis_start": start,
        "analysis_end": end,
        "cost_bps": 5.0,
        "initial_cash": initial_cash,
        "forced_rebuy_pct": forced_rebuy_pct,
        "pbo_block_count": int(parameters["multiple_testing"]["pbo_block_count"]),
        "subperiods": parameters["fixed_subperiods"],
        "minimum_ordinary_buy_count": int(gate["minimum_ordinary_buy_count"]),
        "minimum_confirmed_sell_count": int(gate["minimum_confirmed_sell_count"]),
        "minimum_probation_failure_count": int(gate["minimum_probation_failure_count"]),
    }
    started = time.perf_counter()
    primary = screen_recovery_probation_cases(raw, cases, **common)
    primary_seconds = time.perf_counter() - started
    reference_started = time.perf_counter()
    compiled_reference = screen_recovery_probation_cases_reference(raw, cases, **common)
    reference_seconds = time.perf_counter() - reference_started
    segment_columns = [
        column
        for item in primary.segment_definitions
        for column in (f"{item['period_id']}_cagr_pct", f"{item['period_id']}_sharpe")
    ]
    compared_columns = [*METRIC_COLUMNS, *segment_columns, "primary_metric"]
    compiled_crosscheck = pd.DataFrame({"case_id": primary.metrics["case_id"]})
    maximum_compiled_differences: dict[str, float] = {}
    for column in compared_columns:
        difference = np.abs(
            primary.metrics[column].to_numpy(float)
            - compiled_reference.metrics[column].to_numpy(float)
        )
        finite = difference[np.isfinite(difference)]
        maximum = float(finite.max()) if len(finite) else 0.0
        maximum_compiled_differences[column] = maximum
        compiled_crosscheck[f"abs_{column}_difference"] = difference
        if maximum > COMPILED_LEDGER_TOLERANCE:
            raise AssertionError(f"Compiled ledgers differ on {column}: {maximum}")
    if not np.array_equal(
        primary.metrics["identifiable"].to_numpy(bool),
        compiled_reference.metrics["identifiable"].to_numpy(bool),
    ):
        raise AssertionError("Compiled ledgers disagree on identifiability.")

    results = primary.metrics
    surface = analyze_surface(results, parameters)
    representative = representative_row(results, surface)
    formal_candidates = selected_formal_candidates(results, representative)
    eligible = results["identifiable"].to_numpy(bool)
    pbo_summary, pbo_splits = cscv_pbo(
        primary.pbo_return_sum,
        primary.pbo_return_sumsq,
        primary.pbo_counts,
        eligible,
    )
    effective_trials = effective_trial_count(primary.pbo_log_returns, eligible)
    comparison, anchor_comparison = parent_comparison(results, args.parent_results)

    formal_records: list[dict[str, Any]] = []
    frames_by_name: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "orders", "trades", "signal_plans")
    }
    formal_returns: dict[str, np.ndarray] = {}
    maximum_formal_differences: dict[str, float] = {}
    for _, candidate in formal_candidates.iterrows():
        record, frames, differences, returns = formal_case(
            raw,
            candidate,
            start=start,
            end=end,
            subperiods=parameters["fixed_subperiods"],
            forced_rebuy_pct=forced_rebuy_pct,
            cost_bps=5.0,
            initial_cash=initial_cash,
        )
        formal_records.append(record)
        formal_returns[str(candidate["case_id"])] = returns
        for name, frame in frames.items():
            frames_by_name[name].append(frame)
        for name, value in differences.items():
            maximum_formal_differences[name] = max(
                maximum_formal_differences.get(name, 0.0), float(value)
            )
    formal = pd.DataFrame(formal_records)

    rep_id = str(representative["case_id"])
    trial_sharpes = results.loc[eligible, "sharpe"].to_numpy(float)
    dsr_records: list[dict[str, Any]] = []
    for policy, trial_count in (
        ("effective_correlated_trials", float(effective_trials["effective_trial_count"])),
        ("all_1444_cases", float(len(results))),
    ):
        dsr_records.append(
            {
                "case_id": rep_id,
                "trial_policy": policy,
                **deflated_sharpe_probability(
                    formal_returns[rep_id], trial_sharpes, trial_count=trial_count
                ),
            }
        )
    dsr = pd.DataFrame(dsr_records)
    effective_dsr = float(
        dsr.loc[dsr["trial_policy"] == "effective_correlated_trials", "probability"].iloc[0]
    )
    multiple_testing = parameters["multiple_testing"]
    representative_gate = {
        **json_safe(representative.to_dict()),
        "component_cells": int(surface["largest_component"]["cell_count"]),
        "buy_span": int(surface["largest_component"]["buy_span"]),
        "sell_span": int(surface["largest_component"]["sell_span"]),
        "touches_search_boundary": bool(surface["largest_component"]["touches_search_boundary"]),
        "structural_pass": bool(surface["largest_component"]["structural_pass"]),
        "positive_primary_metric": bool(float(representative["primary_metric"]) > 0.0),
        "pbo_pass": bool(float(pbo_summary["pbo"]) <= float(multiple_testing["pbo_maximum"])),
        "dsr_effective_probability": effective_dsr,
        "dsr_effective_pass": bool(
            effective_dsr >= float(multiple_testing["dsr_minimum_probability"])
        ),
    }
    representative_gate["promotion_gate_pass"] = bool(
        representative_gate["structural_pass"]
        and representative_gate["positive_primary_metric"]
        and bool(representative["identifiable"])
        and representative_gate["pbo_pass"]
        and representative_gate["dsr_effective_pass"]
    )

    benchmark = benchmark_state(primary.analysis, initial_cash=initial_cash, cost_bps=5.0)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    subperiod_rows: list[dict[str, Any]] = []
    for item in primary.segment_definitions:
        for row in results.itertuples(index=False):
            subperiod_rows.append(
                {
                    "case_id": row.case_id,
                    "period_id": item["period_id"],
                    "start": item["actual_start"],
                    "end": item["actual_end"],
                    "bars": item["bar_count"],
                    "cagr_pct": getattr(row, f"{item['period_id']}_cagr_pct"),
                    "sharpe": getattr(row, f"{item['period_id']}_sharpe"),
                }
            )
    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "case_count": len(results),
        "analysis_bars": len(primary.analysis),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "identifiable_case_count": int(eligible.sum()),
        "formal_candidate_count": len(formal),
        "benchmark_metrics": benchmark_metrics,
        "surface_analysis": surface,
        "stable_representative": representative_gate,
        "promotion_eligible": bool(representative_gate["promotion_gate_pass"]),
        "inherited_310_190_comparison": anchor_comparison,
        "pbo": pbo_summary,
        "effective_trials": effective_trials,
        "dsr": json_safe(dsr.to_dict("records")),
        "maximum_compiled_ledger_differences": maximum_compiled_differences,
        "maximum_formal_ledger_differences": maximum_formal_differences,
        "parent_results_sha256": sha256(args.parent_results),
        "primary_screen_seconds": primary_seconds,
        "reference_screen_seconds": reference_seconds,
        "total_elapsed_seconds": elapsed,
    }

    csv_outputs = {
        "parameter_results.csv": results,
        "compiled_crosscheck.csv": compiled_crosscheck,
        "subperiod_results.csv": pd.DataFrame(subperiod_rows),
        "surface_representative.csv": pd.DataFrame([representative_gate]),
        "formal_candidate_results.csv": formal,
        "parent_control_comparison.csv": comparison,
        "pbo_splits.csv": pbo_splits,
        "dsr_results.csv": dsr,
        "buy_hold_daily.csv": benchmark,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frames_by_name.items()
        },
    }
    for name, frame in csv_outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "full_grid_pbo_inputs.npz",
        return_sum=primary.pbo_return_sum,
        return_sumsq=primary.pbo_return_sumsq,
        log_returns=primary.pbo_log_returns,
        block_counts=primary.pbo_counts,
    )
    for name, payload in (
        ("surface_analysis.json", surface),
        ("selection_summary.json", summary),
    ):
        (output_root / name).write_text(
            json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    (output_root / "metrics.json").write_text(
        json.dumps(
            json_safe(
                {
                    "symbol": "QQQ",
                    "cost_bps": 5.0,
                    "analysis_start": start.date().isoformat(),
                    "analysis_end": end.date().isoformat(),
                    "analysis_bars": len(primary.analysis),
                    "case_count": len(results),
                    "formal_candidate_count": len(formal),
                    "benchmark_metrics": benchmark_metrics,
                    "stable_representative": representative_gate,
                    "inherited_310_190_comparison": anchor_comparison,
                    "max_cross_check_differences": {
                        **maximum_compiled_differences,
                        **maximum_formal_differences,
                    },
                }
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 5.0,
        "cost_model": "close-confirmed next-open recovery/failure orders plus predeclared short-SMA and c intraday lines, with symmetric 5 bps adverse adjustment",
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "Numba recovery-probation full-grid ledger",
        "reference_engine": "independently structured Numba full-grid ledger plus Python/PyBroker formal reconciliation",
        "python": platform.python_version(),
        "parameters": parameters,
        "case_count": len(results),
        "formal_candidate_count": len(formal),
        "selection_summary": summary,
        "compiled_ledger_tolerance": COMPILED_LEDGER_TOLERANCE,
        "formal_ledger_tolerance": FORMAL_LEDGER_TOLERANCE,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
        "parent_results_source": str(args.parent_results.resolve()),
        "parent_results_sha256": sha256(args.parent_results),
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
    record_block_complete(context, args.run_id, "QQQ", 5.0, manifest_path)
    print(
        f"Completed {len(results):,} QQQ recovery-probation cases, "
        f"{len(formal):,} formal reconciliations and {len(primary.analysis):,} bars "
        f"in {elapsed:.2f}s."
    )


if __name__ == "__main__":
    main()
