#!/usr/bin/env python3
"""Run the 2x5 star-slot and peak-drawdown bear-event ablation."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.bear_event_sma_portfolio import (
    WeightedTrailingSpec,
    build_weighted_trailing_event_plan,
    calculate_interval_returns,
    prepare_event_indicators,
    summarize_interval_returns,
)
from quantkit.execution import ExplicitFillPolicy, assert_orders_match_policy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.paths import BACKTEST_ROOT
from quantkit.trend_score_portfolio import (
    align_price_panel,
    cross_check_portfolios,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
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
    "TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation"
)
LEDGER_TOLERANCE = 1e-6
SYMBOL_BLOCK = "BEAR_EVENT_WEIGHTED_STOPS"
STOP_MAP = {
    "stop_off": None,
    "stop_06": 0.06,
    "stop_08": 0.08,
    "stop_10": 0.10,
    "stop_12": 0.12,
}


def parse_case_id(case_id: str) -> tuple[str, str, float | None]:
    parts = case_id.split("__", 1)
    if len(parts) != 2 or parts[0] not in {"star_off", "star_on"} or parts[1] not in STOP_MAP:
        raise ValueError(f"invalid formal case: {case_id}")
    return parts[0], parts[1], STOP_MAP[parts[1]]


def slot_multipliers(
    symbols: list[str],
    starred: set[str],
    star_mode: str,
) -> dict[str, float]:
    if star_mode not in {"star_off", "star_on"}:
        raise ValueError(f"invalid star mode: {star_mode}")
    return {
        symbol: 2.0 if star_mode == "star_on" and symbol in starred else 1.0
        for symbol in symbols
    }


def build_star_ablation(summary: pd.DataFrame) -> pd.DataFrame:
    pivot = summary.pivot(
        index=["cost_bps", "stop_id", "trailing_drawdown_pct", "scope"],
        columns="star_mode",
        values="compound_return",
    ).reset_index()
    if missing := {"star_off", "star_on"}.difference(pivot.columns):
        raise AssertionError(f"star ablation misses {sorted(missing)}")
    pivot["star_on_minus_off"] = pivot["star_on"] - pivot["star_off"]
    return pivot.sort_values(["cost_bps", "scope", "stop_id"]).reset_index(drop=True)


def build_stop_ablation(summary: pd.DataFrame) -> pd.DataFrame:
    baseline = summary[summary["stop_id"] == "stop_off"][
        ["cost_bps", "star_mode", "scope", "compound_return"]
    ].rename(columns={"compound_return": "stop_off_return"})
    tested = summary[summary["stop_id"] != "stop_off"].merge(
        baseline,
        on=["cost_bps", "star_mode", "scope"],
        how="left",
        validate="many_to_one",
    )
    tested["stop_minus_off"] = tested["compound_return"] - tested["stop_off_return"]
    return tested.sort_values(
        ["cost_bps", "star_mode", "scope", "trailing_drawdown_pct"]
    ).reset_index(drop=True)


def symbol_interval_contributions(
    executions: pd.DataFrame,
    interval_returns: pd.DataFrame,
    schedule: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    metadata = schedule.set_index("interval_id")
    returns = interval_returns.set_index("interval_id")
    rows: list[dict[str, Any]] = []
    for interval_id, event in metadata.iterrows():
        event_exec = executions[executions["interval_id"] == interval_id].copy()
        if event_exec.empty:
            cashflow = pd.Series(dtype=float)
        else:
            sign = np.where(event_exec["side"] == "sell", 1.0, -1.0)
            event_exec["signed_cashflow"] = (
                sign
                * event_exec["shares_filled"].astype(float)
                * event_exec["fill_price"].astype(float)
            )
            cashflow = event_exec.groupby("symbol")["signed_cashflow"].sum()
        start_equity = float(returns.loc[interval_id, "start_equity"])
        for symbol in symbols:
            pnl = float(cashflow.get(symbol, 0.0))
            rows.append(
                {
                    "ordinal": int(event["ordinal"]),
                    "interval_id": interval_id,
                    "label": event["label"],
                    "severity": event["severity"],
                    "symbol": symbol,
                    "realized_pnl": pnl,
                    "contribution_pct": pnl / start_equity * 100.0,
                }
            )
    frame = pd.DataFrame(rows)
    total_pnl = frame.groupby("interval_id")["realized_pnl"].sum()
    expected = (
        interval_returns.set_index("interval_id")["end_equity"]
        - interval_returns.set_index("interval_id")["start_equity"]
    )
    if float((total_pnl - expected).abs().max()) > 1e-6:
        raise AssertionError("symbol cashflow contributions do not reconcile to interval P&L")
    return frame


def asset_interval_returns(
    price_panel: pd.DataFrame,
    schedule: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    closes = price_panel.pivot(index="date", columns="symbol", values="close")
    opens = price_panel.pivot(index="date", columns="symbol", values="open")
    rows: list[dict[str, Any]] = []
    for event in schedule.itertuples(index=False):
        for symbol in symbols:
            start_close = closes.at[pd.Timestamp(event.start), symbol]
            end_close = closes.at[pd.Timestamp(event.end), symbol]
            entry_open = opens.at[pd.Timestamp(event.entry_execution_date), symbol]
            exit_open = opens.at[pd.Timestamp(event.exit_execution_date), symbol]
            close_return = (
                float(end_close) / float(start_close) - 1.0
                if np.isfinite(start_close) and np.isfinite(end_close)
                else np.nan
            )
            open_return = (
                float(exit_open) / float(entry_open) - 1.0
                if np.isfinite(entry_open) and np.isfinite(exit_open)
                else np.nan
            )
            rows.append(
                {
                    "ordinal": int(event.ordinal),
                    "interval_id": str(event.interval_id),
                    "label": str(event.label),
                    "severity": str(event.severity),
                    "symbol": symbol,
                    "start_close": start_close,
                    "end_close": end_close,
                    "close_to_close_return": close_return,
                    "entry_open": entry_open,
                    "exit_open": exit_open,
                    "open_to_open_return": open_return,
                }
            )
    return pd.DataFrame(rows)


def symbol_holding_stats(
    positions: pd.DataFrame,
    transitions: pd.DataFrame,
    schedule: pd.DataFrame,
    symbols: list[str],
) -> pd.DataFrame:
    event_dates: set[pd.Timestamp] = set()
    for event in schedule.itertuples(index=False):
        mask = (
            (positions["date"] >= pd.Timestamp(event.entry_execution_date))
            & (positions["date"] <= pd.Timestamp(event.exit_execution_date))
        )
        event_dates.update(pd.to_datetime(positions.loc[mask, "date"]).tolist())
    frame = positions[positions["date"].isin(event_dates)].copy()
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        own = frame[frame["symbol"] == symbol]
        own_transitions = transitions[transitions["symbol"] == symbol]
        total = int(len(own))
        held = int((own["shares"].astype(float) > 1e-12).sum())
        rows.append(
            {
                "symbol": symbol,
                "event_session_count": total,
                "held_session_count": held,
                "held_session_pct": held / total * 100.0 if total else np.nan,
                "entry_count": int(own_transitions["new_active"].astype(bool).sum()),
                "forced_exit_count": int(
                    (own_transitions["reason"] == "forced_exit_peak_drawdown").sum()
                ),
                "sma_exit_count": int(
                    (own_transitions["reason"] == "exit_below_sma_minus_buffer").sum()
                ),
            }
        )
    return pd.DataFrame(rows)


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
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if float(args.cost_bps) not in configured_costs:
        raise ValueError("cost scenario is not configured")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))
    parameters = context.config["parameters"]
    symbols = list(dict.fromkeys(str(symbol) for symbol in parameters["universe"]))
    if len(symbols) != 15 or "Q" in symbols:
        raise AssertionError("formal universe must contain 15 unique symbols and exclude Q")
    starred = set(parameters["starred_symbols"])
    if starred != {"EQT", "GIS", "LMT", "ORLY", "AZO"}:
        raise AssertionError("formal starred family changed")
    expected_cases = [
        f"{star_mode}__{stop_id}"
        for star_mode in ("star_off", "star_on")
        for stop_id in STOP_MAP
    ]
    if list(parameters["formal_cases"]) != expected_cases:
        raise AssertionError("formal case matrix is not the frozen 2x5 design")

    candidates, source_paths = load_candidate_frames(symbols)
    base_indicator_spec = WeightedTrailingSpec(
        sma_window=int(parameters["sma_window"]),
        entry_buffer=float(parameters["entry_buffer_pct"]) / 100.0,
        exit_buffer=float(parameters["exit_buffer_pct"]) / 100.0,
    )
    indicators = prepare_event_indicators(candidates, base_indicator_spec)
    interval_path = WORKSPACE_ROOT / parameters["bear_interval_source"]
    intervals = pd.DataFrame(json.loads(interval_path.read_text(encoding="utf-8"))["intervals"])
    if len(intervals) != 12 or intervals["severity"].value_counts().to_dict() != {
        "major": 6,
        "minor": 6,
    }:
        raise ValueError("formal event source must contain six major and six minor intervals")
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    spy = load_canonical(SPY_PATH, "SPY")
    calendar, calendar_exclusions = build_common_calendar(
        spy,
        candidates,
        start=start,
        end=end,
    )
    boundaries = set(pd.to_datetime(intervals["start"])).union(pd.to_datetime(intervals["end"]))
    if boundaries.difference(calendar):
        raise ValueError("common-calendar exclusions removed a formal bear boundary")
    price_panel = align_price_panel(
        candidates,
        calendar,
        analysis_start=start,
        analysis_end=end,
    )
    policy = ExplicitFillPolicy("open", float(args.cost_bps))

    metric_rows: list[dict[str, Any]] = []
    frames: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "daily",
            "positions",
            "orders",
            "trades",
            "signals",
            "transitions",
            "planner_executions",
            "target_shares",
            "interval_returns",
            "interval_summary",
            "interval_schedule",
            "symbol_interval_contributions",
            "symbol_holding_stats",
        )
    }
    differences: dict[str, float] = {}
    shared_asset_returns: pd.DataFrame | None = None

    for case_id in parameters["formal_cases"]:
        star_mode, stop_id, trailing_drawdown = parse_case_id(case_id)
        multipliers = slot_multipliers(symbols, starred, star_mode)
        spec = WeightedTrailingSpec(
            sma_window=int(parameters["sma_window"]),
            entry_buffer=float(parameters["entry_buffer_pct"]) / 100.0,
            exit_buffer=float(parameters["exit_buffer_pct"]) / 100.0,
            trailing_drawdown=trailing_drawdown,
        )
        plan = build_weighted_trailing_event_plan(
            price_panel,
            indicators,
            intervals,
            symbols=symbols,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
            spec=spec,
            slot_multipliers=multipliers,
        )
        result, positions = run_pybroker_portfolio(
            price_panel,
            plan.target_shares,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
        )
        reference = run_reference_portfolio(
            price_panel,
            plan.target_shares,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
        )
        case_differences = cross_check_portfolios(
            result,
            positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        for key, value in case_differences.items():
            differences[f"{case_id}.{key}"] = float(value)
        assert_orders_match_policy(result.orders, price_panel, policy)

        actual = pybroker_daily_state(result)
        actual_positions = positions.copy()
        orders = result.orders.reset_index().copy()
        trades = result.trades.reset_index().copy()
        interval_returns = calculate_interval_returns(actual, plan.interval_schedule)
        interval_summary = summarize_interval_returns(interval_returns)
        contributions = symbol_interval_contributions(
            plan.executions,
            interval_returns,
            plan.interval_schedule,
            symbols,
        )
        holding = symbol_holding_stats(
            actual_positions,
            plan.transitions,
            plan.interval_schedule,
            symbols,
        )
        if shared_asset_returns is None:
            shared_asset_returns = asset_interval_returns(
                price_panel,
                plan.interval_schedule,
                symbols,
            )

        common = {
            "case_id": case_id,
            "star_mode": star_mode,
            "stop_id": stop_id,
            "trailing_drawdown_pct": (
                trailing_drawdown * 100.0 if trailing_drawdown is not None else np.nan
            ),
        }

        def tag(frame: pd.DataFrame) -> pd.DataFrame:
            tagged = frame.copy()
            for column, value in reversed(list(common.items())):
                tagged.insert(0, column, value)
            return tagged

        interval_returns = tag(interval_returns)
        interval_returns.insert(0, "cost_bps", float(args.cost_bps))
        interval_summary = tag(interval_summary)
        interval_summary.insert(0, "cost_bps", float(args.cost_bps))
        contributions = tag(contributions)
        contributions.insert(0, "cost_bps", float(args.cost_bps))
        holding = tag(holding)
        holding.insert(0, "cost_bps", float(args.cost_bps))

        metrics = calculate_metrics(
            actual,
            orders,
            trades,
            initial_cash=float(context.config["initial_cash"]),
        )
        event_mask = bear_session_mask(actual["date"], plan.interval_schedule)
        event_daily = actual.loc[event_mask]
        metrics["average_bear_gross_exposure_pct"] = float(
            event_daily["gross_exposure"].mean() * 100.0
        )
        metrics["average_bear_cash_pct"] = float(
            (event_daily["cash"] / event_daily["equity"]).mean() * 100.0
        )
        metrics["forced_exit_count"] = int(
            (plan.transitions["reason"] == "forced_exit_peak_drawdown").sum()
        )
        for row in interval_summary.itertuples(index=False):
            metrics[f"{row.scope}_bear_compound_return_pct"] = float(
                row.compound_return * 100.0
            )
            metrics[f"{row.scope}_bear_positive_interval_count"] = int(
                row.positive_interval_count
            )
            metrics[f"{row.scope}_bear_worst_interval_return_pct"] = float(
                row.worst_interval_return * 100.0
            )
        metric_rows.append({**common, **metrics})

        frames["daily"].append(tag(actual))
        frames["positions"].append(tag(actual_positions))
        frames["orders"].append(tag(orders))
        frames["trades"].append(tag(trades))
        frames["signals"].append(tag(plan.signals))
        frames["transitions"].append(tag(plan.transitions))
        frames["planner_executions"].append(tag(plan.executions))
        frames["target_shares"].append(tag(plan.target_shares))
        frames["interval_returns"].append(interval_returns)
        frames["interval_summary"].append(interval_summary)
        frames["interval_schedule"].append(tag(plan.interval_schedule))
        frames["symbol_interval_contributions"].append(contributions)
        frames["symbol_holding_stats"].append(holding)

    metrics_frame = pd.DataFrame(metric_rows)
    combined = {name: pd.concat(items, ignore_index=True) for name, items in frames.items()}
    star_ablation = build_star_ablation(combined["interval_summary"])
    stop_ablation = build_stop_ablation(combined["interval_summary"])
    if shared_asset_returns is None:
        raise AssertionError("asset return diagnostics were not built")
    shared_asset_returns.insert(0, "cost_bps", float(args.cost_bps))

    outputs = {
        "parameter_results.csv": metrics_frame,
        "metrics.csv": metrics_frame,
        **{f"{name}.csv": frame for name, frame in combined.items()},
        "star_ablation.csv": star_ablation,
        "stop_ablation.csv": stop_ablation,
        "asset_interval_returns.csv": shared_asset_returns,
        "calendar_exclusions.csv": calendar_exclusions,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "cases": json_safe(metric_rows),
        "star_ablation": json_safe(star_ablation.to_dict("records")),
        "stop_ablation": json_safe(stop_ablation.to_dict("records")),
        "max_cross_check_differences": differences,
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
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.trend_score_portfolio.run_reference_portfolio",
        "signal_planner": "quantkit.bear_event_sma_portfolio.build_weighted_trailing_event_plan",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(calendar)),
        "parameters": parameters,
        "signal_timing": "oracle bear boundary or in-bear completed Close",
        "execution_timing": "next common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
        "star_ablation": json_safe(star_ablation.to_dict("records")),
        "stop_ablation": json_safe(stop_ablation.to_dict("records")),
        "max_cross_check_differences": differences,
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
        context,
        args.run_id,
        args.symbol,
        float(args.cost_bps),
        manifest_path,
    )
    all_scope = combined["interval_summary"][combined["interval_summary"]["scope"] == "all"]
    best = all_scope.loc[all_scope["compound_return"].idxmax()]
    print(
        f"Completed 10 weighted-stop cases at {args.cost_bps:g} bps; "
        f"best descriptive all-bear case={best.case_id} ({best.compound_return * 100:+.2f}%); "
        f"max ledger diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
