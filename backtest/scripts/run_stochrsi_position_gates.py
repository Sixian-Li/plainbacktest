#!/usr/bin/env python3
"""Run three QQQ StochRSI mothers and nine fixed-capital position gates."""

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
from quantkit.stochrsi_position_gates import (
    FourStateSpec,
    run_reference_four_state,
    run_reference_position_gate,
)
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
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80a.3__26-08-26__qqq_stochrsi_position_gates"
TOLERANCE = 1e-6
MOTHER_IDS = ("M1_D0_F0_S0", "M2_D0_F1_S1", "M4_FOUR_STATE")
GATE_THRESHOLDS = (0.70, 0.80, 0.90)


def _save_frames(root: Path, frames: dict[str, pd.DataFrame]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        normalize_frame(frame).to_csv(root / name, index=False, lineterminator="\n")


def _evaluate_reference(
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
    metrics = cash_flow_adjusted_metrics(
        reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
    )
    metrics["average_qqq_weight_pct"] = float(reference.daily["qqq_weight"].mean() * 100.0)
    return actual, differences, metrics


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
    if tuple(item["mother_id"] for item in frozen["mother_strategies"]) != MOTHER_IDS:
        raise ValueError("Frozen mother order mismatch")
    if tuple(float(value) for value in frozen["position_gate_thresholds"]) != GATE_THRESHOLDS:
        raise ValueError("Frozen gate thresholds mismatch")
    if int(frozen["strategy_path_count"]) != 12:
        raise ValueError("Frozen path count must be 12")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
    canonical_manifest_path = WORKSPACE_ROOT / "data/processed/manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    dataset = next(item for item in canonical_manifest["datasets"] if item["symbol"] == "QQQ")
    if dataset["effective_status"] != "approved":
        raise RuntimeError(f"QQQ data is not approved: {dataset['effective_status']}")
    raw = pd.read_csv(canonical_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    start, end = pd.Timestamp(frozen["analysis_start"]), pd.Timestamp(frozen["analysis_end"])
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

    mother_references = {
        "M1_D0_F0_S0": run_reference_scaled_pools(
            prepared, ScaledPoolSpec(), analysis_start=start, analysis_end=end, initial_cash=initial_cash
        ),
        "M2_D0_F1_S1": run_reference_scaled_pools(
            prepared,
            replace(
                ScaledPoolSpec(),
                enable_sparse_42_recovery_buy=True,
                enable_sparse_100_recovery_buy=True,
            ),
            analysis_start=start, analysis_end=end, initial_cash=initial_cash,
        ),
        "M4_FOUR_STATE": run_reference_four_state(
            prepared, FourStateSpec(), analysis_start=start, analysis_end=end, initial_cash=initial_cash
        ),
    }

    path_rows: list[dict[str, object]] = []
    path_summaries: dict[str, object] = {}
    combined_daily: list[pd.DataFrame] = []
    combined_orders: list[pd.DataFrame] = []
    combined_trades: list[pd.DataFrame] = []
    max_difference = 0.0

    for mother_id, reference in mother_references.items():
        actual, differences, metrics = _evaluate_reference(
            prepared, analysis, reference, start=start, end=end, initial_cash=initial_cash
        )
        max_difference = max(max_difference, max(differences.values(), default=0.0))
        matched_metrics = None
        matched_daily = matched_orders = None
        if mother_id != "M4_FOUR_STATE":
            matched_daily, matched_orders = contribution_matched_buy_hold(
                analysis, reference.contributions, initial_cash=initial_cash
            )
            matched_metrics = cash_flow_adjusted_metrics(
                matched_daily, matched_orders, pd.DataFrame(), initial_cash=initial_cash
            )
        summary = {
            "path_type": "mother", "source_mother": mother_id,
            "strategy": metrics, "max_cross_check_differences": differences,
            "matched_buy_hold": matched_metrics,
            "order_signal_counts": reference.orders["primary_signal"].value_counts().to_dict(),
            "event_counts": reference.events["action"].value_counts().to_dict(),
        }
        path_summaries[mother_id] = summary
        path_rows.append({
            "path_id": mother_id, "path_type": "mother", "source_mother": mother_id,
            "gate_threshold": None, **metrics,
            "matched_bh_cagr_pct": matched_metrics["cagr_pct"] if matched_metrics else None,
            "matched_bh_sharpe": matched_metrics["sharpe"] if matched_metrics else None,
            "matched_bh_max_drawdown_pct": matched_metrics["max_drawdown_pct"] if matched_metrics else None,
        })
        frames = {
            "daily.csv": reference.daily, "pybroker_daily.csv": actual,
            "orders.csv": reference.orders, "trades.csv": reference.trades,
            "contributions.csv": reference.contributions, "events.csv": reference.events,
        }
        if matched_daily is not None and matched_orders is not None:
            frames["matched_buy_hold_daily.csv"] = matched_daily
            frames["matched_buy_hold_orders.csv"] = matched_orders
        _save_frames(output_root / "paths" / mother_id, frames)
        for collection, frame in (
            (combined_daily, reference.daily),
            (combined_orders, reference.orders),
            (combined_trades, reference.trades),
        ):
            tagged = frame.copy()
            tagged.insert(0, "path_id", mother_id)
            collection.append(tagged)

        for threshold in GATE_THRESHOLDS:
            gate_id = f"{mother_id}_GATE_{int(threshold * 100)}"
            gate = run_reference_position_gate(
                reference.daily, threshold, initial_cash=initial_cash, mother_id=mother_id
            )
            gate_actual, gate_differences, gate_metrics = _evaluate_reference(
                prepared, analysis, gate, start=start, end=end, initial_cash=initial_cash
            )
            max_difference = max(max_difference, max(gate_differences.values(), default=0.0))
            path_summaries[gate_id] = {
                "path_type": "position_gate", "source_mother": mother_id,
                "gate_threshold": threshold, "strategy": gate_metrics,
                "max_cross_check_differences": gate_differences,
                "order_signal_counts": gate.orders["primary_signal"].value_counts().to_dict(),
            }
            path_rows.append({
                "path_id": gate_id, "path_type": "position_gate",
                "source_mother": mother_id, "gate_threshold": threshold,
                **gate_metrics, "matched_bh_cagr_pct": None,
                "matched_bh_sharpe": None, "matched_bh_max_drawdown_pct": None,
            })
            _save_frames(output_root / "paths" / gate_id, {
                "daily.csv": gate.daily, "pybroker_daily.csv": gate_actual,
                "orders.csv": gate.orders, "trades.csv": gate.trades,
                "contributions.csv": gate.contributions, "events.csv": gate.events,
            })
            for collection, frame in (
                (combined_daily, gate.daily),
                (combined_orders, gate.orders),
                (combined_trades, gate.trades),
            ):
                tagged = frame.copy()
                tagged.insert(0, "path_id", gate_id)
                collection.append(tagged)

    naive_daily, naive_orders, _ = naive_buy_hold(analysis, initial_cash=initial_cash)
    naive_metrics = cash_flow_adjusted_metrics(
        naive_daily, naive_orders, pd.DataFrame(), initial_cash=initial_cash
    )
    normalize_frame(naive_daily).to_csv(output_root / "naive_buy_hold_daily.csv", index=False, lineterminator="\n")
    normalize_frame(naive_orders).to_csv(output_root / "naive_buy_hold_orders.csv", index=False, lineterminator="\n")
    results = pd.DataFrame(path_rows)
    results.to_csv(output_root / "parameter_results.csv", index=False, lineterminator="\n")
    pd.concat(combined_daily, ignore_index=True).to_csv(output_root / "daily.csv", index=False, lineterminator="\n")
    pd.concat(combined_orders, ignore_index=True).to_csv(output_root / "orders.csv", index=False, lineterminator="\n")
    pd.concat(combined_trades, ignore_index=True).to_csv(output_root / "trades.csv", index=False, lineterminator="\n")

    m4_gate_ids = [f"M4_FOUR_STATE_GATE_{int(value * 100)}" for value in GATE_THRESHOLDS]
    m4_gate_daily = [pd.read_csv(output_root / "paths" / path_id / "daily.csv") for path_id in m4_gate_ids]
    m4_equivalence = {
        "expected_identical": True,
        "max_abs_equity_difference": max(
            float((m4_gate_daily[0]["equity"] - frame["equity"]).abs().max())
            for frame in m4_gate_daily[1:]
        ),
        "order_counts": {path_id: len(frame) for path_id, frame in zip(
            m4_gate_ids,
            [pd.read_csv(output_root / "paths" / path_id / "orders.csv") for path_id in m4_gate_ids],
        )},
    }
    equality = {
        mother_id: {
            str(int(threshold * 100)): int(reference.daily["qqq_weight"].eq(threshold).sum())
            for threshold in GATE_THRESHOLDS
        }
        for mother_id, reference in mother_references.items()
    }
    run_summary = {
        "symbol": "QQQ", "cost_bps": 0.0,
        "analysis_start": start.date().isoformat(), "analysis_end": end.date().isoformat(),
        "analysis_bars": len(analysis), "strategy_path_count": len(path_rows),
        "mother_count": 3, "position_gate_count": 9,
        "paths": path_summaries, "naive_buy_hold": naive_metrics,
        "strict_gate_equality_audit": equality,
        "m4_gate_equivalence_audit": m4_equivalence,
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
        "cost_model": "zero cost; completed same-Close signals and same-Close fills; frozen M1/M2 fast-drop theoretical-price exception",
        "engine": "lib-pybroker 1.2.12 compiled replay plus independent FIFO/state ledger for all 12 strategy paths",
        "python": platform.python_version(), "parameters": frozen, "summary": run_summary,
        "max_cross_check_differences": {"maximum_across_all_paths": max_difference},
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
        f"Completed {len(path_rows)} strategy paths in {run_summary['elapsed_seconds']:.2f}s; "
        f"max ledger difference={max_difference:.3g}"
    )


if __name__ == "__main__":
    main()
