#!/usr/bin/env python3
"""Run three individual 90% gates and four strict StochRSI signal intersections."""

from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.stochrsi_position_gates import (
    holding_period_cagr_pct,
    run_reference_and_position_gate,
    run_reference_position_gate,
)
from quantkit.stochrsi_pruned_accumulation import (
    PrunedAccumulationSpec,
    run_reference_pruned_accumulation,
)
from quantkit.stochrsi_scaled_pools import (
    ScaledPoolSpec,
    cash_flow_adjusted_metrics,
    compiled_daily_state,
    prepare_scaled_pool_data,
    run_compiled_pybroker,
    run_reference_scaled_pools,
)
from scripts.run_intraday_sma_backtest import cross_check, json_safe, normalize_frame
from scripts.run_stochrsi_scaled_pools import naive_buy_hold


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/TIM-v0.80a.5__26-08-26__qqq_stochrsi_90_and_gates"
)
TOLERANCE = 1e-6
SOURCE_MOTHERS = {
    "S1": "M1_D0_F0_S0",
    "S2": "M2_D0_F1_S1",
    "S4": "M4_PRUNED_ACCUMULATION",
}
INDIVIDUAL_PATHS = {
    "S1_90": "S1",
    "S2_90": "S2",
    "S4_90": "S4",
}


