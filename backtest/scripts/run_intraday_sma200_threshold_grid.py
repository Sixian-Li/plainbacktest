#!/usr/bin/env python3
"""Run one QQQ cost block of the exhaustive intraday SMA200 threshold grid."""

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
from quantkit.intraday_sma_threshold import (
    IntradaySmaThresholdSpec,
    prepare_intraday_threshold_data,
    run_pybroker_intraday_threshold,
    run_reference_intraday_threshold,
)
from quantkit.intraday_sma_threshold_search import exhaustive_cases, screen_cases
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from quantkit.surface import analyze_surface
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025"
)
FORMAL_LEDGER_TOLERANCE = 1e-6


def inclusive_range(definition: dict[str, Any]) -> list[float]:
    start = float(definition["start"])
    stop = float(definition["stop"])
    step = float(definition["step"])
    count = int(round((stop - start) / step)) + 1
    values = [round(start + index * step, 10) for index in range(count)]
    if not np.isclose(values[-1], stop, rtol=0, atol=1e-10):
        raise ValueError("Parameter range is not exactly inclusive.")
    return values


def benchmark_state(
    analysis: pd.DataFrame,
    *,
    initial_cash: float,
    cost_bps: float,
) -> pd.DataFrame:
    result = analysis[["date", "symbol", "close"]].copy()
    entry_open = float(analysis.iloc[0]["open"])
    entry_fill = entry_open * (1.0 + float(cost_bps) / 10_000.0)
    result["cash"] = 0.0
    result["shares"] = float(initial_cash) / entry_fill
    result["equity"] = result["shares"] * result["close"].astype(float)
    result["is_long"] = 1
    result["entry_open"] = entry_open
    result["entry_fill"] = entry_fill
    return result.reset_index(drop=True)


def correction_value(row: pd.Series | dict[str, Any]) -> float | None:
    value = row["correction_pct"]
    return None if pd.isna(value) else float(value)


def asymmetric_correction_value(
    row: pd.Series | dict[str, Any], column: str
) -> float | None:
    if column not in row:
        return correction_value(row)
    value = row[column]
    return None if pd.isna(value) else float(value)


def case_metadata(frame: pd.DataFrame, case_id: str, row: pd.Series) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    result.insert(1, "correction_mode", str(row["correction_mode"]))
    result.insert(2, "a_pct", float(row["a_pct"]))
    result.insert(3, "b_pct", float(row["b_pct"]))
    result.insert(4, "correction_pct", correction_value(row))
    return result


def selected_candidates(
    screening: pd.DataFrame,
    surface_analysis: dict[str, Any],
) -> pd.DataFrame:
    selections: list[tuple[str, str, float, float]] = []
    for mode, analysis in surface_analysis.items():
        global_point = analysis["global_best"]
        stable_point = analysis["stable_representative"]
        selections.extend(
            [
                (mode, "GLOBAL_CAGR", float(global_point["a_pct"]), float(global_point["b_pct"])),
                (mode, "STABLE_PLATEAU", float(stable_point["a_pct"]), float(stable_point["b_pct"])),
                (mode, "ZERO_ANCHOR", 0.0, 0.0),
            ]
        )
    grouped: dict[tuple[str, float, float], list[str]] = {}
    for mode, reason, a_pct, b_pct in selections:
        grouped.setdefault((mode, a_pct, b_pct), []).append(reason)
    rows: list[dict[str, Any]] = []
    for index, ((mode, a_pct, b_pct), reasons) in enumerate(grouped.items(), start=1):
        match = screening[
            (screening["correction_mode"] == mode)
            & np.isclose(screening["a_pct"], a_pct)
            & np.isclose(screening["b_pct"], b_pct)
        ]
        if len(match) != 1:
            raise AssertionError(f"Expected one screening row for {mode}, a={a_pct}, b={b_pct}.")
        row = match.iloc[0].to_dict()
        row["case_id"] = f"FORMAL_{index:02d}"
        row["selection_reason"] = "|".join(reasons)
        rows.append(row)
    return pd.DataFrame(rows)


