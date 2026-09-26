#!/usr/bin/env python3
"""Run the 2000–2015 QQQ StochRSI supergrid and heatmap surface inputs."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import multiprocessing
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, run_reference
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.run_intraday_sma_backtest import normalize_frame
from scripts.run_stochrsi_cross_period_grid import case_id, period_values
from scripts.run_stochrsi_multistart_robustness import formal_cases

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70a.9__26-08-26__qqq_stochrsi_supergrid_heatmaps_2000_2015"
_SCREEN_STATE: dict[str, Any] = {}


def build_windows(raw: pd.DataFrame, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = pd.DatetimeIndex(raw["date"])
    first = pd.Timestamp(parameters["research_start"])
    windows: list[dict[str, Any]] = []
    for years in map(int, parameters["horizons_years"]):
        last = pd.Timestamp(parameters["horizon_last_start"][str(years)])
        for quarter in pd.period_range(first, last, freq="Q"):
            candidates = sessions[(sessions >= quarter.start_time) & (sessions <= quarter.end_time)]
            if candidates.empty:
                raise RuntimeError(f"No session in {quarter}")
            start = candidates[0]
            eligible = sessions[sessions < start + pd.DateOffset(years=years)]
            if eligible.empty:
                raise RuntimeError(f"No end for {years}Y {quarter}")
            windows.append({
                "cohort": f"H{years}", "horizon_years": years,
                "window_id": f"H{years}_{quarter.year}Q{quarter.quarter}",
                "start": start, "end": eligible[-1],
            })
        actual = sum(w["horizon_years"] == years for w in windows)
        expected = int(parameters["expected_windows"][str(years)])
        if actual != expected:
            raise RuntimeError(f"Expected {expected} {years}Y windows, got {actual}")
    return windows


def aligned_buy_hold(
    daily: pd.DataFrame, orders: pd.DataFrame, *, initial_cash: float
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Cash until the strategy's first buy, then all-in at its exact fill price."""
    benchmark = daily[["date", "symbol", "close"]].copy()
    initial_cash = float(initial_cash)
    benchmark["cash"] = initial_cash
    benchmark["shares"] = 0.0
    benchmark["equity"] = initial_cash
    benchmark["is_long"] = 0
    buys = orders[orders["type"].eq("buy")] if not orders.empty else orders
    if buys.empty:
        metrics = calculate_metrics(benchmark, pd.DataFrame(), pd.DataFrame(), initial_cash=initial_cash)
        return benchmark, pd.DataFrame(), metrics
    first = buys.iloc[0]
    entry_date, entry_fill = pd.Timestamp(first["date"]), float(first["fill_price"])
    shares = initial_cash / entry_fill
    active = benchmark["date"].ge(entry_date)
    benchmark.loc[active, "cash"] = 0.0
    benchmark.loc[active, "shares"] = shares
    benchmark.loc[active, "equity"] = benchmark.loc[active, "close"].astype(float) * shares
    benchmark.loc[active, "is_long"] = 1
    benchmark_orders = pd.DataFrame([{
        "symbol": str(first["symbol"]), "type": "buy", "date": entry_date,
        "shares": shares, "fill_price": entry_fill,
        "raw_fill_price": float(first.get("raw_fill_price", entry_fill)),
        "primary_signal": "ALIGNED_BUY_HOLD_ENTRY",
        "theoretical_trigger": float(first.get("theoretical_trigger", entry_fill)),
        "fill_source": "strategy_first_buy_fill",
    }])
    metrics = calculate_metrics(benchmark, benchmark_orders, pd.DataFrame(), initial_cash=initial_cash)
    return benchmark, benchmark_orders, metrics


def aligned_benchmark_summary(
    daily: pd.DataFrame, orders: pd.DataFrame, *, initial_cash: float
) -> dict[str, float]:
    """Compute only the three aligned-benchmark fields used by the supergrid."""
    initial_cash = float(initial_cash)
    equity = np.full(len(daily), initial_cash, dtype=float)
    buys = orders[orders["type"].eq("buy")] if not orders.empty else orders
    if not buys.empty:
        first = buys.iloc[0]
        active = daily["date"].ge(pd.Timestamp(first["date"])).to_numpy()
        shares = initial_cash / float(first["fill_price"])
        equity[active] = daily.loc[active, "close"].to_numpy(float) * shares
    returns = np.zeros(len(equity), dtype=float)
    if len(equity) > 1:
        returns[1:] = equity[1:] / equity[:-1] - 1.0
    elapsed_days = max((pd.Timestamp(daily.iloc[-1]["date"]) - pd.Timestamp(daily.iloc[0]["date"])).days, 1)
    years = elapsed_days / 365.2425
    cagr = (equity[-1] / initial_cash) ** (1.0 / years) - 1.0
    volatility = returns.std(ddof=1) if len(returns) > 1 else 0.0
    sharpe = returns.mean() / volatility * math.sqrt(252) if volatility > 0 else float("nan")
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    return {"cagr_pct": cagr * 100.0, "sharpe": float(sharpe), "max_drawdown_pct": float(drawdown.min() * 100.0)}


