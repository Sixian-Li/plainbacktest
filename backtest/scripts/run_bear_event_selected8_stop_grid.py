#!/usr/bin/env python3
"""Run the selected-eight SMA200 trailing-stop grid over hindsight bear events."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quantkit.bear_event_sma_portfolio import (
    WeightedTrailingSpec,
    build_weighted_trailing_event_plan,
    calculate_interval_returns,
    compound_returns,
    prepare_event_indicators,
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
from scripts.run_bear_event_sma_portfolios import (
    SPY_PATH,
    bear_session_mask,
    build_common_calendar,
    json_safe,
    load_candidate_frames,
    load_canonical,
    normalize_frame,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid"
)
SYMBOL_BLOCK = "BEAR_SELECTED8_STOP_GRID"
PORTFOLIO_TARGET = "PORTFOLIO_8"
EXPECTED_UNIVERSE = ("LMT", "EQT", "ORLY", "AZO", "TLT", "COR", "SO", "HRL")
EXPECTED_STOPS_PCT: tuple[float | None, ...] = (None, *tuple(float(value) for value in range(3, 21)))
LEDGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    target: str
    stop_id: str
    trailing_drawdown: float | None
    symbols: tuple[str, ...]
    slot_multipliers: dict[str, float]


def stop_identifier(value_pct: float | None) -> str:
    if value_pct is None:
        return "stop_off"
    value = float(value_pct)
    if not value.is_integer() or not 3 <= value <= 20:
        raise ValueError(f"unsupported trailing-stop threshold: {value_pct}")
    return f"stop_{int(value):02d}"


def build_case_specs(parameters: Mapping[str, Any]) -> list[CaseSpec]:
    universe = tuple(str(symbol) for symbol in parameters["universe"])
    if universe != EXPECTED_UNIVERSE or len(set(universe)) != len(universe):
        raise AssertionError("formal selected-eight universe changed")
    targets = tuple(str(target) for target in parameters["targets"])
    if targets != (*universe, PORTFOLIO_TARGET):
        raise AssertionError("formal target order changed")
    if str(parameters["portfolio_target"]) != PORTFOLIO_TARGET:
        raise AssertionError("portfolio target identifier changed")
    raw_stops = tuple(parameters["trailing_stop_drawdown_pct"])
    normalized_stops = tuple(None if value is None else float(value) for value in raw_stops)
    if normalized_stops != EXPECTED_STOPS_PCT:
        raise AssertionError("formal trailing-stop grid must be off plus every integer 3%-20%")

    cases: list[CaseSpec] = []
    for target in targets:
        symbols = universe if target == PORTFOLIO_TARGET else (target,)
        for value_pct in normalized_stops:
            stop_id = stop_identifier(value_pct)
            cases.append(
                CaseSpec(
                    case_id=f"{target}__{stop_id}",
                    target=target,
                    stop_id=stop_id,
                    trailing_drawdown=(None if value_pct is None else value_pct / 100.0),
                    symbols=symbols,
                    slot_multipliers={symbol: 1.0 for symbol in symbols},
                )
            )
    expected_count = int(parameters["formal_case_count_per_cost"])
    if len(cases) != expected_count or expected_count != 171:
        raise AssertionError("formal case count must be 171 per cost")
    return cases


def build_scope_summary(interval_returns: pd.DataFrame) -> pd.DataFrame:
    """Compound major, minor, all, and post-2000 event returns."""

    return_column = "total_return" if "total_return" in interval_returns else "interval_return"
    required = {"ordinal", "severity", return_column}
    if missing := required.difference(interval_returns.columns):
        raise ValueError(f"interval returns miss {sorted(missing)}")
    definitions = (
        ("major", interval_returns[interval_returns["severity"] == "major"]),
        ("minor", interval_returns[interval_returns["severity"] == "minor"]),
        ("all", interval_returns),
        ("all_ex_2000_2002", interval_returns[interval_returns["ordinal"].astype(int) != 1]),
    )
    rows: list[dict[str, Any]] = []
    for scope, subset in definitions:
        values = subset[return_column].astype(float)
        rows.append(
            {
                "scope": scope,
                "interval_count": int(len(subset)),
                "positive_interval_count": int((values > 0).sum()),
                "zero_interval_count": int(np.isclose(values, 0.0, rtol=0, atol=1e-14).sum()),
                "compound_return": compound_returns(values),
                "mean_return": float(values.mean()) if len(values) else np.nan,
                "worst_interval_return": float(values.min()) if len(values) else np.nan,
                "best_interval_return": float(values.max()) if len(values) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _tag(frame: pd.DataFrame, common: Mapping[str, Any]) -> pd.DataFrame:
    tagged = frame.copy()
    for column, value in reversed(list(common.items())):
        tagged.insert(0, column, value)
    return tagged


def _concat_or_empty(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_case_price_panel(
    price_panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    symbols: tuple[str, ...],
) -> pd.DataFrame:
    """Keep shared dates before listing as explicitly unavailable, never fabricated."""

    subset = price_panel[price_panel["symbol"].isin(symbols)].copy()
    subset["date"] = pd.to_datetime(subset["date"])
    index = pd.MultiIndex.from_product(
        [pd.DatetimeIndex(calendar), list(symbols)], names=["date", "symbol"]
    )
    panel = subset.set_index(["date", "symbol"]).reindex(index).reset_index()
    return panel.loc[:, list(price_panel.columns)]


def _position_matrix(
    positions: pd.DataFrame,
    dates: pd.DatetimeIndex,
    universe: tuple[str, ...],
) -> np.ndarray:
    matrix = positions.pivot(index="date", columns="symbol", values="shares")
    return (
        matrix.reindex(index=dates, columns=list(universe))
        .fillna(0.0)
        .to_numpy(dtype=np.float64)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    if args.symbol != SYMBOL_BLOCK or args.symbol not in context.config["symbols"]:
        raise ValueError(f"runner only accepts {SYMBOL_BLOCK}")
    configured_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if float(args.cost_bps) not in configured_costs:
        raise ValueError("cost scenario is not configured")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))
    parameters = context.config["parameters"]
    cases = build_case_specs(parameters)
    universe = EXPECTED_UNIVERSE

    candidates, source_paths = load_candidate_frames(list(universe))
    base_spec = WeightedTrailingSpec(
        sma_window=int(parameters["sma_window"]),
        entry_buffer=float(parameters["entry_buffer_pct"]) / 100.0,
        exit_buffer=float(parameters["exit_buffer_pct"]) / 100.0,
    )
    indicators = prepare_event_indicators(candidates, base_spec)
    interval_path = WORKSPACE_ROOT / parameters["bear_interval_source"]
    intervals = pd.DataFrame(json.loads(interval_path.read_text(encoding="utf-8"))["intervals"])
    if len(intervals) != 12 or intervals["severity"].value_counts().to_dict() != {
        "major": 6,
        "minor": 6,
    }:
        raise ValueError("formal event source must contain six major and six minor intervals")
    if int(intervals.sort_values("ordinal").iloc[0]["ordinal"]) != 1:
        raise ValueError("the 2000-2002 event must remain ordinal one")

    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    spy = load_canonical(SPY_PATH, "SPY")
    calendar, exclusions = build_common_calendar(spy, candidates, start=start, end=end)
    boundaries = set(pd.to_datetime(intervals["start"])).union(pd.to_datetime(intervals["end"]))
    if boundaries.difference(calendar):
        raise ValueError("common-calendar exclusions removed a formal bear boundary")
    price_panel = align_price_panel(candidates, calendar, analysis_start=start, analysis_end=end)
    policy = ExplicitFillPolicy("open", float(args.cost_bps))
    dates = pd.DatetimeIndex(calendar)

    metric_rows: list[dict[str, Any]] = []
    frames: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "orders",
            "trades",
            "signals",
            "transitions",
            "planner_executions",
            "target_shares",
            "interval_returns",
            "scope_summary",
            "interval_schedule",
            "holding_stats",
        )
    }
    daily_cash: list[np.ndarray] = []
    daily_equity: list[np.ndarray] = []
    daily_exposure: list[np.ndarray] = []
    position_shares: list[np.ndarray] = []
    differences: dict[str, float] = {}

    for index, case in enumerate(cases, start=1):
        case_panel = build_case_price_panel(price_panel, dates, case.symbols)
        case_indicators = indicators[indicators["symbol"].isin(case.symbols)].copy()
        spec = WeightedTrailingSpec(
            sma_window=int(parameters["sma_window"]),
            entry_buffer=float(parameters["entry_buffer_pct"]) / 100.0,
            exit_buffer=float(parameters["exit_buffer_pct"]) / 100.0,
            trailing_drawdown=case.trailing_drawdown,
        )
        plan = build_weighted_trailing_event_plan(
            case_panel,
            case_indicators,
            intervals,
            symbols=case.symbols,
            initial_cash=float(context.config["initial_cash"]),
            policy=policy,
            spec=spec,
            slot_multipliers=case.slot_multipliers,
        )
        muted = io.StringIO()
        with contextlib.redirect_stdout(muted), contextlib.redirect_stderr(muted):
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
        case_diff = cross_check_portfolios(
            result,
            positions,
            reference,
            tolerance=LEDGER_TOLERANCE,
            numerical_zero_notional=float(parameters["numerical_zero_order_notional_usd"]),
        )
        for key, value in case_diff.items():
            differences[f"{case.case_id}.{key}"] = float(value)
        assert_orders_match_policy(result.orders, case_panel, policy)

        actual = pybroker_daily_state(result).sort_values("date").reset_index(drop=True)
        if pd.DatetimeIndex(actual["date"]).tolist() != dates.tolist():
            raise AssertionError("daily account dates differ from the common calendar")
        orders = result.orders.reset_index().copy()
        trades = result.trades.reset_index().copy()
        interval_returns = calculate_interval_returns(actual, plan.interval_schedule)
        scope_summary = build_scope_summary(interval_returns)
        common = {
            "case_id": case.case_id,
            "target": case.target,
            "stop_id": case.stop_id,
            "trailing_drawdown_pct": (
                case.trailing_drawdown * 100.0 if case.trailing_drawdown is not None else np.nan
            ),
        }

        metrics = calculate_metrics(
            actual,
            orders,
            trades,
            initial_cash=float(context.config["initial_cash"]),
        )
        event_mask = bear_session_mask(actual["date"], plan.interval_schedule)
        event_daily = actual.loc[event_mask]
        metrics["average_bear_gross_exposure_pct"] = float(
            event_daily["gross_exposure"].mean() * 100.0
        )
        metrics["average_bear_cash_pct"] = float(
            (event_daily["cash"] / event_daily["equity"]).mean() * 100.0
        )
        metrics["forced_exit_count"] = int(
            (plan.transitions["reason"] == "forced_exit_peak_drawdown").sum()
        )
        metrics["sma_exit_count"] = int(
            (plan.transitions["reason"] == "exit_below_sma_minus_buffer").sum()
        )
        metrics["reentry_count"] = int(
            (plan.transitions["reason"] == "entry_cross_above_sma_plus_buffer").sum()
        )
        for row in scope_summary.itertuples(index=False):
            metrics[f"{row.scope}_bear_compound_return_pct"] = float(
                row.compound_return * 100.0
            )
            metrics[f"{row.scope}_bear_worst_interval_return_pct"] = float(
                row.worst_interval_return * 100.0
            )
            metrics[f"{row.scope}_bear_zero_interval_count"] = int(row.zero_interval_count)
        metric_rows.append({**common, **metrics})

        frames["orders"].append(_tag(orders, common))
        frames["trades"].append(_tag(trades, common))
        frames["signals"].append(_tag(plan.signals, common))
        frames["transitions"].append(_tag(plan.transitions, common))
        frames["planner_executions"].append(_tag(plan.executions, common))
        frames["target_shares"].append(_tag(plan.target_shares, common))
        frames["interval_returns"].append(_tag(interval_returns, common))
        frames["scope_summary"].append(_tag(scope_summary, common))
        frames["interval_schedule"].append(_tag(plan.interval_schedule, common))
        holding = positions.groupby("symbol", as_index=False).agg(
            calendar_session_count=("shares", "size"),
            held_session_count=("shares", lambda values: int((values.astype(float) > 1e-12).sum())),
        )
        holding["held_session_pct"] = (
            holding["held_session_count"] / holding["calendar_session_count"] * 100.0
        )
        frames["holding_stats"].append(_tag(holding, common))

        daily_cash.append(actual["cash"].to_numpy(dtype=np.float64))
        daily_equity.append(actual["equity"].to_numpy(dtype=np.float64))
        daily_exposure.append(actual["gross_exposure"].to_numpy(dtype=np.float64))
        position_shares.append(_position_matrix(positions, dates, universe))
        if index % 10 == 0 or index == len(cases):
            print(f"completed {index}/{len(cases)} cases at {args.cost_bps:g} bps")

    metrics_frame = pd.DataFrame(metric_rows)
    combined = {name: _concat_or_empty(items) for name, items in frames.items()}
    outputs = {
        "parameter_results.csv": metrics_frame,
        "metrics.csv": metrics_frame,
        **{f"{name}.csv": frame for name, frame in combined.items()},
        "calendar_exclusions.csv": exclusions,
        "case_index.csv": pd.DataFrame(
            {
                "case_index": range(len(cases)),
                "case_id": [case.case_id for case in cases],
                "target": [case.target for case in cases],
                "stop_id": [case.stop_id for case in cases],
                "trailing_drawdown_pct": [
                    np.nan if case.trailing_drawdown is None else case.trailing_drawdown * 100.0
                    for case in cases
                ],
            }
        ),
        "date_index.csv": pd.DataFrame({"date_index": range(len(dates)), "date": dates}),
        "symbol_index.csv": pd.DataFrame(
            {"symbol_index": range(len(universe)), "symbol": universe}
        ),
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    np.savez_compressed(
        output_root / "daily_state.npz",
        cash=np.stack(daily_cash),
        equity=np.stack(daily_equity),
        gross_exposure=np.stack(daily_exposure),
    )
    np.savez_compressed(
        output_root / "position_state.npz",
        shares=np.stack(position_shares),
    )

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "case_count": len(cases),
        "cases": json_safe(metric_rows),
        "scope_summary": json_safe(combined["scope_summary"].to_dict("records")),
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
        "signal_planner": "quantkit.bear_event_sma_portfolio.build_weighted_trailing_event_plan",
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(len(calendar)),
        "case_count": len(cases),
        "parameters": parameters,
        "signal_timing": "oracle bear boundary or in-bear completed Close",
        "execution_timing": "next common adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "case_metrics": json_safe(metric_rows),
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
    portfolio_all = combined["scope_summary"][
        (combined["scope_summary"]["target"] == PORTFOLIO_TARGET)
        & (combined["scope_summary"]["scope"] == "all")
    ]
    best = portfolio_all.loc[portfolio_all["compound_return"].idxmax()]
    print(
        f"Completed {len(cases)} selected-eight cases at {args.cost_bps:g} bps; "
        f"portfolio descriptive best={best.case_id} ({best.compound_return * 100:+.2f}%); "
        f"max ledger diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
