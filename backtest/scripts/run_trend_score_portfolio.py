#!/usr/bin/env python3
"""Run the 21-candidate unified trend-score portfolio for one cost block."""

from __future__ import annotations

import argparse
import io
import json
import platform
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy, assert_orders_match_policy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from quantkit.trend_score_portfolio import (
    CASE_IDS,
    TrendScoreSpec,
    align_price_panel,
    build_breadth_series,
    build_target_share_table,
    build_weekly_targets,
    cross_check_portfolios,
    prepare_signal_table,
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio"
SP500_CURRENT = WORKSPACE_ROOT / "data/processed/universes/sp500/current_constituents.csv"
SP500_PRICE_DIR = WORKSPACE_ROOT / "data/processed/daily/equities"
NASDAQ_ARCHIVE = WORKSPACE_ROOT / (
    "data/2026-08-05多个数据包_rethink/纳斯达克 100 成分股/"
    "纳斯达克 100 成分股-拆股及股息调整_20260805.zip"
)
TLT_PATH = WORKSPACE_ROOT / (
    "data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/国债系列 ETF/"
    "TLT - 美国 20 年以上国债 ETF iShares/TLT_1day_拆股股息调整_20260805.csv"
)
GLD_PATH = WORKSPACE_ROOT / (
    "data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/核心商品 ETF/"
    "GLD - 黄金 ETF SPDR/GLD_1day_拆股股息调整_20260805.csv"
)
SPY_PATH = WORKSPACE_ROOT / "data/processed/daily/SPY.csv"
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
LEDGER_TOLERANCE = 1e-6


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "true", "yes"}


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


