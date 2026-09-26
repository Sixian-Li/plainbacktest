#!/usr/bin/env python3
"""Run one cost block of the frozen QQQ-flat Bear24 SMA ablation."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy, assert_orders_match_policy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.paths import BACKTEST_ROOT
from quantkit.qqq_flat_substitution import (
    HysteresisSpec,
    attribute_spell_overlays,
    build_case_decisions,
    build_master_flat_spells,
    build_price_panel,
    compile_exact_target_shares,
    prepare_hysteresis_state,
    run_pybroker_substitution,
    run_reference_substitution,
    summarize_spell_overlays,
)
from quantkit.reference import run_buy_and_hold_reference
from quantkit.trend_score_portfolio import cross_check_portfolios, pybroker_daily_state
from scripts.run_bear_event_sma_portfolios import load_candidate_frames, load_canonical
from scripts.run_qqq_flat_trio_substitution import (
    current_asset_series,
    json_safe,
    normalize_frame,
    tag_actual_orders,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation"
)
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
FORMAL_SYMBOL = "QQQ_FLAT_SUBSTITUTION"
LEDGER_TOLERANCE = 1e-6


def direct_hold_state(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Represent an always-eligible substitute without inventing an SMA signal."""

    result = frame.copy().sort_values("date").drop_duplicates("date", keep="last")
    result["symbol"] = symbol
    result["upper_rail"] = np.nan
    result["lower_rail"] = np.nan
    result["ready"] = True
    result["is_long"] = True
    result["transition"] = "direct_hold"
    return result.reset_index(drop=True)


