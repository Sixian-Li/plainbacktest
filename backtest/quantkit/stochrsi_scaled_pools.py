"""State machine and dual-ledger helpers for scaled StochRSI accumulation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.context import ExecContext

from quantkit.dual_stochrsi_timing import (
    TimingSpec,
    _stoch_trigger_price,
    prepare_dual_stochrsi_data,
)


@dataclass(frozen=True)
class ScaledPoolSpec:
    periods: tuple[int, int] = (42, 100)
    b1_threshold: float = 0.20
    b1_cash_fraction: float = 0.05
    b1_floor_fraction: float = 0.25
    b2_cash_fractions: tuple[float, ...] = (0.20, 0.30, 0.40, 0.30)
    b2_floor_fraction: float = 0.60
    a_arm: float = 0.80
    a_daily_add: float = 0.01
    a_zone_low: float = 0.50
    a_zone_high: float = 0.80
    a_daily_sale: float = 0.20
    a_remaining_sale: float = 0.65
    fast_prior: float = 0.60
    fast_current: float = 0.30
    b_crowded_weight: float = 0.75
    b_arm: float = 0.80
    b_daily_add: float = 0.10
    b_downcross: float = 0.70
    b_sale_cap: float = 0.55
    enable_pool_b: bool = True
    enable_a_daily_decay: bool = True
    enable_b1_floor: bool = True
    enable_b2_floor: bool = True
    deferred_buy_cross: float | None = None
    enable_sparse_42_recovery_buy: bool = False
    enable_sparse_100_recovery_buy: bool = False
    sparse_weight_threshold: float = 0.15
    sparse_cross_threshold: float = 0.20
    sparse_42_cash_fraction: float = 0.40
    sparse_100_cash_fraction: float = 0.50


@dataclass
class ScaledPoolResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    contributions: pd.DataFrame
    events: pd.DataFrame


def prepare_scaled_pool_data(data: pd.DataFrame, spec: ScaledPoolSpec) -> pd.DataFrame:
    rows = prepare_dual_stochrsi_data(data, TimingSpec("LEVEL", periods=spec.periods))
    rows["fast_drop_trigger_100_030"] = rows.apply(
        _stoch_trigger_price, axis=1, args=(100, spec.fast_current)
    )
    return rows


def _empty_frame(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def run_reference_scaled_pools(
    prepared: pd.DataFrame,
    spec: ScaledPoolSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float = 100_000.0,
) -> ScaledPoolResult:
    start, end = pd.Timestamp(analysis_start), pd.Timestamp(analysis_end)
    analysis = prepared[prepared["date"].between(start, end)].copy().reset_index(drop=True)
    if analysis.empty or analysis.iloc[0]["date"] != start or analysis.iloc[-1]["date"] != end:
        raise ValueError("Analysis boundaries must be available sessions")
    symbol = str(analysis.iloc[0]["symbol"])
    cash, shares = float(initial_cash), 0.0
    virtual_cash = float(initial_cash)
    pending_buy = pending_contribution = 0.0
    pool_a = pool_b = 0.0
    mechanism_a_armed = False
    buy_a: float | None = None
    buy_b: float | None = None
    b2_count = 0
    sparse_42_armed = False
    lots: list[dict[str, object]] = []
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    contribution_records: list[dict[str, object]] = []
    event_records: list[dict[str, object]] = []

    def reset_buy_cycle(*, cancel_pending: bool = False) -> None:
        nonlocal buy_a, buy_b, b2_count, pending_buy, pending_contribution, virtual_cash
        nonlocal sparse_42_armed
        buy_a = None
        buy_b = None
        b2_count = 0
        sparse_42_armed = False
        if cancel_pending:
            pending_buy = 0.0
            pending_contribution = 0.0
            virtual_cash = cash

    def contribute(date: pd.Timestamp, amount: float, reason: str) -> None:
        nonlocal cash
        if amount <= 1e-12:
            return
        cash += amount
        contribution_records.append({"date": date, "amount": amount, "reason": reason})

    def buy(date: pd.Timestamp, amount: float, price: float, signal: str) -> float:
        nonlocal cash, shares
        amount = min(float(amount), cash)
        if amount <= 1e-12:
            return 0.0
        quantity = amount / price
        cash -= amount
        shares += quantity
        lots.append({
            "entry_date": date, "entry_price": price, "shares": quantity,
            "entry_signal": signal,
        })
        order_records.append({
            "symbol": symbol, "type": "buy", "date": date, "shares": quantity,
            "fill_price": price, "raw_fill_price": price, "primary_signal": signal,
            "fill_source": "same_close", "notional": amount,
        })
        return amount

    def sell(date: pd.Timestamp, quantity: float, price: float, signal: str, source: str) -> float:
        nonlocal cash, shares
        quantity = min(float(quantity), shares)
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
                "symbol": symbol, "entry_date": lot["entry_date"],
                "entry_price": entry_price, "shares": taken,
                "entry_signal": lot["entry_signal"], "exit_date": date,
                "exit_price": price, "exit_signal": signal,
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
            "symbol": symbol, "type": "sell", "date": date, "shares": quantity,
            "fill_price": price, "raw_fill_price": price, "primary_signal": signal,
            "fill_source": source, "notional": quantity * price,
        })
        reset_buy_cycle(cancel_pending=True)
        return quantity

    for row in analysis.itertuples(index=False):
        date = pd.Timestamp(row.date)
        close = float(row.close)
        s42, s100 = float(row.stochrsi_42), float(row.stochrsi_100)
        prior42, prior100 = float(row.prior_stochrsi_42), float(row.prior_stochrsi_100)
        pretrade_shares = shares
        pretrade_equity = cash + shares * close
        weight = shares * close / pretrade_equity if pretrade_equity > 0 else 0.0
        action = "NONE"
        sale_happened = False
        fast_drop_sale = False
        sparse_42_downcross = (
            prior42 >= spec.sparse_cross_threshold > s42
        )
        sparse_42_upcross = (
            prior42 <= spec.sparse_cross_threshold < s42
        )
        sparse_100_upcross = (
            prior100 <= spec.sparse_cross_threshold < s100
        )
        if spec.enable_sparse_42_recovery_buy and sparse_42_downcross:
            sparse_42_armed = True

        if mechanism_a_armed:
            fast_drop_sale = prior100 > spec.fast_prior and s100 < spec.fast_current
            normal_downcross = prior100 >= spec.a_zone_low and s100 < spec.a_zone_low
            if fast_drop_sale or normal_downcross:
                price = (
                    float(row.fast_drop_trigger_100_030) if fast_drop_sale else close
                )
                if not np.isfinite(price) or price <= 0:
                    raise ValueError(f"Invalid fast-drop theoretical fill on {date.date()}: {price}")
                quantity = pool_a + spec.a_remaining_sale * max(shares - pool_a, 0.0)
                sold = sell(
                    date, quantity, price,
                    "SELL_A_FAST_030" if fast_drop_sale else "SELL_A_DOWNCROSS_050",
                    "theoretical_stochrsi_030_no_ohlc_gate" if fast_drop_sale else "same_close",
                )
                pool_a = 0.0
                mechanism_a_armed = False
                sale_happened = sold > 0
                action = "SELL_A_FAST_030" if fast_drop_sale else "SELL_A_DOWNCROSS_050"
            elif spec.enable_a_daily_decay and spec.a_zone_low <= s100 <= spec.a_zone_high:
                quantity = pool_a * spec.a_daily_sale
                sold = sell(date, quantity, close, "SELL_A_DAILY_DECAY", "same_close")
                pool_a = max(pool_a - sold, 0.0)
                sale_happened = sold > 0
                action = "SELL_A_DAILY_DECAY" if sale_happened else "A_ZONE_EMPTY"

        if not sale_happened and s100 > spec.a_arm:
            if not mechanism_a_armed:
                mechanism_a_armed = True
                action = "ARM_A"
            pool_b = 0.0
            pool_a = min(shares, pool_a + spec.a_daily_add * shares)
            action = "ADD_POOL_A" if shares > 0 else action
        elif spec.enable_pool_b and not mechanism_a_armed and not sale_happened:
            b_downcross = prior42 >= spec.b_downcross and s42 < spec.b_downcross
            if b_downcross:
                quantity = min(pool_b, spec.b_sale_cap * shares)
                sold = sell(date, quantity, close, "SELL_B_DOWNCROSS_070", "same_close")
                pool_b = 0.0
                sale_happened = sold > 0
                action = "SELL_B_DOWNCROSS_070" if sale_happened else "CLEAR_POOL_B_EMPTY"
            elif weight > spec.b_crowded_weight and s42 > spec.b_arm:
                pool_b = min(shares, pool_b + spec.b_daily_add * max(shares - pool_b, 0.0))
                action = "ADD_POOL_B"

        may_buy = not sale_happened or fast_drop_sale
        deferred_cross = (
            spec.deferred_buy_cross is not None
            and prior100 <= spec.deferred_buy_cross < s100
        )
        if may_buy and deferred_cross and pending_buy > 1e-12:
            contribute(
                date, pending_contribution,
                "DEFERRED_BUY_MINIMUM_SHORTFALL",
            )
            actual = buy(date, pending_buy, close, "BUY_DEFERRED_S100_CROSS")
            if abs(actual - pending_buy) > 1e-7:
                raise AssertionError("Deferred buy did not consume its frozen pending notional")
            pending_buy = 0.0
            pending_contribution = 0.0
            virtual_cash = cash
            action = f"{action}+BUY_DEFERRED" if action != "NONE" else "BUY_DEFERRED"

        if may_buy:
            b2_signal = (
                (s42 < 0.05 and s100 < 0.01)
                or (s42 < 0.01 and s100 < 0.05)
            )
            b1_signal = s42 < spec.b1_threshold and s100 < spec.b1_threshold
            if b2_signal:
                b2_count += 1
                fraction = spec.b2_cash_fractions[min(b2_count - 1, len(spec.b2_cash_fractions) - 1)]
                sizing_cash = virtual_cash if spec.deferred_buy_cross is not None else cash
                desired = sizing_cash * fraction
                if spec.enable_b2_floor and b2_count >= 5 and buy_b is not None:
                    desired = max(desired, spec.b2_floor_fraction * buy_b)
                shortfall = max(desired - sizing_cash, 0.0)
                if spec.deferred_buy_cross is not None:
                    virtual_cash += shortfall - desired
                    pending_contribution += shortfall
                    pending_buy += desired
                    actual = desired
                else:
                    contribute(date, shortfall, "BUY_B2_MINIMUM_SHORTFALL")
                    actual = buy(date, desired, close, "BUY_B2_EXTREME_LOW")
                if b2_count == 4:
                    buy_b = actual
                if actual > 0:
                    tag = "QUEUE_B2" if spec.deferred_buy_cross is not None else "BUY_B2"
                    action = f"{action}+{tag}" if action != "NONE" else tag
            elif b1_signal:
                sizing_cash = virtual_cash if spec.deferred_buy_cross is not None else cash
                desired = sizing_cash * spec.b1_cash_fraction
                if spec.enable_b1_floor and buy_a is not None:
                    desired = max(desired, spec.b1_floor_fraction * buy_a)
                shortfall = max(desired - sizing_cash, 0.0)
                if spec.deferred_buy_cross is not None:
                    virtual_cash += shortfall - desired
                    pending_contribution += shortfall
                    pending_buy += desired
                    actual = desired
                else:
                    contribute(date, shortfall, "BUY_B1_MINIMUM_SHORTFALL")
                    actual = buy(date, desired, close, "BUY_B1_LOW")
                if buy_a is None and actual > 0:
                    buy_a = actual
                if actual > 0:
                    tag = "QUEUE_B1" if spec.deferred_buy_cross is not None else "BUY_B1"
                    action = f"{action}+{tag}" if action != "NONE" else tag

        sparse_42_recovery = sparse_42_armed and sparse_42_upcross
        if sparse_42_upcross:
            sparse_42_armed = False
        sparse_eligible = may_buy and weight < spec.sparse_weight_threshold
        sparse_signal = ""
        sparse_fraction = 0.0
        if sparse_eligible and spec.enable_sparse_100_recovery_buy and sparse_100_upcross:
            sparse_signal = "BUY_SPARSE_S100_UPCROSS_020"
            sparse_fraction = spec.sparse_100_cash_fraction
        elif (
            sparse_eligible
            and spec.enable_sparse_42_recovery_buy
            and sparse_42_recovery
            and s100 > spec.sparse_cross_threshold
        ):
            sparse_signal = "BUY_SPARSE_S42_RECOVERY_020"
            sparse_fraction = spec.sparse_42_cash_fraction
        if sparse_signal:
            sizing_cash = virtual_cash if spec.deferred_buy_cross is not None else cash
            actual = buy(date, sizing_cash * sparse_fraction, close, sparse_signal)
            if spec.deferred_buy_cross is not None:
                virtual_cash -= actual
            if actual > 0:
                tag = "BUY_SPARSE_100" if sparse_fraction == spec.sparse_100_cash_fraction else "BUY_SPARSE_42"
                action = f"{action}+{tag}" if action != "NONE" else tag

        if pool_a > shares + 1e-8 or pool_b > shares + 1e-8 or min(pool_a, pool_b, cash, shares) < -1e-8:
            raise AssertionError(f"Invalid pool/account state on {date.date()}")
        daily_records.append({
            "date": date, "symbol": symbol, "close": close, "cash": cash,
            "shares": shares, "equity": cash + shares * close,
            "is_long": int(shares > 0), "external_contribution": sum(
                float(item["amount"]) for item in contribution_records if item["date"] == date
            ),
            "pool_a_shares": pool_a, "pool_b_shares": pool_b,
            "pool_a_value": pool_a * close, "pool_b_value": pool_b * close,
            "mechanism_a_armed": int(mechanism_a_armed), "buy_a": buy_a,
            "buy_b": buy_b, "b2_count": b2_count, "qqq_weight": (
                shares * close / (cash + shares * close) if cash + shares * close > 0 else 0.0
            ),
            "position_value": shares * close, "pending_buy": pending_buy,
            "pending_contribution": pending_contribution,
            "sparse_42_armed": int(sparse_42_armed),
        })
        event_records.append({
            "date": date, "stochrsi_42": s42, "stochrsi_100": s100,
            "prior_stochrsi_42": prior42, "prior_stochrsi_100": prior100,
            "action": action, "sale_happened": sale_happened,
            "fast_drop_sale": fast_drop_sale and sale_happened,
            "fast_drop_fill_price": (
                float(row.fast_drop_trigger_100_030) if fast_drop_sale else np.nan
            ),
            "pretrade_shares": pretrade_shares, "pretrade_weight": weight,
            "pending_buy": pending_buy, "deferred_cross": deferred_cross,
            "sparse_42_downcross": sparse_42_downcross,
            "sparse_42_upcross": sparse_42_upcross,
            "sparse_100_upcross": sparse_100_upcross,
            "sparse_42_recovery": sparse_42_recovery,
        })

    order_columns = (
        "symbol", "type", "date", "shares", "fill_price", "raw_fill_price",
        "primary_signal", "fill_source", "notional",
    )
    trade_columns = (
        "symbol", "entry_date", "entry_price", "shares", "entry_signal",
        "exit_date", "exit_price", "exit_signal", "pnl", "return_pct",
    )
    contribution_columns = ("date", "amount", "reason")
    return ScaledPoolResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
        contributions=pd.DataFrame(contribution_records, columns=contribution_columns),
        events=pd.DataFrame(event_records),
    )


def contribution_matched_buy_hold(
    analysis: pd.DataFrame,
    contributions: pd.DataFrame,
    *,
    initial_cash: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    flows = (
        contributions.groupby(pd.to_datetime(contributions["date"]))["amount"].sum().to_dict()
        if not contributions.empty else {}
    )
    shares = 0.0
    daily, orders = [], []
    for index, row in analysis.reset_index(drop=True).iterrows():
        date, close = pd.Timestamp(row["date"]), float(row["close"])
        amount = float(initial_cash) if index == 0 else 0.0
        amount += float(flows.get(date, 0.0))
        if amount > 0:
            quantity = amount / close
            shares += quantity
            orders.append({
                "symbol": str(row["symbol"]), "type": "buy", "date": date,
                "shares": quantity, "fill_price": close, "raw_fill_price": close,
                "primary_signal": "INITIAL_BUY_HOLD" if index == 0 else "MATCHED_CONTRIBUTION",
                "fill_source": "same_close", "notional": amount,
            })
        daily.append({
            "date": date, "symbol": str(row["symbol"]), "close": close,
            "cash": 0.0, "shares": shares, "equity": shares * close,
            "is_long": 1, "external_contribution": float(flows.get(date, 0.0)),
        })
    return pd.DataFrame(daily), pd.DataFrame(orders)


def cash_flow_adjusted_metrics(
    daily: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_cash: float,
) -> dict[str, float | int | str]:
    state = daily.copy().sort_values("date")
    state["date"] = pd.to_datetime(state["date"])
    equity = state["equity"].to_numpy(float)
    flows = state.get("external_contribution", pd.Series(0.0, index=state.index)).to_numpy(float)
    returns = np.zeros(len(state), dtype=float)
    if len(state) > 1:
        returns[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1.0
    unitized = np.cumprod(1.0 + returns)
    elapsed_days = max((state.iloc[-1]["date"] - state.iloc[0]["date"]).days, 1)
    years = elapsed_days / 365.2425
    cagr = unitized[-1] ** (1.0 / years) - 1.0
    std = float(np.std(returns, ddof=1))
    sharpe = float(np.mean(returns) / std * math.sqrt(252)) if std > 0 else float("nan")
    downside = np.minimum(returns, 0.0)
    downside_dev = float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(252))
    sortino = float(np.mean(returns) * 252 / downside_dev) if downside_dev > 0 else float("nan")
    drawdown = unitized / np.maximum.accumulate(unitized) - 1.0
    total_external = float(np.sum(flows))
    total_contributed = float(initial_cash + total_external)
    cashflow_dates = [state.iloc[0]["date"]] + [
        row.date for row in state.itertuples() if float(row.external_contribution) > 0
    ] + [state.iloc[-1]["date"]]
    cashflow_values = [-float(initial_cash)] + [
        -float(row.external_contribution) for row in state.itertuples()
        if float(row.external_contribution) > 0
    ] + [float(equity[-1])]

    def npv(rate: float) -> float:
        origin = cashflow_dates[0]
        return sum(
            value / (1.0 + rate) ** ((date - origin).days / 365.2425)
            for date, value in zip(cashflow_dates, cashflow_values)
        )

    low, high = -0.999999, 1.0
    low_value, high_value = npv(low), npv(high)
    while low_value * high_value > 0 and high < 1_000_000.0:
        high *= 2.0
        high_value = npv(high)
    if low_value * high_value > 0:
        xirr = float("nan")
    else:
        for _ in range(200):
            middle = (low + high) / 2.0
            middle_value = npv(middle)
            if abs(middle_value) < 1e-10:
                low = high = middle
                break
            if low_value * middle_value <= 0:
                high = middle
            else:
                low, low_value = middle, middle_value
        xirr = float((low + high) / 2.0)
    order_notional = float((orders["shares"] * orders["fill_price"]).sum()) if not orders.empty else 0.0
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    losses = int((trades["pnl"] < 0).sum()) if not trades.empty else 0
    return {
        "start": state.iloc[0]["date"].date().isoformat(),
        "end": state.iloc[-1]["date"].date().isoformat(), "bars": len(state),
        "initial_cash": float(initial_cash), "external_contributions": total_external,
        "total_contributed_capital": total_contributed, "final_equity": float(equity[-1]),
        "net_profit": float(equity[-1] - total_contributed),
        "cash_flow_adjusted_total_return_pct": float((unitized[-1] - 1.0) * 100),
        "cagr_pct": float(cagr * 100), "annual_volatility_pct": float(std * math.sqrt(252) * 100),
        "sharpe": sharpe, "sortino": sortino,
        "max_drawdown_pct": float(np.min(drawdown) * 100), "xirr_pct": xirr * 100,
        "order_count": len(orders), "closed_trade_lot_count": len(trades),
        "winning_trade_lots": wins, "losing_trade_lots": losses,
        "turnover_multiple": order_notional / float(np.mean(equity)),
        "exposure_pct": float(np.mean(state["is_long"]) * 100),
    }


def run_compiled_pybroker(
    prepared: pd.DataFrame,
    reference: ScaledPoolResult,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_cash: float,
):
    start, end = pd.Timestamp(analysis_start), pd.Timestamp(analysis_end)
    start_indexes = prepared.index[prepared["date"].eq(start)].tolist()
    end_indexes = prepared.index[prepared["date"].eq(end)].tolist()
    if len(start_indexes) != 1 or len(end_indexes) != 1 or start_indexes[0] == 0:
        raise ValueError("Compiled replay needs unique boundaries and one prior bar")
    engine = prepared.iloc[start_indexes[0] - 1 : end_indexes[0] + 1].copy().reset_index(drop=True)
    orders = reference.orders.reset_index(drop=True)
    order_groups = {
        pd.Timestamp(date): group.to_dict("records")
        for date, group in orders.groupby(pd.to_datetime(orders["date"]), sort=False)
    }
    expanded: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    for row in engine.to_dict("records"):
        date = pd.Timestamp(row["date"])
        day_orders = order_groups.get(date, [])
        if len(day_orders) > 2:
            raise AssertionError("Compiled replay supports at most two same-day orders")
        if len(day_orders) == 2:
            synthetic = dict(row)
            synthetic_date = date - timedelta(hours=1)
            synthetic["date"] = synthetic_date
            for field in ("open", "high", "low", "close"):
                synthetic[field] = float(day_orders[0]["fill_price"])
            expanded.append(synthetic)
            expanded.append(row)
            order_rows.extend([
                {**day_orders[0], "engine_date": synthetic_date},
                {**day_orders[1], "engine_date": date},
            ])
        else:
            expanded.append(row)
            order_rows.extend({**order, "engine_date": date} for order in day_orders)
    engine = pd.DataFrame(expanded).sort_values("date").reset_index(drop=True)
    engine_dates = [pd.Timestamp(value) for value in engine["date"]]
    index_by_date = {date: index for index, date in enumerate(engine_dates)}
    instruction_by_prior: dict[pd.Timestamp, dict[str, object]] = {}
    for order in order_rows:
        fill_date = pd.Timestamp(order["engine_date"])
        prior_date = engine_dates[index_by_date[fill_date] - 1]
        if prior_date in instruction_by_prior:
            raise AssertionError("Two compiled orders share one submission bar")
        instruction_by_prior[prior_date] = order

    def execute(ctx: ExecContext) -> None:
        instruction = instruction_by_prior.get(pd.Timestamp(ctx.dt))
        if instruction is None:
            return
        quantity = Decimal(str(float(instruction["shares"])))
        price = Decimal(str(float(instruction["fill_price"])))
        if instruction["type"] == "buy":
            ctx.buy_shares = quantity
            ctx.buy_fill_price = price
        else:
            ctx.sell_shares = quantity
            ctx.sell_fill_price = price

    total_external = float(reference.contributions["amount"].sum()) if not reference.contributions.empty else 0.0
    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = StrategyConfig(
        initial_cash=float(initial_cash + total_external), fee_mode=None, fee_amount=0,
        enable_fractional_shares=True, round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY, max_long_positions=1,
        buy_delay=1, sell_delay=1, exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.OPEN, exit_sell_fill_price=PriceType.OPEN,
        bars_per_year=252, return_signals=False, round_test_result=False,
    )
    strategy = Strategy(
        engine, engine_dates[0].isoformat(), engine_dates[-1].isoformat(), config
    )
    strategy.add_execution(execute, str(engine.iloc[0]["symbol"]))
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    date_mapping = {pd.Timestamp(item["engine_date"]): pd.Timestamp(item["date"]) for item in order_rows}
    if not result.orders.empty:
        result.orders.loc[:, "date"] = pd.to_datetime(result.orders["date"]).map(date_mapping)
    if not result.trades.empty:
        for column in ("entry_date", "exit_date"):
            result.trades.loc[:, column] = pd.to_datetime(result.trades[column]).map(date_mapping)
    return result, engine


def compiled_daily_state(
    pybroker_result,
    engine: pd.DataFrame,
    canonical: pd.DataFrame,
    contributions: pd.DataFrame,
) -> pd.DataFrame:
    state = pybroker_result.portfolio.reset_index()[["date", "cash"]].copy()
    state["engine_date"] = pd.to_datetime(state["date"])
    synthetic = set(pd.to_datetime(engine.loc[pd.to_datetime(engine["date"]).dt.hour.ne(0), "date"]))
    state["date"] = state["engine_date"].apply(
        lambda value: value.normalize() + pd.Timedelta(days=1) if value in synthetic else value.normalize()
    )
    state = state.sort_values("engine_date").groupby("date", as_index=False).tail(1)
    shares_by_engine: dict[pd.Timestamp, float] = {}
    if not pybroker_result.positions.empty:
        positions = pybroker_result.positions.reset_index()
        shares_by_engine = {
            pd.Timestamp(date): float(value)
            for date, value in positions.groupby("date")["long_shares"].sum().items()
        }
    state["shares"] = state["engine_date"].map(shares_by_engine).fillna(0.0)
    canonical_close = canonical.assign(date=pd.to_datetime(canonical["date"]).dt.normalize()).set_index("date")["close"]
    state["close"] = state["date"].map(canonical_close).astype(float)
    flow_by_date = (
        contributions.groupby(pd.to_datetime(contributions["date"]))["amount"].sum()
        if not contributions.empty else pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    )
    state["locked_future_cash"] = state["date"].apply(
        lambda date: float(flow_by_date.loc[flow_by_date.index > date].sum())
    )
    state["cash"] = state["cash"].astype(float) - state["locked_future_cash"]
    state["equity"] = state["cash"] + state["shares"] * state["close"]
    state["is_long"] = (state["shares"] > 0).astype(int)
    return state[["date", "cash", "shares", "equity", "is_long", "close"]].reset_index(drop=True)
