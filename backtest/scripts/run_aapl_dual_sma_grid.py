#!/usr/bin/env python3
"""Run the AAPL dual-SMA state grid in two independent five-year windows."""

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
from typing import Any

import numpy as np
import pandas as pd

from quantkit.dual_sma_state import (
    FAST_SMA_COLUMN,
    SLOW_SMA_COLUMN,
    DualSmaStateSpec,
    analysis_slice,
    attach_precomputed_smas,
    run_pybroker_dual_sma,
    run_reference_dual_sma,
    valid_parameter_pairs,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import (
    calculate_holding_period_metrics,
    calculate_metrics,
    pybroker_daily_state,
)
from quantkit.reference import run_buy_and_hold_reference
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.90__26-08-30__aapl_dual_sma_two_window_grid"
)
_STATE: dict[str, Any] = {}


def frozen_windows(raw: pd.DataFrame, definitions: list[dict[str, str]]) -> list[dict[str, Any]]:
    dates = pd.DatetimeIndex(pd.to_datetime(raw["date"]).sort_values().unique())
    windows: list[dict[str, Any]] = []
    for definition in definitions:
        declared_start = pd.Timestamp(definition["start_inclusive"])
        declared_end = pd.Timestamp(definition["end_exclusive"])
        sessions = dates[(dates >= declared_start) & (dates < declared_end)]
        if sessions.empty:
            raise ValueError(f"No AAPL sessions in {definition['window_id']}")
        windows.append(
            {
                **definition,
                "start": pd.Timestamp(sessions[0]),
                "end": pd.Timestamp(sessions[-1]),
                "bar_count": int(len(sessions)),
            }
        )
    for previous, current in zip(windows, windows[1:]):
        if previous["end"] >= current["start"]:
            raise ValueError("Frozen comparison windows must not overlap")
    return windows


def precompute_smas(raw: pd.DataFrame, maximum_window: int) -> dict[int, np.ndarray]:
    close = raw["close"].astype(float)
    return {
        window: close.rolling(window, min_periods=window).mean().to_numpy(float)
        for window in range(1, maximum_window + 1)
    }


def configure_worker(
    raw: pd.DataFrame,
    smas: dict[int, np.ndarray],
    windows: list[dict[str, Any]],
    parameters: dict[str, Any],
    case_indexes: dict[tuple[str, int, int], int],
    initial_cash: float,
    cost_bps: float,
) -> None:
    global _STATE
    _STATE = {
        "raw": raw,
        "smas": smas,
        "windows": windows,
        "parameters": parameters,
        "case_indexes": case_indexes,
        "initial_cash": initial_cash,
        "cost_bps": cost_bps,
    }


def cross_check(
    result,
    actual: pd.DataFrame,
    reference,
    *,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        actual_values = actual[column].to_numpy(float)
        expected_values = reference.daily[column].to_numpy(float)
        if len(actual_values) != len(expected_values):
            raise AssertionError(f"Daily {column} length differs")
        maximum = float(np.max(np.abs(actual_values - expected_values))) if len(actual_values) else 0.0
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"PyBroker/reference {column} mismatch: {maximum}")
    actual_orders = result.orders.reset_index()
    expected_orders = reference.orders.reset_index(drop=True)
    if len(actual_orders) != len(expected_orders):
        raise AssertionError(
            f"Order count mismatch: PyBroker={len(actual_orders)}, reference={len(expected_orders)}"
        )
    if len(actual_orders):
        if actual_orders["type"].tolist() != expected_orders["type"].tolist():
            raise AssertionError("Order side sequence differs from reference")
        if pd.to_datetime(actual_orders["date"]).tolist() != pd.to_datetime(
            expected_orders["date"]
        ).tolist():
            raise AssertionError("Order execution dates differ from reference")
        for column in ("shares", "fill_price"):
            maximum = float(
                np.max(
                    np.abs(
                        actual_orders[column].to_numpy(float)
                        - expected_orders[column].to_numpy(float)
                    )
                )
            )
            differences[f"max_abs_order_{column}_difference"] = maximum
            if maximum > tolerance:
                raise AssertionError(f"Order {column} differs from reference: {maximum}")
    else:
        differences["max_abs_order_shares_difference"] = 0.0
        differences["max_abs_order_fill_price_difference"] = 0.0
    return differences


