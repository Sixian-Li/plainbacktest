#!/usr/bin/env python3
"""Run the QQQ three-window local parameter stability experiment."""

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
    prepare_intraday_sma_data,
    run_pybroker_intraday_sma,
    run_reference_intraday_sma,
)
from quantkit.intraday_sma_search import (
    FORCED_REENTRY_COLUMN,
    PARAMETER_COLUMNS,
    case_columns,
    sample_cases_with_reentry_modes,
    sample_refined_cases_with_reentry_modes,
    screen_cases,
    select_frontier_parents,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.window_stability import add_window_ranks, select_representatives, top_fraction_summary
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_intraday_sma_global_search import spec_from_row
from scripts.run_intraday_sma_reentry_grid import benchmark_state


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability"
FORMAL_LEDGER_TOLERANCE = 1e-6


def disabled_signals(row: pd.Series | dict[str, Any]) -> tuple[str, ...]:
    value = row[FORCED_REENTRY_COLUMN]
    enabled = value if isinstance(value, (bool, np.bool_)) else str(value).lower() == "true"
    return () if enabled else (BUY_FORCED_REENTRY,)


def with_identity(
    frame: pd.DataFrame,
    *,
    evaluation_case_id: str,
    representative_id: str,
    window_id: str,
) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "window_id", window_id)
    result.insert(0, "representative_id", representative_id)
    result.insert(0, "case_id", evaluation_case_id)
    return result


