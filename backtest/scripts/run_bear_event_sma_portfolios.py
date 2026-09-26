#!/usr/bin/env python3
"""Run three bear-event universes with no filter and SMA200 hysteresis."""

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
    EventSmaSpec,
    build_event_plan,
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


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios"
)
SP500_PRICE_DIR = WORKSPACE_ROOT / "data/processed/daily/equities"
TLT_PATH = WORKSPACE_ROOT / (
    "data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/国债系列 ETF/"
    "TLT - 美国 20 年以上国债 ETF iShares/TLT_1day_拆股股息调整_20260805.csv"
)
GLD_PATH = WORKSPACE_ROOT / (
    "data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/核心商品 ETF/"
    "GLD - 黄金 ETF SPDR/GLD_1day_拆股股息调整_20260805.csv"
)
SPY_PATH = WORKSPACE_ROOT / "data/processed/daily/SPY.csv"
LEDGER_TOLERANCE = 1e-6


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = result[column].dt.strftime("%Y-%m-%d")
    return result


def load_canonical(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    if "symbol" in frame:
        frame = frame[frame["symbol"] == symbol].copy()
    frame["symbol"] = symbol
    return frame.sort_values("date").reset_index(drop=True)


def load_vendor(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame = frame.rename(columns={column: column.lower() for column in frame.columns})
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["symbol"] = symbol
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values(
        "date"
    ).reset_index(drop=True)


def load_candidate_frames(
    symbols: list[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, Path]]:
    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, Path] = {}
    for symbol in symbols:
        if symbol == "TLT":
            path = TLT_PATH
            frame = load_vendor(path, symbol)
        elif symbol == "GLD":
            path = GLD_PATH
            frame = load_vendor(path, symbol)
        else:
            path = SP500_PRICE_DIR / f"{symbol}.csv"
            frame = load_canonical(path, symbol)
        if not path.is_file():
            raise FileNotFoundError(path)
        frames[symbol] = frame
        sources[symbol] = path
    return frames, sources


def parse_case_id(case_id: str) -> tuple[str, str]:
    parts = case_id.split("__", 1)
    if len(parts) != 2:
        raise ValueError(f"invalid case id: {case_id}")
    return parts[0], parts[1]


def build_common_calendar(
    spy: pd.DataFrame,
    candidate_frames: dict[str, pd.DataFrame],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    base = pd.DatetimeIndex(
        spy.loc[(spy["date"] >= start) & (spy["date"] <= end), "date"]
    ).sort_values()
    if base.empty or base[0] != start or base[-1] != end:
        raise ValueError("SPY calendar does not exactly cover configured analysis bounds")
    excluded_rows: list[dict[str, Any]] = []
    excluded_dates: set[pd.Timestamp] = set()
    for symbol, frame in candidate_frames.items():
        available = pd.DatetimeIndex(
            frame.loc[(frame["date"] >= start) & (frame["date"] <= end), "date"]
        ).sort_values()
        if available.empty:
            continue
        expected_after_listing = base[base >= available[0]]
        missing = expected_after_listing.difference(available)
        for date in missing:
            excluded_dates.add(pd.Timestamp(date))
            excluded_rows.append(
                {
                    "date": pd.Timestamp(date),
                    "symbol": symbol,
                    "reason": "post_listing_missing_ohlc_removed_from_common_calendar",
                }
            )
    calendar = base[~base.isin(sorted(excluded_dates))]
    exclusions = pd.DataFrame(
        excluded_rows,
        columns=["date", "symbol", "reason"],
    ).sort_values(["date", "symbol"]).reset_index(drop=True)
    return calendar, exclusions


def bear_session_mask(dates: pd.Series, schedule: pd.DataFrame) -> pd.Series:
    values = pd.to_datetime(dates)
    mask = pd.Series(False, index=dates.index)
    for row in schedule.itertuples(index=False):
        mask |= (values >= pd.Timestamp(row.start)) & (
            values <= pd.Timestamp(row.exit_execution_date)
        )
    return mask


def build_filter_comparison(summary: pd.DataFrame) -> pd.DataFrame:
    index_columns = ["cost_bps", "universe_id", "scope"]
    pivot = summary.pivot(
        index=index_columns,
        columns="filter_mode",
        values="compound_return",
    ).reset_index()
    required = {"no_filter", "sma200_hysteresis"}
    if missing := required.difference(pivot.columns):
        raise AssertionError(f"summary comparison misses {sorted(missing)}")
    pivot["improvement"] = pivot["sma200_hysteresis"] - pivot["no_filter"]
    return pivot.sort_values(["universe_id", "scope"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != "BEAR_EVENT_PORTFOLIOS" or args.symbol not in context.config["symbols"]:
        raise ValueError("runner only accepts the BEAR_EVENT_PORTFOLIOS block")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if float(args.cost_bps) not in configured_costs:
        raise ValueError("cost scenario is not configured")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    parameters = context.config["parameters"]
    all_symbols = sorted(
        {
            symbol
            for universe in parameters["universes"].values()
            for symbol in universe
        }
    )
    if "Q" in all_symbols:
        raise AssertionError("Q must not enter the event portfolio universe")
    candidates, source_paths = load_candidate_frames(all_symbols)
    indicators = prepare_event_indicators(
        candidates,
        EventSmaSpec(
            sma_window=int(parameters["sma_window"]),
            entry_buffer=float(parameters["mid_bear_entry_buffer_pct"]) / 100.0,
            exit_buffer=float(parameters["mid_bear_exit_buffer_pct"]) / 100.0,
            lock_band=float(parameters["state_change_lock_band_pct"]) / 100.0,
        ),
    )
    spec = EventSmaSpec(
        sma_window=int(parameters["sma_window"]),
        entry_buffer=float(parameters["mid_bear_entry_buffer_pct"]) / 100.0,
        exit_buffer=float(parameters["mid_bear_exit_buffer_pct"]) / 100.0,
        lock_band=float(parameters["state_change_lock_band_pct"]) / 100.0,
    )
    interval_path = WORKSPACE_ROOT / parameters["bear_interval_source"]
    interval_payload = json.loads(interval_path.read_text(encoding="utf-8"))
    intervals = pd.DataFrame(interval_payload["intervals"])
    if len(intervals) != 12 or intervals["severity"].value_counts().to_dict() != {
        "major": 6,
        "minor": 6,
    }:
        raise ValueError("formal event source must contain exactly six major and six minor intervals")

    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    spy = load_canonical(SPY_PATH, "SPY")
    calendar, calendar_exclusions = build_common_calendar(
        spy,
        candidates,
        start=start,
        end=end,
    )
    boundaries = set(pd.to_datetime(intervals["start"])).union(
        pd.to_datetime(intervals["end"])
    )
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
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    signal_frames: list[pd.DataFrame] = []
    transition_frames: list[pd.DataFrame] = []
    planner_execution_frames: list[pd.DataFrame] = []
    target_frames: list[pd.DataFrame] = []
    return_frames: list[pd.DataFrame] = []
    summary_frames: list[pd.DataFrame] = []
    schedule_frames: list[pd.DataFrame] = []
    differences: dict[str, float] = {}

    for case_id in parameters["formal_cases"]:
        universe_id, filter_mode = parse_case_id(case_id)
        symbols = list(parameters["universes"][universe_id])
        case_panel = price_panel[price_panel["symbol"].isin(symbols)].copy()
        case_indicators = indicators[indicators["symbol"].isin(symbols)].copy()
        plan = build_event_plan(
            case_panel,
            case_indicators,
            intervals,
            symbols=symbols,
            mode=filter_mode,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
            spec=spec,
        )
        result, positions = run_pybroker_portfolio(
            case_panel,
            plan.target_shares,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
        )
        reference = run_reference_portfolio(
            case_panel,
            plan.target_shares,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
        )
        case_differences = cross_check_portfolios(
            result,
            positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(
                parameters["numerical_zero_order_notional_usd"]
            ),
        )
        for key, value in case_differences.items():
            differences[f"{case_id}.{key}"] = float(value)
        assert_orders_match_policy(result.orders, case_panel, policy)

        actual = pybroker_daily_state(result)
        actual.insert(0, "filter_mode", filter_mode)
        actual.insert(0, "universe_id", universe_id)
        actual.insert(0, "case_id", case_id)
        actual_positions = positions.copy()
        actual_positions.insert(0, "filter_mode", filter_mode)
        actual_positions.insert(0, "universe_id", universe_id)
        actual_positions.insert(0, "case_id", case_id)
        orders = result.orders.reset_index().copy()
        orders.insert(0, "filter_mode", filter_mode)
        orders.insert(0, "universe_id", universe_id)
        orders.insert(0, "case_id", case_id)
        trades = result.trades.reset_index().copy()
        trades.insert(0, "filter_mode", filter_mode)
        trades.insert(0, "universe_id", universe_id)
        trades.insert(0, "case_id", case_id)

        interval_returns = calculate_interval_returns(actual, plan.interval_schedule)
        interval_returns.insert(0, "filter_mode", filter_mode)
        interval_returns.insert(0, "universe_id", universe_id)
        interval_returns.insert(0, "case_id", case_id)
        interval_returns.insert(0, "cost_bps", float(args.cost_bps))
        interval_summary = summarize_interval_returns(interval_returns)
        interval_summary.insert(0, "filter_mode", filter_mode)
        interval_summary.insert(0, "universe_id", universe_id)
        interval_summary.insert(0, "case_id", case_id)
        interval_summary.insert(0, "cost_bps", float(args.cost_bps))

        metrics = calculate_metrics(
            actual,
            orders,
            trades,
            initial_cash=float(context.config["initial_cash"]),
        )
        event_mask = bear_session_mask(actual["date"], plan.interval_schedule)
        event_daily = actual.loc[event_mask]
        metrics["average_gross_exposure_pct"] = float(
            actual["gross_exposure"].mean() * 100.0
        )
        metrics["average_bear_gross_exposure_pct"] = float(
            event_daily["gross_exposure"].mean() * 100.0
        )
        metrics["average_bear_cash_pct"] = float(
            (event_daily["cash"] / event_daily["equity"]).mean() * 100.0
        )
        for row in interval_summary.itertuples(index=False):
            metrics[f"{row.scope}_bear_compound_return_pct"] = float(
                row.compound_return * 100.0
            )
            metrics[f"{row.scope}_bear_positive_interval_count"] = int(
                row.positive_interval_count
            )
        metric_rows.append(
            {
                "case_id": case_id,
                "universe_id": universe_id,
                "filter_mode": filter_mode,
                **metrics,
            }
        )

        def tag(frame: pd.DataFrame) -> pd.DataFrame:
            tagged = frame.copy()
            tagged.insert(0, "filter_mode", filter_mode)
            tagged.insert(0, "universe_id", universe_id)
            tagged.insert(0, "case_id", case_id)
            return tagged

        daily_frames.append(actual)
        position_frames.append(actual_positions)
        order_frames.append(orders)
        trade_frames.append(trades)
        signal_frames.append(tag(plan.signals))
        transition_frames.append(tag(plan.transitions))
        planner_execution_frames.append(tag(plan.executions))
        target_frames.append(tag(plan.target_shares))
        return_frames.append(interval_returns)
        summary_frames.append(interval_summary)
        schedule_frames.append(tag(plan.interval_schedule))

    metrics_frame = pd.DataFrame(metric_rows)
    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    signals = pd.concat(signal_frames, ignore_index=True)
    transitions = pd.concat(transition_frames, ignore_index=True)
    planner_executions = pd.concat(planner_execution_frames, ignore_index=True)
    targets = pd.concat(target_frames, ignore_index=True)
    interval_returns = pd.concat(return_frames, ignore_index=True)
    interval_summary = pd.concat(summary_frames, ignore_index=True)
    schedules = pd.concat(schedule_frames, ignore_index=True)
    comparison = build_filter_comparison(interval_summary)

    outputs = {
        "parameter_results.csv": metrics_frame,
        "metrics.csv": metrics_frame,
        "daily.csv": daily,
        "positions.csv": positions,
        "orders.csv": orders,
        "trades.csv": trades,
        "signals.csv": signals,
        "transitions.csv": transitions,
        "planner_executions.csv": planner_executions,
        "target_shares.csv": targets,
        "interval_returns.csv": interval_returns,
        "interval_summary.csv": interval_summary,
        "filter_comparison.csv": comparison,
        "interval_schedule.csv": schedules,
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
        "filter_comparison": json_safe(comparison.to_dict("records")),
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
        "signal_planner": "quantkit.bear_event_sma_portfolio.build_event_plan",
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
        "filter_comparison": json_safe(comparison.to_dict("records")),
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
    all_scope = comparison[comparison["scope"] == "all"]
    improvements = ", ".join(
        f"{row.universe_id}={row.improvement * 100:+.2f}pp"
        for row in all_scope.itertuples(index=False)
    )
    print(
        f"Completed 6 event cases at {args.cost_bps:g} bps; all-bear SMA improvements: "
        f"{improvements}; max ledger diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
