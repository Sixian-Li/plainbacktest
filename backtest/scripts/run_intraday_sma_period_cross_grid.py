#!/usr/bin/env python3
"""Run the frozen QQQ independent buy/sell SMA-period grid."""

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
from quantkit.intraday_sma_period_cross import (
    BUY_SMA_CROSS,
    FORCED_REBUY,
    SELL_SMA_CROSS,
    STOP_LOSS,
    SmaPeriodCrossSpec,
    run_pybroker_sma_period_cross,
    run_reference_sma_period_cross,
)
from quantkit.intraday_sma_period_cross_search import (
    METRIC_COLUMNS,
    analyze_period_surface,
    build_grid_cases,
    screen_period_cross_cases,
    screen_period_cross_cases_reference,
)
from quantkit.intraday_sma_threshold_selection import (
    cscv_pbo,
    deflated_sharpe_probability,
    effective_trial_count,
)
from quantkit.metrics import calculate_metrics, pybroker_daily_state
from scripts.run_intraday_sma200_threshold_grid import benchmark_state
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015"
)
FORMAL_LEDGER_TOLERANCE = 1e-6
COMPILED_LEDGER_TOLERANCE = 1e-9


def _surface_config(parameters: dict[str, Any]) -> dict[str, Any]:
    return dict(parameters["surface_selection"])


