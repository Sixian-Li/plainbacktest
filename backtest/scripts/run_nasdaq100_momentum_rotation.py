#!/usr/bin/env python3
"""Run the point-in-time Nasdaq-100 12-1 momentum rotation experiment."""

from __future__ import annotations

import argparse
import gc
import io
import json
import platform
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pybroker

from quantkit.execution import ExplicitFillPolicy, assert_orders_match_policy
from quantkit.experiment import (
    assert_run_writable,
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.metrics import calculate_metrics
from quantkit.nasdaq100_momentum_rotation import (
    FORMAL_CASES,
    PORTFOLIO_REPLACE,
    AbsoluteMomentumGateCase,
    MomentumSpec,
    build_absolute_gate_target_schedule,
    build_target_schedule,
    build_target_share_table,
    calendar_month_ends,
    dense_price_frame,
    rank_monthly_momentum,
    shifted_membership_bounds,
)
from quantkit.trend_score_portfolio import (
    cross_check_portfolios,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50b.1__26-08-29__nasdaq100_12_1_momentum_top10_top20_buffer"
)
REGISTRY_PATH = WORKSPACE_ROOT / "data/nasdaq100_history_registry.json"
SOURCE_MANIFEST_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/source_manifest.json"
)
SECURITY_MASTER_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/security_master.csv"
)
MEMBERSHIP_INTERVALS_PATH = WORKSPACE_ROOT / (
    "data/processed/universes/nasdaq100/pending_review/membership_intervals.csv"
)
CALENDAR_PATH = WORKSPACE_ROOT / "data/processed/calendars/XNYS.csv"
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
LEDGER_TOLERANCE = 1e-6


def _absolute_gate_cases(context) -> tuple[AbsoluteMomentumGateCase, ...]:
    raw = context.config["parameters"].get("absolute_gate_cases", [])
    cases = tuple(
        AbsoluteMomentumGateCase(
            case_id=str(item["case_id"]),
            portfolio_style=str(item["portfolio_style"]),
            gate=str(item["gate"]),
        )
        for item in raw
    )
    for case in cases:
        case.validate()
    if cases:
        declared = tuple(str(value) for value in context.config["parameters"]["formal_cases"])
        observed = tuple(case.case_id for case in cases)
        if declared != observed:
            raise ValueError("absolute_gate_cases disagree with formal_cases")
    return cases


def _formal_case_ids(context) -> tuple[str, ...]:
    declared = tuple(str(value) for value in context.config["parameters"].get("formal_cases", []))
    return declared or FORMAL_CASES


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


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_vendor_member(
    archive: zipfile.ZipFile,
    member: str,
    security_id: str,
) -> pd.DataFrame:
    with archive.open(member) as raw:
        frame = pd.read_csv(
            io.TextIOWrapper(raw, encoding="utf-8-sig"),
            usecols=["Date", "Open", "High", "Low", "Close", "Volume"],
        )
    frame = frame.rename(columns={column: column.lower() for column in frame.columns})
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["symbol"] = security_id
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values(
        "date"
    ).reset_index(drop=True)


