#!/usr/bin/env python3
"""Run one cost block of the fixed QQQ-flat trio substitution study."""

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


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution"
)
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
FORMAL_SYMBOL = "QQQ_FLAT_SUBSTITUTION"
FORMAL_CASES = ("QQQ_CASH", "QQQ_AZO237", "QQQ_TLT160", "QQQ_MO110")
SUBSTITUTE_BY_CASE = {
    "QQQ_CASH": None,
    "QQQ_AZO237": "AZO",
    "QQQ_TLT160": "TLT",
    "QQQ_MO110": "MO",
}
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


def current_asset_series(positions: pd.DataFrame, dates: pd.Series) -> pd.Series:
    held = positions[positions["shares"].astype(float) > 1e-10]
    mapping = held.set_index("date")["symbol"].astype(str).to_dict()
    return pd.to_datetime(dates).map(mapping).fillna("CASH")


def tag_actual_orders(result: Any, price_panel: pd.DataFrame) -> pd.DataFrame:
    if result.orders.empty:
        return pd.DataFrame(
            columns=["id", "date", "symbol", "type", "shares", "fill_price", "signal_date", "raw_price", "implicit_cost"]
        )
    orders = result.orders.reset_index().copy()
    dates = pd.DatetimeIndex(price_panel["date"].drop_duplicates().sort_values())
    previous_date = {dates[index]: dates[index - 1] for index in range(1, len(dates))}
    raw = price_panel.set_index(["date", "symbol"])["open"]
    orders["signal_date"] = pd.to_datetime(orders["date"]).map(previous_date)
    orders["raw_price"] = [
        float(raw.loc[(pd.Timestamp(row.date), str(row.symbol))])
        for row in orders.itertuples(index=False)
    ]
    orders["implicit_cost"] = (
        (orders["fill_price"].astype(float) - orders["raw_price"].astype(float)).abs()
        * orders["shares"].astype(float)
    )
    return orders


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if len(context.config["symbols"]) != 1 or args.symbol != context.config["symbols"][0]:
        raise ValueError(f"runner only accepts {context.config['symbols']}")
    parameters = context.config["parameters"]
    formal_cases = tuple(parameters["formal_cases"])
    substitute_windows = {
        str(symbol): int(window)
        for symbol, window in parameters["substitute_windows"].items()
    }
    expected_cases = ("QQQ_CASH",) + tuple(
        f"QQQ_{symbol}{window}" for symbol, window in substitute_windows.items()
    )
    if formal_cases != expected_cases:
        raise AssertionError(
            "formal cases must be QQQ_CASH followed by one case per frozen substitute window"
        )
    substitute_by_case = {
        "QQQ_CASH": None,
        **{
            f"QQQ_{symbol}{window}": symbol
            for symbol, window in substitute_windows.items()
        },
    }
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    cost_bps = float(args.cost_bps)
    if configured_costs != [0.0, 5.0] or cost_bps not in configured_costs:
        raise ValueError("formal costs must remain 0 and 5 bps")
    if (int(parameters["master_sma_window"]), float(parameters["entry_buffer_pct"]), float(parameters["exit_buffer_pct"])) != (200, 3.0, 3.0):
        raise AssertionError("QQQ master SMA200±3% rule changed")

    end = pd.Timestamp(parameters["analysis_end"])
    qqq_raw = load_canonical(QQQ_PATH, "QQQ")
    qqq_raw = qqq_raw[qqq_raw["date"] <= end].reset_index(drop=True)
    if qqq_raw.empty or pd.Timestamp(qqq_raw.iloc[-1]["date"]) != end:
        raise ValueError(f"QQQ does not end on frozen date {end.date()}")
    substitutes, source_paths = load_candidate_frames(list(substitute_windows))
    substitutes = {
        symbol: frame[frame["date"] <= end].sort_values("date").reset_index(drop=True)
        for symbol, frame in substitutes.items()
    }
    master = prepare_hysteresis_state(
        qqq_raw,
        "QQQ",
        HysteresisSpec(200, 3.0, 3.0, first_valid_level_entry=False),
    )
    master_ready = master[master["ready"]].reset_index(drop=True)
    start = pd.Timestamp(master_ready.iloc[0]["date"])
    if pd.Timestamp(master_ready.iloc[-1]["date"]) != end:
        raise AssertionError("master analysis end drifted")
    substitute_states = {
        symbol: prepare_hysteresis_state(
            frame,
            symbol,
            HysteresisSpec(
                substitute_windows[symbol],
                3.0,
                3.0,
                first_valid_level_entry=True,
            ),
        )
        for symbol, frame in substitutes.items()
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

    for case_id in formal_cases:
        substitute_symbol = substitute_by_case[case_id]
        decisions = build_case_decisions(
            master,
            substitute_symbol=substitute_symbol,
            substitute=None if substitute_symbol is None else substitute_states[substitute_symbol],
        )
        frames = {"QQQ": qqq_raw}
        if substitute_symbol is not None:
            frames[substitute_symbol] = substitutes[substitute_symbol]
        panel = build_price_panel(frames, master_ready["date"])
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

        metrics = calculate_metrics(
            actual,
            actual_orders,
            trades,
            initial_cash=initial_cash,
        )
        metrics["qqq_exposure_pct"] = float((actual["current_asset"] == "QQQ").mean() * 100.0)
        metrics["substitute_exposure_pct"] = float(
            (~actual["current_asset"].isin(["QQQ", "CASH"])).mean() * 100.0
        )
        metrics["cash_sessions_pct"] = float((actual["current_asset"] == "CASH").mean() * 100.0)
        metric_rows.append({"case_id": case_id, "substitute_symbol": substitute_symbol or "CASH", **metrics})
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
            spells = build_master_flat_spells(master)
            events = attribute_spell_overlays(
                spells,
                decisions,
                substitutes[substitute_symbol],
                substitute_symbol=substitute_symbol,
                policy=policy,
                dotcom_start=dotcom_start,
                dotcom_end=dotcom_end,
            )
            events.insert(0, "case_id", case_id)
            event_frames.append(events)
            summary = summarize_spell_overlays(events)
            summary.insert(0, "case_id", case_id)
            event_summary_frames.append(summary)

    qqq_analysis = master_ready[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
    benchmark = run_buy_and_hold_reference(qqq_analysis, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    metrics_frame = pd.DataFrame(metric_rows)
    metrics_frame["excess_total_return_vs_qqq_cash_pp"] = (
        metrics_frame["total_return_pct"]
        - float(metrics_frame.loc[metrics_frame["case_id"] == "QQQ_CASH", "total_return_pct"].iloc[0])
    )
    metrics_frame["excess_cagr_vs_qqq_cash_pp"] = (
        metrics_frame["cagr_pct"]
        - float(metrics_frame.loc[metrics_frame["case_id"] == "QQQ_CASH", "cagr_pct"].iloc[0])
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
    master_spells = build_master_flat_spells(master)

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
        "master_state.csv": master_ready,
        "master_flat_spells.csv": master_spells,
        "substitute_flat_event_returns.csv": events,
        "flat_spell_summary.csv": event_summary,
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
        "case_metrics": json_safe(metric_rows),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "flat_spell_summary": json_safe(event_summary.to_dict("records")),
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
        "analysis_bars": int(len(master_ready)),
        "parameters": parameters,
        "signal_timing": "completed adjusted Close",
        "execution_timing": "next QQQ common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "flat_spell_summary": json_safe(event_summary.to_dict("records")),
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
    comparison = metrics_frame[["case_id", "total_return_pct", "max_drawdown_pct", "excess_total_return_vs_qqq_cash_pp"]]
    print(f"Completed {cost_bps:g}bps QQQ-flat substitution block")
    print(comparison.to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    print(f"max ledger difference={max(differences.values()):.3g}")


if __name__ == "__main__":
    main()
