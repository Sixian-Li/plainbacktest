"""Causal dual-StochRSI trigger prices and Open-to-Close execution ledgers."""

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


Mode = Literal["LEVEL", "EXTREME", "CROSS", "SMA200"]
Direction = Literal["up", "down"]


@dataclass(frozen=True)
class TimingSpec:
    mode: Mode
    cost_bps: float = 0.0
    periods: tuple[int, ...] = (42, 100)
    buy_threshold: float = 0.2
    sell_threshold: float = 0.8
    sma_window: int = 200
    sma_buy_buffer_pct: float = 3.0
    sma_sell_buffer_pct: float = 3.0

    def __post_init__(self) -> None:
        if self.mode not in ("LEVEL", "EXTREME", "CROSS", "SMA200"):
            raise ValueError(f"Unsupported mode: {self.mode}")
        if len(self.periods) not in {1, 2} or len(set(self.periods)) != len(self.periods) or min(self.periods) < 2:
            raise ValueError("periods must contain one value or two distinct values, all >= 2")
        if not 0 <= self.buy_threshold < self.sell_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= buy < sell <= 1")
        if not np.isfinite(self.cost_bps) or not 0 <= self.cost_bps < 10_000:
            raise ValueError("cost_bps must be finite in [0, 10,000)")


@dataclass(frozen=True)
class OrderPlan:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    direction: Direction
    trigger: float | None
    signal: str
    eligible: bool
    cost_bps: float


@dataclass(frozen=True)
class FillEvent:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    primary_signal: str
    theoretical_trigger: float
    raw_fill_price: float
    fill_price: float
    fill_source: Literal["open_gap", "open_close_trigger"]


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


def _validate_prices(data: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows["symbol"].nunique() != 1 or rows.duplicated(["symbol", "date"]).any():
        raise ValueError("Exactly one symbol with unique dates is required")
    prices = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC must be finite and positive")
    if (
        (rows["low"] > rows[["open", "close"]].min(axis=1))
        | (rows["high"] < rows[["open", "close"]].max(axis=1))
    ).any():
        raise ValueError("Invalid OHLC envelope")
    return rows


def wilder_components(close: pd.Series, period: int) -> pd.DataFrame:
    """Return Wilder RSI and its recursive gain/loss state."""

    values = pd.Series(close, dtype=float).reset_index(drop=True)
    count = len(values)
    gain_state = np.full(count, np.nan)
    loss_state = np.full(count, np.nan)
    rsi = np.full(count, np.nan)
    if count <= period:
        return pd.DataFrame({"rsi": rsi, "average_gain": gain_state, "average_loss": loss_state})
    delta = values.diff().to_numpy(float)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(np.mean(gains[1 : period + 1]))
    average_loss = float(np.mean(losses[1 : period + 1]))

    def rsi_value(gain: float, loss: float) -> float:
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    for index in range(period, count):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index]) / period
            average_loss = (average_loss * (period - 1) + losses[index]) / period
        gain_state[index] = average_gain
        loss_state[index] = average_loss
        rsi[index] = rsi_value(average_gain, average_loss)
    return pd.DataFrame(
        {"rsi": rsi, "average_gain": gain_state, "average_loss": loss_state},
        index=close.index,
    )


def solve_close_for_rsi(
    prior_close: float,
    prior_average_gain: float,
    prior_average_loss: float,
    *,
    period: int,
    target_rsi: float,
) -> float:
    """Solve the next close that makes Wilder RSI equal ``target_rsi``."""

    values = np.asarray(
        [prior_close, prior_average_gain, prior_average_loss, target_rsi], dtype=float
    )
    if not np.isfinite(values).all() or prior_close <= 0 or not 0 < target_rsi < 100:
        raise ValueError("RSI price solve requires finite state, positive price, and target in (0,100)")
    decay_gain = prior_average_gain * (period - 1)
    decay_loss = prior_average_loss * (period - 1)
    unchanged = 50.0 if decay_gain == decay_loss == 0 else (
        100.0 if decay_loss == 0 else 100.0 - 100.0 / (1.0 + decay_gain / decay_loss)
    )
    ratio = target_rsi / (100.0 - target_rsi)
    if target_rsi >= unchanged:
        delta = ratio * decay_loss - decay_gain
        if delta < -1e-10:
            raise AssertionError("upward RSI solve produced a negative price delta")
        solved = prior_close + max(delta, 0.0)
    else:
        loss = decay_gain / ratio - decay_loss
        if loss < -1e-10:
            raise AssertionError("downward RSI solve produced a negative loss")
        solved = prior_close - max(loss, 0.0)
    if not np.isfinite(solved) or solved <= 0:
        raise ValueError("RSI threshold implies a non-positive price")
    return float(solved)


