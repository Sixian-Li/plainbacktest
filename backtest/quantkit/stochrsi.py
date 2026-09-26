"""Raw Stochastic RSI indicator and explicit next-open PyBroker adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd
import pybroker
from pybroker import Strategy
from pybroker.context import ExecContext

from quantkit.execution import (
    ExplicitFillPolicy,
    assert_orders_match_policy,
    make_strategy_config,
    request_all_affordable_shares,
    request_sell_all,
)


RSI_COLUMN = "rsi"
STOCHRSI_COLUMN = "stochrsi"


@dataclass(frozen=True)
class StochRsiSpec:
    period: int
    buy_threshold: float
    sell_threshold: float

    def __post_init__(self) -> None:
        if self.period < 2:
            raise ValueError("period must be at least 2")
        for name, value in (
            ("buy_threshold", self.buy_threshold),
            ("sell_threshold", self.sell_threshold),
        ):
            if not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and within [0, 1]")
        if self.buy_threshold >= self.sell_threshold:
            raise ValueError("buy_threshold must be below sell_threshold")


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Return Wilder RSI with the first value after exactly ``period`` deltas."""

    values = pd.Series(close, dtype=float).reset_index(drop=True)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(result, index=close.index, dtype=float)

    delta = values.diff().to_numpy(dtype=float)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(np.mean(gains[1 : period + 1]))
    average_loss = float(np.mean(losses[1 : period + 1]))

    def value(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 50.0 if gain == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index] = value(average_gain, average_loss)
    return pd.Series(result, index=close.index, dtype=float)


def prepare_stochrsi_data(data: pd.DataFrame, period: int) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    if period < 2:
        raise ValueError("period must be at least 2")
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if result.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed")

    rsi_parts: list[pd.Series] = []
    for _, group in result.groupby("symbol", sort=False):
        rsi_parts.append(wilder_rsi(group["close"], period))
    result[RSI_COLUMN] = pd.concat(rsi_parts).sort_index()
    low = result.groupby("symbol", sort=False)[RSI_COLUMN].transform(
        lambda values: values.rolling(period, min_periods=period).min()
    )
    high = result.groupby("symbol", sort=False)[RSI_COLUMN].transform(
        lambda values: values.rolling(period, min_periods=period).max()
    )
    span = high - low
    result[STOCHRSI_COLUMN] = (result[RSI_COLUMN] - low) / span
    result.loc[span.eq(0.0) & low.notna(), STOCHRSI_COLUMN] = 0.5
    return result


def make_execution(
    spec: StochRsiSpec,
    policy: ExplicitFillPolicy,
) -> Callable[[ExecContext], None]:
    def execute(ctx: ExecContext) -> None:
        values = getattr(ctx, STOCHRSI_COLUMN)
        if values is None or not len(values) or not np.isfinite(values[-1]):
            return
        current = float(values[-1])
        if ctx.long_pos() is None and current <= spec.buy_threshold:
            request_all_affordable_shares(ctx, policy)
        elif ctx.long_pos() is not None and current >= spec.sell_threshold:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_stochrsi(
    data: pd.DataFrame,
    spec: StochRsiSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
):
    symbols = data["symbol"].drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("StochRSI runner requires exactly one symbol")
    if data[STOCHRSI_COLUMN].isna().any():
        raise ValueError("Analysis data contains incomplete StochRSI values")

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns(RSI_COLUMN, STOCHRSI_COLUMN)
    config = make_strategy_config(policy, initial_cash=initial_cash)
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(spec, policy), symbols[0])
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result
