"""Causal event-study tools for the frozen Nasdaq-100 Strategy1 energy score."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer


REQUIRED_STATE_COLUMNS = {
    "date",
    "security_id",
    "strategy1_weight",
    "in_universe",
}
REQUIRED_PANEL_COLUMNS = {
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "synthetic_bar",
    "prelisting_proxy",
    "terminal_settlement_proxy",
}


def _normalized_calendar(calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().sort_values().unique()
    if len(dates) < 2:
        raise ValueError("at least two unique calendar sessions are required")
    return dates


def _validate_buckets(buckets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    specs = [dict(item) for item in buckets]
    if not specs:
        raise ValueError("energy buckets cannot be empty")
    prior_upper: float | None = None
    ids: set[str] = set()
    for index, item in enumerate(specs):
        bucket_id = str(item["bucket_id"])
        lower = float(item["lower"])
        upper = float(item["upper"])
        if bucket_id in ids or not np.isfinite([lower, upper]).all() or lower >= upper:
            raise ValueError(f"invalid energy bucket: {item}")
        if prior_upper is not None and abs(lower - prior_upper) > 1e-12:
            raise ValueError("energy buckets must be contiguous")
        if index < len(specs) - 1 and bool(item.get("upper_inclusive", False)):
            raise ValueError("only the final bucket may include its upper boundary")
        prior_upper = upper
        ids.add(bucket_id)
    if abs(float(specs[0]["lower"])) > 1e-12 or abs(float(specs[-1]["upper"]) - 1.0) > 1e-12:
        raise ValueError("energy buckets must cover zero through one")
    if not bool(specs[-1].get("upper_inclusive", False)):
        raise ValueError("final energy bucket must include one")
    return specs


def assign_energy_buckets(
    scores: pd.Series,
    buckets: Sequence[Mapping[str, Any]],
) -> pd.Series:
    """Assign frozen left-closed energy intervals, including one in the last interval."""

    specs = _validate_buckets(buckets)
    values = pd.to_numeric(scores, errors="coerce").astype(float)
    if ((values.dropna() < -1e-10) | (values.dropna() > 1.0 + 1e-10)).any():
        raise ValueError("Strategy1 energy lies outside zero through one")
    values = values.clip(0.0, 1.0)
    labels = [str(item["bucket_id"]) for item in specs]
    output = pd.Series(pd.NA, index=values.index, dtype="string")
    for item in specs:
        lower = float(item["lower"])
        upper = float(item["upper"])
        mask = values.ge(lower) & (
            values.le(upper) if bool(item.get("upper_inclusive", False)) else values.lt(upper)
        )
        output.loc[mask] = str(item["bucket_id"])
    if output[values.notna()].isna().any():
        raise AssertionError("a finite energy score was not assigned to a bucket")
    return output.astype(pd.CategoricalDtype(categories=labels, ordered=True))


def _rolling_current_percentile(values: pd.Series, window: int, minimum: int) -> pd.Series:
    if window < 2 or not 1 <= minimum <= window:
        raise ValueError("invalid causal percentile window")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    return numeric.rolling(window, min_periods=minimum).rank(method="average", pct=True)


def enrich_energy_states(
    state: pd.DataFrame,
    calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    *,
    buckets: Sequence[Mapping[str, Any]],
    direction_lookback: int,
    flat_tolerance: float,
    high_threshold: float,
    percentile_window: int,
    percentile_minimum: int,
) -> pd.DataFrame:
    """Classify every point-in-time member observation without using future scores."""

    if missing := REQUIRED_STATE_COLUMNS.difference(state.columns):
        raise ValueError(f"state table misses {sorted(missing)}")
    if direction_lookback < 1 or flat_tolerance < 0 or not 0 < high_threshold < 1:
        raise ValueError("invalid energy state parameters")
    dates = _normalized_calendar(calendar)
    positions = pd.Series(np.arange(len(dates), dtype=np.int32), index=dates)
    rows = state.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["security_id"] = rows["security_id"].astype(str)
    rows["strategy1_weight"] = pd.to_numeric(rows["strategy1_weight"], errors="coerce")
    rows = rows.sort_values(["security_id", "date"], kind="stable").reset_index(drop=True)
    if rows.duplicated(["security_id", "date"]).any():
        raise ValueError("state table contains duplicate security dates")
    rows["session_index"] = rows["date"].map(positions)
    if rows["session_index"].isna().any():
        raise ValueError("state dates must lie on the frozen XNYS calendar")
    rows["session_index"] = rows["session_index"].astype(np.int32)
    rows["own_history_percentile"] = (
        rows.groupby("security_id", sort=False)["strategy1_weight"]
        .transform(
            lambda values: _rolling_current_percentile(
                values, percentile_window, percentile_minimum
            )
        )
        .astype(float)
    )

    eligible = rows[rows["in_universe"].astype(bool) & rows["strategy1_weight"].notna()].copy()
    lookup = rows[["security_id", "session_index", "strategy1_weight"]]
    for lag in range(1, direction_lookback + 1):
        lagged = lookup.copy()
        lagged["session_index"] = lagged["session_index"] + lag
        lagged = lagged.rename(columns={"strategy1_weight": f"score_lag_{lag}"})
        eligible = eligible.merge(
            lagged,
            on=["security_id", "session_index"],
            how="left",
            validate="many_to_one",
        )
    current = eligible["strategy1_weight"].astype(float)
    lagged_direction = eligible[f"score_lag_{direction_lookback}"].astype(float)
    change = current - lagged_direction
    eligible["energy_change_5"] = change
    eligible["direction"] = np.select(
        [
            lagged_direction.isna(),
            change.gt(flat_tolerance),
            change.lt(-flat_tolerance),
        ],
        ["UNAVAILABLE", "RISING", "FALLING"],
        default="FLAT",
    )
    eligible["energy_bucket"] = assign_energy_buckets(current, buckets)
    eligible["high_energy"] = current.gt(high_threshold)
    previous = eligible["score_lag_1"].astype(float)
    cross_above = previous.le(high_threshold) & current.gt(high_threshold)
    cross_below = previous.gt(high_threshold) & current.le(high_threshold)
    persistent = current.gt(high_threshold)
    for lag in range(1, direction_lookback + 1):
        persistent &= eligible[f"score_lag_{lag}"].astype(float).gt(high_threshold)
    eligible["high_persistent"] = persistent
    labels = np.full(len(eligible), "NOT_HIGH_UNAVAILABLE", dtype=object)
    direction_values = eligible["direction"].astype(str).to_numpy()
    high_values = eligible["high_energy"].to_numpy(bool)
    persistent_values = persistent.to_numpy(bool)
    for direction in ("RISING", "FLAT", "FALLING", "UNAVAILABLE"):
        labels[(~high_values) & (direction_values == direction)] = f"NOT_HIGH_{direction}"
        labels[high_values & (~persistent_values) & (direction_values == direction)] = (
            f"HIGH_RECENT_{direction}"
        )
        labels[persistent_values & (direction_values == direction)] = (
            f"HIGH_PERSIST_{direction}"
        )
    labels[cross_above.to_numpy(bool)] = "CROSS_ABOVE_90"
    labels[cross_below.to_numpy(bool)] = "CROSS_BELOW_90"
    eligible["high_state"] = labels
    eligible = eligible.drop(columns=[f"score_lag_{lag}" for lag in range(1, direction_lookback + 1)])
    return eligible.sort_values(["date", "security_id"], kind="stable").reset_index(drop=True)


def _validated_dense_panel(
    panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
    if missing := REQUIRED_PANEL_COLUMNS.difference(panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    rows = panel.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["symbol"] = rows["symbol"].astype(str)
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows.duplicated(["symbol", "date"]).any():
        raise ValueError("price panel contains duplicate symbol dates")
    expected = len(calendar)
    counts = rows.groupby("symbol", sort=False).size()
    if counts.empty or not counts.eq(expected).all():
        raise ValueError("every security price panel must be dense on the frozen calendar")
    if not rows.groupby("symbol", sort=False)["date"].apply(
        lambda values: pd.DatetimeIndex(values).equals(calendar)
    ).all():
        raise ValueError("dense price panel dates differ from the frozen calendar")
    prices = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("dense price panel contains invalid OHLC")
    return rows


def _validated_qqq(qqq: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close"}
    if missing := required.difference(qqq.columns):
        raise ValueError(f"QQQ table misses {sorted(missing)}")
    rows = qqq.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows[rows["date"].isin(calendar)].drop_duplicates("date", keep="last")
    rows = rows.set_index("date").reindex(calendar).reset_index(names="date")
    prices = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("QQQ does not fully cover the event calendar")
    return rows


def build_forward_events(
    state: pd.DataFrame,
    panel: pd.DataFrame,
    qqq: pd.DataFrame,
    calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    *,
    horizons: Sequence[int],
    cost_bps: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Build independent next-Open event returns with explicit terminal handling."""

    dates = _normalized_calendar(calendar)
    horizon_values = tuple(int(value) for value in horizons)
    if not horizon_values or any(value < 1 for value in horizon_values):
        raise ValueError("forward horizons must be positive")
    if len(set(horizon_values)) != len(horizon_values):
        raise ValueError("forward horizons must be unique")
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost bps must be finite and non-negative")
    required_state = {
        "date", "security_id", "strategy1_weight", "energy_bucket", "direction",
        "high_energy", "high_state", "own_history_percentile",
    }
    if missing := required_state.difference(state.columns):
        raise ValueError(f"enriched state misses {sorted(missing)}")
    prices = _validated_dense_panel(panel, dates)
    benchmark = _validated_qqq(qqq, dates)
    date_positions = pd.Series(np.arange(len(dates), dtype=np.int32), index=dates)
    rows = state.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["security_id"] = rows["security_id"].astype(str)
    rows["signal_index"] = rows["date"].map(date_positions)
    if rows["signal_index"].isna().any():
        raise ValueError("state contains a date outside the event calendar")
    rows["signal_index"] = rows["signal_index"].astype(np.int32)
    q_open = benchmark["open"].to_numpy(float)
    q_close = benchmark["close"].to_numpy(float)
    cost = float(cost_bps) / 10_000.0
    events: list[pd.DataFrame] = []
    exclusions: list[dict[str, Any]] = []
    checks = {
        "max_abs_entry_price_difference": 0.0,
        "max_abs_exit_price_difference": 0.0,
        "max_abs_security_return_difference": 0.0,
        "max_abs_qqq_return_difference": 0.0,
    }
    state_groups = rows.groupby("security_id", sort=False)
    for security_id, security_prices in prices.groupby("symbol", sort=False):
        if security_id not in state_groups.groups:
            continue
        observations = state_groups.get_group(security_id).copy()
        frame = security_prices.sort_values("date", kind="stable").reset_index(drop=True)
        open_values = frame["open"].to_numpy(float)
        high_values = frame["high"].to_numpy(float)
        low_values = frame["low"].to_numpy(float)
        close_values = frame["close"].to_numpy(float)
        synthetic = frame["synthetic_bar"].to_numpy(int).astype(bool)
        prelisting = frame["prelisting_proxy"].to_numpy(int).astype(bool)
        terminal = frame["terminal_settlement_proxy"].to_numpy(int).astype(bool)
        real = ~(synthetic | prelisting | terminal)
        real_positions = np.flatnonzero(real)
        if not len(real_positions):
            exclusions.extend(
                {
                    "security_id": security_id,
                    "signal_date": date,
                    "reason": "NO_REAL_PRICE",
                }
                for date in observations["date"]
            )
            continue
        last_real = int(real_positions[-1])
        signal_indices = observations["signal_index"].to_numpy(np.int32)
        entry_indices = signal_indices + 1
        in_calendar = entry_indices < len(dates)
        entry_is_real = np.zeros(len(observations), dtype=bool)
        entry_is_real[in_calendar] = real[entry_indices[in_calendar]]
        rejected = observations.loc[~entry_is_real, ["date"]]
        exclusions.extend(
            {
                "security_id": security_id,
                "signal_date": item.date,
                "reason": "ENTRY_AFTER_CALENDAR" if int(index) >= len(dates) else "ENTRY_NOT_REAL",
            }
            for index, item in zip(entry_indices[~entry_is_real], rejected.itertuples(index=False))
        )
        observations = observations.loc[entry_is_real].copy()
        if observations.empty:
            continue
        signal_indices = signal_indices[entry_is_real]
        entry_indices = entry_indices[entry_is_real]
        for horizon in horizon_values:
            planned = signal_indices + horizon
            has_horizon = planned < len(dates)
            if not has_horizon.any():
                continue
            selected = observations.loc[has_horizon].copy()
            selected_signal = signal_indices[has_horizon]
            selected_entry = entry_indices[has_horizon]
            selected_planned = planned[has_horizon]
            actual_exit = np.minimum(selected_planned, last_real)
            valid_exit = actual_exit >= selected_entry
            if not valid_exit.all():
                invalid = selected.loc[~valid_exit, "date"]
                exclusions.extend(
                    {
                        "security_id": security_id,
                        "signal_date": date,
                        "reason": "NO_REAL_EXIT_AFTER_ENTRY",
                    }
                    for date in invalid
                )
                selected = selected.loc[valid_exit].copy()
                selected_signal = selected_signal[valid_exit]
                selected_entry = selected_entry[valid_exit]
                selected_planned = selected_planned[valid_exit]
                actual_exit = actual_exit[valid_exit]
            if selected.empty:
                continue
            window = FixedForwardWindowIndexer(window_size=horizon)
            future_high = pd.Series(high_values).rolling(window, min_periods=1).max().to_numpy(float)
            future_low = pd.Series(low_values).rolling(window, min_periods=1).min().to_numpy(float)
            entry_reference = open_values[selected_entry]
            exit_reference = close_values[actual_exit]
            entry_fill = entry_reference * (1.0 + cost)
            exit_fill = exit_reference * (1.0 - cost)
            q_entry_fill = q_open[selected_entry] * (1.0 + cost)
            q_exit_fill = q_close[actual_exit] * (1.0 - cost)
            security_return = exit_fill / entry_fill - 1.0
            qqq_return = q_exit_fill / q_entry_fill - 1.0
            path_high = future_high[selected_entry].copy()
            path_low = future_low[selected_entry].copy()
            truncated_positions = np.flatnonzero(actual_exit < selected_planned)
            for position in truncated_positions:
                path_start = int(selected_entry[position])
                path_end = int(actual_exit[position]) + 1
                path_high[position] = float(np.max(high_values[path_start:path_end]))
                path_low[position] = float(np.min(low_values[path_start:path_end]))
            selected["signal_date"] = selected["date"]
            selected["entry_date"] = dates[selected_entry]
            selected["requested_exit_date"] = dates[selected_planned]
            selected["exit_date"] = dates[actual_exit]
            selected["horizon"] = int(horizon)
            selected["actual_holding_sessions"] = actual_exit - selected_signal
            selected["entry_reference_price"] = entry_reference
            selected["exit_reference_price"] = exit_reference
            selected["entry_fill_price"] = entry_fill
            selected["exit_fill_price"] = exit_fill
            selected["security_return"] = security_return
            selected["qqq_return"] = qqq_return
            selected["excess_return"] = security_return - qqq_return
            selected["maximum_favorable_excursion"] = (
                path_high * (1.0 - cost) / entry_fill - 1.0
            )
            selected["maximum_adverse_excursion"] = (
                path_low * (1.0 - cost) / entry_fill - 1.0
            )
            selected["terminal_truncated"] = selected_planned > last_real
            selected["requested_exit_synthetic"] = synthetic[selected_planned]
            selected["cost_bps"] = float(cost_bps)
            keep = [
                "security_id", "signal_date", "entry_date", "requested_exit_date", "exit_date",
                "horizon", "actual_holding_sessions", "strategy1_weight", "energy_bucket",
                "direction", "high_energy", "high_state", "own_history_percentile",
                "entry_reference_price", "exit_reference_price", "entry_fill_price",
                "exit_fill_price", "security_return", "qqq_return", "excess_return",
                "maximum_favorable_excursion", "maximum_adverse_excursion",
                "terminal_truncated", "requested_exit_synthetic", "cost_bps",
            ]
            for optional in ("display_ticker", "source_file", "energy_change_5", "high_persistent"):
                if optional in selected.columns:
                    keep.insert(1, optional)
            events.append(selected[keep])

            reference_security_return = (
                close_values[actual_exit] * (1.0 - cost)
                / (open_values[selected_entry] * (1.0 + cost))
                - 1.0
            )
            reference_qqq_return = (
                q_close[actual_exit] * (1.0 - cost)
                / (q_open[selected_entry] * (1.0 + cost))
                - 1.0
            )
            checks["max_abs_entry_price_difference"] = max(
                checks["max_abs_entry_price_difference"],
                float(np.max(np.abs(entry_reference - open_values[selected_entry]))),
            )
            checks["max_abs_exit_price_difference"] = max(
                checks["max_abs_exit_price_difference"],
                float(np.max(np.abs(exit_reference - close_values[actual_exit]))),
            )
            checks["max_abs_security_return_difference"] = max(
                checks["max_abs_security_return_difference"],
                float(np.max(np.abs(security_return - reference_security_return))),
            )
            checks["max_abs_qqq_return_difference"] = max(
                checks["max_abs_qqq_return_difference"],
                float(np.max(np.abs(qqq_return - reference_qqq_return))),
            )
    output = pd.concat(events, ignore_index=True) if events else pd.DataFrame()
    excluded = pd.DataFrame(exclusions, columns=["security_id", "signal_date", "reason"])
    return output, excluded, checks