def identified_orders(
    result,
    reference,
    *,
    case_id: str,
    case_index: int,
    window_id: str,
    fast_window: int,
    slow_window: int,
    cost_bps: float,
) -> pd.DataFrame:
    if result.orders.empty:
        return pd.DataFrame()
    frame = result.orders.reset_index().rename(columns={"id": "pybroker_order_id"})
    ref = reference.orders.reset_index(drop=True)
    metadata = [
        ("case_id", case_id),
        ("case_index", case_index),
        ("window_id", window_id),
        ("fast_window", fast_window),
        ("slow_window", slow_window),
        ("cost_bps", cost_bps),
    ]
    for offset, (name, value) in enumerate(metadata):
        frame.insert(offset, name, value)
    frame["signal_date"] = pd.to_datetime(ref["signal_date"]).dt.strftime("%Y-%m-%d")
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["raw_price"] = ref["raw_price"].to_numpy(float)
    frame["implicit_cost"] = ref["implicit_cost"].to_numpy(float)
    return frame


def identified_trades(
    result,
    *,
    case_id: str,
    case_index: int,
    window_id: str,
    fast_window: int,
    slow_window: int,
    cost_bps: float,
) -> pd.DataFrame:
    if result.trades.empty:
        return pd.DataFrame()
    frame = result.trades.reset_index().rename(columns={"id": "pybroker_trade_id"})
    metadata = [
        ("case_id", case_id),
        ("case_index", case_index),
        ("window_id", window_id),
        ("fast_window", fast_window),
        ("slow_window", slow_window),
        ("cost_bps", cost_bps),
    ]
    for offset, (name, value) in enumerate(metadata):
        frame.insert(offset, name, value)
    for column in ("entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column]).dt.strftime("%Y-%m-%d")
    return frame


def run_fast_window(fast_window: int) -> list[dict[str, Any]]:
    state = _STATE
    raw = state["raw"]
    parameters = state["parameters"]
    slow_start = int(parameters["slow_window_start"])
    slow_end = int(parameters["slow_window_end"])
    cost_bps = float(state["cost_bps"])
    policy = ExplicitFillPolicy("open", cost_bps=cost_bps)
    zero_policy = ExplicitFillPolicy("open", cost_bps=0)
    outputs: list[dict[str, Any]] = []
    for slow_window in range(slow_start, slow_end + 1):
        if fast_window >= slow_window:
            continue
        spec = DualSmaStateSpec(fast_window, slow_window)
        prepared = attach_precomputed_smas(
            raw,
            state["smas"][fast_window],
            state["smas"][slow_window],
        )
        for window in state["windows"]:
            analysis = analysis_slice(prepared, window["start"], window["end"])
            result = run_pybroker_dual_sma(
                analysis,
                spec,
                policy,
                initial_cash=float(state["initial_cash"]),
            )
            actual = pybroker_daily_state(result, analysis)
            reference = run_reference_dual_sma(
                analysis,
                spec,
                policy,
                initial_cash=float(state["initial_cash"]),
            )
            differences = cross_check(result, actual, reference)
            metrics = calculate_metrics(
                actual,
                result.orders.reset_index(),
                result.trades.reset_index(),
                initial_cash=float(state["initial_cash"]),
            )
            holding_metrics = calculate_holding_period_metrics(
                actual,
                initial_cash=float(state["initial_cash"]),
            )
            reference_holding_metrics = calculate_holding_period_metrics(
                reference.daily,
                initial_cash=float(state["initial_cash"]),
            )
            if holding_metrics["holding_sessions"] != reference_holding_metrics["holding_sessions"]:
                raise AssertionError("PyBroker/reference holding-session count differs")
            holding_cagr_difference = abs(
                float(holding_metrics["holding_period_cagr_pct"])
                - float(reference_holding_metrics["holding_period_cagr_pct"])
            )
            differences["max_abs_holding_period_cagr_difference"] = holding_cagr_difference
            if holding_cagr_difference > 1e-9:
                raise AssertionError(
                    "PyBroker/reference holding-period CAGR mismatch: "
                    f"{holding_cagr_difference}"
                )
            zero_reference = run_reference_dual_sma(
                analysis,
                spec,
                zero_policy,
                initial_cash=float(state["initial_cash"]),
            )
            zero_metrics = calculate_metrics(
                zero_reference.daily,
                zero_reference.orders,
                zero_reference.trades,
                initial_cash=float(state["initial_cash"]),
            )
            zero_holding_metrics = calculate_holding_period_metrics(
                zero_reference.daily,
                initial_cash=float(state["initial_cash"]),
            )
            if holding_metrics["holding_sessions"] != zero_holding_metrics["holding_sessions"]:
                raise AssertionError("Cost scenario changed the holding-session count")
            if pd.to_datetime(reference.orders.get("date", pd.Series(dtype=str))).tolist() != pd.to_datetime(
                zero_reference.orders.get("date", pd.Series(dtype=str))
            ).tolist():
                raise AssertionError("Cost scenario changed the signal/order schedule")
            case_id = f"F{fast_window:03d}_S{slow_window:03d}__{window['window_id']}"
            case_index = int(state["case_indexes"][(str(window["window_id"]), fast_window, slow_window)])
            outputs.append(
                {
                    "case_id": case_id,
                    "window_id": str(window["window_id"]),
                    "window_label": str(window["label"]),
                    "window_start": pd.Timestamp(window["start"]).date().isoformat(),
                    "window_end": pd.Timestamp(window["end"]).date().isoformat(),
                    "fast_window": fast_window,
                    "slow_window": slow_window,
                    "metrics": {
                        **metrics,
                        **holding_metrics,
                        "zero_cost_total_return_pct": zero_metrics["total_return_pct"],
                        "zero_cost_cagr_pct": zero_metrics["cagr_pct"],
                        "zero_cost_holding_period_cagr_pct": zero_holding_metrics[
                            "holding_period_cagr_pct"
                        ],
                    },
                    "dates": actual["date"].dt.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
                    "cash": actual["cash"].to_numpy(np.float32),
                    "shares": actual["shares"].to_numpy(np.float32),
                    "equity": actual["equity"].to_numpy(np.float32),
                    "signal": reference.daily["signal"].to_numpy(np.int8),
                    "orders": identified_orders(
                        result,
                        reference,
                        case_id=case_id,
                        case_index=case_index,
                        window_id=str(window["window_id"]),
                        fast_window=fast_window,
                        slow_window=slow_window,
                        cost_bps=cost_bps,
                    ),
                    "trades": identified_trades(
                        result,
                        case_id=case_id,
                        case_index=case_index,
                        window_id=str(window["window_id"]),
                        fast_window=fast_window,
                        slow_window=slow_window,
                        cost_bps=cost_bps,
                    ),
                    "differences": differences,
                }
            )
    return outputs


def write_append(frame: pd.DataFrame, path: Path, header_state: dict[str, bool]) -> None:
    if frame.empty:
        return
    frame.to_csv(
        path,
        mode="a",
        header=not header_state.get(path.name, False),
        index=False,
        lineterminator="\n",
    )
    header_state[path.name] = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", type=float, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if args.symbol != "AAPL" or context.config["symbols"] != ["AAPL"]:
        raise ValueError("This frozen experiment requires AAPL only")
    if float(args.cost_bps) != 5.0 or context.config["cost_scenarios_bps_per_side"] != [5]:
        raise ValueError("This frozen experiment requires the formal 5 bps block")
    output_root = reserve_block(context, args.run_id, args.symbol, float(args.cost_bps))

    canonical_path = WORKSPACE_ROOT / "data/processed/daily/equities/AAPL.csv"
    registry_path = WORKSPACE_ROOT / "data/sp500_history_registry.json"
    manifest_path = WORKSPACE_ROOT / "data/processed/universes/sp500/manifest.json"
    security_master_path = WORKSPACE_ROOT / "data/processed/universes/sp500/security_master.csv"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    canonical_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    security_master = pd.read_csv(security_master_path)
    identity = security_master[security_master["symbol"].eq("AAPL")]
    if len(identity) != 1:
        raise RuntimeError("AAPL must have exactly one canonical security-master row")
    identity_row = identity.iloc[0]
    if identity_row["quality_status"] != "passed" or identity_row["member_price_coverage_status"] != "complete":
        raise RuntimeError("AAPL canonical price history has not passed the data gate")
    if canonical_manifest["baseline_status"] != "validated_for_point_in_time_research_with_documented_gaps":
        raise RuntimeError("S&P 500 historical price baseline is not validated")
    raw = pd.read_csv(canonical_path, parse_dates=["date"])
    raw = raw[raw["symbol"].eq("AAPL")].sort_values("date").reset_index(drop=True)
    if len(raw) != int(identity_row["price_rows"]):
        raise RuntimeError("AAPL row count differs from the security master")

    parameters = context.config["parameters"]
    pairs = valid_parameter_pairs(
        int(parameters["fast_window_start"]),
        int(parameters["fast_window_end"]),
        int(parameters["slow_window_start"]),
        int(parameters["slow_window_end"]),
    )
    if len(pairs) != int(parameters["valid_pair_count"]):
        raise RuntimeError("Frozen valid-pair count is inconsistent")
    windows = frozen_windows(raw, parameters["windows"])
    if len(windows) != int(parameters["window_count"]):
        raise RuntimeError("Frozen window count is inconsistent")
    combination_count = len(pairs) * len(windows)
    if combination_count != int(parameters["combination_count"]):
        raise RuntimeError("Frozen combination count is inconsistent")
    smas = precompute_smas(raw, int(parameters["slow_window_end"]))

    case_order = [
        (window["window_id"], fast, slow)
        for window in windows
        for fast, slow in pairs
    ]
    case_indexes = {key: index for index, key in enumerate(case_order)}
    max_bars = max(int(window["bar_count"]) for window in windows)
    temp_paths = {
        name: output_root / f".tmp_{name}.npy"
        for name in ("cash", "shares", "equity", "signal")
    }
    state_arrays = {
        "cash": np.lib.format.open_memmap(
            temp_paths["cash"], mode="w+", dtype=np.float32, shape=(combination_count, max_bars)
        ),
        "shares": np.lib.format.open_memmap(
            temp_paths["shares"], mode="w+", dtype=np.float32, shape=(combination_count, max_bars)
        ),
        "equity": np.lib.format.open_memmap(
            temp_paths["equity"], mode="w+", dtype=np.float32, shape=(combination_count, max_bars)
        ),
        "signal": np.lib.format.open_memmap(
            temp_paths["signal"], mode="w+", dtype=np.int8, shape=(combination_count, max_bars)
        ),
    }
    for name in ("cash", "shares", "equity"):
        state_arrays[name][:] = np.nan
    state_arrays["signal"][:] = 0

    started = time.perf_counter()
    configure_worker(
        raw,
        smas,
        windows,
        parameters,
        case_indexes,
        float(context.config["initial_cash"]),
        float(args.cost_bps),
    )
    fast_values = range(
        int(parameters["fast_window_start"]), int(parameters["fast_window_end"]) + 1
    )
    executor = None
    batches = None
    if args.workers == 1:
        batches = map(run_fast_window, fast_values)
    else:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=args.workers,
            mp_context=multiprocessing.get_context("fork"),
        )
        batches = executor.map(run_fast_window, fast_values, chunksize=1)

    records: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    maximum_differences: dict[str, float] = {}
    header_state: dict[str, bool] = {}
    try:
        for batch in batches:
            order_frames: list[pd.DataFrame] = []
            trade_frames: list[pd.DataFrame] = []
            for output in batch:
                key = (
                    output["window_id"],
                    int(output["fast_window"]),
                    int(output["slow_window"]),
                )
                case_index = case_indexes[key]
                bars = len(output["dates"])
                for name in ("cash", "shares", "equity", "signal"):
                    state_arrays[name][case_index, :bars] = output[name]
                records.append(
                    {
                        "case_id": output["case_id"],
                        "case_index": case_index,
                        "window_id": output["window_id"],
                        "window_label": output["window_label"],
                        "window_start": output["window_start"],
                        "window_end": output["window_end"],
                        "fast_window": output["fast_window"],
                        "slow_window": output["slow_window"],
                        **output["metrics"],
                        **output["differences"],
                    }
                )
                index_rows.append(
                    {
                        "case_id": output["case_id"],
                        "case_index": case_index,
                        "window_id": output["window_id"],
                        "fast_window": output["fast_window"],
                        "slow_window": output["slow_window"],
                        "bars": bars,
                        "date_start": output["dates"][0],
                        "date_end": output["dates"][-1],
                    }
                )
                order_frames.append(output["orders"])
                trade_frames.append(output["trades"])
                for name, value in output["differences"].items():
                    maximum_differences[name] = max(
                        maximum_differences.get(name, 0.0), float(value)
                    )
            valid_orders = [frame for frame in order_frames if not frame.empty]
            valid_trades = [frame for frame in trade_frames if not frame.empty]
            if valid_orders:
                write_append(
                    pd.concat(valid_orders, ignore_index=True),
                    output_root / "orders.csv",
                    header_state,
                )
            if valid_trades:
                write_append(
                    pd.concat(valid_trades, ignore_index=True),
                    output_root / "trades.csv",
                    header_state,
                )
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    if len(records) != combination_count:
        raise RuntimeError(f"Grid produced {len(records)} cases, expected {combination_count}")
    for array in state_arrays.values():
        array.flush()
    results = pd.DataFrame(records).sort_values("case_index").reset_index(drop=True)
    index_frame = pd.DataFrame(index_rows).sort_values("case_index").reset_index(drop=True)
    normalize_frame(results).to_csv(
        output_root / "parameter_results.csv", index=False, lineterminator="\n"
    )
    normalize_frame(index_frame).to_csv(
        output_root / "daily_state_index.csv", index=False, lineterminator="\n"
    )
    dates_rows = []
    for window in windows:
        window_dates = raw[raw["date"].between(window["start"], window["end"])]["date"]
        dates_rows.extend(
            {
                "window_id": window["window_id"],
                "bar_index": index,
                "date": pd.Timestamp(date).date().isoformat(),
            }
            for index, date in enumerate(window_dates)
        )
    pd.DataFrame(dates_rows).to_csv(
        output_root / "window_dates.csv", index=False, lineterminator="\n"
    )
    np.savez_compressed(
        output_root / "daily_signal_state.npz",
        cash=np.asarray(state_arrays["cash"]),
        shares=np.asarray(state_arrays["shares"]),
        equity=np.asarray(state_arrays["equity"]),
        signal=np.asarray(state_arrays["signal"]),
    )
    del state_arrays
    for path in temp_paths.values():
        path.unlink(missing_ok=True)

    benchmark_rows: list[dict[str, Any]] = []
    benchmark_daily: list[pd.DataFrame] = []
    for window in windows:
        spec = DualSmaStateSpec(1, int(parameters["slow_window_end"]))
        prepared = attach_precomputed_smas(raw, smas[1], smas[int(parameters["slow_window_end"])])
        analysis = analysis_slice(prepared, window["start"], window["end"])
        benchmark = run_buy_and_hold_reference(
            analysis,
            ExplicitFillPolicy("open", cost_bps=float(args.cost_bps)),
            initial_cash=float(context.config["initial_cash"]),
        )
        metrics = calculate_metrics(
            benchmark.daily,
            benchmark.orders,
            benchmark.trades,
            initial_cash=float(context.config["initial_cash"]),
        )
        metrics.update(
            calculate_holding_period_metrics(
                benchmark.daily,
                initial_cash=float(context.config["initial_cash"]),
            )
        )
        benchmark_rows.append(
            {
                "window_id": window["window_id"],
                "window_label": window["label"],
                "window_start": pd.Timestamp(window["start"]).date().isoformat(),
                "window_end": pd.Timestamp(window["end"]).date().isoformat(),
                **metrics,
            }
        )
        daily = benchmark.daily.copy()
        daily.insert(0, "window_id", window["window_id"])
        benchmark_daily.append(daily)
    normalize_frame(pd.DataFrame(benchmark_rows)).to_csv(
        output_root / "buy_hold_results.csv", index=False, lineterminator="\n"
    )
    normalize_frame(pd.concat(benchmark_daily, ignore_index=True)).to_csv(
        output_root / "buy_hold_daily.csv", index=False, lineterminator="\n"
    )

    summary = {
        "symbol": "AAPL",
        "cost_bps": float(args.cost_bps),
        "rectangular_cells_per_window": int(parameters["rectangular_cells_per_window"]),
        "invalid_role_cells_per_window": int(parameters["invalid_role_cells_per_window"]),
        "valid_pair_count": len(pairs),
        "window_count": len(windows),
        "strategy_case_count": len(records),
        "parallel_workers": args.workers,
        "state_storage_dtype": "float32 (formal reconciliation was performed in float64)",
        "windows": [
            {
                **{key: value for key, value in window.items() if key not in {"start", "end"}},
                "start": pd.Timestamp(window["start"]).date().isoformat(),
                "end": pd.Timestamp(window["end"]).date().isoformat(),
            }
            for window in windows
        ],
        "max_cross_check_differences": maximum_differences,
        "elapsed_seconds": time.perf_counter() - started,
    }
    (output_root / "metrics.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": args.run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": "AAPL",
        "cost_bps": float(args.cost_bps),
        "engine": "lib-pybroker 1.2.12 plus quantkit.dual_sma_state independent ledger for every valid case",
        "python": platform.python_version(),
        "parameters": parameters,
        "summary": summary,
        "max_cross_check_differences": maximum_differences,
        "source_file": str(canonical_path.relative_to(WORKSPACE_ROOT)),
        "source_file_sha256": sha256(canonical_path),
        "source_registry": str(registry_path.relative_to(WORKSPACE_ROOT)),
        "source_registry_sha256": sha256(registry_path),
        "source_manifest": str(manifest_path.relative_to(WORKSPACE_ROOT)),
        "source_manifest_sha256": sha256(manifest_path),
        "source_manifest_build_id": canonical_manifest["build_id"],
        "source_price_adjustment": registry["price_source"]["adjustment"],
        "security_master_entry": json_safe(identity_row.to_dict()),
        "artifacts": {},
    }
    for path in sorted(output_root.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    block_manifest = output_root / "manifest.json"
    block_manifest.write_text(
        json.dumps(json_safe(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_block_complete(context, args.run_id, "AAPL", float(args.cost_bps), block_manifest)
    print(
        json.dumps(
            {
                "cases": len(records),
                "seconds": summary["elapsed_seconds"],
                "max_difference": max(maximum_differences.values(), default=0.0),
            }
        )
    )


if __name__ == "__main__":
    main()
