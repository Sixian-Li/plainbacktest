#!/usr/bin/env python3
"""Run the frozen full-history Nasdaq-100 Strategy1 energy factor anatomy."""

from __future__ import annotations

import argparse
import gc
import json
import platform
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
from quantkit.nasdaq100_strategy1_energy_factor import (
    bootstrap_month_blocks,
    build_forward_events,
    daily_diagnostic_spreads,
    daily_rank_ic,
    enrich_energy_states,
    summarize_energy_buckets,
    summarize_states,
)
from scripts.run_nasdaq100_strategy1_rotation_factorial import (
    _load_shared,
    prepare_shared_artifacts,
)
from scripts.run_nasdaq100_stochrsi_rotation import json_safe, write_json


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.5__26-08-28__nasdaq100_strategy1_energy_factor_anatomy"
)
SYMBOL = "NASDAQ100_STRATEGY1_ENERGY_FACTOR"
QQQ_PATH = WORKSPACE_ROOT / "data/processed/daily/QQQ.csv"
CROSS_CHECK_TOLERANCE = 1e-10


def _energy_parameters(context) -> dict[str, Any]:
    parameters = context.config["parameters"]
    return {
        "buckets": parameters["energy_buckets"],
        "direction_lookback": int(parameters["energy_direction"]["lookback_xnys_sessions"]),
        "flat_tolerance": float(parameters["energy_direction"]["numerical_flat_tolerance"]),
        "high_threshold": float(parameters["high_energy_states"]["threshold"]),
        "percentile_window": int(parameters["own_history_percentile"]["window_xnys_sessions"]),
        "percentile_minimum": int(parameters["own_history_percentile"]["minimum_observations"]),
    }


