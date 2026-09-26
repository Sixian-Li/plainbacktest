#!/usr/bin/env python3
"""Run the frozen 2005-2012 Nasdaq-100 P24 three-state portfolios."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import (
    assert_run_writable,
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.nasdaq100_p24_portfolio import CASE_IDS, run_p24_portfolio, prepare_security_state
from quantkit.nasdaq100_stochrsi_rotation import membership_flags_for_dates, shifted_membership_bounds
from quantkit.nasdaq100_strategy1_rotation_factorial import (
    LEDGER_TOLERANCE,
    cross_check_pybroker_daily_state,
    cross_check_pybroker_order_plan,
    run_pybroker_order_plan,
)
from scripts.run_nasdaq100_stochrsi_rotation import (
    _assert_no_synthetic_fills,
    _source_member_map,
    json_safe,
    qqq_buy_hold,
    read_vendor_member,
    write_json,
)
from scripts.run_nasdaq100_strategy1_rotation_factorial import _full_dense_price_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.40c.1__26-08-29__nasdaq100_p24_three_state_2005_2012"
)
REGISTRY_PATH = WORKSPACE_ROOT / "data/nasdaq100_history_registry.json"
SECURITY_MASTER_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/security_master.csv"
)
MEMBERSHIP_INTERVALS_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/membership_intervals.csv"
)
SOURCE_MANIFEST_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/source_manifest.json"
)
CALENDAR_PATH = WORKSPACE_ROOT / "data/processed/calendars/XNYS.csv"
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
SYMBOL_BLOCK = "NASDAQ100_P24_THREE_STATE_2005_2012"


def _load_shared(shared_root: Path) -> dict[str, Any]:
    manifest_path = shared_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"shared manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, record in manifest["artifacts"].items():
        path = shared_root / name
        if not path.is_file() or sha256(path) != record["sha256"]:
            raise ValueError(f"shared artifact changed: {name}")
    return manifest


def prepare_shared_artifacts(context, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError(f"shared destination is not empty: {destination}")
    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if registry.get("review_status") != "pending_review":
        raise ValueError("Nasdaq-100 registry review status changed")
    if source_manifest.get("status") != "candidate_pending_review" or source_manifest.get("approved") is not False:
        raise ValueError("Nasdaq-100 candidate manifest status changed")
    package = registry["packages"][parameters["universe"]["source_package_key"]]
    archive_path = WORKSPACE_ROOT / package["path"]
    if sha256(archive_path) != package["sha256"]:
        raise ValueError("Nasdaq-100 archive hash changed")
    sessions = pd.DatetimeIndex(pd.read_csv(CALENDAR_PATH, parse_dates=["date"])["date"]).normalize()
    sessions = sessions.sort_values().unique()
    calendar = sessions[(sessions >= start) & (sessions <= end)]
    if len(calendar) < 2 or calendar[0] != start or calendar[-1] != end:
        raise ValueError("analysis window does not match XNYS calendar")
    master = pd.read_csv(SECURITY_MASTER_PATH, dtype=str).fillna("")
    intervals = pd.read_csv(MEMBERSHIP_INTERVALS_PATH, parse_dates=["effective_start", "effective_end"])
    bounds = shifted_membership_bounds(intervals, sessions)
    overlap = bounds[bounds["known_start"].le(end) & bounds["known_end"].ge(start)]["security_id"].astype(str)
    records = master[master["security_id"].astype(str).isin(set(overlap))].sort_values("security_id")
    if len(records) < 80:
        raise AssertionError(f"unexpectedly small point-in-time universe: {len(records)}")

    state_frames: list[pd.DataFrame] = []
    price_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    with zipfile.ZipFile(archive_path) as archive:
        member_map = _source_member_map(archive)
        for offset, item in enumerate(records.to_dict("records"), start=1):
            security_id = str(item["security_id"])
            source_file = str(item["source_file"])
            try:
                member = member_map.get(source_file)
                if member is None:
                    raise FileNotFoundError(f"archive misses {source_file}")
                raw = read_vendor_member(archive, member, security_id)
                state = prepare_security_state(raw, calendar, parameters)
                state["security_id"] = security_id
                state["display_ticker"] = str(item["display_ticker"])
                state["source_file"] = source_file
                state["in_universe"] = membership_flags_for_dates(
                    state["date"], bounds, security_id
                )
                state_frames.append(state)
                price_frames.append(_full_dense_price_frame(raw, calendar, security_id=security_id))
                diagnostic_rows.append({
                    "security_id": security_id,
                    "display_ticker": str(item["display_ticker"]),
                    "source_file": source_file,
                    "analysis_real_bars": int(state["real_bar"].sum()),
                    "in_universe_analysis_bars": int(state["in_universe"].sum()),
                    "original_target_bars": int((state["in_universe"] & state["original_target_long"]).sum()),
                    "strict_target_bars": int((state["in_universe"] & state["strict_target_long"]).sum()),
                    "difference_target_bars": int((state["in_universe"] & state["difference_target_long"]).sum()),
                })
            except Exception as exc:
                error_rows.append({
                    "security_id": security_id,
                    "display_ticker": str(item["display_ticker"]),
                    "source_file": source_file,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
            if offset % 25 == 0 or offset == len(records):
                print(
                    f"P24 preparation {offset}/{len(records)}; errors={len(error_rows)}; "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    if error_rows:
        raise RuntimeError(f"shared P24 preparation has {len(error_rows)} security errors")
    state = pd.concat(state_frames, ignore_index=True).sort_values(["date", "security_id"], kind="stable")
    panel = pd.concat(price_frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable")
    if (state["strict_target_long"] & ~state["original_target_long"]).any():
        raise AssertionError("strict target escaped original target in shared state")
    state.to_csv(destination / "security_state.csv.gz", index=False, compression="gzip")
    panel.to_csv(destination / "price_panel.csv.gz", index=False, compression="gzip")
    records[["security_id", "display_ticker", "source_file"]].to_csv(
        destination / "instrument_master.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(diagnostic_rows).to_csv(
        destination / "signal_diagnostics.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(error_rows, columns=["security_id", "display_ticker", "source_file", "error_type", "error"]).to_csv(
        destination / "signal_errors.csv", index=False, lineterminator="\n"
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in (
            REGISTRY_PATH, SECURITY_MASTER_PATH, MEMBERSHIP_INTERVALS_PATH,
            SOURCE_MANIFEST_PATH, CALENDAR_PATH, QQQ_PATH, archive_path,
        )
    }
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset_status": "candidate_pending_review",
        "dataset_approved": False,
        "source_build_id": source_manifest.get("build_id"),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_sessions": len(calendar),
        "point_in_time_security_count": len(records),
        "state_rows": len(state),
        "price_panel_rows": len(panel),
        "synthetic_bars": int(panel["synthetic_bar"].sum()),
        "prelisting_proxy_bars": int(panel["prelisting_proxy"].sum()),
        "terminal_settlement_proxy_bars": int(panel["terminal_settlement_proxy"].sum()),
        "signal_error_count": len(error_rows),
        "strict_subset_violation_count": int((state["strict_target_long"] & ~state["original_target_long"]).sum()),
        "source_files": source_files,
        "artifacts": {},
    }
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(destination / "manifest.json", manifest)
    return manifest


def _prepare_formal_shared(context, run_id: str) -> Path:
    run_root = context.run_root(run_id)
    shared = run_root / "shared"
    if shared.is_dir():
        _load_shared(shared)
        return shared
    building = Path(tempfile.mkdtemp(prefix="shared_building_", dir=run_root))
    prepare_shared_artifacts(context, building)
    building.rename(shared)
    return shared


def _flatten_checks(checks: dict[str, dict[str, float]]) -> dict[str, float]:
    return {
        f"{case_id}.{name}": float(value)
        for case_id in sorted(checks)
        for name, value in sorted(checks[case_id].items())
    }


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
    temp = Path(tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=context.run_root(run_id)))
    state = pd.read_csv(shared / "security_state.csv.gz", parse_dates=["date"])
    panel = pd.read_csv(shared / "price_panel.csv.gz", parse_dates=["date"])
    calendar = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    initial_cash = float(context.config["initial_cash"])
    expected_ids = list(context.config["parameters"]["formal_case_ids_per_cost"])
    if expected_ids != list(CASE_IDS):
        raise AssertionError("formal case order differs from frozen implementation")

    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    checks: dict[str, dict[str, float]] = {}
    for offset, case_id in enumerate(CASE_IDS, start=1):
        result = run_p24_portfolio(
            state, panel, case_id, initial_cash=initial_cash, cost_bps=cost_bps
        )
        synthetic_terminal_count = _assert_no_synthetic_fills(
            result.orders,
            panel,
            numerical_zero_notional=float(context.config["parameters"]["numerical_zero_order_notional_usd"]),
        )
        broker, broker_positions = run_pybroker_order_plan(
            panel, result.orders, initial_cash=initial_cash, cost_bps=cost_bps
        )
        order_checks = cross_check_pybroker_order_plan(
            broker,
            result.orders,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(context.config["parameters"]["numerical_zero_order_notional_usd"]),
        )
        state_checks = cross_check_pybroker_daily_state(
            broker, broker_positions, result.daily, result.positions, tolerance=LEDGER_TOLERANCE
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
            (result.daily, daily_frames), (result.positions, position_frames),
            (result.orders, order_frames), (result.trades, trade_frames),
            (result.selections, selection_frames),
        ):
            item = frame.copy()
            item.insert(0, "case_id", case_id)
            collection.append(item)
        print(
            f"cost={cost_bps:g}bps case {offset}/3 {case_id}: "
            f"CAGR={metrics['cagr_pct']:.3f}% maxDD={metrics['max_drawdown_pct']:.3f}% "
            f"held={metrics['holding_sessions']} holdingCAGR={metrics['holding_cagr_pct']:.3f}% "
            f"avgNames={metrics['average_holdings_count']:.1f}",
            flush=True,
        )
        del result, broker, broker_positions
        gc.collect()

    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    qqq["date"] = pd.to_datetime(qqq["date"]).dt.normalize()
    qqq_daily, qqq_orders, qqq_metrics = qqq_buy_hold(
        qqq, calendar, initial_cash=initial_cash, policy=ExplicitFillPolicy("open", float(cost_bps))
    )
    qqq_metrics.update({"case_id": "QQQ_BUY_HOLD", "cost_bps": cost_bps})
    metrics = pd.DataFrame(metric_rows)
    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    selections = pd.concat(selection_frames, ignore_index=True)
    metrics.to_csv(temp / "metrics.csv", index=False, lineterminator="\n")
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
        "engine": "independent daily equal-weight FIFO ledger plus lib-pybroker 1.2.12 Open-event replay",
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
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
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
        print(json.dumps(json_safe(prepare_shared_artifacts(context, args.prepare_only.resolve())), ensure_ascii=False, indent=2))
        return
    if args.run_id is None or args.symbol is None or args.cost_bps is None:
        parser.error("formal execution requires --run-id, --symbol and --cost-bps")
    run_cost_block(context, args.run_id, args.symbol, float(args.cost_bps))


if __name__ == "__main__":
    main()
