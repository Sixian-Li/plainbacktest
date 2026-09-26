#!/usr/bin/env python3
"""Run per-security attribution for the frozen Nasdaq-100 Strategy1 >90% gate."""

from __future__ import annotations

import argparse
import gc
import gzip
import json
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantkit.experiment import (
    assert_run_writable,
    load_experiment,
    record_block_complete,
    reserve_block,
    sha256,
)
from quantkit.nasdaq100_strategy1_holding_attribution import run_single_security_gate


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution"
)
SYMBOL = "NASDAQ100_STRATEGY1_90_ATTRIBUTION"
PARENT_REQUIRED = (
    "eligible_scores.csv.gz",
    "price_panel.csv.gz",
    "instrument_master.csv",
    "manifest.json",
)


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


def _validate_parent_shared(parent_shared_root: Path) -> dict[str, Any]:
    missing = [name for name in PARENT_REQUIRED if not (parent_shared_root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"parent shared cache misses: {missing}")
    manifest = json.loads((parent_shared_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("dataset_status") != "candidate_pending_review":
        raise ValueError("parent shared cache is not the frozen pending-review candidate")
    for name in PARENT_REQUIRED:
        if name == "manifest.json":
            continue
        record = manifest.get("artifacts", {}).get(name)
        if not record or sha256(parent_shared_root / name) != record.get("sha256"):
            raise ValueError(f"parent shared artifact changed: {name}")
    return manifest


def _prepare_shared(context, run_id: str, parent_shared_root: Path | None) -> Path:
    run_root = context.run_root(run_id)
    destination = run_root / "shared"
    if destination.is_dir():
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        for name, record in manifest["artifacts"].items():
            path = destination / name
            if not path.is_file() or sha256(path) != record["sha256"]:
                raise ValueError(f"reused shared artifact changed: {name}")
        return destination
    if parent_shared_root is None:
        raise ValueError("--parent-shared-root is required when the copied cache is absent")
    parent_shared_root = parent_shared_root.resolve()
    parent_manifest = _validate_parent_shared(parent_shared_root)
    building = Path(tempfile.mkdtemp(prefix="shared_building_", dir=run_root))
    for name in PARENT_REQUIRED[:-1]:
        shutil.copy2(parent_shared_root / name, building / name)
    shutil.copy2(parent_shared_root / "manifest.json", building / "parent_manifest.json")
    eligible = pd.read_csv(building / "eligible_scores.csv.gz")
    panel_ids = set(
        pd.read_csv(building / "instrument_master.csv", dtype=str)
        .query("asset_type == 'nasdaq100_member'")["instrument_id"]
    )
    eligible_ids = set(eligible["security_id"].astype(str))
    if eligible_ids != panel_ids:
        raise AssertionError(
            f"eligible/panel identity mismatch: eligible_only={len(eligible_ids-panel_ids)}, "
            f"panel_only={len(panel_ids-eligible_ids)}"
        )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reuse_mode": "byte-for-byte copy of parent validated shared artifacts",
        "parent_experiment_id": context.config["parameters"]["parent_experiment_id"],
        "parent_run_id": context.config["parameters"]["parent_run_id"],
        "parent_shared_manifest_sha256": sha256(parent_shared_root / "manifest.json"),
        "dataset_status": parent_manifest["dataset_status"],
        "dataset_approved": parent_manifest["dataset_approved"],
        "security_count": len(eligible_ids),
        "eligible_observations": len(eligible),
        "source_files": parent_manifest["source_files"],
        "artifacts": {},
    }
    for path in sorted(building.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    write_json(building / "manifest.json", manifest)
    building.rename(destination)
    return destination


def run_cost_block(
    context,
    run_id: str,
    symbol: str,
    cost_bps: float,
    *,
    parent_shared_root: Path | None,
) -> None:
    assert_run_writable(context, run_id)
    if symbol != SYMBOL:
        raise ValueError(f"unexpected symbol block: {symbol}")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"unexpected cost scenario: {cost_bps}")
    shared = _prepare_shared(context, run_id, parent_shared_root)
    shared_manifest = json.loads((shared / "manifest.json").read_text(encoding="utf-8"))
    eligible = pd.read_csv(
        shared / "eligible_scores.csv.gz",
        usecols=["date", "security_id"],
        parse_dates=["date"],
        dtype={"security_id": "category"},
    )
    master = pd.read_csv(shared / "instrument_master.csv", dtype=str)
    master = master[master["asset_type"].eq("nasdaq100_member")].sort_values("instrument_id")
    prices = pd.read_csv(
        shared / "price_panel.csv.gz",
        usecols=[
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "synthetic_bar",
            "terminal_settlement_proxy",
        ],
        parse_dates=["date"],
        dtype={"symbol": "category", "synthetic_bar": "int8", "terminal_settlement_proxy": "int8"},
    )
    parameters = context.config["parameters"]
    horizons = tuple(int(value) for value in parameters["post_exit_horizons_trading_days"])
    initial_cash = float(context.config["initial_cash"])
    min_holding = int(parameters["reliability_flags"]["minimum_holding_sessions_for_primary_cross_section"])
    min_exits = int(parameters["reliability_flags"]["minimum_exit_events_for_rebound_summary"])

    eligible_groups = {
        str(security_id): pd.DatetimeIndex(group["date"])
        for security_id, group in eligible.groupby("security_id", sort=False)
    }
    price_groups = prices.groupby("symbol", sort=False, observed=True)
    metric_rows: list[dict[str, Any]] = []
    cross_checks: dict[str, float] = {}
    records = master.to_dict("records")
    run_root = context.run_root(run_id)
    temporary = Path(tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=run_root))
    output_names = {
        "daily": "daily.csv.gz",
        "orders": "orders.csv.gz",
        "trades": "trades.csv.gz",
        "active": "exposed_returns.csv.gz",
        "exits": "exit_events.csv.gz",
    }
    handles = {
        key: gzip.open(temporary / name, mode="wt", encoding="utf-8", newline="")
        for key, name in output_names.items()
    }
    wrote_header = {key: False for key in output_names}
    try:
        for offset, item in enumerate(records, start=1):
            security_id = str(item["instrument_id"])
            security_prices = price_groups.get_group(security_id).sort_values(
                "date", kind="stable"
            ).reset_index(drop=True)
            result = run_single_security_gate(
                security_prices,
                eligible_groups.get(security_id, pd.DatetimeIndex([])),
                initial_cash=initial_cash,
                cost_bps=cost_bps,
                post_exit_horizons=horizons,
            )
            metrics = {
                "security_id": security_id,
                "display_ticker": str(item["display_ticker"]),
                "source_file": str(item["source_file"]),
                "cost_bps": float(cost_bps),
                **result.metrics,
            }
            metrics["adequate_holding_sample"] = bool(metrics["holding_sessions"] >= min_holding)
            metrics["adequate_exit_sample"] = bool(metrics[f"post_exit_20d_count"] >= min_exits)
            metric_rows.append(metrics)
            frames = {
                "daily": result.daily,
                "orders": result.orders,
                "trades": result.trades,
                "active": result.exposed_returns,
                "exits": result.exits,
            }
            for key, frame in frames.items():
                if frame.empty:
                    continue
                frame.insert(0, "display_ticker", str(item["display_ticker"]))
                frame.insert(0, "security_id", security_id)
                frame.to_csv(handles[key], index=False, header=not wrote_header[key], lineterminator="\n")
                wrote_header[key] = True
            for name, value in result.cross_checks.items():
                cross_checks[f"{security_id}.{name}"] = float(value)
            if offset % 50 == 0 or offset == len(records):
                print(f"cost={cost_bps:g}bps attribution {offset}/{len(records)}", flush=True)
                gc.collect()
            del security_prices, result, frames
    finally:
        for handle in handles.values():
            handle.close()

    metrics = pd.DataFrame(metric_rows)
    if len(metrics) != int(shared_manifest["security_count"]):
        raise AssertionError("per-security metric row count changed")

    metrics.to_csv(temporary / "metrics.csv", index=False, lineterminator="\n")
    adequate = metrics[metrics["adequate_holding_sample"].astype(bool)]
    exit_adequate = metrics[metrics["adequate_exit_sample"].astype(bool)]
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "data_status": shared_manifest["dataset_status"],
        "security_count": len(metrics),
        "adequate_holding_sample_count": len(adequate),
        "adequate_exit_sample_count": len(exit_adequate),
        "cross_section": {
            "all": metrics[
                ["holding_period_cagr_pct", "holding_return_sharpe", "max_drawdown_pct", "holding_time_pct"]
            ].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).to_dict(),
            "adequate_holding_sample": adequate[
                ["holding_period_cagr_pct", "holding_return_sharpe", "max_drawdown_pct", "holding_time_pct"]
            ].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).to_dict(),
        },
        "post_exit_event_counts": {
            f"{horizon}d": int(metrics[f"post_exit_{horizon}d_count"].sum())
            for horizon in horizons
        },
        "max_cross_check_differences": cross_checks,
    }
    write_json(temporary / "metrics.json", summary)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "quantkit.nasdaq100_strategy1_holding_attribution.run_single_security_gate",
        "reference_engine": "independent exposed-return geometric reconstruction",
        "python": platform.python_version(),
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "security_count": len(metrics),
        "data_status": shared_manifest["dataset_status"],
        "parameters": parameters,
        "max_cross_check_differences": cross_checks,
        "shared_manifest_sha256": sha256(shared / "manifest.json"),
        "artifacts": {},
    }
    for path in sorted(temporary.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    write_json(temporary / "manifest.json", manifest)
    output = reserve_block(context, run_id, symbol, cost_bps)
    for path in sorted(temporary.iterdir()):
        path.replace(output / path.name)
    temporary.rmdir()
    record_block_complete(context, run_id, symbol, cost_bps, output / "manifest.json")
    del prices, price_groups
    gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--parent-shared-root", type=Path)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_cost_block(
        context,
        args.run_id,
        args.symbol,
        float(args.cost_bps),
        parent_shared_root=args.parent_shared_root,
    )


if __name__ == "__main__":
    main()
