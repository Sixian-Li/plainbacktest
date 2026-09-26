#!/usr/bin/env python3
"""Run a configured QQQ one-at-a-time parameter sensitivity experiment."""

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
from quantkit.intraday_sma import (
    BUY_FORCED_REENTRY,
    SELL_FAST_DROP,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.intraday_sma_search import (
    FAST_DROP_ENABLED_COLUMN,
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
    screen_cases,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.oat_sensitivity import (
    baseline_plateau_diagnostics,
    build_oat_cases,
    cross_window_plateau_intersections,
    metric_winner,
    select_formal_oat_cases,
    select_plateau_endpoint_cases,
)
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_global_search import spec_from_row
from scripts.run_intraday_sma_reentry_grid import benchmark_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30b.1__26-08-13__qqq_intraday_sma_oat_sensitivity_two_periods"
FORMAL_LEDGER_TOLERANCE = 1e-6


def disabled_signals(row: pd.Series | dict[str, Any]) -> tuple[str, ...]:
    value = row[FORCED_REENTRY_COLUMN]
    enabled = value if isinstance(value, (bool, np.bool_)) else str(value).lower() == "true"
    return () if enabled else (BUY_FORCED_REENTRY,)


def add_identity(
    frame: pd.DataFrame,
    *,
    formal_case_id: str,
    screen_case_id: str,
    window_id: str,
) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "window_id", window_id)
    result.insert(0, "screen_case_id", screen_case_id)
    result.insert(0, "formal_case_id", formal_case_id)
    return result


def formal_case(
    raw: pd.DataFrame,
    candidate: pd.Series,
    *,
    window_id: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float]]:
    screen_case_id = str(candidate["case_id"])
    formal_case_id = f"{screen_case_id}__{window_id}"
    spec = spec_from_row(candidate)
    disabled = disabled_signals(candidate)
    pybroker_run = run_pybroker_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
        disabled_signals=disabled,
    )
    result = pybroker_run.pybroker_result
    engine = raw[(raw["date"] >= pybroker_run.engine_start) & (raw["date"] <= end)].copy()
    actual = pybroker_daily_state(result, engine)
    actual = actual[actual["date"] >= start].reset_index(drop=True)
    reference = run_reference_intraday_sma(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_position="flat",
        initial_cash=initial_cash,
        disabled_signals=disabled,
    )
    differences = cross_check(result, actual, reference, tolerance=FORMAL_LEDGER_TOLERANCE)
    if reference.orders.empty:
        raise AssertionError(f"{formal_case_id} never received an ordinary buy signal.")
    first = reference.orders.iloc[0]
    if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
        raise AssertionError(f"{formal_case_id} did not begin with an ordinary buy.")
    if reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{formal_case_id} traded more than once on one day.")
    if BUY_FORCED_REENTRY in disabled and BUY_FORCED_REENTRY in set(reference.orders["primary_signal"]):
        raise AssertionError(f"{formal_case_id} emitted a disabled forced re-entry.")
    if not spec.fast_drop_enabled and SELL_FAST_DROP in set(reference.orders["primary_signal"]):
        raise AssertionError(f"{formal_case_id} emitted the disabled C/D fast-drop sell.")

    metrics = calculate_metrics(actual, reference.orders, reference.trades, initial_cash=initial_cash)
    analysis = raw[(raw["date"] >= start) & (raw["date"] <= end)].reset_index(drop=True)
    benchmark = benchmark_state(
        analysis,
        first_entry_date=start,
        first_entry_fill=float(analysis.iloc[0]["open"]),
        initial_cash=initial_cash,
    )
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    for field in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
        screened = float(candidate[field])
        formal = float(metrics[field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance):
            raise AssertionError(
                f"{formal_case_id} screening/formal {field} differs: {screened} vs {formal}"
            )
    for screened_field, formal_field in (
        ("benchmark_final_equity", "final_equity"),
        ("benchmark_cagr_pct", "cagr_pct"),
        ("benchmark_sharpe", "sharpe"),
        ("benchmark_max_drawdown_pct", "max_drawdown_pct"),
    ):
        screened = float(candidate[screened_field])
        formal = float(benchmark_metrics[formal_field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance):
            raise AssertionError(
                f"{formal_case_id} screening/formal {screened_field} differs: "
                f"{screened} vs {formal}"
            )
    if int(candidate["order_count"]) != int(metrics["order_count"]):
        raise AssertionError(f"{formal_case_id} screening/formal order count differs.")
    first_date = pd.Timestamp(first["date"]).normalize()
    if pd.Timestamp(candidate["first_entry_date"]).normalize() != first_date:
        raise AssertionError(f"{formal_case_id} screening/formal first entry differs.")

    record = {
        "formal_case_id": formal_case_id,
        "case_id": screen_case_id,
        "window_id": window_id,
        "selection_reason": str(candidate["selection_reason"]),
        **{name: float(candidate[name]) for name in PARAMETER_COLUMNS},
        FORCED_REENTRY_COLUMN: bool(candidate[FORCED_REENTRY_COLUMN]),
        FAST_DROP_ENABLED_COLUMN: bool(candidate.get(FAST_DROP_ENABLED_COLUMN, True)),
        **metrics,
        "first_entry_date": first_date,
        "first_entry_fill": float(first["fill_price"]),
        "first_entry_signal": str(first["primary_signal"]),
        "benchmark_final_equity": benchmark_metrics["final_equity"],
        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
        "benchmark_sharpe": benchmark_metrics["sharpe"],
        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
        "delta_cagr_vs_buy_hold_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
        "delta_sharpe_vs_buy_hold": metrics["sharpe"] - benchmark_metrics["sharpe"],
        "primary_signal_counts": json.dumps(
            reference.orders["primary_signal"].value_counts().to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        **differences,
    }
    frames = {
        "daily": add_identity(actual, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
        "reference_daily": add_identity(reference.daily, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
        "buy_hold_daily": add_identity(benchmark, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
        "orders": add_identity(reference.orders, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
        "trades": add_identity(reference.trades, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
        "signal_plans": add_identity(reference.signal_plans, formal_case_id=formal_case_id, screen_case_id=screen_case_id, window_id=window_id),
    }
    return record, frames, differences


def sensitivity_summary(sensitivity: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (window_id, sweep_id), group in sensitivity.groupby(["window_id", "sweep_id"], sort=False):
        baseline = group[group["is_baseline"]]
        if len(baseline) != 1:
            raise AssertionError(f"{window_id}/{sweep_id} does not have one baseline point.")
        for metric in ("cagr_pct", "sharpe"):
            winner = metric_winner(group, metric)
            maximum = float(group[metric].max())
            tolerance = 1e-12 * max(1.0, abs(maximum))
            tied = group[np.isclose(group[metric].astype(float), maximum, rtol=0, atol=tolerance)]
            metric_range = float(group[metric].max() - group[metric].min())
            boundaries = {1, int(group["point_order"].max())}
            records.append(
                {
                    "window_id": window_id,
                    "sweep_id": sweep_id,
                    "parameter": group.iloc[0]["parameter"],
                    "parameter_label": group.iloc[0]["parameter_label"],
                    "metric": metric,
                    "baseline_value": baseline.iloc[0]["value"],
                    "baseline_metric": float(baseline.iloc[0][metric]),
                    "best_value": winner["value"],
                    "best_metric": float(winner[metric]),
                    "minimum_metric": float(group[metric].min()),
                    "metric_range": metric_range,
                    "best_tie_count": len(tied),
                    "curve_is_flat": metric_range <= tolerance,
                    "best_at_boundary": int(winner["point_order"]) in boundaries,
                    "maximum_plateau_touches_boundary": bool(
                        tied["point_order"].astype(int).isin(boundaries).any()
                    ),
                }
            )
    return pd.DataFrame(records)


def cross_period_correlations(sensitivity: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for sweep_id, sweep in sensitivity.groupby("sweep_id", sort=False):
        windows = list(dict.fromkeys(sweep["window_id"]))
        if len(windows) != 2:
            raise AssertionError(f"{sweep_id} does not have two windows.")
        left = sweep[sweep["window_id"] == windows[0]].sort_values("point_order")
        right = sweep[sweep["window_id"] == windows[1]].sort_values("point_order")
        for metric in ("cagr_pct", "sharpe"):
            left_values = left[metric].reset_index(drop=True)
            right_values = right[metric].reset_index(drop=True)
            if left_values.nunique() < 2 or right_values.nunique() < 2:
                pearson = float("nan")
                spearman = float("nan")
            else:
                pearson = left_values.corr(right_values)
                spearman = left_values.rank().corr(right_values.rank())
            records.append(
                {
                    "sweep_id": sweep_id,
                    "parameter": sweep.iloc[0]["parameter"],
                    "parameter_label": sweep.iloc[0]["parameter_label"],
                    "metric": metric,
                    "pearson_correlation": pearson,
                    "spearman_rank_correlation": spearman,
                }
            )
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or args.symbol not in context.config["symbols"] or float(args.cost_bps) != 0:
        raise ValueError("This frozen experiment requires QQQ at 0 bps.")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError(f"QQQ is not approved: {dataset_manifest['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)

    parameters = context.config["parameters"]
    initial_cash = float(context.config["initial_cash"])
    cases, points = build_oat_cases(parameters["baseline"], parameters["sweeps"])
    if len(cases) != int(parameters["unique_case_count"]):
        raise AssertionError(
            f"Expected {parameters['unique_case_count']} unique cases, observed {len(cases)}."
        )
    if len(points) != int(parameters["sweep_point_count"]):
        raise AssertionError(
            f"Expected {parameters['sweep_point_count']} sweep points, observed {len(points)}."
        )

    started = time.perf_counter()
    screening_frames: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    for window in parameters["windows"]:
        screen = screen_cases(
            raw,
            cases,
            initial_cash=initial_cash,
            analysis_start=window["analysis_start"],
            analysis_end=window["analysis_end"],
            evaluation_start="analysis_start",
        )
        screen.insert(0, "window_id", window["window_id"])
        screening_frames.append(screen)
        selected = select_formal_oat_cases(screen, points)
        selected.insert(0, "window_id", window["window_id"])
        selection_frames.append(selected)
    screening = pd.concat(screening_frames, ignore_index=True)
    if len(screening) != len(cases) * len(parameters["windows"]):
        raise AssertionError("Unexpected case/window screening count.")
    sensitivity = pd.concat(
        [
            points.merge(
                screening[screening["window_id"] == window["window_id"]],
                on="case_id",
                how="left",
                validate="many_to_one",
            ).assign(window_id=window["window_id"])
            for window in parameters["windows"]
        ],
        ignore_index=True,
    )
    sweep_summary = sensitivity_summary(sensitivity)
    correlations = (
        cross_period_correlations(sensitivity)
        if len(parameters["windows"]) == 2
        else pd.DataFrame(
            columns=[
                "sweep_id",
                "parameter",
                "parameter_label",
                "metric",
                "pearson_correlation",
                "spearman_rank_correlation",
            ]
        )
    )
    plateau_rules = parameters.get("plateau_diagnostic", {})
    plateau = baseline_plateau_diagnostics(
        sensitivity,
        cagr_tolerance_pct_points=float(
            plateau_rules.get("cagr_tolerance_pct_points", 0.50)
        ),
        sharpe_tolerance=float(plateau_rules.get("sharpe_tolerance", 0.03)),
        reference_mode=str(plateau_rules.get("reference_mode", "baseline")),
    )
    plateau_intersections = cross_window_plateau_intersections(plateau)

    formal_selection = pd.concat(
        [
            pd.concat(selection_frames, ignore_index=True),
            select_plateau_endpoint_cases(plateau, points),
        ],
        ignore_index=True,
    )
    formal_selection = (
        formal_selection.groupby(["window_id", "case_id"], sort=False)["selection_reason"]
        .agg(lambda values: "|".join(dict.fromkeys(values)))
        .reset_index()
    )
    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "buy_hold_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    window_lookup = {window["window_id"]: window for window in parameters["windows"]}
    for selection in formal_selection.itertuples(index=False):
        window = window_lookup[selection.window_id]
        matches = screening[
            screening["window_id"].eq(selection.window_id)
            & screening["case_id"].eq(selection.case_id)
        ]
        if len(matches) != 1:
            raise AssertionError(f"Missing unique formal screen row for {selection.case_id}.")
        candidate = matches.iloc[0].copy()
        candidate["selection_reason"] = selection.selection_reason
        record, frames, differences = formal_case(
            raw,
            candidate,
            window_id=selection.window_id,
            start=pd.Timestamp(window["analysis_start"]),
            end=pd.Timestamp(window["analysis_end"]),
            initial_cash=initial_cash,
        )
        formal_records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal = pd.DataFrame(formal_records)
    elapsed = time.perf_counter() - started
    summary = {
        "symbol": "QQQ",
        "windows": parameters["windows"],
        "unique_candidate_count": len(cases),
        "sweep_point_count": len(points),
        "case_window_count": len(screening),
        "plotted_sweep_point_count": len(sensitivity),
        "formal_case_count": len(formal),
        "plateau_reference_mode": str(plateau_rules.get("reference_mode", "baseline")),
        "elapsed_seconds": elapsed,
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "oat_cases.csv": cases,
        "sweep_definition.csv": points,
        "parameter_results.csv": screening,
        "sensitivity_results.csv": sensitivity,
        "sensitivity_summary.csv": sweep_summary,
        "baseline_plateau_diagnostics.csv": plateau,
        "cross_window_plateau_intersections.csv": plateau_intersections,
        "cross_period_correlations.csv": correlations,
        "formal_selection.csv": formal_selection,
        "formal_candidate_results.csv": formal,
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
        "symbol": "QQQ",
        "cost_bps": 0.0,
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "quantkit.intraday_sma_search compiled ledger; analysis-start metrics",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "parameters": parameters,
        "summary": summary,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset_manifest,
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
    record_block_complete(context, args.run_id, "QQQ", 0.0, manifest_path)
    print(
        f"Completed {len(screening)} case-window screens, {len(sensitivity)} plotted points "
        f"and {len(formal)} formal cases in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