def _stoch_trigger_price(row: pd.Series, period: int, threshold: float) -> float:
    low = float(row[f"prior_rsi_low_{period}"])
    high = float(row[f"prior_rsi_high_{period}"])
    if not np.isfinite(low) or not np.isfinite(high):
        return float("nan")
    target = low + threshold * (high - low)
    if not 0 < target < 100:
        return float("nan")
    try:
        return solve_close_for_rsi(
            float(row["prior_close"]),
            float(row[f"prior_average_gain_{period}"]),
            float(row[f"prior_average_loss_{period}"]),
            period=period,
            target_rsi=target,
        )
    except ValueError:
        return float("nan")


def prepare_dual_stochrsi_data(data: pd.DataFrame, spec: TimingSpec) -> pd.DataFrame:
    if spec.mode == "SMA200":
        raise ValueError("Use prepare_sma200_data for the SMA baseline")
    rows = _validate_prices(data)
    closes = rows["close"].astype(float)
    rows["prior_close"] = closes.shift(1)
    for period in spec.periods:
        components = wilder_components(closes, period)
        rsi = components["rsi"]
        rows[f"rsi_{period}"] = rsi
        rows[f"prior_average_gain_{period}"] = components["average_gain"].shift(1)
        rows[f"prior_average_loss_{period}"] = components["average_loss"].shift(1)
        rows[f"prior_rsi_low_{period}"] = rsi.shift(1).rolling(
            period - 1, min_periods=period - 1
        ).min()
        rows[f"prior_rsi_high_{period}"] = rsi.shift(1).rolling(
            period - 1, min_periods=period - 1
        ).max()
        rolling_low = rsi.rolling(period, min_periods=period).min()
        rolling_high = rsi.rolling(period, min_periods=period).max()
        span = rolling_high - rolling_low
        stoch = (rsi - rolling_low) / span
        stoch.loc[span.eq(0) & rolling_low.notna()] = 0.5
        rows[f"stochrsi_{period}"] = stoch
        rows[f"prior_stochrsi_{period}"] = stoch.shift(1)

    if spec.mode == "EXTREME":
        buy_level, sell_level = 0.0, 1.0
        buy_direction: Direction = "down"
        sell_direction: Direction = "up"
    elif spec.mode == "LEVEL":
        buy_level, sell_level = spec.buy_threshold, spec.sell_threshold
        buy_direction = "down"
        sell_direction = "up"
    else:
        buy_level, sell_level = spec.buy_threshold, spec.sell_threshold
        buy_direction = "up"
        sell_direction = "down"

    for period in spec.periods:
        rows[f"buy_trigger_{period}"] = rows.apply(
            _stoch_trigger_price, axis=1, args=(period, buy_level)
        )
        rows[f"sell_trigger_{period}"] = rows.apply(
            _stoch_trigger_price, axis=1, args=(period, sell_level)
        )
    buy_parts = rows[[f"buy_trigger_{period}" for period in spec.periods]]
    sell_parts = rows[[f"sell_trigger_{period}" for period in spec.periods]]
    rows["buy_trigger"] = (
        buy_parts.max(axis=1, skipna=False) if buy_direction == "up"
        else buy_parts.min(axis=1, skipna=False)
    )
    rows["sell_trigger"] = (
        sell_parts.max(axis=1, skipna=False) if sell_direction == "up"
        else sell_parts.min(axis=1, skipna=False)
    )
    rows["buy_direction"] = buy_direction
    rows["sell_direction"] = sell_direction
    if spec.mode == "CROSS":
        rows["buy_eligible"] = np.logical_and.reduce(
            [rows[f"prior_stochrsi_{period}"] < spec.buy_threshold for period in spec.periods]
        )
        rows["sell_eligible"] = np.logical_and.reduce(
            [rows[f"prior_stochrsi_{period}"] > spec.sell_threshold for period in spec.periods]
        )
    else:
        rows["buy_eligible"] = True
        rows["sell_eligible"] = True
    rows["buy_signal"] = f"BUY_DUAL_STOCHRSI_{spec.mode}"
    rows["sell_signal"] = f"SELL_DUAL_STOCHRSI_{spec.mode}"
    return rows


