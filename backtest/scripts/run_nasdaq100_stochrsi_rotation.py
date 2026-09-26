#!/usr/bin/env python3
"""Prepare and run the frozen point-in-time Nasdaq-100 Strategy1 rotation."""

from __future__ import annotations

import argparse
import gc
import io
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
from quantkit.nasdaq100_stochrsi_rotation import (
    build_rotation_target_share_table,
    build_target_weights,
    case_definitions,
    membership_flags_for_dates,
    prepare_strategy1_score_inputs,
    rank_eligible_scores,
    run_strategy1_score,
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
    "experiments/ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20"
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
EQUITY_PRICE_ROOT = WORKSPACE_ROOT / "data/processed/daily/equities"
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
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


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_vendor_member(archive: zipfile.ZipFile, member: str, security_id: str) -> pd.DataFrame:
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


def read_canonical_equity(path: Path, instrument_id: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["symbol"] = instrument_id
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values(
        "date"
    ).reset_index(drop=True)


def dense_price_frame(
    frame: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    instrument_id: str,
    display_ticker: str,
    asset_type: str,
) -> pd.DataFrame:
    source = frame.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.normalize()
    source = source[source["date"].isin(calendar)].drop_duplicates("date", keep="last")
    if source.empty:
        raise ValueError(f"{instrument_id} has no price in the analysis calendar")
    source_last_date = pd.Timestamp(source["date"].max())
    active_calendar = calendar[calendar >= source["date"].min()]
    indexed = source.set_index("date").reindex(active_calendar)
    synthetic = indexed["close"].isna()
    for column in ("open", "high", "low", "close"):
        indexed[column] = indexed[column].ffill()
    indexed["volume"] = indexed["volume"].fillna(0.0)
    if indexed[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"{instrument_id} dense panel could not fill an internal price gap")
    indexed["symbol"] = instrument_id
    indexed["synthetic_bar"] = synthetic.astype(int)
    indexed["terminal_settlement_proxy"] = (indexed.index > source_last_date).astype(int)
    indexed["display_ticker"] = display_ticker
    indexed["asset_type"] = asset_type
    return indexed.reset_index(names="date")


def _source_member_map(archive: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in archive.namelist():
        if not name.lower().endswith(".csv"):
            continue
        basename = Path(name).name
        if basename in mapping:
            raise ValueError(f"duplicate source basename in archive: {basename}")
        mapping[basename] = name
    return mapping


def prepare_shared_artifacts(context, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise FileExistsError(f"shared artifact destination is not empty: {destination}")
    parameters = context.config["parameters"]
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if registry.get("review_status") != "pending_review":
        raise ValueError("Nasdaq-100 registry review status changed")
    if source_manifest.get("status") != "candidate_pending_review" or source_manifest.get("approved") is not False:
        raise ValueError("Nasdaq-100 source manifest is not the frozen pending-review candidate")
    package = registry["packages"][parameters["universe"]["source_package_key"]]
    archive_path = WORKSPACE_ROOT / package["path"]
    if sha256(archive_path) != package["sha256"]:
        raise ValueError("Nasdaq-100 source archive hash changed")
    master = pd.read_csv(SECURITY_MASTER_PATH, dtype=str).fillna("")
    intervals = pd.read_csv(MEMBERSHIP_INTERVALS_PATH, parse_dates=["effective_start", "effective_end"])
    sessions = pd.DatetimeIndex(pd.read_csv(CALENDAR_PATH, parse_dates=["date"])["date"]).normalize()
    sessions = sessions.sort_values().unique()
    analysis_calendar = sessions[(sessions >= start) & (sessions <= end)]
    if len(analysis_calendar) < 2 or analysis_calendar[0] != start or analysis_calendar[-1] != end:
        raise ValueError("analysis boundaries do not match XNYS sessions")
    membership_bounds = shifted_membership_bounds(intervals, sessions)

    score_frames: list[pd.DataFrame] = []
    diagnostic_rows: list[dict[str, Any]] = []
    score_errors: list[dict[str, Any]] = []
    started = time.perf_counter()
    with zipfile.ZipFile(archive_path) as archive:
        member_map = _source_member_map(archive)
        records = master.sort_values("security_id").to_dict("records")
        for offset, item in enumerate(records, start=1):
            source_file = str(item["source_file"])
            member = member_map.get(source_file)
            if member is None:
                raise FileNotFoundError(f"archive misses {source_file}")
            security_id = str(item["security_id"])
            try:
                raw = read_vendor_member(archive, member, security_id)
                prepared = prepare_strategy1_score_inputs(raw)
                result = run_strategy1_score(prepared, initial_cash=float(context.config["initial_cash"]))
                state = result.daily[result.daily["date"].between(start, end)].copy()
                state["security_id"] = security_id
                state["display_ticker"] = str(item["display_ticker"])
                state["source_symbol"] = str(item["source_symbol"])
                state["in_universe"] = membership_flags_for_dates(
                    state["date"], membership_bounds, security_id
                )
                state = state[state["in_universe"]][
                    [
                        "date",
                        "security_id",
                        "display_ticker",
                        "source_symbol",
                        "strategy1_weight",
                        "in_universe",
                    ]
                ]
                score_frames.append(state)
                diagnostic_rows.append(
                    {
                        "security_id": security_id,
                        "display_ticker": str(item["display_ticker"]),
                        "source_file": source_file,
                        "in_universe_analysis_bars": len(state),
                        **result.diagnostics,
                    }
                )
            except Exception as exc:  # retain the rest of the large candidate audit
                score_errors.append(
                    {
                        "security_id": security_id,
                        "display_ticker": str(item["display_ticker"]),
                        "source_file": source_file,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            if offset % 25 == 0 or offset == len(records):
                print(
                    f"score preparation {offset}/{len(records)}; errors={len(score_errors)}; "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    scores = pd.concat(score_frames, ignore_index=True) if score_frames else pd.DataFrame()
    selected, selection_daily = rank_eligible_scores(
        scores,
        analysis_calendar,
        threshold=float(parameters["signal_engine"]["eligibility_threshold"]),
        maximum_selected=int(parameters["ranking"]["maximum_selected"]),
    )
    eligible_scores = scores[
        scores["strategy1_weight"].astype(float)
        > float(parameters["signal_engine"]["eligibility_threshold"])
    ].copy()
    selected_ids = sorted(selected["security_id"].unique())
    master_index = master.set_index("security_id")

    price_frames: list[pd.DataFrame] = []
    instrument_rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as archive:
        member_map = _source_member_map(archive)
        for security_id in selected_ids:
            item = master_index.loc[security_id]
            raw = read_vendor_member(archive, member_map[str(item["source_file"])], security_id)
            price_frames.append(
                dense_price_frame(
                    raw,
                    analysis_calendar,
                    instrument_id=security_id,
                    display_ticker=str(item["display_ticker"]),
                    asset_type="nasdaq100_member",
                )
            )
            instrument_rows.append(
                {
                    "instrument_id": security_id,
                    "display_ticker": str(item["display_ticker"]),
                    "asset_type": "nasdaq100_member",
                    "source_file": str(item["source_file"]),
                }
            )

    bear_available: dict[str, pd.Series] = {}
    bear_paths: list[Path] = []
    for symbol in parameters["bear9_weights"]:
        path = EQUITY_PRICE_ROOT / f"{symbol}.csv"
        bear_paths.append(path)
        instrument_id = f"BEAR9__{symbol}"
        raw = read_canonical_equity(path, instrument_id)
        observed = raw.set_index("date")["close"].reindex(analysis_calendar).notna()
        bear_available[symbol] = observed
        price_frames.append(
            dense_price_frame(
                raw,
                analysis_calendar,
                instrument_id=instrument_id,
                display_ticker=symbol,
                asset_type="bear9",
            )
        )
        instrument_rows.append(
            {
                "instrument_id": instrument_id,
                "display_ticker": symbol,
                "asset_type": "bear9",
                "source_file": str(path.relative_to(WORKSPACE_ROOT)),
            }
        )
    price_panel = pd.concat(price_frames, ignore_index=True).sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    if price_panel.duplicated(["date", "symbol"]).any():
        raise AssertionError("prepared price panel contains duplicate instrument dates")

    target_frames = [
        build_target_weights(
            selected,
            analysis_calendar,
            case_id=case["case_id"],
            rebalance_days=case["rebalance_days"],
            allocation_mode=case["allocation_mode"],
            bear_weights=parameters["bear9_weights"],
            bear_available=bear_available,
        )
        for case in case_definitions(parameters)
    ]
    target_weights = pd.concat(target_frames, ignore_index=True)
    totals = target_weights.groupby(["case_id", "date"])["target_weight"].sum()
    if (totals > 1 + 1e-12).any():
        raise AssertionError("shared target schedule exceeds 100%")

    eligible_scores.to_csv(destination / "eligible_scores.csv.gz", index=False, compression="gzip")
    selected.to_csv(destination / "selected_rankings.csv.gz", index=False, compression="gzip")
    selection_daily.to_csv(destination / "selection_daily.csv", index=False, lineterminator="\n")
    target_weights.to_csv(destination / "target_weights.csv.gz", index=False, compression="gzip")
    price_panel.to_csv(destination / "price_panel.csv.gz", index=False, compression="gzip")
    pd.DataFrame(instrument_rows).to_csv(
        destination / "instrument_master.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(diagnostic_rows).to_csv(
        destination / "signal_diagnostics.csv", index=False, lineterminator="\n"
    )
    pd.DataFrame(score_errors, columns=["security_id", "display_ticker", "source_file", "error_type", "error"]).to_csv(
        destination / "signal_errors.csv", index=False, lineterminator="\n"
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [
            REGISTRY_PATH,
            SECURITY_MASTER_PATH,
            MEMBERSHIP_INTERVALS_PATH,
            SOURCE_MANIFEST_PATH,
            CALENDAR_PATH,
            archive_path,
            QQQ_PATH,
            *bear_paths,
        ]
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
        "analysis_sessions": len(analysis_calendar),
        "security_records": len(master),
        "score_error_count": len(score_errors),
        "score_errors": score_errors,
        "selected_security_count_ever": len(selected_ids),
        "eligible_observations": len(eligible_scores),
        "selected_observations": len(selected),
        "price_panel_rows": len(price_panel),
        "price_panel_instruments": int(price_panel["symbol"].nunique()),
        "synthetic_carry_bars": int(price_panel["synthetic_bar"].sum()),
        "internal_synthetic_bars": int(
            (
                price_panel["synthetic_bar"].astype(bool)
                & ~price_panel["terminal_settlement_proxy"].astype(bool)
            ).sum()
        ),
        "terminal_settlement_proxy_bars": int(
            price_panel["terminal_settlement_proxy"].sum()
        ),
        "minimum_eligible_count": int(selection_daily["eligible_count"].min()),
        "maximum_eligible_count": int(selection_daily["eligible_count"].max()),
        "mean_eligible_count": float(selection_daily["eligible_count"].mean()),
        "days_below_ten_eligible": int((selection_daily["eligible_count"] < 10).sum()),
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


def _exposure_state(
    daily: pd.DataFrame,
    positions: pd.DataFrame,
    price_panel: pd.DataFrame,
    instrument_master: pd.DataFrame,
) -> pd.DataFrame:
    prices = price_panel[["date", "symbol", "close"]].copy()
    state = positions.merge(prices, on=["date", "symbol"], how="left", validate="one_to_one")
    state = state.merge(
        instrument_master.rename(columns={"instrument_id": "symbol"})[["symbol", "asset_type"]],
        on="symbol",
        how="left",
        validate="many_to_one",
    )
    state["market_value"] = state["shares"].astype(float) * state["close"].fillna(0.0).astype(float)
    grouped = state.pivot_table(
        index="date", columns="asset_type", values="market_value", aggfunc="sum", fill_value=0.0
    )
    counts = (
        state[(state["asset_type"] == "nasdaq100_member") & (state["shares"].astype(float) > 1e-10)]
        .groupby("date")["symbol"]
        .nunique()
    )
    result = daily.copy().set_index("date")
    member_value = grouped.get("nasdaq100_member", pd.Series(0.0, index=grouped.index)).reindex(result.index).fillna(0.0)
    bear_value = grouped.get("bear9", pd.Series(0.0, index=grouped.index)).reindex(result.index).fillna(0.0)
    result["member_value"] = member_value
    result["bear9_value"] = bear_value
    result["member_exposure_pct"] = member_value / result["equity"].astype(float) * 100.0
    result["bear9_exposure_pct"] = bear_value / result["equity"].astype(float) * 100.0
    result["cash_pct"] = result["cash"].astype(float) / result["equity"].astype(float) * 100.0
    result["member_holdings_count"] = counts.reindex(result.index).fillna(0).astype(int)
    return result.reset_index()


def _assert_no_synthetic_fills(
    orders: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    numerical_zero_notional: float = 0.0,
) -> int:
    if orders.empty:
        return 0
    bars = price_panel[["date", "symbol", "synthetic_bar", "terminal_settlement_proxy"]]
    merged = orders.reset_index().merge(
        bars, on=["date", "symbol"], how="left", validate="many_to_one"
    )
    synthetic = merged["synthetic_bar"].fillna(0).astype(int).eq(1)
    numerical_dust = pd.Series(False, index=merged.index)
    if {"shares", "fill_price"}.issubset(merged.columns):
        notional = (
            merged["shares"].astype(float).abs()
            * merged["fill_price"].astype(float).abs()
        )
        numerical_dust = notional.le(float(numerical_zero_notional))
    terminal_sell = (
        synthetic
        & merged["terminal_settlement_proxy"].fillna(0).astype(int).eq(1)
        & merged["type"].eq("sell")
        & ~numerical_dust
    )
    forbidden = synthetic & ~terminal_sell & ~numerical_dust
    if forbidden.any():
        sample = merged[forbidden].head(10)
        raise AssertionError(f"orders executed on forbidden synthetic bars: {sample.to_dict('records')}")
    return int(terminal_sell.sum())


def _flatten_cross_checks(
    cross_checks: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Expose every case-level reconciliation value to the standard validator."""

    flattened: dict[str, float] = {}
    for case_id in sorted(cross_checks):
        for metric in sorted(cross_checks[case_id]):
            value = float(cross_checks[case_id][metric])
            if not np.isfinite(value):
                raise AssertionError(f"non-finite reconciliation value: {case_id}.{metric}")
            flattened[f"{case_id}.{metric}"] = value
    return flattened


def _cross_check_payloads(
    cross_checks: dict[str, dict[str, float]],
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Keep case detail in metrics while giving the validator a scalar manifest."""

    return cross_checks, _flatten_cross_checks(cross_checks)


def qqq_buy_hold(
    qqq: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    prices = qqq.set_index("date").reindex(calendar)
    if prices[["open", "close"]].isna().any().any():
        raise ValueError("QQQ benchmark does not cover the common calendar")
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
        [
            {
                "date": execution_date,
                "symbol": "QQQ",
                "type": "buy",
                "fill_price": fill,
                "shares": shares,
            }
        ]
    )
    metrics = calculate_metrics(daily, orders, pd.DataFrame(columns=["pnl"]), initial_cash=initial_cash)
    return daily, orders, metrics


def run_cost_block(context, run_id: str, symbol: str, cost_bps: float) -> None:
    assert_run_writable(context, run_id)
    if symbol != "NASDAQ100_PIT_STRATEGY1_ROTATION":
        raise ValueError("unexpected experiment symbol block")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("unexpected cost scenario")
    shared_root = _prepare_formal_shared(context, run_id)
    shared_manifest = _load_shared(shared_root)
    if shared_manifest["score_error_count"]:
        raise RuntimeError(
            "formal run cannot start with unresolved score errors; inspect shared/signal_errors.csv"
        )
    temp_root = Path(tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=context.run_root(run_id)))
    price_panel = pd.read_csv(shared_root / "price_panel.csv.gz", parse_dates=["date"])
    targets = pd.read_csv(shared_root / "target_weights.csv.gz", parse_dates=["date"])
    instrument_master = pd.read_csv(shared_root / "instrument_master.csv", dtype=str)
    selection_daily = pd.read_csv(shared_root / "selection_daily.csv", parse_dates=["date"])
    parameters = context.config["parameters"]
    policy = ExplicitFillPolicy("open", float(cost_bps))
    initial_cash = float(context.config["initial_cash"])
    pybroker.disable_logging()
    pybroker.disable_progress_bar()

    daily_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    cross_checks: dict[str, Any] = {}
    final_position_rows: list[pd.DataFrame] = []
    for offset, case in enumerate(case_definitions(parameters), start=1):
        case_id = case["case_id"]
        case_targets = targets[targets["case_id"] == case_id].rename(
            columns={"instrument_id": "symbol"}
        )
        target_shares = build_rotation_target_share_table(
            price_panel,
            case_targets,
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
        differences = cross_check_portfolios(
            broker_result,
            broker_positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        cross_checks[case_id] = differences
        assert_orders_match_policy(broker_result.orders, price_panel, policy)
        terminal_settlement_order_count = _assert_no_synthetic_fills(
            broker_result.orders,
            price_panel,
            numerical_zero_notional=float(
                parameters["numerical_zero_order_notional_usd"]
            ),
        )
        actual = pybroker_daily_state(broker_result)
        exposure = _exposure_state(actual, reference.positions, price_panel, instrument_master)
        exposure.insert(0, "case_id", case_id)
        orders = broker_result.orders.reset_index().copy()
        orders.insert(0, "case_id", case_id)
        trades = broker_result.trades.reset_index().copy()
        trades.insert(0, "case_id", case_id)
        metrics = calculate_metrics(actual, orders, trades, initial_cash=initial_cash)
        metrics.update(
            {
                "average_member_exposure_pct": float(exposure["member_exposure_pct"].mean()),
                "average_bear9_exposure_pct": float(exposure["bear9_exposure_pct"].mean()),
                "average_cash_pct": float(exposure["cash_pct"].mean()),
                "average_member_holdings_count": float(exposure["member_holdings_count"].mean()),
                "rebalance_days": int(case["rebalance_days"]),
                "allocation_mode": str(case["allocation_mode"]),
                "terminal_settlement_order_count": terminal_settlement_order_count,
                "unavailable_target_deferral_count": int(
                    target_shares["execution_deferred_unavailable"].sum()
                ),
            }
        )
        metric_rows.append({"case_id": case_id, **metrics})
        daily_frames.append(exposure)
        order_frames.append(orders)
        trade_frames.append(trades)
        final_positions = reference.positions[
            reference.positions["date"] == reference.positions["date"].max()
        ].copy()
        final_positions = final_positions[final_positions["shares"].astype(float) > 1e-10]
        final_positions.insert(0, "case_id", case_id)
        final_position_rows.append(final_positions)
        print(
            f"cost={cost_bps:g}bps case {offset}/8 {case_id}: "
            f"CAGR={metrics['cagr_pct']:.3f}% Sharpe={metrics['sharpe']:.3f} "
            f"maxDD={metrics['max_drawdown_pct']:.3f}% orders={metrics['order_count']}",
            flush=True,
        )
        del broker_result, broker_positions, reference, target_shares
        gc.collect()

    daily = pd.concat(daily_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    final_positions = pd.concat(final_position_rows, ignore_index=True) if final_position_rows else pd.DataFrame()
    metric_frame = pd.DataFrame(metric_rows)
    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    qqq["date"] = pd.to_datetime(qqq["date"]).dt.normalize()
    calendar = pd.DatetimeIndex(selection_daily["date"])
    benchmark_daily, benchmark_orders, benchmark_metrics = qqq_buy_hold(
        qqq,
        calendar,
        initial_cash=initial_cash,
        policy=policy,
    )
    metric_frame.to_csv(temp_root / "metrics.csv", index=False, lineterminator="\n")
    daily.to_csv(temp_root / "daily.csv.gz", index=False, compression="gzip")
    orders.to_csv(temp_root / "orders.csv.gz", index=False, compression="gzip")
    trades.to_csv(temp_root / "trades.csv.gz", index=False, compression="gzip")
    final_positions.to_csv(temp_root / "final_positions.csv", index=False, lineterminator="\n")
    benchmark_daily.to_csv(temp_root / "qqq_buy_hold_daily.csv", index=False, lineterminator="\n")
    benchmark_orders.to_csv(temp_root / "qqq_buy_hold_orders.csv", index=False, lineterminator="\n")
    detailed_cross_checks, manifest_cross_checks = _cross_check_payloads(cross_checks)
    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "data_status": "candidate_pending_review",
        "cases": metric_rows,
        "benchmark": benchmark_metrics,
        "selection_summary": {
            "minimum_eligible_count": int(selection_daily["eligible_count"].min()),
            "maximum_eligible_count": int(selection_daily["eligible_count"].max()),
            "mean_eligible_count": float(selection_daily["eligible_count"].mean()),
            "days_below_ten": int((selection_daily["eligible_count"] < 10).sum()),
        },
        "max_cross_check_differences": detailed_cross_checks,
    }
    write_json(temp_root / "metrics.json", metrics_payload)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "lib-pybroker 1.2.12",
        "reference_engine": "quantkit.trend_score_portfolio.run_reference_portfolio",
        "python": platform.python_version(),
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "analysis_bars": len(calendar),
        "signal_timing": "completed adjusted Close with one-XNYS-session lagged membership",
        "execution_timing": "next XNYS adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "data_status": "candidate_pending_review",
        "parameters": parameters,
        "case_metrics": metric_rows,
        "benchmark_metrics": benchmark_metrics,
        "max_cross_check_differences": manifest_cross_checks,
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

    output_root = reserve_block(context, run_id, symbol, cost_bps)
    for path in sorted(temp_root.iterdir()):
        path.replace(output_root / path.name)
    temp_root.rmdir()
    record_block_complete(context, run_id, symbol, cost_bps, output_root / "manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id")
    parser.add_argument("--symbol")
    parser.add_argument("--cost-bps", type=float)
    parser.add_argument(
        "--prepare-only",
        type=Path,
        help="Build the full real-data signal/ranking/target/panel cache outside a run for preflight.",
    )
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
