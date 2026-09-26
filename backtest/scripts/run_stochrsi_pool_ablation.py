#!/usr/bin/env python3
"""Run six frozen structural ablations of the QQQ scaled StochRSI strategy."""

from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.stochrsi_scaled_pools import (
    ScaledPoolSpec,
    cash_flow_adjusted_metrics,
    compiled_daily_state,
    contribution_matched_buy_hold,
    prepare_scaled_pool_data,
    run_compiled_pybroker,
    run_reference_scaled_pools,
)
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_stochrsi_scaled_pools import naive_buy_hold


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80a.1__26-08-25__qqq_stochrsi_pool_ablation"
TOLERANCE = 1e-6

CASE_SPECS = {
    "FULL": ScaledPoolSpec(),
    "NO_POOL_B": replace(ScaledPoolSpec(), enable_pool_b=False),
    "NO_A_DAILY_DECAY": replace(ScaledPoolSpec(), enable_a_daily_decay=False),
    "NO_B1_FLOOR": replace(ScaledPoolSpec(), enable_b1_floor=False),
    "NO_B2_FLOOR": replace(ScaledPoolSpec(), enable_b2_floor=False),
    "DEFERRED_BUY_CROSS_040": replace(ScaledPoolSpec(), deferred_buy_cross=0.40),
}

