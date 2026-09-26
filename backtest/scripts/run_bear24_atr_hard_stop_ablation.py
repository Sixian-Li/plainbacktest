#!/usr/bin/env python3
"""Run 24 single assets and three fixed-sleeve groups over bear events."""

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
    POLICIES,
    BearAtrSpec,
    PolicyPath,
    aggregate_group_path,
    compiled_pybroker_path,
    cross_check_policy_paths,
    prepare_symbol_data,
    run_pybroker_compiled,
    run_reference_policy,
)
from quantkit.bear_event_sma_portfolio import calculate_interval_returns, compound_returns
from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.paths import BACKTEST_ROOT
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
    "TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation"
)
SYMBOL_BLOCK = "BEAR24_ATR_ABLATION"
EXPECTED_CORE12 = (
    "AZO", "TLT", "COR", "EXE", "DVA", "SJM", "SO", "ED", "GLD", "CHD", "HRL", "GILD"
)
EXPECTED_NEAR8 = ("HSY", "ORLY", "MO", "WRB", "EQT", "LMT", "GIS", "WEC")
EXPECTED_RETAIL4 = ("DLTR", "DG", "WMT", "TSCO")
EXPECTED_GROUPS = {
    "GROUP_CORE12": EXPECTED_CORE12,
    "GROUP_NEAR8": EXPECTED_NEAR8,
    "GROUP_RETAIL4": EXPECTED_RETAIL4,
}
EXPECTED_UNIVERSE = (*EXPECTED_CORE12, *EXPECTED_NEAR8, *EXPECTED_RETAIL4)
LEDGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    target: str
    policy_id: str
    target_type: str
    members: tuple[str, ...]


def build_case_specs(parameters: Mapping[str, Any]) -> list[CaseSpec]:
    core = tuple(str(value) for value in parameters["core12"])
    near = tuple(str(value) for value in parameters["near_core8"])
    retail = tuple(str(value) for value in parameters["retail4"])
    if (core, near, retail) != (EXPECTED_CORE12, EXPECTED_NEAR8, EXPECTED_RETAIL4):
        raise AssertionError("formal 12/8/4 lists changed")
    if len(set((*core, *near, *retail))) != 24 or "Q" in (*core, *near, *retail):
        raise AssertionError("formal universe must contain 24 unique non-Q symbols")
    groups = {
        str(name): tuple(str(symbol) for symbol in members)
        for name, members in parameters["groups"].items()
    }
    if groups != EXPECTED_GROUPS:
        raise AssertionError("formal group definitions changed")
    targets = tuple(str(target) for target in parameters["targets"])
    if targets != (*EXPECTED_UNIVERSE, *EXPECTED_GROUPS):
        raise AssertionError("formal target order changed")
    policies = tuple(str(policy) for policy in parameters["policies"])
    if policies != POLICIES:
        raise AssertionError("formal four-policy order changed")
    cases: list[CaseSpec] = []
    for target in targets:
        target_type = "group" if target in groups else "single"
        members = groups[target] if target_type == "group" else (target,)
        for policy_id in policies:
            cases.append(
                CaseSpec(
                    case_id=f"{target}__{policy_id}",
                    target=target,
                    policy_id=policy_id,
                    target_type=target_type,
                    members=members,
                )
            )
    if len(cases) != int(parameters["formal_case_count_per_cost"]) or len(cases) != 108:
        raise AssertionError("formal case count must be 108 per cost")
    return cases