def configure_screen(raw: pd.DataFrame, windows: list[dict[str, Any]], parameters: dict[str, Any], initial_cash: float) -> None:
    global _SCREEN_STATE
    _SCREEN_STATE = {"raw": raw, "windows": windows, "parameters": parameters, "initial_cash": initial_cash}


def screen_worker(pair: tuple[int, int]) -> list[dict[str, Any]]:
    short, long = pair
    state, p = _SCREEN_STATE, _SCREEN_STATE["parameters"]
    spec = TimingSpec("CROSS", cost_bps=float(p["screen_cost_bps"]), periods=(short, long), buy_threshold=float(p["buy_threshold"]), sell_threshold=float(p["sell_threshold"]))
    prepared = prepare_dual_stochrsi_data(state["raw"], spec)
    rows = []
    for window in state["windows"]:
        reference = run_reference(prepared, spec, analysis_start=window["start"], analysis_end=window["end"], initial_cash=state["initial_cash"])
        metrics = calculate_metrics(reference.daily, reference.orders, reference.trades, initial_cash=state["initial_cash"])
        bh = aligned_benchmark_summary(reference.daily, reference.orders, initial_cash=state["initial_cash"])
        buys = reference.orders[reference.orders["type"].eq("buy")] if not reference.orders.empty else reference.orders
        rows.append({
            "case_id": case_id(short, long), "short_period": short, "long_period": long,
            "cohort": window["cohort"], "window_id": window["window_id"],
            "window_start": window["start"].date().isoformat(), "window_end": window["end"].date().isoformat(),
            "first_entry_date": None if buys.empty else pd.Timestamp(buys.iloc[0]["date"]).date().isoformat(),
            **metrics,
            "aligned_bh_cagr_pct": bh["cagr_pct"], "aligned_bh_sharpe": bh["sharpe"],
            "aligned_bh_max_drawdown_pct": bh["max_drawdown_pct"],
        })
    return rows


