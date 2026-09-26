#!/usr/bin/env python3
"""Run the frozen Nasdaq-100 P24 holding/rebalance 2x2 factorial."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import (
    assert_run_writable,
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.nasdaq100_p24_portfolio import (
    FACTORIAL_CASES,
    LEDGER_TOLERANCE,
    run_p24_factorial_portfolio,
)
from quantkit.nasdaq100_strategy1_rotation_factorial import (
    cross_check_pybroker_daily_state,
    cross_check_pybroker_order_plan,
    run_pybroker_order_plan,
)
from scripts.run_nasdaq100_p24_three_state import (
    QQQ_PATH,
    _assert_no_synthetic_fills,
    _flatten_checks,
    _load_shared,
    _prepare_formal_shared,
    json_safe,
    prepare_shared_artifacts,
    qqq_buy_hold,
    write_json,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.40d.1__26-08-29__nasdaq100_p24_hysteresis_rebalance_ablation"
)
SYMBOL_BLOCK = "NASDAQ100_P24_HYSTERESIS_REBALANCE_2005_2012"


def run_cost_block(context, run_id: str, symbol: str, cost_bps: float) -> None:
    assert_run_writable(context, run_id)
    if symbol != SYMBOL_BLOCK:
        raise ValueError("unexpected symbol block")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("unexpected cost scenario")
    shared = _prepare_formal_shared(context, run_id)
    shared_manifest = _load_shared(shared)
    if shared_manifest["signal_error_count"]:
        raise RuntimeError("shared signal preparation contains errors")
    temp = Path(
        tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=context.run_root(run_id))
    )
    state = pd.read_csv(shared / "security_state.csv.gz", parse_dates=["date"])
    panel = pd.read_csv(shared / "price_panel.csv.gz", parse_dates=["date"])
    calendar = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    initial_cash = float(context.config["initial_cash"])
    expected_ids = list(context.config["parameters"]["formal_case_ids_per_cost"])
    if expected_ids != list(FACTORIAL_CASES):
        raise AssertionError("formal case order differs from frozen implementation")

    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    checks: dict[str, dict[str, float]] = {}
    for offset, case_id in enumerate(FACTORIAL_CASES, start=1):
        result = run_p24_factorial_portfolio(
            state,
            panel,
            case_id,
            initial_cash=initial_cash,
            cost_bps=cost_bps,
        )
        synthetic_terminal_count = _assert_no_synthetic_fills(
            result.orders,
            panel,
            numerical_zero_notional=float(
                context.config["parameters"]["numerical_zero_order_notional_usd"]
            ),
        )
        broker, broker_positions = run_pybroker_order_plan(
            panel, result.orders, initial_cash=initial_cash, cost_bps=cost_bps
        )
        order_checks = cross_check_pybroker_order_plan(
            broker,
            result.orders,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(
                context.config["parameters"]["numerical_zero_order_notional_usd"]
            ),
        )
        state_checks = cross_check_pybroker_daily_state(
            broker,
            broker_positions,
            result.daily,
            result.positions,
            tolerance=LEDGER_TOLERANCE,
        )
        checks[case_id] = {
            **{f"reference_{name}": value for name, value in result.replay_checks.items()},
            **{f"pybroker_{name}": value for name, value in order_checks.items()},
            **{f"pybroker_{name}": value for name, value in state_checks.items()},
        }
        metrics = {
            **result.metrics,
            "synthetic_terminal_fill_audit_count": synthetic_terminal_count,
            "cost_bps": cost_bps,
        }
        metric_rows.append(metrics)
        for frame, collection in (
            (result.daily, daily_frames),
            (result.positions, position_frames),
            (result.orders, order_frames),
            (result.trades, trade_frames),
            (result.selections, selection_frames),
        ):
            item = frame.copy()
            item.insert(0, "case_id", case_id)
            collection.append(item)
        print(
            f"cost={cost_bps:g}bps case {offset}/4 {case_id}: "
            f"CAGR={metrics['cagr_pct']:.3f}% maxDD={metrics['max_drawdown_pct']:.3f}% "
            f"turnover={metrics['turnover_multiple']:.1f}x orders={metrics['order_count']} "
            f"held={metrics['holding_sessions']} avgNames={metrics['average_holdings_count']:.1f}",
            flush=True,
        )
        del result, broker, broker_positions
        gc.collect()

    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    qqq["date"] = pd.to_datetime(qqq["date"]).dt.normalize()
    qqq_daily, qqq_orders, qqq_metrics = qqq_buy_hold(
        qqq,
        calendar,
        initial_cash=initial_cash,
        policy=ExplicitFillPolicy("open", float(cost_bps)),
    )
    qqq_metrics.update({"case_id": "QQQ_BUY_HOLD", "cost_bps": cost_bps})
    metrics_frame = pd.DataFrame(metric_rows)
    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    selections = pd.concat(selection_frames, ignore_index=True)
    metrics_frame.to_csv(temp / "metrics.csv", index=False, lineterminator="\n")
    daily.to_csv(temp / "daily.csv.gz", index=False, compression="gzip")
    positions.to_csv(temp / "positions.csv.gz", index=False, compression="gzip")
    orders.to_csv(temp / "orders.csv.gz", index=False, compression="gzip")
    trades.to_csv(temp / "trades.csv.gz", index=False, compression="gzip")
    selections.to_csv(temp / "selections.csv.gz", index=False, compression="gzip")
    qqq_daily.to_csv(temp / "qqq_buy_hold_daily.csv", index=False, lineterminator="\n")
    qqq_orders.to_csv(temp / "qqq_buy_hold_orders.csv", index=False, lineterminator="\n")
    payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": context.config["parameters"]["analysis_start"],
        "analysis_end": context.config["parameters"]["analysis_end"],
        "data_status": "candidate_pending_review",
        "cases": metric_rows,
        "benchmark": qqq_metrics,
        "max_cross_check_differences": checks,
    }
    write_json(temp / "metrics.json", payload)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "independent P24 factorial FIFO ledger plus lib-pybroker 1.2.12 Open-event replay",
        "python": platform.python_version(),
        "analysis_start": context.config["parameters"]["analysis_start"],
        "analysis_end": context.config["parameters"]["analysis_end"],
        "analysis_bars": len(calendar),
        "signal_timing": "completed adjusted Close with one-XNYS-session lagged membership",
        "execution_timing": "scheduled next adjusted Open, sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "data_status": "candidate_pending_review",
        "parameters": context.config["parameters"],
        "case_metrics": metric_rows,
        "benchmark_metrics": qqq_metrics,
        "max_cross_check_differences": _flatten_checks(checks),
        "shared_manifest_sha256": sha256(shared / "manifest.json"),
        "artifacts": {},
    }
    for path in sorted(temp.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    write_json(temp / "manifest.json", manifest)
    output = reserve_block(context, run_id, symbol, cost_bps)
    for path in sorted(temp.iterdir()):
        path.replace(output / path.name)
    temp.rmdir()
    record_block_complete(context, run_id, symbol, cost_bps, output / "manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id")
    parser.add_argument("--symbol")
    parser.add_argument("--cost-bps", type=float)
    parser.add_argument("--prepare-only", type=Path)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.prepare_only is not None:
        print(
            json.dumps(
                json_safe(prepare_shared_artifacts(context, args.prepare_only.resolve())),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.run_id is None or args.symbol is None or args.cost_bps is None:
        parser.error("formal execution requires --run-id, --symbol and --cost-bps")
    run_cost_block(context, args.run_id, args.symbol, float(args.cost_bps))


if __name__ == "__main__":
    main()
