"""PyBroker adapter for the asymmetric SMA threshold strategy."""

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


SMA_COLUMN = "sma200"


@dataclass(frozen=True)
class SmaThresholdSpec:
    a_pct: float
    b_pct: float
    window: int = 200

    def __post_init__(self) -> None:
        for name, value in (("a_pct", self.a_pct), ("b_pct", self.b_pct)):
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.sell_multiplier <= 0:
            raise ValueError("a_pct must keep the sell threshold multiplier positive.")
        if self.buy_multiplier <= 0:
            raise ValueError("b_pct must keep the buy threshold multiplier positive.")
        if self.window < 2:
            raise ValueError("window must be at least 2.")

    @property
    def sell_multiplier(self) -> float:
        return 1.0 - self.a_pct / 100.0

    @property
    def buy_multiplier(self) -> float:
        return 1.0 + self.b_pct / 100.0


def prepare_sma_data(data: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if result.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed.")
    result[SMA_COLUMN] = result.groupby("symbol", sort=False)["close"].transform(
        lambda values: values.rolling(window=window, min_periods=window).mean()
    )
    return result


def analysis_slice(data: pd.DataFrame, window: int) -> pd.DataFrame:
    ready = data[data[SMA_COLUMN].notna()].copy()
    if ready.empty:
        raise ValueError(f"No rows have a complete SMA{window} warmup.")
    return ready.reset_index(drop=True)


def make_execution(
    spec: SmaThresholdSpec,
    policy: ExplicitFillPolicy,
) -> Callable[[ExecContext], None]:
    def execute(ctx: ExecContext) -> None:
        sma_values = getattr(ctx, SMA_COLUMN)
        if sma_values is None or not len(sma_values) or not np.isfinite(sma_values[-1]):
            return
        close = float(ctx.close[-1])
        sma = float(sma_values[-1])
        if ctx.long_pos() is None:
            # Entry is a crossing event, not a state check. The first bar with
            # a valid SMA can establish the prior state but can never buy by
            # itself. This avoids entering merely because price was already
            # above the threshold when the warmup ended.
            if (
                len(sma_values) >= 2
                and len(ctx.close) >= 2
                and np.isfinite(sma_values[-2])
                and float(ctx.close[-2]) <= float(sma_values[-2]) * spec.buy_multiplier
                and close > sma * spec.buy_multiplier
            ):
                request_all_affordable_shares(ctx, policy)
        elif close < sma * spec.sell_multiplier:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_threshold(
    data: pd.DataFrame,
    spec: SmaThresholdSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
    return_signals: bool = False,
):
    symbols = data["symbol"].drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("Threshold runner requires exactly one symbol.")
    if data[SMA_COLUMN].isna().any():
        raise ValueError("Pass analysis_slice(data) so every row has a complete SMA.")

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns(SMA_COLUMN)
    config = make_strategy_config(
        policy, initial_cash=initial_cash, return_signals=return_signals
    )
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(spec, policy), symbols[0])
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result
