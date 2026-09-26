"""Causal intraday SMA threshold orders with an independent reference ledger.

Before each session the strategy solves the exact price at which the current
provisional SMA reaches a declared buffer.  A touched threshold fills at that
price; an overnight overshoot fills at the regular-session open.  Optional
correction orders are anchored to the previous effective account fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.common import BarData
from pybroker.context import ExecContext

from quantkit.execution import MAX_AFFORDABLE_REQUEST


BUY_SMA200_THRESHOLD = "BUY_SMA200_THRESHOLD"
BUY_CORRECTION = "BUY_CORRECTION"
SELL_SMA200_THRESHOLD = "SELL_SMA200_THRESHOLD"
SELL_CORRECTION = "SELL_CORRECTION"

BUY_SIGNALS = (BUY_SMA200_THRESHOLD, BUY_CORRECTION)
SELL_SIGNALS = (SELL_SMA200_THRESHOLD, SELL_CORRECTION)


@dataclass(frozen=True)
class IntradaySmaThresholdSpec:
    a_pct: float
    b_pct: float
    correction_pct: float | None = None
    correction_buy_pct: float | None = None
    correction_sell_pct: float | None = None
    window: int = 200
    cost_bps: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("a_pct", self.a_pct), ("b_pct", self.b_pct)):
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.window < 2:
            raise ValueError("window must be at least 2.")
        if self.sell_multiplier <= 0 or self.buy_multiplier <= 0:
            raise ValueError("SMA threshold multipliers must remain positive.")
        if self.window <= max(self.sell_multiplier, self.buy_multiplier):
            raise ValueError("SMA threshold equation has a non-positive denominator.")
        if self.correction_pct is not None and (
            self.correction_buy_pct is not None or self.correction_sell_pct is not None
        ):
            raise ValueError(
                "correction_pct cannot be combined with asymmetric correction values."
            )
        for name, value in (
            ("correction_pct", self.correction_pct),
            ("correction_buy_pct", self.correction_buy_pct),
            ("correction_sell_pct", self.correction_sell_pct),
        ):
            if value is not None and (
                not np.isfinite(value) or value <= 0 or value >= 100
            ):
                raise ValueError(f"{name} must be None or finite in (0, 100).")
        if not np.isfinite(self.cost_bps) or not 0 <= self.cost_bps < 10_000:
            raise ValueError("cost_bps must be finite in [0, 10,000).")

    @property
    def sell_multiplier(self) -> float:
        return 1.0 - self.a_pct / 100.0

    @property
    def buy_multiplier(self) -> float:
        return 1.0 + self.b_pct / 100.0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps / 10_000.0

    @property
    def resolved_buy_correction_pct(self) -> float | None:
        """Return c, retaining the legacy symmetric correction contract."""

        if self.correction_pct is not None:
            return self.correction_pct
        return self.correction_buy_pct

    @property
    def resolved_sell_correction_pct(self) -> float | None:
        """Return d, retaining the legacy symmetric correction contract."""

        if self.correction_pct is not None:
            return self.correction_pct
        return self.correction_sell_pct


@dataclass(frozen=True)
class Trigger:
    signal: str
    price: float
    formula: str


@dataclass(frozen=True)
class OrderPlan:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    triggers: tuple[Trigger, ...]
    cost_bps: float
    cost_basis_before: float | None
    last_sell_price_before: float | None


@dataclass(frozen=True)
class FillEvent:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    primary_signal: str
    matched_signals: tuple[str, ...]
    theoretical_trigger: float
    raw_fill_price: float
    fill_price: float
    fill_source: Literal["open_gap", "intraday_trigger"]
    trigger_prices: tuple[tuple[str, float], ...]


@dataclass
class ReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    signal_plans: pd.DataFrame


@dataclass
class PyBrokerResult:
    pybroker_result: object
    engine_start: pd.Timestamp


def dynamic_sma_threshold(prior_sum: float, *, window: int, multiplier: float) -> float:
    """Solve P = multiplier * (prior_sum + P) / window for P."""

    denominator = float(window) - float(multiplier)
    if not np.isfinite(prior_sum) or prior_sum <= 0 or denominator <= 0:
        raise ValueError("Cannot solve a positive dynamic SMA threshold.")
    return float(multiplier) * float(prior_sum) / denominator


def prepare_intraday_threshold_data(data: pd.DataFrame, window: int = 200) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows["symbol"].nunique() != 1:
        raise ValueError("Exactly one symbol is required.")
    if rows.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed.")
    ohlc = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError("OHLC must be finite and positive.")
    if (
        (rows["low"] > rows[["open", "close"]].min(axis=1))
        | (rows["high"] < rows[["open", "close"]].max(axis=1))
    ).any():
        raise ValueError("Invalid OHLC envelope.")
    closes = rows["close"].astype(float)
    rows["sma"] = closes.rolling(window, min_periods=window).mean()
    rows["prior_close"] = closes.shift(1)
    rows["prior_sma"] = rows["sma"].shift(1)
    rows["prior_sum"] = closes.shift(1).rolling(window - 1, min_periods=window - 1).sum()
    return rows


def _analysis_rows(
    data: pd.DataFrame,
    *,
    window: int,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prepared = prepare_intraday_threshold_data(data, window)
    start = pd.Timestamp(analysis_start).normalize()
    end = pd.Timestamp(analysis_end).normalize()
    start_matches = prepared.index[prepared["date"] == start].tolist()
    end_matches = prepared.index[prepared["date"] == end].tolist()
    if len(start_matches) != 1 or len(end_matches) != 1:
        raise ValueError("Analysis start/end must each be one available trading date.")
    start_index = start_matches[0]
    end_index = end_matches[0]
    if start_index < window - 1 or end_index < start_index:
        raise ValueError("Insufficient SMA warmup or invalid analysis interval.")
    analysis = prepared.iloc[start_index : end_index + 1].copy().reset_index(drop=True)
    if analysis["prior_sum"].isna().any():
        raise ValueError("Analysis interval contains incomplete SMA trigger inputs.")
    engine = prepared.iloc[start_index - 1 : end_index + 1].copy().reset_index(drop=True)
    return engine, analysis


def build_order_plan(
    row: pd.Series,
    spec: IntradaySmaThresholdSpec,
    *,
    is_long: bool,
    cost_basis: float | None,
    last_sell_price: float | None,
) -> OrderPlan:
    prior_sum = float(row["prior_sum"])
    date = pd.Timestamp(row["date"]).normalize()
    triggers: list[Trigger] = []
    if is_long:
        if cost_basis is None:
            raise ValueError("A long state requires a cost basis.")
        triggers.append(
            Trigger(
                SELL_SMA200_THRESHOLD,
                dynamic_sma_threshold(
                    prior_sum,
                    window=spec.window,
                    multiplier=spec.sell_multiplier,
                ),
                f"P=(1-{spec.a_pct:g}%)*(sum(previous {spec.window - 1} closes)+P)/{spec.window}",
            )
        )
        sell_correction_pct = spec.resolved_sell_correction_pct
        if sell_correction_pct is not None:
            triggers.append(
                Trigger(
                    SELL_CORRECTION,
                    float(cost_basis) * (1.0 - sell_correction_pct / 100.0),
                    f"last effective buy fill*(1-{sell_correction_pct:g}%)",
                )
            )
        side: Literal["buy", "sell"] = "sell"
    else:
        # The ordinary entry retains the original crossing semantics.  A flat
        # account only places the upward order when the completed prior close
        # was at or below its completed-SMA boundary.  Correction re-entry is
        # independent of this eligibility gate.
        prior_close = float(row["prior_close"])
        prior_sma = float(row["prior_sma"])
        if prior_close <= prior_sma * spec.buy_multiplier:
            triggers.append(
                Trigger(
                    BUY_SMA200_THRESHOLD,
                    dynamic_sma_threshold(
                        prior_sum,
                        window=spec.window,
                        multiplier=spec.buy_multiplier,
                    ),
                    f"P=(1+{spec.b_pct:g}%)*(sum(previous {spec.window - 1} closes)+P)/{spec.window}",
                )
            )
        buy_correction_pct = spec.resolved_buy_correction_pct
        if buy_correction_pct is not None and last_sell_price is not None:
            triggers.append(
                Trigger(
                    BUY_CORRECTION,
                    float(last_sell_price) * (1.0 + buy_correction_pct / 100.0),
                    f"last effective sell fill*(1+{buy_correction_pct:g}%)",
                )
            )
        side = "buy"
    return OrderPlan(
        date=date,
        side=side,
        triggers=tuple(triggers),
        cost_bps=spec.cost_bps,
        cost_basis_before=cost_basis,
        last_sell_price_before=last_sell_price,
    )


def evaluate_order_plan(
    plan: OrderPlan,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> FillEvent | None:
    values = np.asarray((open_, high, low, close), dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Bar OHLC must be finite and positive.")
    if not plan.triggers:
        return None
    if plan.side == "sell":
        ordered = sorted(plan.triggers, key=lambda trigger: (-trigger.price, SELL_SIGNALS.index(trigger.signal)))
        gap = [trigger for trigger in ordered if open_ <= trigger.price]
        if gap:
            primary = ordered[0]
            matched = tuple(trigger.signal for trigger in gap)
            raw_fill = float(open_)
            source: Literal["open_gap", "intraday_trigger"] = "open_gap"
        else:
            touched = [trigger for trigger in ordered if low <= trigger.price < open_]
            if not touched:
                return None
            primary = touched[0]
            matched = tuple(
                trigger.signal
                for trigger in touched
                if np.isclose(trigger.price, primary.price, rtol=0, atol=1e-12)
            )
            raw_fill = primary.price
            source = "intraday_trigger"
        effective_fill = raw_fill * (1.0 - plan.cost_bps / 10_000.0)
    else:
        ordered = sorted(plan.triggers, key=lambda trigger: (trigger.price, BUY_SIGNALS.index(trigger.signal)))
        gap = [trigger for trigger in ordered if open_ >= trigger.price]
        if gap:
            primary = ordered[0]
            matched = tuple(trigger.signal for trigger in gap)
            raw_fill = float(open_)
            source = "open_gap"
        else:
            touched = [trigger for trigger in ordered if high >= trigger.price > open_]
            if not touched:
                return None
            primary = touched[0]
            matched = tuple(
                trigger.signal
                for trigger in touched
                if np.isclose(trigger.price, primary.price, rtol=0, atol=1e-12)
            )
            raw_fill = primary.price
            source = "intraday_trigger"
        effective_fill = raw_fill * (1.0 + plan.cost_bps / 10_000.0)
    return FillEvent(
        date=plan.date,
        side=plan.side,
        primary_signal=primary.signal,
        matched_signals=matched,
        theoretical_trigger=primary.price,
        raw_fill_price=raw_fill,
        fill_price=effective_fill,
        fill_source=source,
        trigger_prices=tuple((trigger.signal, trigger.price) for trigger in ordered),
    )


def _records_frame(records: list[dict[str, object]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def _plan_record(plan: OrderPlan, event: FillEvent | None) -> dict[str, object]:
    record: dict[str, object] = {
        "date": plan.date,
        "side": plan.side,
        "candidate_signals": "|".join(trigger.signal for trigger in plan.triggers),
        "candidate_triggers": "|".join(
            f"{trigger.signal}:{trigger.price:.12g}" for trigger in plan.triggers
        ),
        "sma_trigger": next(
            (trigger.price for trigger in plan.triggers if "SMA200" in trigger.signal),
            np.nan,
        ),
        "correction_trigger": next(
            (trigger.price for trigger in plan.triggers if "CORRECTION" in trigger.signal),
            np.nan,
        ),
        "filled": event is not None,
        "cost_basis_before": plan.cost_basis_before,
        "last_sell_price_before": plan.last_sell_price_before,
    }
    if event is not None:
        record.update(
            {
                "primary_signal": event.primary_signal,
                "matched_signals": "|".join(event.matched_signals),
                "theoretical_trigger": event.theoretical_trigger,
                "raw_fill_price": event.raw_fill_price,
                "fill_price": event.fill_price,
                "fill_source": event.fill_source,
            }
        )
    return record


def run_reference_intraday_threshold(
    data: pd.DataFrame,
    spec: IntradaySmaThresholdSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive.")
    _, analysis = _analysis_rows(
        data,
        window=spec.window,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    symbol = str(analysis.iloc[0]["symbol"])
    cash = float(initial_cash)
    shares = 0.0
    cost_basis: float | None = None
    last_sell_price: float | None = None
    open_trade: dict[str, object] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    plan_records: list[dict[str, object]] = []

    for _, row in analysis.iterrows():
        current_date = pd.Timestamp(row["date"])
        before_cost = cost_basis
        before_sell = last_sell_price
        plan = build_order_plan(
            row,
            spec,
            is_long=shares > 0,
            cost_basis=cost_basis,
            last_sell_price=last_sell_price,
        )
        event = evaluate_order_plan(
            plan,
            open_=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )
        plan_records.append(_plan_record(plan, event))
        executed = 0
        signal = ""
        if event is not None:
            signal = event.primary_signal
            if event.side == "sell":
                if shares <= 0 or open_trade is None:
                    raise AssertionError("Independent ledger attempted to sell while flat.")
                order_shares = shares
                cash = order_shares * event.fill_price
                pnl = (event.fill_price - float(open_trade["entry_price"])) * order_shares
                trade_records.append(
                    {
                        **open_trade,
                        "exit_date": current_date,
                        "exit_price": event.fill_price,
                        "exit_signal": event.primary_signal,
                        "pnl": pnl,
                        "return_pct": (
                            event.fill_price / float(open_trade["entry_price"]) - 1.0
                        )
                        * 100.0,
                    }
                )
                shares = 0.0
                cost_basis = None
                last_sell_price = event.fill_price
                open_trade = None
                executed = -1
            else:
                if shares > 0:
                    raise AssertionError("Independent ledger attempted to buy while long.")
                shares = cash / event.fill_price
                cash = 0.0
                order_shares = shares
                cost_basis = event.fill_price
                open_trade = {
                    "symbol": symbol,
                    "entry_date": current_date,
                    "entry_price": event.fill_price,
                    "shares": shares,
                    "entry_signal": event.primary_signal,
                }
                executed = 1
            order_records.append(
                {
                    "symbol": symbol,
                    "type": event.side,
                    "date": current_date,
                    "shares": order_shares,
                    "fill_price": event.fill_price,
                    "raw_fill_price": event.raw_fill_price,
                    "primary_signal": event.primary_signal,
                    "matched_signals": "|".join(event.matched_signals),
                    "theoretical_trigger": event.theoretical_trigger,
                    "fill_source": event.fill_source,
                    "cost_basis_before": before_cost,
                    "cost_basis_after": cost_basis,
                    "last_sell_price_before": before_sell,
                    "last_sell_price_after": last_sell_price,
                }
            )
        daily_records.append(
            {
                "date": current_date,
                "symbol": symbol,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "executed": executed,
                "primary_signal": signal,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
                "cost_basis": cost_basis,
                "last_sell_price": last_sell_price,
            }
        )

    order_columns = (
        "symbol", "type", "date", "shares", "fill_price", "raw_fill_price",
        "primary_signal", "matched_signals", "theoretical_trigger", "fill_source",
        "cost_basis_before", "cost_basis_after", "last_sell_price_before",
        "last_sell_price_after",
    )
    trade_columns = (
        "symbol", "entry_date", "entry_price", "shares", "entry_signal",
        "exit_date", "exit_price", "exit_signal", "pnl", "return_pct",
    )
    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=_records_frame(order_records, order_columns),
        trades=_records_frame(trade_records, trade_columns),
        signal_plans=pd.DataFrame(plan_records),
    )


def run_pybroker_intraday_threshold(
    data: pd.DataFrame,
    spec: IntradaySmaThresholdSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> PyBrokerResult:
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive.")
    engine, analysis = _analysis_rows(
        data,
        window=spec.window,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )
    symbol = str(analysis.iloc[0]["symbol"])
    analysis_dates = [pd.Timestamp(value) for value in analysis["date"]]
    row_by_date = {
        pd.Timestamp(row.date): pd.Series(row._asdict())
        for row in analysis.itertuples(index=False)
    }
    next_date = {
        current: following
        for current, following in zip(analysis_dates, analysis_dates[1:])
    }
    pre_start = pd.Timestamp(engine.iloc[0]["date"])
    first_date = analysis_dates[0]
    runtime: dict[str, object] = {
        "cost_basis": None,
        "last_sell_price": None,
        "pending_event": None,
    }
    buy_reject_limit = Decimal("100000000000")
    sell_reject_limit = Decimal("0.000000001")

    def conditional_fill(plan: OrderPlan):
        def fill(_symbol: str, bar: BarData) -> float:
            event = evaluate_order_plan(
                plan,
                open_=float(bar.open[-1]),
                high=float(bar.high[-1]),
                low=float(bar.low[-1]),
                close=float(bar.close[-1]),
            )
            runtime["pending_event"] = event
            if event is None:
                return 100000000001.0 if plan.side == "buy" else 0.0000000001
            return event.fill_price

        return fill

    def submit(ctx: ExecContext, plan: OrderPlan) -> None:
        if not plan.triggers:
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
        if isinstance(pending, FillEvent) and pending.date == current:
            if pending.side == "sell":
                runtime["cost_basis"] = None
                runtime["last_sell_price"] = pending.fill_price
            else:
                runtime["cost_basis"] = pending.fill_price
            runtime["pending_event"] = None

        if current == pre_start:
            submit(
                ctx,
                build_order_plan(
                    row_by_date[first_date],
                    spec,
                    is_long=False,
                    cost_basis=None,
                    last_sell_price=None,
                ),
            )
            return
        following = next_date.get(current)
        if following is None:
            return
        submit(
            ctx,
            build_order_plan(
                row_by_date[following],
                spec,
                is_long=ctx.long_pos() is not None,
                cost_basis=(
                    None if runtime["cost_basis"] is None else float(runtime["cost_basis"])
                ),
                last_sell_price=(
                    None
                    if runtime["last_sell_price"] is None
                    else float(runtime["last_sell_price"])
                ),
            ),
        )

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
