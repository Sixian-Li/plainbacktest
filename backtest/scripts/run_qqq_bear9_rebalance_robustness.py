#!/usr/bin/env python3
"""Run one formal cost block for QQQ timing plus Bear9 rebalancing."""

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
from quantkit.qqq_bear9_rebalance import (
    add_realized_exposures,
    build_target_weight_schedule,
    case_definitions,
    prepare_layered_timing,
    prepare_symmetric_timing,
)
from quantkit.qqq_flat_substitution import build_price_panel
from quantkit.reference import run_buy_and_hold_reference
from quantkit.trend_score_portfolio import (
    build_target_share_table,
    cross_check_portfolios,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.run_bear_event_sma_portfolios import load_candidate_frames, load_canonical
from scripts.run_qqq_flat_bear24_sma_ablation import shared_execution_calendar
from scripts.run_qqq_flat_trio_substitution import json_safe, normalize_frame, tag_actual_orders


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness"
)
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
FORMAL_SYMBOL = "QQQ_BEAR9_REBALANCE"
LEDGER_TOLERANCE = 1e-6


def _tag(frame: pd.DataFrame, case_id: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    return result


def _period_metrics(
    daily: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    start: pd.Timestamp,
) -> dict[str, Any]:
    subset = daily[pd.to_datetime(daily["date"]) >= start].copy()
    if len(subset) < 2:
        raise ValueError("post-dotcom metric window is too short")
    order_subset = orders[pd.to_datetime(orders["date"]) >= start].copy()
    trade_subset = trades.copy()
    if not trade_subset.empty:
        trade_subset = trade_subset[pd.to_datetime(trade_subset["exit_date"]) >= start]
    return calculate_metrics(
        subset,
        order_subset,
        trade_subset,
        initial_cash=float(subset.iloc[0]["equity"]),
    )


def _timing_states(
    qqq: pd.DataFrame,
    parameters: dict[str, Any],
    common_dates: pd.DatetimeIndex,
) -> dict[str, pd.DataFrame]:
    states: dict[str, pd.DataFrame] = {}
    symmetric = parameters["symmetric_timing"]
    for raw_window in symmetric["sma_windows"]:
        window = int(raw_window)
        state = prepare_symmetric_timing(
            qqq,
            window=window,
            buffer_pct=float(symmetric["entry_buffer_pct"]),
        )
        state = state[state["ready"] & state["date"].isin(common_dates)].copy()
        states[f"SMA{window}"] = state.reset_index(drop=True)
    layered = parameters["layered_timing"]
    state = prepare_layered_timing(
        qqq,
        fast_window=int(layered["fast_sma_window"]),
        slow_window=int(layered["slow_sma_window"]),
    )
    states["LAYERED_190_310"] = state[
        state["ready"] & state["date"].isin(common_dates)
    ].reset_index(drop=True)
    if list(states) != list(parameters["timing_ids"]):
        raise AssertionError("timing engine order changed")
    expected_dates = list(common_dates)
    for timing_id, state in states.items():
        if state["date"].tolist() != expected_dates:
            raise AssertionError(f"{timing_id} does not cover the common evaluation calendar")
    return states


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
    cases = case_definitions(parameters)
    if len(cases) != 97 or int(parameters["formal_path_count"]) != 194:
        raise AssertionError("formal path count changed")
    cost_bps = float(args.cost_bps)
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if configured_costs != [0.0, 5.0] or cost_bps not in configured_costs:
        raise ValueError("formal costs must remain 0 and 5 bps")

    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    qqq = load_canonical(QQQ_PATH, "QQQ")
    qqq = qqq[qqq["date"] <= end].sort_values("date").reset_index(drop=True)
    if qqq.empty or pd.Timestamp(qqq.iloc[-1]["date"]) != end:
        raise ValueError(f"QQQ does not end on frozen date {end.date()}")
    bear_weights = {str(key): float(value) for key, value in parameters["bear9_weights"].items()}
    if not np.isclose(sum(bear_weights.values()), 1.0, atol=1e-12):
        raise AssertionError("Bear9 weights changed")
    substitutes, source_paths = load_candidate_frames(list(bear_weights))
    substitutes = {
        symbol: frame[frame["date"] <= end].sort_values("date").reset_index(drop=True)
        for symbol, frame in substitutes.items()
    }
    candidate_dates = qqq.loc[(qqq["date"] >= start) & (qqq["date"] <= end), "date"]
    common_dates, exclusions = shared_execution_calendar(candidate_dates, substitutes)
    if exclusions:
        raise AssertionError(f"unexpected post-listing Bear9 calendar holes: {exclusions}")
    if common_dates[0] != start or common_dates[-1] != end:
        raise AssertionError("common evaluation boundary drifted")
    price_panel = build_price_panel({"QQQ": qqq, **substitutes}, common_dates)
    states = _timing_states(qqq, parameters, common_dates)

    interval_path = WORKSPACE_ROOT / str(parameters["bear_interval_source"])
    interval_payload = json.loads(interval_path.read_text(encoding="utf-8"))
    intervals = pd.DataFrame(interval_payload["intervals"])
    if len(intervals) != 12 or intervals.iloc[0]["interval_id"] != "subjective_bear_01":
        raise ValueError("bear attribution source changed")
    first_bear_end = pd.Timestamp(intervals.iloc[0]["end"])
    post_dotcom_candidates = common_dates[common_dates > first_bear_end]
    if len(post_dotcom_candidates) < 2:
        raise ValueError("post-dotcom evaluation window is unavailable")
    post_dotcom_start = pd.Timestamp(post_dotcom_candidates[0])

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
    schedule_frames: list[pd.DataFrame] = []
    target_frames: list[pd.DataFrame] = []
    differences: dict[str, float] = {}

    for number, case in enumerate(cases, start=1):
        case_id = str(case["case_id"])
        timing_id = str(case["timing_id"])
        schedule = build_target_weight_schedule(
            states[timing_id],
            price_panel,
            case_id=case_id,
            bear_weights=bear_weights,
            bear_mode=str(case["bear_mode"]),
            rebalance_days=case["rebalance_days"],
        )
        target_shares = build_target_share_table(
            price_panel,
            schedule,
            case_id=case_id,
            initial_cash=initial_cash,
            policy=policy,
        )
        result, positions = run_pybroker_portfolio(
            price_panel,
            target_shares,
            initial_cash=initial_cash,
            policy=policy,
        )
        reference = run_reference_portfolio(
            price_panel,
            target_shares,
            initial_cash=initial_cash,
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

        actual = add_realized_exposures(
            pybroker_daily_state(result),
            positions,
            price_panel,
            bear_symbols=list(bear_weights),
        )
        actual_orders = tag_actual_orders(result, price_panel)
        trades = result.trades.reset_index().copy()
        full_metrics = calculate_metrics(
            actual, actual_orders, trades, initial_cash=initial_cash
        )
        post_metrics = _period_metrics(
            actual, actual_orders, trades, start=post_dotcom_start
        )
        metric_rows.append(
            {
                "case_id": case_id,
                "timing_id": timing_id,
                "bear_mode": str(case["bear_mode"]),
                "rebalance_days": case["rebalance_days"],
                **full_metrics,
                **{f"post_dotcom_{key}": value for key, value in post_metrics.items()},
                "mean_qqq_exposure_pct": float(actual["qqq_exposure_pct"].mean()),
                "mean_bear9_exposure_pct": float(actual["bear9_exposure_pct"].mean()),
                "mean_cash_exposure_pct": float(actual["cash_exposure_pct"].mean()),
                "periodic_rebalance_signal_count": int(
                    (schedule.drop_duplicates("date")["reason"] == "periodic_rebalance").sum()
                ),
                "target_signal_count": int(schedule["date"].nunique()),
            }
        )
        daily_frames.append(_tag(actual, case_id))
        position_frames.append(_tag(positions, case_id))
        order_frames.append(_tag(actual_orders, case_id))
        trade_frames.append(_tag(trades, case_id))
        reference_daily_frames.append(_tag(reference.daily, case_id))
        reference_order_frames.append(_tag(reference.orders, case_id))
        schedule_frames.append(schedule)
        target_frames.append(_tag(target_shares, case_id))
        print(f"[{number:02d}/{len(cases)}] {case_id} reconciled")

    metrics = pd.DataFrame(metric_rows)
    cash_lookup = (
        metrics[metrics["bear_mode"] == "CASH"]
        .set_index("timing_id")
        [["total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct",
          "post_dotcom_total_return_pct", "post_dotcom_cagr_pct", "post_dotcom_sharpe",
          "post_dotcom_max_drawdown_pct"]]
    )
    for index, row in metrics.iterrows():
        baseline = cash_lookup.loc[row["timing_id"]]
        for column in (
            "total_return_pct",
            "cagr_pct",
            "sharpe",
            "max_drawdown_pct",
            "post_dotcom_total_return_pct",
            "post_dotcom_cagr_pct",
            "post_dotcom_sharpe",
            "post_dotcom_max_drawdown_pct",
        ):
            metrics.loc[index, f"delta_vs_cash_{column}"] = float(row[column]) - float(
                baseline[column]
            )

    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    reference_daily = pd.concat(reference_daily_frames, ignore_index=True)
    reference_orders = pd.concat(reference_order_frames, ignore_index=True)
    schedules = pd.concat(schedule_frames, ignore_index=True)
    target_shares = pd.concat(target_frames, ignore_index=True)

    qqq_analysis = qqq[qqq["date"].isin(common_dates)][
        ["date", "symbol", "open", "high", "low", "close", "volume"]
    ].copy()
    benchmark = run_buy_and_hold_reference(qqq_analysis, policy, initial_cash=initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    benchmark_post = _period_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, start=post_dotcom_start
    )

    outputs = {
        "parameter_results.csv": metrics,
        "metrics.csv": metrics,
        "daily.csv": daily,
        "positions.csv": positions,
        "orders.csv": orders,
        "trades.csv": trades,
        "reference_daily.csv": reference_daily,
        "reference_orders.csv": reference_orders,
        "target_weight_schedule.csv": schedules,
        "target_shares.csv": target_shares,
        "qqq_hold_daily.csv": benchmark.daily,
        "qqq_hold_orders.csv": benchmark.orders,
        "qqq_hold_trades.csv": benchmark.trades,
        "timing_state_sma160.csv": states["SMA160"],
        "timing_state_sma180.csv": states["SMA180"],
        "timing_state_sma200.csv": states["SMA200"],
        "timing_state_sma220.csv": states["SMA220"],
        "timing_state_sma240.csv": states["SMA240"],
        "timing_state_layered_190_310.csv": states["LAYERED_190_310"],
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
        "analysis_bars": int(len(common_dates)),
        "post_dotcom_start": post_dotcom_start.date().isoformat(),
        "case_metrics": json_safe(metrics.to_dict("records")),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "qqq_hold_post_dotcom_metrics": json_safe(benchmark_post),
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
        "reference_engine": "quantkit.trend_score_portfolio.run_reference_portfolio",
        "signal_planner": "quantkit.qqq_bear9_rebalance close-confirmed target schedule",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(common_dates)),
        "post_dotcom_start": post_dotcom_start.date().isoformat(),
        "parameters": parameters,
        "signal_timing": "completed adjusted Close",
        "execution_timing": "next QQQ common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metrics.to_dict("records")),
        "qqq_hold_metrics": json_safe(benchmark_metrics),
        "qqq_hold_post_dotcom_metrics": json_safe(benchmark_post),
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
    print(
        f"Completed {cost_bps:g}bps QQQ/Bear9 robustness block: "
        f"{len(cases)} cases, {len(common_dates)} bars"
    )


if __name__ == "__main__":
    main()
