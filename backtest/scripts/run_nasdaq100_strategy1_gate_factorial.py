#!/usr/bin/env python3
"""Run the frozen 12-security, three-factor Strategy1 gate experiment."""

from __future__ import annotations

import argparse
import io
import json
import platform
import shutil
import tempfile
import zipfile
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
from quantkit.nasdaq100_stochrsi_rotation import (
    prepare_strategy1_score_inputs,
    run_strategy1_score,
)
from quantkit.nasdaq100_strategy1_gate_factorial import factorial_specs, run_factorial_gate


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.3__26-08-27__nasdaq100_strategy1_gate_factorial"
)
SYMBOL = "NASDAQ100_STRATEGY1_GATE_FACTORIAL"
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


def _source_member_map(archive: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name in archive.namelist():
        if not name.lower().endswith(".csv"):
            continue
        basename = Path(name).name
        if basename in mapping:
            raise ValueError(f"duplicate source basename: {basename}")
        mapping[basename] = name
    return mapping


def _read_vendor_member(archive: zipfile.ZipFile, member: str, security_id: str) -> pd.DataFrame:
    with archive.open(member) as raw:
        frame = pd.read_csv(
            io.TextIOWrapper(raw, encoding="utf-8-sig"),
            usecols=["Date", "Open", "High", "Low", "Close", "Volume"],
        )
    frame = frame.rename(columns={column: column.lower() for column in frame.columns})
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["symbol"] = security_id
    return frame[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values(
        "date", kind="stable"
    ).drop_duplicates("date", keep="last").reset_index(drop=True)


def _select_parent_sample(metrics: pd.DataFrame) -> pd.DataFrame:
    eligible = metrics[
        metrics["cost_bps"].astype(float).eq(0.0)
        & (metrics["holding_sessions"].astype(int) > 200)
    ].copy()
    eligible = eligible.sort_values(["holding_period_cagr_pct", "security_id"], kind="stable")
    median = float(eligible["holding_period_cagr_pct"].median())
    low = eligible.head(4).copy()
    low["sample_group"] = "LOW"
    low["group_rank"] = np.arange(1, 5)
    high = eligible.tail(4).sort_values(
        ["holding_period_cagr_pct", "security_id"], ascending=[False, True], kind="stable"
    ).copy()
    high["sample_group"] = "HIGH"
    high["group_rank"] = np.arange(1, 5)
    middle = eligible.assign(
        median_distance=(eligible["holding_period_cagr_pct"] - median).abs()
    ).sort_values(["median_distance", "security_id"], kind="stable").head(4).copy()
    middle = middle.sort_values(
        ["holding_period_cagr_pct", "security_id"], ascending=[False, True], kind="stable"
    )
    middle["sample_group"] = "MEDIAN"
    middle["group_rank"] = np.arange(1, 5)
    selected = pd.concat([high, middle, low], ignore_index=True)
    selected["eligible_parent_count"] = len(eligible)
    selected["eligible_parent_median_holding_cagr_pct"] = median
    return selected


def _validate_parent_block(parent_cost_root: Path) -> pd.DataFrame:
    manifest_path = parent_cost_root / "manifest.json"
    metrics_path = parent_cost_root / "metrics.csv"
    if not manifest_path.is_file() or not metrics_path.is_file():
        raise FileNotFoundError("parent zero-cost block must contain manifest.json and metrics.csv")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest.get("artifacts", {}).get("metrics.csv")
    if not record or sha256(metrics_path) != record.get("sha256"):
        raise ValueError("parent metrics hash does not match its block manifest")
    metrics = pd.read_csv(metrics_path)
    if set(metrics["cost_bps"].astype(float)) != {0.0}:
        raise ValueError("parent selection block is not the frozen zero-cost block")
    return metrics


def _verify_frozen_sample(context, selected: pd.DataFrame) -> None:
    expected = {
        str(item["security_id"]): item for item in context.config["parameters"]["selected_sample"]
    }
    actual = set(selected["security_id"].astype(str))
    if actual != set(expected):
        raise AssertionError(f"frozen sample changed: actual={sorted(actual)}, expected={sorted(expected)}")
    for row in selected.itertuples(index=False):
        item = expected[str(row.security_id)]
        fields = {
            "sample_group": str(row.sample_group),
            "display_ticker": str(row.display_ticker),
            "source_file": str(row.source_file),
        }
        for key, value in fields.items():
            if value != str(item[key]):
                raise AssertionError(f"frozen sample {row.security_id} changed {key}: {value}")


def _prepare_shared(context, run_id: str, parent_cost_root: Path | None) -> Path:
    run_root = context.run_root(run_id)
    destination = run_root / "shared"
    if destination.is_dir():
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        for name, record in manifest["artifacts"].items():
            path = destination / name
            if not path.is_file() or sha256(path) != record["sha256"]:
                raise ValueError(f"shared artifact changed: {name}")
        return destination
    if parent_cost_root is None:
        raise ValueError("--parent-cost0-root is required until the shared cache exists")

    metrics = _validate_parent_block(parent_cost_root.resolve())
    selected = _select_parent_sample(metrics)
    _verify_frozen_sample(context, selected)
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if registry.get("review_status") != "pending_review":
        raise ValueError("Nasdaq-100 registry status changed")
    if source_manifest.get("status") != "candidate_pending_review" or source_manifest.get("approved") is not False:
        raise ValueError("Nasdaq-100 source is not the frozen pending-review candidate")
    package = registry["packages"]["split_and_dividend"]
    archive_path = WORKSPACE_ROOT / package["path"]
    if sha256(archive_path) != package["sha256"]:
        raise ValueError("adjusted Nasdaq-100 archive hash changed")

    master = pd.read_csv(SECURITY_MASTER_PATH, dtype=str).fillna("")
    intervals = pd.read_csv(
        MEMBERSHIP_INTERVALS_PATH, parse_dates=["effective_start", "effective_end"]
    )
    expected_by_id = {
        str(item["security_id"]): item for item in context.config["parameters"]["selected_sample"]
    }
    building = Path(tempfile.mkdtemp(prefix="shared_building_", dir=run_root))
    state_frames: list[pd.DataFrame] = []
    selection_rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as archive:
        member_map = _source_member_map(archive)
        for row in selected.itertuples(index=False):
            security_id = str(row.security_id)
            source_file = str(row.source_file)
            master_row = master[master["security_id"].eq(security_id)]
            if len(master_row) != 1 or str(master_row.iloc[0]["source_file"]) != source_file:
                raise AssertionError(f"security master mismatch for {security_id}")
            member = member_map.get(source_file)
            if member is None:
                raise FileNotFoundError(f"archive misses {source_file}")
            raw = _read_vendor_member(archive, member, security_id)
            prepared = prepare_strategy1_score_inputs(raw)
            score = run_strategy1_score(prepared, initial_cash=float(context.config["initial_cash"]))
            state = prepared[[
                "date", "symbol", "open", "high", "low", "close", "volume",
                "stochrsi_42", "stochrsi_100",
            ]].merge(
                score.daily[["date", "strategy1_weight"]], on="date", how="left", validate="one_to_one"
            )
            item = expected_by_id[security_id]
            window_end = pd.Timestamp(item["window_end"])
            observed_end = intervals.loc[
                intervals["security_id"].astype(str).eq(security_id), "effective_end"
            ].max()
            if pd.Timestamp(observed_end) != window_end:
                raise AssertionError(f"membership end changed for {security_id}: {observed_end}")
            requested_start = window_end - pd.DateOffset(
                years=int(context.config["parameters"]["window_calendar_years"])
            )
            window = state[state["date"].between(requested_start, window_end)].copy()
            if len(window) < 2:
                raise ValueError(f"insufficient ten-year vendor history for {security_id}")
            window["synthetic_bar"] = 0
            window["terminal_settlement_proxy"] = 0
            window["sample_group"] = str(row.sample_group)
            window["display_ticker"] = str(row.display_ticker)
            state_frames.append(window)
            complete = window[["strategy1_weight", "stochrsi_42", "stochrsi_100"]].notna().all(axis=1)
            selection_rows.append({
                "sample_group": str(row.sample_group),
                "group_rank": int(row.group_rank),
                "security_id": security_id,
                "display_ticker": str(row.display_ticker),
                "source_file": source_file,
                "parent_holding_sessions": int(row.holding_sessions),
                "parent_holding_period_cagr_pct": float(row.holding_period_cagr_pct),
                "parent_median_holding_period_cagr_pct": float(row.eligible_parent_median_holding_cagr_pct),
                "requested_window_start": requested_start,
                "requested_window_end": window_end,
                "actual_window_start": window["date"].min(),
                "actual_window_end": window["date"].max(),
                "window_bars": len(window),
                "complete_signal_bars": int(complete.sum()),
                "raw_history_start": raw["date"].min(),
                "raw_history_end": raw["date"].max(),
            })

    selection = pd.DataFrame(selection_rows).sort_values(
        ["sample_group", "group_rank"], kind="stable"
    )
    state_panel = pd.concat(state_frames, ignore_index=True).sort_values(
        ["symbol", "date"], kind="stable"
    )
    selection.to_csv(building / "selection.csv", index=False, lineterminator="\n")
    state_panel.to_csv(
        building / "state_panel.csv.gz", index=False, compression="gzip", lineterminator="\n"
    )
    metrics.to_csv(
        building / "parent_metrics.csv.gz", index=False, compression="gzip", lineterminator="\n"
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset_status": "candidate_pending_review",
        "dataset_approved": False,
        "security_count": len(selection),
        "state_rows": len(state_panel),
        "parent_experiment_id": context.config["parameters"]["parent_experiment_id"],
        "parent_run_id": context.config["parameters"]["parent_run_id"],
        "parent_block_manifest_sha256": sha256(parent_cost_root / "manifest.json"),
        "source_archive": str(archive_path.relative_to(WORKSPACE_ROOT)),
        "source_archive_sha256": sha256(archive_path),
        "source_manifest_sha256": sha256(SOURCE_MANIFEST_PATH),
        "security_master_sha256": sha256(SECURITY_MASTER_PATH),
        "membership_intervals_sha256": sha256(MEMBERSHIP_INTERVALS_PATH),
        "artifacts": {},
    }
    for path in sorted(building.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(building / "manifest.json", manifest)
    building.rename(destination)
    return destination


def run_cost_block(
    context,
    run_id: str,
    symbol: str,
    cost_bps: float,
    *,
    parent_cost_root: Path | None,
) -> None:
    assert_run_writable(context, run_id)
    if symbol != SYMBOL:
        raise ValueError(f"unexpected symbol: {symbol}")
    if cost_bps not in [float(value) for value in context.config["cost_scenarios_bps_per_side"]]:
        raise ValueError(f"unexpected cost: {cost_bps}")
    shared = _prepare_shared(context, run_id, parent_cost_root)
    selection = pd.read_csv(shared / "selection.csv", parse_dates=[
        "requested_window_start", "requested_window_end", "actual_window_start",
        "actual_window_end", "raw_history_start", "raw_history_end",
    ])
    state = pd.read_csv(shared / "state_panel.csv.gz", parse_dates=["date"])
    groups = {str(key): part.copy() for key, part in state.groupby("symbol", sort=False)}
    specs = factorial_specs()
    expected_count = int(context.config["parameters"]["formal_security_case_count_per_cost"])
    metric_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    active_frames: list[pd.DataFrame] = []
    checks: dict[str, float] = {}
    selection_index = selection.set_index("security_id")
    for security_id in selection["security_id"].astype(str):
        item = selection_index.loc[security_id]
        for spec in specs:
            result = run_factorial_gate(
                groups[security_id], spec,
                initial_cash=float(context.config["initial_cash"]), cost_bps=cost_bps,
            )
            prefix = {
                "security_id": security_id,
                "display_ticker": str(item["display_ticker"]),
                "sample_group": str(item["sample_group"]),
                "case_id": spec.case_id,
                "mechanical_stop": int(spec.mechanical_stop),
                "strict_entry": int(spec.strict_entry),
                "fast_exit": int(spec.fast_exit),
                "cost_bps": float(cost_bps),
            }
            metric_rows.append({**prefix, **result.metrics})
            for frame, destination in (
                (result.daily, daily_frames), (result.orders, order_frames),
                (result.trades, trade_frames), (result.active_returns, active_frames),
            ):
                if frame.empty:
                    continue
                copy = frame.copy()
                for key, value in reversed(list(prefix.items())):
                    if key not in copy.columns:
                        copy.insert(0, key, value)
                destination.append(copy)
            for name, value in result.cross_checks.items():
                checks[f"{security_id}.{spec.case_id}.{name}"] = float(value)

    metrics = pd.DataFrame(metric_rows)
    if len(metrics) != expected_count:
        raise AssertionError(f"expected {expected_count} paths, got {len(metrics)}")
    temporary = Path(tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=context.run_root(run_id)))
    metrics.to_csv(temporary / "metrics.csv", index=False, lineterminator="\n")
    for name, frames in (
        ("daily.csv.gz", daily_frames), ("orders.csv.gz", order_frames),
        ("trades.csv.gz", trade_frames), ("active_returns.csv.gz", active_frames),
    ):
        pd.concat(frames, ignore_index=True).to_csv(
            temporary / name, index=False, compression="gzip", lineterminator="\n"
        )
    group_summary = metrics.groupby(["sample_group", "case_id"], sort=True)[[
        "cagr_pct", "holding_period_cagr_pct", "holding_return_sharpe", "max_drawdown_pct",
        "holding_time_pct", "entry_count", "stop_exit_count", "fast_exit_count",
    ]].median(numeric_only=True).reset_index()
    group_summary.to_csv(temporary / "group_case_medians.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "cost_bps": cost_bps,
        "security_count": int(metrics["security_id"].nunique()),
        "case_count": int(metrics["case_id"].nunique()),
        "path_count": len(metrics),
        "data_status": "candidate_pending_review",
        "max_cross_check_difference": max(checks.values(), default=0.0),
        "cross_checks": checks,
    }
    write_json(temporary / "metrics.json", summary)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "quantkit.nasdaq100_strategy1_gate_factorial.run_factorial_gate",
        "reference_engine": "independent immutable-fill order replay",
        "python": platform.python_version(),
        "security_count": int(metrics["security_id"].nunique()),
        "case_count": int(metrics["case_id"].nunique()),
        "path_count": len(metrics),
        "data_status": "candidate_pending_review",
        "shared_manifest_sha256": sha256(shared / "manifest.json"),
        "max_cross_check_difference": max(checks.values(), default=0.0),
        "artifacts": {},
    }
    for path in sorted(temporary.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(temporary / "manifest.json", manifest)
    output = reserve_block(context, run_id, symbol, cost_bps)
    for path in sorted(temporary.iterdir()):
        path.replace(output / path.name)
    temporary.rmdir()
    record_block_complete(context, run_id, symbol, cost_bps, output / "manifest.json")
    print(f"completed {symbol} cost={cost_bps:g}bps: {len(metrics)} paths", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    parser.add_argument("--parent-cost0-root", type=Path)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_cost_block(
        context, args.run_id, args.symbol, float(args.cost_bps),
        parent_cost_root=args.parent_cost0_root,
    )


if __name__ == "__main__":
    main()