def _spearman(values: pd.DataFrame, factor: str) -> float:
    selected = values[[factor, "excess_return"]].dropna()
    if selected[factor].nunique() < 2 or selected["excess_return"].nunique() < 2:
        return float("nan")
    factor_rank = selected[factor].rank(method="average")
    return float(factor_rank.corr(selected["excess_return"].rank(method="average")))


def daily_rank_ic(events: pd.DataFrame, *, minimum_securities: int) -> pd.DataFrame:
    """Compute daily cross-sectional raw-score and own-history-percentile IC."""

    if minimum_securities < 2:
        raise ValueError("minimum cross section must be at least two")
    required = {
        "signal_date", "horizon", "strategy1_weight", "own_history_percentile", "excess_return"
    }
    if missing := required.difference(events.columns):
        raise ValueError(f"event table misses {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for (date, horizon), group in events.groupby(["signal_date", "horizon"], sort=True):
        for variant, column in (
            ("RAW_SCORE", "strategy1_weight"),
            ("OWN_HISTORY_PERCENTILE", "own_history_percentile"),
        ):
            selected = group[[column, "excess_return"]].dropna()
            if len(selected) < minimum_securities:
                continue
            value = _spearman(selected, column)
            if np.isfinite(value):
                rows.append(
                    {
                        "signal_date": pd.Timestamp(date),
                        "horizon": int(horizon),
                        "factor_variant": variant,
                        "security_count": len(selected),
                        "rank_ic": value,
                    }
                )
    return pd.DataFrame(
        rows,
        columns=["signal_date", "horizon", "factor_variant", "security_count", "rank_ic"],
    )


def bootstrap_month_blocks(
    daily: pd.DataFrame,
    *,
    value_column: str,
    replications: int,
    seed: int,
    confidence_level: float = 0.95,
) -> dict[str, float | int]:
    """Deterministic calendar-month block bootstrap over daily aggregates."""

    if not {"date", value_column}.issubset(daily.columns):
        raise ValueError("bootstrap input misses date or value")
    if replications < 2 or not 0 < confidence_level < 1:
        raise ValueError("invalid bootstrap parameters")
    rows = daily[["date", value_column]].dropna().copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    if rows.empty:
        return {
            "count": 0,
            "month_count": 0,
            "mean": float("nan"),
            "ci_lower": float("nan"),
            "ci_upper": float("nan"),
        }
    rows["month"] = rows["date"].dt.to_period("M").astype(str)
    monthly = rows.groupby("month", sort=True)[value_column].agg(["sum", "count"])
    sums = monthly["sum"].to_numpy(float)
    counts = monthly["count"].to_numpy(float)
    rng = np.random.default_rng(int(seed))
    choices = rng.integers(0, len(monthly), size=(int(replications), len(monthly)))
    estimates = sums[choices].sum(axis=1) / counts[choices].sum(axis=1)
    alpha = (1.0 - confidence_level) / 2.0
    return {
        "count": int(len(rows)),
        "month_count": int(len(monthly)),
        "mean": float(rows[value_column].mean()),
        "ci_lower": float(np.quantile(estimates, alpha)),
        "ci_upper": float(np.quantile(estimates, 1.0 - alpha)),
    }


def summarize_energy_buckets(events: pd.DataFrame) -> pd.DataFrame:
    required = {
        "signal_date", "security_id", "horizon", "energy_bucket", "security_return",
        "excess_return", "maximum_favorable_excursion", "maximum_adverse_excursion",
        "terminal_truncated", "requested_exit_synthetic",
    }
    if missing := required.difference(events.columns):
        raise ValueError(f"event table misses {sorted(missing)}")
    pooled = events.groupby(["horizon", "energy_bucket"], observed=True, as_index=False).agg(
        event_count=("excess_return", "size"),
        signal_date_count=("signal_date", "nunique"),
        security_count=("security_id", "nunique"),
        pooled_mean_security_return=("security_return", "mean"),
        pooled_median_security_return=("security_return", "median"),
        pooled_mean_excess_return=("excess_return", "mean"),
        pooled_median_excess_return=("excess_return", "median"),
        positive_excess_rate=("excess_return", lambda values: float((values > 0).mean())),
        mean_maximum_favorable_excursion=("maximum_favorable_excursion", "mean"),
        mean_maximum_adverse_excursion=("maximum_adverse_excursion", "mean"),
        terminal_truncation_rate=("terminal_truncated", "mean"),
        synthetic_target_rate=("requested_exit_synthetic", "mean"),
    )
    date_means = events.groupby(
        ["signal_date", "horizon", "energy_bucket"], observed=True, as_index=False
    )["excess_return"].mean()
    date_summary = date_means.groupby(
        ["horizon", "energy_bucket"], observed=True, as_index=False
    ).agg(
        date_equal_mean_excess_return=("excess_return", "mean"),
        date_equal_median_excess_return=("excess_return", "median"),
    )
    security_means = events.groupby(
        ["security_id", "horizon", "energy_bucket"], observed=True, as_index=False
    )["excess_return"].mean()
    security_summary = security_means.groupby(
        ["horizon", "energy_bucket"], observed=True, as_index=False
    ).agg(
        security_equal_mean_excess_return=("excess_return", "mean"),
        security_equal_median_excess_return=("excess_return", "median"),
    )
    return pooled.merge(
        date_summary, on=["horizon", "energy_bucket"], how="left", validate="one_to_one"
    ).merge(
        security_summary,
        on=["horizon", "energy_bucket"],
        how="left",
        validate="one_to_one",
    )


def summarize_states(events: pd.DataFrame) -> pd.DataFrame:
    required = {
        "signal_date", "security_id", "horizon", "high_energy", "direction", "high_state",
        "excess_return", "security_return",
    }
    if missing := required.difference(events.columns):
        raise ValueError(f"event table misses {sorted(missing)}")
    return events.groupby(
        ["horizon", "high_energy", "direction", "high_state"], as_index=False
    ).agg(
        event_count=("excess_return", "size"),
        signal_date_count=("signal_date", "nunique"),
        security_count=("security_id", "nunique"),
        mean_security_return=("security_return", "mean"),
        median_security_return=("security_return", "median"),
        mean_excess_return=("excess_return", "mean"),
        median_excess_return=("excess_return", "median"),
        positive_excess_rate=("excess_return", lambda values: float((values > 0).mean())),
    )


def daily_diagnostic_spreads(events: pd.DataFrame) -> pd.DataFrame:
    """Return daily HIGH, top-bucket, and high-direction cross-sectional spreads."""

    required = {
        "signal_date", "horizon", "energy_bucket", "high_energy", "direction", "excess_return"
    }
    if missing := required.difference(events.columns):
        raise ValueError(f"event table misses {sorted(missing)}")
    rows: list[pd.DataFrame] = []

    high = events.groupby(
        ["signal_date", "horizon", "high_energy"], as_index=False
    )["excess_return"].mean()
    high = high.pivot(index=["signal_date", "horizon"], columns="high_energy", values="excess_return")
    if False in high.columns and True in high.columns:
        item = (high[True] - high[False]).rename("spread").dropna().reset_index()
        item["spread_id"] = "HIGH_GT90_MINUS_NOT_HIGH"
        rows.append(item)

    bucket = events[events["energy_bucket"].isin(["E99_100", "E90_95"])].groupby(
        ["signal_date", "horizon", "energy_bucket"], observed=True, as_index=False
    )["excess_return"].mean()
    bucket = bucket.pivot(index=["signal_date", "horizon"], columns="energy_bucket", values="excess_return")
    if {"E99_100", "E90_95"}.issubset(bucket.columns):
        item = (bucket["E99_100"] - bucket["E90_95"]).rename("spread").dropna().reset_index()
        item["spread_id"] = "E99_100_MINUS_E90_95"
        rows.append(item)

    direction = events[
        events["high_energy"].astype(bool) & events["direction"].isin(["RISING", "FALLING"])
    ].groupby(
        ["signal_date", "horizon", "direction"], as_index=False
    )["excess_return"].mean()
    direction = direction.pivot(index=["signal_date", "horizon"], columns="direction", values="excess_return")
    if {"RISING", "FALLING"}.issubset(direction.columns):
        item = (direction["RISING"] - direction["FALLING"]).rename("spread").dropna().reset_index()
        item["spread_id"] = "HIGH_RISING_MINUS_HIGH_FALLING"
        rows.append(item)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["signal_date", "horizon", "spread", "spread_id"]
    )
