"""Pure calculations for the interactive dual-SMA backtest laboratory."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import math
from typing import Iterable

import numpy as np
import pandas as pd

from quantkit.dual_sma_state import (
    FAST_SMA_COLUMN,
    SLOW_SMA_COLUMN,
    DualSmaStateSpec,
    run_reference_dual_sma,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import calculate_holding_period_metrics, calculate_metrics
from quantkit.reference import ReferenceResult, run_buy_and_hold_reference


ANNUAL_BARS = 252
MAX_GRID_CASES = 50_000


@dataclass(frozen=True)
class GridSpec:
    fast_start: int = 1
    fast_end: int = 50
    fast_step: int = 1
    slow_start: int = 15
    slow_end: int = 250
    slow_step: int = 1

    def __post_init__(self) -> None:
        for name in ("fast_start", "fast_end", "fast_step", "slow_start", "slow_end", "slow_step"):
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} 必须为正整数。")
            object.__setattr__(self, name, value)
        if self.fast_end < self.fast_start or self.slow_end < self.slow_start:
            raise ValueError("网格终点不能早于起点。")
        pairs = self.pairs
        if not pairs:
            raise ValueError("当前范围没有 fast < slow 的有效参数。")
        if len(pairs) > MAX_GRID_CASES:
            raise ValueError(
                f"有效参数共 {len(pairs):,} 组，超过页面上限 {MAX_GRID_CASES:,}；"
                "请缩小范围或增大步长。"
            )

    @cached_property
    def fast_values(self) -> tuple[int, ...]:
        return tuple(range(self.fast_start, self.fast_end + 1, self.fast_step))

    @cached_property
    def slow_values(self) -> tuple[int, ...]:
        return tuple(range(self.slow_start, self.slow_end + 1, self.slow_step))

    @cached_property
    def pairs(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (fast, slow)
            for fast in self.fast_values
            for slow in self.slow_values
            if fast < slow
        )


@dataclass(frozen=True)
class SingleRunResult:
    symbol: str
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp
    effective_start: pd.Timestamp
    effective_end: pd.Timestamp
    fast_window: int
    slow_window: int
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, object]
    benchmark_daily: pd.DataFrame
    benchmark_metrics: dict[str, object]


@dataclass(frozen=True)
class GridResult:
    symbol: str
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp
    effective_start: pd.Timestamp
    effective_end: pd.Timestamp
    spec: GridSpec
    metrics: pd.DataFrame
    benchmark_metrics: dict[str, object]


def _validate_inputs(
    data: pd.DataFrame,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    *,
    initial_cash: float,
    cost_bps: float,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp, ExplicitFillPolicy]:
    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"缺少 canonical 列：{sorted(missing)}")
    frame = data.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    if frame.empty or frame["symbol"].nunique() != 1:
        raise ValueError("输入必须包含一个非空标的。")
    if frame["date"].duplicated().any():
        raise ValueError("输入包含重复交易日。")
    requested_start = pd.Timestamp(start).normalize()
    requested_end = pd.Timestamp(end).normalize()
    if requested_end < requested_start:
        raise ValueError("结束日期不能早于开始日期。")
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("初始资金必须为正数。")
    policy = ExplicitFillPolicy("open", cost_bps=float(cost_bps))
    return frame, requested_start, requested_end, policy


def _rolling_sma(values: np.ndarray, window: int) -> np.ndarray:
    return (
        pd.Series(np.asarray(values, dtype=float))
        .rolling(window, min_periods=window)
        .mean()
        .to_numpy(float)
    )


def _analysis_rows(
    frame: pd.DataFrame,
    requested_start: pd.Timestamp,
    requested_end: pd.Timestamp,
    *,
    maximum_window: int,
) -> tuple[pd.DataFrame, np.ndarray]:
    selected = frame["date"].between(requested_start, requested_end, inclusive="both").to_numpy()
    warm = np.isfinite(_rolling_sma(frame["close"].to_numpy(float), maximum_window))
    positions = np.flatnonzero(selected & warm)
    if len(positions) < 2:
        raise ValueError(
            f"所选日期在 SMA{maximum_window} 完成预热后不足两个交易日；"
            "请延后开始日期、延长区间或缩短均线。"
        )
    return frame.iloc[positions].reset_index(drop=True), positions


def _sample_sharpe(values: pd.Series | np.ndarray, annual_bars: int = ANNUAL_BARS) -> float:
    returns = np.asarray(values, dtype=float)
    returns = returns[np.isfinite(returns)]
    if len(returns) < 2:
        return float("nan")
    deviation = float(np.std(returns, ddof=1))
    if deviation <= 0:
        return float("nan")
    return float(np.mean(returns) / deviation * math.sqrt(annual_bars))


def holding_return_metrics(daily: pd.DataFrame) -> dict[str, object]:
    """Sharpe of account returns affected by a position, including exit opens."""

    state = daily.copy()
    state["date"] = pd.to_datetime(state["date"])
    state = state.sort_values("date", kind="stable").reset_index(drop=True)
    account_returns = state["equity"].astype(float).pct_change().fillna(0.0)
    executed = state.get("executed", pd.Series(0, index=state.index)).astype(int)
    active = state["is_long"].astype(bool) | executed.eq(-1)
    active_returns = account_returns.loc[active]
    return {
        "holding_return_observations": int(active.sum()),
        "holding_return_sharpe": _sample_sharpe(active_returns),
        "holding_return_compound_pct": float(
            (np.prod(1.0 + active_returns.to_numpy(float)) - 1.0) * 100.0
        ),
    }


def _summarize_reference(
    result: ReferenceResult,
    *,
    initial_cash: float,
) -> dict[str, object]:
    metrics = calculate_metrics(
        result.daily,
        result.orders,
        result.trades,
        initial_cash=float(initial_cash),
    )
    metrics.update(
        calculate_holding_period_metrics(
            result.daily,
            initial_cash=float(initial_cash),
        )
    )
    active_metrics = holding_return_metrics(result.daily)
    metrics.update(active_metrics)
    reconstruction_difference = abs(
        float(active_metrics["holding_return_compound_pct"])
        - float(metrics["total_return_pct"])
    )
    if reconstruction_difference > 1e-8:
        raise AssertionError(
            "持仓收益序列未能重建账户累计收益："
            f"差异 {reconstruction_difference:.3e} 个百分点。"
        )
    metrics["holding_return_reconstruction_difference_pct"] = reconstruction_difference
    return metrics


def run_single(
    data: pd.DataFrame,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    fast_window: int,
    slow_window: int,
    initial_cash: float = 100_000.0,
    cost_bps: float = 5.0,
) -> SingleRunResult:
    """Run one exact independent-ledger dual-SMA path and its benchmark."""

    spec = DualSmaStateSpec(int(fast_window), int(slow_window))
    frame, requested_start, requested_end, policy = _validate_inputs(
        data,
        start,
        end,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )
    analysis, positions = _analysis_rows(
        frame,
        requested_start,
        requested_end,
        maximum_window=spec.slow_window,
    )
    fast_sma = _rolling_sma(frame["close"].to_numpy(float), spec.fast_window)[positions]
    slow_sma = _rolling_sma(frame["close"].to_numpy(float), spec.slow_window)[positions]
    analysis[FAST_SMA_COLUMN] = fast_sma
    analysis[SLOW_SMA_COLUMN] = slow_sma
    strategy = run_reference_dual_sma(
        analysis,
        spec,
        policy,
        initial_cash=float(initial_cash),
    )
    benchmark = run_buy_and_hold_reference(
        analysis,
        policy,
        initial_cash=float(initial_cash),
    )
    benchmark.daily["executed"] = 0
    if len(benchmark.daily) > 1:
        benchmark.daily.loc[1, "executed"] = 1
    return SingleRunResult(
        symbol=str(frame.loc[0, "symbol"]),
        requested_start=requested_start,
        requested_end=requested_end,
        effective_start=pd.Timestamp(analysis.loc[0, "date"]),
        effective_end=pd.Timestamp(analysis.loc[len(analysis) - 1, "date"]),
        fast_window=spec.fast_window,
        slow_window=spec.slow_window,
        daily=strategy.daily,
        orders=strategy.orders,
        trades=strategy.trades,
        metrics=_summarize_reference(strategy, initial_cash=float(initial_cash)),
        benchmark_daily=benchmark.daily,
        benchmark_metrics=_summarize_reference(benchmark, initial_cash=float(initial_cash)),
    )


def _sharpe_from_sums(total: np.ndarray, square: np.ndarray, count: np.ndarray) -> np.ndarray:
    result = np.full(len(total), np.nan, dtype=float)
    valid = count >= 2
    variance = np.zeros(len(total), dtype=float)
    variance[valid] = (
        square[valid] - np.square(total[valid]) / count[valid]
    ) / (count[valid] - 1)
    valid &= variance > 0
    result[valid] = (
        total[valid] / count[valid] / np.sqrt(variance[valid]) * math.sqrt(ANNUAL_BARS)
    )
    return result


def _grid_chunk(
    opens: np.ndarray,
    closes: np.ndarray,
    dates: pd.Series,
    sma_by_window: dict[int, np.ndarray],
    pairs: Iterable[tuple[int, int]],
    *,
    initial_cash: float,
    cost_bps: float,
) -> pd.DataFrame:
    pair_list = tuple(pairs)
    case_count = len(pair_list)
    bar_count = len(opens)
    signals = np.empty((case_count, bar_count), dtype=bool)
    for index, (fast, slow) in enumerate(pair_list):
        signals[index] = sma_by_window[fast] > sma_by_window[slow]

    impact = float(cost_bps) / 10_000.0
    cash = np.full(case_count, float(initial_cash), dtype=float)
    shares = np.zeros(case_count, dtype=float)
    long = np.zeros(case_count, dtype=bool)
    previous_equity = np.full(case_count, float(initial_cash), dtype=float)
    peak = previous_equity.copy()
    maximum_drawdown = np.zeros(case_count, dtype=float)
    calendar_sum = np.zeros(case_count, dtype=float)
    calendar_square = np.zeros(case_count, dtype=float)
    active_sum = np.zeros(case_count, dtype=float)
    active_square = np.zeros(case_count, dtype=float)
    active_count = np.zeros(case_count, dtype=int)
    holding_count = np.zeros(case_count, dtype=int)
    order_count = np.zeros(case_count, dtype=int)
    sell_count = np.zeros(case_count, dtype=int)

    for bar in range(bar_count):
        desired = np.zeros(case_count, dtype=bool) if bar == 0 else signals[:, bar - 1]
        buy = desired & ~long
        sell = ~desired & long
        if buy.any():
            shares[buy] = cash[buy] / (opens[bar] * (1.0 + impact))
            cash[buy] = 0.0
        if sell.any():
            cash[sell] = shares[sell] * opens[bar] * (1.0 - impact)
            shares[sell] = 0.0
        long = desired
        equity = cash + shares * closes[bar]
        returns = equity / previous_equity - 1.0
        calendar_sum += returns
        calendar_square += np.square(returns)
        active = long | sell
        active_sum[active] += returns[active]
        active_square[active] += np.square(returns[active])
        active_count += active.astype(int)
        holding_count += long.astype(int)
        order_count += buy.astype(int) + sell.astype(int)
        sell_count += sell.astype(int)
        peak = np.maximum(peak, equity)
        maximum_drawdown = np.minimum(maximum_drawdown, equity / peak - 1.0)
        previous_equity = equity

    elapsed_days = max((pd.Timestamp(dates.iloc[-1]) - pd.Timestamp(dates.iloc[0])).days, 1)
    years = elapsed_days / 365.2425
    total_return = previous_equity / float(initial_cash) - 1.0
    cagr = np.power(previous_equity / float(initial_cash), 1.0 / years) - 1.0
    holding_cagr = np.full(case_count, np.nan, dtype=float)
    exposed = holding_count > 0
    holding_cagr[exposed] = (
        np.power(
            previous_equity[exposed] / float(initial_cash),
            ANNUAL_BARS / holding_count[exposed],
        )
        - 1.0
    )
    calendar_count = np.full(case_count, bar_count, dtype=int)
    calendar_sharpe = _sharpe_from_sums(
        calendar_sum, calendar_square, calendar_count
    )
    active_sharpe = _sharpe_from_sums(active_sum, active_square, active_count)
    return pd.DataFrame(
        {
            "fast_window": [pair[0] for pair in pair_list],
            "slow_window": [pair[1] for pair in pair_list],
            "final_equity": previous_equity,
            "total_return_pct": total_return * 100.0,
            "cagr_pct": cagr * 100.0,
            "sharpe": calendar_sharpe,
            "max_drawdown_pct": maximum_drawdown * 100.0,
            "holding_sessions": holding_count,
            "holding_time_pct": holding_count / bar_count * 100.0,
            "holding_period_cagr_pct": holding_cagr * 100.0,
            "holding_return_observations": active_count,
            "holding_return_sharpe": active_sharpe,
            "order_count": order_count,
            "closed_trade_count": sell_count,
        }
    )


def run_grid(
    data: pd.DataFrame,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    spec: GridSpec,
    initial_cash: float = 100_000.0,
    cost_bps: float = 5.0,
    chunk_size: int = 1_024,
) -> GridResult:
    """Evaluate a parameter plane with a chunked vectorized reference ledger."""

    if chunk_size < 1:
        raise ValueError("chunk_size 必须为正整数。")
    frame, requested_start, requested_end, policy = _validate_inputs(
        data,
        start,
        end,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )
    windows = sorted(set(spec.fast_values).union(spec.slow_values))
    maximum_window = max(windows)
    analysis, positions = _analysis_rows(
        frame,
        requested_start,
        requested_end,
        maximum_window=maximum_window,
    )
    closes_full = frame["close"].to_numpy(float)
    sma_by_window = {
        window: _rolling_sma(closes_full, window)[positions] for window in windows
    }
    opens = analysis["open"].to_numpy(float)
    closes = analysis["close"].to_numpy(float)
    chunks: list[pd.DataFrame] = []
    for offset in range(0, len(spec.pairs), int(chunk_size)):
        chunks.append(
            _grid_chunk(
                opens,
                closes,
                analysis["date"],
                sma_by_window,
                spec.pairs[offset : offset + int(chunk_size)],
                initial_cash=float(initial_cash),
                cost_bps=float(cost_bps),
            )
        )
    metrics = pd.concat(chunks, ignore_index=True)
    benchmark = run_buy_and_hold_reference(
        analysis,
        policy,
        initial_cash=float(initial_cash),
    )
    benchmark.daily["executed"] = 0
    benchmark.daily.loc[1, "executed"] = 1
    return GridResult(
        symbol=str(frame.loc[0, "symbol"]),
        requested_start=requested_start,
        requested_end=requested_end,
        effective_start=pd.Timestamp(analysis.loc[0, "date"]),
        effective_end=pd.Timestamp(analysis.loc[len(analysis) - 1, "date"]),
        spec=spec,
        metrics=metrics,
        benchmark_metrics=_summarize_reference(
            benchmark,
            initial_cash=float(initial_cash),
        ),
    )