def formal_cross_case(
    raw: pd.DataFrame,
    candidate: pd.Series,
    *,
    window_id: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float]]:
    representative_id = str(candidate["representative_id"])
    evaluation_case_id = f"{representative_id}__{window_id}"
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
        raise AssertionError(f"{evaluation_case_id} has no ordinary buy.")
    first = reference.orders.iloc[0]
    if str(first["type"]) != "buy" or bool(first["is_initial_seed"]):
        raise AssertionError(f"{evaluation_case_id} did not begin with an ordinary buy.")
    if reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{evaluation_case_id} traded more than once on one day.")
    if BUY_FORCED_REENTRY in disabled and BUY_FORCED_REENTRY in set(reference.orders["primary_signal"]):
        raise AssertionError(f"{evaluation_case_id} emitted a disabled forced re-entry.")

    first_date = pd.Timestamp(first["date"]).normalize()
    first_fill = float(first["fill_price"])
    daily = actual[actual["date"] >= first_date].reset_index(drop=True)
    reference_daily = reference.daily[reference.daily["date"] >= first_date].reset_index(drop=True)
    analysis = raw[(raw["date"] >= start) & (raw["date"] <= end)].reset_index(drop=True)
    benchmark = benchmark_state(
        analysis,
        first_entry_date=first_date,
        first_entry_fill=first_fill,
        initial_cash=initial_cash,
    )
    metrics = calculate_metrics(daily, reference.orders, reference.trades, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    for field in ("final_equity", "cagr_pct", "sharpe", "max_drawdown_pct", "exposure_pct"):
        screened = float(candidate[field])
        formal = float(metrics[field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance):
            raise AssertionError(
                f"{evaluation_case_id} screening/formal {field} differs: {screened} vs {formal}"
            )
    if int(candidate["order_count"]) != int(metrics["order_count"]):
        raise AssertionError(f"{evaluation_case_id} screening/formal order count differs.")
    if pd.Timestamp(candidate["first_entry_date"]).normalize() != first_date:
        raise AssertionError(f"{evaluation_case_id} screening/formal first date differs.")

    record = {
        "case_id": evaluation_case_id,
        "representative_id": representative_id,
        "window_id": window_id,
        "selection_reason": str(candidate["selection_reason"]),
        **{name: float(candidate[name]) for name in PARAMETER_COLUMNS},
        FORCED_REENTRY_COLUMN: bool(candidate[FORCED_REENTRY_COLUMN]),
        **metrics,
        "first_entry_date": first_date,
        "first_entry_fill": first_fill,
        "first_entry_signal": str(first["primary_signal"]),
        "benchmark_final_equity": benchmark_metrics["final_equity"],
        "benchmark_cagr_pct": benchmark_metrics["cagr_pct"],
        "benchmark_sharpe": benchmark_metrics["sharpe"],
        "benchmark_max_drawdown_pct": benchmark_metrics["max_drawdown_pct"],
        "delta_cagr_vs_buy_hold_pct_points": metrics["cagr_pct"] - benchmark_metrics["cagr_pct"],
        "delta_sharpe_vs_buy_hold": metrics["sharpe"] - benchmark_metrics["sharpe"],
        "cagr_pct_rank": int(candidate["cagr_pct_rank"]),
        "cagr_pct_rank_percentile": float(candidate["cagr_pct_rank_percentile"]),
        "sharpe_rank": int(candidate["sharpe_rank"]),
        "sharpe_rank_percentile": float(candidate["sharpe_rank_percentile"]),
        "primary_signal_counts": json.dumps(
            reference.orders["primary_signal"].value_counts().to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        **differences,
    }
    frames = {
        "daily": with_identity(daily, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
        "reference_daily": with_identity(reference_daily, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
        "buy_hold_daily": with_identity(benchmark, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
        "orders": with_identity(reference.orders, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
        "trades": with_identity(reference.trades, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
        "signal_plans": with_identity(reference.signal_plans, evaluation_case_id=evaluation_case_id, representative_id=representative_id, window_id=window_id),
    }
    return record, frames, differences


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
    windows = parameters["windows"]
    window_order = [item["window_id"] for item in windows]
    initial_cash = float(context.config["initial_cash"])
    search_space = parameters["search_space"]
    anchors = parameters["anchors"]
    seed = int(parameters["random_seed"])
    started = time.perf_counter()

    common_cases = sample_cases_with_reentry_modes(
        search_space,
        reentry_modes=parameters["forced_reentry_modes"],
        count=int(parameters["common_sample_count"]),
        seed=seed,
        anchors=anchors,
    )
    common_cases["search_origins"] = "common"
    common_screens: dict[str, pd.DataFrame] = {}
    refined_frames: list[pd.DataFrame] = []
    for index, window in enumerate(windows):
        start = pd.Timestamp(window["analysis_start"])
        end = pd.Timestamp(window["analysis_end"])
        screen = screen_cases(
            raw,
            common_cases.drop(columns=["search_origins"]),
            analysis_start=start,
            analysis_end=end,
            initial_cash=initial_cash,
        )
        screen.insert(0, "window_id", window["window_id"])
        common_screens[window["window_id"]] = screen
        parents = select_frontier_parents(
            screen,
            per_objective=int(parameters["frontier_parent_count_per_objective"]),
        )
        refined = sample_refined_cases_with_reentry_modes(
            search_space,
            parents,
            common_cases,
            reentry_modes=parameters["forced_reentry_modes"],
            count=int(parameters["refined_sample_count_per_window"]),
            seed=seed + index + 1,
        )
        refined["search_origins"] = f"refined:{window['window_id']}"
        refined_frames.append(refined)

    identity_columns = [*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN]
    candidates = pd.concat([common_cases, *refined_frames], ignore_index=True)
    candidates["search_origins"] = candidates.groupby(identity_columns, dropna=False)["search_origins"].transform(
        lambda values: "|".join(dict.fromkeys(values.astype(str)))
    )
    candidates = candidates.drop_duplicates(identity_columns, ignore_index=True)
    candidates.insert(0, "screen_case_id", [f"LOCAL_{index:06d}" for index in range(1, len(candidates) + 1)])

    screened_frames: list[pd.DataFrame] = []
    for window in windows:
        screen = screen_cases(
            raw,
            candidates,
            analysis_start=pd.Timestamp(window["analysis_start"]),
            analysis_end=pd.Timestamp(window["analysis_end"]),
            initial_cash=initial_cash,
        )
        screen.insert(0, "window_id", window["window_id"])
        screened_frames.append(screen)
    screening = add_window_ranks(pd.concat(screened_frames, ignore_index=True))
    if screening[identity_columns + ["window_id"]].duplicated().any():
        raise AssertionError("Final screening contains duplicate case/window rows.")
    expected_rows = len(candidates) * len(windows)
    if len(screening) != expected_rows:
        raise AssertionError(f"Expected {expected_rows} final screen rows, observed {len(screening)}.")

    representatives = select_representatives(
        screening,
        parameter_columns=identity_columns,
        window_order=window_order,
        anchor=anchors[0],
    )
    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "buy_hold_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for representative in representatives.itertuples(index=False):
        representative_values = representative._asdict()
        for window in windows:
            window_id = window["window_id"]
            mask = screening["window_id"].eq(window_id)
            for column in identity_columns:
                mask &= screening[column].eq(representative_values[column])
            matches = screening[mask]
            if len(matches) != 1:
                raise AssertionError(f"Could not find unique {representative.representative_id}/{window_id} screen row.")
            candidate = matches.iloc[0].copy()
            candidate["representative_id"] = representative.representative_id
            candidate["selection_reason"] = representative.selection_reason
            record, frames, differences = formal_cross_case(
                raw,
                candidate,
                window_id=window_id,
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
    top_summary = top_fraction_summary(
        screening,
        parameter_columns=identity_columns,
        fraction=float(parameters["top_fraction_for_stability"]),
    )
    window_winners = []
    for window_id in window_order:
        window = screening[screening["window_id"] == window_id]
        for objective in ("cagr_pct", "sharpe"):
            row = window.loc[window[objective].idxmax()]
            window_winners.append({
                "window_id": window_id,
                "objective": objective,
                "screen_case_id": row["screen_case_id"],
                "value": float(row[objective]),
                **{name: row[name] for name in identity_columns},
            })
    winners = pd.DataFrame(window_winners)
    elapsed = time.perf_counter() - started
    summary = {
        "symbol": "QQQ",
        "windows": windows,
        "common_sample_count": len(common_cases),
        "unique_candidate_count": len(candidates),
        "screened_case_window_count": len(screening),
        "representative_count": len(representatives),
        "formal_cross_case_count": len(formal),
        "search_elapsed_seconds": elapsed,
        "window_winners": json_safe(winners.to_dict("records")),
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "parameter_results.csv": screening,
        "representative_parameters.csv": representatives,
        "formal_candidate_results.csv": formal,
        "window_winners.csv": winners,
        "top_percent_parameter_summary.csv": top_summary,
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
        "screening_engine": "quantkit.intraday_sma_search numba compiled ledger with pre-window warmup",
        "reference_engine": "quantkit.intraday_sma.run_reference_intraday_sma",
        "python": platform.python_version(),
        "parameters": parameters,
        "search_summary": summary,
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
        f"Completed {len(screening):,} case-window screens and {len(formal)} formal cross-cases "
        f"from {len(candidates):,} unique candidates in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