def _save_frames(root: Path, frames: dict[str, pd.DataFrame]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        normalize_frame(frame).to_csv(root / name, index=False, lineterminator="\n")


def _metrics(reference, *, initial_cash: float) -> dict[str, object]:
    metrics = cash_flow_adjusted_metrics(
        reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
    )
    holding_time = float(metrics["exposure_pct"])
    metrics["holding_time_pct"] = holding_time
    metrics["holding_period_cagr_pct"] = holding_period_cagr_pct(
        float(metrics["cagr_pct"]), holding_time
    )
    metrics["average_qqq_weight_pct"] = float(reference.daily["qqq_weight"].mean() * 100.0)
    return metrics


def _evaluate(
    prepared: pd.DataFrame,
    analysis: pd.DataFrame,
    reference,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    initial_cash: float,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, object]]:
    broker, engine = run_compiled_pybroker(
        prepared, reference, analysis_start=start, analysis_end=end, initial_cash=initial_cash
    )
    actual = compiled_daily_state(broker, engine, analysis, reference.contributions)
    actual = actual[actual["date"].between(start, end)].reset_index(drop=True)
    differences = cross_check(broker, actual, reference, tolerance=TOLERANCE)
    return actual, differences, _metrics(reference, initial_cash=initial_cash)


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
    frozen = context.config["parameters"]
    frozen_sources = {
        item["signal_id"]: item["mother_id"] for item in frozen["source_mothers"]
    }
    if frozen_sources != SOURCE_MOTHERS:
        raise ValueError("Frozen source-mother mapping mismatch")
    if float(frozen["source_gate_threshold"]) != 0.90:
        raise ValueError("Frozen threshold must be 0.90")
    if list(frozen["individual_baseline_paths"]) != list(INDIVIDUAL_PATHS):
        raise ValueError("Frozen individual path order mismatch")
    intersection_specs = {
        item["path_id"]: tuple(item["required_signals"])
        for item in frozen["intersection_paths"]
    }
    if len(intersection_specs) != 4 or int(frozen["strategy_path_count"]) != 7:
        raise ValueError("Frozen experiment must contain three individual and four AND paths")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    start, end = pd.Timestamp(frozen["analysis_start"]), pd.Timestamp(frozen["analysis_end"])
    analysis = raw[raw["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Frozen boundaries do not match QQQ sessions")
    initial_cash = float(context.config["initial_cash"])
    started = time.perf_counter()
    prepared = prepare_scaled_pool_data(raw, ScaledPoolSpec())
    indicator = prepared[prepared["date"].between(start, end)][
        ["date", "close", "stochrsi_42", "stochrsi_100"]
    ].reset_index(drop=True)

    mothers = {
        "S1": run_reference_scaled_pools(
            prepared, ScaledPoolSpec(),
            analysis_start=start, analysis_end=end, initial_cash=initial_cash,
        ),
        "S2": run_reference_scaled_pools(
            prepared,
            replace(
                ScaledPoolSpec(),
                enable_sparse_42_recovery_buy=True,
                enable_sparse_100_recovery_buy=True,
            ),
            analysis_start=start, analysis_end=end, initial_cash=initial_cash,
        ),
        "S4": run_reference_pruned_accumulation(
            prepared, PrunedAccumulationSpec(),
            analysis_start=start, analysis_end=end, initial_cash=initial_cash,
        ),
    }
    source_summaries: dict[str, object] = {}
    max_difference = 0.0
    for signal_id, reference in mothers.items():
        actual, differences, source_metrics = _evaluate(
            prepared, analysis, reference,
            start=start, end=end, initial_cash=initial_cash,
        )
        max_difference = max(max_difference, max(differences.values(), default=0.0))
        source_summaries[signal_id] = {
            "mother_id": SOURCE_MOTHERS[signal_id],
            "strategy": source_metrics,
            "max_cross_check_differences": differences,
        }
        _save_frames(output_root / "signal_sources" / signal_id, {
            "daily.csv": reference.daily,
            "pybroker_daily.csv": actual,
            "orders.csv": reference.orders,
            "trades.csv": reference.trades,
            "contributions.csv": reference.contributions,
            "events.csv": reference.events,
        })
        indicator[f"{signal_id}_mother_weight"] = reference.daily["qqq_weight"].to_numpy(float)
        indicator[f"{signal_id}_signal_90"] = (
            reference.daily["qqq_weight"].to_numpy(float) > 0.90
        ).astype(int)
    normalize_frame(indicator).to_csv(
        output_root / "indicator_daily.csv", index=False, lineterminator="\n"
    )

    paths = {
        path_id: run_reference_position_gate(
            mothers[signal_id].daily,
            0.90,
            initial_cash=initial_cash,
            mother_id=path_id,
        )
        for path_id, signal_id in INDIVIDUAL_PATHS.items()
    }
    for path_id, required_signals in intersection_specs.items():
        paths[path_id] = run_reference_and_position_gate(
            {signal_id: mothers[signal_id].daily for signal_id in required_signals},
            0.90,
            initial_cash=initial_cash,
            composite_id=path_id,
        )

    path_rows: list[dict[str, object]] = []
    path_summaries: dict[str, object] = {}
    combined_daily: list[pd.DataFrame] = []
    combined_orders: list[pd.DataFrame] = []
    combined_trades: list[pd.DataFrame] = []
    truth_audit: dict[str, object] = {}
    for path_id, reference in paths.items():
        actual, differences, path_metrics = _evaluate(
            prepared, analysis, reference,
            start=start, end=end, initial_cash=initial_cash,
        )
        max_difference = max(max_difference, max(differences.values(), default=0.0))
        path_type = "individual_90" if path_id in INDIVIDUAL_PATHS else "intersection"
        required_signals = (
            (INDIVIDUAL_PATHS[path_id],)
            if path_id in INDIVIDUAL_PATHS else intersection_specs[path_id]
        )
        path_summaries[path_id] = {
            "path_type": path_type,
            "required_signals": list(required_signals),
            "strategy": path_metrics,
            "max_cross_check_differences": differences,
            "order_signal_counts": reference.orders["primary_signal"].value_counts().to_dict(),
        }
        path_rows.append({
            "path_id": path_id,
            "path_type": path_type,
            "required_signals": "+".join(required_signals),
            "gate_threshold": 0.90,
            **path_metrics,
        })
        _save_frames(output_root / "paths" / path_id, {
            "daily.csv": reference.daily,
            "pybroker_daily.csv": actual,
            "orders.csv": reference.orders,
            "trades.csv": reference.trades,
            "contributions.csv": reference.contributions,
            "events.csv": reference.events,
        })
        for collection, frame in (
            (combined_daily, reference.daily),
            (combined_orders, reference.orders),
            (combined_trades, reference.trades),
        ):
            tagged = frame.copy()
            tagged.insert(0, "path_id", path_id)
            collection.append(tagged)
        required_flags = [
            mothers[signal_id].daily["qqq_weight"].to_numpy(float) > 0.90
            for signal_id in required_signals
        ]
        expected_long = required_flags[0].copy()
        for flag in required_flags[1:]:
            expected_long &= flag
        actual_long = reference.daily["is_long"].to_numpy(bool)
        truth_audit[path_id] = {
            "required_signals": list(required_signals),
            "expected_long_days": int(expected_long.sum()),
            "actual_long_days": int(actual_long.sum()),
            "truth_table_violation_count": int((expected_long != actual_long).sum()),
            "external_contribution_total": float(reference.daily["external_contribution"].sum()),
        }

    naive_daily, naive_orders, _ = naive_buy_hold(analysis, initial_cash=initial_cash)
    naive_metrics = cash_flow_adjusted_metrics(
        naive_daily, naive_orders, pd.DataFrame(), initial_cash=initial_cash
    )
    naive_metrics["holding_time_pct"] = float(naive_metrics["exposure_pct"])
    naive_metrics["holding_period_cagr_pct"] = holding_period_cagr_pct(
        float(naive_metrics["cagr_pct"]), float(naive_metrics["holding_time_pct"])
    )
    naive_metrics["average_qqq_weight_pct"] = 100.0
    normalize_frame(naive_daily).to_csv(
        output_root / "naive_buy_hold_daily.csv", index=False, lineterminator="\n"
    )
    normalize_frame(naive_orders).to_csv(
        output_root / "naive_buy_hold_orders.csv", index=False, lineterminator="\n"
    )
    results = pd.DataFrame(path_rows)
    results.to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    pd.concat(combined_daily, ignore_index=True).to_csv(
        output_root / "daily.csv", index=False, lineterminator="\n"
    )
    pd.concat(combined_orders, ignore_index=True).to_csv(
        output_root / "orders.csv", index=False, lineterminator="\n"
    )
    pd.concat(combined_trades, ignore_index=True).to_csv(
        output_root / "trades.csv", index=False, lineterminator="\n"
    )

    equality_audit = {
        signal_id: int(reference.daily["qqq_weight"].eq(0.90).sum())
        for signal_id, reference in mothers.items()
    }
    run_summary = {
        "symbol": "QQQ",
        "cost_bps": 0.0,
        "analysis_start": start.date().isoformat(),
        "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis),
        "strategy_path_count": len(paths),
        "source_mothers": source_summaries,
        "paths": path_summaries,
        "naive_buy_hold": naive_metrics,
        "truth_table_audit": truth_audit,
        "strict_90_equality_audit": equality_audit,
        "holding_period_cagr_definition": frozen["holding_period_cagr"],
        "max_cross_check_difference": max_difference,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(run_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "QQQ",
        "cost_bps": 0.0,
        "cost_model": "zero cost; completed same-Close source weights and same-Close gate fills",
        "engine": "lib-pybroker 1.2.12 compiled replay plus independent FIFO/state ledger for three sources and seven gate accounts",
        "python": platform.python_version(),
        "parameters": frozen,
        "summary": run_summary,
        "max_cross_check_differences": {"maximum_across_all_ledgers": max_difference},
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_manifest_entry": dataset,
        "artifacts": {},
    }
    for path in sorted(output_root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = str(path.relative_to(output_root))
            manifest["artifacts"][relative] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "QQQ", 0.0, manifest_path)
    print(
        f"Completed {len(paths)} gates plus three source ledgers in "
        f"{run_summary['elapsed_seconds']:.2f}s; max difference={max_difference:.3g}"
    )


if __name__ == "__main__":
    main()
