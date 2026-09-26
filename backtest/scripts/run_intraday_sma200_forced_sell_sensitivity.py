#!/usr/bin/env python3
"""Run the frozen QQQ SMA200 asymmetric forced-sell sensitivity experiment."""

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
from quantkit.intraday_sma_threshold import SELL_CORRECTION, prepare_intraday_threshold_data
from quantkit.intraday_sma_threshold_search import screen_cases
from quantkit.intraday_sma_threshold_selection import (
    rolling_five_year_results,
    screen_continuous_cases,
    start_sensitivity_results,
)
from quantkit.metrics import calculate_metrics
from scripts.run_intraday_sma200_threshold_grid import (
    FORMAL_LEDGER_TOLERANCE,
    benchmark_state,
    formal_case,
)
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity"
)


def _pct(value: Any) -> float:
    return -1.0 if value is None or pd.isna(value) else float(value)


def build_cases(parameters: dict[str, Any]) -> pd.DataFrame:
    combinations: list[tuple[float | None, float | None, str]] = []
    seen: set[tuple[float, float]] = set()
    for item in parameters["causal_ablation"]:
        key = (_pct(item["c_pct"]), _pct(item["d_pct"]))
        if key not in seen:
            seen.add(key)
            combinations.append((item["c_pct"], item["d_pct"], "causal_ablation"))
    fixed_c = float(parameters["d_sweep_with_c_fixed_pct"])
    for value in parameters["d_sweep_pct"]:
        key = (_pct(fixed_c), _pct(value))
        if key not in seen:
            seen.add(key)
            combinations.append((fixed_c, value, "d_sweep"))

    rows: list[dict[str, Any]] = []
    for pair in parameters["fixed_ab_pairs"]:
        for c_pct, d_pct, source in combinations:
            c_label = "off" if c_pct is None else f"{float(c_pct):g}"
            d_label = "off" if d_pct is None else f"{float(d_pct):g}"
            rows.append(
                {
                    "case_id": f"CASE_{len(rows) + 1:02d}",
                    "pair_id": str(pair["pair_id"]),
                    "case_source": source,
                    "case_label": f"{pair['pair_id']} | c={c_label}% | d={d_label}%",
                    "a_pct": float(pair["a_pct"]),
                    "b_pct": float(pair["b_pct"]),
                    "correction_buy_pct": np.nan if c_pct is None else float(c_pct),
                    "correction_sell_pct": np.nan if d_pct is None else float(d_pct),
                }
            )
    frame = pd.DataFrame(rows)
    if len(frame) != int(parameters["combination_count"]):
        raise AssertionError(
            f"Expected {parameters['combination_count']} cases, generated {len(frame)}."
        )
    return frame


def _formal_candidate(screen_row: pd.Series) -> pd.Series:
    candidate = screen_row.copy()
    candidate["selection_reason"] = "PREDECLARED_CASE"
    candidate["correction_mode"] = str(candidate["case_label"])
    candidate["correction_pct"] = np.nan
    return candidate