def ordered_assets(parameters: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    groups = parameters["groups"]
    assets: list[str] = []
    family_by_symbol: dict[str, str] = {}
    for family in ("core12", "near_core8", "retail4"):
        for symbol in groups[family]:
            if symbol in family_by_symbol:
                raise AssertionError(f"duplicate Bear24 symbol: {symbol}")
            family_by_symbol[symbol] = family
            assets.append(symbol)
    if [len(groups[key]) for key in ("core12", "near_core8", "retail4")] != [12, 8, 4]:
        raise AssertionError("Bear24 group sizes changed")
    return assets, family_by_symbol


def expected_cases(assets: list[str], windows: dict[str, int]) -> tuple[str, ...]:
    cases = ["QQQ_CASH"]
    for symbol in assets:
        cases.extend((f"QQQ_{symbol}_DIRECT", f"QQQ_{symbol}_SMA{windows[symbol]}"))
    return tuple(cases)


def shared_execution_calendar(
    master_dates: pd.Series,
    substitutes: dict[str, pd.DataFrame],
) -> tuple[pd.DatetimeIndex, list[dict[str, str]]]:
    """Remove only genuine post-listing holes, never pre-listing dates or filled prices."""

    dates = pd.DatetimeIndex(pd.to_datetime(master_dates)).sort_values().unique()
    exclusions: list[dict[str, str]] = []
    excluded_dates: set[pd.Timestamp] = set()
    for symbol, frame in substitutes.items():
        available = pd.DatetimeIndex(pd.to_datetime(frame["date"])).sort_values().unique()
        listed = dates[dates >= available.min()]
        missing = listed.difference(available)
        for date in missing:
            exclusions.append({"date": date.date().isoformat(), "missing_symbol": symbol})
            excluded_dates.add(pd.Timestamp(date))
    common = dates.difference(pd.DatetimeIndex(sorted(excluded_dates)))
    return common, exclusions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if context.config["symbols"] != [FORMAL_SYMBOL] or args.symbol != FORMAL_SYMBOL:
        raise ValueError(f"runner only accepts {FORMAL_SYMBOL}")
    parameters = context.config["parameters"]
    assets, family_by_symbol = ordered_assets(parameters)
    windows = {str(key): int(value) for key, value in parameters["substitute_windows"].items()}
    if list(windows) != assets:
        raise AssertionError("substitute window order must match core12 + near_core8 + retail4")
    formal_cases = tuple(parameters["formal_cases"])
    if formal_cases != expected_cases(assets, windows) or len(formal_cases) != 49:
        raise AssertionError("49 formal case definitions changed")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    cost_bps = float(args.cost_bps)
    if configured_costs != [0.0, 5.0] or cost_bps not in configured_costs:
        raise ValueError("formal costs must remain 0 and 5 bps")
    if (
        int(parameters["master_sma_window"]),
        float(parameters["entry_buffer_pct"]),
        float(parameters["exit_buffer_pct"]),
    ) != (200, 3.0, 3.0):
        raise AssertionError("QQQ master SMA200±3% rule changed")

    end = pd.Timestamp(parameters["analysis_end"])
    qqq_raw = load_canonical(QQQ_PATH, "QQQ")
    qqq_raw = qqq_raw[qqq_raw["date"] <= end].sort_values("date").reset_index(drop=True)
    if qqq_raw.empty or pd.Timestamp(qqq_raw.iloc[-1]["date"]) != end:
        raise ValueError(f"QQQ does not end on frozen date {end.date()}")
    substitutes, source_paths = load_candidate_frames(assets)
    substitutes = {
        symbol: frame[frame["date"] <= end].sort_values("date").reset_index(drop=True)
        for symbol, frame in substitutes.items()
    }
    if set(substitutes) != set(assets):
        raise ValueError("not all 24 substitute datasets are available")

    master_full = prepare_hysteresis_state(
        qqq_raw,
        "QQQ",
        HysteresisSpec(200, 3.0, 3.0, first_valid_level_entry=False),
    )
    master_ready_full = master_full[master_full["ready"]].reset_index(drop=True)
    common_dates, exclusion_rows = shared_execution_calendar(
        master_ready_full["date"], substitutes
    )
    observed_exclusions = sorted({row["date"] for row in exclusion_rows})
    if observed_exclusions != sorted(parameters["expected_common_calendar_exclusions"]):
        raise AssertionError(
            f"shared calendar exclusions drifted: {observed_exclusions}"
        )
    master = master_ready_full[master_ready_full["date"].isin(common_dates)].reset_index(drop=True)
    start = pd.Timestamp(master.iloc[0]["date"])
    if pd.Timestamp(master.iloc[-1]["date"]) != end:
        raise AssertionError("master analysis end drifted")

    sma_states = {
        symbol: prepare_hysteresis_state(
            substitutes[symbol],
            symbol,
            HysteresisSpec(windows[symbol], 3.0, 3.0, first_valid_level_entry=True),
        )
        for symbol in assets
    }
    direct_states = {
        symbol: direct_hold_state(substitutes[symbol], symbol) for symbol in assets
    }
    interval_path = WORKSPACE_ROOT / str(parameters["bear_interval_source"])
    interval_payload = json.loads(interval_path.read_text(encoding="utf-8"))
    intervals = pd.DataFrame(interval_payload["intervals"])
    if len(intervals) != 12 or intervals.iloc[0]["interval_id"] != "subjective_bear_01":
        raise ValueError("bear attribution source changed")
    dotcom_start = pd.Timestamp(intervals.iloc[0]["start"])
    dotcom_end = pd.Timestamp(intervals.iloc[0]["end"])

    output_root = reserve_block(context, args.run_id, args.symbol, cost_bps)
    policy = ExplicitFillPolicy("open", cost_bps)
    initial_cash = float(context.config["initial_cash"])
    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    reference_daily_frames: list[pd.DataFrame] = []
    reference_order_frames: list[pd.DataFrame] = []
    decision_frames: list[pd.DataFrame] = []
    target_frames: list[pd.DataFrame] = []
    execution_frames: list[pd.DataFrame] = []
    event_frames: list[pd.DataFrame] = []
    event_summary_frames: list[pd.DataFrame] = []
    differences: dict[str, float] = {}
    spells = build_master_flat_spells(master)

    case_specs: dict[str, tuple[str | None, str, int | None, str]] = {
        "QQQ_CASH": (None, "CASH", None, "baseline")
    }
    for symbol in assets:
        family = family_by_symbol[symbol]
        case_specs[f"QQQ_{symbol}_DIRECT"] = (symbol, "DIRECT", None, family)
        case_specs[f"QQQ_{symbol}_SMA{windows[symbol]}"] = (
            symbol,
            "SMA",
            windows[symbol],
            family,
        )

    for case_id in formal_cases:
        substitute_symbol, mode, sma_window, family = case_specs[case_id]
        substitute_state = None
        if substitute_symbol is not None:
            substitute_state = (
                direct_states[substitute_symbol]
                if mode == "DIRECT"
                else sma_states[substitute_symbol]
            )
        decisions = build_case_decisions(
            master,
            substitute_symbol=substitute_symbol,
            substitute=substitute_state,
        )
        frames = {"QQQ": qqq_raw}
        if substitute_symbol is not None:
            frames[substitute_symbol] = substitutes[substitute_symbol]
        panel = build_price_panel(frames, master["date"])
        plan = compile_exact_target_shares(
            panel, decisions, initial_cash=initial_cash, policy=policy
        )
        result, positions = run_pybroker_substitution(
            panel, plan.target_shares, initial_cash=initial_cash, policy=policy
        )
        reference = run_reference_substitution(
            panel, decisions, initial_cash=initial_cash, policy=policy
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
        assert_orders_match_policy(result.orders, panel, policy)

        actual = pybroker_daily_state(result)
        actual["current_asset"] = current_asset_series(positions, actual["date"])
        actual.insert(0, "case_id", case_id)
        tagged_positions = positions.copy()
        tagged_positions.insert(0, "case_id", case_id)
        actual_orders = tag_actual_orders(result, panel)
        actual_orders.insert(0, "case_id", case_id)
        trades = result.trades.reset_index().copy()
        trades.insert(0, "case_id", case_id)
        ref_daily = reference.daily.copy()
        ref_daily.insert(0, "case_id", case_id)
        ref_orders = reference.orders.copy()
        ref_orders.insert(0, "case_id", case_id)
        tagged_decisions = decisions.copy()
        tagged_decisions.insert(0, "case_id", case_id)
        targets = plan.target_shares.copy()
        targets.insert(0, "case_id", case_id)
        executions = plan.executions.copy()
        executions.insert(0, "case_id", case_id)

        metrics = calculate_metrics(actual, actual_orders, trades, initial_cash=initial_cash)
        metrics["qqq_exposure_pct"] = float((actual["current_asset"] == "QQQ").mean() * 100.0)
        metrics["substitute_exposure_pct"] = float(
            (~actual["current_asset"].isin(["QQQ", "CASH"])).mean() * 100.0
        )
        metrics["cash_sessions_pct"] = float((actual["current_asset"] == "CASH").mean() * 100.0)
        metric_rows.append(
            {
                "case_id": case_id,
                "substitute_symbol": substitute_symbol or "CASH",
                "mode": mode,
                "sma_window": sma_window,
                "family": family,
                **metrics,
            }
        )
        daily_frames.append(actual)
        position_frames.append(tagged_positions)
        order_frames.append(actual_orders)
        trade_frames.append(trades)
        reference_daily_frames.append(ref_daily)
        reference_order_frames.append(ref_orders)
        decision_frames.append(tagged_decisions)
        target_frames.append(targets)
        execution_frames.append(executions)

        if substitute_symbol is not None:
            events = attribute_spell_overlays(
                spells,
                decisions,
                substitutes[substitute_symbol],
                substitute_symbol=substitute_symbol,
                policy=policy,
                dotcom_start=dotcom_start,
                dotcom_end=dotcom_end,
            )
            events.insert(0, "family", family)
            events.insert(0, "sma_window", sma_window)
            events.insert(0, "mode", mode)
            events.insert(0, "case_id", case_id)
            event_frames.append(events)
            summary = summarize_spell_overlays(events)
            summary.insert(0, "family", family)
            summary.insert(0, "sma_window", sma_window)
            summary.insert(0, "mode", mode)
            summary.insert(0, "case_id", case_id)
            event_summary_frames.append(summary)

    qqq_analysis = master[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
    benchmark = run_buy_and_hold_reference(qqq_analysis, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    metrics_frame = pd.DataFrame(metric_rows)
    cash_return = float(
        metrics_frame.loc[metrics_frame["case_id"] == "QQQ_CASH", "total_return_pct"].iloc[0]
    )
    cash_cagr = float(
        metrics_frame.loc[metrics_frame["case_id"] == "QQQ_CASH", "cagr_pct"].iloc[0]
    )
    metrics_frame["excess_total_return_vs_qqq_cash_pp"] = metrics_frame["total_return_pct"] - cash_return
    metrics_frame["excess_cagr_vs_qqq_cash_pp"] = metrics_frame["cagr_pct"] - cash_cagr
    direct_by_symbol = metrics_frame[metrics_frame["mode"] == "DIRECT"].set_index("substitute_symbol")
    for index, row in metrics_frame[metrics_frame["mode"] == "SMA"].iterrows():
        direct = direct_by_symbol.loc[row["substitute_symbol"]]
        metrics_frame.loc[index, "sma_minus_direct_total_return_pp"] = (
            float(row["total_return_pct"]) - float(direct["total_return_pct"])
        )
        metrics_frame.loc[index, "sma_minus_direct_cagr_pp"] = (
            float(row["cagr_pct"]) - float(direct["cagr_pct"])
        )
        metrics_frame.loc[index, "max_drawdown_improvement_vs_direct_pp"] = (
            float(row["max_drawdown_pct"]) - float(direct["max_drawdown_pct"])
        )

    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    reference_daily = pd.concat(reference_daily_frames, ignore_index=True)
    reference_orders = pd.concat(reference_order_frames, ignore_index=True)
    decisions = pd.concat(decision_frames, ignore_index=True)
    targets = pd.concat(target_frames, ignore_index=True)
    executions = pd.concat(execution_frames, ignore_index=True)
    events = pd.concat(event_frames, ignore_index=True)
    event_summary = pd.concat(event_summary_frames, ignore_index=True)
    exclusions = pd.DataFrame(exclusion_rows, columns=["date", "missing_symbol"])

    outputs = {
        "parameter_results.csv": metrics_frame,
        "metrics.csv": metrics_frame,
        "daily.csv": daily,
        "positions.csv": positions,
        "orders.csv": orders,
        "trades.csv": trades,
        "reference_daily.csv": reference_daily,
        "reference_orders.csv": reference_orders,
        "decisions.csv": decisions,
        "target_shares.csv": targets,
        "planner_executions.csv": executions,
        "master_state.csv": master,
        "master_flat_spells.csv": spells,
        "substitute_flat_event_returns.csv": events,
        "flat_spell_summary.csv": event_summary,
        "common_calendar_exclusions.csv": exclusions,
        "qqq_hold_daily.csv": benchmark.daily,
        "qqq_hold_orders.csv": benchmark.orders,
        "qqq_hold_trades.csv": benchmark.trades,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "cost_bps": cost_bps,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(master)),
        "case_metrics": json_safe(metric_rows),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "flat_spell_summary": json_safe(event_summary.to_dict("records")),
        "common_calendar_exclusions": exclusion_rows,
        "max_cross_check_differences": differences,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [QQQ_PATH, *source_paths.values(), interval_path]
    }
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": args.symbol,
        "cost_bps": cost_bps,
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.qqq_flat_substitution.run_reference_substitution",
        "signal_planner": "quantkit.qqq_flat_substitution close-confirmed hysteresis state machine",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(master)),
        "parameters": parameters,
        "signal_timing": "completed adjusted Close",
        "execution_timing": "next all-24-common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "flat_spell_summary": json_safe(event_summary.to_dict("records")),
        "common_calendar_exclusions": exclusion_rows,
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
    record_block_complete(context, args.run_id, args.symbol, cost_bps, manifest_path)
    comparison = metrics_frame[
        ["case_id", "total_return_pct", "max_drawdown_pct", "excess_total_return_vs_qqq_cash_pp"]
    ]
    print(f"Completed {cost_bps:g}bps Bear24 DIRECT/SMA block ({len(master)} shared bars)")
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    print(f"max ledger difference={max(differences.values()):.3g}")


if __name__ == "__main__":
    main()
