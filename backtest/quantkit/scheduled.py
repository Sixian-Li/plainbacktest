"""Backtest an externally supplied all-in/all-out close execution schedule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Literal

import numpy as np
import pandas as pd
import pybroker
from pybroker import PriceType, Strategy
from pybroker.context import ExecContext

from quantkit.execution import (
    ExplicitFillPolicy,
    make_strategy_config,
    request_all_affordable_shares,
    request_sell_all,
)


@dataclass(frozen=True)
class ScheduledOrder:
    date: pd.Timestamp | str
    side: Literal["buy", "sell"]

    def __post_init__(self) -> None:
        timestamp = pd.Timestamp(self.date).normalize()
        if pd.isna(timestamp):
            raise ValueError("Scheduled order date must be valid.")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Unsupported scheduled side: {self.side!r}")
        object.__setattr__(self, "date", timestamp)


@dataclass
class ScheduledReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame


def _normalized_rows(data: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows["symbol"].nunique() != 1:
        raise ValueError("Scheduled runner requires exactly one symbol.")
    if rows.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed.")
    numeric = rows[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or (numeric <= 0).any():
        raise ValueError("Scheduled runner requires finite positive OHLC data.")
    return rows


def validate_schedule(
    data: pd.DataFrame,
    schedule: Iterable[ScheduledOrder],
    *,
    initial_date: pd.Timestamp | str,
    initial_position: Literal["long", "flat"] = "long",
) -> tuple[ScheduledOrder, ...]:
    rows = _normalized_rows(data)
    orders = tuple(schedule)
    if not orders:
        raise ValueError("Trade schedule cannot be empty.")
    initial = pd.Timestamp(initial_date).normalize()
    available = set(rows["date"])
    if initial not in available:
        raise ValueError(f"Initial date is not a trading date in the data: {initial.date()}")
    missing = [order.date.date().isoformat() for order in orders if order.date not in available]
    if missing:
        raise ValueError(f"Scheduled dates are not trading dates in the data: {missing}")
    dates = [order.date for order in orders]
    if any(current <= previous for previous, current in zip(dates, dates[1:])):
        raise ValueError("Scheduled dates must be unique and strictly increasing.")
    if dates[0] <= initial:
        raise ValueError("The first scheduled order must occur after the initial position date.")
    expected = "sell" if initial_position == "long" else "buy"
    for order in orders:
        if order.side != expected:
            raise ValueError("Scheduled buy and sell sides must alternate from the initial position.")
        expected = "buy" if expected == "sell" else "sell"
    return orders


def _signal_schedule(
    rows: pd.DataFrame,
    schedule: tuple[ScheduledOrder, ...],
    initial_date: pd.Timestamp,
) -> tuple[dict[pd.Timestamp, tuple[str, pd.Timestamp]], pd.Timestamp]:
    dates = rows["date"].tolist()
    positions = {date: index for index, date in enumerate(dates)}
    initial_index = positions[initial_date]
    if initial_index == 0:
        raise ValueError("PyBroker schedule data must include one trading bar before initial_date.")
    seed_signal_date = pd.Timestamp(dates[initial_index - 1])
    signals: dict[pd.Timestamp, tuple[str, pd.Timestamp]] = {
        seed_signal_date: ("seed", initial_date)
    }
    for order in schedule:
        order_index = positions[pd.Timestamp(order.date)]
        if order_index == 0:
            raise ValueError(f"No prior bar is available to submit {order.date.date()} order.")
        signal_date = pd.Timestamp(dates[order_index - 1])
        if signal_date in signals:
            raise ValueError(f"Multiple scheduled instructions share signal date {signal_date.date()}.")
        signals[signal_date] = (order.side, pd.Timestamp(order.date))
    return signals, seed_signal_date


def run_pybroker_schedule(
    data: pd.DataFrame,
    schedule: Iterable[ScheduledOrder],
    *,
    initial_date: pd.Timestamp | str,
    initial_shares: float,
    policy: ExplicitFillPolicy,
):
    """Run a predeclared schedule; listed dates are fills, not signal dates.

    PyBroker begins with cash one bar earlier and buys the supplied initial
    shares at the initial close without cost. That seed is bookkeeping only:
    the reported analysis begins after the user-supplied position already
    exists.
    """

    if not np.isfinite(initial_shares) or initial_shares <= 0:
        raise ValueError("initial_shares must be finite and positive.")
    if policy.timing != "close":
        raise ValueError("Manual schedule currently requires explicit close fills.")
    rows = _normalized_rows(data)
    initial = pd.Timestamp(initial_date).normalize()
    orders = validate_schedule(rows, schedule, initial_date=initial, initial_position="long")
    signals, _ = _signal_schedule(rows, orders, initial)
    initial_close = float(rows.loc[rows["date"] == initial, "close"].iloc[0])
    initial_equity = initial_shares * initial_close

    def execute(ctx: ExecContext) -> None:
        instruction = signals.get(pd.Timestamp(ctx.dt).normalize())
        if instruction is None:
            return
        side, _fill_date = instruction
        if side == "seed":
            if ctx.long_pos() is not None:
                raise ValueError("Initial seed attempted while already long.")
            ctx.buy_shares = Decimal(str(initial_shares))
            ctx.buy_fill_price = PriceType.CLOSE
        elif side == "buy":
            request_all_affordable_shares(ctx, policy)
        else:
            request_sell_all(ctx, policy)

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = make_strategy_config(policy, initial_cash=initial_equity)
    strategy = Strategy(
        rows,
        pd.Timestamp(rows["date"].min()).date().isoformat(),
        pd.Timestamp(rows["date"].max()).date().isoformat(),
        config,
    )
    strategy.add_execution(execute, str(rows.iloc[0]["symbol"]))
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)

    expected = [(initial, "buy", initial_close)] + [
        (
            pd.Timestamp(order.date),
            order.side,
            policy.expected_fill(
                order.side,
                float(rows.loc[rows["date"] == order.date, "close"].iloc[0]),
            ),
        )
        for order in orders
    ]
    actual = result.orders.reset_index(drop=True)
    if len(actual) != len(expected):
        raise AssertionError(f"Scheduled order count mismatch: {len(actual)} != {len(expected)}")
    for row, (date, side, fill) in zip(actual.itertuples(index=False), expected, strict=True):
        if pd.Timestamp(row.date).normalize() != date or row.type != side:
            raise AssertionError("PyBroker did not execute the predeclared date/side sequence.")
        if not np.isclose(float(row.fill_price), fill, rtol=0.0, atol=1e-9):
            raise AssertionError(
                f"Scheduled {side} on {date.date()} filled at {row.fill_price}; expected {fill}."
            )
    return result


def run_reference_schedule(
    data: pd.DataFrame,
    schedule: Iterable[ScheduledOrder],
    *,
    initial_date: pd.Timestamp | str,
    initial_shares: float,
    policy: ExplicitFillPolicy,
    seed_signal_date: pd.Timestamp | str,
) -> ScheduledReferenceResult:
    """Independent cash/share ledger for the same predeclared schedule."""

    if policy.timing != "close":
        raise ValueError("Manual schedule currently requires explicit close fills.")
    rows = _normalized_rows(data)
    initial = pd.Timestamp(initial_date).normalize()
    orders = validate_schedule(rows, schedule, initial_date=initial, initial_position="long")
    if pd.Timestamp(rows.iloc[0]["date"]) != initial:
        raise ValueError("Reference data must begin on initial_date.")
    symbol = str(rows.iloc[0]["symbol"])
    initial_close = float(rows.iloc[0]["close"])
    cash = 0.0
    shares = float(initial_shares)
    schedule_by_date = {pd.Timestamp(order.date): order.side for order in orders}
    order_records: list[dict[str, object]] = [
        {
            "symbol": symbol,
            "type": "buy",
            "signal_date": pd.Timestamp(seed_signal_date).normalize(),
            "date": initial,
            "shares": shares,
            "raw_price": initial_close,
            "fill_price": initial_close,
            "implicit_cost": 0.0,
            "is_initial_seed": True,
        }
    ]
    trade_records: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = {
        "symbol": symbol,
        "entry_signal_date": pd.Timestamp(seed_signal_date).normalize(),
        "entry_date": initial,
        "entry_price": initial_close,
        "shares": shares,
        "is_initial_position": True,
    }
    daily_records: list[dict[str, object]] = []
    previous_date = pd.Timestamp(seed_signal_date).normalize()

    for row in rows.itertuples(index=False):
        current_date = pd.Timestamp(row.date).normalize()
        side = schedule_by_date.get(current_date)
        executed = 0
        if side is not None:
            raw_price = float(row.close)
            fill = policy.expected_fill(side, raw_price)
            if side == "sell":
                if shares <= 0 or open_trade is None:
                    raise AssertionError("Reference sell executed while flat.")
                order_shares = shares
                cash = order_shares * fill
                pnl = (fill - float(open_trade["entry_price"])) * order_shares
                trade_records.append(
                    {
                        **open_trade,
                        "exit_signal_date": previous_date,
                        "exit_date": current_date,
                        "exit_price": fill,
                        "pnl": pnl,
                        "return_pct": (fill / float(open_trade["entry_price"]) - 1.0) * 100.0,
                    }
                )
                shares = 0.0
                open_trade = None
                executed = -1
            else:
                if shares > 0:
                    raise AssertionError("Reference buy executed while already long.")
                shares = cash / fill
                cash = 0.0
                order_shares = shares
                open_trade = {
                    "symbol": symbol,
                    "entry_signal_date": previous_date,
                    "entry_date": current_date,
                    "entry_price": fill,
                    "shares": shares,
                    "is_initial_position": False,
                }
                executed = 1
            order_records.append(
                {
                    "symbol": symbol,
                    "type": side,
                    "signal_date": previous_date,
                    "date": current_date,
                    "shares": order_shares,
                    "raw_price": raw_price,
                    "fill_price": fill,
                    "implicit_cost": abs(fill - raw_price) * order_shares,
                    "is_initial_seed": False,
                }
            )

        daily_records.append(
            {
                "date": current_date,
                "symbol": symbol,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "executed": executed,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row.close),
                "is_long": int(shares > 0),
            }
        )
        previous_date = current_date

    return ScheduledReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records),
        trades=pd.DataFrame(trade_records),
    )
