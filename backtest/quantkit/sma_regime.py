"""Close-confirmed SMA regime strategy and an independent reference ledger."""

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


CONDITION_PRICE_ABOVE_SMA200 = "price_above_sma200"
CONDITION_ALL_SMAS_RISING_3D = "all_smas_rising_3d"
CONDITION_SMA_ORDERING = "sma200_above_sma250_above_sma300"
ALL_CONDITIONS = (
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_ALL_SMAS_RISING_3D,
    CONDITION_SMA_ORDERING,
)
CASE_CONDITIONS: dict[str, tuple[str, ...]] = {
    "all_conditions": ALL_CONDITIONS,
    "without_condition_1": (
        CONDITION_ALL_SMAS_RISING_3D,
        CONDITION_SMA_ORDERING,
    ),
    "without_condition_2": (
        CONDITION_PRICE_ABOVE_SMA200,
        CONDITION_SMA_ORDERING,
    ),
    "without_condition_3": (
        CONDITION_PRICE_ABOVE_SMA200,
        CONDITION_ALL_SMAS_RISING_3D,
    ),
}


@dataclass(frozen=True)
class SmaRegimeSpec:
    sma_windows: tuple[int, ...] = (30, 200, 250, 300)
    price_sma_window: int = 200
    ordering_windows: tuple[int, int, int] = (200, 250, 300)
    rising_sessions: int = 3

    def __post_init__(self) -> None:
        if len(set(self.sma_windows)) != len(self.sma_windows):
            raise ValueError("SMA windows must be unique.")
        if any(window < 2 for window in self.sma_windows):
            raise ValueError("Every SMA window must be at least 2.")
        required = {self.price_sma_window, *self.ordering_windows}
        if not required.issubset(self.sma_windows):
            raise ValueError("Price and ordering windows must be present in sma_windows.")
        if len(set(self.ordering_windows)) != 3:
            raise ValueError("Ordering requires three distinct windows.")
        if self.rising_sessions < 1:
            raise ValueError("rising_sessions must be positive.")

    @classmethod
    def from_parameters(cls, parameters: dict[str, object]) -> "SmaRegimeSpec":
        return cls(
            sma_windows=tuple(int(value) for value in parameters["sma_windows"]),  # type: ignore[index]
            price_sma_window=int(parameters["price_sma_window"]),
            ordering_windows=tuple(  # type: ignore[arg-type]
                int(value) for value in parameters["ordering_windows"]  # type: ignore[index]
            ),
            rising_sessions=int(parameters["rising_sessions"]),
        )

    @property
    def sma_columns(self) -> tuple[str, ...]:
        return tuple(f"sma{window}" for window in self.sma_windows)


@dataclass
class RegimeReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame


def validate_case_id(case_id: str) -> None:
    if case_id not in CASE_CONDITIONS:
        raise ValueError(f"Unknown regime case: {case_id!r}")


def case_is_eligible(row: pd.Series, case_id: str) -> bool:
    validate_case_id(case_id)
    return all(bool(row[condition]) for condition in CASE_CONDITIONS[case_id])


def prepare_sma_regime_data(data: pd.DataFrame, spec: SmaRegimeSpec) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    result = data.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if result.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed.")

    grouped = result.groupby("symbol", sort=False)["close"]
    for window in spec.sma_windows:
        column = f"sma{window}"
        result[column] = grouped.transform(
            lambda values, size=window: values.rolling(size, min_periods=size).mean()
        )

    rising_flags: list[pd.Series] = []
    for window in spec.sma_windows:
        column = f"sma{window}"
        daily_increase = result.groupby("symbol", sort=False)[column].diff().gt(0)
        rising = daily_increase.groupby(result["symbol"], sort=False).transform(
            lambda values: values.rolling(
                spec.rising_sessions, min_periods=spec.rising_sessions
            ).sum()
        ).eq(spec.rising_sessions)
        rising_flags.append(rising)

    short, middle, long = spec.ordering_windows
    result[CONDITION_PRICE_ABOVE_SMA200] = (
        result["close"] > result[f"sma{spec.price_sma_window}"]
    )
    result[CONDITION_ALL_SMAS_RISING_3D] = np.logical_and.reduce(rising_flags)
    result[CONDITION_SMA_ORDERING] = (
        (result[f"sma{short}"] > result[f"sma{middle}"])
        & (result[f"sma{middle}"] > result[f"sma{long}"])
    )

    # Every ablation uses the same warmup boundary so the four equity curves
    # have an identical observation period. Three rising sessions means three
    # positive first differences, requiring t, t-1, t-2 and t-3 SMA values.
    result["regime_ready"] = result[list(spec.sma_columns)].notna().all(axis=1)
    for window in spec.sma_windows:
        result["regime_ready"] &= (
            result.groupby("symbol", sort=False)[f"sma{window}"].shift(spec.rising_sessions).notna()
        )
    return result


