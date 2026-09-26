"""Pruned StochRSI accumulation ledger used by the corrected 1/2/4 study."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantkit.stochrsi_scaled_pools import ScaledPoolResult


@dataclass(frozen=True)
class PrunedAccumulationSpec:
    periods: tuple[int, int] = (42, 100)
    normal_low_threshold: float = 0.20
    normal_low_cash_fraction: float = 0.05
    normal_low_position_cap: float = 0.15
    extreme_short_threshold: float = 0.01
    extreme_long_threshold: float = 0.05
    extreme_low_cash_fraction: float = 0.20
    extreme_low_position_cap: float = 0.30
    recovery_threshold: float = 0.20
    short_recovery_cash_fraction: float = 0.40
    long_recovery_cash_fraction: float = 0.50
    pool_a_arm_threshold: float = 0.80
    pool_a_daily_add: float = 0.01
    pool_a_exit_threshold: float = 0.50
    deep_failure_exit_threshold: float = 0.20

    def __post_init__(self) -> None:
        if self.periods != (42, 100):
            raise ValueError("The corrected experiment freezes periods at 42 and 100")
        fractions = (
            self.normal_low_cash_fraction,
            self.extreme_low_cash_fraction,
            self.short_recovery_cash_fraction,
            self.long_recovery_cash_fraction,
            self.pool_a_daily_add,
        )
        if any(not 0 < value <= 1 for value in fractions):
            raise ValueError("Cash and pool fractions must be in (0, 1]")
        if not 0 < self.normal_low_position_cap < self.extreme_low_position_cap <= 1:
            raise ValueError("Position caps must be ordered inside (0, 1]")
        if not 0 <= self.deep_failure_exit_threshold < self.pool_a_exit_threshold < self.pool_a_arm_threshold <= 1:
            raise ValueError("Sell thresholds must satisfy deep < Pool A exit < Pool A arm")


def _empty_frame(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _strict_upcross(prior: float, current: float, threshold: float) -> bool:
    return bool(np.isfinite(prior) and np.isfinite(current) and prior <= threshold < current)


def _strict_downcross(prior: float, current: float, threshold: float) -> bool:
    return bool(np.isfinite(prior) and np.isfinite(current) and prior >= threshold > current)


def run_reference_pruned_accumulation(
    prepared: pd.DataFrame,
    spec: PrunedAccumulationSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ScaledPoolResult:
    """Run the independent same-Close FIFO ledger for the pruned mother strategy."""

    start, end = pd.Timestamp(analysis_start), pd.Timestamp(analysis_end)
    analysis = prepared[prepared["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Analysis boundaries must be available sessions")
    short_period, long_period = spec.periods
    short_column, long_column = f"stochrsi_{short_period}", f"stochrsi_{long_period}"
    prior_short_column = f"prior_stochrsi_{short_period}"
    prior_long_column = f"prior_stochrsi_{long_period}"
    required = {short_column, long_column, prior_short_column, prior_long_column}
    missing = required - set(analysis.columns)
    if missing:
        raise ValueError(f"Prepared data is missing indicators: {sorted(missing)}")

    symbol = str(analysis.iloc[0]["symbol"])
    cash, shares, pool_a = float(initial_cash), 0.0, 0.0
    mechanism_a_armed = False
    lots: list[dict[str, object]] = []
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    event_records: list[dict[str, object]] = []

    def buy(date: pd.Timestamp, amount: float, price: float, signal: str) -> float:
        nonlocal cash, shares
        amount = min(max(float(amount), 0.0), cash)
        if amount <= 1e-12:
            return 0.0
        quantity = amount / price
        cash -= amount
        shares += quantity
        lots.append({
            "entry_date": date,
            "entry_price": price,
            "shares": quantity,
            "entry_signal": signal,
        })
        order_records.append({
            "symbol": symbol,
            "type": "buy",
            "date": date,
            "shares": quantity,
            "fill_price": price,
            "raw_fill_price": price,
            "primary_signal": signal,
            "fill_source": "same_close",
            "notional": amount,
        })
        return amount

    def capped_buy_amount(price: float, cash_fraction: float, position_cap: float) -> float:
        equity = cash + shares * price
        remaining_cap = max(position_cap * equity - shares * price, 0.0)
        return min(cash * cash_fraction, remaining_cap, cash)

    def sell(date: pd.Timestamp, quantity: float, price: float, signal: str) -> float:
        nonlocal cash, shares
        quantity = min(max(float(quantity), 0.0), shares)
        if quantity <= 1e-12:
            return 0.0
        remaining = quantity
        while remaining > 1e-10:
            if not lots:
                raise AssertionError("FIFO lots exhausted before shares")
            lot = lots[0]
            taken = min(remaining, float(lot["shares"]))
            entry_price = float(lot["entry_price"])
            trade_records.append({
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
        cash += quantity * price
        shares -= quantity
        if shares <= 1e-10:
            shares = 0.0
            lots.clear()
        order_records.append({
            "symbol": symbol,
            "type": "sell",
            "date": date,
            "shares": quantity + (1e-12 if shares == 0.0 else 0.0),
            "fill_price": price,
            "raw_fill_price": price,
            "primary_signal": signal,
            "fill_source": "same_close",
            "notional": quantity * price,
        })
        return quantity

    for row in analysis.itertuples(index=False):
        date, close = pd.Timestamp(row.date), float(row.close)
        short = float(getattr(row, short_column))
        long = float(getattr(row, long_column))
        prior_short = float(getattr(row, prior_short_column))
        prior_long = float(getattr(row, prior_long_column))
        short_up = _strict_upcross(prior_short, short, spec.recovery_threshold)
        long_up = _strict_upcross(prior_long, long, spec.recovery_threshold)
        pool_exit = mechanism_a_armed and _strict_downcross(
            prior_long, long, spec.pool_a_exit_threshold
        )
        deep_exit = _strict_downcross(prior_long, long, spec.deep_failure_exit_threshold)
        pretrade_equity = cash + shares * close
        pretrade_weight = shares * close / pretrade_equity if pretrade_equity > 0 else 0.0
        action = "NONE"
        sale_happened = False

        if deep_exit:
            sold = sell(date, shares, close, "SELL_DEEP_FAILURE_S100_DOWNCROSS_020")
            sale_happened = sold > 0
            pool_a = 0.0
            mechanism_a_armed = False
            action = "SELL_DEEP_FAILURE" if sale_happened else "CLEAR_DEEP_FAILURE_EMPTY"
        elif pool_exit:
            sold = sell(date, pool_a, close, "SELL_POOL_A_S100_DOWNCROSS_050")
            sale_happened = sold > 0
            pool_a = 0.0
            mechanism_a_armed = False
            action = "SELL_POOL_A" if sale_happened else "CLEAR_POOL_A_EMPTY"

        if not sale_happened:
            extreme_low = (
                (short < spec.extreme_short_threshold and long < spec.extreme_long_threshold)
                or (short < spec.extreme_long_threshold and long < spec.extreme_short_threshold)
            )
            normal_low = short < spec.normal_low_threshold and long < spec.normal_low_threshold
            if extreme_low:
                amount = capped_buy_amount(
                    close, spec.extreme_low_cash_fraction, spec.extreme_low_position_cap
                )
                if buy(date, amount, close, "BUY_EXTREME_LOW_CAPPED_030") > 0:
                    action = "BUY_EXTREME_LOW"
            elif normal_low:
                amount = capped_buy_amount(
                    close, spec.normal_low_cash_fraction, spec.normal_low_position_cap
                )
                if buy(date, amount, close, "BUY_NORMAL_LOW_CAPPED_015") > 0:
                    action = "BUY_NORMAL_LOW"

            recovery_signal = ""
            recovery_fraction = 0.0
            if long_up:
                recovery_signal = "BUY_LONG_RECOVERY_S100_UPCROSS_020"
                recovery_fraction = spec.long_recovery_cash_fraction
            elif short_up and long > spec.recovery_threshold:
                recovery_signal = "BUY_SHORT_RECOVERY_S42_UPCROSS_020"
                recovery_fraction = spec.short_recovery_cash_fraction
            if recovery_signal and buy(date, cash * recovery_fraction, close, recovery_signal) > 0:
                action = "BUY_LONG_RECOVERY" if long_up else "BUY_SHORT_RECOVERY"

        # Crossing day is included.  Pool A records 1% of the current
        # post-trade share position so a same-Close recovery purchase is not omitted.
        if not sale_happened and np.isfinite(long) and long > spec.pool_a_arm_threshold:
            mechanism_a_armed = True
            previous_pool = pool_a
            pool_a = min(shares, pool_a + spec.pool_a_daily_add * shares)
            if pool_a > previous_pool + 1e-12:
                action = f"{action}+ADD_POOL_A" if action != "NONE" else "ADD_POOL_A"

        if pool_a > shares + 1e-8 or min(pool_a, cash, shares) < -1e-8:
            raise AssertionError(f"Invalid pruned account state on {date.date()}")
        equity = cash + shares * close
        weight = shares * close / equity if equity > 0 else 0.0
        daily_records.append({
            "date": date,
            "symbol": symbol,
            "close": close,
            "cash": cash,
            "shares": shares,
            "equity": equity,
            "is_long": int(shares > 0),
            "external_contribution": 0.0,
            "position_value": shares * close,
            "qqq_weight": weight,
            "pool_a_shares": pool_a,
            "pool_a_value": pool_a * close,
            "mechanism_a_armed": int(mechanism_a_armed),
        })
        event_records.append({
            "date": date,
            "stochrsi_42": short,
            "stochrsi_100": long,
            "prior_stochrsi_42": prior_short,
            "prior_stochrsi_100": prior_long,
            "short_upcross_020": short_up,
            "long_upcross_020": long_up,
            "pool_a_downcross_050": pool_exit,
            "deep_failure_downcross_020": deep_exit,
            "pretrade_weight": pretrade_weight,
            "posttrade_weight": weight,
            "sale_happened": sale_happened,
            "action": action,
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
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
        contributions=_empty_frame(("date", "amount", "reason")),
        events=pd.DataFrame(event_records),
    )
