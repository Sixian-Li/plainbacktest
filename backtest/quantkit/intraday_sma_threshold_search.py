"""Compiled exhaustive screening for the intraday SMA200 threshold grid."""

from __future__ import annotations

from collections.abc import Sequence

import numba
import numpy as np
import pandas as pd


PARAMETER_COLUMNS = ("a_pct", "b_pct", "correction_pct")
METRIC_COLUMNS = (
    "final_equity",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "max_drawdown_pct",
    "exposure_pct",
    "order_count",
)


@numba.njit(cache=True)
def _moments(count: int, mean: float, m2: float, value: float) -> tuple[int, float, float]:
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
    prior_close: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    a_pct: float,
    b_pct: float,
    correction_pct: float,
    window: int,
    cost_rate: float,
    initial_cash: float,
) -> np.ndarray:
    sell_multiplier = 1.0 - a_pct / 100.0
    buy_multiplier = 1.0 + b_pct / 100.0
    correction_enabled = correction_pct >= 0.0
    cash = initial_cash
    shares = 0.0
    cost_basis = 0.0
    last_sell = 0.0
    has_last_sell = False
    order_count = 0
    exposure_count = 0
    previous_equity = initial_cash
    peak = 0.0
    max_drawdown = 0.0
    return_count = 0
    return_mean = 0.0
    return_m2 = 0.0

    for index in range(close.shape[0]):
        raw_fill = np.nan
        if shares > 0.0:
            trigger = sell_multiplier * prior_sum[index] / (window - sell_multiplier)
            if correction_enabled:
                correction = cost_basis * (1.0 - correction_pct / 100.0)
                if correction > trigger:
                    trigger = correction
            if open_[index] <= trigger:
                raw_fill = open_[index]
            elif low[index] <= trigger < open_[index]:
                raw_fill = trigger
            if np.isfinite(raw_fill):
                fill = raw_fill * (1.0 - cost_rate)
                cash = shares * fill
                shares = 0.0
                cost_basis = 0.0
                last_sell = fill
                has_last_sell = True
                order_count += 1
        else:
            trigger = 1e300
            if prior_close[index] <= prior_sma[index] * buy_multiplier:
                trigger = buy_multiplier * prior_sum[index] / (window - buy_multiplier)
            if correction_enabled and has_last_sell:
                correction = last_sell * (1.0 + correction_pct / 100.0)
                if correction < trigger:
                    trigger = correction
            if trigger < 1e299:
                if open_[index] >= trigger:
                    raw_fill = open_[index]
                elif high[index] >= trigger > open_[index]:
                    raw_fill = trigger
            if np.isfinite(raw_fill):
                fill = raw_fill * (1.0 + cost_rate)
                shares = cash / fill
                cash = 0.0
                cost_basis = fill
                order_count += 1

        equity = cash + shares * close[index]
        if shares > 0.0:
            exposure_count += 1
        daily_return = 0.0 if index == 0 else equity / previous_equity - 1.0
        return_count, return_mean, return_m2 = _moments(
            return_count, return_mean, return_m2, daily_return
        )
        if index == 0 or equity > peak:
            peak = equity
        drawdown = equity / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
        previous_equity = equity

    elapsed_days = max(int(date_days[-1] - date_days[0]), 1)
    years = elapsed_days / 365.2425
    result = np.empty(len(METRIC_COLUMNS), dtype=np.float64)
    result[0] = previous_equity
    result[1] = (previous_equity / initial_cash - 1.0) * 100.0
    result[2] = ((previous_equity / initial_cash) ** (1.0 / years) - 1.0) * 100.0
    result[3] = np.nan
    if return_count > 1 and return_m2 > 0.0:
        result[3] = return_mean / np.sqrt(return_m2 / (return_count - 1)) * np.sqrt(252.0)
    result[4] = max_drawdown * 100.0
    result[5] = exposure_count / close.shape[0] * 100.0
    result[6] = order_count
    return result


@numba.njit(parallel=True, cache=True)
def _simulate_batch(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    parameters: np.ndarray,
    window: int,
    cost_rate: float,
    initial_cash: float,
) -> np.ndarray:
    output = np.empty((parameters.shape[0], len(METRIC_COLUMNS)), dtype=np.float64)
    for index in numba.prange(parameters.shape[0]):
        output[index] = _simulate_case(
            open_, high, low, close, prior_close, prior_sma, prior_sum, date_days,
            parameters[index, 0], parameters[index, 1], parameters[index, 2],
            window, cost_rate, initial_cash,
        )
    return output


def exhaustive_cases(
    a_values: Sequence[float],
    b_values: Sequence[float],
    correction_values: Sequence[float | None],
) -> pd.DataFrame:
    rows = [
        (float(a), float(b), -1.0 if correction is None else float(correction))
        for correction in correction_values
        for a in a_values
        for b in b_values
    ]
    frame = pd.DataFrame(rows, columns=PARAMETER_COLUMNS)
    frame.insert(
        0,
        "correction_mode",
        frame["correction_pct"].map(lambda value: "disabled" if value < 0 else f"cd_{value:g}pct"),
    )
    frame.insert(0, "case_id", [f"SCREEN_{index:06d}" for index in range(1, len(frame) + 1)])
    return frame


def screen_cases(
    analysis: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    window: int,
    cost_bps: float,
    initial_cash: float,
) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close", "prior_close", "prior_sma", "prior_sum"}
    missing = required - set(analysis.columns)
    if missing:
        raise ValueError(f"Missing prepared analysis columns: {sorted(missing)}")
    if analysis[list(required - {"date"})].isna().any().any():
        raise ValueError("Prepared analysis contains incomplete numeric inputs.")
    parameters = cases[list(PARAMETER_COLUMNS)].to_numpy(float)
    dates = pd.to_datetime(analysis["date"]).to_numpy(dtype="datetime64[D]").astype(np.int64)
    metrics = _simulate_batch(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        analysis["prior_sma"].to_numpy(float),
        analysis["prior_sum"].to_numpy(float),
        dates,
        parameters,
        int(window),
        float(cost_bps) / 10_000.0,
        float(initial_cash),
    )
    result = cases.reset_index(drop=True).copy()
    result["correction_pct"] = result["correction_pct"].replace(-1.0, np.nan)
    for index, name in enumerate(METRIC_COLUMNS):
        result[name] = metrics[:, index]
    return result