def analysis_slice(data: pd.DataFrame) -> pd.DataFrame:
    ready = data[data["regime_ready"].astype(bool)].copy()
    if ready.empty:
        raise ValueError("No rows have the shared SMA regime warmup.")
    return ready.reset_index(drop=True)


def make_execution(
    case_id: str,
    policy: ExplicitFillPolicy,
) -> Callable[[ExecContext], None]:
    validate_case_id(case_id)

    def execute(ctx: ExecContext) -> None:
        eligible = all(bool(getattr(ctx, condition)[-1]) for condition in CASE_CONDITIONS[case_id])
        if ctx.long_pos() is None and eligible:
            request_all_affordable_shares(ctx, policy)
        elif ctx.long_pos() is not None and not eligible:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_sma_regime(
    data: pd.DataFrame,
    case_id: str,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
):
    validate_case_id(case_id)
    symbols = data["symbol"].drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("SMA regime runner requires exactly one symbol.")
    if not data["regime_ready"].astype(bool).all():
        raise ValueError("Pass analysis_slice(data) so every row shares the full warmup.")

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns(*ALL_CONDITIONS)
    config = make_strategy_config(policy, initial_cash=initial_cash)
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(case_id, policy), symbols[0])
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result


def run_reference_sma_regime(
    data: pd.DataFrame,
    case_id: str,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
) -> RegimeReferenceResult:
    validate_case_id(case_id)
    if len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Reference SMA regime runner requires exactly one symbol.")
    if not data["regime_ready"].astype(bool).all():
        raise ValueError("Reference data contains rows before the shared warmup.")

    rows = data.reset_index(drop=True)
    symbol = str(rows.loc[0, "symbol"])
    cash = float(initial_cash)
    shares = 0.0
    pending: tuple[str, pd.Timestamp, str] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = None

    for index, row in rows.iterrows():
        date = pd.Timestamp(row["date"])
        executed = 0
        if pending is not None:
            side, signal_date, reason = pending
            raw_price = float(row[policy.timing])
            fill_price = policy.expected_fill(side, raw_price)
            if side == "buy":
                order_shares = cash / fill_price
                shares = order_shares
                cash = 0.0
                executed = 1
                open_trade = {
                    "case_id": case_id,
                    "symbol": symbol,
                    "entry_signal_date": signal_date,
                    "entry_date": date,
                    "entry_price": fill_price,
                    "shares": shares,
                }
            else:
                order_shares = shares
                cash = shares * fill_price
                executed = -1
                if open_trade is None:
                    raise AssertionError("Sell executed without an open reference trade.")
                trade_records.append(
                    {
                        **open_trade,
                        "exit_signal_date": signal_date,
                        "exit_date": date,
                        "exit_price": fill_price,
                        "pnl": (fill_price - float(open_trade["entry_price"])) * shares,
                        "return_pct": (
                            fill_price / float(open_trade["entry_price"]) - 1.0
                        )
                        * 100.0,
                    }
                )
                shares = 0.0
                open_trade = None
            order_records.append(
                {
                    "case_id": case_id,
                    "symbol": symbol,
                    "type": side,
                    "signal_date": signal_date,
                    "date": date,
                    "reason": reason,
                    "shares": order_shares,
                    "raw_price": raw_price,
                    "fill_price": fill_price,
                    "implicit_cost": abs(fill_price - raw_price) * order_shares,
                }
            )
            pending = None

        eligible = case_is_eligible(row, case_id)
        signal = 0
        signal_reason = ""
        if index < len(rows) - 1:
            if shares == 0 and eligible:
                pending = ("buy", date, "ALL_ACTIVE_CONDITIONS_TRUE")
                signal = 1
                signal_reason = pending[2]
            elif shares > 0 and not eligible:
                failed = [condition for condition in CASE_CONDITIONS[case_id] if not bool(row[condition])]
                signal_reason = "FAILED:" + "|".join(failed)
                pending = ("sell", date, signal_reason)
                signal = -1

        record: dict[str, object] = {
            "case_id": case_id,
            "date": date,
            "symbol": symbol,
            "close": float(row["close"]),
            "eligible": int(eligible),
            "signal": signal,
            "signal_reason": signal_reason,
            "executed": executed,
            "cash": cash,
            "shares": shares,
            "equity": cash + shares * float(row["close"]),
            "is_long": int(shares > 0),
        }
        for condition in ALL_CONDITIONS:
            record[condition] = int(bool(row[condition]))
        daily_records.append(record)

    order_columns = [
        "case_id", "symbol", "type", "signal_date", "date", "reason",
        "shares", "raw_price", "fill_price", "implicit_cost",
    ]
    trade_columns = [
        "case_id", "symbol", "entry_signal_date", "entry_date", "entry_price",
        "shares", "exit_signal_date", "exit_date", "exit_price", "pnl", "return_pct",
    ]
    return RegimeReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
    )
