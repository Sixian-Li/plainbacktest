"""Peak-to-close drawdown overlay for the two-condition SMA regime."""

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
from quantkit.sma_regime import (
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_SMA_ORDERING,
    RegimeReferenceResult,
    SmaRegimeSpec,
    prepare_sma_regime_data,
)


BASE_CONDITIONS = (
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_SMA_ORDERING,
)
BASE_EXIT_REASON = "FAILED_BASE_CONDITION"
STOP_EXIT_REASON = "PEAK_CLOSE_DRAWDOWN_STOP"
ENTRY_REASON = "BOTH_BASE_CONDITIONS_TRUE"


@dataclass(frozen=True)
class PeakDrawdownSpec:
    """Optional strict peak-to-completed-Close drawdown exit threshold."""

    stop_pct: float | None = None

    def __post_init__(self) -> None:
        if self.stop_pct is None:
            return
        if not np.isfinite(self.stop_pct) or not 0 < self.stop_pct < 100:
            raise ValueError("stop_pct must be finite and strictly between 0 and 100.")

    @property
    def case_suffix(self) -> str:
        return "base" if self.stop_pct is None else f"stop_{self.stop_pct:g}pct"


def base_is_eligible(row: pd.Series) -> bool:
    return all(bool(row[condition]) for condition in BASE_CONDITIONS)


def prepare_peak_drawdown_data(data: pd.DataFrame, spec: SmaRegimeSpec) -> pd.DataFrame:
    """Prepare SMA fields without retaining condition 2's extra three-day warmup."""
    result = prepare_sma_regime_data(data, spec)
    result["regime_ready"] = result[list(spec.sma_columns)].notna().all(axis=1)
    return result


def _position_entry_price(position) -> float:
    if not position.entries:
        raise AssertionError("Long position has no entry price.")
    return float(position.entries[0].price)


def make_execution(
    spec: PeakDrawdownSpec,
    policy: ExplicitFillPolicy,
) -> Callable[[ExecContext], None]:
    peak_close_or_entry: float | None = None

    def execute(ctx: ExecContext) -> None:
        nonlocal peak_close_or_entry
        position = ctx.long_pos()
        eligible = all(bool(getattr(ctx, condition)[-1]) for condition in BASE_CONDITIONS)
        close = float(ctx.close[-1])

        if position is None:
            peak_close_or_entry = None
            if eligible:
                request_all_affordable_shares(ctx, policy)
            return

        if peak_close_or_entry is None:
            peak_close_or_entry = max(_position_entry_price(position), close)
        else:
            peak_close_or_entry = max(peak_close_or_entry, close)
        drawdown_pct = (peak_close_or_entry - close) / peak_close_or_entry * 100.0
        stop_triggered = spec.stop_pct is not None and drawdown_pct > spec.stop_pct
        if not eligible or stop_triggered:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_peak_drawdown(
    data: pd.DataFrame,
    spec: PeakDrawdownSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
):
    symbols = data["symbol"].drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("Peak drawdown runner requires exactly one symbol.")
    if not data["regime_ready"].astype(bool).all():
        raise ValueError("Pass shared warmup-complete data to the runner.")

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns(*BASE_CONDITIONS)
    config = make_strategy_config(policy, initial_cash=initial_cash)
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(spec, policy), symbols[0])
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result


