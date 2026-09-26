#!/usr/bin/env python3
"""Run the frozen five-path StochRSI/Bear9 drift comparison."""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data
from quantkit.execution import ExplicitFillPolicy, assert_orders_match_policy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.paths import BACKTEST_ROOT
from quantkit.qqq_flat_substitution import build_price_panel
from quantkit.reference import run_buy_and_hold_reference
from quantkit.stochrsi_bear9_drift import (
    add_realized_exposures,
    build_close_cross_state,
    build_drift_target_schedule,
)
from quantkit.trend_score_portfolio import (
    build_target_share_table,
    cross_check_portfolios,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.run_bear_event_sma_portfolios import load_candidate_frames, load_canonical
from scripts.run_qqq_flat_trio_substitution import json_safe, normalize_frame, tag_actual_orders


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison"
)
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
FORMAL_SYMBOL = "QQQ_STOCHRSI_BEAR9"
LEDGER_TOLERANCE = 1e-6


def _tag(frame: pd.DataFrame, case_id: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "case_id", case_id)
    return result


def _timing_states(
    qqq: pd.DataFrame,
    pairs: dict[str, list[int]],
    dates: pd.DatetimeIndex,
    *,
    buy_threshold: float,
    sell_threshold: float,
) -> dict[str, pd.DataFrame]:
    states: dict[str, pd.DataFrame] = {}
    for timing_id, raw_periods in pairs.items():
        periods = tuple(int(value) for value in raw_periods)
        prepared = prepare_dual_stochrsi_data(
            qqq,
            TimingSpec(
                mode="CROSS",
                periods=periods,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            ),
        )
        states[timing_id] = build_close_cross_state(
            prepared,
            periods,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            analysis_dates=dates,
        )
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
    cost_bps = float(args.cost_bps)
    if [float(value) for value in context.config["cost_scenarios_bps_per_side"]] != [5.0] or cost_bps != 5.0:
        raise ValueError("formal cost is frozen at 5 bps")
    expected_cases = list(parameters["formal_case_ids"])
    if expected_cases != [
        "STOCH_12_61_CASH",
        "STOCH_62_111_CASH",
        "STOCH_12_61_BEAR9_DRIFT3",
        "STOCH_62_111_BEAR9_DRIFT3",
    ]:
        raise AssertionError("formal case identity changed")

    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    qqq = load_canonical(QQQ_PATH, "QQQ")
    qqq = qqq[qqq["date"] <= end].sort_values("date").reset_index(drop=True)
    analysis_dates = pd.DatetimeIndex(
        qqq.loc[(qqq["date"] >= start) & (qqq["date"] <= end), "date"]
    )
    if analysis_dates.empty or analysis_dates[0] != start or analysis_dates[-1] != end:
        raise ValueError("QQQ analysis boundary does not match the frozen dates")
    bear_weights = {str(symbol): float(weight) for symbol, weight in parameters["bear9_weights"].items()}
    if not np.isclose(sum(bear_weights.values()), 1.0, rtol=0.0, atol=1e-12):
        raise AssertionError("Bear9 weights changed")
    substitutes, source_paths = load_candidate_frames(list(bear_weights))
    substitutes = {
        symbol: frame[frame["date"] <= end].sort_values("date").reset_index(drop=True)
        for symbol, frame in substitutes.items()
    }
    price_panel = build_price_panel({"QQQ": qqq, **substitutes}, analysis_dates)
    states = _timing_states(
        qqq,
        parameters["timing_pairs"],
        analysis_dates,
        buy_threshold=float(parameters["buy_threshold"]),
        sell_threshold=float(parameters["sell_threshold"]),
    )

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

    for number, case_id in enumerate(expected_cases, start=1):
        timing_id = "STOCH_12_61" if "12_61" in case_id else "STOCH_62_111"
        use_bear = "BEAR9" in case_id
        schedule = build_drift_target_schedule(
            states[timing_id],
            price_panel,
            case_id=case_id,
            bear_weights=bear_weights,
            use_bear=use_bear,
            drift_threshold=float(parameters["bear9_drift_threshold_absolute_weight"]),
            initial_cash=initial_cash,
            policy=policy,
        )
        target_shares = build_target_share_table(
            price_panel,
            schedule,
            case_id=case_id,
            initial_cash=initial_cash,
            policy=policy,
        )
        result, positions = run_pybroker_portfolio(
            price_panel, target_shares, initial_cash=initial_cash, policy=policy
        )
        reference = run_reference_portfolio(
            price_panel, target_shares, initial_cash=initial_cash, policy=policy
        )
        checked = cross_check_portfolios(
            result,
            positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        differences.update({f"{case_id}.{key}": float(value) for key, value in checked.items()})
        assert_orders_match_policy(result.orders, price_panel, policy)
        actual = add_realized_exposures(
            pybroker_daily_state(result), positions, price_panel, bear_symbols=list(bear_weights)
        )
        actual_orders = tag_actual_orders(result, price_panel)
        trades = result.trades.reset_index().copy()
        metrics = calculate_metrics(actual, actual_orders, trades, initial_cash=initial_cash)
        metric_rows.append(
            {
                "case_id": case_id,
                "timing_id": timing_id,
                "flat_asset": "BEAR9" if use_bear else "CASH",
                **metrics,
                "mean_qqq_exposure_pct": float(actual["qqq_exposure_pct"].mean()),
                "mean_bear9_exposure_pct": float(actual["bear9_exposure_pct"].mean()),
                "mean_cash_exposure_pct": float(actual["cash_exposure_pct"].mean()),
                "target_signal_count": int(schedule["date"].nunique()),
                "drift_rebalance_count": int(
                    (schedule.drop_duplicates("date")["reason"] == "drift_rebalance").sum()
                ),
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
        print(f"[{number}/{len(expected_cases)}] {case_id} reconciled")

    qqq_analysis = qqq[qqq["date"].isin(analysis_dates)][
        ["date", "symbol", "open", "high", "low", "close", "volume"]
    ].copy()
    benchmark = run_buy_and_hold_reference(qqq_analysis, policy, initial_cash=initial_cash)
    assert_orders_match_policy(benchmark.orders, qqq_analysis, policy)
    benchmark_metrics = calculate_metrics(
        benchmark.daily, benchmark.orders, benchmark.trades, initial_cash=initial_cash
    )
    metric_rows.insert(
        0,
        {
            "case_id": "QQQ_BUY_HOLD",
            "timing_id": "BENCHMARK",
            "flat_asset": "NONE",
            **benchmark_metrics,
            "mean_qqq_exposure_pct": float(benchmark.daily["is_long"].mean() * 100.0),
            "mean_bear9_exposure_pct": 0.0,
            "mean_cash_exposure_pct": float((benchmark.daily["cash"] / benchmark.daily["equity"]).mean() * 100.0),
            "target_signal_count": 1,
            "drift_rebalance_count": 0,
        },
    )
    benchmark_daily = benchmark.daily.copy()
    benchmark_daily["qqq_exposure_pct"] = benchmark_daily["is_long"].astype(float) * 100.0
    benchmark_daily["bear9_exposure_pct"] = 0.0
    benchmark_daily["cash_exposure_pct"] = benchmark_daily["cash"] / benchmark_daily["equity"] * 100.0
    daily_frames.insert(0, _tag(benchmark_daily, "QQQ_BUY_HOLD"))

    metrics_frame = pd.DataFrame(metric_rows)
    cash_metrics = metrics_frame[metrics_frame["flat_asset"] == "CASH"].set_index("timing_id")
    for index, row in metrics_frame[metrics_frame["flat_asset"] == "BEAR9"].iterrows():
        baseline = cash_metrics.loc[row["timing_id"]]
        for column in ("final_equity", "total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct", "turnover_multiple"):
            metrics_frame.loc[index, f"delta_vs_cash_{column}"] = float(row[column]) - float(baseline[column])

    outputs = {
        "metrics.csv": metrics_frame,
        "parameter_results.csv": metrics_frame,
        "daily.csv": pd.concat(daily_frames, ignore_index=True),
        "positions.csv": pd.concat(position_frames, ignore_index=True),
        "orders.csv": pd.concat(order_frames, ignore_index=True),
        "trades.csv": pd.concat(trade_frames, ignore_index=True),
        "reference_daily.csv": pd.concat(reference_daily_frames, ignore_index=True),
        "reference_orders.csv": pd.concat(reference_order_frames, ignore_index=True),
        "target_weight_schedule.csv": pd.concat(schedule_frames, ignore_index=True),
        "target_shares.csv": pd.concat(target_frames, ignore_index=True),
        "qqq_hold_daily.csv": benchmark.daily,
        "qqq_hold_orders.csv": benchmark.orders,
        "qqq_hold_trades.csv": benchmark.trades,
        "timing_state_12_61.csv": states["STOCH_12_61"],
        "timing_state_62_111.csv": states["STOCH_62_111"],
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
        "analysis_bars": int(len(analysis_dates)),
        "case_metrics": json_safe(metrics_frame.to_dict("records")),
        "max_cross_check_differences": differences,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [QQQ_PATH, *source_paths.values()]
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
        "signal_planner": "quantkit.stochrsi_bear9_drift completed-close crossings and drift schedule",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(analysis_dates)),
        "parameters": parameters,
        "signal_timing": "completed adjusted Close",
        "execution_timing": "next common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metrics_frame.to_dict("records")),
        "max_cross_check_differences": differences,
        "source_files": source_files,
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, args.symbol, cost_bps, manifest_path)
    print(f"Completed five-path comparison: {len(analysis_dates)} bars at {cost_bps:g} bps")


if __name__ == "__main__":
    main()
