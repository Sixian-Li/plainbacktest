#!/usr/bin/env python3
"""Run the frozen 2005-2010 Nasdaq-100 Strategy1 portfolio factorial."""

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
from quantkit.nasdaq100_stochrsi_rotation import (
    membership_flags_for_dates,
    prepare_strategy1_score_inputs,
    run_strategy1_score,
    shifted_membership_bounds,
)
from quantkit.nasdaq100_strategy1_rotation_factorial import (
    LEDGER_TOLERANCE,
    RotationFactorSpec,
    cross_check_pybroker_daily_state,
    cross_check_pybroker_order_plan,
    factor_specs,
    run_pybroker_order_plan,
    run_rotation_factor,
)
from scripts.run_nasdaq100_stochrsi_rotation import (
    _assert_no_synthetic_fills,
    _source_member_map,
    json_safe,
    qqq_buy_hold,
    read_vendor_member,
    write_json,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010"
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


def _full_dense_price_frame(
    raw: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    security_id: str,
) -> pd.DataFrame:
    source = raw.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.normalize()
    source = source[source["date"].isin(calendar)].drop_duplicates("date", keep="last")
    if source.empty:
        raise ValueError(f"{security_id} has no vendor price in the analysis window")
    first = pd.Timestamp(source["date"].min())
    last = pd.Timestamp(source["date"].max())
    indexed = source.set_index("date").reindex(calendar)
    missing = indexed["close"].isna()
    for column in ("open", "high", "low", "close"):
        indexed[column] = indexed[column].ffill().bfill()
    indexed["volume"] = indexed["volume"].fillna(0.0)
    indexed["symbol"] = security_id
    indexed["synthetic_bar"] = missing.astype(int)
    indexed["terminal_settlement_proxy"] = (indexed.index > last).astype(int)
    indexed["prelisting_proxy"] = (indexed.index < first).astype(int)
    return indexed.reset_index(names="date")


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


def prepare_shared_artifacts(
    context,
    destination: Path,
    *,
    allow_empty_analysis_price: bool = False,
) -> dict[str, Any]:
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
    intervals = pd.read_csv(
        MEMBERSHIP_INTERVALS_PATH, parse_dates=["effective_start", "effective_end"]
    )
    bounds = shifted_membership_bounds(intervals, sessions)
    overlap = bounds[
        bounds["known_start"].le(end) & bounds["known_end"].ge(start)
    ]["security_id"].astype(str)
    universe_ids = set(overlap)
    records = master[master["security_id"].astype(str).isin(universe_ids)].sort_values("security_id")
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
                prepared = prepare_strategy1_score_inputs(raw)
                result = run_strategy1_score(
                    prepared, initial_cash=float(context.config["initial_cash"])
                )
                state = result.daily[["date", "strategy1_weight"]].merge(
                    prepared[["date", "stochrsi_100"]],
                    on="date", how="left", validate="one_to_one",
                )
                state = state[state["date"].between(start, end)].copy()
                state["security_id"] = security_id
                state["display_ticker"] = str(item["display_ticker"])
                state["source_file"] = source_file
                state["in_universe"] = membership_flags_for_dates(
                    state["date"], bounds, security_id
                )
                state_frames.append(state)
                price_frames.append(
                    _full_dense_price_frame(raw, calendar, security_id=security_id)
                )
                diagnostic_rows.append({
                    "security_id": security_id,
                    "display_ticker": str(item["display_ticker"]),
                    "source_file": source_file,
                    "analysis_real_bars": len(state),
                    "in_universe_analysis_bars": int(state["in_universe"].sum()),
                    **result.diagnostics,
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
                    f"shared preparation {offset}/{len(records)}; errors={len(error_rows)}; "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    allowed_empty_price_errors = [
        item for item in error_rows
        if item["error_type"] == "ValueError"
        and item["error"] == f"{item['security_id']} has no vendor price in the analysis window"
    ]
    fatal_error_rows = [item for item in error_rows if item not in allowed_empty_price_errors]
    if fatal_error_rows or (allowed_empty_price_errors and not allow_empty_analysis_price):
        raise RuntimeError(f"shared preparation has {len(error_rows)} security errors")
    state = pd.concat(state_frames, ignore_index=True).sort_values(
        ["date", "security_id"], kind="stable"
    ).reset_index(drop=True)
    panel = pd.concat(price_frames, ignore_index=True).sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    if panel.duplicated(["date", "symbol"]).any():
        raise AssertionError("shared panel contains duplicate security dates")
    state.to_csv(destination / "security_state.csv.gz", index=False, compression="gzip")
    panel.to_csv(destination / "price_panel.csv.gz", index=False, compression="gzip")
    records[["security_id", "display_ticker", "source_file"]].to_csv(
        destination / "instrument_master.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(diagnostic_rows).to_csv(
        destination / "signal_diagnostics.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(
        error_rows,
        columns=["security_id", "display_ticker", "source_file", "error_type", "error"],
    ).to_csv(destination / "signal_errors.csv", index=False, lineterminator="\n")
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
        "score_error_count": len(fatal_error_rows),
        "excluded_no_analysis_price_count": len(allowed_empty_price_errors),
        "excluded_no_analysis_price": allowed_empty_price_errors,
        "source_files": source_files,
        "artifacts": {},
    }
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
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
    flattened: dict[str, float] = {}
    for case_id in sorted(checks):
        for key in sorted(checks[case_id]):
            value = float(checks[case_id][key])
            if not np.isfinite(value):
                raise AssertionError(f"non-finite check: {case_id}.{key}")
            flattened[f"{case_id}.{key}"] = value
    return flattened


def run_cost_block(context, run_id: str, symbol: str, cost_bps: float) -> None:
    assert_run_writable(context, run_id)
    if symbol != "NASDAQ100_STRATEGY1_ROTATION_2005_2010":
        raise ValueError("unexpected symbol block")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("unexpected cost scenario")
    shared = _prepare_formal_shared(context, run_id)
    shared_manifest = _load_shared(shared)
    if shared_manifest["score_error_count"]:
        raise RuntimeError("shared score preparation contains errors")
    temp = Path(tempfile.mkdtemp(
        prefix=f"cost_{cost_bps:g}bps_building_", dir=context.run_root(run_id)
    ))
    state = pd.read_csv(shared / "security_state.csv.gz", parse_dates=["date"])
    panel = pd.read_csv(shared / "price_panel.csv.gz", parse_dates=["date"])
    calendar = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    initial_cash = float(context.config["initial_cash"])
    parameters = context.config["parameters"]
    expected_ids = list(parameters["formal_case_ids_per_cost"])
    specs = factor_specs()
    if [item.case_id for item in specs] != expected_ids:
        raise AssertionError("factorial case order differs from experiment definition")

    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    selection_frames: list[pd.DataFrame] = []
    checks: dict[str, dict[str, float]] = {}
    cases: list[tuple[str, RotationFactorSpec, bool]] = [
        (item.case_id, item, False) for item in specs
    ] + [
        (
            parameters["baseline2_case_id"],
            RotationFactorSpec(False, "TOP20_90", "RESTORE_EQUAL"),
            True,
        )
    ]
    for offset, (case_id, spec, all_members) in enumerate(cases, start=1):
        result = run_rotation_factor(
            state, panel, spec, initial_cash=initial_cash, cost_bps=cost_bps,
            all_member_baseline=all_members,
        )
        if result.metrics["case_id"] != case_id:
            raise AssertionError("result case id differs")
        terminal_count = _assert_no_synthetic_fills(
            result.orders,
            panel,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        broker, broker_positions = run_pybroker_order_plan(
            panel, result.orders, initial_cash=initial_cash, cost_bps=cost_bps
        )
        order_checks = cross_check_pybroker_order_plan(
            broker,
            result.orders,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        state_checks = cross_check_pybroker_daily_state(
            broker, broker_positions, result.daily, result.positions,
            tolerance=LEDGER_TOLERANCE,
        )
        checks[case_id] = {
            **{f"reference_{key}": value for key, value in result.replay_checks.items()},
            **{f"pybroker_{key}": value for key, value in order_checks.items()},
            **{f"pybroker_{key}": value for key, value in state_checks.items()},
        }
        metrics = {
            **result.metrics,
            "terminal_settlement_order_count": terminal_count,
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
            f"cost={cost_bps:g}bps case {offset}/{len(cases)} {case_id}: "
            f"CAGR={metrics['cagr_pct']:.3f}% Sharpe={metrics['sharpe']:.3f} "
            f"maxDD={metrics['max_drawdown_pct']:.3f}% holdings={metrics['average_holdings_count']:.1f}",
            flush=True,
        )
        del result, broker, broker_positions
        gc.collect()

    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    qqq["date"] = pd.to_datetime(qqq["date"]).dt.normalize()
    policy = ExplicitFillPolicy("open", float(cost_bps))
    qqq_daily, qqq_orders, qqq_metrics = qqq_buy_hold(
        qqq, calendar, initial_cash=initial_cash, policy=policy
    )
    qqq_metrics.update({
        "case_id": parameters["baseline3_case_id"],
        "fast_exit": False,
        "selection_mode": "QQQ",
        "allocation_mode": "BUY_HOLD",
        "average_holdings_count": float(qqq_daily["is_long"].mean()),
        "median_holdings_count": 1.0,
        "maximum_holdings_count": 1,
        "fast_exit_count": 0,
        "unavailable_target_deferral_count": 0,
        "terminal_settlement_order_count": 0,
        "cost_bps": cost_bps,
    })
    metric_rows.append(qqq_metrics)
    qqq_daily.to_csv(temp / "qqq_buy_hold_daily.csv", index=False, lineterminator="\n")
    qqq_orders.to_csv(temp / "qqq_buy_hold_orders.csv", index=False, lineterminator="\n")

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
    payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "data_status": "candidate_pending_review",
        "cases": metric_rows,
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
        "engine": "lib-pybroker 1.2.12 mixed Open/Close event replay",
        "reference_engine": "quantkit.nasdaq100_strategy1_rotation_factorial.run_rotation_factor",
        "python": platform.python_version(),
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "analysis_bars": len(calendar),
        "signal_timing": "completed adjusted Close with lagged point-in-time membership",
        "execution_timing": "scheduled next adjusted Open; fast true downcross current adjusted Close",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "data_status": "candidate_pending_review",
        "parameters": parameters,
        "case_metrics": metric_rows,
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
        manifest = prepare_shared_artifacts(context, args.prepare_only.resolve())
        print(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2))
        return
    if args.run_id is None or args.symbol is None or args.cost_bps is None:
        parser.error("formal execution requires --run-id, --symbol and --cost-bps")
    run_cost_block(context, args.run_id, args.symbol, float(args.cost_bps))


if __name__ == "__main__":
    main()