CASE_LABELS = {
    "FULL": "完整策略",
    "NO_POOL_B": "删除Pool B",
    "NO_A_DAILY_DECAY": "Pool A取消每日递减卖出",
    "NO_B1_FLOOR": "删除B1的a/4最低买入额",
    "NO_B2_FLOOR": "删除B2的0.6b最低买入额",
    "DEFERRED_BUY_CROSS_040": "低位份额累计至StochRSI100上穿0.4买入",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "QQQ" or context.config["symbols"] != ["QQQ"]:
        raise ValueError("This experiment requires QQQ")
    if float(args.cost_bps) != 0 or context.config["cost_scenarios_bps_per_side"] != [0]:
        raise ValueError("This frozen experiment has only the zero-cost case")
    frozen_cases = [item["case_id"] for item in context.config["parameters"]["ablation_cases"]]
    if frozen_cases != list(CASE_SPECS):
        raise ValueError(f"Frozen case order mismatch: {frozen_cases}")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    params = context.config["parameters"]
    start, end = pd.Timestamp(params["analysis_start"]), pd.Timestamp(params["analysis_end"])
    initial_cash = float(context.config["initial_cash"])
    analysis = raw[raw["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen boundaries do not match QQQ sessions")

    started = time.perf_counter()
    prepared = prepare_scaled_pool_data(raw, ScaledPoolSpec())
    indicator = prepared[prepared["date"].between(start, end)][
        ["date", "close", "stochrsi_42", "stochrsi_100", "prior_stochrsi_42",
         "prior_stochrsi_100", "fast_drop_trigger_100_030"]
    ].reset_index(drop=True)
    normalize_frame(indicator).to_csv(output_root / "indicator_daily.csv", index=False, lineterminator="\n")
    equality = {
        "stochrsi100_exact_0_60_count": int(indicator["stochrsi_100"].eq(0.60).sum()),
        "stochrsi100_exact_0_30_count": int(indicator["stochrsi_100"].eq(0.30).sum()),
        "stochrsi100_exact_0_40_count": int(indicator["stochrsi_100"].eq(0.40).sum()),
        "closest_abs_distance_to_0_60": float((indicator["stochrsi_100"] - 0.60).abs().min()),
        "closest_abs_distance_to_0_30": float((indicator["stochrsi_100"] - 0.30).abs().min()),
        "closest_abs_distance_to_0_40": float((indicator["stochrsi_100"] - 0.40).abs().min()),
    }

    result_rows: list[dict[str, object]] = []
    all_orders: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_daily: list[pd.DataFrame] = []
    case_summaries: dict[str, object] = {}
    max_difference = 0.0
    for case_id, spec in CASE_SPECS.items():
        case_root = output_root / "cases" / case_id
        case_root.mkdir(parents=True, exist_ok=True)
        reference = run_reference_scaled_pools(
            prepared, spec, analysis_start=start, analysis_end=end, initial_cash=initial_cash
        )
        broker, engine = run_compiled_pybroker(
            prepared, reference, analysis_start=start, analysis_end=end, initial_cash=initial_cash
        )
        actual = compiled_daily_state(broker, engine, analysis, reference.contributions)
        actual = actual[actual["date"].between(start, end)].reset_index(drop=True)
        differences = cross_check(broker, actual, reference, tolerance=TOLERANCE)
        max_difference = max(max_difference, max(differences.values(), default=0.0))
        metrics = cash_flow_adjusted_metrics(
            reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
        )
        modified_daily, modified_orders = contribution_matched_buy_hold(
            analysis, reference.contributions, initial_cash=initial_cash
        )
        modified_metrics = cash_flow_adjusted_metrics(
            modified_daily, modified_orders, pd.DataFrame(), initial_cash=initial_cash
        )
        event_counts = reference.events["action"].value_counts().to_dict()
        summary = {
            "label": CASE_LABELS[case_id], "spec": asdict(spec), "strategy": metrics,
            "modified_buy_hold": modified_metrics, "event_counts": event_counts,
            "fast_drop_event_count": int(reference.events["fast_drop_sale"].sum()),
            "max_cross_check_differences": differences,
        }
        case_summaries[case_id] = summary
        result_rows.append({
            "case_id": case_id, "label": CASE_LABELS[case_id], **metrics,
            "matched_bh_cagr_pct": modified_metrics["cagr_pct"],
            "matched_bh_sharpe": modified_metrics["sharpe"],
            "matched_bh_max_drawdown_pct": modified_metrics["max_drawdown_pct"],
            "matched_bh_final_equity": modified_metrics["final_equity"],
            "cagr_gap_vs_matched_bh_pct_points": metrics["cagr_pct"] - modified_metrics["cagr_pct"],
            "drawdown_improvement_vs_matched_bh_pct_points": metrics["max_drawdown_pct"] - modified_metrics["max_drawdown_pct"],
        })
        frames = {
            "daily.csv": reference.daily, "pybroker_daily.csv": actual,
            "orders.csv": reference.orders, "trades.csv": reference.trades,
            "contributions.csv": reference.contributions, "events.csv": reference.events,
            "modified_buy_hold_daily.csv": modified_daily,
            "modified_buy_hold_orders.csv": modified_orders,
        }
        for name, frame in frames.items():
            normalize_frame(frame).to_csv(case_root / name, index=False, lineterminator="\n")
        for collection, frame in (
            (all_orders, reference.orders), (all_trades, reference.trades),
            (all_daily, reference.daily),
        ):
            tagged = frame.copy()
            tagged.insert(0, "case_id", case_id)
            collection.append(tagged)

    naive_daily, naive_orders, naive_metrics = naive_buy_hold(analysis, initial_cash=initial_cash)
    pd.DataFrame(result_rows).to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    pd.concat(all_orders, ignore_index=True).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    pd.concat(all_trades, ignore_index=True).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")
    pd.concat(all_daily, ignore_index=True).to_csv(output_root / "daily.csv", index=False, lineterminator="\n")
    normalize_frame(naive_daily).to_csv(output_root / "naive_buy_hold_daily.csv", index=False, lineterminator="\n")
    normalize_frame(naive_orders).to_csv(output_root / "naive_buy_hold_orders.csv", index=False, lineterminator="\n")

    run_summary = {
        "symbol": "QQQ", "cost_bps": 0.0,
        "analysis_start": start.date().isoformat(), "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis), "strategy_case_count": len(CASE_SPECS),
        "formal_case_count_including_benchmarks": len(CASE_SPECS) * 2 + 1,
        "cases": case_summaries, "naive_buy_hold": naive_metrics,
        "strict_boundary_equality_audit": equality,
        "max_cross_check_difference": max_difference,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(run_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ", "cost_bps": 0.0,
        "cost_model": "zero cost; ordinary same-Close fills; strict fast-drop theoretical StochRSI100=0.30 fill without OHLC-touch gate",
        "engine": "lib-pybroker 1.2.12 compiled replay plus independent FIFO/state ledger for every ablation case",
        "python": platform.python_version(), "parameters": params, "summary": run_summary,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset, "artifacts": {},
    }
    for path in sorted(output_root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = str(path.relative_to(output_root))
            manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 0.0, manifest_path)
    print(
        f"Completed {len(CASE_SPECS)} ablation cases in {run_summary['elapsed_seconds']:.2f}s; "
        f"max ledger difference={max_difference:.3g}"
    )


if __name__ == "__main__":
    main()