def formal_case(
    raw: pd.DataFrame,
    analysis: pd.DataFrame,
    candidate: pd.Series,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    window: int,
    cost_bps: float,
    initial_cash: float,
    asymmetric_corrections: bool = False,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float]]:
    case_id = str(candidate["case_id"])
    spec_kwargs: dict[str, Any] = {
        "a_pct": float(candidate["a_pct"]),
        "b_pct": float(candidate["b_pct"]),
        "window": window,
        "cost_bps": cost_bps,
    }
    if asymmetric_corrections:
        spec_kwargs.update(
            {
                "correction_buy_pct": asymmetric_correction_value(
                    candidate, "correction_buy_pct"
                ),
                "correction_sell_pct": asymmetric_correction_value(
                    candidate, "correction_sell_pct"
                ),
            }
        )
    else:
        spec_kwargs["correction_pct"] = correction_value(candidate)
    spec = IntradaySmaThresholdSpec(**spec_kwargs)
    pybroker_run = run_pybroker_intraday_threshold(
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
    reference = run_reference_intraday_threshold(
        raw,
        spec,
        analysis_start=start,
        analysis_end=end,
        initial_cash=initial_cash,
    )
    differences = cross_check(
        result,
        actual,
        reference,
        tolerance=FORMAL_LEDGER_TOLERANCE,
    )
    if not reference.orders.empty and reference.orders.groupby("date").size().max() > 1:
        raise AssertionError(f"{case_id} traded more than once on one day.")
    metrics = calculate_metrics(
        actual,
        reference.orders,
        reference.trades,
        initial_cash=initial_cash,
    )
    for field in (
        "final_equity", "total_return_pct", "cagr_pct", "sharpe",
        "max_drawdown_pct", "exposure_pct",
    ):
        screened = float(candidate[field])
        formal = float(metrics[field])
        tolerance = 1e-7 * max(1.0, abs(formal))
        if not np.isclose(screened, formal, rtol=0, atol=tolerance, equal_nan=True):
            raise AssertionError(
                f"{case_id} screening/formal {field} differs: {screened} vs {formal}"
            )
    if int(candidate["order_count"]) != int(metrics["order_count"]):
        raise AssertionError(f"{case_id} screening/formal order count differs.")

    signal_counts = reference.orders["primary_signal"].value_counts().to_dict()
    fill_counts = reference.orders["fill_source"].value_counts().to_dict()
    record = {
        "case_id": case_id,
        "selection_reason": str(candidate["selection_reason"]),
        "correction_mode": str(candidate["correction_mode"]),
        "correction_pct": correction_value(candidate),
        "a_pct": float(candidate["a_pct"]),
        "b_pct": float(candidate["b_pct"]),
        **metrics,
        "primary_signal_counts": json.dumps(signal_counts, ensure_ascii=False, sort_keys=True),
        "fill_source_counts": json.dumps(fill_counts, ensure_ascii=False, sort_keys=True),
        **differences,
    }
    frames = {
        "daily": case_metadata(actual, case_id, candidate),
        "reference_daily": case_metadata(reference.daily, case_id, candidate),
        "orders": case_metadata(reference.orders, case_id, candidate),
        "trades": case_metadata(reference.trades, case_id, candidate),
        "signal_plans": case_metadata(reference.signal_plans, case_id, candidate),
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
    symbol = str(args.symbol)
    cost_bps = float(args.cost_bps)
    if symbol != "QQQ" or symbol not in context.config["symbols"]:
        raise ValueError("This frozen experiment requires QQQ only.")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if cost_bps not in configured_costs:
        raise ValueError(f"Cost {cost_bps:g} bps is not configured.")
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
    corrections = [
        None if value is None else float(value)
        for value in parameters["correction_scenarios_pct"]
    ]
    cases = exhaustive_cases(a_values, b_values, corrections)
    declared = int(parameters["combination_count_per_cost"])
    if len(cases) != declared:
        raise AssertionError(f"Expected {declared} cases, generated {len(cases)}.")

    started = time.perf_counter()
    screening = screen_cases(
        analysis,
        cases,
        window=window,
        cost_bps=cost_bps,
        initial_cash=initial_cash,
    )
    surfaces: dict[str, Any] = {}
    for mode, frame in screening.groupby("correction_mode", sort=False):
        surfaces[str(mode)] = analyze_surface(frame, metric="cagr_pct", top_quantile=0.90)
    candidates = selected_candidates(screening, surfaces)

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
        formal_records.append(record)
        for name, frame in frames.items():
            frame_groups[name].append(frame)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal = pd.DataFrame(formal_records)
    benchmark = benchmark_state(
        analysis,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_cash,
    )
    elapsed = time.perf_counter() - started
    summary = {
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "screen_case_count": len(screening),
        "formal_candidate_count": len(formal),
        "search_elapsed_seconds": elapsed,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": maximum_differences,
        "modes": {},
    }
    for mode, surface in surfaces.items():
        global_point = surface["global_best"]
        stable_point = surface["stable_representative"]
        summary["modes"][mode] = {
            "global_best": global_point,
            "stable_representative": stable_point,
            "largest_plateau": surface["largest_plateau"],
            "surface_roughness": surface["surface_roughness"],
        }

    outputs = {
        "parameter_results.csv": screening,
        "formal_candidate_results.csv": formal,
        "buy_hold_daily.csv": benchmark,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frame_groups.items()
        },
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "surface_analysis.json").write_text(
        json.dumps(json_safe(surfaces), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
        "screening_engine": "quantkit.intraday_sma_threshold_search numba exhaustive ledger",
        "reference_engine": "quantkit.intraday_sma_threshold.run_reference_intraday_threshold",
        "python": platform.python_version(),
        "parameters": parameters,
        "screening_case_count": len(screening),
        "formal_candidate_count": len(formal),
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
    best = formal.loc[formal["cagr_pct"].idxmax()]
    print(
        f"Completed {len(screening):,} QQQ cases at {cost_bps:g} bps and "
        f"{len(formal)} formal candidates in {elapsed:.1f}s; "
        f"formal best CAGR={best['cagr_pct']:.4f}%"
    )


if __name__ == "__main__":
    main()
