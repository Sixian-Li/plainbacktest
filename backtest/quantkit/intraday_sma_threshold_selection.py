"""Robust parameter selection diagnostics for the intraday SMA200 strategy.

The execution rules deliberately mirror :mod:`intraday_sma_threshold_search`.
This module adds only research bookkeeping: annual state-preserving slices,
signal-identifiability counts, CSCV/PBO inputs, a stable-plateau selector, and
the Deflated Sharpe Ratio calculation.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numba
import numpy as np
import pandas as pd

from quantkit.intraday_sma_threshold_search import METRIC_COLUMNS, PARAMETER_COLUMNS
from quantkit.surface import connected_components


SIGNAL_COUNT_COLUMNS = (
    "buy_sma_count",
    "sell_sma_count",
    "buy_correction_count",
    "sell_correction_count",
)


@dataclass
class ContinuousScreen:
    metrics: pd.DataFrame
    year_start_equity: np.ndarray
    year_end_equity: np.ndarray
    year_return_sum: np.ndarray
    year_return_sumsq: np.ndarray
    year_counts: np.ndarray
    pbo_return_sum: np.ndarray
    pbo_return_sumsq: np.ndarray
    pbo_log_returns: np.ndarray
    pbo_counts: np.ndarray


@numba.njit(parallel=True, cache=True)
def _simulate_continuous_batch(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    prior_close: np.ndarray,
    prior_sma: np.ndarray,
    prior_sum: np.ndarray,
    date_days: np.ndarray,
    year_index: np.ndarray,
    pbo_index: np.ndarray,
    parameters: np.ndarray,
    window: int,
    cost_rate: float,
    initial_cash: float,
    year_count: int,
    pbo_block_count: int,
) -> tuple[np.ndarray, ...]:
    case_count = parameters.shape[0]
    metric_count = len(METRIC_COLUMNS) + len(SIGNAL_COUNT_COLUMNS)
    metrics = np.empty((case_count, metric_count), dtype=np.float64)
    year_start = np.empty((case_count, year_count), dtype=np.float64)
    year_end = np.empty((case_count, year_count), dtype=np.float64)
    year_sum = np.zeros((case_count, year_count), dtype=np.float64)
    year_sumsq = np.zeros((case_count, year_count), dtype=np.float64)
    pbo_sum = np.zeros((case_count, pbo_block_count), dtype=np.float64)
    pbo_sumsq = np.zeros((case_count, pbo_block_count), dtype=np.float64)
    pbo_log = np.zeros((case_count, pbo_block_count), dtype=np.float64)

    for case_index in numba.prange(case_count):
        a_pct = parameters[case_index, 0]
        b_pct = parameters[case_index, 1]
        correction_buy_pct = parameters[case_index, 2]
        correction_sell_pct = parameters[case_index, 3]
        sell_multiplier = 1.0 - a_pct / 100.0
        buy_multiplier = 1.0 + b_pct / 100.0
        buy_correction_enabled = correction_buy_pct >= 0.0
        sell_correction_enabled = correction_sell_pct >= 0.0
        cash = initial_cash
        shares = 0.0
        cost_basis = 0.0
        last_sell = 0.0
        has_last_sell = False
        order_count = 0
        exposure_count = 0
        buy_sma_count = 0
        sell_sma_count = 0
        buy_correction_count = 0
        sell_correction_count = 0
        previous_equity = initial_cash
        peak = 0.0
        max_drawdown = 0.0
        return_count = 0
        return_sum = 0.0
        return_sumsq = 0.0
        prior_year = -1

        for bar_index in range(close.shape[0]):
            current_year = year_index[bar_index]
            if current_year != prior_year:
                year_start[case_index, current_year] = previous_equity
                prior_year = current_year

            raw_fill = np.nan
            primary_signal = -1
            if shares > 0.0:
                trigger = sell_multiplier * prior_sum[bar_index] / (window - sell_multiplier)
                primary_signal = 1  # ordinary SMA sell
                if sell_correction_enabled:
                    correction = cost_basis * (1.0 - correction_sell_pct / 100.0)
                    if correction > trigger:
                        trigger = correction
                        primary_signal = 3  # correction sell
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
                        sell_correction_count += 1
            else:
                trigger = 1e300
                primary_signal = -1
                if prior_close[bar_index] <= prior_sma[bar_index] * buy_multiplier:
                    trigger = buy_multiplier * prior_sum[bar_index] / (window - buy_multiplier)
                    primary_signal = 0  # ordinary SMA buy
                if buy_correction_enabled and has_last_sell:
                    correction = last_sell * (1.0 + correction_buy_pct / 100.0)
                    if correction < trigger:
                        trigger = correction
                        primary_signal = 2  # correction buy
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
                        buy_correction_count += 1

            equity = cash + shares * close[bar_index]
            if shares > 0.0:
                exposure_count += 1
            daily_return = 0.0 if bar_index == 0 else equity / previous_equity - 1.0
            return_count += 1
            return_sum += daily_return
            return_sumsq += daily_return * daily_return
            year_sum[case_index, current_year] += daily_return
            year_sumsq[case_index, current_year] += daily_return * daily_return
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
            year_end[case_index, current_year] = equity

        elapsed_days = max(int(date_days[-1] - date_days[0]), 1)
        years = elapsed_days / 365.2425
        metrics[case_index, 0] = previous_equity
        metrics[case_index, 1] = (previous_equity / initial_cash - 1.0) * 100.0
        metrics[case_index, 2] = (
            (previous_equity / initial_cash) ** (1.0 / years) - 1.0
        ) * 100.0
        metrics[case_index, 3] = np.nan
        if return_count > 1:
            variance = (
                return_sumsq - return_sum * return_sum / return_count
            ) / (return_count - 1)
            if variance > 0.0:
                metrics[case_index, 3] = (
                    (return_sum / return_count) / math.sqrt(variance) * math.sqrt(252.0)
                )
        metrics[case_index, 4] = max_drawdown * 100.0
        metrics[case_index, 5] = exposure_count / close.shape[0] * 100.0
        metrics[case_index, 6] = order_count
        metrics[case_index, 7] = buy_sma_count
        metrics[case_index, 8] = sell_sma_count
        metrics[case_index, 9] = buy_correction_count
        metrics[case_index, 10] = sell_correction_count

    return metrics, year_start, year_end, year_sum, year_sumsq, pbo_sum, pbo_sumsq, pbo_log


def screen_continuous_cases(
    analysis: pd.DataFrame,
    cases: pd.DataFrame,
    *,
    window: int,
    cost_bps: float,
    initial_cash: float,
    pbo_block_count: int = 12,
) -> ContinuousScreen:
    required = {"date", "open", "high", "low", "close", "prior_close", "prior_sma", "prior_sum"}
    missing = required - set(analysis.columns)
    if missing:
        raise ValueError(f"Missing prepared analysis columns: {sorted(missing)}")
    numeric = list(required - {"date"})
    if analysis[numeric].isna().any().any():
        raise ValueError("Prepared analysis contains incomplete numeric inputs.")
    if pbo_block_count < 4 or pbo_block_count % 2:
        raise ValueError("pbo_block_count must be even and at least four.")

    dates = pd.to_datetime(analysis["date"])
    years = np.sort(dates.dt.year.unique())
    year_map = {int(year): index for index, year in enumerate(years)}
    year_index = dates.dt.year.map(year_map).to_numpy(np.int64)
    bar_count = len(analysis)
    pbo_index = np.minimum(
        np.arange(bar_count, dtype=np.int64) * pbo_block_count // bar_count,
        pbo_block_count - 1,
    )
    date_days = dates.to_numpy(dtype="datetime64[D]").astype(np.int64)
    if {"correction_buy_pct", "correction_sell_pct"}.issubset(cases.columns):
        parameters = cases[
            ["a_pct", "b_pct", "correction_buy_pct", "correction_sell_pct"]
        ].fillna(-1.0).to_numpy(float)
    else:
        symmetric = cases[list(PARAMETER_COLUMNS)].copy()
        symmetric["correction_pct"] = symmetric["correction_pct"].fillna(-1.0)
        parameters = np.column_stack(
            [
                symmetric["a_pct"].to_numpy(float),
                symmetric["b_pct"].to_numpy(float),
                symmetric["correction_pct"].to_numpy(float),
                symmetric["correction_pct"].to_numpy(float),
            ]
        )
    arrays = _simulate_continuous_batch(
        analysis["open"].to_numpy(float),
        analysis["high"].to_numpy(float),
        analysis["low"].to_numpy(float),
        analysis["close"].to_numpy(float),
        analysis["prior_close"].to_numpy(float),
        analysis["prior_sma"].to_numpy(float),
        analysis["prior_sum"].to_numpy(float),
        date_days,
        year_index,
        pbo_index,
        parameters,
        int(window),
        float(cost_bps) / 10_000.0,
        float(initial_cash),
        len(years),
        pbo_block_count,
    )
    metric_values, *diagnostics = arrays
    result = cases.reset_index(drop=True).copy()
    for column in ("correction_pct", "correction_buy_pct", "correction_sell_pct"):
        if column in result:
            result[column] = result[column].replace(-1.0, np.nan)
    for index, name in enumerate((*METRIC_COLUMNS, *SIGNAL_COUNT_COLUMNS)):
        result[name] = metric_values[:, index]
    year_counts = np.bincount(year_index, minlength=len(years)).astype(np.int64)
    pbo_counts = np.bincount(pbo_index, minlength=pbo_block_count).astype(np.int64)
    return ContinuousScreen(
        metrics=result,
        year_start_equity=diagnostics[0],
        year_end_equity=diagnostics[1],
        year_return_sum=diagnostics[2],
        year_return_sumsq=diagnostics[3],
        year_counts=year_counts,
        pbo_return_sum=diagnostics[4],
        pbo_return_sumsq=diagnostics[5],
        pbo_log_returns=diagnostics[6],
        pbo_counts=pbo_counts,
    )


def _sharpe_from_moments(total: np.ndarray, total_sq: np.ndarray, count: int) -> np.ndarray:
    mean = total / float(count)
    variance = (total_sq - total * total / float(count)) / float(max(count - 1, 1))
    output = np.full_like(mean, np.nan, dtype=float)
    valid = variance > 0.0
    output[valid] = mean[valid] / np.sqrt(variance[valid]) * math.sqrt(252.0)
    return output


def rolling_five_year_results(
    screen: ContinuousScreen,
    dates: pd.Series,
    case_ids: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized = pd.to_datetime(dates).reset_index(drop=True)
    years = np.sort(normalized.dt.year.unique())
    if len(years) < 5 or not np.array_equal(years, np.arange(years[0], years[-1] + 1)):
        raise ValueError("Rolling analysis requires consecutive calendar years.")
    first_dates = normalized.groupby(normalized.dt.year).min()
    last_dates = normalized.groupby(normalized.dt.year).max()
    cagr_columns: list[np.ndarray] = []
    sharpe_columns: list[np.ndarray] = []
    records: list[pd.DataFrame] = []
    for start_index in range(len(years) - 4):
        end_index = start_index + 4
        start_year = int(years[start_index])
        end_year = int(years[end_index])
        elapsed = max(
            (pd.Timestamp(last_dates.loc[end_year]) - pd.Timestamp(first_dates.loc[start_year])).days,
            1,
        ) / 365.2425
        start_equity = screen.year_start_equity[:, start_index]
        end_equity = screen.year_end_equity[:, end_index]
        cagr = (np.power(end_equity / start_equity, 1.0 / elapsed) - 1.0) * 100.0
        total = screen.year_return_sum[:, start_index : end_index + 1].sum(axis=1)
        total_sq = screen.year_return_sumsq[:, start_index : end_index + 1].sum(axis=1)
        count = int(screen.year_counts[start_index : end_index + 1].sum())
        sharpe = _sharpe_from_moments(total, total_sq, count)
        cagr_columns.append(cagr)
        sharpe_columns.append(sharpe)
        records.append(
            pd.DataFrame(
                {
                    "case_id": case_ids.to_numpy(),
                    "window_start_year": start_year,
                    "window_end_year": end_year,
                    "cagr_pct": cagr,
                    "sharpe": sharpe,
                }
            )
        )
    cagr_matrix = np.column_stack(cagr_columns)
    sharpe_matrix = np.column_stack(sharpe_columns)
    summary = pd.DataFrame(
        {
            "case_id": case_ids.to_numpy(),
            "rolling_5y_cagr_q25_pct": np.quantile(cagr_matrix, 0.25, axis=1),
            "rolling_5y_cagr_median_pct": np.median(cagr_matrix, axis=1),
            "rolling_5y_cagr_min_pct": np.min(cagr_matrix, axis=1),
            "rolling_5y_cagr_max_pct": np.max(cagr_matrix, axis=1),
            "rolling_5y_sharpe_q25": np.nanquantile(sharpe_matrix, 0.25, axis=1),
            "rolling_5y_sharpe_median": np.nanmedian(sharpe_matrix, axis=1),
        }
    )
    return summary, pd.concat(records, ignore_index=True)


def start_sensitivity_results(
    case_ids: pd.Series,
    start_years: list[int],
    cagr_matrix: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if cagr_matrix.shape != (len(case_ids), len(start_years)):
        raise ValueError("Start-sensitivity matrix shape does not match cases and years.")
    summary = pd.DataFrame(
        {
            "case_id": case_ids.to_numpy(),
            "restart_10y_cagr_q25_pct": np.quantile(cagr_matrix, 0.25, axis=1),
            "restart_10y_cagr_median_pct": np.median(cagr_matrix, axis=1),
            "restart_10y_cagr_min_pct": np.min(cagr_matrix, axis=1),
            "restart_10y_cagr_max_pct": np.max(cagr_matrix, axis=1),
        }
    )
    long = pd.DataFrame(
        {
            "case_id": np.repeat(case_ids.to_numpy(), len(start_years)),
            "start_year": np.tile(np.asarray(start_years, dtype=int), len(case_ids)),
            "end_year": np.tile(np.asarray(start_years, dtype=int) + 9, len(case_ids)),
            "cagr_pct": cagr_matrix.reshape(-1),
        }
    )
    return summary, long


def select_stable_plateau(
    frame: pd.DataFrame,
    *,
    metric: str = "rolling_5y_cagr_q25_pct",
    gate_column: str = "passes_restart_gate",
    top_quantile: float = 0.90,
    a_step: float = 0.25,
    b_step: float = 0.25,
    local_a_radius: float = 0.5,
    local_b_radius: float = 1.0,
    minimum_a_span: float = 1.0,
    minimum_b_span: float = 2.0,
) -> dict[str, Any]:
    required = {"a_pct", "b_pct", metric, gate_column, "restart_10y_cagr_q25_pct"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing plateau selection columns: {sorted(missing)}")
    pivot = frame.pivot(index="a_pct", columns="b_pct", values=metric).sort_index().sort_index(axis=1)
    gate = frame.pivot(index="a_pct", columns="b_pct", values=gate_column).reindex_like(pivot)
    if pivot.isna().any().any() or gate.isna().any().any():
        raise ValueError("Plateau selection requires a complete rectangular grid.")
    values = pivot.to_numpy(float)
    gate_values = gate.to_numpy(bool)
    if not gate_values.any():
        raise ValueError("No parameter passes the restart gate.")
    threshold = float(np.quantile(values[gate_values], top_quantile))
    mask = gate_values & (values >= threshold)
    components = connected_components(mask)
    if not components:
        raise ValueError("No gated cells pass the primary-metric threshold.")
    components = sorted(
        components,
        key=lambda component: (
            len(component),
            float(np.median([values[row, column] for row, column in component])),
        ),
        reverse=True,
    )
    component = components[0]
    a_axis = pivot.index.to_numpy(float)
    b_axis = pivot.columns.to_numpy(float)
    coords = [(float(a_axis[row]), float(b_axis[column])) for row, column in component]
    a_radius_cells = int(round(local_a_radius / a_step))
    b_radius_cells = int(round(local_b_radius / b_step))
    centroid_a = float(np.mean([point[0] for point in coords]))
    centroid_b = float(np.mean([point[1] for point in coords]))
    restart = frame.pivot(
        index="a_pct", columns="b_pct", values="restart_10y_cagr_q25_pct"
    ).reindex_like(pivot).to_numpy(float)
    candidates: list[dict[str, Any]] = []
    for row, column in component:
        row_start = row - a_radius_cells
        row_end = row + a_radius_cells + 1
        col_start = column - b_radius_cells
        col_end = column + b_radius_cells + 1
        if row_start < 0 or col_start < 0 or row_end > values.shape[0] or col_end > values.shape[1]:
            continue
        local = values[row_start:row_end, col_start:col_end]
        local_score = float(np.median(local) - 0.5 * np.std(local))
        a_value = float(a_axis[row])
        b_value = float(b_axis[column])
        candidates.append(
            {
                "a_pct": a_value,
                "b_pct": b_value,
                "primary_metric": float(values[row, column]),
                "restart_metric": float(restart[row, column]),
                "local_score": local_score,
                "local_median": float(np.median(local)),
                "local_std": float(np.std(local)),
                "local_cell_count": int(local.size),
                "distance_to_component_centroid": float(
                    math.hypot(
                        (a_value - centroid_a) / max(local_a_radius, a_step),
                        (b_value - centroid_b) / max(local_b_radius, b_step),
                    )
                ),
            }
        )
    if not candidates:
        raise ValueError("Largest plateau contains no complete local neighborhood.")
    representative = max(
        candidates,
        key=lambda item: (
            item["local_score"],
            item["restart_metric"],
            -item["distance_to_component_centroid"],
            item["primary_metric"],
        ),
    )
    boundary_sides: list[str] = []
    if any(row == 0 for row, _ in component):
        boundary_sides.append("a_min")
    if any(row == values.shape[0] - 1 for row, _ in component):
        boundary_sides.append("a_max")
    if any(column == 0 for _, column in component):
        boundary_sides.append("b_min")
    if any(column == values.shape[1] - 1 for _, column in component):
        boundary_sides.append("b_max")
    a_span = max(point[0] for point in coords) - min(point[0] for point in coords)
    b_span = max(point[1] for point in coords) - min(point[1] for point in coords)
    structural_pass = (
        a_span >= minimum_a_span
        and b_span >= minimum_b_span
        and not boundary_sides
    )
    return {
        "metric": metric,
        "gate_column": gate_column,
        "top_quantile": top_quantile,
        "top_threshold": threshold,
        "gated_cell_count": int(gate_values.sum()),
        "top_gated_cell_count": int(mask.sum()),
        "component_count": len(components),
        "component_sizes": [len(item) for item in components],
        "largest_component": {
            "cell_count": len(component),
            "a_min": min(point[0] for point in coords),
            "a_max": max(point[0] for point in coords),
            "a_span": a_span,
            "b_min": min(point[1] for point in coords),
            "b_max": max(point[1] for point in coords),
            "b_span": b_span,
            "centroid_a": centroid_a,
            "centroid_b": centroid_b,
            "boundary_sides": boundary_sides,
            "touches_search_boundary": bool(boundary_sides),
            "structural_pass": structural_pass,
            "cells": [{"a_pct": a, "b_pct": b} for a, b in coords],
        },
        "representative": representative,
    }


def cscv_pbo(
    return_sum: np.ndarray,
    return_sumsq: np.ndarray,
    block_counts: np.ndarray,
    eligible: np.ndarray,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if return_sum.shape != return_sumsq.shape:
        raise ValueError("PBO moment matrices must have identical shapes.")
    block_count = return_sum.shape[1]
    if block_count % 2 or len(block_counts) != block_count:
        raise ValueError("PBO requires an even block count and matching counts.")
    eligible_indices = np.flatnonzero(np.asarray(eligible, dtype=bool))
    if len(eligible_indices) < 2:
        raise ValueError("PBO requires at least two eligible strategies.")
    half = block_count // 2
    all_blocks = set(range(block_count))
    records: list[dict[str, Any]] = []
    for split_id, train_tuple in enumerate(itertools.combinations(range(block_count), half), start=1):
        train = np.asarray(train_tuple, dtype=int)
        test = np.asarray(sorted(all_blocks - set(train_tuple)), dtype=int)
        train_count = int(block_counts[train].sum())
        test_count = int(block_counts[test].sum())
        train_sharpe = _sharpe_from_moments(
            return_sum[:, train].sum(axis=1),
            return_sumsq[:, train].sum(axis=1),
            train_count,
        )[eligible_indices]
        test_sharpe = _sharpe_from_moments(
            return_sum[:, test].sum(axis=1),
            return_sumsq[:, test].sum(axis=1),
            test_count,
        )[eligible_indices]
        finite_train = np.isfinite(train_sharpe)
        if not finite_train.any():
            continue
        local_winner = int(np.nanargmax(np.where(finite_train, train_sharpe, np.nan)))
        winner_index = int(eligible_indices[local_winner])
        selected_test = float(test_sharpe[local_winner])
        finite_test = test_sharpe[np.isfinite(test_sharpe)]
        if not np.isfinite(selected_test) or len(finite_test) < 2:
            rank_percentile = 0.0
        else:
            below = float(np.count_nonzero(finite_test < selected_test))
            equal = float(np.count_nonzero(np.isclose(finite_test, selected_test, rtol=0, atol=1e-12)))
            rank_percentile = (below + 0.5 * equal) / len(finite_test)
        clipped = min(max(rank_percentile, 1e-12), 1.0 - 1e-12)
        records.append(
            {
                "split_id": split_id,
                "train_blocks": "|".join(str(value + 1) for value in train),
                "test_blocks": "|".join(str(value + 1) for value in test),
                "winner_row_index": winner_index,
                "train_sharpe": float(train_sharpe[local_winner]),
                "test_sharpe": selected_test,
                "oos_rank_percentile": rank_percentile,
                "logit_rank": math.log(clipped / (1.0 - clipped)),
                "below_median_oos": bool(rank_percentile <= 0.5),
            }
        )
    splits = pd.DataFrame(records)
    expected = math.comb(block_count, half)
    if len(splits) != expected:
        raise AssertionError(f"Expected {expected} valid CSCV splits, found {len(splits)}.")
    summary = {
        "block_count": block_count,
        "train_block_count": half,
        "split_count": len(splits),
        "eligible_strategy_count": len(eligible_indices),
        "performance_metric": "annualized daily Sharpe from block-level return moments",
        "pbo": float(splits["below_median_oos"].mean()),
        "median_oos_rank_percentile": float(splits["oos_rank_percentile"].median()),
        "median_train_sharpe": float(splits["train_sharpe"].median()),
        "median_test_sharpe": float(splits["test_sharpe"].median()),
    }
    return summary, splits


def effective_trial_count(block_log_returns: np.ndarray, eligible: np.ndarray) -> dict[str, float]:
    matrix = np.asarray(block_log_returns, dtype=float)[np.asarray(eligible, dtype=bool)]
    centered = matrix - matrix.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(centered, axis=1)
    valid = norms > 0.0
    centered = centered[valid]
    norms = norms[valid]
    if len(centered) < 2:
        return {
            "strategy_count": float(len(centered)),
            "average_pairwise_correlation": 1.0,
            "clamped_correlation": 1.0,
            "effective_trial_count": 1.0,
        }
    standardized = centered / norms[:, None]
    summed = standardized.sum(axis=0)
    ordered_pair_sum = float(np.dot(summed, summed) - len(standardized))
    average = ordered_pair_sum / (len(standardized) * (len(standardized) - 1))
    clamped = min(max(average, 0.0), 1.0)
    effective = 1.0 + (len(standardized) - 1.0) * (1.0 - clamped)
    return {
        "strategy_count": float(len(standardized)),
        "average_pairwise_correlation": average,
        "clamped_correlation": clamped,
        "effective_trial_count": effective,
    }


def deflated_sharpe_probability(
    daily_returns: np.ndarray,
    trial_sharpes_annualized: np.ndarray,
    *,
    trial_count: float,
    annual_bars: int = 252,
) -> dict[str, float]:
    returns = np.asarray(daily_returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    sharpes = np.asarray(trial_sharpes_annualized, dtype=float)
    sharpes = sharpes[np.isfinite(sharpes)] / math.sqrt(annual_bars)
    if len(returns) < 3 or len(sharpes) < 2 or trial_count < 1:
        raise ValueError("DSR requires sufficient returns, trial Sharpes, and trial_count >= 1.")
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std <= 0.0:
        raise ValueError("DSR candidate returns have zero variance.")
    candidate_sr = mean / std
    centered = returns - mean
    population_std = float(np.std(returns, ddof=0))
    skew = float(np.mean(centered**3) / population_std**3)
    kurtosis = float(np.mean(centered**4) / population_std**4)
    trial_sr_std = float(np.std(sharpes, ddof=1))
    gamma = 0.5772156649015329
    n = max(float(trial_count), 1.0000001)
    normal = NormalDist()
    expected_max = trial_sr_std * (
        (1.0 - gamma) * normal.inv_cdf(1.0 - 1.0 / n)
        + gamma * normal.inv_cdf(1.0 - 1.0 / (n * math.e))
    )
    variance_adjustment = 1.0 - skew * candidate_sr + (
        (kurtosis - 1.0) / 4.0
    ) * candidate_sr * candidate_sr
    if variance_adjustment <= 0.0:
        raise ValueError("DSR variance adjustment is non-positive.")
    z_score = (
        (candidate_sr - expected_max)
        * math.sqrt(len(returns) - 1.0)
        / math.sqrt(variance_adjustment)
    )
    return {
        "trial_count": float(trial_count),
        "candidate_sharpe_daily": candidate_sr,
        "candidate_sharpe_annualized": candidate_sr * math.sqrt(annual_bars),
        "trial_sharpe_std_daily": trial_sr_std,
        "expected_max_sharpe_daily": expected_max,
        "expected_max_sharpe_annualized": expected_max * math.sqrt(annual_bars),
        "return_observation_count": float(len(returns)),
        "return_skewness": skew,
        "return_kurtosis_non_excess": kurtosis,
        "z_score": z_score,
        "probability": normal.cdf(z_score),
    }
