"""Deterministic compiled screening for the intraday SMA strategy.

The screening ledger mirrors the formal reference engine but intentionally
returns metrics only.  Selected candidates must still pass PyBroker and the
independent pandas ledger before they appear as formal results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numba
import numpy as np
import pandas as pd


PARAMETER_COLUMNS = (
    "A_negative_days_slow",
    "B_slow_sma_window",
    "C_fast_derivative_pct",
    "D_negative_days_fast",
    "E_fallback_sma_window",
    "F_short_sma_center",
    "F_short_sma_spacing",
    "G_short_recovery_below_pct",
    "H_reentry_sma_window",
    "L_cost_stop_pct",
    "R_forced_rebuy_pct",
)
FORCED_REENTRY_COLUMN = "forced_reentry_enabled"
FAST_DROP_ENABLED_COLUMN = "fast_drop_enabled"
METRIC_COLUMNS = (
    "final_equity",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "max_drawdown_pct",
    "exposure_pct",
    "order_count",
    "first_entry_index",
    "first_entry_fill",
    "benchmark_final_equity",
    "benchmark_cagr_pct",
    "benchmark_sharpe",
    "benchmark_max_drawdown_pct",
)


@numba.njit(cache=True)
def _sum(prefix: np.ndarray, start: int, end: int) -> float:
    if start > end:
        return 0.0
    return prefix[end + 1] - prefix[start]


@numba.njit(cache=True)
def _short_average(prefix: np.ndarray, index: int, f1: int, f2: int, f3: int) -> float:
    if index < f3 - 1:
        return np.nan
    return (
        _sum(prefix, index - f1 + 1, index) / f1
        + _sum(prefix, index - f2 + 1, index) / f2
        + _sum(prefix, index - f3 + 1, index) / f3
    ) / 3.0


@numba.njit(cache=True)
def _short_derivative(prefix: np.ndarray, index: int, f1: int, f2: int, f3: int) -> float:
    current = _short_average(prefix, index, f1, f2, f3)
    previous = _short_average(prefix, index - 1, f1, f2, f3)
    if not np.isfinite(current) or not np.isfinite(previous):
        return np.nan
    return current / previous - 1.0


@numba.njit(cache=True)
def _negative_streak(
    prefix: np.ndarray,
    end_index: int,
    count: int,
    f1: int,
    f2: int,
    f3: int,
) -> bool:
    if count <= 0:
        return True
    for index in range(end_index - count + 1, end_index + 1):
        value = _short_derivative(prefix, index, f1, f2, f3)
        if not np.isfinite(value) or value >= 0.0:
            return False
    return True


@numba.njit(cache=True)
def _update_moments(count: int, mean: float, m2: float, value: float) -> tuple[int, float, float]:
    count += 1
    delta = value - mean
    mean += delta / count
    m2 += delta * (value - mean)
    return count, mean, m2


@numba.njit(cache=True)
def _simulate_case(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    date_days: np.ndarray,
    prefix: np.ndarray,
    parameters: np.ndarray,
    forced_reentry_enabled: bool,
    fast_drop_enabled: bool,
    metric_from_analysis_start: bool,
    analysis_start_index: int,
    analysis_end_index: int,
    initial_cash: float,
) -> np.ndarray:
    a = int(parameters[0])
    b = int(parameters[1])
    c = parameters[2]
    d = int(parameters[3])
    e = int(parameters[4])
    center = int(parameters[5])
    spacing = int(parameters[6])
    f1, f2, f3 = center - spacing, center, center + spacing
    g = parameters[7]
    h = int(parameters[8])
    l = parameters[9]
    r = parameters[10]
    beta = (1.0 / f1 + 1.0 / f2 + 1.0 / f3) / 3.0
    fast_multiplier = 1.0 + c / 100.0
    recovery_multiplier = 1.0 - g / 100.0

    cash = initial_cash
    shares = 0.0
    cost_basis = 0.0
    last_sell = 0.0
    has_last_sell = False
    first_index = -1
    first_fill = np.nan
    order_count = 0
    exposure_count = 0
    previous_equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    return_count = 0
    return_mean = 0.0
    return_m2 = 0.0

    benchmark_shares = (
        initial_cash / open_[analysis_start_index] if metric_from_analysis_start else 0.0
    )
    benchmark_previous = 0.0
    benchmark_peak = 0.0
    benchmark_max_drawdown = 0.0
    benchmark_count = 0
    benchmark_mean = 0.0
    benchmark_m2 = 0.0

    for i in range(analysis_start_index, analysis_end_index + 1):
        fill = np.nan
        if shares > 0.0:
            highest = cost_basis * (1.0 - l / 100.0)

            if _negative_streak(prefix, i - 1, a - 1, f1, f2, f3) and i >= max(f3, b - 1):
                previous_short = _short_average(prefix, i - 1, f1, f2, f3)
                alpha = (
                    _sum(prefix, i - f1 + 1, i - 1) / f1
                    + _sum(prefix, i - f2 + 1, i - 1) / f2
                    + _sum(prefix, i - f3 + 1, i - 1) / f3
                ) / 3.0
                zero_derivative = (previous_short - alpha) / beta
                slow_boundary = _sum(prefix, i - b + 1, i - 1) / (b - 1)
                slow_trigger = min(zero_derivative, slow_boundary)
                if np.isfinite(slow_trigger) and slow_trigger > highest:
                    highest = slow_trigger

            if (
                fast_drop_enabled
                and _negative_streak(prefix, i - 1, d - 1, f1, f2, f3)
                and i >= f3
            ):
                fast_trigger = 1e300
                for window in (f1, f2, f3):
                    previous_sma = _sum(prefix, i - window, i - 1) / window
                    prior_sum = _sum(prefix, i - window + 1, i - 1)
                    trigger = window * previous_sma * fast_multiplier - prior_sum
                    if trigger < fast_trigger:
                        fast_trigger = trigger
                if np.isfinite(fast_trigger) and fast_trigger > highest:
                    highest = fast_trigger

            if i >= e:
                previous_sma = _sum(prefix, i - e, i - 1) / e
                if close[i - 1] >= previous_sma:
                    trigger = _sum(prefix, i - e + 1, i - 1) / (e - 1)
                    if trigger > highest:
                        highest = trigger

            if highest > 0.0:
                if open_[i] <= highest:
                    fill = open_[i]
                elif low[i] <= highest < open_[i]:
                    fill = highest
            if np.isfinite(fill):
                cash = shares * fill
                shares = 0.0
                cost_basis = 0.0
                last_sell = fill
                has_last_sell = True
                order_count += 1
        else:
            lowest = 1e300
            if has_last_sell and forced_reentry_enabled:
                lowest = last_sell * (1.0 + r / 100.0)

            if i >= h:
                previous_sma = _sum(prefix, i - h, i - 1) / h
                if close[i - 1] < previous_sma:
                    trigger = _sum(prefix, i - h + 1, i - 1) / (h - 1)
                    if trigger < lowest:
                        lowest = trigger

            if i >= f3:
                previous_short = _short_average(prefix, i - 1, f1, f2, f3)
                if close[i - 1] < previous_short * recovery_multiplier:
                    alpha = (
                        _sum(prefix, i - f1 + 1, i - 1) / f1
                        + _sum(prefix, i - f2 + 1, i - 1) / f2
                        + _sum(prefix, i - f3 + 1, i - 1) / f3
                    ) / 3.0
                    denominator = 1.0 - recovery_multiplier * beta
                    trigger = recovery_multiplier * alpha / denominator
                    if trigger < lowest:
                        lowest = trigger

            if lowest < 1e299 and lowest > 0.0:
                if open_[i] >= lowest:
                    fill = open_[i]
                elif high[i] >= lowest > open_[i]:
                    fill = lowest
            if np.isfinite(fill):
                shares = cash / fill
                cash = 0.0
                cost_basis = fill
                order_count += 1
                if first_index < 0:
                    first_index = i
                    first_fill = fill
                    if not metric_from_analysis_start:
                        benchmark_shares = initial_cash / fill

        metric_start_index = analysis_start_index if metric_from_analysis_start else first_index
        if metric_start_index >= 0:
            equity = cash + shares * close[i]
            if shares > 0.0:
                exposure_count += 1
            daily_return = 0.0 if i == metric_start_index else equity / previous_equity - 1.0
            return_count, return_mean, return_m2 = _update_moments(
                return_count, return_mean, return_m2, daily_return
            )
            if i == metric_start_index:
                peak = equity
            elif equity > peak:
                peak = equity
            drawdown = equity / peak - 1.0
            if drawdown < max_drawdown:
                max_drawdown = drawdown
            previous_equity = equity

            benchmark_equity = benchmark_shares * close[i]
            benchmark_return = (
                0.0 if i == metric_start_index else benchmark_equity / benchmark_previous - 1.0
            )
            benchmark_count, benchmark_mean, benchmark_m2 = _update_moments(
                benchmark_count, benchmark_mean, benchmark_m2, benchmark_return
            )
            if i == metric_start_index:
                benchmark_peak = benchmark_equity
            elif benchmark_equity > benchmark_peak:
                benchmark_peak = benchmark_equity
            benchmark_drawdown = benchmark_equity / benchmark_peak - 1.0
            if benchmark_drawdown < benchmark_max_drawdown:
                benchmark_max_drawdown = benchmark_drawdown
            benchmark_previous = benchmark_equity

    result = np.full(13, np.nan)
    if first_index < 0 and not metric_from_analysis_start:
        return result
    metric_start_index = analysis_start_index if metric_from_analysis_start else first_index
    elapsed_days = max(int(date_days[analysis_end_index] - date_days[metric_start_index]), 1)
    years = elapsed_days / 365.2425
    final_equity = previous_equity
    benchmark_final = benchmark_previous
    result[0] = final_equity
    result[1] = (final_equity / initial_cash - 1.0) * 100.0
    result[2] = ((final_equity / initial_cash) ** (1.0 / years) - 1.0) * 100.0
    if return_count > 1 and return_m2 > 0.0:
        result[3] = return_mean / np.sqrt(return_m2 / (return_count - 1)) * np.sqrt(252.0)
    result[4] = max_drawdown * 100.0
    result[5] = exposure_count / return_count * 100.0
    result[6] = order_count
    result[7] = first_index if first_index >= 0 else np.nan
    result[8] = first_fill
    result[9] = benchmark_final
    result[10] = ((benchmark_final / initial_cash) ** (1.0 / years) - 1.0) * 100.0
    if benchmark_count > 1 and benchmark_m2 > 0.0:
        result[11] = benchmark_mean / np.sqrt(benchmark_m2 / (benchmark_count - 1)) * np.sqrt(252.0)
    result[12] = benchmark_max_drawdown * 100.0
    return result


@numba.njit(parallel=True, cache=True)
def _simulate_batch(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    date_days: np.ndarray,
    prefix: np.ndarray,
    parameters: np.ndarray,
    forced_reentry_enabled: np.ndarray,
    fast_drop_enabled: np.ndarray,
    metric_from_analysis_start: bool,
    analysis_start_index: int,
    analysis_end_index: int,
    initial_cash: float,
) -> np.ndarray:
    output = np.empty((parameters.shape[0], len(METRIC_COLUMNS)), dtype=np.float64)
    for index in numba.prange(parameters.shape[0]):
        output[index] = _simulate_case(
            open_, high, low, close, date_days, prefix, parameters[index],
            forced_reentry_enabled[index], fast_drop_enabled[index], metric_from_analysis_start,
            analysis_start_index, analysis_end_index,
            initial_cash,
        )
    return output


def parameter_values(search_space: Mapping[str, Sequence[float]]) -> list[np.ndarray]:
    missing = set(PARAMETER_COLUMNS) - set(search_space)
    if missing:
        raise ValueError(f"Missing search dimensions: {sorted(missing)}")
    return [np.asarray(search_space[name], dtype=float) for name in PARAMETER_COLUMNS]


def sample_global_cases(
    search_space: Mapping[str, Sequence[float]],
    *,
    count: int,
    seed: int,
    anchors: Sequence[Mapping[str, float]] = (),
) -> pd.DataFrame:
    """Uniformly sample the declared discrete space with stable de-duplication."""

    values = parameter_values(search_space)
    rng = np.random.default_rng(seed)
    rows = np.column_stack([rng.choice(options, size=count, replace=True) for options in values])
    if anchors:
        anchor_rows = np.asarray(
            [[float(anchor[name]) for name in PARAMETER_COLUMNS] for anchor in anchors], dtype=float
        )
        rows = np.vstack((anchor_rows, rows))
    frame = pd.DataFrame(rows, columns=PARAMETER_COLUMNS).drop_duplicates(ignore_index=True)
    return frame.iloc[:count].copy()


def sample_cases_with_reentry_modes(
    search_space: Mapping[str, Sequence[float]],
    *,
    reentry_modes: Sequence[bool],
    count: int,
    seed: int,
    anchors: Sequence[Mapping[str, float | bool]] = (),
) -> pd.DataFrame:
    """Sample the parameter grid plus an explicit forced-reentry on/off switch."""

    if not reentry_modes:
        raise ValueError("At least one forced-reentry mode is required.")
    values = parameter_values(search_space)
    rng = np.random.default_rng(seed)
    draw_count = max(count * 3, count + 100)
    rows = np.column_stack(
        [rng.choice(options, size=draw_count, replace=True) for options in values]
        + [rng.choice(np.asarray(reentry_modes, dtype=np.bool_), size=draw_count, replace=True)]
    )
    columns = (*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN)
    if anchors:
        anchor_rows = np.asarray(
            [[float(anchor[name]) for name in PARAMETER_COLUMNS]
             + [float(bool(anchor[FORCED_REENTRY_COLUMN]))] for anchor in anchors],
            dtype=float,
        )
        rows = np.vstack((anchor_rows, rows))
    frame = pd.DataFrame(rows, columns=columns).drop_duplicates(ignore_index=True)
    frame[FORCED_REENTRY_COLUMN] = frame[FORCED_REENTRY_COLUMN].astype(bool)
    frame.loc[~frame[FORCED_REENTRY_COLUMN], "R_forced_rebuy_pct"] = 0.0
    frame = frame.drop_duplicates(ignore_index=True)
    if len(frame) < count:
        raise RuntimeError(f"Could only generate {len(frame)} of {count} unique cases.")
    return frame.iloc[:count].copy()


def sample_refined_cases(
    search_space: Mapping[str, Sequence[float]],
    parents: pd.DataFrame,
    existing: pd.DataFrame,
    *,
    count: int,
    seed: int,
) -> pd.DataFrame:
    """Mutate front-runners on the discrete grid while preserving global jumps."""

    values = parameter_values(search_space)
    rng = np.random.default_rng(seed)
    existing_keys = {
        tuple(float(value) for value in row)
        for row in existing[list(PARAMETER_COLUMNS)].to_numpy(float)
    }
    parent_values = parents[list(PARAMETER_COLUMNS)].to_numpy(float)
    rows: list[list[float]] = []
    keys = set(existing_keys)
    attempts = 0
    while len(rows) < count and attempts < count * 30:
        attempts += 1
        parent = parent_values[rng.integers(0, len(parent_values))]
        candidate: list[float] = []
        for current, options in zip(parent, values, strict=True):
            if rng.random() < 0.18:
                chosen = float(options[rng.integers(0, len(options))])
            else:
                location = int(np.argmin(np.abs(options - current)))
                shift = int(rng.integers(-2, 3))
                chosen = float(options[min(max(location + shift, 0), len(options) - 1)])
            candidate.append(chosen)
        key = tuple(candidate)
        if key not in keys:
            keys.add(key)
            rows.append(candidate)
    if len(rows) != count:
        raise RuntimeError(f"Could only generate {len(rows)} of {count} refined cases.")
    return pd.DataFrame(rows, columns=PARAMETER_COLUMNS)


def sample_refined_cases_with_reentry_modes(
    search_space: Mapping[str, Sequence[float]],
    parents: pd.DataFrame,
    existing: pd.DataFrame,
    *,
    reentry_modes: Sequence[bool],
    count: int,
    seed: int,
) -> pd.DataFrame:
    """Refine front-runners while treating forced re-entry as a discrete mode."""

    if not reentry_modes:
        raise ValueError("At least one forced-reentry mode is required.")
    columns = (*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN)
    values = parameter_values(search_space)
    mode_values = np.asarray(reentry_modes, dtype=np.bool_)
    rng = np.random.default_rng(seed)
    keys = {
        tuple(float(value) for value in row[:-1]) + (bool(row[-1]),)
        for row in existing[list(columns)].to_numpy()
    }
    parent_values = parents[list(columns)].to_numpy()
    rows: list[list[float | bool]] = []
    attempts = 0
    while len(rows) < count and attempts < count * 40:
        attempts += 1
        parent = parent_values[rng.integers(0, len(parent_values))]
        candidate: list[float | bool] = []
        for current, options in zip(parent[:-1], values, strict=True):
            if rng.random() < 0.18:
                chosen = float(options[rng.integers(0, len(options))])
            else:
                location = int(np.argmin(np.abs(options - float(current))))
                shift = int(rng.integers(-2, 3))
                chosen = float(options[min(max(location + shift, 0), len(options) - 1)])
            candidate.append(chosen)
        if rng.random() < 0.18:
            mode = bool(mode_values[rng.integers(0, len(mode_values))])
        else:
            mode = bool(parent[-1])
        candidate.append(mode)
        if not mode:
            candidate[PARAMETER_COLUMNS.index("R_forced_rebuy_pct")] = 0.0
        key = tuple(float(value) for value in candidate[:-1]) + (bool(candidate[-1]),)
        if key not in keys:
            keys.add(key)
            rows.append(candidate)
    if len(rows) != count:
        raise RuntimeError(f"Could only generate {len(rows)} of {count} refined cases.")
    frame = pd.DataFrame(rows, columns=columns)
    frame[FORCED_REENTRY_COLUMN] = frame[FORCED_REENTRY_COLUMN].astype(bool)
    return frame


def case_columns(frame: pd.DataFrame) -> list[str]:
    """Return the identifying parameter columns present in a result frame."""

    columns = list(PARAMETER_COLUMNS)
    if FORCED_REENTRY_COLUMN in frame:
        columns.append(FORCED_REENTRY_COLUMN)
    if FAST_DROP_ENABLED_COLUMN in frame:
        columns.append(FAST_DROP_ENABLED_COLUMN)
    return columns


def screen_cases(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    initial_cash: float,
    analysis_start: pd.Timestamp | str | None = None,
    analysis_end: pd.Timestamp | str | None = None,
    evaluation_start: str = "first_entry",
) -> pd.DataFrame:
    """Run the compiled screening ledger and return one row per input case.

    ``data`` may contain history before ``analysis_start``.  Those earlier
    closes warm the indicators but never create orders or performance.  This
    matches the formal engines' window semantics and prevents every validation
    window from artificially restarting its moving averages.

    ``evaluation_start="first_entry"`` retains the legacy case-specific
    first-buy metric start. ``evaluation_start="analysis_start"`` includes
    the initial cash waiting period and uses a common benchmark bought at the
    analysis window's first Open.

    When ``forced_reentry_enabled`` or ``fast_drop_enabled`` is absent from
    ``cases``, the legacy behavior (enabled for every case) is retained.
    """

    rows = data.sort_values("date").reset_index(drop=True)
    if rows.empty:
        raise ValueError("Screening data is empty.")
    start = pd.Timestamp(rows.iloc[0]["date"] if analysis_start is None else analysis_start).normalize()
    end = pd.Timestamp(rows.iloc[-1]["date"] if analysis_end is None else analysis_end).normalize()
    matches = rows.index[pd.to_datetime(rows["date"]).dt.normalize() == start].tolist()
    end_matches = rows.index[pd.to_datetime(rows["date"]).dt.normalize() == end].tolist()
    if len(matches) != 1 or len(end_matches) != 1:
        raise ValueError("Analysis start/end must each be one available trading date.")
    analysis_start_index = int(matches[0])
    analysis_end_index = int(end_matches[0])
    if analysis_end_index < analysis_start_index:
        raise ValueError("Analysis end precedes analysis start.")
    if evaluation_start not in {"first_entry", "analysis_start"}:
        raise ValueError("evaluation_start must be 'first_entry' or 'analysis_start'.")
    dates = pd.to_datetime(rows["date"]).to_numpy(dtype="datetime64[D]").astype(np.int64)
    close = rows["close"].to_numpy(float)
    prefix = np.empty(len(close) + 1, dtype=float)
    prefix[0] = 0.0
    np.cumsum(close, out=prefix[1:])
    if FORCED_REENTRY_COLUMN in cases:
        forced_reentry_enabled = cases[FORCED_REENTRY_COLUMN].astype(bool).to_numpy()
    else:
        forced_reentry_enabled = np.ones(len(cases), dtype=np.bool_)
    if FAST_DROP_ENABLED_COLUMN in cases:
        fast_drop_enabled = cases[FAST_DROP_ENABLED_COLUMN].astype(bool).to_numpy()
    else:
        fast_drop_enabled = np.ones(len(cases), dtype=np.bool_)
    metrics = _simulate_batch(
        rows["open"].to_numpy(float),
        rows["high"].to_numpy(float),
        rows["low"].to_numpy(float),
        close,
        dates,
        prefix,
        cases[list(PARAMETER_COLUMNS)].to_numpy(float),
        forced_reentry_enabled,
        fast_drop_enabled,
        evaluation_start == "analysis_start",
        analysis_start_index,
        analysis_end_index,
        float(initial_cash),
    )
    result = cases.reset_index(drop=True).copy()
    for index, name in enumerate(METRIC_COLUMNS):
        result[name] = metrics[:, index]
    valid = result["first_entry_index"].notna()
    first_indices = result.loc[valid, "first_entry_index"].astype(int).to_numpy()
    result["first_entry_date"] = pd.NaT
    result.loc[valid, "first_entry_date"] = pd.to_datetime(rows.loc[first_indices, "date"]).to_numpy()
    result["delta_cagr_vs_buy_hold_pct_points"] = result["cagr_pct"] - result["benchmark_cagr_pct"]
    result["delta_sharpe_vs_buy_hold"] = result["sharpe"] - result["benchmark_sharpe"]
    return result


def select_frontier_parents(results: pd.DataFrame, *, per_objective: int = 50) -> pd.DataFrame:
    frames = [
        results.nlargest(per_objective, "cagr_pct"),
        results.nlargest(per_objective, "sharpe"),
        results.nlargest(per_objective, "delta_cagr_vs_buy_hold_pct_points"),
        results.nlargest(per_objective, "delta_sharpe_vs_buy_hold"),
    ]
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=case_columns(results), ignore_index=True
    )


def select_formal_candidates(results: pd.DataFrame, *, per_objective: int = 3) -> pd.DataFrame:
    selections: list[pd.DataFrame] = []
    for objective, prefix in (
        ("cagr_pct", "CAGR"),
        ("sharpe", "SHARPE"),
        ("delta_cagr_vs_buy_hold_pct_points", "DELTA_CAGR"),
        ("delta_sharpe_vs_buy_hold", "DELTA_SHARPE"),
    ):
        frame = results.nlargest(per_objective, objective).copy()
        frame["selection_reason"] = [f"{prefix}_RANK_{rank}" for rank in range(1, len(frame) + 1)]
        selections.append(frame)
    selected = pd.concat(selections, ignore_index=True)
    grouped: list[dict[str, Any]] = []
    for _, group in selected.groupby(case_columns(results), sort=False, dropna=False):
        row = group.iloc[0].to_dict()
        row["selection_reason"] = "|".join(group["selection_reason"].astype(str))
        grouped.append(row)
    result = pd.DataFrame(grouped)
    result.insert(0, "case_id", [f"CANDIDATE_{index:02d}" for index in range(1, len(result) + 1)])
    return result