def load_canonical(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame = frame[frame["symbol"] == symbol].copy()
    return frame.sort_values("date").reset_index(drop=True)


def load_vendor(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame = frame.rename(columns={name: name.lower() for name in frame.columns})
    frame["date"] = pd.to_datetime(frame["date"])
    frame["symbol"] = symbol
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def load_candidate_frames(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], dict[str, Path]]:
    frames: dict[str, pd.DataFrame] = {}
    sources: dict[str, Path] = {}
    for symbol in symbols:
        if symbol == "TLT":
            path = TLT_PATH
            frames[symbol] = load_vendor(path, symbol)
        elif symbol == "GLD":
            path = GLD_PATH
            frames[symbol] = load_vendor(path, symbol)
        else:
            path = SP500_PRICE_DIR / f"{symbol}.csv"
            frames[symbol] = load_canonical(path, symbol)
        sources[symbol] = path
    return frames, sources


def load_breadth_prices(end: pd.Timestamp) -> tuple[dict[str, pd.Series], dict[str, Any]]:
    official = pd.read_csv(SP500_CURRENT, dtype=str).fillna("")
    prices: dict[str, pd.Series] = {}
    for row in official.to_dict("records"):
        if not truthy(row["vendor_price_available"]):
            continue
        asset_id = row["vendor_instrument_id"] or row["symbol"]
        path = SP500_PRICE_DIR / f"{asset_id}.csv"
        frame = pd.read_csv(path, usecols=["date", "close"], parse_dates=["date"])
        series = frame.loc[frame["date"] <= end].set_index("date")["close"].astype(float)
        prices[asset_id] = series
    nasdaq_current = 0
    overlap = 0
    with zipfile.ZipFile(NASDAQ_ARCHIVE) as archive:
        for name in sorted(item for item in archive.namelist() if item.lower().endswith(".csv")):
            with archive.open(name) as raw:
                frame = pd.read_csv(io.TextIOWrapper(raw, encoding="utf-8-sig"), usecols=["Date", "Close", "InIndex"])
            frame["Date"] = pd.to_datetime(frame["Date"])
            frame = frame[frame["Date"] <= end]
            snapshot = frame[frame["Date"] == end]
            if snapshot.empty or len(snapshot) != 1 or not truthy(snapshot.iloc[0]["InIndex"]):
                continue
            nasdaq_current += 1
            asset_id = Path(name).stem
            if asset_id in prices:
                overlap += 1
                continue
            prices[asset_id] = frame.set_index("Date")["Close"].astype(float)
    metadata = {
        "sp500_price_available": int(len([row for row in official.to_dict('records') if truthy(row['vendor_price_available'])])),
        "nasdaq100_current": nasdaq_current,
        "overlap": overlap,
        "union": len(prices),
    }
    return prices, metadata


def benchmark_daily(spy: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, initial_cash: float) -> pd.DataFrame:
    data = spy[(spy["date"] >= start) & (spy["date"] <= end)].copy()
    shares = initial_cash / float(data.iloc[0]["open"])
    return pd.DataFrame(
        {
            "date": data["date"],
            "cash": 0.0,
            "shares": shares,
            "equity": shares * data["close"].astype(float),
            "is_long": 1,
            "gross_exposure": 1.0,
        }
    )


def interval_returns(daily: pd.DataFrame, intervals_path: Path, cost_bps: float) -> pd.DataFrame:
    payload = json.loads(intervals_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for case_id, frame in daily.groupby("case_id"):
        series = frame.sort_values("date").set_index("date")["equity"].astype(float)
        for interval in payload["intervals"]:
            start = pd.Timestamp(interval["start"])
            end = pd.Timestamp(interval["end"])
            available = series.loc[(series.index >= start) & (series.index <= end)]
            if available.empty or start not in series.index or end not in series.index:
                total_return = np.nan
            else:
                total_return = float(series.loc[end] / series.loc[start] - 1.0)
            rows.append(
                {
                    "cost_bps": cost_bps,
                    "case_id": case_id,
                    "interval_id": interval["interval_id"],
                    "label": interval["label"],
                    "severity": interval["severity"],
                    "start": start,
                    "end": end,
                    "total_return": total_return,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "RESILIENCE_21" or args.symbol not in context.config["symbols"]:
        raise ValueError("This runner only accepts the RESILIENCE_21 portfolio block")
    if float(args.cost_bps) not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError("Cost scenario is not configured")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    parameters = context.config["parameters"]
    spec = TrendScoreSpec.from_parameters(parameters)
    start = pd.Timestamp(parameters["analysis_start"])
    end = pd.Timestamp(parameters["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    candidate_symbols = list(parameters["candidate_symbols"])
    candidates, candidate_sources = load_candidate_frames(candidate_symbols)
    spy = load_canonical(SPY_PATH, "SPY")
    qqq = load_canonical(QQQ_PATH, "QQQ")
    if spy.iloc[-1]["date"] != end or qqq.iloc[-1]["date"] != end:
        raise ValueError("SPY/QQQ approved data does not end on configured analysis_end")

    breadth_prices, breadth_metadata = load_breadth_prices(end)
    breadth = build_breadth_series(
        breadth_prices,
        sma_window=spec.sma_window,
        minimum_assets=spec.minimum_breadth_assets,
    )
    signals = prepare_signal_table(
        candidates,
        spy,
        qqq,
        breadth,
        spec,
        defensive_assets=parameters["defensive_assets"],
    )
    calendar = spy.loc[(spy["date"] >= start) & (spy["date"] <= end), "date"]
    targets = build_weekly_targets(
        signals,
        calendar,
        spec,
        analysis_start=start,
        analysis_end=end,
        case_ids=parameters["formal_cases"],
    )
    panel = align_price_panel(
        candidates,
        calendar,
        analysis_start=start,
        analysis_end=end,
    )
    policy = ExplicitFillPolicy("open", float(args.cost_bps))
    daily_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    target_share_frames: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    differences: dict[str, float] = {}
    for case_id in parameters["formal_cases"]:
        target_shares = build_target_share_table(
            panel,
            targets,
            case_id=case_id,
            initial_cash=initial_cash,
            policy=policy,
        )
        result, positions = run_pybroker_portfolio(
            panel,
            target_shares,
            initial_cash=initial_cash,
            policy=policy,
        )
        reference = run_reference_portfolio(
            panel,
            target_shares,
            initial_cash=initial_cash,
            policy=policy,
        )
        case_differences = cross_check_portfolios(
            result, positions, reference, tolerance=LEDGER_TOLERANCE
        )
        for key, value in case_differences.items():
            differences[f"{case_id}.{key}"] = value
        assert_orders_match_policy(result.orders, panel, policy)
        actual = pybroker_daily_state(result)
        actual.insert(0, "case_id", case_id)
        actual_positions = positions.copy()
        actual_positions.insert(0, "case_id", case_id)
        orders = result.orders.reset_index().copy()
        orders.insert(0, "case_id", case_id)
        trades = result.trades.reset_index().copy()
        trades.insert(0, "case_id", case_id)
        target_shares.insert(0, "case_id", case_id)
        metrics = calculate_metrics(
            actual,
            orders,
            trades,
            initial_cash=initial_cash,
        )
        metrics["average_gross_exposure_pct"] = float(actual["gross_exposure"].mean() * 100.0)
        metrics["average_cash_pct"] = float((actual["cash"] / actual["equity"]).mean() * 100.0)
        metric_rows.append({"case_id": case_id, **metrics})
        daily_frames.append(actual)
        position_frames.append(actual_positions)
        order_frames.append(orders)
        trade_frames.append(trades)
        target_share_frames.append(target_shares)

    daily = pd.concat(daily_frames, ignore_index=True)
    positions = pd.concat(position_frames, ignore_index=True)
    orders = pd.concat(order_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    target_shares = pd.concat(target_share_frames, ignore_index=True)
    metric_frame = pd.DataFrame(metric_rows)
    benchmark = benchmark_daily(spy, start, end, initial_cash)
    benchmark_metrics = calculate_metrics(
        benchmark,
        pd.DataFrame(columns=["shares", "fill_price"]),
        pd.DataFrame(columns=["pnl"]),
        initial_cash=initial_cash,
    )
    bear_returns = interval_returns(
        daily,
        WORKSPACE_ROOT / parameters["bear_interval_source"],
        float(args.cost_bps),
    )
    current_scores = targets[targets["date"] == targets["date"].max()].copy()
    current_scores["cost_bps"] = float(args.cost_bps)

    outputs = {
        "metrics.csv": metric_frame,
        "daily.csv": daily,
        "positions.csv": positions,
        "orders.csv": orders,
        "trades.csv": trades,
        "signals.csv": signals,
        "weekly_targets.csv": targets,
        "target_shares.csv": target_shares,
        "breadth.csv": breadth,
        "bear_interval_returns.csv": bear_returns,
        "current_scores.csv": current_scores,
        "spy_buy_hold_daily.csv": benchmark,
    }
    for name, frame in outputs.items():
        normalize_frame(frame).to_csv(output_root / name, index=False, lineterminator="\n")

    metrics_payload = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "symbol": args.symbol,
        "cost_bps": float(args.cost_bps),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "cases": json_safe(metric_rows),
        "benchmark": json_safe(benchmark_metrics),
        "breadth_universe": breadth_metadata,
        "max_cross_check_differences": differences,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    source_files = {
        str(path.relative_to(WORKSPACE_ROOT)): sha256(path)
        for path in [
            *candidate_sources.values(),
            SPY_PATH,
            QQQ_PATH,
            SP500_CURRENT,
            NASDAQ_ARCHIVE,
        ]
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
        "python": platform.python_version(),
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": int(calendar.size),
        "parameters": parameters,
        "signal_timing": "weekly last common session completed Close",
        "execution_timing": "next common regular-session adjusted Open; sells before buys",
        "fractional_shares": True,
        "cash_interest": 0.0,
        "benchmark": context.config["benchmark"],
        "case_metrics": json_safe(metric_rows),
        "benchmark_metrics": json_safe(benchmark_metrics),
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
        context, args.run_id, args.symbol, float(args.cost_bps), manifest_path
    )
    primary = metric_frame.set_index("case_id").loc[parameters["primary_case"]]
    print(
        f"Completed {len(parameters['formal_cases'])} cases at {args.cost_bps:g} bps; "
        f"primary CAGR={primary['cagr_pct']:.3f}% maxDD={primary['max_drawdown_pct']:.3f}%; "
        f"max ledger diff={max(differences.values()):.3g}"
    )


if __name__ == "__main__":
    main()