def forced_sell_event_attribution(
    analysis: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    horizons: list[int],
) -> pd.DataFrame:
    prices = analysis[["date", "open", "high", "low", "close"]].copy().reset_index(drop=True)
    prices["date"] = pd.to_datetime(prices["date"])
    index_by_date = {date: index for index, date in enumerate(prices["date"])}
    records: list[dict[str, Any]] = []
    for case_id, case_orders in orders.groupby("case_id", sort=False):
        ordered = case_orders.sort_values("date").reset_index(drop=True)
        forced = ordered[
            (ordered["type"] == "sell")
            & (ordered["primary_signal"] == SELL_CORRECTION)
        ]
        for event_index, (_, sell) in enumerate(forced.iterrows(), start=1):
            date = pd.Timestamp(sell["date"])
            price_index = index_by_date[date]
            later_buy = ordered[
                (pd.to_datetime(ordered["date"]) > date) & (ordered["type"] == "buy")
            ].head(1)
            next_buy = later_buy.iloc[0] if len(later_buy) else None
            record: dict[str, Any] = {
                "case_id": str(case_id),
                "event_index": event_index,
                "sell_date": date,
                "sell_raw_fill": float(sell["raw_fill_price"]),
                "sell_effective_fill": float(sell["fill_price"]),
                "fill_source": str(sell["fill_source"]),
                "next_buy_date": pd.NaT if next_buy is None else pd.Timestamp(next_buy["date"]),
                "next_buy_signal": "" if next_buy is None else str(next_buy["primary_signal"]),
                "next_buy_fill": np.nan if next_buy is None else float(next_buy["fill_price"]),
                "flat_sessions": (
                    np.nan
                    if next_buy is None
                    else int(index_by_date[pd.Timestamp(next_buy["date"])] - price_index)
                ),
                "buyback_vs_sell_pct": (
                    np.nan
                    if next_buy is None
                    else (float(next_buy["fill_price"]) / float(sell["fill_price"]) - 1.0)
                    * 100.0
                ),
            }
            for horizon in horizons:
                end_index = min(price_index + int(horizon), len(prices) - 1)
                # Attribute only sessions after the forced sell. Including the
                # sell bar would let its intraday low masquerade as subsequent
                # downside protection, even though the position was still held
                # before the trigger filled.
                path = prices.iloc[price_index + 1 : end_index + 1]
                record[f"available_sessions_{horizon}"] = int(end_index - price_index)
                record[f"min_low_vs_raw_sell_pct_{horizon}"] = (
                    np.nan
                    if path.empty
                    else (
                        float(path["low"].min()) / float(sell["raw_fill_price"]) - 1.0
                    )
                    * 100.0
                )
                record[f"end_close_vs_raw_sell_pct_{horizon}"] = (
                    np.nan
                    if path.empty
                    else (
                        float(path.iloc[-1]["close"]) / float(sell["raw_fill_price"]) - 1.0
                    )
                    * 100.0
                )
            records.append(record)
    return pd.DataFrame(records)


def paired_deltas(results: pd.DataFrame) -> pd.DataFrame:
    metrics = (
        "rolling_5y_cagr_q25_pct",
        "restart_10y_cagr_q25_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "final_equity",
    )
    rows: list[dict[str, Any]] = []
    for pair_id, group in results.groupby("pair_id", sort=False):
        controls = group[
            np.isclose(group["correction_buy_pct"], 5.0)
            & group["correction_sell_pct"].isna()
        ]
        if len(controls) != 1:
            raise AssertionError(f"Expected one c=5,d-off control for {pair_id}.")
        control = controls.iloc[0]
        for _, row in group[np.isclose(group["correction_buy_pct"], 5.0)].iterrows():
            record = {
                "case_id": str(row["case_id"]),
                "pair_id": str(pair_id),
                "d_pct": row["correction_sell_pct"],
                "control_case_id": str(control["case_id"]),
            }
            for metric in metrics:
                record[f"delta_{metric}"] = float(row[metric]) - float(control[metric])
            rows.append(record)
    return pd.DataFrame(rows)


