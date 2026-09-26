"""Close-confirmed dual-SMA state strategy with an independent ledger."""

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
from quantkit.reference import ReferenceResult


FAST_SMA_COLUMN = "fast_sma"
SLOW_SMA_COLUMN = "slow_sma"


@dataclass(frozen=True)
class DualSmaStateSpec:
    fast_window: int
    slow_window: int

    def __post_init__(self) -> None:
        if self.fast_window < 1:
            raise ValueError("fast_window must be positive")
        if self.slow_window < 2:
            raise ValueError("slow_window must be at least 2")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be strictly shorter than slow_window")


def valid_parameter_pairs(
    fast_start: int,
    fast_end: int,
    slow_start: int,
    slow_end: int,
) -> list[tuple[int, int]]:
    if min(fast_start, slow_start) < 1 or fast_end < fast_start or slow_end < slow_start:
        raise ValueError("Invalid dual-SMA grid bounds")
    return [
        (fast, slow)
        for fast in range(fast_start, fast_end + 1)
        for slow in range(slow_start, slow_end + 1)
        if fast < slow
    ]


def prepare_dual_sma_data(data: pd.DataFrame, spec: DualSmaStateSpec) -> pd.DataFrame:
    """Calculate both SMAs over all supplied history before any date slice."""

    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if result.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed")
    grouped = result.groupby("symbol", sort=False)["close"]
    result[FAST_SMA_COLUMN] = grouped.transform(
        lambda values: values.rolling(spec.fast_window, min_periods=spec.fast_window).mean()
    )
    result[SLOW_SMA_COLUMN] = grouped.transform(
        lambda values: values.rolling(spec.slow_window, min_periods=spec.slow_window).mean()
    )
    return result


def attach_precomputed_smas(
    data: pd.DataFrame,
    fast_sma: np.ndarray,
    slow_sma: np.ndarray,
) -> pd.DataFrame:
    if len(data) != len(fast_sma) or len(data) != len(slow_sma):
        raise ValueError("Precomputed SMA arrays must align one-to-one with price rows")
    result = data.copy()
    result[FAST_SMA_COLUMN] = np.asarray(fast_sma, dtype=float)
    result[SLOW_SMA_COLUMN] = np.asarray(slow_sma, dtype=float)
    return result


def analysis_slice(data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    result = data[data["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    if result.empty:
        raise ValueError("Analysis window contains no price rows")
    if result[[FAST_SMA_COLUMN, SLOW_SMA_COLUMN]].isna().any().any():
        raise ValueError("Analysis window starts before both SMAs are fully warmed")
    return result.reset_index(drop=True)


def make_execution(
    _spec: DualSmaStateSpec,
    policy: ExplicitFillPolicy,
) -> Callable[[ExecContext], None]:
    def execute(ctx: ExecContext) -> None:
        fast = getattr(ctx, FAST_SMA_COLUMN)
        slow = getattr(ctx, SLOW_SMA_COLUMN)
        if fast is None or slow is None or not len(fast) or not len(slow):
            return
        if not np.isfinite(fast[-1]) or not np.isfinite(slow[-1]):
            return
        should_hold = float(fast[-1]) > float(slow[-1])
        if should_hold and ctx.long_pos() is None:
            request_all_affordable_shares(ctx, policy)
        elif not should_hold and ctx.long_pos() is not None:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_dual_sma(
    data: pd.DataFrame,
    spec: DualSmaStateSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
):
    symbols = data["symbol"].drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("Dual-SMA runner requires exactly one symbol")
    if data[[FAST_SMA_COLUMN, SLOW_SMA_COLUMN]].isna().any().any():
        raise ValueError("Dual-SMA runner requires fully warmed indicators")
    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns(FAST_SMA_COLUMN, SLOW_SMA_COLUMN)
    config = make_strategy_config(policy, initial_cash=initial_cash)
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(spec, policy), symbols[0])
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result


def run_reference_dual_sma(
    data: pd.DataFrame,
    spec: DualSmaStateSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    """Independent all-in/all-out ledger for the dual-SMA level condition."""

    del spec  # The prepared indicator columns are the independent ledger input.
    if len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Reference runner requires exactly one symbol")
    if data[[FAST_SMA_COLUMN, SLOW_SMA_COLUMN]].isna().any().any():
        raise ValueError("Reference data contains incomplete moving averages")
    rows = data.reset_index(drop=True)
    symbol = str(rows.loc[0, "symbol"])
    cash = float(initial_cash)
    shares = 0.0
    pending: tuple[str, pd.Timestamp] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = None

    for index, row in rows.iterrows():
        current_date = pd.Timestamp(row["date"])
        executed = 0
        if pending is not None:
            side, signal_date = pending
            raw_price = float(row[policy.timing])
            fill = policy.expected_fill(side, raw_price)
            executed_shares = shares
            if side == "buy":
                shares = cash / fill
                executed_shares = shares
                cash = 0.0
                executed = 1
                open_trade = {
                    "symbol": symbol,
                    "entry_signal_date": signal_date,
                    "entry_date": current_date,
                    "entry_price": fill,
                    "shares": shares,
                }
            else:
                cash = shares * fill
                executed = -1
                if open_trade is None:
                    raise AssertionError("Sell executed without an open reference trade")
                trade_records.append(
                    {
                        **open_trade,
                        "exit_signal_date": signal_date,
                        "exit_date": current_date,
                        "exit_price": fill,
                        "pnl": (fill - float(open_trade["entry_price"])) * shares,
                        "return_pct": (fill / float(open_trade["entry_price"]) - 1.0) * 100.0,
                    }
                )
                shares = 0.0
                open_trade = None
            order_records.append(
                {
                    "symbol": symbol,
                    "type": side,
                    "signal_date": signal_date,
                    "date": current_date,
                    "shares": executed_shares,
                    "raw_price": raw_price,
                    "fill_price": fill,
                    "implicit_cost": abs(fill - raw_price) * executed_shares,
                }
            )
            pending = None

        should_hold = float(row[FAST_SMA_COLUMN]) > float(row[SLOW_SMA_COLUMN])
        signal = 0
        if index < len(rows) - 1:
            if should_hold and shares == 0.0:
                pending = ("buy", current_date)
                signal = 1
            elif not should_hold and shares > 0.0:
                pending = ("sell", current_date)
                signal = -1
        daily_records.append(
            {
                "date": current_date,
                "symbol": symbol,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                FAST_SMA_COLUMN: float(row[FAST_SMA_COLUMN]),
                SLOW_SMA_COLUMN: float(row[SLOW_SMA_COLUMN]),
                "signal": signal,
                "executed": executed,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0.0),
            }
        )
    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records),
        trades=pd.DataFrame(trade_records),
    )