def score_surface(screen: pd.DataFrame, p: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    d = screen.copy()
    d["start_year"] = pd.to_datetime(d["window_start"]).dt.year
    d["horizon_years"] = d["cohort"].str.removeprefix("H").astype(int)
    d["cagr_rank"] = d.groupby("window_id")["cagr_pct"].rank(pct=True, method="average")
    d["sharpe_rank"] = d.groupby("window_id")["sharpe"].rank(pct=True, method="average")
    d["joint_rank"] = d[["cagr_rank", "sharpe_rank"]].min(axis=1)
    blocks = p["chronological_blocks"]
    d["time_block"] = [next(f"{a}_{b}" for a, b in blocks if a <= year <= b) for year in d["start_year"]]
    records = []
    for keys, group in d.groupby(["case_id", "short_period", "long_period"], sort=False):
        horizon_q25 = group.groupby("horizon_years")["joint_rank"].quantile(.25)
        block_median = group.groupby(["horizon_years", "time_block"])["joint_rank"].median()
        loo = [group.loc[group["start_year"].ne(year), "joint_rank"].quantile(.25) for year in sorted(group["start_year"].unique())]
        records.append({
            "case_id": keys[0], "short_period": keys[1], "long_period": keys[2],
            "worst_horizon_q25_joint_rank": horizon_q25.min(),
            "worst_time_block_median_joint_rank": block_median.min(),
            "leave_one_year_out_q25_joint_rank": min(loo),
            "cagr_win_rate_vs_aligned_buy_hold": (group["cagr_pct"] >= group["aligned_bh_cagr_pct"]).mean(),
            "sharpe_win_rate_vs_aligned_buy_hold": (group["sharpe"] >= group["aligned_bh_sharpe"]).mean(),
            "drawdown_win_rate_vs_aligned_buy_hold": (group["max_drawdown_pct"] >= group["aligned_bh_max_drawdown_pct"]).mean(),
            "median_cagr_pct": group["cagr_pct"].median(), "q25_cagr_pct": group["cagr_pct"].quantile(.25),
            "median_sharpe": group["sharpe"].median(), "q25_sharpe": group["sharpe"].quantile(.25),
        })
    surface = pd.DataFrame(records)
    lookup = {(r.short_period, r.long_period): min(r.worst_horizon_q25_joint_rank, r.worst_time_block_median_joint_rank, r.leave_one_year_out_q25_joint_rank) for r in surface.itertuples()}
    step = int(p["period_step"])
    surface["worst_local_3x3_score"] = [
        np.nan if any((r.short_period + di*step, r.long_period + dj*step) not in lookup for di in (-1,0,1) for dj in (-1,0,1))
        else min(lookup[(r.short_period + di*step, r.long_period + dj*step)] for di in (-1,0,1) for dj in (-1,0,1))
        for r in surface.itertuples()
    ]
    stability = ["worst_horizon_q25_joint_rank", "worst_time_block_median_joint_rank", "leave_one_year_out_q25_joint_rank", "worst_local_3x3_score"]
    surface["maximin_score"] = surface[stability].min(axis=1)
    gates = p["gates"]
    specs = [
        ("worst_horizon_q25_joint_rank", "minimum_worst_horizon_q25_joint_rank"),
        ("worst_time_block_median_joint_rank", "minimum_worst_time_block_median_joint_rank"),
        ("leave_one_year_out_q25_joint_rank", "minimum_leave_one_start_year_out_q25_joint_rank"),
        ("worst_local_3x3_score", "minimum_worst_local_3x3_score"),
        ("cagr_win_rate_vs_aligned_buy_hold", "minimum_cagr_win_rate_vs_aligned_buy_hold"),
        ("sharpe_win_rate_vs_aligned_buy_hold", "minimum_sharpe_win_rate_vs_aligned_buy_hold"),
        ("drawdown_win_rate_vs_aligned_buy_hold", "minimum_drawdown_win_rate_vs_aligned_buy_hold"),
    ]
    checks = pd.concat([surface[column].ge(float(gates[key])) for column, key in specs], axis=1)
    surface["gate_pass_count"] = checks.sum(axis=1)
    surface["passes_all_gates"] = surface["worst_local_3x3_score"].notna() & checks.all(axis=1)
    order = ["maximin_score", "q25_cagr_pct", "median_cagr_pct", "sharpe_win_rate_vs_aligned_buy_hold", "cagr_win_rate_vs_aligned_buy_hold", "case_id"]
    ascending = [False, False, False, False, False, True]
    passing = surface[surface["passes_all_gates"]].sort_values(order, ascending=ascending)
    champion = passing.iloc[0].to_dict() if not passing.empty else None
    interior = surface[surface["worst_local_3x3_score"].notna()]
    near = interior.sort_values(["gate_pass_count", *order], ascending=[False, *ascending]).iloc[0].to_dict()
    return surface, {"robust_champion": json_safe(champion), "strongest_near_miss": json_safe(near), "passing_case_count": int(len(passing))}


def formal_aligned_benchmarks(raw: pd.DataFrame, windows: list[dict[str, Any]], case_ids: list[str], p: dict[str, Any], initial_cash: float, cost_bps: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, order_frames = [], []
    for current_id in case_ids:
        short, long = map(int, current_id.removeprefix("P").split("_"))
        spec = TimingSpec("CROSS", cost_bps=cost_bps, periods=(short, long), buy_threshold=float(p["buy_threshold"]), sell_threshold=float(p["sell_threshold"]))
        prepared = prepare_dual_stochrsi_data(raw, spec)
        for window in windows:
            reference = run_reference(prepared, spec, analysis_start=window["start"], analysis_end=window["end"], initial_cash=initial_cash)
            _, orders, metrics = aligned_buy_hold(reference.daily, reference.orders, initial_cash=initial_cash)
            rows.append({"case_id": current_id, "cohort": window["cohort"], "window_id": window["window_id"], "horizon_years": window["horizon_years"], "window_start": window["start"].date().isoformat(), "window_end": window["end"].date().isoformat(), **metrics})
            if not orders.empty:
                tagged = orders.copy(); tagged.insert(0, "case_id", current_id); tagged.insert(1, "window_id", window["window_id"]); order_frames.append(tagged)
    return pd.DataFrame(rows), pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT); parser.add_argument("--run-id", required=True); parser.add_argument("--cost-bps", type=float, required=True); parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1)); args = parser.parse_args()
    context = load_experiment(args.experiment); cost_bps = float(args.cost_bps)
    if cost_bps not in {float(value) for value in context.config["cost_scenarios_bps_per_side"]}: raise ValueError("Cost is not frozen in experiment.json")
    output = reserve_block(context, args.run_id, "QQQ", cost_bps); p = context.config["parameters"]; initial_cash = float(context.config["initial_cash"])
    source = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"; manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"; canonical_manifest = json.loads(manifest_path.read_text()); dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved": raise RuntimeError("QQQ data is not approved")
    raw = pd.read_csv(source, parse_dates=["date"]); raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    windows = build_windows(raw, p); started = time.perf_counter(); selection_path = context.run_root(args.run_id) / "QQQ/cost_5bps/selection.json"
    if cost_bps == 5.:
        pairs = [(short, long) for short in period_values(p["short_period_start"], p["short_period_end"], p["period_step"]) for long in period_values(p["long_period_start"], p["long_period_end"], p["period_step"]) if short < long]
        if len(pairs) != int(p["combination_count"]): raise RuntimeError("Grid count mismatch")
        configure_screen(raw, windows, p, initial_cash); executor = concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("fork")) if args.workers > 1 else None
        try: screen = pd.DataFrame([row for batch in (executor.map(screen_worker, pairs, chunksize=1) if executor else map(screen_worker, pairs)) for row in batch])
        finally:
            if executor: executor.shutdown(wait=True, cancel_futures=True)
        surface, selection = score_surface(screen, p)
        normalize_frame(screen).to_csv(output / "screen.csv", index=False, lineterminator="\n"); normalize_frame(surface).to_csv(output / "surface.csv", index=False, lineterminator="\n"); selection_path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n")
    else:
        if not selection_path.exists(): raise RuntimeError("Run 5 bps selection before cost checks")
        selection = json.loads(selection_path.read_text())
    selected = str((selection["robust_champion"] or selection["strongest_near_miss"])["case_id"])
    ids = list(dict.fromkeys([case_id(int(pair[0]), int(pair[1])) for pair in p["reference_pairs"]] + [selected]))
    formal, orders, trades, differences = formal_cases(raw, windows, ids, p, initial_cash, cost_bps)
    aligned_results, aligned_orders = formal_aligned_benchmarks(raw, windows, ids, p, initial_cash, cost_bps)
    normalize_frame(formal).to_csv(output / "formal_results.csv", index=False, lineterminator="\n"); normalize_frame(pd.concat(orders, ignore_index=True) if orders else pd.DataFrame()).to_csv(output / "formal_orders.csv", index=False, lineterminator="\n"); normalize_frame(pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()).to_csv(output / "formal_trades.csv", index=False, lineterminator="\n"); normalize_frame(aligned_results).to_csv(output / "aligned_buy_hold_results.csv", index=False, lineterminator="\n"); normalize_frame(aligned_orders).to_csv(output / "aligned_buy_hold_orders.csv", index=False, lineterminator="\n")
    summary = {"training_window_count": len(windows), "screen_case_windows": len(windows) * int(p["combination_count"]) if cost_bps == 5 else 0, "selected_case_ids": ids, "selection": selection, "benchmark_semantics": "cash_until_candidate_first_buy_then_same_fill_and_hold", "max_cross_check_differences": differences, "elapsed_seconds": time.perf_counter() - started}
    (output / "metrics.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2) + "\n")
    manifest = {"schema_version": 1, "experiment_id": context.config["experiment_id"], "experiment_run_id": args.run_id, "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "symbol": "QQQ", "cost_bps": cost_bps, "python": platform.python_version(), "source_file_sha256": sha256(source), "source_manifest_build_id": canonical_manifest["build_id"], "source_manifest_entry": dataset, "summary": summary, "artifacts": {}}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "manifest.json": manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_file = output / "manifest.json"; manifest_file.write_text(json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n"); record_block_complete(context, args.run_id, "QQQ", cost_bps, manifest_file); print(json.dumps({"cost_bps": cost_bps, "seconds": summary["elapsed_seconds"], "selected": selected, "passing": selection["passing_case_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