def evaluate_plateau(results: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"pairs": {}}
    for pair_id, group in results.groupby("pair_id", sort=False):
        sweep = group[
            np.isclose(group["correction_buy_pct"], 5.0)
            & group["correction_sell_pct"].notna()
        ].sort_values("correction_sell_pct")
        control = group[
            np.isclose(group["correction_buy_pct"], 5.0)
            & group["correction_sell_pct"].isna()
        ].iloc[0]
        eligible: list[float] = []
        detail: list[dict[str, Any]] = []
        for _, row in sweep.iterrows():
            passes_regression = bool(
                row["rolling_5y_cagr_q25_pct"] >= control["rolling_5y_cagr_q25_pct"] - 0.50
                and row["restart_10y_cagr_q25_pct"] >= control["restart_10y_cagr_q25_pct"] - 0.50
                and row["cagr_pct"] >= control["cagr_pct"] - 0.25
                and row["sharpe"] >= control["sharpe"] - 0.03
                and row["max_drawdown_pct"] >= control["max_drawdown_pct"] - 2.0
            )
            strict_improvement = bool(
                row["max_drawdown_pct"] >= control["max_drawdown_pct"] + 1.0
                or row["cagr_pct"] >= control["cagr_pct"] + 0.25
            )
            activity = int(row["sell_correction_count"]) >= 10
            if passes_regression:
                eligible.append(float(row["correction_sell_pct"]))
            detail.append(
                {
                    "d_pct": float(row["correction_sell_pct"]),
                    "passes_no_material_regression": passes_regression,
                    "strict_improvement": strict_improvement,
                    "activity_gate": activity,
                    "sell_correction_count": int(row["sell_correction_count"]),
                }
            )
        runs: list[list[float]] = []
        for value in eligible:
            if not runs or not np.isclose(value - runs[-1][-1], 2.5):
                runs.append([value])
            else:
                runs[-1].append(value)
        qualifying_before_activity = []
        qualifying = []
        for run in runs:
            points = [item for item in detail if item["d_pct"] in run]
            if len(run) >= 3 and any(item["strict_improvement"] for item in points):
                qualifying_before_activity.append(run)
                if all(item["activity_gate"] for item in points):
                    qualifying.append(run)
        activity_pass = any(
            all(item["activity_gate"] for item in detail if item["d_pct"] in run)
            for run in qualifying_before_activity
        )
        selected = max(qualifying, key=len) if qualifying else None
        output["pairs"][str(pair_id)] = {
            "control_case_id": str(control["case_id"]),
            "point_diagnostics": detail,
            "qualifying_plateaus_before_activity_gate": qualifying_before_activity,
            "activity_gate_any_candidate_plateau": activity_pass,
            "qualifying_plateaus_after_activity_gate": qualifying,
            "selected_plateau": selected,
            "selected_d_pct": None if selected is None else float(np.median(selected)),
            "nomination_pass": bool(selected is not None),
        }
    output["overall_nomination_pass"] = bool(
        all(item["nomination_pass"] for item in output["pairs"].values())
        and len(
            {
                tuple(item["selected_plateau"])
                for item in output["pairs"].values()
                if item["selected_plateau"] is not None
            }
        )
        == 1
    )
    return output


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
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_manifest = next(
        item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ"
    )
    if dataset_manifest["effective_status"] != "approved":
        raise RuntimeError("QQQ canonical data is not approved.")

    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    window = int(context.config["strategy"]["sma_window"])
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)
    prepared = prepare_intraday_threshold_data(raw, window)
    analysis = prepared[(prepared["date"] >= start) & (prepared["date"] <= end)].reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen normalized dates do not match QQQ canonical sessions.")

    cases = build_cases(parameters)
    started = time.perf_counter()
    continuous = screen_continuous_cases(
        analysis,
        cases,
        window=window,
        cost_bps=5.0,
        initial_cash=initial_cash,
        pbo_block_count=12,
    )
    rolling_summary, rolling_long = rolling_five_year_results(
        continuous, analysis["date"], continuous.metrics["case_id"]
    )

    restart_years = [int(value) for value in parameters["restart_sensitivity"]["start_years"]]
    restart_columns: list[np.ndarray] = []
    restart_windows: list[dict[str, Any]] = []
    for year in restart_years:
        restart = prepared[
            (prepared["date"] >= pd.Timestamp(year=year, month=1, day=1))
            & (prepared["date"] <= pd.Timestamp(year=year + 9, month=12, day=31))
        ].reset_index(drop=True)
        symmetric_cases = cases.copy()
        # screen_cases uses the legacy symmetric engine, so use the continuous
        # ledger here on each independently flat restart to retain asymmetric c/d.
        restart_screen = screen_continuous_cases(
            restart,
            symmetric_cases,
            window=window,
            cost_bps=5.0,
            initial_cash=initial_cash,
            pbo_block_count=4,
        ).metrics
        restart_columns.append(restart_screen["cagr_pct"].to_numpy(float))
        restart_windows.append(
            {
                "start_year": year,
                "analysis_start": pd.Timestamp(restart.iloc[0]["date"]).date().isoformat(),
                "analysis_end": pd.Timestamp(restart.iloc[-1]["date"]).date().isoformat(),
                "bars": len(restart),
            }
        )
    restart_matrix = np.column_stack(restart_columns)
    restart_summary, restart_long = start_sensitivity_results(
        continuous.metrics["case_id"], restart_years, restart_matrix
    )
    results = continuous.metrics.merge(rolling_summary, on="case_id", validate="one_to_one")
    results = results.merge(restart_summary, on="case_id", validate="one_to_one")

    formal_records: list[dict[str, Any]] = []
    frame_groups: dict[str, list[pd.DataFrame]] = {
        name: [] for name in ("daily", "reference_daily", "orders", "trades", "signal_plans")
    }
    maximum_differences: dict[str, float] = {}
    for _, screened in results.iterrows():
        candidate = _formal_candidate(screened)
        record, frames, differences = formal_case(
            raw,
            analysis,
            candidate,
            start=start,
            end=end,
            window=window,
            cost_bps=5.0,
            initial_cash=initial_cash,
            asymmetric_corrections=True,
        )
        counts = frames["orders"]["primary_signal"].value_counts()
        for field, signal in (
            ("buy_sma_count", "BUY_SMA200_THRESHOLD"),
            ("sell_sma_count", "SELL_SMA200_THRESHOLD"),
            ("buy_correction_count", "BUY_CORRECTION"),
            ("sell_correction_count", "SELL_CORRECTION"),
        ):
            if int(screened[field]) != int(counts.get(signal, 0)):
                raise AssertionError(f"{screened['case_id']} {field} screening/formal mismatch.")
            record[field] = int(screened[field])
        record.update(
            {
                "pair_id": str(screened["pair_id"]),
                "case_label": str(screened["case_label"]),
                "case_source": str(screened["case_source"]),
                "correction_buy_pct": (
                    None if pd.isna(screened["correction_buy_pct"]) else float(screened["correction_buy_pct"])
                ),
                "correction_sell_pct": (
                    None if pd.isna(screened["correction_sell_pct"]) else float(screened["correction_sell_pct"])
                ),
                **{
                    name: float(screened[name])
                    for name in (
                        "rolling_5y_cagr_q25_pct",
                        "rolling_5y_cagr_median_pct",
                        "rolling_5y_cagr_min_pct",
                        "rolling_5y_sharpe_q25",
                        "restart_10y_cagr_q25_pct",
                        "restart_10y_cagr_median_pct",
                        "restart_10y_cagr_min_pct",
                    )
                },
            }
        )
        formal_records.append(record)
        for name, frame in frames.items():
            tagged = frame.copy()
            tagged["pair_id"] = str(screened["pair_id"])
            tagged["case_label"] = str(screened["case_label"])
            tagged["correction_buy_pct"] = screened["correction_buy_pct"]
            tagged["correction_sell_pct"] = screened["correction_sell_pct"]
            frame_groups[name].append(tagged)
        for key, value in differences.items():
            maximum_differences[key] = max(maximum_differences.get(key, 0.0), float(value))
    formal = pd.DataFrame(formal_records)
    orders = pd.concat(frame_groups["orders"], ignore_index=True)
    events = forced_sell_event_attribution(
        analysis,
        orders,
        horizons=[int(value) for value in parameters["event_attribution"]["forward_sessions"]],
    )
    deltas = paired_deltas(results)
    plateau = evaluate_plateau(results, parameters["selection_rule"])

    benchmark = benchmark_state(analysis, initial_cash=initial_cash, cost_bps=5.0)
    benchmark_metrics = calculate_metrics(
        benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash
    )
    elapsed = time.perf_counter() - started
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "case_count": len(results),
        "restart_window_count": len(restart_years),
        "rolling_window_count": int(rolling_long["window_start_year"].nunique()),
        "forced_sell_event_count": len(events),
        "selection": plateau,
        "benchmark_metrics": benchmark_metrics,
        "restart_windows": restart_windows,
        "search_elapsed_seconds": elapsed,
        "max_cross_check_differences": maximum_differences,
    }

    outputs = {
        "parameter_results.csv": results,
        "formal_candidate_results.csv": formal,
        "rolling_5y_results.csv": rolling_long,
        "restart_10y_results.csv": restart_long,
        "forced_sell_events.csv": events,
        "paired_deltas.csv": deltas,
        "buy_hold_daily.csv": benchmark,
        **{
            f"{name}.csv": pd.concat(frames, ignore_index=True)
            for name, frames in frame_groups.items()
        },
    }
    for name, frame in outputs.items():
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
                    "analysis_bars": len(analysis),
                    "formal_candidate_count": len(formal),
                    "benchmark_metrics": benchmark_metrics,
                    "max_cross_check_differences": maximum_differences,
                }
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )

    block_manifest: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 5.0,
        "cost_model": "raw threshold/open-gap fill with symmetric 5 bps adverse fill adjustment",
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "Numba continuous asymmetric c/d ledger plus independent flat restarts",
        "reference_engine": "quantkit.intraday_sma_threshold.run_reference_intraday_threshold",
        "python": platform.python_version(),
        "parameters": parameters,
        "formal_candidate_count": len(formal),
        "selection_summary": summary,
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
    manifest = output_root / "manifest.json"
    manifest.write_text(
        json.dumps(json_safe(block_manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 5.0, manifest)
    print(
        f"Completed {len(formal)} asymmetric c/d cases, {len(restart_years)} restarts "
        f"and {len(events)} forced-sell events in {elapsed:.1f}s; "
        f"nomination_pass={plateau['overall_nomination_pass']}"
    )


if __name__ == "__main__":
    main()