def run_reference_peak_drawdown(
    data: pd.DataFrame,
    spec: PeakDrawdownSpec,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
    case_id: str | None = None,
) -> RegimeReferenceResult:
    if len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Reference peak drawdown runner requires exactly one symbol.")
    if not data["regime_ready"].astype(bool).all():
        raise ValueError("Reference data contains rows before the shared warmup.")

    rows = data.reset_index(drop=True)
    symbol = str(rows.loc[0, "symbol"])
    case_id = case_id or spec.case_suffix
    cash = float(initial_cash)
    shares = 0.0
    peak_close_or_entry: float | None = None
    pending: tuple[str, pd.Timestamp, str, float | None, float | None] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = None

    for index, row in rows.iterrows():
        date = pd.Timestamp(row["date"])
        executed = 0
        executed_reason = ""
        if pending is not None:
            side, signal_date, reason, signal_peak, signal_drawdown = pending
            raw_price = float(row[policy.timing])
            fill_price = policy.expected_fill(side, raw_price)
            if side == "buy":
                order_shares = cash / fill_price
                shares = order_shares
                cash = 0.0
                peak_close_or_entry = fill_price
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
                        "exit_reason": reason,
                        "pnl": (fill_price - float(open_trade["entry_price"])) * shares,
                        "return_pct": (
                            fill_price / float(open_trade["entry_price"]) - 1.0
                        )
                        * 100.0,
                    }
                )
                shares = 0.0
                peak_close_or_entry = None
                open_trade = None
            executed_reason = reason
            order_records.append(
                {
                    "case_id": case_id,
                    "symbol": symbol,
                    "type": side,
                    "signal_date": signal_date,
                    "date": date,
                    "reason": reason,
                    "signal_peak_close_or_entry": signal_peak,
                    "signal_drawdown_pct": signal_drawdown,
                    "shares": order_shares,
                    "raw_price": raw_price,
                    "fill_price": fill_price,
                    "implicit_cost": abs(fill_price - raw_price) * order_shares,
                }
            )
            pending = None

        close = float(row["close"])
        eligible = base_is_eligible(row)
        signal = 0
        signal_reason = ""
        drawdown_pct: float | None = None
        stop_triggered = False
        if shares > 0:
            if peak_close_or_entry is None:
                raise AssertionError("Long position lost its peak state.")
            peak_close_or_entry = max(peak_close_or_entry, close)
            drawdown_pct = (peak_close_or_entry - close) / peak_close_or_entry * 100.0
            stop_triggered = spec.stop_pct is not None and drawdown_pct > spec.stop_pct

        if index < len(rows) - 1:
            if shares == 0 and eligible:
                pending = ("buy", date, ENTRY_REASON, None, None)
                signal = 1
                signal_reason = ENTRY_REASON
            elif shares > 0 and not eligible:
                failed = [condition for condition in BASE_CONDITIONS if not bool(row[condition])]
                signal_reason = BASE_EXIT_REASON + ":" + "|".join(failed)
                pending = (
                    "sell", date, signal_reason, peak_close_or_entry, drawdown_pct
                )
                signal = -1
            elif shares > 0 and stop_triggered:
                signal_reason = STOP_EXIT_REASON
                pending = (
                    "sell", date, signal_reason, peak_close_or_entry, drawdown_pct
                )
                signal = -1

        record: dict[str, object] = {
            "case_id": case_id,
            "date": date,
            "symbol": symbol,
            "close": close,
            "eligible": int(eligible),
            "peak_close_or_entry": peak_close_or_entry,
            "position_drawdown_pct": drawdown_pct,
            "stop_triggered": int(stop_triggered),
            "signal": signal,
            "signal_reason": signal_reason,
            "executed": executed,
            "executed_reason": executed_reason,
            "cash": cash,
            "shares": shares,
            "equity": cash + shares * close,
            "is_long": int(shares > 0),
        }
        for condition in BASE_CONDITIONS:
            record[condition] = int(bool(row[condition]))
        daily_records.append(record)

    order_columns = [
        "case_id", "symbol", "type", "signal_date", "date", "reason",
        "signal_peak_close_or_entry", "signal_drawdown_pct", "shares",
        "raw_price", "fill_price", "implicit_cost",
    ]
    trade_columns = [
        "case_id", "symbol", "entry_signal_date", "entry_date", "entry_price",
        "shares", "exit_signal_date", "exit_date", "exit_price", "exit_reason",
        "pnl", "return_pct",
    ]
    return RegimeReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
    )
