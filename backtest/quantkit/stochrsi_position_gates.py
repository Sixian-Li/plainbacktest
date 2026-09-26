"""Independent ledgers and metrics for StochRSI position gates."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantkit.stochrsi_scaled_pools import ScaledPoolResult


def holding_period_cagr_pct(
    calendar_cagr_pct: float,
    holding_time_pct: float,
) -> float | None:
    """Geometrically compress calendar CAGR into sessions with any position.

    This is a descriptive intensity metric under a zero cash-return assumption,
    not a separately simulated subperiod return or IRR.
    """

    if not np.isfinite(calendar_cagr_pct) or not np.isfinite(holding_time_pct):
        return None
    if holding_time_pct <= 0:
        return None
    if holding_time_pct > 100:
        raise ValueError("holding_time_pct must not exceed 100")
    annual_growth = 1.0 + float(calendar_cagr_pct) / 100.0
    if annual_growth < 0:
        raise ValueError("calendar CAGR cannot be below -100%")
    compressed = annual_growth ** (100.0 / float(holding_time_pct)) - 1.0
    return float(compressed * 100.0)


@dataclass(frozen=True)
class FourStateSpec:
    short_period: int = 42
    long_period: int = 100
    on_threshold: float = 0.20
    off_threshold: float = 0.10
    both_off_weight: float = 0.00
    short_only_weight: float = 0.40
    long_only_weight: float = 0.60
    both_on_weight: float = 1.00

    def __post_init__(self) -> None:
        if not 0 <= self.off_threshold < self.on_threshold <= 1:
            raise ValueError("thresholds must satisfy 0 <= off < on <= 1")
        weights = (
            self.both_off_weight,
            self.short_only_weight,
            self.long_only_weight,
            self.both_on_weight,
        )
        if any(not 0 <= value <= 1 for value in weights):
            raise ValueError("target weights must be in [0, 1]")


def _empty_frame(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _target_weight(short_on: bool, long_on: bool, spec: FourStateSpec) -> float:
    if short_on and long_on:
        return spec.both_on_weight
    if short_on:
        return spec.short_only_weight
    if long_on:
        return spec.long_only_weight
    return spec.both_off_weight


def _fifo_sell(
    lots: list[dict[str, object]],
    quantity: float,
    *,
    symbol: str,
    date: pd.Timestamp,
    price: float,
    signal: str,
    trades: list[dict[str, object]],
) -> None:
    remaining = float(quantity)
    while remaining > 1e-10:
        if not lots:
            raise AssertionError("FIFO lots exhausted before shares")
        lot = lots[0]
        taken = min(remaining, float(lot["shares"]))
        entry_price = float(lot["entry_price"])
        trades.append({
            "symbol": symbol,
            "entry_date": lot["entry_date"],
            "entry_price": entry_price,
            "shares": taken,
            "entry_signal": lot["entry_signal"],
            "exit_date": date,
            "exit_price": price,
            "exit_signal": signal,
            "pnl": (price - entry_price) * taken,
            "return_pct": (price / entry_price - 1.0) * 100.0,
        })
        lot["shares"] = float(lot["shares"]) - taken
        remaining -= taken
        if float(lot["shares"]) <= 1e-10:
            lots.pop(0)


def run_reference_four_state(
    prepared: pd.DataFrame,
    spec: FourStateSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ScaledPoolResult:
    """Rebalance at the confirming Close to one of four frozen target weights."""

    start, end = pd.Timestamp(analysis_start), pd.Timestamp(analysis_end)
    rows = prepared[prepared["date"].le(end)].copy().reset_index(drop=True)
    analysis = rows[rows["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Analysis boundaries must be available sessions")
    symbol = str(analysis.iloc[0]["symbol"])
    short_column, long_column = f"stochrsi_{spec.short_period}", f"stochrsi_{spec.long_period}"
    prior_short_column = f"prior_stochrsi_{spec.short_period}"
    prior_long_column = f"prior_stochrsi_{spec.long_period}"
    required = {short_column, long_column, prior_short_column, prior_long_column}
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f"Prepared data is missing indicators: {sorted(missing)}")

    cash, shares = float(initial_cash), 0.0
    short_on = long_on = False
    lots: list[dict[str, object]] = []
    daily: list[dict[str, object]] = []
    orders: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    events: list[dict[str, object]] = []

    for row in rows.itertuples(index=False):
        date = pd.Timestamp(row.date)
        short = float(getattr(row, short_column))
        long = float(getattr(row, long_column))
        prior_short = float(getattr(row, prior_short_column))
        prior_long = float(getattr(row, prior_long_column))
        short_up = np.isfinite(prior_short) and np.isfinite(short) and prior_short <= spec.on_threshold < short
        short_down = np.isfinite(prior_short) and np.isfinite(short) and prior_short >= spec.off_threshold > short
        long_up = np.isfinite(prior_long) and np.isfinite(long) and prior_long <= spec.on_threshold < long
        long_down = np.isfinite(prior_long) and np.isfinite(long) and prior_long >= spec.off_threshold > long
        if short_up:
            short_on = True
        elif short_down:
            short_on = False
        if long_up:
            long_on = True
        elif long_down:
            long_on = False
        if date < start:
            continue

        close = float(row.close)
        target = _target_weight(short_on, long_on, spec)
        equity = cash + shares * close
        desired_value = target * equity
        current_value = shares * close
        difference = desired_value - current_value
        action = "HOLD_TARGET"
        if difference > 1e-8:
            notional = min(difference, cash)
            quantity = notional / close
            cash -= notional
            shares += quantity
            lots.append({
                "entry_date": date,
                "entry_price": close,
                "shares": quantity,
                "entry_signal": "FOUR_STATE_TARGET_INCREASE",
            })
            orders.append({
                "symbol": symbol, "type": "buy", "date": date, "shares": quantity,
                "fill_price": close, "raw_fill_price": close,
                "primary_signal": "FOUR_STATE_TARGET_INCREASE",
                "fill_source": "same_close", "notional": notional,
            })
            action = "BUY_TO_TARGET"
        elif difference < -1e-8:
            quantity = min(-difference / close, shares)
            _fifo_sell(
                lots, quantity, symbol=symbol, date=date, price=close,
                signal="FOUR_STATE_TARGET_DECREASE", trades=trades,
            )
            cash += quantity * close
            shares -= quantity
            if shares <= 1e-10:
                shares = 0.0
                lots.clear()
            # PyBroker accumulates fractional fills with Decimal while this
            # independent ledger uses float.  On an all-out target, a tiny
            # over-request lets the broker cap at its exact position instead
            # of leaving a sub-femtoshare residue for a later order.
            instruction_quantity = quantity + 1e-12 if target == 0.0 else quantity
            orders.append({
                "symbol": symbol, "type": "sell", "date": date, "shares": instruction_quantity,
                "fill_price": close, "raw_fill_price": close,
                "primary_signal": "FOUR_STATE_TARGET_DECREASE",
                "fill_source": "same_close", "notional": quantity * close,
            })
            action = "SELL_TO_TARGET"
        equity = cash + shares * close
        weight = shares * close / equity if equity > 0 else 0.0
        daily.append({
            "date": date, "symbol": symbol, "close": close, "cash": cash,
            "shares": shares, "equity": equity, "is_long": int(shares > 0),
            "external_contribution": 0.0, "position_value": shares * close,
            "qqq_weight": weight, "short_state_on": int(short_on),
            "long_state_on": int(long_on), "target_weight": target,
        })
        events.append({
            "date": date, "stochrsi_42": short, "stochrsi_100": long,
            "prior_stochrsi_42": prior_short, "prior_stochrsi_100": prior_long,
            "short_upcross": short_up, "short_downcross": short_down,
            "long_upcross": long_up, "long_downcross": long_down,
            "short_state_on": short_on, "long_state_on": long_on,
            "target_weight": target, "action": action,
        })

    order_columns = (
        "symbol", "type", "date", "shares", "fill_price", "raw_fill_price",
        "primary_signal", "fill_source", "notional",
    )
    trade_columns = (
        "symbol", "entry_date", "entry_price", "shares", "entry_signal",
        "exit_date", "exit_price", "exit_signal", "pnl", "return_pct",
    )
    return ScaledPoolResult(
        daily=pd.DataFrame(daily),
        orders=pd.DataFrame(orders, columns=order_columns),
        trades=pd.DataFrame(trades, columns=trade_columns),
        contributions=_empty_frame(("date", "amount", "reason")),
        events=pd.DataFrame(events),
    )


def run_reference_position_gate(
    mother_daily: pd.DataFrame,
    threshold: float,
    *,
    initial_cash: float = 100_000.0,
    mother_id: str,
) -> ScaledPoolResult:
    """Hold QQQ iff the mother's completed post-trade weight is strictly above a gate."""

    if not 0 < threshold < 1:
        raise ValueError("threshold must be in (0, 1)")
    required = {"date", "symbol", "close", "qqq_weight"}
    missing = required - set(mother_daily.columns)
    if missing:
        raise ValueError(f"Mother daily state is missing columns: {sorted(missing)}")
    cash, shares = float(initial_cash), 0.0
    symbol = str(mother_daily.iloc[0]["symbol"])
    lots: list[dict[str, object]] = []
    daily: list[dict[str, object]] = []
    orders: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for row in mother_daily.itertuples(index=False):
        date, close = pd.Timestamp(row.date), float(row.close)
        mother_weight = float(row.qqq_weight)
        target_long = mother_weight > threshold
        action = "HOLD_LONG" if shares > 0 else "HOLD_CASH"
        if target_long and shares <= 1e-12:
            quantity = cash / close
            notional = cash
            cash = 0.0
            shares = quantity
            signal = f"{mother_id}_WEIGHT_GT_{int(threshold * 100)}"
            lots.append({
                "entry_date": date, "entry_price": close, "shares": quantity,
                "entry_signal": signal,
            })
            orders.append({
                "symbol": symbol, "type": "buy", "date": date, "shares": quantity,
                "fill_price": close, "raw_fill_price": close, "primary_signal": signal,
                "fill_source": "same_close", "notional": notional,
            })
            action = "ENTER_LONG"
        elif not target_long and shares > 1e-12:
            quantity = shares
            signal = f"{mother_id}_WEIGHT_NOT_GT_{int(threshold * 100)}"
            _fifo_sell(
                lots, quantity, symbol=symbol, date=date, price=close,
                signal=signal, trades=trades,
            )
            notional = quantity * close
            cash += notional
            shares = 0.0
            lots.clear()
            orders.append({
                "symbol": symbol, "type": "sell", "date": date, "shares": quantity,
                "fill_price": close, "raw_fill_price": close, "primary_signal": signal,
                "fill_source": "same_close", "notional": notional,
            })
            action = "EXIT_TO_CASH"
        equity = cash + shares * close
        daily.append({
            "date": date, "symbol": symbol, "close": close, "cash": cash,
            "shares": shares, "equity": equity, "is_long": int(shares > 0),
            "external_contribution": 0.0, "position_value": shares * close,
            "qqq_weight": float(shares > 0), "mother_qqq_weight": mother_weight,
            "gate_threshold": threshold,
        })
        events.append({
            "date": date, "mother_id": mother_id, "mother_qqq_weight": mother_weight,
            "gate_threshold": threshold, "target_long": target_long, "action": action,
        })

    order_columns = (
        "symbol", "type", "date", "shares", "fill_price", "raw_fill_price",
        "primary_signal", "fill_source", "notional",
    )
    trade_columns = (
        "symbol", "entry_date", "entry_price", "shares", "entry_signal",
        "exit_date", "exit_price", "exit_signal", "pnl", "return_pct",
    )
    return ScaledPoolResult(
        daily=pd.DataFrame(daily),
        orders=pd.DataFrame(orders, columns=order_columns),
        trades=pd.DataFrame(trades, columns=trade_columns),
        contributions=_empty_frame(("date", "amount", "reason")),
        events=pd.DataFrame(events),
    )