def _source_member_map(archive: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in archive.namelist():
        if not name.lower().endswith(".csv"):
            continue
        basename = Path(name).name
        if basename in mapping:
            raise ValueError(f"duplicate archive basename: {basename}")
        mapping[basename] = name
    return mapping


def _source_contract(context) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    parameters = context.config["parameters"]
    frozen = parameters["universe"]
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if registry.get("review_status") != "pending_review":
        raise ValueError("Nasdaq-100 registry review status changed")
    if source_manifest.get("status") != "candidate_pending_review":
        raise ValueError("Nasdaq-100 source manifest status changed")
    if source_manifest.get("approved") is not False:
        raise ValueError("exploratory run requires the explicitly frozen non-approved state")
    if source_manifest.get("build_id") != frozen["build_id"]:
        raise ValueError("Nasdaq-100 candidate build ID changed")
    package = registry["packages"][frozen["source_package_key"]]
    archive_path = WORKSPACE_ROOT / package["path"]
    if package["sha256"] != frozen["source_archive_sha256"] or sha256(archive_path) != package["sha256"]:
        raise ValueError("Nasdaq-100 source archive hash changed")
    if sha256(MEMBERSHIP_INTERVALS_PATH) != frozen["membership_intervals_sha256"]:
        raise ValueError("Nasdaq-100 membership intervals hash changed")
    if sha256(SECURITY_MASTER_PATH) != frozen["security_master_sha256"]:
        raise ValueError("Nasdaq-100 security master hash changed")
    return archive_path, registry, source_manifest


def prepare_shared_artifacts(context, destination: Path) -> dict[str, Any]:
    """Build immutable rankings, schedules and the dense execution panel once."""

    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError(f"shared destination is not empty: {destination}")
    archive_path, registry, source_manifest = _source_contract(context)
    parameters = context.config["parameters"]
    signal_start = pd.Timestamp(parameters["analysis_signal_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    sessions = pd.DatetimeIndex(pd.read_csv(CALENDAR_PATH, parse_dates=["date"])["date"])
    sessions = sessions.normalize().sort_values().unique()
    analysis_calendar = sessions[(sessions >= signal_start) & (sessions <= end)]
    if len(analysis_calendar) < 2 or analysis_calendar[0] != signal_start or analysis_calendar[-1] != end:
        raise ValueError("analysis boundaries do not match XNYS sessions")
    full_month_ends = calendar_month_ends(sessions)
    signal_dates = full_month_ends[
        (full_month_ends >= signal_start) & (full_month_ends <= end)
    ]
    if signal_dates.empty or signal_dates[0] != signal_start:
        raise ValueError("first frozen signal is not a real month end")

    master = pd.read_csv(SECURITY_MASTER_PATH, dtype=str).fillna("")
    intervals = pd.read_csv(
        MEMBERSHIP_INTERVALS_PATH,
        parse_dates=["effective_start", "effective_end"],
    )
    bounds = shifted_membership_bounds(
        intervals,
        sessions,
        lag_sessions=int(parameters["universe"]["membership_decision_lag_xnys_sessions"]),
    )
    display = dict(zip(master["security_id"], master["display_ticker"], strict=True))
    source_files = dict(zip(master["security_id"], master["source_file"], strict=True))
    frames: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(archive_path) as archive:
        members = _source_member_map(archive)
        if len(members) != 480 or len(master) != 480:
            raise ValueError("frozen Nasdaq-100 source must contain 480 securities")
        for row in master.itertuples(index=False):
            source_name = str(row.source_file)
            archive_member = members.get(source_name)
            if archive_member is None:
                raise FileNotFoundError(f"archive misses {source_name}")
            frames[str(row.security_id)] = read_vendor_member(
                archive,
                archive_member,
                str(row.security_id),
            )

    spec = MomentumSpec(
        lookback_months=int(parameters["momentum"]["lookback_months"]),
        absolute_lookback_months=int(
            parameters["momentum"].get("absolute_lookback_months", 6)
        ),
        skip_recent_months=int(parameters["momentum"]["skip_recent_months"]),
        entry_rank=int(parameters["ranking"]["entry_rank"]),
        exit_rank=int(parameters["ranking"]["buffer_exit_rank"]),
        membership_lag_sessions=int(
            parameters["universe"]["membership_decision_lag_xnys_sessions"]
        ),
    )
    rankings, ranking_audit = rank_monthly_momentum(
        frames,
        signal_dates,
        full_month_ends,
        bounds,
        display,
        spec,
    )
    absolute_cases = _absolute_gate_cases(context)
    if absolute_cases:
        schedule, schedule_audit = build_absolute_gate_target_schedule(
            rankings,
            signal_dates,
            analysis_calendar,
            bounds,
            display,
            absolute_cases,
            spec,
        )
    else:
        schedule, schedule_audit = build_target_schedule(
            rankings,
            signal_dates,
            analysis_calendar,
            bounds,
            display,
            spec,
        )

    dense_frames = []
    for security_id in sorted(frames):
        source_dates = pd.DatetimeIndex(frames[security_id]["date"])
        if source_dates.max() < analysis_calendar[0] or source_dates.min() > analysis_calendar[-1]:
            continue
        dense_frames.append(
            dense_price_frame(
                frames[security_id],
                analysis_calendar,
                instrument_id=security_id,
                display_ticker=display[security_id],
            )
        )
    price_panel = pd.concat(dense_frames, ignore_index=True).sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)
    instrument_master = master[
        ["security_id", "source_file", "source_symbol", "display_ticker", "company_name"]
    ].copy()
    instrument_master["source_file"] = instrument_master["security_id"].map(source_files)

    paths = {
        "rankings.csv.gz": rankings,
        "ranking_audit.csv": ranking_audit,
        "target_schedule.csv.gz": schedule,
        "target_schedule_audit.csv": schedule_audit,
        "price_panel.csv.gz": price_panel,
        "instrument_master.csv": instrument_master,
        "membership_bounds.csv": bounds,
    }
    for name, frame in paths.items():
        compression = "gzip" if name.endswith(".gz") else None
        frame.to_csv(destination / name, index=False, compression=compression, lineterminator="\n")

    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "data_status": "candidate_pending_review",
        "approved": False,
        "dataset_id": source_manifest["dataset_id"],
        "build_id": source_manifest["build_id"],
        "source_archive": {
            "path": str(archive_path.relative_to(WORKSPACE_ROOT)),
            "sha256": sha256(archive_path),
            "declared_adjustment": registry["packages"]["split_and_dividend"][
                "declared_adjustment_from_filename"
            ],
        },
        "analysis_signal_start": signal_start,
        "analysis_end": end,
        "analysis_sessions": len(analysis_calendar),
        "monthly_signals": len(signal_dates),
        "securities": len(master),
        "execution_securities": int(price_panel["symbol"].nunique()),
        "price_rows_source": int(sum(len(frame) for frame in frames.values())),
        "price_rows_dense": len(price_panel),
        "ranking_rows": len(rankings),
        "target_schedule_rows": len(schedule),
        "synthetic_internal_bars": int(
            ((price_panel["synthetic_bar"] == 1) & (price_panel["terminal_settlement_proxy"] == 0)).sum()
        ),
        "terminal_proxy_bars": int(price_panel["terminal_settlement_proxy"].sum()),
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


def _load_shared(shared_root: Path) -> dict[str, Any]:
    manifest_path = shared_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"shared manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, record in manifest["artifacts"].items():
        path = shared_root / name
        if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"shared artifact changed: {name}")
    return manifest


def _prepare_formal_shared(context, run_id: str) -> Path:
    run_root = context.run_root(run_id)
    shared_root = run_root / "shared"
    if shared_root.is_dir():
        _load_shared(shared_root)
        return shared_root
    building = Path(tempfile.mkdtemp(prefix="shared_building_", dir=run_root))
    prepare_shared_artifacts(context, building)
    building.rename(shared_root)
    return shared_root


def _assert_no_forbidden_synthetic_fills(
    orders: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    numerical_zero_notional: float,
) -> int:
    if orders.empty:
        return 0
    bars = price_panel[["date", "symbol", "synthetic_bar", "terminal_settlement_proxy"]]
    merged = orders.reset_index().merge(
        bars,
        on=["date", "symbol"],
        how="left",
        validate="many_to_one",
    )
    synthetic = merged["synthetic_bar"].fillna(0).astype(int).eq(1)
    notional = merged["shares"].astype(float).abs() * merged["fill_price"].astype(float).abs()
    dust = notional <= float(numerical_zero_notional)
    terminal_sell = (
        synthetic
        & merged["terminal_settlement_proxy"].fillna(0).astype(int).eq(1)
        & merged["type"].eq("sell")
        & ~dust
    )
    forbidden = synthetic & ~terminal_sell & ~dust
    if forbidden.any():
        raise AssertionError(
            "orders executed on forbidden synthetic bars: "
            + str(merged[forbidden].head(10).to_dict("records"))
        )
    return int(terminal_sell.sum())


def _flatten_cross_checks(values: dict[str, dict[str, float]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for case_id in sorted(values):
        for name in sorted(values[case_id]):
            value = float(values[case_id][name])
            if not np.isfinite(value):
                raise AssertionError(f"non-finite ledger difference: {case_id}.{name}")
            result[f"{case_id}.{name}"] = value
    return result


def qqq_buy_hold(
    calendar: pd.DatetimeIndex,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    qqq["date"] = pd.to_datetime(qqq["date"]).dt.normalize()
    prices = qqq.set_index("date").reindex(calendar)
    if prices[["open", "close"]].isna().any().any():
        raise ValueError("QQQ does not cover the common analysis calendar")
    execution_date = calendar[1]
    fill = policy.expected_fill("buy", float(prices.at[execution_date, "open"]))
    shares = initial_cash / fill
    daily = pd.DataFrame(
        {
            "date": calendar,
            "cash": [initial_cash] + [0.0] * (len(calendar) - 1),
            "shares": [0.0] + [shares] * (len(calendar) - 1),
            "equity": [initial_cash]
            + (shares * prices.loc[calendar[1:], "close"].astype(float)).tolist(),
            "is_long": [0] + [1] * (len(calendar) - 1),
            "gross_exposure": [0.0] + [1.0] * (len(calendar) - 1),
        }
    )
    orders = pd.DataFrame(
        [{"date": execution_date, "symbol": "QQQ", "type": "buy", "fill_price": fill, "shares": shares}]
    )
    metrics = calculate_metrics(
        daily,
        orders,
        pd.DataFrame(columns=["pnl"]),
        initial_cash=initial_cash,
    )
    elapsed_years = max((calendar[-1] - calendar[0]).days / 365.2425, 1e-12)
    metrics["annualized_turnover_multiple"] = metrics["turnover_multiple"] / elapsed_years
    return daily, orders, metrics


def _case_state(
    daily: pd.DataFrame,
    positions: pd.DataFrame,
) -> pd.DataFrame:
    counts = (
        positions[positions["shares"].astype(float) > 1e-10]
        .groupby("date")["symbol"]
        .nunique()
    )
    result = daily.copy().set_index("date")
    result["holdings_count"] = counts.reindex(result.index).fillna(0).astype(int)
    result["cash_pct"] = np.where(
        result["equity"].astype(float) > 0,
        result["cash"].astype(float) / result["equity"].astype(float) * 100.0,
        0.0,
    )
    return result.reset_index()


def run_cost_block(context, run_id: str, symbol: str, cost_bps: float) -> None:
    assert_run_writable(context, run_id)
    if symbol != "NASDAQ100_MOMENTUM_ROTATION":
        raise ValueError("unexpected symbol block")
    if float(cost_bps) not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("unexpected cost scenario")
    shared_root = _prepare_formal_shared(context, run_id)
    shared_manifest = _load_shared(shared_root)
    temp_root = Path(
        tempfile.mkdtemp(prefix=f"cost_{float(cost_bps):g}bps_building_", dir=context.run_root(run_id))
    )
    price_panel = pd.read_csv(shared_root / "price_panel.csv.gz", parse_dates=["date"])
    schedule = pd.read_csv(shared_root / "target_schedule.csv.gz", parse_dates=["date"])
    schedule_audit = pd.read_csv(shared_root / "target_schedule_audit.csv", parse_dates=["date"])
    policy = ExplicitFillPolicy("open", float(cost_bps))
    initial_cash = float(context.config["initial_cash"])
    numerical_zero = float(context.config["parameters"]["numerical_zero_order_notional_usd"])
    formal_cases = _formal_case_ids(context)
    absolute_cases = {case.case_id: case for case in _absolute_gate_cases(context)}
    pybroker.disable_logging()
    pybroker.disable_progress_bar()

    daily_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    target_share_frames: list[pd.DataFrame] = []
    metrics_rows: list[dict[str, Any]] = []
    differences: dict[str, dict[str, float]] = {}
    years = max(
        (pd.Timestamp(shared_manifest["analysis_end"]) - pd.Timestamp(shared_manifest["analysis_signal_start"])).days
        / 365.2425,
        1e-12,
    )

    for offset, case_id in enumerate(formal_cases, start=1):
        target_shares = build_target_share_table(
            price_panel,
            schedule,
            case_id=case_id,
            initial_cash=initial_cash,
            policy=policy,
        )
        broker_result, broker_positions = run_pybroker_portfolio(
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
        differences[case_id] = cross_check_portfolios(
            broker_result,
            broker_positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=numerical_zero,
        )
        assert_orders_match_policy(broker_result.orders, price_panel, policy)
        terminal_count = _assert_no_forbidden_synthetic_fills(
            broker_result.orders,
            price_panel,
            numerical_zero_notional=numerical_zero,
        )
        actual = pybroker_daily_state(broker_result)
        state = _case_state(actual, reference.positions)
        orders = broker_result.orders.reset_index().copy()
        trades = broker_result.trades.reset_index().copy()
        metrics = calculate_metrics(actual, orders, trades, initial_cash=initial_cash)
        metrics.update(
            {
                "case_id": case_id,
                "annualized_turnover_multiple": float(metrics["turnover_multiple"] / years),
                "average_holdings_count": float(state["holdings_count"].mean()),
                "maximum_holdings_count": int(state["holdings_count"].max()),
                "average_cash_pct": float(state["cash_pct"].mean()),
                "terminal_settlement_order_count": terminal_count,
                "unavailable_target_deferral_count": int(
                    target_shares["execution_deferred_unavailable"].sum()
                ),
                "forced_exit_signal_rows": int(
                    (
                        schedule[
                            (schedule["case_id"] == case_id)
                            & (schedule["event_type"] == "forced_membership_exit")
                        ]["date"].nunique()
                    )
                ),
            }
        )
        if case_id in absolute_cases:
            case = absolute_cases[case_id]
            cap = int(
                context.config["parameters"]["ranking"][
                    "entry_rank"
                    if case.portfolio_style == PORTFOLIO_REPLACE
                    else "buffer_exit_rank"
                ]
            )
            if metrics["maximum_holdings_count"] > cap:
                raise AssertionError(f"{case_id} actual holdings exceeded {cap}")
        elif case_id == "TOP10_MONTHLY_REPLACE" and metrics["maximum_holdings_count"] > 10:
            raise AssertionError("Top10 actual holdings exceeded 10")
        elif case_id == "TOP10_EXIT20_BUFFER" and metrics["maximum_holdings_count"] > 20:
            raise AssertionError("buffer actual holdings exceeded 20")
        metrics_rows.append(metrics)
        state.insert(0, "case_id", case_id)
        orders.insert(0, "case_id", case_id)
        trades.insert(0, "case_id", case_id)
        positions = reference.positions.copy()
        positions.insert(0, "case_id", case_id)
        target_shares.insert(0, "case_id", case_id)
        daily_frames.append(state)
        order_frames.append(orders)
        trade_frames.append(trades)
        position_frames.append(positions)
        target_share_frames.append(target_shares)
        print(
            f"cost={float(cost_bps):g}bps case {offset}/{len(formal_cases)} {case_id}: "
            f"CAGR={metrics['cagr_pct']:.3f}% Sharpe={metrics['sharpe']:.3f} "
            f"maxDD={metrics['max_drawdown_pct']:.3f}% turnover/yr="
            f"{metrics['annualized_turnover_multiple']:.3f}",
            flush=True,
        )
        del broker_result, broker_positions, reference
        gc.collect()

    daily = pd.concat(daily_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    target_shares = pd.concat(target_share_frames, ignore_index=True)
    metrics_frame = pd.DataFrame(metrics_rows)
    calendar = pd.DatetimeIndex(
        daily[daily["case_id"] == formal_cases[0]]["date"]
    )
    benchmark_daily, benchmark_orders, benchmark_metrics = qqq_buy_hold(
        calendar,
        initial_cash=initial_cash,
        policy=policy,
    )

    metrics_frame.to_csv(temp_root / "metrics.csv", index=False, lineterminator="\n")
    daily.to_csv(temp_root / "daily.csv.gz", index=False, compression="gzip")
    orders.to_csv(temp_root / "orders.csv.gz", index=False, compression="gzip")
    trades.to_csv(temp_root / "trades.csv.gz", index=False, compression="gzip")
    positions.to_csv(temp_root / "positions.csv.gz", index=False, compression="gzip")
    target_shares.to_csv(temp_root / "target_shares.csv.gz", index=False, compression="gzip")
    benchmark_daily.to_csv(temp_root / "qqq_buy_hold_daily.csv", index=False, lineterminator="\n")
    benchmark_orders.to_csv(temp_root / "qqq_buy_hold_orders.csv", index=False, lineterminator="\n")
    primary_audit = schedule_audit[
        schedule_audit["case_id"] == context.config["parameters"]["primary_case"]
    ]
    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": float(cost_bps),
        "data_status": "candidate_pending_review",
        "cases": metrics_rows,
        "benchmark": benchmark_metrics,
        "primary_schedule": {
            "monthly_rebalances": int((primary_audit["event_type"] == "full_rebalance").sum()),
            "minimum_selected": int(
                primary_audit.loc[primary_audit["event_type"] == "full_rebalance", "selected_count"].min()
            ),
            "maximum_selected": int(
                primary_audit.loc[primary_audit["event_type"] == "full_rebalance", "selected_count"].max()
            ),
        },
        "max_cross_check_differences": differences,
    }
    write_json(temp_root / "metrics.json", metrics_payload)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": float(cost_bps),
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.trend_score_portfolio.run_reference_portfolio",
        "python": platform.python_version(),
        "analysis_start": shared_manifest["analysis_signal_start"],
        "analysis_end": shared_manifest["analysis_end"],
        "analysis_bars": shared_manifest["analysis_sessions"],
        "signal_timing": "calendar month-end adjusted Close with one-XNYS-session lagged membership",
        "execution_timing": "next XNYS adjusted Open; Close-sized shares; sells before buys",
        "data_status": "candidate_pending_review",
        "parameters": context.config["parameters"],
        "case_metrics": metrics_rows,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": _flatten_cross_checks(differences),
        "shared_manifest_sha256": sha256(shared_root / "manifest.json"),
        "artifacts": {},
    }
    for path in sorted(temp_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    write_json(temp_root / "manifest.json", manifest)

    output_root = reserve_block(context, run_id, symbol, float(cost_bps))
    for path in sorted(temp_root.iterdir()):
        path.replace(output_root / path.name)
    temp_root.rmdir()
    record_block_complete(context, run_id, symbol, float(cost_bps), output_root / "manifest.json")


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
        manifest = prepare_shared_artifacts(context, args.prepare_only)
        print(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2))
        return
    if not args.run_id or not args.symbol or args.cost_bps is None:
        parser.error("formal execution requires --run-id, --symbol and --cost-bps")
    run_cost_block(context, args.run_id, args.symbol, float(args.cost_bps))


if __name__ == "__main__":
    main()