def _prepare_shared(context, run_id: str) -> Path:
    run_root = context.run_root(run_id)
    shared = run_root / "shared"
    if shared.is_dir():
        _load_shared(shared)
        return shared
    building = Path(tempfile.mkdtemp(prefix="shared_building_", dir=run_root))
    manifest = prepare_shared_artifacts(
        context,
        building,
        allow_empty_analysis_price=True,
    )
    if manifest.get("score_error_count"):
        raise RuntimeError("full energy preparation contains score errors")
    state = pd.read_csv(building / "security_state.csv.gz", parse_dates=["date"])
    calendar = pd.DatetimeIndex(
        pd.read_csv(WORKSPACE_ROOT / "data/processed/calendars/XNYS.csv", parse_dates=["date"])["date"]
    ).normalize()
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    calendar = calendar[(calendar >= start) & (calendar <= end)]
    energy = enrich_energy_states(state, calendar, **_energy_parameters(context))
    energy.to_csv(building / "energy_observations.csv.gz", index=False, compression="gzip")
    manifest_path = building / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["energy_observation_rows"] = int(len(energy))
    payload["energy_observation_securities"] = int(energy["security_id"].nunique())
    payload["energy_parameters"] = _energy_parameters(context)
    path = building / "energy_observations.csv.gz"
    payload["artifacts"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(manifest_path, payload)
    building.rename(shared)
    del state, energy
    gc.collect()
    return shared


def _assign_era(dates: pd.Series, eras: list[dict[str, Any]]) -> pd.Series:
    values = pd.to_datetime(dates).dt.normalize()
    output = pd.Series(pd.NA, index=values.index, dtype="string")
    for item in eras:
        mask = values.between(pd.Timestamp(item["start"]), pd.Timestamp(item["end"]))
        output.loc[mask] = str(item["era_id"])
    if output.isna().any():
        raise AssertionError("an event date was not assigned to a frozen era")
    return output


def _era_bucket_summary(events: pd.DataFrame) -> pd.DataFrame:
    date_means = events.groupby(
        ["era_id", "signal_date", "horizon", "energy_bucket"],
        observed=True,
        as_index=False,
    )["excess_return"].mean()
    return date_means.groupby(
        ["era_id", "horizon", "energy_bucket"], observed=True, as_index=False
    ).agg(
        signal_date_count=("signal_date", "nunique"),
        date_equal_mean_excess_return=("excess_return", "mean"),
        date_equal_median_excess_return=("excess_return", "median"),
        positive_date_rate=("excess_return", lambda values: float((values > 0).mean())),
    )


def _era_ic_summary(ic: pd.DataFrame, eras: list[dict[str, Any]]) -> pd.DataFrame:
    rows = ic.copy()
    rows["era_id"] = _assign_era(rows["signal_date"], eras)
    return rows.groupby(["era_id", "horizon", "factor_variant"], as_index=False).agg(
        date_count=("rank_ic", "size"),
        mean_rank_ic=("rank_ic", "mean"),
        median_rank_ic=("rank_ic", "median"),
        positive_rank_ic_rate=("rank_ic", lambda values: float((values > 0).mean())),
    )


def _bootstrap_spreads(
    spreads: pd.DataFrame,
    *,
    replications: int,
    seed: int,
    confidence_level: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    keys = sorted(
        (str(spread_id), int(horizon))
        for spread_id, horizon in spreads[["spread_id", "horizon"]].drop_duplicates().itertuples(index=False)
    )
    for offset, (spread_id, horizon) in enumerate(keys):
        selected = spreads[
            spreads["spread_id"].eq(spread_id) & spreads["horizon"].eq(horizon)
        ][["signal_date", "spread"]].rename(columns={"signal_date": "date"})
        result = bootstrap_month_blocks(
            selected,
            value_column="spread",
            replications=replications,
            seed=seed + offset,
            confidence_level=confidence_level,
        )
        rows.append({"spread_id": spread_id, "horizon": horizon, **result})
    return pd.DataFrame(rows)


def _era_spreads(spreads: pd.DataFrame, eras: list[dict[str, Any]]) -> pd.DataFrame:
    rows = spreads.copy()
    rows["era_id"] = _assign_era(rows["signal_date"], eras)
    return rows.groupby(["era_id", "horizon", "spread_id"], as_index=False).agg(
        date_count=("spread", "size"),
        mean_spread=("spread", "mean"),
        median_spread=("spread", "median"),
        positive_date_rate=("spread", lambda values: float((values > 0).mean())),
    )


def _monotonicity(bucket_summary: pd.DataFrame) -> pd.DataFrame:
    order = {
        "E00_20": 0,
        "E20_50": 1,
        "E50_80": 2,
        "E80_90": 3,
        "E90_95": 4,
        "E95_99": 5,
        "E99_100": 6,
    }
    rows = []
    for horizon, group in bucket_summary.groupby("horizon"):
        selected = group.assign(bucket_order=group["energy_bucket"].astype(str).map(order)).dropna(
            subset=["bucket_order", "date_equal_mean_excess_return"]
        )
        rank_correlation = selected["bucket_order"].rank(method="average").corr(
            selected["date_equal_mean_excess_return"].rank(method="average")
        )
        differences = selected.sort_values("bucket_order")["date_equal_mean_excess_return"].diff().dropna()
        rows.append(
            {
                "horizon": int(horizon),
                "bucket_count": len(selected),
                "bucket_order_spearman": float(rank_correlation),
                "nondecreasing_adjacent_count": int(differences.ge(0).sum()),
                "adjacent_comparison_count": int(len(differences)),
            }
        )
    return pd.DataFrame(rows)


def _factor_decision(
    ic: pd.DataFrame,
    era_ic: pd.DataFrame,
    bootstrap: pd.DataFrame,
    era_spread: pd.DataFrame,
) -> dict[str, Any]:
    primary = {10, 20, 60}
    raw = ic[ic["factor_variant"].eq("RAW_SCORE") & ic["horizon"].isin(primary)]
    raw_full = raw.groupby("horizon")["rank_ic"].mean()
    raw_all_positive = bool(len(raw_full) == 3 and raw_full.gt(0).all())
    raw_era = era_ic[
        era_ic["factor_variant"].eq("RAW_SCORE") & era_ic["horizon"].isin(primary)
    ]
    raw_era_counts = raw_era.groupby("horizon")["mean_rank_ic"].apply(lambda values: int((values > 0).sum()))
    raw_stable_horizons = int(raw_era_counts.ge(4).sum())
    raw_supported = bool(raw_all_positive and raw_stable_horizons >= 2)

    def spread_support(spread_id: str) -> tuple[bool, dict[str, Any]]:
        selected = bootstrap[
            bootstrap["spread_id"].eq(spread_id) & bootstrap["horizon"].isin(primary)
        ].set_index("horizon")
        positive_horizons = int(selected["mean"].gt(0).sum())
        positive_lower_bounds = int(selected["ci_lower"].gt(0).sum())
        era = era_spread[
            era_spread["spread_id"].eq(spread_id) & era_spread["horizon"].isin(primary)
        ]
        era_counts = era.groupby("horizon")["mean_spread"].apply(lambda values: int((values > 0).sum()))
        stable_horizons = int(era_counts.ge(4).sum())
        supported = bool(
            positive_horizons >= 2 and positive_lower_bounds >= 1 and stable_horizons >= 2
        )
        return supported, {
            "positive_primary_horizons": positive_horizons,
            "positive_lower_bound_horizons": positive_lower_bounds,
            "stable_four_of_five_era_horizons": stable_horizons,
        }

    gate_supported, gate_detail = spread_support("HIGH_GT90_MINUS_NOT_HIGH")
    direction_supported, direction_detail = spread_support("HIGH_RISING_MINUS_HIGH_FALLING")
    return {
        "raw_score_ranking_supported": raw_supported,
        "raw_score_all_10_20_60_mean_ic_positive": raw_all_positive,
        "raw_score_stable_four_of_five_era_horizons": raw_stable_horizons,
        "high_energy_gate_supported": gate_supported,
        "high_energy_gate_detail": gate_detail,
        "high_energy_direction_supported": direction_supported,
        "high_energy_direction_detail": direction_detail,
    }


def run_cost_block(context, run_id: str, symbol: str, cost_bps: float) -> None:
    assert_run_writable(context, run_id)
    if symbol != SYMBOL:
        raise ValueError(f"unexpected symbol block: {symbol}")
    expected_costs = [float(value) for value in context.config["cost_scenarios_bps_per_side"]]
    if cost_bps not in expected_costs:
        raise ValueError(f"unexpected cost block: {cost_bps}")
    shared = _prepare_shared(context, run_id)
    shared_manifest = _load_shared(shared)
    run_root = context.run_root(run_id)
    temporary = Path(tempfile.mkdtemp(prefix=f"cost_{cost_bps:g}bps_building_", dir=run_root))
    energy = pd.read_csv(shared / "energy_observations.csv.gz", parse_dates=["date"])
    panel = pd.read_csv(shared / "price_panel.csv.gz", parse_dates=["date"])
    qqq = pd.read_csv(QQQ_PATH, parse_dates=["date"])
    calendar = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    parameters = context.config["parameters"]
    horizons = tuple(int(value) for value in parameters["forward_horizons_xnys_sessions"])
    events, exclusions, cross_checks = build_forward_events(
        energy,
        panel,
        qqq,
        calendar,
        horizons=horizons,
        cost_bps=cost_bps,
    )
    if events.empty:
        raise RuntimeError("factor event engine produced no observations")
    if max(cross_checks.values(), default=0.0) > CROSS_CHECK_TOLERANCE:
        raise AssertionError(f"event ledger cross-check failed: {cross_checks}")
    events["era_id"] = _assign_era(events["signal_date"], parameters["era_windows"])
    bucket = summarize_energy_buckets(events)
    states = summarize_states(events)
    ic = daily_rank_ic(
        events,
        minimum_securities=int(parameters["cross_sectional_ic"]["minimum_securities_per_date"]),
    )
    era_ic = _era_ic_summary(ic, parameters["era_windows"])
    spreads = daily_diagnostic_spreads(events)
    bootstrap_params = parameters["bootstrap"]
    bootstrap = _bootstrap_spreads(
        spreads,
        replications=int(bootstrap_params["replications"]),
        seed=int(bootstrap_params["seed"]),
        confidence_level=float(bootstrap_params["confidence_level"]),
    )
    era_spread = _era_spreads(spreads, parameters["era_windows"])
    era_bucket = _era_bucket_summary(events)
    monotonicity = _monotonicity(bucket)
    decision = _factor_decision(ic, era_ic, bootstrap, era_spread)

    events.to_csv(temporary / "events.csv.gz", index=False, compression="gzip")
    exclusions.to_csv(temporary / "excluded_observations.csv.gz", index=False, compression="gzip")
    bucket.to_csv(temporary / "bucket_summary.csv", index=False, lineterminator="\n")
    states.to_csv(temporary / "state_summary.csv", index=False, lineterminator="\n")
    ic.to_csv(temporary / "daily_rank_ic.csv.gz", index=False, compression="gzip")
    era_ic.to_csv(temporary / "era_rank_ic_summary.csv", index=False, lineterminator="\n")
    spreads.to_csv(temporary / "daily_spreads.csv.gz", index=False, compression="gzip")
    bootstrap.to_csv(temporary / "bootstrap_spreads.csv", index=False, lineterminator="\n")
    era_spread.to_csv(temporary / "era_spreads.csv", index=False, lineterminator="\n")
    era_bucket.to_csv(temporary / "era_bucket_summary.csv", index=False, lineterminator="\n")
    monotonicity.to_csv(temporary / "bucket_monotonicity.csv", index=False, lineterminator="\n")
    metrics = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "symbol": symbol,
        "cost_bps": cost_bps,
        "data_status": shared_manifest["dataset_status"],
        "energy_observation_count": int(shared_manifest["energy_observation_rows"]),
        "event_count": int(len(events)),
        "event_security_count": int(events["security_id"].nunique()),
        "event_signal_date_count": int(events["signal_date"].nunique()),
        "excluded_observation_count": int(len(exclusions)),
        "terminal_truncation_count": int(events["terminal_truncated"].sum()),
        "requested_exit_synthetic_count": int(events["requested_exit_synthetic"].sum()),
        "factor_decision": decision,
        "bucket_monotonicity": monotonicity.to_dict("records"),
        "bootstrap_spreads": bootstrap.to_dict("records"),
        "max_cross_check_differences": cross_checks,
    }
    write_json(temporary / "metrics.json", metrics)
    manifest = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "experiment_run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": symbol,
        "cost_bps": cost_bps,
        "engine": "quantkit.nasdaq100_strategy1_energy_factor independent normalized event engine",
        "reference_engine": "direct price-index and return reconstruction for every event; path extrema regression-tested",
        "portfolio_engine": "not applicable: overlapping event accounts intentionally do not share capital",
        "python": platform.python_version(),
        "analysis_start": parameters["analysis_start"],
        "analysis_end": parameters["analysis_end"],
        "event_count": len(events),
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
    print(
        f"cost={cost_bps:g}bps energy factor: observations={len(energy):,}; "
        f"events={len(events):,}; IC support={decision['raw_score_ranking_supported']}; "
        f"gate support={decision['high_energy_gate_supported']}; "
        f"direction support={decision['high_energy_direction_supported']}",
        flush=True,
    )
    del energy, panel, events, exclusions, bucket, states, ic, spreads
    gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cost-bps", required=True, type=float)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_cost_block(context, args.run_id, args.symbol, args.cost_bps)


if __name__ == "__main__":
    main()