def prepare_sma200_data(data: pd.DataFrame, spec: TimingSpec) -> pd.DataFrame:
    if spec.mode != "SMA200":
        raise ValueError("SMA preparation requires mode SMA200")
    rows = _validate_prices(data)
    closes = rows["close"].astype(float)
    prior_sum = closes.shift(1).rolling(spec.sma_window - 1, min_periods=spec.sma_window - 1).sum()

    def dynamic(multiplier: float) -> pd.Series:
        denominator = spec.sma_window - multiplier
        return multiplier * prior_sum / denominator

    rows["buy_trigger"] = dynamic(1.0 + spec.sma_buy_buffer_pct / 100.0)
    rows["sell_trigger"] = dynamic(1.0 - spec.sma_sell_buffer_pct / 100.0)
    rows["buy_direction"] = "up"
    rows["sell_direction"] = "down"
    rows["buy_eligible"] = True
    rows["sell_eligible"] = True
    rows["buy_signal"] = "BUY_SMA200_PLUS_3"
    rows["sell_signal"] = "SELL_SMA200_MINUS_3"
    rows["sma200"] = closes.rolling(spec.sma_window, min_periods=spec.sma_window).mean()
    return rows


def build_order_plan(row: pd.Series, spec: TimingSpec, *, is_long: bool) -> OrderPlan:
    side: Literal["buy", "sell"] = "sell" if is_long else "buy"
    trigger_value = row[f"{side}_trigger"]
    trigger = float(trigger_value) if np.isfinite(trigger_value) else None
    return OrderPlan(
        date=pd.Timestamp(row["date"]).normalize(),
        side=side,
        direction=str(row[f"{side}_direction"]),  # type: ignore[arg-type]
        trigger=trigger,
        signal=str(row[f"{side}_signal"]),
        eligible=bool(row[f"{side}_eligible"]),
        cost_bps=spec.cost_bps,
    )


def evaluate_order_plan(plan: OrderPlan, *, open_: float, close: float) -> FillEvent | None:
    if not np.isfinite([open_, close]).all() or min(open_, close) <= 0:
        raise ValueError("Open and Close must be finite and positive")
    if not plan.eligible or plan.trigger is None:
        return None
    trigger = plan.trigger
    if plan.direction == "up":
        if open_ >= trigger:
            raw_fill, source = float(open_), "open_gap"
        elif close >= trigger > open_:
            raw_fill, source = trigger, "open_close_trigger"
        else:
            return None
    else:
        if open_ <= trigger:
            raw_fill, source = float(open_), "open_gap"
        elif close <= trigger < open_:
            raw_fill, source = trigger, "open_close_trigger"
        else:
            return None
    multiplier = 1.0 + plan.cost_bps / 10_000.0 if plan.side == "buy" else 1.0 - plan.cost_bps / 10_000.0
    return FillEvent(
        date=plan.date,
        side=plan.side,
        primary_signal=plan.signal,
        theoretical_trigger=trigger,
        raw_fill_price=raw_fill,
        fill_price=raw_fill * multiplier,
        fill_source=source,  # type: ignore[arg-type]
    )


