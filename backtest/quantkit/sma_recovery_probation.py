"""Two-SMA recovery strategy with probationary entries.

The short SMA is the confirmed-trend exit boundary and the long SMA is the
recovery-entry boundary.  A completed-close recovery signal fills at the next
open.  Every new position is probationary until a completed close reaches the
short SMA; failed ordinary and forced recoveries exit at the next open.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum
from typing import Literal

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.common import BarData
from pybroker.context import ExecContext

from quantkit.execution import MAX_AFFORDABLE_REQUEST
from quantkit.intraday_sma_period_cross import (
    PyBrokerResult,
    ReferenceResult,
    analysis_rows,
    dynamic_sma_cross_price,
)


ORDINARY_SMA_BUY = "BUY_LONG_SMA_CLOSE_CROSS"
FORCED_REBUY = "FORCED_REBUY"
CONFIRMED_SMA_SELL = "SELL_SHORT_SMA_CROSS"
ORDINARY_PROBATION_FAILURE = "ORDINARY_RECOVERY_FAILURE"
FORCED_PROBATION_FAILURE = "FORCED_REBUY_FAILURE"


class RecoveryState(IntEnum):
    FLAT = 0
    ORDINARY_PROBATION = 1
    FORCED_PROBATION = 2
    CONFIRMED_LONG = 3


STATE_LABELS = {
    RecoveryState.FLAT: "flat",
    RecoveryState.ORDINARY_PROBATION: "ordinary_probation",
    RecoveryState.FORCED_PROBATION: "forced_probation",
    RecoveryState.CONFIRMED_LONG: "confirmed_long",
}


@dataclass(frozen=True)
class SmaRecoveryProbationSpec:
    buy_window: int
    sell_window: int
    forced_rebuy_pct: float = 3.0
    cost_bps: float = 5.0

    def __post_init__(self) -> None:
        for name, value in (("buy_window", self.buy_window), ("sell_window", self.sell_window)):
            if isinstance(value, bool) or int(value) != value or int(value) < 2:
                raise ValueError(f"{name} must be an integer of at least two.")
        if (
            not np.isfinite(self.forced_rebuy_pct)
            or self.forced_rebuy_pct <= 0
            or self.forced_rebuy_pct >= 100
        ):
            raise ValueError("forced_rebuy_pct must be finite in (0, 100).")
        if not np.isfinite(self.cost_bps) or not 0 <= self.cost_bps < 10_000:
            raise ValueError("cost_bps must be finite in [0, 10,000).")


@dataclass(frozen=True)
class SessionPlan:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    signal: str
    fill_mode: Literal["next_open", "up_trigger", "down_trigger"]
    trigger_price: float | None
    state_before: RecoveryState
    bear_sell_anchor_before: float | None
    c_available_before: bool


@dataclass(frozen=True)
class RecoveryFillEvent:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    primary_signal: str
    matched_signals: tuple[str, ...]
    theoretical_trigger: float
    raw_fill_price: float
    fill_price: float
    fill_source: Literal["next_open", "open_gap", "intraday_trigger"]


@dataclass(frozen=True)
class CloseDecision:
    state: RecoveryState
    c_available: bool
    plan: SessionPlan | None
    state_event: str


def _ordinary_buy_cross(row: pd.Series, spec: SmaRecoveryProbationSpec) -> bool:
    return bool(
        float(row["prior_close"]) <= float(row[f"prior_sma_{spec.buy_window}"])
        and float(row["close"]) > float(row[f"sma_{spec.buy_window}"])
    )


def _plan(
    *,
    date: pd.Timestamp,
    side: Literal["buy", "sell"],
    signal: str,
    fill_mode: Literal["next_open", "up_trigger", "down_trigger"],
    trigger_price: float | None,
    state: RecoveryState,
    bear_sell_anchor: float | None,
    c_available: bool,
) -> SessionPlan:
    return SessionPlan(
        date=pd.Timestamp(date).normalize(),
        side=side,
        signal=signal,
        fill_mode=fill_mode,
        trigger_price=trigger_price,
        state_before=state,
        bear_sell_anchor_before=bear_sell_anchor,
        c_available_before=bool(c_available),
    )


def decide_after_close(
    row: pd.Series,
    next_row: pd.Series | None,
    spec: SmaRecoveryProbationSpec,
    *,
    state: RecoveryState,
    bear_sell_anchor: float | None,
    c_available: bool,
) -> CloseDecision:
    """Apply close-only transitions and freeze at most one next-session plan."""

    next_date = None if next_row is None else pd.Timestamp(next_row["date"]).normalize()
    close = float(row["close"])
    buy_sma = float(row[f"sma_{spec.buy_window}"])
    sell_sma = float(row[f"sma_{spec.sell_window}"])
    ordinary_cross = _ordinary_buy_cross(row, spec)
    next_plan: SessionPlan | None = None
    state_event = ""

    if state == RecoveryState.FLAT:
        if next_date is not None and ordinary_cross:
            next_plan = _plan(
                date=next_date,
                side="buy",
                signal=ORDINARY_SMA_BUY,
                fill_mode="next_open",
                trigger_price=None,
                state=state,
                bear_sell_anchor=bear_sell_anchor,
                c_available=c_available,
            )
        elif next_date is not None and c_available and bear_sell_anchor is not None:
            next_plan = _plan(
                date=next_date,
                side="buy",
                signal=FORCED_REBUY,
                fill_mode="up_trigger",
                trigger_price=float(bear_sell_anchor)
                * (1.0 + spec.forced_rebuy_pct / 100.0),
                state=state,
                bear_sell_anchor=bear_sell_anchor,
                c_available=c_available,
            )
        return CloseDecision(state, c_available, next_plan, state_event)

    if state == RecoveryState.ORDINARY_PROBATION:
        if close < buy_sma:
            if next_date is not None:
                next_plan = _plan(
                    date=next_date,
                    side="sell",
                    signal=ORDINARY_PROBATION_FAILURE,
                    fill_mode="next_open",
                    trigger_price=buy_sma,
                    state=state,
                    bear_sell_anchor=bear_sell_anchor,
                    c_available=c_available,
                )
            return CloseDecision(state, c_available, next_plan, "ordinary_recovery_failed")
        if close >= sell_sma:
            state = RecoveryState.CONFIRMED_LONG
            c_available = False
            state_event = "probation_confirmed"

    elif state == RecoveryState.FORCED_PROBATION:
        if bear_sell_anchor is None:
            raise AssertionError("Forced probation requires a bear sell anchor.")
        if close < float(bear_sell_anchor):
            if next_date is not None:
                next_plan = _plan(
                    date=next_date,
                    side="sell",
                    signal=FORCED_PROBATION_FAILURE,
                    fill_mode="next_open",
                    trigger_price=float(bear_sell_anchor),
                    state=state,
                    bear_sell_anchor=bear_sell_anchor,
                    c_available=False,
                )
            return CloseDecision(state, False, next_plan, "forced_rebuy_failed")
        if close >= sell_sma:
            state = RecoveryState.CONFIRMED_LONG
            c_available = False
            state_event = "probation_confirmed"
        elif ordinary_cross:
            state = RecoveryState.ORDINARY_PROBATION
            c_available = False
            state_event = "forced_to_ordinary_probation"

    if state == RecoveryState.CONFIRMED_LONG and next_date is not None:
        trigger = dynamic_sma_cross_price(
            float(next_row[f"prior_sum_{spec.sell_window}"]),
            window=spec.sell_window,
        )
        next_plan = _plan(
            date=next_date,
            side="sell",
            signal=CONFIRMED_SMA_SELL,
            fill_mode="down_trigger",
            trigger_price=trigger,
            state=state,
            bear_sell_anchor=bear_sell_anchor,
            c_available=False,
        )
    return CloseDecision(state, c_available, next_plan, state_event)


def evaluate_session_plan(
    plan: SessionPlan,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    cost_bps: float,
) -> RecoveryFillEvent | None:
    values = np.asarray((open_, high, low, close), dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Bar OHLC must be finite and positive.")
    source: Literal["next_open", "open_gap", "intraday_trigger"]
    if plan.fill_mode == "next_open":
        raw_fill = float(open_)
        source = "next_open"
        theoretical = float("nan") if plan.trigger_price is None else float(plan.trigger_price)
    elif plan.fill_mode == "up_trigger":
        if plan.trigger_price is None:
            raise AssertionError("An up-trigger plan requires a price.")
        theoretical = float(plan.trigger_price)
        if open_ >= theoretical:
            raw_fill = float(open_)
            source = "open_gap"
        elif high >= theoretical > open_:
            raw_fill = theoretical
            source = "intraday_trigger"
        else:
            return None
    else:
        if plan.trigger_price is None:
            raise AssertionError("A down-trigger plan requires a price.")
        theoretical = float(plan.trigger_price)
        if open_ <= theoretical:
            raw_fill = float(open_)
            source = "open_gap"
        elif low <= theoretical < open_:
            raw_fill = theoretical
            source = "intraday_trigger"
        else:
            return None
    cost_rate = float(cost_bps) / 10_000.0
    effective = raw_fill * (1.0 + cost_rate if plan.side == "buy" else 1.0 - cost_rate)
    return RecoveryFillEvent(
        date=plan.date,
        side=plan.side,
        primary_signal=plan.signal,
        matched_signals=(plan.signal,),
        theoretical_trigger=theoretical,
        raw_fill_price=raw_fill,
        fill_price=effective,
        fill_source=source,
    )


def _records_frame(records: list[dict[str, object]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def _apply_filled_state(
    event: RecoveryFillEvent,
    *,
    state: RecoveryState,
    bear_sell_anchor: float | None,
    c_available: bool,
) -> tuple[RecoveryState, float | None, bool]:
    if event.side == "buy":
        if state != RecoveryState.FLAT:
            raise AssertionError("A buy fill requires a flat state.")
        if event.primary_signal == ORDINARY_SMA_BUY:
            return RecoveryState.ORDINARY_PROBATION, bear_sell_anchor, c_available
        if event.primary_signal == FORCED_REBUY:
            if bear_sell_anchor is None or not c_available:
                raise AssertionError("A forced rebuy requires an available bear anchor.")
            return RecoveryState.FORCED_PROBATION, bear_sell_anchor, False
        raise AssertionError(f"Unknown buy signal: {event.primary_signal}")
    if state == RecoveryState.FLAT:
        raise AssertionError("A sell fill requires a position state.")
    if event.primary_signal == CONFIRMED_SMA_SELL:
        return RecoveryState.FLAT, float(event.fill_price), True
    if event.primary_signal in (ORDINARY_PROBATION_FAILURE, FORCED_PROBATION_FAILURE):
        return RecoveryState.FLAT, bear_sell_anchor, c_available
    raise AssertionError(f"Unknown sell signal: {event.primary_signal}")


def _plan_record(plan: SessionPlan | None, event: RecoveryFillEvent | None, date: pd.Timestamp) -> dict[str, object]:
    record: dict[str, object] = {
        "date": date,
        "candidate_signal": "" if plan is None else plan.signal,
        "candidate_side": "" if plan is None else plan.side,
        "fill_mode": "" if plan is None else plan.fill_mode,
        "trigger_price": np.nan if plan is None or plan.trigger_price is None else plan.trigger_price,
        "filled": event is not None,
    }
    if event is not None:
        record.update(
            {
                "primary_signal": event.primary_signal,
                "raw_fill_price": event.raw_fill_price,
                "fill_price": event.fill_price,
                "fill_source": event.fill_source,
            }
        )
    return record


def run_reference_sma_recovery_probation(
    data: pd.DataFrame,
    spec: SmaRecoveryProbationSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive.")
    _, analysis = analysis_rows(
        data,
        windows=[spec.buy_window, spec.sell_window],
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    symbol = str(analysis.iloc[0]["symbol"])
    cash = float(initial_cash)
    shares = 0.0
    cost_basis: float | None = None
    state = RecoveryState.FLAT
    bear_sell_anchor: float | None = None
    c_available = False
    pending_plan: SessionPlan | None = None
    open_trade: dict[str, object] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    plan_records: list[dict[str, object]] = []

    for index, row in analysis.iterrows():
        date = pd.Timestamp(row["date"]).normalize()
        plan = pending_plan if pending_plan is not None and pending_plan.date == date else None
        before_state = state
        before_anchor = bear_sell_anchor
        before_c = c_available
        before_cost = cost_basis
        event = None
        if plan is not None:
            event = evaluate_session_plan(
                plan,
                open_=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                cost_bps=spec.cost_bps,
            )
        plan_records.append(_plan_record(plan, event, date))
        executed = 0
        primary_signal = ""
        if event is not None:
            primary_signal = event.primary_signal
            state, bear_sell_anchor, c_available = _apply_filled_state(
                event,
                state=state,
                bear_sell_anchor=bear_sell_anchor,
                c_available=c_available,
            )
            if event.side == "buy":
                if shares > 0 or open_trade is not None:
                    raise AssertionError("Reference ledger attempted to buy while long.")
                shares = cash / event.fill_price
                cash = 0.0
                cost_basis = event.fill_price
                order_shares = shares
                open_trade = {
                    "symbol": symbol,
                    "entry_date": date,
                    "entry_price": event.fill_price,
                    "shares": shares,
                    "entry_signal": event.primary_signal,
                }
                executed = 1
            else:
                if shares <= 0 or open_trade is None:
                    raise AssertionError("Reference ledger attempted to sell while flat.")
                order_shares = shares
                cash = shares * event.fill_price
                trade_records.append(
                    {
                        **open_trade,
                        "exit_date": date,
                        "exit_price": event.fill_price,
                        "exit_signal": event.primary_signal,
                        "pnl": (event.fill_price - float(open_trade["entry_price"])) * shares,
                        "return_pct": (
                            event.fill_price / float(open_trade["entry_price"]) - 1.0
                        )
                        * 100.0,
                    }
                )
                shares = 0.0
                cost_basis = None
                open_trade = None
                executed = -1
            order_records.append(
                {
                    "symbol": symbol,
                    "type": event.side,
                    "date": date,
                    "shares": order_shares,
                    "fill_price": event.fill_price,
                    "raw_fill_price": event.raw_fill_price,
                    "primary_signal": event.primary_signal,
                    "matched_signals": event.primary_signal,
                    "theoretical_trigger": event.theoretical_trigger,
                    "fill_source": event.fill_source,
                    "state_before": STATE_LABELS[before_state],
                    "state_after_fill": STATE_LABELS[state],
                    "cost_basis_before": before_cost,
                    "cost_basis_after": cost_basis,
                    "bear_sell_anchor_before": before_anchor,
                    "bear_sell_anchor_after": bear_sell_anchor,
                    "c_available_before": before_c,
                    "c_available_after": c_available,
                }
            )
        pending_plan = None
        next_row = analysis.iloc[index + 1] if index + 1 < len(analysis) else None
        decision = decide_after_close(
            row,
            next_row,
            spec,
            state=state,
            bear_sell_anchor=bear_sell_anchor,
            c_available=c_available,
        )
        state = decision.state
        c_available = decision.c_available
        pending_plan = decision.plan
        daily_records.append(
            {
                "date": date,
                "symbol": symbol,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "executed": executed,
                "primary_signal": primary_signal,
                "state_event": decision.state_event,
                "position_state": STATE_LABELS[state],
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
                "cost_basis": cost_basis,
                "bear_sell_anchor": bear_sell_anchor,
                "c_available": int(c_available),
                "pending_signal": "" if pending_plan is None else pending_plan.signal,
            }
        )

    order_columns = (
        "symbol",
        "type",
        "date",
        "shares",
        "fill_price",
        "raw_fill_price",
        "primary_signal",
        "matched_signals",
        "theoretical_trigger",
        "fill_source",
        "state_before",
        "state_after_fill",
        "cost_basis_before",
        "cost_basis_after",
        "bear_sell_anchor_before",
        "bear_sell_anchor_after",
        "c_available_before",
        "c_available_after",
    )
    trade_columns = (
        "symbol",
        "entry_date",
        "entry_price",
        "shares",
        "entry_signal",
        "exit_date",
        "exit_price",
        "exit_signal",
        "pnl",
        "return_pct",
    )
    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=_records_frame(order_records, order_columns),
        trades=_records_frame(trade_records, trade_columns),
        signal_plans=pd.DataFrame(plan_records),
    )


def run_pybroker_sma_recovery_probation(
    data: pd.DataFrame,
    spec: SmaRecoveryProbationSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> PyBrokerResult:
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive.")
    engine, analysis = analysis_rows(
        data,
        windows=[spec.buy_window, spec.sell_window],
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    symbol = str(analysis.iloc[0]["symbol"])
    analysis_dates = [pd.Timestamp(value).normalize() for value in analysis["date"]]
    row_by_date = {
        pd.Timestamp(row.date).normalize(): pd.Series(row._asdict())
        for row in analysis.itertuples(index=False)
    }
    next_date = {
        current: following for current, following in zip(analysis_dates, analysis_dates[1:])
    }
    pre_start = pd.Timestamp(engine.iloc[0]["date"]).normalize()
    runtime: dict[str, object] = {
        "state": RecoveryState.FLAT,
        "bear_sell_anchor": None,
        "c_available": False,
        "pending_event": None,
    }
    buy_reject_limit = Decimal("100000000000")
    sell_reject_limit = Decimal("0.000000001")

    def conditional_fill(plan: SessionPlan):
        def fill(_symbol: str, bar: BarData) -> float:
            event = evaluate_session_plan(
                plan,
                open_=float(bar.open[-1]),
                high=float(bar.high[-1]),
                low=float(bar.low[-1]),
                close=float(bar.close[-1]),
                cost_bps=spec.cost_bps,
            )
            runtime["pending_event"] = event
            if event is None:
                return 100000000001.0 if plan.side == "buy" else 0.0000000001
            return event.fill_price

        return fill

    def submit(ctx: ExecContext, plan: SessionPlan | None) -> None:
        if plan is None:
            return
        if plan.side == "sell":
            ctx.sell_all_shares()
            ctx.sell_fill_price = conditional_fill(plan)
            ctx.sell_limit_price = sell_reject_limit
        else:
            ctx.buy_shares = MAX_AFFORDABLE_REQUEST
            ctx.buy_fill_price = conditional_fill(plan)
            ctx.buy_limit_price = buy_reject_limit

    def execute(ctx: ExecContext) -> None:
        current = pd.Timestamp(ctx.dt).normalize()
        pending = runtime.get("pending_event")
        if isinstance(pending, RecoveryFillEvent) and pending.date == current:
            state, anchor, available = _apply_filled_state(
                pending,
                state=RecoveryState(runtime["state"]),
                bear_sell_anchor=(
                    None
                    if runtime["bear_sell_anchor"] is None
                    else float(runtime["bear_sell_anchor"])
                ),
                c_available=bool(runtime["c_available"]),
            )
            runtime["state"] = state
            runtime["bear_sell_anchor"] = anchor
            runtime["c_available"] = available
            runtime["pending_event"] = None
        if current == pre_start:
            return
        row = row_by_date.get(current)
        if row is None:
            return
        following = next_date.get(current)
        next_row = None if following is None else row_by_date[following]
        decision = decide_after_close(
            row,
            next_row,
            spec,
            state=RecoveryState(runtime["state"]),
            bear_sell_anchor=(
                None
                if runtime["bear_sell_anchor"] is None
                else float(runtime["bear_sell_anchor"])
            ),
            c_available=bool(runtime["c_available"]),
        )
        runtime["state"] = decision.state
        runtime["c_available"] = decision.c_available
        submit(ctx, decision.plan)

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = StrategyConfig(
        initial_cash=float(initial_cash),
        fee_mode=None,
        fee_amount=0,
        enable_fractional_shares=True,
        round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY,
        max_long_positions=1,
        buy_delay=1,
        sell_delay=1,
        exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.OPEN,
        exit_sell_fill_price=PriceType.OPEN,
        bars_per_year=252,
        return_signals=False,
        round_test_result=False,
    )
    strategy = Strategy(
        engine,
        pre_start.date().isoformat(),
        pd.Timestamp(engine.iloc[-1]["date"]).date().isoformat(),
        config,
    )
    strategy.add_execution(execute, symbol)
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    return PyBrokerResult(pybroker_result=result, engine_start=pre_start)
