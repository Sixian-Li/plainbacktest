"""Compiled full-grid ledgers for the two-SMA recovery probation strategy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numba
import numpy as np
import pandas as pd

from quantkit.intraday_sma_period_cross import analysis_rows
from quantkit.intraday_sma_period_cross_search import (
    _append_segment_metrics,
    analyze_period_surface,
)


METRIC_COLUMNS = (
    "final_equity",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "max_drawdown_pct",
    "exposure_pct",
    "order_count",
    "ordinary_buy_count",
    "confirmed_sell_count",
    "forced_rebuy_count",
    "ordinary_failure_count",
    "forced_failure_count",
    "probation_confirmation_count",
    "forced_to_ordinary_count",
)


@dataclass
class RecoveryProbationScreen:
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


def build_recovery_grid_cases(parameters: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"buy_window": buy, "sell_window": sell}
        for buy in _inclusive_integer_range(parameters["buy_sma_window_range"])
        for sell in _inclusive_integer_range(parameters["sell_sma_window_range"])
    ]
    frame = pd.DataFrame(rows)
    expected = int(parameters["combination_count"])
    if len(frame) != expected:
        raise AssertionError(f"Expected {expected:,} grid cases, generated {len(frame):,}.")
    width = max(4, len(str(expected)))
    frame.insert(0, "case_id", [f"CASE_{index:0{width}d}" for index in range(1, len(frame) + 1)])
    if frame.duplicated(["buy_window", "sell_window"]).any():
        raise AssertionError("Recovery-probation grid contains duplicate cases.")
    return frame


@numba.njit(cache=True)
def _write_metrics(
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
    counts: np.ndarray,
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
    for offset in range(8):
        metrics[case_index, 6 + offset] = counts[offset]


@numba.njit(parallel=True, cache=True)
def _simulate_recovery_grid(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    sma: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    segment_index: np.ndarray,
    pbo_index: np.ndarray,
    parameters: np.ndarray,
    windows: np.ndarray,
    forced_rebuy_pct: float,
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
    rebuy_multiplier = 1.0 + forced_rebuy_pct / 100.0

    for case_index in numba.prange(case_count):
        buy_index = int(parameters[case_index, 0])
        sell_index = int(parameters[case_index, 1])
        sell_window = windows[sell_index]
        state = 0  # 0 flat, 1 ordinary probation, 2 forced probation, 3 confirmed
        pending = 0  # 1 ordinary buy, 2 c buy, 3 SMA sell, 4/5 failure sells
        cash = initial_cash
        shares = 0.0
        anchor = 0.0
        has_anchor = False
        c_available = False
        previous_equity = initial_cash
        peak = 0.0
        max_drawdown = 0.0
        exposure_count = 0
        return_sum = 0.0
        return_sumsq = 0.0
        counts = np.zeros(8, dtype=np.int64)
        prior_segment = -1

        for bar_index in range(bar_count):
            segment = segment_index[bar_index]
            if segment != prior_segment:
                segment_start[case_index, segment] = previous_equity
                prior_segment = segment

            raw_fill = np.nan
            if pending == 1 or pending == 4 or pending == 5:
                raw_fill = open_[bar_index]
            elif pending == 2:
                trigger = anchor * rebuy_multiplier
                if open_[bar_index] >= trigger:
                    raw_fill = open_[bar_index]
                elif high[bar_index] >= trigger > open_[bar_index]:
                    raw_fill = trigger
            elif pending == 3:
                trigger = prior_sum[bar_index, sell_index] / (sell_window - 1.0)
                if open_[bar_index] <= trigger:
                    raw_fill = open_[bar_index]
                elif low[bar_index] <= trigger < open_[bar_index]:
                    raw_fill = trigger

            if np.isfinite(raw_fill):
                counts[0] += 1
                if pending == 1:
                    fill = raw_fill * (1.0 + cost_rate)
                    shares = cash / fill
                    cash = 0.0
                    state = 1
                    counts[1] += 1
                elif pending == 2:
                    fill = raw_fill * (1.0 + cost_rate)
                    shares = cash / fill
                    cash = 0.0
                    state = 2
                    c_available = False
                    counts[3] += 1
                else:
                    fill = raw_fill * (1.0 - cost_rate)
                    cash = shares * fill
                    shares = 0.0
                    if pending == 3:
                        anchor = fill
                        has_anchor = True
                        c_available = True
                        counts[2] += 1
                    elif pending == 4:
                        counts[4] += 1
                    else:
                        c_available = False
                        counts[5] += 1
                    state = 0
            pending = 0

            ordinary_cross = (
                prior_close[bar_index] <= prior_sma[bar_index, buy_index]
                and close[bar_index] > sma[bar_index, buy_index]
            )
            if state == 0:
                if ordinary_cross:
                    pending = 1
                elif c_available and has_anchor:
                    pending = 2
            elif state == 1:
                if close[bar_index] < sma[bar_index, buy_index]:
                    pending = 4
                elif close[bar_index] >= sma[bar_index, sell_index]:
                    state = 3
                    c_available = False
                    counts[6] += 1
                    pending = 3
            elif state == 2:
                if close[bar_index] < anchor:
                    c_available = False
                    pending = 5
                elif close[bar_index] >= sma[bar_index, sell_index]:
                    state = 3
                    c_available = False
                    counts[6] += 1
                    pending = 3
                elif ordinary_cross:
                    state = 1
                    c_available = False
                    counts[7] += 1
            else:
                pending = 3

            equity = cash + shares * close[bar_index]
            if shares > 0.0:
                exposure_count += 1
            daily_return = 0.0 if bar_index == 0 else equity / previous_equity - 1.0
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

        _write_metrics(
            metrics,
            case_index,
            previous_equity,
            initial_cash,
            int(date_days[-1] - date_days[0]),
            bar_count,
            return_sum,
            return_sumsq,
            max_drawdown,
            exposure_count,
            bar_count,
            counts,
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
def _simulate_recovery_grid_reference(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    sma: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    segment_index: np.ndarray,
    parameters: np.ndarray,
    windows: np.ndarray,
    forced_rebuy_pct: float,
    cost_rate: float,
    initial_cash: float,
    segment_count: int,
) -> tuple[np.ndarray, ...]:
    case_count = parameters.shape[0]
    bar_count = close.shape[0]
    metrics = np.empty((case_count, len(METRIC_COLUMNS)), dtype=np.float64)
    segment_start = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_end = np.full((case_count, segment_count), np.nan, dtype=np.float64)
    segment_sum = np.zeros((case_count, segment_count), dtype=np.float64)
    segment_sumsq = np.zeros((case_count, segment_count), dtype=np.float64)

    for case_index in numba.prange(case_count):
        buy_col = int(parameters[case_index, 0])
        sell_col = int(parameters[case_index, 1])
        short_n = windows[sell_col]
        phase = 0
        next_action = 0
        cash_balance = initial_cash
        share_balance = 0.0
        original_sell = 0.0
        original_sell_exists = False
        correction_is_live = False
        totals = np.zeros(8, dtype=np.int64)
        prior_equity = initial_cash
        high_water = 0.0
        worst_drawdown = 0.0
        exposed = 0
        sum_returns = 0.0
        sum_squared_returns = 0.0
        prior_segment = -1

        for bar in range(bar_count):
            segment = segment_index[bar]
            if segment != prior_segment:
                segment_start[case_index, segment] = prior_equity
                prior_segment = segment

            did_fill = False
            market_fill = 0.0
            if next_action in (1, 4, 5):
                market_fill = open_[bar]
                did_fill = True
            elif next_action == 2:
                correction_line = original_sell * (1.0 + forced_rebuy_pct / 100.0)
                if open_[bar] >= correction_line:
                    market_fill = open_[bar]
                    did_fill = True
                elif open_[bar] < correction_line <= high[bar]:
                    market_fill = correction_line
                    did_fill = True
            elif next_action == 3:
                exit_line = prior_sum[bar, sell_col] / (short_n - 1.0)
                if open_[bar] <= exit_line:
                    market_fill = open_[bar]
                    did_fill = True
                elif low[bar] <= exit_line < open_[bar]:
                    market_fill = exit_line
                    did_fill = True

            if did_fill:
                totals[0] += 1
                if next_action == 1:
                    paid = market_fill * (1.0 + cost_rate)
                    share_balance = cash_balance / paid
                    cash_balance = 0.0
                    phase = 1
                    totals[1] += 1
                elif next_action == 2:
                    paid = market_fill * (1.0 + cost_rate)
                    share_balance = cash_balance / paid
                    cash_balance = 0.0
                    phase = 2
                    correction_is_live = False
                    totals[3] += 1
                else:
                    received = market_fill * (1.0 - cost_rate)
                    cash_balance = share_balance * received
                    share_balance = 0.0
                    if next_action == 3:
                        original_sell = received
                        original_sell_exists = True
                        correction_is_live = True
                        totals[2] += 1
                    elif next_action == 4:
                        totals[4] += 1
                    else:
                        correction_is_live = False
                        totals[5] += 1
                    phase = 0
            next_action = 0

            recovered_long = (
                prior_close[bar] <= prior_sma[bar, buy_col]
                and close[bar] > sma[bar, buy_col]
            )
            if phase == 0:
                if recovered_long:
                    next_action = 1
                elif correction_is_live and original_sell_exists:
                    next_action = 2
            elif phase == 1:
                failed = close[bar] < sma[bar, buy_col]
                confirmed = close[bar] >= sma[bar, sell_col]
                if failed:
                    next_action = 4
                elif confirmed:
                    phase = 3
                    correction_is_live = False
                    totals[6] += 1
                    next_action = 3
            elif phase == 2:
                failed = close[bar] < original_sell
                confirmed = close[bar] >= sma[bar, sell_col]
                if failed:
                    correction_is_live = False
                    next_action = 5
                elif confirmed:
                    phase = 3
                    correction_is_live = False
                    totals[6] += 1
                    next_action = 3
                elif recovered_long:
                    phase = 1
                    correction_is_live = False
                    totals[7] += 1
            else:
                next_action = 3

            equity = cash_balance + share_balance * close[bar]
            if share_balance > 0.0:
                exposed += 1
            one_day = 0.0 if bar == 0 else equity / prior_equity - 1.0
            sum_returns += one_day
            sum_squared_returns += one_day * one_day
            segment_sum[case_index, segment] += one_day
            segment_sumsq[case_index, segment] += one_day * one_day
            segment_end[case_index, segment] = equity
            if bar == 0 or equity > high_water:
                high_water = equity
            underwater = equity / high_water - 1.0
            if underwater < worst_drawdown:
                worst_drawdown = underwater
            prior_equity = equity

        _write_metrics(
            metrics,
            case_index,
            prior_equity,
            initial_cash,
            int(date_days[-1] - date_days[0]),
            bar_count,
            sum_returns,
            sum_squared_returns,
            worst_drawdown,
            exposed,
            bar_count,
            totals,
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
) -> tuple[Any, ...]:
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
            cases["buy_window"].astype(int).map(window_index).to_numpy(np.int64),
            cases["sell_window"].astype(int).map(window_index).to_numpy(np.int64),
        ]
    )
    sma = np.column_stack(
        [analysis[f"sma_{int(window)}"].to_numpy(float) for window in windows]
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
        mask = (dates >= pd.Timestamp(item["start"])) & (dates <= pd.Timestamp(item["end"]))
        if not mask.any():
            raise ValueError(f"Subperiod {item['period_id']} contains no sessions.")
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
    return (
        analysis,
        windows,
        parameters,
        sma,
        prior_sma,
        prior_sum,
        definitions,
        segment_index,
        pbo_index,
    )


def _finish_result(
    cases: pd.DataFrame,
    arrays: tuple[np.ndarray, ...],
    definitions: list[dict[str, Any]],
    *,
    minimum_ordinary_buy_count: int,
    minimum_confirmed_sell_count: int,
    minimum_probation_failure_count: int,
) -> pd.DataFrame:
    result = cases.reset_index(drop=True).copy()
    for index, column in enumerate(METRIC_COLUMNS):
        result[column] = arrays[0][:, index]
    _append_segment_metrics(result, arrays, definitions)
    failures = result["ordinary_failure_count"] + result["forced_failure_count"]
    result["probation_failure_count"] = failures
    result["identifiable"] = (
        (result["ordinary_buy_count"] >= int(minimum_ordinary_buy_count))
        & (result["confirmed_sell_count"] >= int(minimum_confirmed_sell_count))
        & (failures >= int(minimum_probation_failure_count))
    )
    return result


def screen_recovery_probation_cases(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    cost_bps: float,
    initial_cash: float,
    forced_rebuy_pct: float,
    pbo_block_count: int = 12,
    subperiods: list[dict[str, Any]] | None = None,
    minimum_ordinary_buy_count: int = 0,
    minimum_confirmed_sell_count: int = 0,
    minimum_probation_failure_count: int = 0,
) -> RecoveryProbationScreen:
    (
        analysis,
        windows,
        parameters,
        sma,
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
    arrays = _simulate_recovery_grid(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        sma,
        prior_sma,
        prior_sum,
        dates.to_numpy(dtype="datetime64[D]").astype(np.int64),
        segment_index,
        pbo_index,
        parameters,
        windows,
        float(forced_rebuy_pct),
        float(cost_bps) / 10_000.0,
        float(initial_cash),
        len(definitions),
        pbo_block_count,
    )
    result = _finish_result(
        cases,
        arrays,
        definitions,
        minimum_ordinary_buy_count=minimum_ordinary_buy_count,
        minimum_confirmed_sell_count=minimum_confirmed_sell_count,
        minimum_probation_failure_count=minimum_probation_failure_count,
    )
    return RecoveryProbationScreen(
        metrics=result,
        pbo_return_sum=arrays[5],
        pbo_return_sumsq=arrays[6],
        pbo_log_returns=arrays[7],
        pbo_counts=np.bincount(pbo_index, minlength=pbo_block_count).astype(np.int64),
        analysis=analysis,
        segment_definitions=definitions,
    )


def screen_recovery_probation_cases_reference(
    data: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    cost_bps: float,
    initial_cash: float,
    forced_rebuy_pct: float,
    pbo_block_count: int = 12,
    subperiods: list[dict[str, Any]] | None = None,
    minimum_ordinary_buy_count: int = 0,
    minimum_confirmed_sell_count: int = 0,
    minimum_probation_failure_count: int = 0,
) -> RecoveryProbationScreen:
    (
        analysis,
        windows,
        parameters,
        sma,
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
    arrays = _simulate_recovery_grid_reference(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        sma,
        prior_sma,
        prior_sum,
        dates.to_numpy(dtype="datetime64[D]").astype(np.int64),
        segment_index,
        parameters,
        windows,
        float(forced_rebuy_pct),
        float(cost_bps) / 10_000.0,
        float(initial_cash),
        len(definitions),
    )
    result = _finish_result(
        cases,
        arrays,
        definitions,
        minimum_ordinary_buy_count=minimum_ordinary_buy_count,
        minimum_confirmed_sell_count=minimum_confirmed_sell_count,
        minimum_probation_failure_count=minimum_probation_failure_count,
    )
    return RecoveryProbationScreen(
        metrics=result,
        pbo_return_sum=np.empty((len(result), 0)),
        pbo_return_sumsq=np.empty((len(result), 0)),
        pbo_log_returns=np.empty((len(result), 0)),
        pbo_counts=np.empty(0, dtype=np.int64),
        analysis=analysis,
        segment_definitions=definitions,
    )


__all__ = [
    "METRIC_COLUMNS",
    "RecoveryProbationScreen",
    "analyze_period_surface",
    "build_recovery_grid_cases",
    "screen_recovery_probation_cases",
    "screen_recovery_probation_cases_reference",
]