def _analysis_rows(
    prepared: pd.DataFrame,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    start, end = pd.Timestamp(analysis_start), pd.Timestamp(analysis_end)
    matches = prepared.index[prepared["date"].eq(start)].tolist()
    end_matches = prepared.index[prepared["date"].eq(end)].tolist()
    if len(matches) != 1 or len(end_matches) != 1 or matches[0] < 1:
        raise ValueError("Analysis boundaries must be unique available sessions with a prior bar")
    analysis = prepared.iloc[matches[0] : end_matches[0] + 1].copy().reset_index(drop=True)
    engine = prepared.iloc[matches[0] - 1 : end_matches[0] + 1].copy().reset_index(drop=True)
    return engine, analysis


def run_reference(
    prepared: pd.DataFrame,
    spec: TimingSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ReferenceResult:
    _, analysis = _analysis_rows(prepared, analysis_start, analysis_end)
    symbol = str(analysis.iloc[0]["symbol"])
    cash, shares = float(initial_cash), 0.0
    open_trade: dict[str, object] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    plan_records: list[dict[str, object]] = []
    for _, row in analysis.iterrows():
        plan = build_order_plan(row, spec, is_long=shares > 0)
        event = evaluate_order_plan(plan, open_=float(row["open"]), close=float(row["close"]))
        plan_records.append(
            {
                "date": plan.date,
                "side": plan.side,
                "direction": plan.direction,
                "eligible": plan.eligible,
                "trigger": plan.trigger,
                "filled": event is not None,
            }
        )
        if event is not None:
            if event.side == "buy":
                shares = cash / event.fill_price
                cash = 0.0
                open_trade = {
                    "symbol": symbol,
                    "entry_date": event.date,
                    "entry_price": event.fill_price,
                    "shares": shares,
                    "entry_signal": event.primary_signal,
                }
            else:
                if shares <= 0 or open_trade is None:
                    raise AssertionError("Reference ledger attempted to sell while flat")
                cash = shares * event.fill_price
                pnl = (event.fill_price - float(open_trade["entry_price"])) * shares
                trade_records.append(
                    {
                        **open_trade,
                        "exit_date": event.date,
                        "exit_price": event.fill_price,
                        "exit_signal": event.primary_signal,
                        "pnl": pnl,
                        "return_pct": (event.fill_price / float(open_trade["entry_price"]) - 1) * 100,
                    }
                )
                shares = 0.0
                open_trade = None
            order_records.append(
                {
                    "symbol": symbol,
                    "type": event.side,
                    "date": event.date,
                    "shares": float(open_trade["shares"]) if event.side == "buy" and open_trade else cash / event.fill_price,
                    "fill_price": event.fill_price,
                    "raw_fill_price": event.raw_fill_price,
                    "primary_signal": event.primary_signal,
                    "theoretical_trigger": event.theoretical_trigger,
                    "fill_source": event.fill_source,
                }
            )
        daily_records.append(
            {
                "date": pd.Timestamp(row["date"]),
                "symbol": symbol,
                "open": float(row["open"]),
                "close": float(row["close"]),
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
            }
        )
    order_columns = (
        "symbol", "type", "date", "shares", "fill_price", "raw_fill_price",
        "primary_signal", "theoretical_trigger", "fill_source",
    )
    trade_columns = (
        "symbol", "entry_date", "entry_price", "shares", "entry_signal", "exit_date",
        "exit_price", "exit_signal", "pnl", "return_pct",
    )
    return ReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
        signal_plans=pd.DataFrame(plan_records),
    )


def run_pybroker(
    prepared: pd.DataFrame,
    spec: TimingSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> PyBrokerResult:
    engine, analysis = _analysis_rows(prepared, analysis_start, analysis_end)
    symbol = str(analysis.iloc[0]["symbol"])
    dates = [pd.Timestamp(value) for value in analysis["date"]]
    row_by_date = {pd.Timestamp(row.date): pd.Series(row._asdict()) for row in analysis.itertuples(index=False)}
    next_date = dict(zip(dates, dates[1:]))
    pre_start, first_date = pd.Timestamp(engine.iloc[0]["date"]), dates[0]

    def conditional_fill(plan: OrderPlan):
        def fill(_symbol: str, bar: BarData) -> float:
            event = evaluate_order_plan(plan, open_=float(bar.open[-1]), close=float(bar.close[-1]))
            if event is None:
                return 100000000001.0 if plan.side == "buy" else 0.0000000001
            return event.fill_price

        return fill

    def submit(ctx: ExecContext, plan: OrderPlan) -> None:
        if not plan.eligible or plan.trigger is None:
            return
        if plan.side == "buy":
            ctx.buy_shares = MAX_AFFORDABLE_REQUEST
            ctx.buy_fill_price = conditional_fill(plan)
            ctx.buy_limit_price = Decimal("100000000000")
        else:
            ctx.sell_all_shares()
            ctx.sell_fill_price = conditional_fill(plan)
            ctx.sell_limit_price = Decimal("0.000000001")

    def execute(ctx: ExecContext) -> None:
        current = pd.Timestamp(ctx.dt).normalize()
        if current == pre_start:
            submit(ctx, build_order_plan(row_by_date[first_date], spec, is_long=False))
            return
        following = next_date.get(current)
        if following is not None:
            submit(ctx, build_order_plan(row_by_date[following], spec, is_long=ctx.long_pos() is not None))

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = StrategyConfig(
        initial_cash=float(initial_cash), fee_mode=None, fee_amount=0,
        enable_fractional_shares=True, round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY, max_long_positions=1,
        buy_delay=1, sell_delay=1, exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.OPEN, exit_sell_fill_price=PriceType.OPEN,
        bars_per_year=252, return_signals=False, round_test_result=False,
    )
    strategy = Strategy(
        engine, pre_start.date().isoformat(), pd.Timestamp(engine.iloc[-1]["date"]).date().isoformat(), config
    )
    strategy.add_execution(execute, symbol)
    return PyBrokerResult(
        pybroker_result=strategy.backtest(calc_bootstrap=False, disable_parallel=True),
        engine_start=pre_start,
    )
