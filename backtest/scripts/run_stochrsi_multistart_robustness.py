#!/usr/bin/env python3
"""Run deterministic five-year cash-restart robustness for the QQQ StochRSI period grid."""

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

import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, run_reference
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from scripts.analyze_stochrsi_cross_threshold_grid import connected_components, json_safe
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import normalize_frame
from scripts.run_stochrsi_cross_period_grid import case_id, period_values
from scripts.run_stochrsi_cross_threshold_grid import run_case


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70a.3__26-08-25__qqq_stochrsi_multistart_robustness"
_SCREEN_STATE: dict[str, Any] = {}


def quarterly_windows(raw: pd.DataFrame, parameters: dict[str, Any], cohort: str) -> list[dict[str, Any]]:
    first = pd.Timestamp(parameters[f"{cohort}_start_first"])
    last = pd.Timestamp(parameters[f"{cohort}_start_last"])
    years = int(parameters["window_years"])
    sessions = pd.DatetimeIndex(raw["date"])
    quarters = pd.period_range(first, last, freq="Q")
    windows = []
    for quarter in quarters:
        candidates = sessions[(sessions >= quarter.start_time) & (sessions <= quarter.end_time)]
        if candidates.empty:
            raise RuntimeError(f"No session in {quarter}")
        start = candidates[0]
        anniversary = start + pd.DateOffset(years=years)
        eligible_end = sessions[sessions < anniversary]
        if eligible_end.empty:
            raise RuntimeError(f"No end session for {quarter}")
        end = eligible_end[-1]
        windows.append({
            "cohort": cohort,
            "window_id": f"{cohort[:2].upper()}_{quarter.year}Q{quarter.quarter}",
            "start": start,
            "end": end,
        })
    expected = int(parameters[f"expected_{cohort}_windows"])
    if len(windows) != expected:
        raise RuntimeError(f"Expected {expected} {cohort} windows, got {len(windows)}")
    return windows


def configure_screen(raw: pd.DataFrame, windows: list[dict[str, Any]], parameters: dict[str, Any], initial_cash: float) -> None:
    global _SCREEN_STATE
    _SCREEN_STATE = {"raw": raw, "windows": windows, "parameters": parameters, "initial_cash": initial_cash}


def screen_worker(pair: tuple[int, int]) -> list[dict[str, Any]]:
    short, long = pair
    state = _SCREEN_STATE
    parameters = state["parameters"]
    spec = TimingSpec(
        "CROSS", cost_bps=float(parameters["screen_cost_bps"]), periods=(short, long),
        buy_threshold=float(parameters["buy_threshold"]), sell_threshold=float(parameters["sell_threshold"]),
    )
    prepared = prepare_dual_stochrsi_data(state["raw"], spec)
    rows = []
    for window in state["windows"]:
        reference = run_reference(
            prepared, spec, analysis_start=window["start"], analysis_end=window["end"],
            initial_cash=state["initial_cash"],
        )
        metrics = calculate_metrics(
            reference.daily, reference.orders, reference.trades, initial_cash=state["initial_cash"]
        )
        rows.append({
            "case_id": case_id(short, long), "short_period": short, "long_period": long,
            "cohort": window["cohort"], "window_id": window["window_id"],
            "window_start": window["start"].date().isoformat(), "window_end": window["end"].date().isoformat(),
            **metrics,
        })
    return rows


