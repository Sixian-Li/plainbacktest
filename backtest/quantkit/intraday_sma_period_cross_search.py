"""Exhaustive and independently cross-checked SMA-period crossing search."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numba
import numpy as np
import pandas as pd

from quantkit.intraday_sma_period_cross import analysis_rows
from quantkit.surface import connected_components


METRIC_COLUMNS = (
    "final_equity",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "max_drawdown_pct",
    "exposure_pct",
    "order_count",
    "buy_sma_count",
    "sell_sma_count",
    "forced_rebuy_count",
    "stop_loss_count",
)


@dataclass
class PeriodCrossScreen:
    metrics: pd.DataFrame
    pbo_return_sum: np.ndarray
    pbo_return_sumsq: np.ndarray
    pbo_log_returns: np.ndarray
    pbo_counts: np.ndarray
    analysis: pd.DataFrame
    segment_definitions: list[dict[str, Any]]


def _inclusive_integer_range(definition: dict[str, Any]) -> list[int]:
    start = int(definition["start"])
    stop = int(definition["stop"])
    step = int(definition["step"])
    if step <= 0 or stop < start or (stop - start) % step:
        raise ValueError("SMA period range must be positive and exactly inclusive.")
    values = list(range(start, stop + 1, step))
    if len(values) != int(definition["count"]):
        raise ValueError("SMA period count does not match its inclusive range.")
    return values


def build_grid_cases(parameters: dict[str, Any]) -> pd.DataFrame:
    buy_windows = _inclusive_integer_range(parameters["buy_sma_window_range"])
    sell_windows = _inclusive_integer_range(parameters["sell_sma_window_range"])
    forced_rebuy = [float(value) for value in parameters["forced_rebuy_pct"]]
    stop_losses = [None if value is None else float(value) for value in parameters["stop_loss_pct"]]
    rows: list[dict[str, Any]] = []
    for buy_window in buy_windows:
        for sell_window in sell_windows:
            for rebuy in forced_rebuy:
                for stop in stop_losses:
                    stop_key = -1.0 if stop is None else float(stop)
                    rows.append(
                        {
                            "buy_window": int(buy_window),
                            "sell_window": int(sell_window),
                            "forced_rebuy_pct": float(rebuy),
                            "stop_loss_pct": np.nan if stop is None else float(stop),
                            "stop_loss_pct_key": stop_key,
                            "correction_mode": (
                                f"c{rebuy:g}_d{'off' if stop is None else f'{stop:g}'}"
                            ),
                        }
                    )
    frame = pd.DataFrame(rows)
    expected = int(parameters["combination_count"])
    if len(frame) != expected:
        raise AssertionError(f"Expected {expected:,} grid cases, generated {len(frame):,}.")
    frame.insert(0, "case_id", [f"CASE_{index:05d}" for index in range(1, len(frame) + 1)])
    if frame.duplicated(
        ["buy_window", "sell_window", "forced_rebuy_pct", "stop_loss_pct_key"]
    ).any():
        raise AssertionError("SMA period grid contains duplicate cases.")
    return frame


@numba.njit(cache=True)
def _metrics_from_state(
    metrics: np.ndarray,
    case_index: int,
    final_equity: float,
    initial_cash: float,
    elapsed_days: int,
    return_count: int,
    return_sum: float,
    return_sumsq: float,
    max_drawdown: float,
    exposure_count: int,
    bar_count: int,
    order_count: int,
    buy_sma_count: int,
    sell_sma_count: int,
    forced_rebuy_count: int,
    stop_loss_count: int,
) -> None:
    years = max(elapsed_days, 1) / 365.2425
    metrics[case_index, 0] = final_equity
    metrics[case_index, 1] = (final_equity / initial_cash - 1.0) * 100.0
    metrics[case_index, 2] = (
        (final_equity / initial_cash) ** (1.0 / years) - 1.0
    ) * 100.0
    metrics[case_index, 3] = np.nan
    if return_count > 1:
        variance = (
            return_sumsq - return_sum * return_sum / return_count
        ) / (return_count - 1)
        if variance > 0.0:
            metrics[case_index, 3] = (
                return_sum / return_count / math.sqrt(variance) * math.sqrt(252.0)
            )
    metrics[case_index, 4] = max_drawdown * 100.0
    metrics[case_index, 5] = exposure_count / bar_count * 100.0
    metrics[case_index, 6] = order_count
    metrics[case_index, 7] = buy_sma_count
    metrics[case_index, 8] = sell_sma_count
    metrics[case_index, 9] = forced_rebuy_count
    metrics[case_index, 10] = stop_loss_count


@numba.njit(parallel=True, cache=True)
def _simulate_period_grid(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    segment_index: np.ndarray,
    pbo_index: np.ndarray,
    parameters: np.ndarray,
    windows: np.ndarray,
    cost_rate: float,
    initial_cash: float,
    segment_count: int,
    pbo_block_count: int,
) -> tuple[np.ndarray, ...]:
    case_count = parameters.shape[0]
    bar_count = close.shape[0]
    metrics = np.empty((case_count, len(METRIC_COLUMNS)), dtype=np.float64)
    segment_start = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_end = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_sum = np.zeros((case_count, segment_count), dtype=np.float64)
    segment_sumsq = np.zeros((case_count, segment_count), dtype=np.float64)
    pbo_sum = np.zeros((case_count, pbo_block_count), dtype=np.float64)
    pbo_sumsq = np.zeros((case_count, pbo_block_count), dtype=np.float64)
    pbo_log = np.zeros((case_count, pbo_block_count), dtype=np.float64)

    for case_index in numba.prange(case_count):
        buy_index = int(parameters[case_index, 0])
        sell_index = int(parameters[case_index, 1])
        rebuy_pct = parameters[case_index, 2]
        stop_pct = parameters[case_index, 3]
        stop_enabled = stop_pct >= 0.0
        buy_window = windows[buy_index]
        sell_window = windows[sell_index]
        cash = initial_cash
        shares = 0.0
        cost_basis = 0.0
        last_sell = 0.0
        has_last_sell = False
        order_count = 0
        exposure_count = 0
        buy_sma_count = 0
        sell_sma_count = 0
        forced_rebuy_count = 0
        stop_loss_count = 0
        previous_equity = initial_cash
        peak = 0.0
        max_drawdown = 0.0
        return_count = 0
        return_sum = 0.0
        return_sumsq = 0.0
        prior_segment = -1

        for bar_index in range(bar_count):
            segment = segment_index[bar_index]
            if segment != prior_segment:
                segment_start[case_index, segment] = previous_equity
                prior_segment = segment

            raw_fill = np.nan
            primary_signal = -1
            if shares > 0.0:
                trigger = -1.0
                if prior_close[bar_index] >= prior_sma[bar_index, sell_index]:
                    trigger = prior_sum[bar_index, sell_index] / (sell_window - 1.0)
                    primary_signal = 1
                if stop_enabled:
                    stop = cost_basis * (1.0 - stop_pct / 100.0)
                    if stop > trigger:
                        trigger = stop
                        primary_signal = 3
                if trigger > 0.0:
                    if open_[bar_index] <= trigger:
                        raw_fill = open_[bar_index]
                    elif low[bar_index] <= trigger < open_[bar_index]:
                        raw_fill = trigger
                if np.isfinite(raw_fill):
                    fill = raw_fill * (1.0 - cost_rate)
                    cash = shares * fill
                    shares = 0.0
                    cost_basis = 0.0
                    last_sell = fill
                    has_last_sell = True
                    order_count += 1
                    if primary_signal == 1:
                        sell_sma_count += 1
                    else:
                        stop_loss_count += 1
            else:
                trigger = 1e300
                primary_signal = -1
                if prior_close[bar_index] <= prior_sma[bar_index, buy_index]:
                    trigger = prior_sum[bar_index, buy_index] / (buy_window - 1.0)
                    primary_signal = 0
                if has_last_sell:
                    rebuy = last_sell * (1.0 + rebuy_pct / 100.0)
                    if rebuy < trigger:
                        trigger = rebuy
                        primary_signal = 2
                if trigger < 1e299:
                    if open_[bar_index] >= trigger:
                        raw_fill = open_[bar_index]
                    elif high[bar_index] >= trigger > open_[bar_index]:
                        raw_fill = trigger
                if np.isfinite(raw_fill):
                    fill = raw_fill * (1.0 + cost_rate)
                    shares = cash / fill
                    cash = 0.0
                    cost_basis = fill
                    order_count += 1
                    if primary_signal == 0:
                        buy_sma_count += 1
                    else:
                        forced_rebuy_count += 1

            equity = cash + shares * close[bar_index]
            if shares > 0.0:
                exposure_count += 1
            daily_return = 0.0 if bar_index == 0 else equity / previous_equity - 1.0
            return_count += 1
            return_sum += daily_return
            return_sumsq += daily_return * daily_return
            segment_sum[case_index, segment] += daily_return
            segment_sumsq[case_index, segment] += daily_return * daily_return
            segment_end[case_index, segment] = equity
            block = pbo_index[bar_index]
            pbo_sum[case_index, block] += daily_return
            pbo_sumsq[case_index, block] += daily_return * daily_return
            if bar_index > 0:
                pbo_log[case_index, block] += math.log(equity / previous_equity)
            if bar_index == 0 or equity > peak:
                peak = equity
            drawdown = equity / peak - 1.0
            if drawdown < max_drawdown:
                max_drawdown = drawdown
            previous_equity = equity

        _metrics_from_state(
            metrics,
            case_index,
            previous_equity,
            initial_cash,
            int(date_days[-1] - date_days[0]),
            return_count,
            return_sum,
            return_sumsq,
            max_drawdown,
            exposure_count,
            bar_count,
            order_count,
            buy_sma_count,
            sell_sma_count,
            forced_rebuy_count,
            stop_loss_count,
        )
    return (
        metrics,
        segment_start,
        segment_end,
        segment_sum,
        segment_sumsq,
        pbo_sum,
        pbo_sumsq,
        pbo_log,
    )


@numba.njit(parallel=True, cache=True)
def _simulate_period_grid_reference(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    segment_index: np.ndarray,
    parameters: np.ndarray,
    windows: np.ndarray,
    cost_rate: float,
    initial_cash: float,
    segment_count: int,
) -> tuple[np.ndarray, ...]:
    """Second full-grid ledger with separately structured trigger selection."""

    case_count = parameters.shape[0]
    bar_count = close.shape[0]
    metrics = np.empty((case_count, len(METRIC_COLUMNS)), dtype=np.float64)
    segment_start = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_end = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_sum = np.zeros((case_count, segment_count), dtype=np.float64)
    segment_sumsq = np.zeros((case_count, segment_count), dtype=np.float64)
    for case_index in numba.prange(case_count):
        buy_column = int(parameters[case_index, 0])
        sell_column = int(parameters[case_index, 1])
        rebuy_fraction = parameters[case_index, 2] / 100.0
        stop_value = parameters[case_index, 3]
        stop_fraction = stop_value / 100.0
        holding = False
        cash_balance = initial_cash
        share_balance = 0.0
        effective_buy = 0.0
        effective_sell = 0.0
        sold_before = False
        counts = np.zeros(5, dtype=np.int64)
        prior_equity = initial_cash
        high_water = 0.0
        worst_drawdown = 0.0
        sum_returns = 0.0
        sum_squared_returns = 0.0
        prior_segment = -1

        for bar_index in range(bar_count):
            segment = segment_index[bar_index]
            if segment != prior_segment:
                segment_start[case_index, segment] = prior_equity
                prior_segment = segment
            fill_price = np.nan
            reason = -1
            if holding:
                selected = -np.inf
                if prior_close[bar_index] >= prior_sma[bar_index, sell_column]:
                    selected = prior_sum[bar_index, sell_column] / (
                        windows[sell_column] - 1.0
                    )
                    reason = 1
                if stop_value >= 0.0:
                    candidate = effective_buy * (1.0 - stop_fraction)
                    if candidate > selected:
                        selected = candidate
                        reason = 3
                if selected > 0.0:
                    if open_[bar_index] <= selected:
                        fill_price = open_[bar_index] * (1.0 - cost_rate)
                    elif low[bar_index] <= selected < open_[bar_index]:
                        fill_price = selected * (1.0 - cost_rate)
                if np.isfinite(fill_price):
                    cash_balance = share_balance * fill_price
                    share_balance = 0.0
                    effective_sell = fill_price
                    effective_buy = 0.0
                    sold_before = True
                    holding = False
                    counts[0] += 1
                    counts[2 if reason == 1 else 4] += 1
            else:
                selected = np.inf
                if prior_close[bar_index] <= prior_sma[bar_index, buy_column]:
                    selected = prior_sum[bar_index, buy_column] / (
                        windows[buy_column] - 1.0
                    )
                    reason = 0
                if sold_before:
                    candidate = effective_sell * (1.0 + rebuy_fraction)
                    if candidate < selected:
                        selected = candidate
                        reason = 2
                if np.isfinite(selected):
                    if open_[bar_index] >= selected:
                        fill_price = open_[bar_index] * (1.0 + cost_rate)
                    elif high[bar_index] >= selected > open_[bar_index]:
                        fill_price = selected * (1.0 + cost_rate)
                if np.isfinite(fill_price):
                    share_balance = cash_balance / fill_price
                    cash_balance = 0.0
                    effective_buy = fill_price
                    holding = True
                    counts[0] += 1
                    counts[1 if reason == 0 else 3] += 1

            equity = cash_balance + share_balance * close[bar_index]
            if holding:
                counts[0] += 0
            daily_return = 0.0 if bar_index == 0 else equity / prior_equity - 1.0
            sum_returns += daily_return
            sum_squared_returns += daily_return * daily_return
            segment_sum[case_index, segment] += daily_return
            segment_sumsq[case_index, segment] += daily_return * daily_return
            segment_end[case_index, segment] = equity
            if bar_index == 0 or equity > high_water:
                high_water = equity
            drawdown = equity / high_water - 1.0
            if drawdown < worst_drawdown:
                worst_drawdown = drawdown
            prior_equity = equity

        exposure_count = 0
        # Re-run only the position flag to avoid sharing state bookkeeping with
        # the primary engine would be wasteful.  Exposure is therefore derived
        # in the independent loop below, using a second explicit state pass.
        holding = False
        effective_buy = 0.0
        effective_sell = 0.0
        sold_before = False
        cash_balance = initial_cash
        share_balance = 0.0
        for bar_index in range(bar_count):
            selected = np.nan
            if holding:
                if prior_close[bar_index] >= prior_sma[bar_index, sell_column]:
                    selected = prior_sum[bar_index, sell_column] / (
                        windows[sell_column] - 1.0
                    )
                if stop_value >= 0.0:
                    candidate = effective_buy * (1.0 - stop_fraction)
                    if not np.isfinite(selected) or candidate > selected:
                        selected = candidate
                if np.isfinite(selected) and (
                    open_[bar_index] <= selected
                    or low[bar_index] <= selected < open_[bar_index]
                ):
                    raw = open_[bar_index] if open_[bar_index] <= selected else selected
                    effective_sell = raw * (1.0 - cost_rate)
                    cash_balance = share_balance * effective_sell
                    share_balance = 0.0
                    effective_buy = 0.0
                    sold_before = True
                    holding = False
            else:
                if prior_close[bar_index] <= prior_sma[bar_index, buy_column]:
                    selected = prior_sum[bar_index, buy_column] / (
                        windows[buy_column] - 1.0
                    )
                if sold_before:
                    candidate = effective_sell * (1.0 + rebuy_fraction)
                    if not np.isfinite(selected) or candidate < selected:
                        selected = candidate
                if np.isfinite(selected) and (
                    open_[bar_index] >= selected
                    or high[bar_index] >= selected > open_[bar_index]
                ):
                    raw = open_[bar_index] if open_[bar_index] >= selected else selected
                    effective_buy = raw * (1.0 + cost_rate)
                    share_balance = cash_balance / effective_buy
                    cash_balance = 0.0
                    holding = True
            if holding:
                exposure_count += 1

        _metrics_from_state(
            metrics,
            case_index,
            prior_equity,
            initial_cash,
            int(date_days[-1] - date_days[0]),
            bar_count,
            sum_returns,
            sum_squared_returns,
            worst_drawdown,
            exposure_count,
            bar_count,
            int(counts[0]),
            int(counts[1]),
            int(counts[2]),
            int(counts[3]),
            int(counts[4]),
        )
    return metrics, segment_start, segment_end, segment_sum, segment_sumsq


def _screen_inputs(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    pbo_block_count: int,
    subperiods: list[dict[str, Any]] | None,
) -> tuple[
    pd.DataFrame,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    list[dict[str, Any]],
]:
    if pbo_block_count < 4 or pbo_block_count % 2:
        raise ValueError("pbo_block_count must be even and at least four.")
    windows = np.asarray(
        sorted(set(cases["buy_window"].astype(int)) | set(cases["sell_window"].astype(int))),
        dtype=np.int64,
    )
    _, analysis = analysis_rows(
        data,
        windows=windows,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    window_index = {int(window): index for index, window in enumerate(windows)}
    parameters = np.column_stack(
        [
            cases["buy_window"].astype(int).map(window_index).to_numpy(np.float64),
            cases["sell_window"].astype(int).map(window_index).to_numpy(np.float64),
            cases["forced_rebuy_pct"].to_numpy(float),
            cases["stop_loss_pct_key"].to_numpy(float),
        ]
    )
    prior_sma = np.column_stack(
        [analysis[f"prior_sma_{int(window)}"].to_numpy(float) for window in windows]
    )
    prior_sum = np.column_stack(
        [analysis[f"prior_sum_{int(window)}"].to_numpy(float) for window in windows]
    )
    dates = pd.to_datetime(analysis["date"]).reset_index(drop=True)
    if subperiods is None:
        definitions = [
            {
                "period_id": "FULL",
                "start": dates.iloc[0].date().isoformat(),
                "end": dates.iloc[-1].date().isoformat(),
            }
        ]
    else:
        definitions = [dict(item) for item in subperiods]
    segment_index = np.full(len(dates), -1, dtype=np.int64)
    for index, item in enumerate(definitions):
        start = pd.Timestamp(item["start"])
        end = pd.Timestamp(item["end"])
        mask = (dates >= start) & (dates <= end)
        if not mask.any():
            raise ValueError(f"Subperiod {item['period_id']} contains no analysis sessions.")
        if (segment_index[mask.to_numpy()] >= 0).any():
            raise ValueError("Fixed subperiods overlap.")
        segment_index[mask.to_numpy()] = index
        item["actual_start"] = dates[mask].iloc[0].date().isoformat()
        item["actual_end"] = dates[mask].iloc[-1].date().isoformat()
        item["bar_count"] = int(mask.sum())
    if (segment_index < 0).any():
        raise ValueError("Fixed subperiods must cover every analysis session.")
    bar_count = len(analysis)
    pbo_index = np.minimum(
        np.arange(bar_count, dtype=np.int64) * pbo_block_count // bar_count,
        pbo_block_count - 1,
    )
    return analysis, windows, parameters, prior_sma, prior_sum, definitions, segment_index, pbo_index


def _append_segment_metrics(
    result: pd.DataFrame,
    arrays: tuple[np.ndarray, ...],
    definitions: list[dict[str, Any]],
) -> None:
    _, segment_start, segment_end, segment_sum, segment_sumsq, *_ = arrays
    primary_columns: list[str] = []
    for index, item in enumerate(definitions):
        period_id = str(item["period_id"])
        count = int(item["bar_count"])
        start_equity = segment_start[:, index]
        end_equity = segment_end[:, index]
        elapsed = max(
            (pd.Timestamp(item["actual_end"]) - pd.Timestamp(item["actual_start"])).days,
            1,
        ) / 365.2425
        cagr = (np.power(end_equity / start_equity, 1.0 / elapsed) - 1.0) * 100.0
        mean = segment_sum[:, index] / count
        variance = (
            segment_sumsq[:, index]
            - segment_sum[:, index] * segment_sum[:, index] / count
        ) / max(count - 1, 1)
        sharpe = np.full(len(result), np.nan, dtype=float)
        valid = variance > 0.0
        sharpe[valid] = mean[valid] / np.sqrt(variance[valid]) * math.sqrt(252.0)
        cagr_column = f"{period_id}_cagr_pct"
        result[cagr_column] = cagr
        result[f"{period_id}_sharpe"] = sharpe
        primary_columns.append(cagr_column)
    result["primary_metric"] = result[primary_columns].min(axis=1)


def screen_period_cross_cases(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    cost_bps: float,
    initial_cash: float,
    pbo_block_count: int = 12,
    subperiods: list[dict[str, Any]] | None = None,
    minimum_ordinary_buy_count: int = 0,
    minimum_ordinary_sell_count: int = 0,
) -> PeriodCrossScreen:
    (
        analysis,
        windows,
        parameters,
        prior_sma,
        prior_sum,
        definitions,
        segment_index,
        pbo_index,
    ) = _screen_inputs(
        data,
        cases,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        pbo_block_count=pbo_block_count,
        subperiods=subperiods,
    )
    dates = pd.to_datetime(analysis["date"])
    arrays = _simulate_period_grid(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        prior_sma,
        prior_sum,
        dates.to_numpy(dtype="datetime64[D]").astype(np.int64),
        segment_index,
        pbo_index,
        parameters,
        windows,
        float(cost_bps) / 10_000.0,
        float(initial_cash),
        len(definitions),
        pbo_block_count,
    )
    result = cases.reset_index(drop=True).copy()
    for index, column in enumerate(METRIC_COLUMNS):
        result[column] = arrays[0][:, index]
    _append_segment_metrics(result, arrays, definitions)
    result["identifiable"] = (
        (result["buy_sma_count"] >= int(minimum_ordinary_buy_count))
        & (result["sell_sma_count"] >= int(minimum_ordinary_sell_count))
    )
    pbo_counts = np.bincount(pbo_index, minlength=pbo_block_count).astype(np.int64)
    return PeriodCrossScreen(
        metrics=result,
        pbo_return_sum=arrays[5],
        pbo_return_sumsq=arrays[6],
        pbo_log_returns=arrays[7],
        pbo_counts=pbo_counts,
        analysis=analysis,
        segment_definitions=definitions,
    )


def screen_period_cross_cases_reference(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    cost_bps: float,
    initial_cash: float,
    pbo_block_count: int = 12,
    subperiods: list[dict[str, Any]] | None = None,
    minimum_ordinary_buy_count: int = 0,
    minimum_ordinary_sell_count: int = 0,
) -> PeriodCrossScreen:
    (
        analysis,
        windows,
        parameters,
        prior_sma,
        prior_sum,
        definitions,
        segment_index,
        _pbo_index,
    ) = _screen_inputs(
        data,
        cases,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
        pbo_block_count=pbo_block_count,
        subperiods=subperiods,
    )
    dates = pd.to_datetime(analysis["date"])
    arrays = _simulate_period_grid_reference(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        prior_sma,
        prior_sum,
        dates.to_numpy(dtype="datetime64[D]").astype(np.int64),
        segment_index,
        parameters,
        windows,
        float(cost_bps) / 10_000.0,
        float(initial_cash),
        len(definitions),
    )
    result = cases.reset_index(drop=True).copy()
    for index, column in enumerate(METRIC_COLUMNS):
        result[column] = arrays[0][:, index]
    _append_segment_metrics(result, arrays, definitions)
    result["identifiable"] = (
        (result["buy_sma_count"] >= int(minimum_ordinary_buy_count))
        & (result["sell_sma_count"] >= int(minimum_ordinary_sell_count))
    )
    return PeriodCrossScreen(
        metrics=result,
        pbo_return_sum=np.empty((len(result), 0)),
        pbo_return_sumsq=np.empty((len(result), 0)),
        pbo_log_returns=np.empty((len(result), 0)),
        pbo_counts=np.empty(0, dtype=np.int64),
        analysis=analysis,
        segment_definitions=definitions,
    )


def analyze_period_surface(
    frame: pd.DataFrame,
    *,
    metric: str,
    top_quantile: float,
    minimum_component_cells: int,
    minimum_buy_span: int,
    minimum_sell_span: int,
) -> dict[str, Any]:
    required = {"buy_window", "sell_window", metric, "sharpe", "cagr_pct", "order_count"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing period-surface columns: {sorted(missing)}")
    if not 0 < top_quantile < 1:
        raise ValueError("top_quantile must be between zero and one.")
    metric_pivot = (
        frame.pivot(index="buy_window", columns="sell_window", values=metric)
        .sort_index()
        .sort_index(axis=1)
    )
    if metric_pivot.isna().any().any():
        raise ValueError("Period surface must be a complete rectangle.")
    if "identifiable" in frame:
        gate = (
            frame.pivot(index="buy_window", columns="sell_window", values="identifiable")
            .reindex_like(metric_pivot)
            .to_numpy(bool)
        )
    else:
        gate = np.ones(metric_pivot.shape, dtype=bool)
    values = metric_pivot.to_numpy(float)
    eligible_values = values[gate]
    if not len(eligible_values):
        raise ValueError("No identifiable cells remain on this period surface.")
    threshold = float(np.quantile(eligible_values, top_quantile))
    mask = gate & (values >= threshold)
    components = connected_components(mask)
    if not components:
        raise ValueError("No cells passed the period-surface threshold.")
    component = components[0]
    buy_axis = metric_pivot.index.to_numpy(int)
    sell_axis = metric_pivot.columns.to_numpy(int)
    buy_min = int(min(buy_axis[row] for row, _ in component))
    buy_max = int(max(buy_axis[row] for row, _ in component))
    sell_min = int(min(sell_axis[column] for _, column in component))
    sell_max = int(max(sell_axis[column] for _, column in component))
    boundary_sides: list[str] = []
    if any(row == 0 for row, _ in component):
        boundary_sides.append("buy_min")
    if any(row == values.shape[0] - 1 for row, _ in component):
        boundary_sides.append("buy_max")
    if any(column == 0 for _, column in component):
        boundary_sides.append("sell_min")
    if any(column == values.shape[1] - 1 for _, column in component):
        boundary_sides.append("sell_max")
    structural_pass = bool(
        len(component) >= int(minimum_component_cells)
        and buy_max - buy_min >= int(minimum_buy_span)
        and sell_max - sell_min >= int(minimum_sell_span)
        and not boundary_sides
    )
    sharpe = (
        frame.pivot(index="buy_window", columns="sell_window", values="sharpe")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    cagr = (
        frame.pivot(index="buy_window", columns="sell_window", values="cagr_pct")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    orders = (
        frame.pivot(index="buy_window", columns="sell_window", values="order_count")
        .reindex_like(metric_pivot)
        .to_numpy(float)
    )
    centroid = (
        float(np.mean([buy_axis[row] for row, _ in component])),
        float(np.mean([sell_axis[column] for _, column in component])),
    )
    candidates: list[tuple[Any, ...]] = []
    for row, column in component:
        row_start, row_end = row - 1, row + 2
        col_start, col_end = column - 1, column + 2
        if row_start < 0 or col_start < 0 or row_end > values.shape[0] or col_end > values.shape[1]:
            continue
        local = values[row_start:row_end, col_start:col_end]
        local_score = float(np.median(local) - 0.5 * np.std(local))
        distance = math.hypot(
            float(buy_axis[row]) - centroid[0],
            float(sell_axis[column]) - centroid[1],
        )
        record = {
            "buy_window": int(buy_axis[row]),
            "sell_window": int(sell_axis[column]),
            "primary_metric": float(values[row, column]),
            "local_score": local_score,
            "local_median": float(np.median(local)),
            "local_std": float(np.std(local)),
            "full_history_sharpe": float(sharpe[row, column]),
            "full_history_cagr_pct": float(cagr[row, column]),
            "order_count": int(orders[row, column]),
            "distance_to_component_centroid": float(distance),
        }
        candidates.append(
            (
                local_score,
                float(sharpe[row, column]),
                float(cagr[row, column]),
                -distance,
                -float(orders[row, column]),
                record,
            )
        )
    representative = max(candidates, key=lambda item: item[:-1])[-1] if candidates else None
    return {
        "metric": metric,
        "top_quantile": float(top_quantile),
        "top_threshold": threshold,
        "eligible_cell_count": int(gate.sum()),
        "top_cell_count": int(mask.sum()),
        "component_count": len(components),
        "component_sizes": [len(item) for item in components],
        "largest_component": {
            "cell_count": len(component),
            "buy_min": buy_min,
            "buy_max": buy_max,
            "buy_span": buy_max - buy_min,
            "sell_min": sell_min,
            "sell_max": sell_max,
            "sell_span": sell_max - sell_min,
            "touches_search_boundary": bool(boundary_sides),
            "boundary_sides": boundary_sides,
            "structural_pass": structural_pass,
            "cells": [
                {
                    "buy_window": int(buy_axis[row]),
                    "sell_window": int(sell_axis[column]),
                }
                for row, column in component
            ],
        },
        "representative": representative,
    }
