#!/usr/bin/env python3
"""Run the frozen 2016+ QQQ SMA-period anchor and its diagnostic neighborhood."""

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
from quantkit.intraday_sma_period_cross_search import (
    METRIC_COLUMNS,
    build_grid_cases,
    screen_period_cross_cases,
    screen_period_cross_cases_reference,
)
from quantkit.metrics import calculate_metrics
from scripts.run_intraday_sma200_threshold_grid import benchmark_state
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame
from scripts.run_intraday_sma_period_cross_grid import formal_case, subperiod_metrics


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026"
)
FORMAL_LEDGER_TOLERANCE = 1e-6
COMPILED_LEDGER_TOLERANCE = 1e-9


def assign_case_roles(cases: pd.DataFrame, parameters: dict[str, Any]) -> pd.DataFrame:
    """Label predeclared diagnostics without selecting any case from holdout results."""

    anchor = parameters["locked_anchor"]
    buy_anchor = int(anchor["buy_window"])
    sell_anchor = int(anchor["sell_window"])
    result = cases.copy()
    roles: list[str] = []
    for row in result.itertuples(index=False):
        labels = ["FULL_PREDECLARED_PERTURBATION"]
        if abs(int(row.buy_window) - buy_anchor) <= 10 and abs(
            int(row.sell_window) - sell_anchor
        ) <= 10:
            labels.append("LOCAL_3X3")
        if int(row.sell_window) == sell_anchor:
            labels.append("BUY_WINDOW_OAT")
        if int(row.buy_window) == buy_anchor:
            labels.append("SELL_WINDOW_OAT")
        if int(row.buy_window) == buy_anchor and int(row.sell_window) == sell_anchor:
            labels.insert(0, "LOCKED_ANCHOR")
        roles.append("|".join(labels))
    result["selection_reason"] = roles
    if int(result["selection_reason"].str.contains("LOCKED_ANCHOR").sum()) != 1:
        raise AssertionError("The frozen grid must contain exactly one locked anchor.")
    return result


def metric_distribution(frame: pd.DataFrame) -> dict[str, Any]:
    output: dict[str, Any] = {"case_count": int(len(frame))}
    for field in ("cagr_pct", "sharpe", "max_drawdown_pct", "order_count", "exposure_pct"):
        values = frame[field].astype(float)
        output[field] = {
            "minimum": float(values.min()),
            "q25": float(values.quantile(0.25)),
            "median": float(values.median()),
            "q75": float(values.quantile(0.75)),
            "maximum": float(values.max()),
        }
    return output


