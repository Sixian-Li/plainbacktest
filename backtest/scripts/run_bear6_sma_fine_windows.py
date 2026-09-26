#!/usr/bin/env python3
"""Run the frozen six-asset target-specific SMA fine-window scan."""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quantkit.bear_atr_hard_stop import (
    POLICY_HOLD,
    POLICY_SMA,
    BearAtrSpec,
    compiled_pybroker_path,
    cross_check_policy_paths,
    prepare_symbol_data,
    run_pybroker_compiled,
    run_reference_policy,
)
from quantkit.bear_event_sma_portfolio import calculate_interval_returns
from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.paths import BACKTEST_ROOT
from scripts.run_bear24_sma_window_plateau import (
    LEDGER_TOLERANCE,
    add_interval_drawdowns,
    build_scope_summary,
    concat_or_empty,
    sma_identifier,
)
from scripts.run_bear_event_sma_portfolios import (
    SPY_PATH,
    bear_session_mask,
    build_common_calendar,
    json_safe,
    load_candidate_frames,
    load_canonical,
    normalize_frame,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan"
)
SYMBOL_BLOCK = "BEAR6_SMA_FINE_WINDOW_GRID"
STRATEGY_HOLD = "hold"
EXPECTED_TARGETS = ("AZO", "TLT", "SO", "ED", "MO", "GIS")
EXPECTED_RANGES = {
    "AZO": (190, 270, 1),
    "TLT": (130, 200, 1),
    "SO": (1, 50, 1),
    "ED": (1, 50, 1),
    "MO": (80, 120, 1),
    "GIS": (270, 500, 10),
}


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    target: str
    target_type: str
    members: tuple[str, ...]
    strategy_id: str
    sma_window: int | None


def build_windows_by_target(parameters: Mapping[str, Any]) -> dict[str, list[int]]:
    targets = tuple(str(value) for value in parameters["targets"])
    if targets != EXPECTED_TARGETS:
        raise AssertionError("formal target order changed")
    configured_ranges = parameters["target_window_ranges"]
    configured_windows = parameters["formal_sma_windows_by_target"]
    result: dict[str, list[int]] = {}
    for target in EXPECTED_TARGETS:
        expected_start, expected_end, expected_step = EXPECTED_RANGES[target]
        expected = list(range(expected_start, expected_end + 1, expected_step))
        frozen_range = configured_ranges[target]
        actual_range = (
            int(frozen_range["start"]),
            int(frozen_range["end"]),
            int(frozen_range["step"]),
        )
        actual = [int(value) for value in configured_windows[target]]
        if actual_range != EXPECTED_RANGES[target] or actual != expected:
            raise AssertionError(f"formal {target} SMA grid changed")
        if any(window < 1 for window in actual):
            raise AssertionError("SMA0 must never be constructed")
        result[target] = actual
    if int(parameters["common_warmup_window"]) != 500:
        raise AssertionError("formal scan must use common SMA500 warmup")
    if str(parameters["baseline_strategy_id"]) != STRATEGY_HOLD:
        raise AssertionError("formal baseline must remain Hold")
    return result


def build_case_specs(parameters: Mapping[str, Any]) -> list[CaseSpec]:
    windows_by_target = build_windows_by_target(parameters)
    cases: list[CaseSpec] = []
    for target in EXPECTED_TARGETS:
        cases.append(
            CaseSpec(
                case_id=f"{target}__{STRATEGY_HOLD}",
                target=target,
                target_type="single",
                members=(target,),
                strategy_id=STRATEGY_HOLD,
                sma_window=None,
            )
        )
        for window in windows_by_target[target]:
            strategy_id = sma_identifier(window)
            cases.append(
                CaseSpec(
                    case_id=f"{target}__{strategy_id}",
                    target=target,
                    target_type="single",
                    members=(target,),
                    strategy_id=strategy_id,
                    sma_window=window,
                )
            )
    expected_count = int(parameters["formal_case_count_per_cost"])
    if len(cases) != expected_count or len(cases) != 323:
        raise AssertionError("formal case count must be 323 per cost")
    if len({case.case_id for case in cases}) != len(cases):
        raise AssertionError("formal case IDs must be unique")
    return cases