def build_scope_summary(interval_returns: pd.DataFrame) -> pd.DataFrame:
    definitions = (
        ("major", interval_returns[interval_returns["severity"] == "major"]),
        ("minor", interval_returns[interval_returns["severity"] == "minor"]),
        ("all", interval_returns),
        ("all_ex_2000_2002", interval_returns[interval_returns["ordinal"].astype(int) != 1]),
    )
    rows: list[dict[str, Any]] = []
    for scope, subset in definitions:
        values = subset["total_return"].astype(float)
        rows.append(
            {
                "scope": scope,
                "interval_count": int(len(subset)),
                "positive_interval_count": int((values > 0).sum()),
                "zero_interval_count": int(np.isclose(values, 0.0, atol=1e-14, rtol=0).sum()),
                "compound_return": compound_returns(values),
                "mean_return": float(values.mean()) if len(values) else np.nan,
                "worst_interval_return": float(values.min()) if len(values) else np.nan,
                "best_interval_return": float(values.max()) if len(values) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def add_interval_drawdowns(
    interval_returns: pd.DataFrame,
    daily: pd.DataFrame,
) -> pd.DataFrame:
    equity = daily.copy()
    equity["date"] = pd.to_datetime(equity["date"])
    equity = equity.set_index("date")["equity"].astype(float)
    result = interval_returns.copy()
    drawdowns: list[float] = []
    for row in result.itertuples(index=False):
        values = equity.loc[pd.Timestamp(row.start) : pd.Timestamp(row.exit_execution_date)]
        drawdowns.append(float((values / values.cummax() - 1.0).min()))
    result["max_drawdown"] = drawdowns
    return result


def position_matrix(path: PolicyPath, dates: pd.DatetimeIndex) -> np.ndarray:
    pivot = path.positions.pivot(index="date", columns="symbol", values="shares")
    return (
        pivot.reindex(index=dates, columns=list(EXPECTED_UNIVERSE))
        .fillna(0.0)
        .to_numpy(dtype=np.float64)
    )


def tag(frame: pd.DataFrame, case: CaseSpec) -> pd.DataFrame:
    result = frame.copy()
    for column, value in reversed(
        list(
            {
                "case_id": case.case_id,
                "target": case.target,
                "target_type": case.target_type,
                "policy_id": case.policy_id,
            }.items()
        )
    ):
        if column in result:
            result[column] = value
        else:
            result.insert(0, column, value)
    return result


def concat_or_empty(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
    candidates, source_paths = load_candidate_frames(list(EXPECTED_UNIVERSE))
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    spy = load_canonical(SPY_PATH, "SPY")
    calendar, exclusions = build_common_calendar(spy, candidates, start=start, end=end)
    dates = pd.DatetimeIndex(calendar)
    interval_path = WORKSPACE_ROOT / parameters["bear_interval_source"]
    intervals = pd.DataFrame(
        json.loads(interval_path.read_text(encoding="utf-8"))["intervals"]
    )
    if len(intervals) != 12 or intervals["severity"].value_counts().to_dict() != {
        "major": 6,
        "minor": 6,
    }:
        raise ValueError("formal event source must contain six major and six minor intervals")
    if int(intervals.sort_values("ordinal").iloc[0]["ordinal"]) != 1:
        raise ValueError("the 2000-2002 bear must remain ordinal one")
    boundaries = set(pd.to_datetime(intervals["start"])).union(
        pd.to_datetime(intervals["end"])
    )
    next_dates = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    required = boundaries.union(next_dates[date] for date in boundaries)
    if required.difference(dates):
        raise ValueError("common calendar removed an event boundary or liquidation date")

    spec = BearAtrSpec(
        sma_window=int(parameters["sma_window"]),
        sma_buffer=float(parameters["sma_buffer_pct"]) / 100.0,
        atr_window=int(parameters["atr_window"]),
        atr_multiplier=float(parameters["atr_multiplier"]),
        stop_min=float(parameters["atr_stop_min_pct"]) / 100.0,
        stop_max=float(parameters["atr_stop_max_pct"]) / 100.0,
    )
    prepared = {
        symbol: prepare_symbol_data(frame, dates, spec)
        for symbol, frame in candidates.items()
    }
    fill_policy = ExplicitFillPolicy("open", float(args.cost_bps))
    reference_members: dict[str, dict[str, PolicyPath]] = {
        policy: {} for policy in POLICIES
    }
    actual_members: dict[str, dict[str, PolicyPath]] = {
        policy: {} for policy in POLICIES
    }
    differences: dict[str, dict[str, float]] = {}

    for policy_id in POLICIES:
        for symbol in EXPECTED_UNIVERSE:
            case_id = f"{symbol}__{policy_id}"
            reference = run_reference_policy(
                prepared[symbol],
                intervals,
                policy_id=policy_id,
                initial_cash=float(context.config["initial_cash"]),
                fill_policy=fill_policy,
                spec=spec,
                case_id=case_id,
            )
            compiled = run_pybroker_compiled(
                prepared[symbol], reference, initial_cash=float(context.config["initial_cash"])
            )
            actual = compiled_pybroker_path(
                compiled,
                prepared[symbol],
                reference,
                initial_cash=float(context.config["initial_cash"]),
            )
            differences[case_id] = cross_check_policy_paths(
                actual, reference, tolerance=LEDGER_TOLERANCE
            )
            reference_members[policy_id][symbol] = reference
            actual_members[policy_id][symbol] = actual
        print(f"completed 24 single-symbol paths for {policy_id} at {args.cost_bps:g} bps")

    paths: dict[str, PolicyPath] = {}
    reference_paths: dict[str, PolicyPath] = {}
    for case in cases:
        if case.target_type == "single":
            paths[case.case_id] = actual_members[case.policy_id][case.target]
            reference_paths[case.case_id] = reference_members[case.policy_id][case.target]
            continue
        actual_group = aggregate_group_path(
            actual_members[case.policy_id],
            case.members,
            group_target=case.target,
            policy_id=case.policy_id,
            initial_cash=float(context.config["initial_cash"]),
        )
        reference_group = aggregate_group_path(
            reference_members[case.policy_id],
            case.members,
            group_target=case.target,
            policy_id=case.policy_id,
            initial_cash=float(context.config["initial_cash"]),
        )
        differences[case.case_id] = cross_check_policy_paths(
            actual_group, reference_group, tolerance=LEDGER_TOLERANCE
        )
        paths[case.case_id] = actual_group
        reference_paths[case.case_id] = reference_group

    metric_rows: list[dict[str, Any]] = []
    frames: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "orders", "reference_orders", "trades", "signals", "interval_returns",
            "scope_summary", "interval_schedule",
        )
    }
    daily_cash: list[np.ndarray] = []
    daily_equity: list[np.ndarray] = []
    daily_exposure: list[np.ndarray] = []
    daily_is_long: list[np.ndarray] = []
    position_shares: list[np.ndarray] = []
    difference_rows: list[dict[str, Any]] = []

    for case in cases:
        path = paths[case.case_id]
        reference = reference_paths[case.case_id]
        interval_returns = add_interval_drawdowns(
            calculate_interval_returns(path.daily, path.interval_schedule), path.daily
        )
        scope_summary = build_scope_summary(interval_returns)
        metrics = calculate_metrics(
            path.daily,
            path.orders,
            path.trades,
            initial_cash=float(context.config["initial_cash"]),
        )
        event_mask = bear_session_mask(path.daily["date"], path.interval_schedule)
        event_daily = path.daily.loc[event_mask]
        metrics["average_bear_gross_exposure_pct"] = float(
            event_daily["gross_exposure"].mean() * 100.0
        )
        metrics["average_bear_cash_pct"] = float(
            (event_daily["cash"] / event_daily["equity"]).mean() * 100.0
        )
        metrics["hard_stop_count"] = int((path.orders["reason"] == "hard_stop").sum())
        metrics["sma_exit_count"] = int((path.orders["reason"] == "sma_exit").sum())
        metrics["reentry_count"] = int(
            path.orders["reason"].isin(
                ["price_line_reentry", "post_stop_sma_cross", "sma_entry_cross"]
            ).sum()
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
                "policy_id": case.policy_id,
                **metrics,
            }
        )
        frames["orders"].append(tag(path.orders, case))
        frames["reference_orders"].append(tag(reference.orders, case))
        frames["trades"].append(tag(path.trades, case))
        frames["signals"].append(tag(path.signals, case))
        frames["interval_returns"].append(tag(interval_returns, case))
        frames["scope_summary"].append(tag(scope_summary, case))
        frames["interval_schedule"].append(tag(path.interval_schedule, case))
        daily_cash.append(path.daily["cash"].to_numpy(dtype=np.float64))
        daily_equity.append(path.daily["equity"].to_numpy(dtype=np.float64))
        daily_exposure.append(path.daily["gross_exposure"].to_numpy(dtype=np.float64))
        daily_is_long.append(path.daily["is_long"].to_numpy(dtype=np.int8))
        position_shares.append(position_matrix(path, dates))
        difference_rows.extend(
            {
                "case_id": case.case_id,
                "target": case.target,
                "policy_id": case.policy_id,
                "measure": measure,
                "difference": value,
            }
            for measure, value in differences[case.case_id].items()
        )

    metrics_frame = pd.DataFrame(metric_rows)
    combined = {name: concat_or_empty(items) for name, items in frames.items()}
    case_index = pd.DataFrame(
        {
            "case_index": range(len(cases)),
            "case_id": [case.case_id for case in cases],
            "target": [case.target for case in cases],
            "target_type": [case.target_type for case in cases],
            "policy_id": [case.policy_id for case in cases],
            "members": [",".join(case.members) for case in cases],
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
        "symbol_index.csv": pd.DataFrame(
            {"symbol_index": range(len(EXPECTED_UNIVERSE)), "symbol": EXPECTED_UNIVERSE}
        ),
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
    np.savez_compressed(
        output_root / "position_state.npz",
        shares=np.stack(position_shares),
    )

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
        "max_cross_check_difference": float(
            max(value for case_values in differences.values() for value in case_values.values())
        ),
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [*source_paths.values(), SPY_PATH, interval_path]
    }
    flat_differences = {
        f"{case_id}.{measure}": float(value)
        for case_id, case_values in differences.items()
        for measure, value in case_values.items()
    }
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "engine": "lib-pybroker 1.2.12 explicit compiled fills",
        "reference_engine": "quantkit.bear_atr_hard_stop.run_reference_policy",
        "signal_planner": "quantkit.bear_atr_hard_stop causal event state machine",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(dates)),
        "case_count": len(cases),
        "parameters": parameters,
        "signal_timing": "oracle bear boundary or completed Close; standing stop uses known line with current Open/Low",
        "execution_timing": "next common adjusted Open for Close signals; gap Open or fixed intraday line for hard stops",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
        "max_cross_check_differences": json_safe(flat_differences),
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
    record_block_complete(
        context, args.run_id, args.symbol, float(args.cost_bps), manifest_path
    )
    group_all = combined["scope_summary"][
        (combined["scope_summary"]["target_type"] == "group")
        & (combined["scope_summary"]["scope"] == "all")
    ]
    best = group_all.loc[group_all["compound_return"].idxmax()]
    print(
        f"Completed {len(cases)} bear24 policy cases at {args.cost_bps:g} bps; "
        f"descriptive group best={best.case_id} ({best.compound_return * 100:+.2f}%); "
        f"max ledger diff={metrics_payload['max_cross_check_difference']:.3g}"
    )


if __name__ == "__main__":
    main()