def evaluate_predeclared_gates(
    results: pd.DataFrame,
    benchmark_metrics: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate frozen gates; never choose a replacement for the locked anchor."""

    anchor_spec = parameters["locked_anchor"]
    anchor_rows = results[
        (results["buy_window"] == int(anchor_spec["buy_window"]))
        & (results["sell_window"] == int(anchor_spec["sell_window"]))
        & np.isclose(results["forced_rebuy_pct"], float(anchor_spec["forced_rebuy_pct"]))
        & results["stop_loss_pct"].isna()
    ]
    if len(anchor_rows) != 1:
        raise AssertionError(f"Expected exactly one locked anchor, found {len(anchor_rows)}.")
    anchor = anchor_rows.iloc[0]
    buy_anchor = int(anchor["buy_window"])
    sell_anchor = int(anchor["sell_window"])
    local = results[
        results["buy_window"].isin([buy_anchor - 10, buy_anchor, buy_anchor + 10])
        & results["sell_window"].isin([sell_anchor - 10, sell_anchor, sell_anchor + 10])
    ].copy()
    if len(local) != 9:
        raise AssertionError(f"Expected complete local 3x3, found {len(local)} cases.")

    gates = parameters["predeclared_diagnostic_gates"]
    effectiveness_spec = gates["anchor_effectiveness"]
    benchmark_cagr = float(benchmark_metrics["cagr_pct"])
    cagr_retention = (
        float(anchor["cagr_pct"]) / benchmark_cagr if benchmark_cagr > 0.0 else float("nan")
    )
    sharpe_advantage = float(anchor["sharpe"]) - float(benchmark_metrics["sharpe"])
    drawdown_improvement = float(anchor["max_drawdown_pct"]) - float(
        benchmark_metrics["max_drawdown_pct"]
    )
    effectiveness_checks = {
        "positive_anchor_cagr": bool(float(anchor["cagr_pct"]) > 0.0),
        "buy_hold_cagr_retention": cagr_retention,
        "buy_hold_cagr_retention_pass": bool(
            np.isfinite(cagr_retention)
            and cagr_retention
            >= float(effectiveness_spec["minimum_buy_hold_cagr_retention"])
        ),
        "sharpe_advantage": sharpe_advantage,
        "sharpe_advantage_pass": bool(
            sharpe_advantage >= float(effectiveness_spec["minimum_sharpe_advantage"])
        ),
        "max_drawdown_improvement_percentage_points": drawdown_improvement,
        "max_drawdown_improvement_pass": bool(
            drawdown_improvement
            >= float(effectiveness_spec["minimum_max_drawdown_improvement_percentage_points"])
        ),
    }
    effectiveness_checks["pass"] = bool(
        effectiveness_checks["positive_anchor_cagr"]
        and effectiveness_checks["buy_hold_cagr_retention_pass"]
        and effectiveness_checks["sharpe_advantage_pass"]
        and effectiveness_checks["max_drawdown_improvement_pass"]
    )

    local_spec = gates["local_3x3_stability"]
    anchor_cagr = float(anchor["cagr_pct"])
    local_median_fraction = (
        float(local["cagr_pct"].median()) / anchor_cagr
        if anchor_cagr > 0.0
        else float("nan")
    )
    worst_sharpe_delta = float(local["sharpe"].min()) - float(anchor["sharpe"])
    worst_drawdown_deterioration = float(anchor["max_drawdown_pct"]) - float(
        local["max_drawdown_pct"].min()
    )
    local_checks = {
        "all_identifiable": bool(local["identifiable"].all()),
        "all_positive_cagr": bool((local["cagr_pct"] > 0.0).all()),
        "median_cagr_fraction_of_anchor": local_median_fraction,
        "median_cagr_fraction_pass": bool(
            np.isfinite(local_median_fraction)
            and local_median_fraction
            >= float(local_spec["minimum_median_cagr_fraction_of_anchor"])
        ),
        "worst_sharpe_relative_to_anchor": worst_sharpe_delta,
        "worst_sharpe_pass": bool(
            worst_sharpe_delta
            >= float(local_spec["minimum_worst_sharpe_relative_to_anchor"])
        ),
        "worst_drawdown_deterioration_percentage_points": worst_drawdown_deterioration,
        "worst_drawdown_pass": bool(
            worst_drawdown_deterioration
            <= float(local_spec["maximum_worst_drawdown_deterioration_percentage_points"])
        ),
    }
    local_checks["pass"] = bool(
        local_checks["all_identifiable"]
        and local_checks["all_positive_cagr"]
        and local_checks["median_cagr_fraction_pass"]
        and local_checks["worst_sharpe_pass"]
        and local_checks["worst_drawdown_pass"]
    )

    return {
        "anchor_case_id": str(anchor["case_id"]),
        "anchor_effectiveness": effectiveness_checks,
        "local_3x3_stability": local_checks,
        "diagnostic_support_pass": bool(
            effectiveness_checks["pass"] and local_checks["pass"]
        ),
        "promotion_allowed": False,
        "promotion_blocker": (
            "TIM-v0.50 failed its predeclared PBO and DSR gates; this holdout "
            "neighborhood may support or weaken the anchor but cannot promote it."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This frozen experiment requires QQQ only.")
    if float(args.cost_bps) != 5.0 or context.config["cost_scenarios_bps_per_side"] != [5]:
        raise ValueError("This frozen experiment requires exactly 5 bps per side.")
    output_root = reserve_block(context, args.run_id, "QQQ", 5.0)

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    source_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ"
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError("QQQ canonical data is not approved.")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)
    if raw.iloc[0]["date"] > pd.Timestamp(parameters["requested_data_start"]):
        raise ValueError("Canonical QQQ does not contain the frozen warmup start.")
    if raw.iloc[-1]["date"] < end:
        raise ValueError("Canonical QQQ ends before the frozen analysis end.")

    cases = assign_case_roles(build_grid_cases(parameters), parameters)
    gate = parameters["identifiability_gate"]
    started = time.perf_counter()
    primary = screen_period_cross_cases(
        raw,
        cases,
        analysis_start=start,
        analysis_end=end,
        cost_bps=5.0,
        initial_cash=initial_cash,
        pbo_block_count=12,
        subperiods=parameters["fixed_subperiods"],
        minimum_ordinary_buy_count=int(gate["minimum_ordinary_buy_count"]),
        minimum_ordinary_sell_count=int(gate["minimum_ordinary_sell_count"]),
    )
    primary_seconds = time.perf_counter() - started
    reference_started = time.perf_counter()
    compiled_reference = screen_period_cross_cases_reference(
        raw,
        cases,
        analysis_start=start,
        analysis_end=end,
        cost_bps=5.0,
        initial_cash=initial_cash,
        pbo_block_count=12,
        subperiods=parameters["fixed_subperiods"],
        minimum_ordinary_buy_count=int(gate["minimum_ordinary_buy_count"]),
        minimum_ordinary_sell_count=int(gate["minimum_ordinary_sell_count"]),
    )
    reference_seconds = time.perf_counter() - reference_started

    segment_columns = [
        column
        for item in primary.segment_definitions
        for column in (f"{item['period_id']}_cagr_pct", f"{item['period_id']}_sharpe")
    ]
    compared_columns = [*METRIC_COLUMNS, *segment_columns, "primary_metric"]
    compiled_differences = pd.DataFrame({"case_id": primary.metrics["case_id"]})
    maximum_compiled_differences: dict[str, float] = {}
    for column in compared_columns:
        difference = np.abs(
            primary.metrics[column].to_numpy(float)
            - compiled_reference.metrics[column].to_numpy(float)
        )
        finite = difference[np.isfinite(difference)]
        maximum = float(finite.max()) if len(finite) else 0.0
        maximum_compiled_differences[column] = maximum
        compiled_differences[f"abs_{column}_difference"] = difference
        if maximum > COMPILED_LEDGER_TOLERANCE:
            raise AssertionError(f"Compiled ledgers differ on {column}: {maximum}")
    if not np.array_equal(
        primary.metrics["identifiable"].to_numpy(bool),
        compiled_reference.metrics["identifiable"].to_numpy(bool),
    ):
        raise AssertionError("Compiled ledgers disagree on identifiability.")

    results = primary.metrics.copy()
    formal_records: list[dict[str, Any]] = []
    frames_by_name: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "orders", "trades", "signal_plans")
    }
    maximum_formal_differences: dict[str, float] = {}
    formal_started = time.perf_counter()
    for _, candidate in results.iterrows():
        record, frames, differences, _returns = formal_case(
            raw,
            candidate,
            start=start,
            end=end,
            subperiods=parameters["fixed_subperiods"],
            cost_bps=5.0,
            initial_cash=initial_cash,
        )
        formal_records.append(record)
        for name, frame in frames.items():
            frames_by_name[name].append(frame)
        for name, value in differences.items():
            maximum_formal_differences[name] = max(
                maximum_formal_differences.get(name, 0.0), float(value)
            )
    formal_seconds = time.perf_counter() - formal_started
    formal = pd.DataFrame(formal_records)
    if len(formal) != 81:
        raise AssertionError(f"Expected 81 formal reconciliations, found {len(formal)}.")

    orders = pd.concat(frames_by_name["orders"], ignore_index=True)
    if len(orders):
        if pd.to_datetime(orders["date"]).min() < start:
            raise AssertionError("A formal ledger traded before the locked test window.")
        daily_order_max = int(orders.groupby(["case_id", "date"]).size().max())
        if daily_order_max > 1:
            raise AssertionError("A formal case traded more than once in one session.")

    benchmark = benchmark_state(primary.analysis, initial_cash=initial_cash, cost_bps=5.0)
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_cash,
    )
    benchmark_period_metrics = subperiod_metrics(
        benchmark,
        parameters["fixed_subperiods"],
        initial_cash=initial_cash,
    )
    diagnostic_gates = evaluate_predeclared_gates(results, benchmark_metrics, parameters)
    anchor_case_id = diagnostic_gates["anchor_case_id"]
    anchor = formal[formal["case_id"] == anchor_case_id]
    if len(anchor) != 1 or "LOCKED_ANCHOR" not in str(anchor.iloc[0]["selection_reason"]):
        raise AssertionError("Formal locked anchor is missing or was relabeled.")
    anchor_record = json_safe(anchor.iloc[0].to_dict())

    buy_anchor = int(parameters["locked_anchor"]["buy_window"])
    sell_anchor = int(parameters["locked_anchor"]["sell_window"])
    buy_oat = results[results["sell_window"] == sell_anchor].copy()
    sell_oat = results[results["buy_window"] == buy_anchor].copy()
    local = results[
        results["buy_window"].isin([buy_anchor - 10, buy_anchor, buy_anchor + 10])
        & results["sell_window"].isin([sell_anchor - 10, sell_anchor, sell_anchor + 10])
    ].copy()
    distance_summary = results.assign(
        chebyshev_steps=np.maximum(
            np.abs(results["buy_window"] - buy_anchor),
            np.abs(results["sell_window"] - sell_anchor),
        )
        // 10
    ).groupby("chebyshev_steps", as_index=False).agg(
        case_count=("case_id", "size"),
        median_cagr_pct=("cagr_pct", "median"),
        minimum_cagr_pct=("cagr_pct", "min"),
        median_sharpe=("sharpe", "median"),
        minimum_sharpe=("sharpe", "min"),
        median_max_drawdown_pct=("max_drawdown_pct", "median"),
        worst_max_drawdown_pct=("max_drawdown_pct", "min"),
    )

    subperiod_long: list[dict[str, Any]] = []
    for item in primary.segment_definitions:
        for row in results.itertuples(index=False):
            subperiod_long.append(
                {
                    "case_id": row.case_id,
                    "buy_window": int(row.buy_window),
                    "sell_window": int(row.sell_window),
                    "period_id": item["period_id"],
                    "start": item["actual_start"],
                    "end": item["actual_end"],
                    "bars": item["bar_count"],
                    "cagr_pct": getattr(row, f"{item['period_id']}_cagr_pct"),
                    "sharpe": getattr(row, f"{item['period_id']}_sharpe"),
                }
            )
    subperiod_frame = pd.DataFrame(subperiod_long)

    anchor_ranks = {}
    for field in ("cagr_pct", "sharpe", "max_drawdown_pct"):
        ranks = results[field].rank(method="min", ascending=False)
        anchor_index = results.index[results["case_id"] == anchor_case_id][0]
        anchor_ranks[field] = {
            "rank": int(ranks.loc[anchor_index]),
            "case_count": int(len(results)),
            "percentile": float((len(results) - ranks.loc[anchor_index]) / (len(results) - 1)),
        }

    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(primary.analysis),
        "case_count": len(results),
        "formal_candidate_count": len(formal),
        "identifiable_case_count": int(results["identifiable"].sum()),
        "locked_anchor": anchor_record,
        "locked_anchor_ranks_within_diagnostic_surface": anchor_ranks,
        "benchmark_metrics": benchmark_metrics,
        "benchmark_subperiod_metrics": benchmark_period_metrics,
        "predeclared_gates": diagnostic_gates,
        "local_3x3_distribution": metric_distribution(local),
        "full_neighborhood_distribution": metric_distribution(results),
        "buy_oat_distribution": metric_distribution(buy_oat),
        "sell_oat_distribution": metric_distribution(sell_oat),
        "winner_selection_performed": False,
        "maximum_compiled_ledger_differences": maximum_compiled_differences,
        "maximum_formal_ledger_differences": maximum_formal_differences,
        "primary_screen_seconds": primary_seconds,
        "reference_screen_seconds": reference_seconds,
        "formal_reconciliation_seconds": formal_seconds,
        "total_elapsed_seconds": elapsed,
    }

    csv_outputs = {
        "parameter_results.csv": results,
        "compiled_crosscheck.csv": compiled_differences,
        "formal_candidate_results.csv": formal,
        "subperiod_results.csv": subperiod_frame,
        "buy_oat.csv": buy_oat,
        "sell_oat.csv": sell_oat,
        "local_3x3.csv": local,
        "distance_summary.csv": distance_summary,
        "buy_hold_daily.csv": benchmark,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frames_by_name.items()
        },
    }
    for name, frame in csv_outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "selection_summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
                    "locked_anchor": anchor_record,
                    "benchmark_metrics": benchmark_metrics,
                    "predeclared_gates": diagnostic_gates,
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

    all_differences = {**maximum_compiled_differences, **maximum_formal_differences}
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 5.0,
        "cost_model": "predeclared dynamic SMA cross/open-gap fill with symmetric 5 bps adverse adjustment",
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "Numba SMA-period ledger",
        "reference_engine": "independently structured Numba ledger plus Python/PyBroker reconciliation for all 81 cases",
        "python": platform.python_version(),
        "parameters": parameters,
        "case_count": len(results),
        "formal_candidate_count": len(formal),
        "selection_summary": summary,
        "winner_selection_performed": False,
        "compiled_ledger_tolerance": COMPILED_LEDGER_TOLERANCE,
        "formal_ledger_tolerance": FORMAL_LEDGER_TOLERANCE,
        "max_cross_check_differences": all_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
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
        f"Completed {len(results)} locked-neighborhood cases, {len(formal)} formal "
        f"reconciliations and {len(primary.analysis):,} bars in {elapsed:.2f}s."
    )


if __name__ == "__main__":
    main()