def run_reference_and_position_gate(
    mother_daily_by_signal: dict[str, pd.DataFrame],
    threshold: float,
    *,
    initial_cash: float = 100_000.0,
    composite_id: str,
) -> ScaledPoolResult:
    """Hold QQQ only when every named mother is strictly above one threshold."""

    if len(mother_daily_by_signal) < 2:
        raise ValueError("An AND gate requires at least two source signals")
    required = {"date", "symbol", "close", "qqq_weight"}
    normalized: dict[str, pd.DataFrame] = {}
    for signal_id, frame in mother_daily_by_signal.items():
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Source {signal_id} is missing columns: {sorted(missing)}")
        rows = frame[["date", "symbol", "close", "qqq_weight"]].copy().reset_index(drop=True)
        rows["date"] = pd.to_datetime(rows["date"])
        normalized[signal_id] = rows

    first_id = next(iter(normalized))
    first = normalized[first_id]
    for signal_id, rows in normalized.items():
        if len(rows) != len(first) or not rows["date"].equals(first["date"]):
            raise ValueError(f"Source {signal_id} dates do not align with {first_id}")
        if not rows["symbol"].equals(first["symbol"]):
            raise ValueError(f"Source {signal_id} symbols do not align with {first_id}")
        if not np.allclose(rows["close"], first["close"], rtol=0.0, atol=1e-12):
            raise ValueError(f"Source {signal_id} closes do not align with {first_id}")

    source_weights = {
        signal_id: rows["qqq_weight"].to_numpy(float)
        for signal_id, rows in normalized.items()
    }
    minimum_weight = np.minimum.reduce(list(source_weights.values()))
    synthetic = first.copy()
    synthetic["qqq_weight"] = minimum_weight
    result = run_reference_position_gate(
        synthetic,
        threshold,
        initial_cash=initial_cash,
        mother_id=composite_id,
    )
    for signal_id, weights in source_weights.items():
        result.daily[f"{signal_id}_mother_weight"] = weights
        result.daily[f"{signal_id}_signal"] = (weights > threshold).astype(int)
        result.events[f"{signal_id}_mother_weight"] = weights
        result.events[f"{signal_id}_signal"] = (weights > threshold).astype(int)
    result.daily = result.daily.rename(columns={"mother_qqq_weight": "minimum_source_weight"})
    result.events = result.events.rename(columns={"mother_qqq_weight": "minimum_source_weight"})
    result.events["required_signals"] = "+".join(normalized)
    return result