def select_training(screen: pd.DataFrame, parameters: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    scored = screen.copy()
    scored["cagr_rank_pct"] = scored.groupby("window_id")["cagr_pct"].rank(pct=True, method="average")
    scored["sharpe_rank_pct"] = scored.groupby("window_id")["sharpe"].rank(pct=True, method="average")
    scored["joint_rank_pct"] = scored[["cagr_rank_pct", "sharpe_rank_pct"]].min(axis=1)
    surface = scored.groupby(["case_id", "short_period", "long_period"], as_index=False).agg(
        robust_score=("joint_rank_pct", lambda values: float(values.quantile(0.25))),
        median_joint_rank=("joint_rank_pct", "median"),
        q25_cagr_pct=("cagr_pct", lambda values: float(values.quantile(0.25))),
        median_cagr_pct=("cagr_pct", "median"),
        q25_sharpe=("sharpe", lambda values: float(values.quantile(0.25))),
        median_sharpe=("sharpe", "median"),
        median_max_drawdown_pct=("max_drawdown_pct", "median"),
        median_closed_trades=("closed_trade_count", "median"),
    )
    best = surface.sort_values(
        ["robust_score", "median_joint_rank", "q25_cagr_pct", "q25_sharpe", "case_id"],
        ascending=[False, False, False, False, True],
    ).iloc[0]
    maximum = float(best["robust_score"])
    high = surface[surface["robust_score"].ge(0.95 * maximum)].copy()
    short_start, long_start, step = (
        int(parameters["short_period_start"]), int(parameters["long_period_start"]), int(parameters["period_step"])
    )
    short_last = (int(parameters["short_period_end"]) - short_start) // step
    long_last = (int(parameters["long_period_end"]) - long_start) // step
    points = {
        ((int(row.short_period) - short_start) // step, (int(row.long_period) - long_start) // step)
        for row in high.itertuples()
    }
    components = connected_components(points)
    records, qualifying = [], []
    for component in components:
        touches = any(i in {0, short_last} or j in {0, long_last} for i, j in component)
        qualifies = len(component) >= 9 and not touches
        if qualifies:
            qualifying.append(component)
        records.append({
            "size": len(component), "touches_boundary": touches, "qualifies": qualifies,
            "short_min": short_start + min(i for i, _ in component) * step,
            "short_max": short_start + max(i for i, _ in component) * step,
            "long_min": long_start + min(j for _, j in component) * step,
            "long_max": long_start + max(j for _, j in component) * step,
        })
    champion = None
    if qualifying:
        largest_size = max(map(len, qualifying))
        candidates = []
        for component_index, component in enumerate(qualifying):
            if len(component) != largest_size:
                continue
            ci = sum(i for i, _ in component) / len(component)
            cj = sum(j for _, j in component) / len(component)
            for i, j in component:
                short, long = short_start + i * step, long_start + j * step
                row = surface[surface["short_period"].eq(short) & surface["long_period"].eq(long)].iloc[0]
                candidates.append((float(row["robust_score"]), -math.hypot(i-ci, j-cj), str(row["case_id"]), component_index, row))
        chosen = sorted(candidates, key=lambda item: (-item[0], -item[1], item[2]))[0]
        champion = {**json_safe(chosen[4].to_dict()), "component_index": chosen[3], "component_size": largest_size}
    selection = {
        "mechanical_max": json_safe(best.to_dict()),
        "maximum_robust_score": maximum,
        "high_performance_case_count": len(high),
        "component_count": len(components),
        "components": records,
        "robust_champion": champion,
    }
    return surface, selection


def selected_case_ids(selection: dict[str, Any], parameters: dict[str, Any]) -> list[str]:
    ids = [
        case_id(int(parameters["anchor_short_period"]), int(parameters["anchor_long_period"])),
        case_id(int(parameters["full_sample_max_cagr_short_period"]), int(parameters["full_sample_max_cagr_long_period"])),
        case_id(int(parameters["full_sample_max_sharpe_short_period"]), int(parameters["full_sample_max_sharpe_long_period"])),
        str(selection["mechanical_max"]["case_id"]),
    ]
    if selection["robust_champion"] is not None:
        ids.append(str(selection["robust_champion"]["case_id"]))
    return list(dict.fromkeys(ids))


def formal_cases(raw: pd.DataFrame, windows: list[dict[str, Any]], ids: list[str], parameters: dict[str, Any], initial_cash: float, cost_bps: float):
    rows, orders_out, trades_out, maximum_differences = [], [], [], {}
    for current_id in ids:
        short, long = map(int, current_id.removeprefix("P").split("_"))
        spec = TimingSpec(
            "CROSS", cost_bps=cost_bps, periods=(short, long),
            buy_threshold=float(parameters["buy_threshold"]), sell_threshold=float(parameters["sell_threshold"]),
        )
        prepared = prepare_dual_stochrsi_data(raw, spec)
        for window in windows:
            metrics, _, _, orders, trades, checked = run_case(
                prepared, spec, start=window["start"], end=window["end"], initial_cash=initial_cash
            )
            checked.pop("signal_plans")
            rows.append({
                "case_id": current_id, "short_period": short, "long_period": long,
                "cohort": window["cohort"], "window_id": window["window_id"],
                "window_start": window["start"].date().isoformat(), "window_end": window["end"].date().isoformat(),
                **metrics, **{name: float(value) for name, value in checked.items()},
            })
            for frame, target in ((orders, orders_out), (trades, trades_out)):
                if not frame.empty:
                    current = frame.copy()
                    current.insert(0, "case_id", current_id); current.insert(1, "window_id", window["window_id"])
                    target.append(current)
            for name, value in checked.items():
                maximum_differences[name] = max(maximum_differences.get(name, 0.0), float(value))
    return pd.DataFrame(rows), orders_out, trades_out, maximum_differences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ": raise ValueError("This experiment requires QQQ")
    cost_bps = float(args.cost_bps)
    if cost_bps not in [0.0, 5.0]: raise ValueError("Frozen costs are 0 and 5 bps")
    output_root = reserve_block(context, args.run_id, "QQQ", cost_bps)
    parameters, initial_cash = context.config["parameters"], float(context.config["initial_cash"])
    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved": raise RuntimeError("QQQ data is not approved")
    raw = pd.read_csv(canonical_path, parse_dates=["date"]); raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    training = quarterly_windows(raw, parameters, "training")
    validation = quarterly_windows(raw, parameters, "validation")
    if training[-1]["end"] >= validation[0]["start"]: raise RuntimeError("Training and validation dates overlap")
    all_windows = training + validation
    started = time.perf_counter()
    selection_path = context.run_root(args.run_id) / "QQQ" / "cost_5bps" / "training_selection.json"
    if cost_bps == 5.0:
        shorts = period_values(int(parameters["short_period_start"]), int(parameters["short_period_end"]), int(parameters["period_step"]))
        longs = period_values(int(parameters["long_period_start"]), int(parameters["long_period_end"]), int(parameters["period_step"]))
        pairs = [(short, long) for short in shorts for long in longs]
        if len(pairs) != int(parameters["combination_count"]): raise RuntimeError("Grid count mismatch")
        configure_screen(raw, training, parameters, initial_cash)
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("fork")) if args.workers > 1 else None
        try:
            outputs = executor.map(screen_worker, pairs, chunksize=1) if executor else map(screen_worker, pairs)
            screen = pd.DataFrame([row for batch in outputs for row in batch])
        finally:
            if executor: executor.shutdown(wait=True, cancel_futures=True)
        surface, selection = select_training(screen, parameters)
        normalize_frame(screen).to_csv(output_root / "training_screen.csv", index=False, lineterminator="\n")
        normalize_frame(surface).to_csv(output_root / "training_surface.csv", index=False, lineterminator="\n")
        selection_path.write_text(json.dumps(json_safe(selection), ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    else:
        if not selection_path.exists(): raise RuntimeError("Run the frozen 5 bps selection block before 0 bps formal checks")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    ids = selected_case_ids(selection, parameters)
    formal, order_frames, trade_frames, maximum_differences = formal_cases(raw, all_windows, ids, parameters, initial_cash, cost_bps)
    normalize_frame(formal).to_csv(output_root / "formal_results.csv", index=False, lineterminator="\n")
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    normalize_frame(orders).to_csv(output_root / "formal_orders.csv", index=False, lineterminator="\n")
    normalize_frame(trades).to_csv(output_root / "formal_trades.csv", index=False, lineterminator="\n")
    benchmark_rows = []
    for window in all_windows:
        frame = raw[raw["date"].between(window["start"], window["end"])].reset_index(drop=True)
        _, _, metrics = buy_hold(frame, initial_cash=initial_cash, cost_bps=cost_bps)
        benchmark_rows.append({"cohort":window["cohort"],"window_id":window["window_id"],"window_start":window["start"].date().isoformat(),"window_end":window["end"].date().isoformat(),**metrics})
    normalize_frame(pd.DataFrame(benchmark_rows)).to_csv(output_root / "buy_hold_results.csv", index=False, lineterminator="\n")
    window_frame = pd.DataFrame([{**w,"start":w["start"].date().isoformat(),"end":w["end"].date().isoformat()} for w in all_windows])
    normalize_frame(window_frame).to_csv(output_root / "windows.csv", index=False, lineterminator="\n")
    summary = {
        "symbol":"QQQ","cost_bps":cost_bps,"training_windows":len(training),"validation_windows":len(validation),
        "screening_case_windows":int(parameters["combination_count"])*len(training) if cost_bps==5 else 0,
        "formal_case_windows":len(formal),"selected_case_ids":ids,"selection":selection,
        "max_cross_check_differences":maximum_differences,"elapsed_seconds":time.perf_counter()-started,
    }
    (output_root/"metrics.json").write_text(json.dumps(json_safe(summary),ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    manifest = {
        "schema_version":1,"experiment_id":context.config["experiment_id"],"experiment_run_id":args.run_id,
        "completed_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"symbol":"QQQ","cost_bps":cost_bps,
        "engine":"independent ledger for the frozen training screen; lib-pybroker plus independent ledger for every reported candidate-window",
        "python":platform.python_version(),"parameters":parameters,"summary":summary,"source_file":str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256":sha256(canonical_path),"source_manifest_build_id":canonical_manifest["build_id"],"source_manifest_entry":dataset,"artifacts":{},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name!="manifest.json": manifest["artifacts"][path.name]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    block_manifest=output_root/"manifest.json"; block_manifest.write_text(json.dumps(json_safe(manifest),ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    record_block_complete(context,args.run_id,"QQQ",cost_bps,block_manifest)
    print(f"Completed multistart block {cost_bps:g} bps in {summary['elapsed_seconds']:.2f}s; selected {ids}; max difference {max(maximum_differences.values(),default=0):.3g}")


if __name__ == "__main__": main()
