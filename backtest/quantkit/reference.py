"""Small independent ledger used to cross-check PyBroker results."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.sma_threshold import SMA_COLUMN, SmaThresholdSpec
from quantkit.stochrsi import STOCHRSI_COLUMN, StochRsiSpec


@dataclass
class ReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame


def run_reference_stochrsi(
    data: pd.DataFrame,
    spec: StochRsiSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    """Independent all-in/all-out ledger for StochRSI level conditions."""

    if len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Reference runner requires exactly one symbol")
    if data[STOCHRSI_COLUMN].isna().any():
        raise ValueError("Reference data contains incomplete StochRSI values")
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

        value = float(row[STOCHRSI_COLUMN])
        signal = 0
        if index < len(rows) - 1:
            if shares == 0.0 and value <= spec.buy_threshold:
                pending = ("buy", current_date)
                signal = 1
            elif shares > 0.0 and value >= spec.sell_threshold:
                pending = ("sell", current_date)
                signal = -1
        daily_records.append(
            {
                "date": current_date,
                "symbol": symbol,
                "stochrsi": value,
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


def run_reference_threshold(
    data: pd.DataFrame,
    spec: SmaThresholdSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    if len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Reference runner requires exactly one symbol.")
    if data[SMA_COLUMN].isna().any():
        raise ValueError("Reference data contains incomplete SMA values.")

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
            if side == "buy":
                shares = cash / fill
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
                proceeds = shares * fill
                cash = proceeds
                executed = -1
                if open_trade is None:
                    raise AssertionError("Sell executed without an open reference trade.")
                pnl = (fill - float(open_trade["entry_price"])) * shares
                trade_records.append(
                    {
                        **open_trade,
                        "exit_signal_date": signal_date,
                        "exit_date": current_date,
                        "exit_price": fill,
                        "pnl": pnl,
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
                    "shares": shares if side == "buy" else float(trade_records[-1]["shares"]),
                    "raw_price": raw_price,
                    "fill_price": fill,
                    "implicit_cost": abs(fill - raw_price)
                    * (shares if side == "buy" else float(trade_records[-1]["shares"])),
                }
            )
            pending = None

        equity = cash + shares * float(row["close"])
        signal = 0
        if index < len(rows) - 1:
            close = float(row["close"])
            sma = float(row[SMA_COLUMN])
            crossed_above = (
                index >= 1
                and float(rows.loc[index - 1, "close"])
                <= float(rows.loc[index - 1, SMA_COLUMN]) * spec.buy_multiplier
                and close > sma * spec.buy_multiplier
            )
            if shares == 0 and crossed_above:
                pending = ("buy", current_date)
                signal = 1
            elif shares > 0 and close < sma * spec.sell_multiplier:
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
                SMA_COLUMN: float(row[SMA_COLUMN]),
                "buy_threshold": float(row[SMA_COLUMN]) * spec.buy_multiplier,
                "sell_threshold": float(row[SMA_COLUMN]) * spec.sell_multiplier,
                "signal": signal,
                "executed": executed,
                "cash": cash,
                "shares": shares,
                "equity": equity,
                "is_long": int(shares > 0),
            }
        )

    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records),
        trades=pd.DataFrame(trade_records),
    )


def run_buy_and_hold_reference(
    data: pd.DataFrame,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    """Signals on the first analysis close and buys on the following bar."""

    rows = data.reset_index(drop=True)
    if len(rows) < 2:
        raise ValueError("Buy-and-hold requires at least two analysis bars.")
    cash = float(initial_cash)
    shares = 0.0
    symbol = str(rows.loc[0, "symbol"])
    order_records: list[dict[str, object]] = []
    daily_records: list[dict[str, object]] = []
    for index, row in rows.iterrows():
        executed = 0
        if index == 1:
            raw = float(row[policy.timing])
            fill = policy.expected_fill("buy", raw)
            shares = cash / fill
            cash = 0.0
            executed = 1
            order_records.append(
                {
                    "symbol": symbol,
                    "type": "buy",
                    "signal_date": pd.Timestamp(rows.loc[0, "date"]),
                    "date": pd.Timestamp(row["date"]),
                    "shares": shares,
                    "raw_price": raw,
                    "fill_price": fill,
                    "implicit_cost": abs(fill - raw) * shares,
                }
            )
        daily_records.append(
            {
                "date": pd.Timestamp(row["date"]),
                "symbol": symbol,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
            }
        )
    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records),
        trades=pd.DataFrame(),
    )
