#!/usr/bin/env python3
"""Run all observable 2010–2026 five-year QQQ StochRSI restart windows."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import normalize_frame
from scripts.run_stochrsi_cross_period_grid import period_values
from scripts.run_stochrsi_multistart_robustness import (
    configure_screen, formal_cases, quarterly_windows, screen_worker,
    select_training, selected_case_ids,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70a.4__26-08-25__qqq_stochrsi_multistart_full_2010_2026"


def all_windows(raw: pd.DataFrame, parameters: dict) -> list[dict]:
    mapped = {
        **parameters,
        "training_start_first": parameters["all_start_first"],
        "training_start_last": parameters["all_start_last"],
        "expected_training_windows": parameters["expected_all_windows"],
    }
    windows = quarterly_windows(raw, mapped, "training")
    for window in windows:
        window["cohort"] = "all_sample"
        window["window_id"] = window["window_id"].replace("TR_", "ALL_")
    return windows


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
    if cost_bps not in {0.0, 5.0}: raise ValueError("Frozen costs are 0 and 5 bps")
    output_root = reserve_block(context, args.run_id, "QQQ", cost_bps)
    parameters, initial_cash = context.config["parameters"], float(context.config["initial_cash"])
    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved": raise RuntimeError("QQQ data is not approved")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq("QQQ")].sort_values("date").reset_index(drop=True)
    windows = all_windows(raw, parameters)
    started = time.perf_counter()
    selection_path = context.run_root(args.run_id) / "QQQ" / "cost_5bps" / "selection.json"
    if cost_bps == 5.0:
        shorts = period_values(int(parameters["short_period_start"]), int(parameters["short_period_end"]), int(parameters["period_step"]))
        longs = period_values(int(parameters["long_period_start"]), int(parameters["long_period_end"]), int(parameters["period_step"]))
        pairs = [(short, long) for short in shorts for long in longs]
        if len(pairs) != int(parameters["combination_count"]): raise RuntimeError("Grid count mismatch")
        configure_screen(raw, windows, parameters, initial_cash)
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("fork")) if args.workers > 1 else None
        try:
            batches = executor.map(screen_worker, pairs, chunksize=1) if executor else map(screen_worker, pairs)
            screen = pd.DataFrame([row for batch in batches for row in batch])
        finally:
            if executor: executor.shutdown(wait=True, cancel_futures=True)
        surface, selection = select_training(screen, parameters)
        normalize_frame(screen).to_csv(output_root / "screen.csv", index=False, lineterminator="\n")
        normalize_frame(surface).to_csv(output_root / "surface.csv", index=False, lineterminator="\n")
        selection_path.write_text(json.dumps(json_safe(selection), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    else:
        if not selection_path.exists(): raise RuntimeError("Run 5 bps selection before 0 bps formal checks")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    ids = selected_case_ids(selection, parameters)
    formal, order_frames, trade_frames, maximum_differences = formal_cases(raw, windows, ids, parameters, initial_cash, cost_bps)
    normalize_frame(formal).to_csv(output_root / "formal_results.csv", index=False, lineterminator="\n")
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    normalize_frame(orders).to_csv(output_root / "formal_orders.csv", index=False, lineterminator="\n")
    normalize_frame(trades).to_csv(output_root / "formal_trades.csv", index=False, lineterminator="\n")
    benchmark_rows = []
    for window in windows:
        frame = raw[raw["date"].between(window["start"], window["end"])].reset_index(drop=True)
        _, _, metrics = buy_hold(frame, initial_cash=initial_cash, cost_bps=cost_bps)
        benchmark_rows.append({"cohort":"all_sample","window_id":window["window_id"],"window_start":window["start"].date().isoformat(),"window_end":window["end"].date().isoformat(),**metrics})
    normalize_frame(pd.DataFrame(benchmark_rows)).to_csv(output_root / "buy_hold_results.csv", index=False, lineterminator="\n")
    normalize_frame(pd.DataFrame([{**w,"start":w["start"].date().isoformat(),"end":w["end"].date().isoformat()} for w in windows])).to_csv(output_root / "windows.csv", index=False, lineterminator="\n")
    summary = {"symbol":"QQQ","cost_bps":cost_bps,"window_count":len(windows),"screening_case_windows":int(parameters["combination_count"])*len(windows) if cost_bps==5 else 0,
               "formal_case_windows":len(formal),"selected_case_ids":ids,"selection":selection,"max_cross_check_differences":maximum_differences,"elapsed_seconds":time.perf_counter()-started}
    (output_root/"metrics.json").write_text(json.dumps(json_safe(summary),ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    manifest={"schema_version":1,"experiment_id":context.config["experiment_id"],"experiment_run_id":args.run_id,"completed_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
              "symbol":"QQQ","cost_bps":cost_bps,"engine":"independent ledger for all 874x47 screening paths; lib-pybroker plus independent ledger for every reported candidate-window",
              "python":platform.python_version(),"parameters":parameters,"summary":summary,"source_file":str(canonical_path.relative_to(WORKSPACE_ROOT)),"source_file_sha256":sha256(canonical_path),
              "source_manifest_build_id":canonical_manifest["build_id"],"source_manifest_entry":dataset,"artifacts":{}}
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json": manifest["artifacts"][path.name]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    block_manifest=output_root/"manifest.json"; block_manifest.write_text(json.dumps(json_safe(manifest),ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    record_block_complete(context,args.run_id,"QQQ",cost_bps,block_manifest)
    print(f"Completed full 2010–2026 multistart block {cost_bps:g} bps in {summary['elapsed_seconds']:.2f}s; selected {ids}; max difference {max(maximum_differences.values(),default=0):.3g}")


if __name__ == "__main__": main()