def tagged(frame: pd.DataFrame, case: CaseSpec) -> pd.DataFrame:
    result = frame.copy()
    values = {
        "case_id": case.case_id,
        "target": case.target,
        "target_type": case.target_type,
        "strategy_id": case.strategy_id,
        "sma_window": case.sma_window,
    }
    for column, value in reversed(list(values.items())):
        if column in result:
            result[column] = value
        else:
            result.insert(0, column, value)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != SYMBOL_BLOCK or args.symbol not in context.config["symbols"]:
        raise ValueError(f"runner only accepts {SYMBOL_BLOCK}")
    costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if float(args.cost_bps) not in costs:
        raise ValueError("cost scenario is not configured")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))
    parameters = context.config["parameters"]
    cases = build_case_specs(parameters)
    windows_by_target = build_windows_by_target(parameters)

    candidates, source_paths = load_candidate_frames(list(EXPECTED_TARGETS))
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    spy = load_canonical(SPY_PATH, "SPY")
    calendar, exclusions = build_common_calendar(spy, candidates, start=start, end=end)
    dates = pd.DatetimeIndex(calendar)
    interval_path = WORKSPACE_ROOT / parameters["bear_interval_source"]
    intervals = pd.DataFrame(json.loads(interval_path.read_text(encoding="utf-8"))["intervals"])
    if len(intervals) != 12 or intervals["severity"].value_counts().to_dict() != {
        "major": 6,
        "minor": 6,
    }:
        raise ValueError("formal event source must contain six major and six minor intervals")
    if int(intervals.sort_values("ordinal").iloc[0]["ordinal"]) != 1:
        raise ValueError("the 2000-2002 bear must remain ordinal one")
    boundaries = set(pd.to_datetime(intervals["start"])).union(pd.to_datetime(intervals["end"]))
    next_dates = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    required = boundaries.union(next_dates[date] for date in boundaries)
    if required.difference(dates):
        raise ValueError("common calendar removed an event boundary or liquidation date")

    buffer = float(parameters["sma_buffer_pct"]) / 100.0
    common_spec = BearAtrSpec(
        sma_window=int(parameters["common_warmup_window"]),
        sma_buffer=buffer,
    )
    common_prepared = {
        symbol: prepare_symbol_data(frame, dates, common_spec)
        for symbol, frame in candidates.items()
    }
    common_ready = {
        symbol: frame["indicator_ready"].to_numpy(dtype=bool)
        for symbol, frame in common_prepared.items()
    }
    fill_policy = ExplicitFillPolicy("open", float(args.cost_bps))

    metric_rows: list[dict[str, Any]] = []
    frames: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "orders",
            "reference_orders",
            "trades",
            "signals",
            "interval_returns",
            "scope_summary",
            "interval_schedule",
        )
    }
    daily_cash: list[np.ndarray] = []
    daily_equity: list[np.ndarray] = []
    daily_exposure: list[np.ndarray] = []
    daily_is_long: list[np.ndarray] = []
    difference_rows: list[dict[str, Any]] = []
    case_lookup = {case.case_id: case for case in cases}

    completed = 0
    for target_index, target in enumerate(EXPECTED_TARGETS, start=1):
        target_cases = [case_lookup[f"{target}__{STRATEGY_HOLD}"]] + [
            case_lookup[f"{target}__{sma_identifier(window)}"]
            for window in windows_by_target[target]
        ]
        for case in target_cases:
            if case.sma_window is None:
                spec = common_spec
                prepared = common_prepared[target]
                engine_policy = POLICY_HOLD
            else:
                spec = BearAtrSpec(sma_window=case.sma_window, sma_buffer=buffer)
                prepared = prepare_symbol_data(candidates[target], dates, spec)
                prepared.loc[:, "indicator_ready"] = common_ready[target]
                engine_policy = POLICY_SMA

            reference = run_reference_policy(
                prepared,
                intervals,
                policy_id=engine_policy,
                initial_cash=float(context.config["initial_cash"]),
                fill_policy=fill_policy,
                spec=spec,
                case_id=case.case_id,
            )
            compiled = run_pybroker_compiled(
                prepared,
                reference,
                initial_cash=float(context.config["initial_cash"]),
            )
            actual = compiled_pybroker_path(
                compiled,
                prepared,
                reference,
                initial_cash=float(context.config["initial_cash"]),
            )
            differences = cross_check_policy_paths(
                actual,
                reference,
                tolerance=LEDGER_TOLERANCE,
            )
            interval_returns = add_interval_drawdowns(
                calculate_interval_returns(actual.daily, actual.interval_schedule),
                actual.daily,
            )
            scope_summary = build_scope_summary(interval_returns)
            metrics = calculate_metrics(
                actual.daily,
                actual.orders,
                actual.trades,
                initial_cash=float(context.config["initial_cash"]),
            )
            event_daily = actual.daily.loc[
                bear_session_mask(actual.daily["date"], actual.interval_schedule)
            ]
            metrics["average_bear_gross_exposure_pct"] = float(
                event_daily["gross_exposure"].mean() * 100.0
            )
            metrics["average_bear_cash_pct"] = float(
                (event_daily["cash"] / event_daily["equity"]).mean() * 100.0
            )
            metrics["eligible_interval_count"] = int(
                actual.interval_schedule["eligible_at_start"].astype(bool).sum()
            )
            metrics["sma_exit_count"] = int((actual.orders["reason"] == "sma_exit").sum())
            metrics["sma_cross_entry_count"] = int(
                (actual.orders["reason"] == "sma_entry_cross").sum()
            )
            for row in scope_summary.itertuples(index=False):
                metrics[f"{row.scope}_bear_compound_return_pct"] = float(
                    row.compound_return * 100.0
                )
                metrics[f"{row.scope}_bear_worst_interval_return_pct"] = float(
                    row.worst_interval_return * 100.0
                )
                metrics[f"{row.scope}_bear_zero_interval_count"] = int(row.zero_interval_count)
            metric_rows.append(
                {
                    "case_id": case.case_id,
                    "target": case.target,
                    "target_type": case.target_type,
                    "strategy_id": case.strategy_id,
                    "sma_window": case.sma_window,
                    **metrics,
                }
            )
            frames["orders"].append(tagged(actual.orders, case))
            frames["reference_orders"].append(tagged(reference.orders, case))
            frames["trades"].append(tagged(actual.trades, case))
            frames["signals"].append(tagged(actual.signals, case))
            frames["interval_returns"].append(tagged(interval_returns, case))
            frames["scope_summary"].append(tagged(scope_summary, case))
            frames["interval_schedule"].append(tagged(actual.interval_schedule, case))
            daily_cash.append(actual.daily["cash"].to_numpy(dtype=np.float64))
            daily_equity.append(actual.daily["equity"].to_numpy(dtype=np.float64))
            daily_exposure.append(actual.daily["gross_exposure"].to_numpy(dtype=np.float64))
            daily_is_long.append(actual.daily["is_long"].to_numpy(dtype=np.int8))
            difference_rows.extend(
                {
                    "case_id": case.case_id,
                    "target": case.target,
                    "strategy_id": case.strategy_id,
                    "sma_window": case.sma_window,
                    "measure": measure,
                    "difference": value,
                }
                for measure, value in differences.items()
            )
            completed += 1
        print(
            f"[{target_index}/6] completed {target} ({len(target_cases)} cases); "
            f"{completed}/{len(cases)} total at {args.cost_bps:g} bps",
            flush=True,
        )

    metrics_frame = pd.DataFrame(metric_rows)
    combined = {name: concat_or_empty(items) for name, items in frames.items()}
    case_index = pd.DataFrame(
        {
            "case_index": range(len(cases)),
            "case_id": [case.case_id for case in cases],
            "target": [case.target for case in cases],
            "target_type": [case.target_type for case in cases],
            "strategy_id": [case.strategy_id for case in cases],
            "sma_window": [case.sma_window for case in cases],
            "members": [case.target for case in cases],
        }
    )
    outputs = {
        "parameter_results.csv": metrics_frame,
        "metrics.csv": metrics_frame,
        **{f"{name}.csv": frame for name, frame in combined.items()},
        "ledger_differences.csv": pd.DataFrame(difference_rows),
        "calendar_exclusions.csv": exclusions,
        "case_index.csv": case_index,
        "date_index.csv": pd.DataFrame({"date_index": range(len(dates)), "date": dates}),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")
    np.savez_compressed(
        output_root / "daily_state.npz",
        cash=np.stack(daily_cash),
        equity=np.stack(daily_equity),
        gross_exposure=np.stack(daily_exposure),
        is_long=np.stack(daily_is_long),
    )

    max_difference = float(pd.DataFrame(difference_rows)["difference"].max())
    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "case_count": len(cases),
        "cases": json_safe(metric_rows),
        "scope_summary": json_safe(combined["scope_summary"].to_dict("records")),
        "max_cross_check_difference": max_difference,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [*source_paths.values(), SPY_PATH, interval_path]
    }
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "engine": "lib-pybroker 1.2.12 explicit compiled fills",
        "reference_engine": "quantkit.bear_atr_hard_stop.run_reference_policy using Hold/SMA paths only",
        "signal_planner": "causal completed-Close SMA hysteresis state machine",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(dates)),
        "case_count": len(cases),
        "parameters": parameters,
        "signal_timing": "oracle bear boundary or completed Close; no intraday stop",
        "execution_timing": "next common adjusted Open for every signal",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
        "max_cross_check_difference": max_difference,
        "source_files": source_files,
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
    record_block_complete(context, args.run_id, args.symbol, float(args.cost_bps), manifest_path)
    print(
        f"Completed {len(cases)} Hold/SMA fine-window cases at {args.cost_bps:g} bps; "
        f"max ledger diff={max_difference:.3g}",
        flush=True,
    )


if __name__ == "__main__":
    main()