def analyze_surfaces(
    results: pd.DataFrame,
    parameters: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    config = _surface_config(parameters)
    analyses: dict[str, Any] = {}
    representatives: list[dict[str, Any]] = []
    for mode, group in results.groupby("correction_mode", sort=False):
        analysis = analyze_period_surface(
            group,
            metric="primary_metric",
            top_quantile=float(config["top_quantile"]),
            minimum_component_cells=int(config["minimum_component_cells"]),
            minimum_buy_span=int(config["minimum_buy_window_span"]),
            minimum_sell_span=int(config["minimum_sell_window_span"]),
        )
        analyses[str(mode)] = analysis
        representative = analysis["representative"]
        if representative is None:
            continue
        match = group[
            (group["buy_window"] == int(representative["buy_window"]))
            & (group["sell_window"] == int(representative["sell_window"]))
        ]
        if len(match) != 1:
            raise AssertionError(f"Expected one representative row for {mode}.")
        row = match.iloc[0]
        representatives.append(
            {
                "correction_mode": str(mode),
                "case_id": str(row["case_id"]),
                "buy_window": int(row["buy_window"]),
                "sell_window": int(row["sell_window"]),
                "forced_rebuy_pct": float(row["forced_rebuy_pct"]),
                "stop_loss_pct": (
                    None if pd.isna(row["stop_loss_pct"]) else float(row["stop_loss_pct"])
                ),
                "primary_metric": float(row["primary_metric"]),
                "cagr_pct": float(row["cagr_pct"]),
                "sharpe": float(row["sharpe"]),
                "max_drawdown_pct": float(row["max_drawdown_pct"]),
                "order_count": int(row["order_count"]),
                "buy_sma_count": int(row["buy_sma_count"]),
                "sell_sma_count": int(row["sell_sma_count"]),
                "forced_rebuy_count": int(row["forced_rebuy_count"]),
                "stop_loss_count": int(row["stop_loss_count"]),
                "component_cells": int(analysis["largest_component"]["cell_count"]),
                "buy_span": int(analysis["largest_component"]["buy_span"]),
                "sell_span": int(analysis["largest_component"]["sell_span"]),
                "touches_search_boundary": bool(
                    analysis["largest_component"]["touches_search_boundary"]
                ),
                "structural_pass": bool(analysis["largest_component"]["structural_pass"]),
            }
        )
    return analyses, pd.DataFrame(representatives)


def selected_formal_candidates(
    results: pd.DataFrame,
    representatives: pd.DataFrame,
) -> pd.DataFrame:
    reasons: dict[str, list[str]] = {}

    def add(case_id: str, reason: str) -> None:
        reasons.setdefault(case_id, []).append(reason)

    for row in representatives.itertuples(index=False):
        add(str(row.case_id), "MODE_SURFACE_REPRESENTATIVE")
    anchors = results[(results["buy_window"] == 200) & (results["sell_window"] == 200)]
    if len(anchors) != 20:
        raise AssertionError(f"Expected 20 SMA200/SMA200 anchors, found {len(anchors)}.")
    for case_id in anchors["case_id"]:
        add(str(case_id), "SMA200_SMA200_MODE_ANCHOR")
    identifiable = results[results["identifiable"]].copy()
    objectives = (
        ("FULL_CAGR_CHAMPION", "cagr_pct"),
        ("FULL_SHARPE_CHAMPION", "sharpe"),
        ("MAX_DRAWDOWN_CHAMPION", "max_drawdown_pct"),
    )
    for reason, metric in objectives:
        index = identifiable[metric].astype(float).idxmax()
        add(str(identifiable.loc[index, "case_id"]), reason)
    selected = results[results["case_id"].isin(reasons)].copy()
    selected["selection_reason"] = selected["case_id"].map(
        lambda case_id: "|".join(reasons[str(case_id)])
    )
    return selected.sort_values("case_id").reset_index(drop=True)


def subperiod_metrics(
    daily: pd.DataFrame,
    definitions: list[dict[str, Any]],
    *,
    initial_cash: float,
) -> dict[str, float]:
    state = daily.copy()
    state["date"] = pd.to_datetime(state["date"])
    state = state.sort_values("date").reset_index(drop=True)
    equity = state["equity"].to_numpy(float)
    returns = np.zeros(len(state), dtype=float)
    if len(state) > 1:
        returns[1:] = equity[1:] / equity[:-1] - 1.0
    output: dict[str, float] = {}
    for item in definitions:
        period_id = str(item["period_id"])
        mask = (
            (state["date"] >= pd.Timestamp(item["start"]))
            & (state["date"] <= pd.Timestamp(item["end"]))
        ).to_numpy()
        indices = np.flatnonzero(mask)
        if not len(indices):
            raise AssertionError(f"Formal ledger has no rows for {period_id}.")
        first = int(indices[0])
        last = int(indices[-1])
        start_equity = float(initial_cash) if first == 0 else float(equity[first - 1])
        elapsed = max((state.iloc[last]["date"] - state.iloc[first]["date"]).days, 1) / 365.2425
        output[f"{period_id}_cagr_pct"] = (
            (float(equity[last]) / start_equity) ** (1.0 / elapsed) - 1.0
        ) * 100.0
        period_returns = returns[indices]
        std = float(np.std(period_returns, ddof=1)) if len(period_returns) > 1 else 0.0
        output[f"{period_id}_sharpe"] = (
            float(np.mean(period_returns) / std * np.sqrt(252.0)) if std > 0 else float("nan")
        )
    output["primary_metric"] = min(
        output[f"{item['period_id']}_cagr_pct"] for item in definitions
    )
    return output


def case_metadata(frame: pd.DataFrame, row: pd.Series) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", str(row["case_id"]))
    result.insert(1, "correction_mode", str(row["correction_mode"]))
    result.insert(2, "buy_window", int(row["buy_window"]))
    result.insert(3, "sell_window", int(row["sell_window"]))
    result.insert(4, "forced_rebuy_pct", float(row["forced_rebuy_pct"]))
    result.insert(
        5,
        "stop_loss_pct",
        np.nan if pd.isna(row["stop_loss_pct"]) else float(row["stop_loss_pct"]),
    )
    return result


def formal_case(
    raw: pd.DataFrame,
    candidate: pd.Series,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    subperiods: list[dict[str, Any]],
    cost_bps: float,
    initial_cash: float,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, float], np.ndarray]:
    stop = None if pd.isna(candidate["stop_loss_pct"]) else float(candidate["stop_loss_pct"])
    spec = SmaPeriodCrossSpec(
        buy_window=int(candidate["buy_window"]),
        sell_window=int(candidate["sell_window"]),
        forced_rebuy_pct=float(candidate["forced_rebuy_pct"]),
        stop_loss_pct=stop,
        cost_bps=float(cost_bps),
    )
    pybroker_run = run_pybroker_sma_period_cross(
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
    reference = run_reference_sma_period_cross(
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
        raise AssertionError(f"{candidate['case_id']} traded more than once in one session.")
    metrics = calculate_metrics(
        actual,
        reference.orders,
        reference.trades,
        initial_cash=initial_cash,
    )
    period_metrics = subperiod_metrics(
        actual,
        subperiods,
        initial_cash=initial_cash,
    )
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
                f"{candidate['case_id']} screening/formal {field} differs: {screened} vs {formal}"
            )
    if int(candidate["order_count"]) != int(metrics["order_count"]):
        raise AssertionError(f"{candidate['case_id']} screening/formal order count differs.")
    counts = reference.orders["primary_signal"].value_counts().to_dict()
    expected_counts = {
        "buy_sma_count": int(counts.get(BUY_SMA_CROSS, 0)),
        "sell_sma_count": int(counts.get(SELL_SMA_CROSS, 0)),
        "forced_rebuy_count": int(counts.get(FORCED_REBUY, 0)),
        "stop_loss_count": int(counts.get(STOP_LOSS, 0)),
    }
    for field, observed in expected_counts.items():
        if int(candidate[field]) != observed:
            raise AssertionError(f"{candidate['case_id']} screening/formal {field} differs.")
    record = {
        "case_id": str(candidate["case_id"]),
        "selection_reason": str(candidate["selection_reason"]),
        "correction_mode": str(candidate["correction_mode"]),
        "buy_window": int(candidate["buy_window"]),
        "sell_window": int(candidate["sell_window"]),
        "forced_rebuy_pct": float(candidate["forced_rebuy_pct"]),
        "stop_loss_pct": stop,
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
        "daily": case_metadata(actual, candidate),
        "reference_daily": case_metadata(reference.daily, candidate),
        "orders": case_metadata(reference.orders, candidate),
        "trades": case_metadata(reference.trades, candidate),
        "signal_plans": case_metadata(reference.signal_plans, candidate),
    }
    returns = actual["equity"].astype(float).pct_change().fillna(0.0).to_numpy(float)
    return record, frames, differences, returns


def paired_stop_loss_deltas(results: pd.DataFrame) -> pd.DataFrame:
    metrics = (
        "primary_metric",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "order_count",
        "exposure_pct",
    )
    rows: list[dict[str, Any]] = []
    for keys, group in results.groupby(
        ["buy_window", "sell_window", "forced_rebuy_pct"], sort=False
    ):
        control = group[group["stop_loss_pct"].isna()]
        if len(control) != 1:
            raise AssertionError(f"Expected one stop-loss-off control for {keys}.")
        baseline = control.iloc[0]
        for _, row in group.iterrows():
            record: dict[str, Any] = {
                "case_id": str(row["case_id"]),
                "control_case_id": str(baseline["case_id"]),
                "buy_window": int(row["buy_window"]),
                "sell_window": int(row["sell_window"]),
                "forced_rebuy_pct": float(row["forced_rebuy_pct"]),
                "stop_loss_pct": (
                    None if pd.isna(row["stop_loss_pct"]) else float(row["stop_loss_pct"])
                ),
                "stop_loss_count": int(row["stop_loss_count"]),
            }
            for metric in metrics:
                record[f"delta_{metric}"] = float(row[metric]) - float(baseline[metric])
            rows.append(record)
    return pd.DataFrame(rows)


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
    initial_cash = float(context.config["initial_cash"])
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"] == "QQQ"].sort_values("date").reset_index(drop=True)
    if raw.iloc[0]["date"] > pd.Timestamp(parameters["requested_data_start"]):
        raise ValueError("Canonical QQQ does not contain the frozen warmup start.")
    cases = build_grid_cases(parameters)
    gate = parameters["identifiability_gate"]
    started = time.perf_counter()
    primary = screen_period_cross_cases(
        raw,
        cases,
        analysis_start=start,
        analysis_end=end,
        cost_bps=5.0,
        initial_cash=initial_cash,
        pbo_block_count=int(parameters["multiple_testing"]["pbo_block_count"]),
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
        pbo_block_count=int(parameters["multiple_testing"]["pbo_block_count"]),
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

    results = primary.metrics
    surfaces, representatives = analyze_surfaces(results, parameters)
    formal_candidates = selected_formal_candidates(results, representatives)
    eligible = results["identifiable"].to_numpy(bool)
    pbo_summary, pbo_splits = cscv_pbo(
        primary.pbo_return_sum,
        primary.pbo_return_sumsq,
        primary.pbo_counts,
        eligible,
    )
    effective_trials = effective_trial_count(primary.pbo_log_returns, eligible)
    paired = paired_stop_loss_deltas(results)

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

    dsr_records: list[dict[str, Any]] = []
    trial_sharpes = results.loc[eligible, "sharpe"].to_numpy(float)
    rep_ids = set(representatives["case_id"].astype(str))
    for case_id in sorted(rep_ids):
        if case_id not in formal_returns:
            raise AssertionError(f"Surface representative {case_id} was not reconciled.")
        for policy, trial_count in (
            ("effective_correlated_trials", float(effective_trials["effective_trial_count"])),
            ("all_28880_cases", float(len(results))),
        ):
            diagnostic = deflated_sharpe_probability(
                formal_returns[case_id],
                trial_sharpes,
                trial_count=trial_count,
            )
            dsr_records.append({"case_id": case_id, "trial_policy": policy, **diagnostic})
    dsr = pd.DataFrame(dsr_records)

    multiple_testing = parameters["multiple_testing"]
    stop_activity_minimum = int(gate["stop_loss_activity_count"])
    representative_gates: list[dict[str, Any]] = []
    for _, representative in representatives.iterrows():
        case_id = str(representative["case_id"])
        case_dsr = dsr[dsr["case_id"] == case_id].set_index("trial_policy")
        if set(case_dsr.index) != {"effective_correlated_trials", "all_28880_cases"}:
            raise AssertionError(f"Expected both DSR policies for {case_id}.")
        effective_probability = float(
            case_dsr.loc["effective_correlated_trials", "probability"]
        )
        full_probability = float(case_dsr.loc["all_28880_cases", "probability"])
        stop_enabled = not pd.isna(representative["stop_loss_pct"])
        stop_activity_pass = bool(
            not stop_enabled
            or int(representative["stop_loss_count"]) >= stop_activity_minimum
        )
        record = {
            **json_safe(representative.to_dict()),
            "label": str(representative["correction_mode"]),
            "positive_primary_metric": bool(float(representative["primary_metric"]) > 0.0),
            "stop_loss_activity_minimum": stop_activity_minimum,
            "stop_loss_activity_pass": stop_activity_pass,
            "pbo_pass": bool(
                float(pbo_summary["pbo"]) <= float(multiple_testing["pbo_maximum"])
            ),
            "dsr_effective_probability": effective_probability,
            "dsr_full_probability": full_probability,
            "dsr_effective_pass": bool(
                effective_probability
                >= float(multiple_testing["dsr_minimum_probability"])
            ),
        }
        record["promotion_gate_pass"] = bool(
            record["structural_pass"]
            and record["positive_primary_metric"]
            and record["stop_loss_activity_pass"]
            and record["pbo_pass"]
            and record["dsr_effective_pass"]
        )
        representative_gates.append(record)

    benchmark = benchmark_state(primary.analysis, initial_cash=initial_cash, cost_bps=5.0)
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(),
        pd.DataFrame(),
        initial_cash=initial_cash,
    )
    subperiod_long: list[dict[str, Any]] = []
    for item in primary.segment_definitions:
        for row in results.itertuples(index=False):
            subperiod_long.append(
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
    subperiod_frame = pd.DataFrame(subperiod_long)
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
        "surface_count": len(surfaces),
        "structurally_qualifying_surface_count": int(
            representatives["structural_pass"].sum() if len(representatives) else 0
        ),
        "formal_candidate_count": len(formal),
        "benchmark_metrics": benchmark_metrics,
        "surface_analysis": surfaces,
        # The catalog headline path is intentionally stable even when the
        # structural gate rejects every surface.  promotion_gate_pass carries
        # the distinction between a deterministic diagnostic representative
        # and a period region that may be nominated for future locking.
        "stable_representatives": representative_gates,
        "promotion_eligible_representatives": [
            item for item in representative_gates if item["promotion_gate_pass"]
        ],
        "pbo": pbo_summary,
        "effective_trials": effective_trials,
        "dsr": json_safe(dsr.to_dict("records")),
        "maximum_compiled_ledger_differences": maximum_compiled_differences,
        "maximum_formal_ledger_differences": maximum_formal_differences,
        "primary_screen_seconds": primary_seconds,
        "reference_screen_seconds": reference_seconds,
        "total_elapsed_seconds": elapsed,
    }

    csv_outputs = {
        "parameter_results.csv": results,
        "compiled_crosscheck.csv": compiled_differences,
        "subperiod_results.csv": subperiod_frame,
        "surface_representatives.csv": representatives,
        "formal_candidate_results.csv": formal,
        "paired_stop_loss_deltas.csv": paired,
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
    (output_root / "surface_analysis.json").write_text(
        json.dumps(json_safe(surfaces), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
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
                    "benchmark_metrics": benchmark_metrics,
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
        "cost_model": "predeclared dynamic SMA cross/open-gap fill with symmetric 5 bps adverse adjustment",
        "engine": "lib-pybroker 1.2.12",
        "screening_engine": "Numba independent buy/sell-period full-grid ledger",
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
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest_path_out = output_root / "manifest.json"
    manifest_path_out.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 5.0, manifest_path_out)
    print(
        f"Completed {len(results):,} QQQ cases, {len(formal):,} formal reconciliations "
        f"and {len(primary.analysis):,} bars in {elapsed:.2f}s."
    )


if __name__ == "__main__":
    main()
