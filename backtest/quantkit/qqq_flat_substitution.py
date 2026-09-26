"""Causal QQQ master-timing portfolios with one timed substitute asset."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import numpy as np
import pandas as pd
from pybroker import PositionMode, Strategy, StrategyConfig
from pybroker.context import ExecContext

from quantkit.execution import ExplicitFillPolicy
from quantkit.trend_score_portfolio import PortfolioRun


@dataclass(frozen=True)
class HysteresisSpec:
    window: int
    entry_buffer_pct: float = 3.0
    exit_buffer_pct: float = 3.0
    first_valid_level_entry: bool = False

    def validate(self) -> None:
        if self.window < 1:
            raise ValueError("window must be positive")
        if self.entry_buffer_pct < 0 or self.exit_buffer_pct < 0:
            raise ValueError("buffers must be non-negative")


@dataclass(frozen=True)
class CompiledPlan:
    target_shares: pd.DataFrame
    executions: pd.DataFrame


def _normalized_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{symbol} price data misses {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result = result.sort_values("date").drop_duplicates("date", keep="last")
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    if result.empty or not np.isfinite(result[["open", "high", "low", "close"]]).all().all():
        raise ValueError(f"{symbol} price data is empty or non-finite")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol} contains non-positive prices")
    result["symbol"] = symbol
    return result.reset_index(drop=True)


def prepare_hysteresis_state(
    frame: pd.DataFrame,
    symbol: str,
    spec: HysteresisSpec,
) -> pd.DataFrame:
    """Build a close-confirmed, path-dependent SMA hysteresis state."""

    spec.validate()
    data = _normalized_frame(frame, symbol)
    sma_column = f"sma{spec.window}"
    data[sma_column] = data["close"].rolling(
        spec.window, min_periods=spec.window
    ).mean()
    data["upper_rail"] = data[sma_column] * (1.0 + spec.entry_buffer_pct / 100.0)
    data["lower_rail"] = data[sma_column] * (1.0 - spec.exit_buffer_pct / 100.0)
    data["ready"] = data[sma_column].notna()
    data["is_long"] = False
    data["transition"] = "not_ready"

    ready_indices = data.index[data["ready"]].tolist()
    if not ready_indices:
        return data
    first = ready_indices[0]
    state = bool(
        spec.first_valid_level_entry
        and float(data.at[first, "close"]) > float(data.at[first, "upper_rail"])
    )
    data.at[first, "is_long"] = state
    data.at[first, "transition"] = "initial_enter" if state else "initial_flat"
    previous_ready = first
    for index in ready_indices[1:]:
        price = float(data.at[index, "close"])
        upper = float(data.at[index, "upper_rail"])
        lower = float(data.at[index, "lower_rail"])
        previous_price = float(data.at[previous_ready, "close"])
        previous_upper = float(data.at[previous_ready, "upper_rail"])
        transition = "hold_long" if state else "hold_flat"
        if state and price < lower:
            state = False
            transition = "exit"
        elif not state and previous_price <= previous_upper and price > upper:
            state = True
            transition = "enter"
        data.at[index, "is_long"] = state
        data.at[index, "transition"] = transition
        previous_ready = index
    return data


def align_substitute_state(
    master_dates: pd.Series | pd.DatetimeIndex,
    substitute: pd.DataFrame,
) -> pd.DataFrame:
    """Align a continuously calculated substitute state to the QQQ calendar."""

    dates = pd.DatetimeIndex(pd.to_datetime(master_dates)).sort_values().unique()
    columns = [
        "date",
        "symbol",
        "close",
        "upper_rail",
        "lower_rail",
        "ready",
        "is_long",
        "transition",
    ]
    selected = substitute[columns].copy().set_index("date").reindex(dates)
    selected.index.name = "date"
    selected["ready"] = selected["ready"].fillna(False).astype(bool)
    selected["is_long"] = selected["is_long"].fillna(False).astype(bool)
    selected["transition"] = selected["transition"].fillna("unavailable")
    return selected.reset_index()


def build_case_decisions(
    master: pd.DataFrame,
    *,
    substitute_symbol: str | None,
    substitute: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return the one allowed asset after each completed QQQ close."""

    required = {"date", "is_long", "ready", "transition"}
    if missing := required.difference(master.columns):
        raise ValueError(f"master state misses {sorted(missing)}")
    base = master.loc[master["ready"], ["date", "is_long", "transition"]].copy()
    if base.empty:
        raise ValueError("master has no ready rows")
    if substitute_symbol is None:
        base["substitute_eligible"] = False
        base["substitute_transition"] = "cash_case"
    else:
        if substitute is None:
            raise ValueError("substitute state is required for a substitute case")
        aligned = align_substitute_state(base["date"], substitute)
        base["substitute_eligible"] = aligned["is_long"].to_numpy(bool)
        base["substitute_transition"] = aligned["transition"].astype(str).to_numpy()
    base["target_asset"] = np.where(
        base["is_long"].astype(bool),
        "QQQ",
        np.where(base["substitute_eligible"].astype(bool), substitute_symbol, "CASH"),
    )
    base["master_state"] = np.where(base["is_long"].astype(bool), "long", "flat")
    base = base.rename(columns={"transition": "master_transition"})
    base["decision_changed"] = base["target_asset"].ne(base["target_asset"].shift())
    return base.reset_index(drop=True)


def build_price_panel(
    frames: Mapping[str, pd.DataFrame],
    master_dates: pd.Series | pd.DatetimeIndex,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(pd.to_datetime(master_dates)).sort_values().unique()
    rows: list[pd.DataFrame] = []
    for symbol, frame in frames.items():
        data = _normalized_frame(frame, symbol)
        data = data[data["date"].isin(dates)].copy()
        if data.empty:
            raise ValueError(f"{symbol} has no prices on the master calendar")
        expected = dates[dates >= data["date"].min()]
        if not pd.DatetimeIndex(data["date"]).equals(expected):
            raise ValueError(f"{symbol} misses a QQQ session after listing")
        rows.append(data)
    return pd.concat(rows, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)


def _price_matrices(price_panel: pd.DataFrame) -> tuple[pd.DatetimeIndex, list[str], pd.DataFrame, pd.DataFrame]:
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    symbols = sorted(panel["symbol"].astype(str).unique())
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    return dates, symbols, opens, closes


def compile_exact_target_shares(
    price_panel: pd.DataFrame,
    decisions: pd.DataFrame,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> CompiledPlan:
    """Compile close decisions into all-cash next-open share targets."""

    dates, symbols, opens, _ = _price_matrices(price_panel)
    next_date = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    current = pd.Series(0.0, index=symbols)
    cash = float(initial_cash)
    current_asset = "CASH"
    target_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    for row in decisions.itertuples(index=False):
        signal_date = pd.Timestamp(row.date)
        execution_date = next_date.get(signal_date)
        if execution_date is None:
            continue
        desired_asset = str(row.target_asset)
        if desired_asset == current_asset:
            continue
        if desired_asset != "CASH" and (
            desired_asset not in opens.columns or pd.isna(opens.at[execution_date, desired_asset])
        ):
            desired_asset = "CASH"
        pre_equity = cash
        for symbol in symbols:
            raw = opens.at[execution_date, symbol]
            if float(current[symbol]) > 1e-12:
                if pd.isna(raw):
                    raise ValueError(f"held {symbol} has no execution Open on {execution_date.date()}")
                pre_equity += float(current[symbol]) * float(raw)
        sold_asset = current_asset if current_asset != "CASH" else ""
        sold_shares = 0.0
        sold_fill = np.nan
        if sold_asset:
            sold_shares = float(current[sold_asset])
            sold_fill = policy.expected_fill("sell", float(opens.at[execution_date, sold_asset]))
            cash += sold_shares * sold_fill
            current[sold_asset] = 0.0
        bought_asset = desired_asset if desired_asset != "CASH" else ""
        bought_shares = 0.0
        bought_fill = np.nan
        if bought_asset:
            bought_fill = policy.expected_fill("buy", float(opens.at[execution_date, bought_asset]))
            bought_shares = cash / bought_fill
            cash -= bought_shares * bought_fill
            current[bought_asset] = bought_shares
        post_equity = cash
        for symbol in symbols:
            raw = opens.at[execution_date, symbol]
            if float(current[symbol]) > 1e-12:
                post_equity += float(current[symbol]) * float(raw)
        for symbol in symbols:
            target_rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "target_weight": 1.0 if symbol == bought_asset else 0.0,
                    "target_shares": float(current[symbol]),
                }
            )
        execution_rows.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "from_asset": current_asset,
                "to_asset": desired_asset,
                "sold_asset": sold_asset,
                "sold_shares": sold_shares,
                "sold_fill": sold_fill,
                "bought_asset": bought_asset,
                "bought_shares": bought_shares,
                "bought_fill": bought_fill,
                "pre_equity_at_raw_open": pre_equity,
                "post_equity_at_raw_open": post_equity,
            }
        )
        current_asset = desired_asset
    return CompiledPlan(pd.DataFrame(target_rows), pd.DataFrame(execution_rows))


def run_pybroker_substitution(
    price_panel: pd.DataFrame,
    target_shares: pd.DataFrame,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> tuple[Any, pd.DataFrame]:
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    symbols = sorted(panel["symbol"].unique())
    lookup = {
        (pd.Timestamp(row.signal_date), str(row.symbol)): float(row.target_shares)
        for row in target_shares.itertuples(index=False)
    }

    def execute(ctx: ExecContext) -> None:
        key = (pd.Timestamp(ctx.dt), ctx.symbol)
        if key not in lookup:
            return
        desired = lookup[key]
        position = ctx.long_pos()
        current = float(position.shares) if position is not None else 0.0
        delta = desired - current
        if delta > 1e-12:
            ctx.buy_shares = Decimal(str(delta))
            ctx.buy_fill_price = policy.buy_fill()
            ctx.score = 1.0
        elif delta < -1e-12:
            ctx.sell_shares = Decimal(str(-delta))
            ctx.sell_fill_price = policy.sell_fill()
            ctx.score = 1000.0

    config = StrategyConfig(
        initial_cash=initial_cash,
        fee_mode=None,
        fee_amount=0,
        enable_fractional_shares=True,
        round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY,
        max_long_positions=len(symbols),
        buy_delay=1,
        sell_delay=1,
        exit_on_last_bar=False,
        exit_cover_fill_price=policy.buy_fill(),
        exit_sell_fill_price=policy.sell_fill(),
        bars_per_year=252,
        round_test_result=False,
    )
    strategy = Strategy(panel, panel["date"].min(), panel["date"].max(), config)
    strategy.add_execution(execute, symbols)
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    positions = pd.Series(0.0, index=index)
    if not result.positions.empty:
        observed = result.positions.reset_index().set_index(["date", "symbol"])["long_shares"]
        positions.loc[observed.index] = observed.astype(float).to_numpy()
    return result, positions.rename("shares").reset_index()


def run_reference_substitution(
    price_panel: pd.DataFrame,
    decisions: pd.DataFrame,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> PortfolioRun:
    """Independent sell-first ledger that consumes decisions, not target shares."""

    dates, symbols, opens, closes = _price_matrices(price_panel)
    decision_map = decisions.set_index("date")["target_asset"].astype(str).to_dict()
    signal_for_execution = {dates[index + 1]: dates[index] for index in range(len(dates) - 1)}
    current = pd.Series(0.0, index=symbols)
    cash = float(initial_cash)
    current_asset = "CASH"
    lots: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    order_id = 0
    trade_id = 0
    for date in dates:
        signal_date = signal_for_execution.get(date)
        desired_asset = current_asset
        if signal_date is not None and signal_date in decision_map:
            desired_asset = decision_map[signal_date]
            if desired_asset != "CASH" and (
                desired_asset not in opens.columns or pd.isna(opens.at[date, desired_asset])
            ):
                desired_asset = "CASH"
        if desired_asset != current_asset:
            if current_asset != "CASH":
                symbol = current_asset
                shares = float(current[symbol])
                fill = policy.expected_fill("sell", float(opens.at[date, symbol]))
                cash += shares * fill
                current[symbol] = 0.0
                order_id += 1
                orders.append(
                    {"id": order_id, "date": date, "symbol": symbol, "type": "sell", "fill_price": fill, "shares": shares}
                )
                remaining = shares
                while remaining > 1e-12 and lots[symbol]:
                    lot = lots[symbol][0]
                    matched = min(remaining, float(lot["shares"]))
                    pnl = matched * (fill - float(lot["price"]))
                    trade_id += 1
                    trades.append(
                        {
                            "id": trade_id,
                            "type": "long",
                            "symbol": symbol,
                            "entry_date": lot["date"],
                            "exit_date": date,
                            "entry": float(lot["price"]),
                            "exit": fill,
                            "shares": matched,
                            "pnl": pnl,
                            "return_pct": (fill / float(lot["price"]) - 1.0) * 100.0,
                        }
                    )
                    lot["shares"] = float(lot["shares"]) - matched
                    remaining -= matched
                    if float(lot["shares"]) <= 1e-12:
                        lots[symbol].pop(0)
            if desired_asset != "CASH":
                symbol = desired_asset
                fill = policy.expected_fill("buy", float(opens.at[date, symbol]))
                shares = cash / fill
                cash -= shares * fill
                current[symbol] = shares
                lots[symbol].append({"date": date, "price": fill, "shares": shares})
                order_id += 1
                orders.append(
                    {"id": order_id, "date": date, "symbol": symbol, "type": "buy", "fill_price": fill, "shares": shares}
                )
            current_asset = desired_asset
        values = pd.Series(0.0, index=symbols)
        for symbol in symbols:
            if float(current[symbol]) > 1e-12:
                values[symbol] = float(current[symbol]) * float(closes.at[date, symbol])
            positions.append({"date": date, "symbol": symbol, "shares": float(current[symbol])})
        equity = cash + float(values.sum())
        daily.append(
            {
                "date": date,
                "cash": cash,
                "shares": float(current.sum()),
                "equity": equity,
                "is_long": int(current.sum() > 1e-12),
                "gross_exposure": float(values.sum() / equity) if equity > 0 else 0.0,
                "current_asset": current_asset,
            }
        )
    return PortfolioRun(
        daily=pd.DataFrame(daily),
        positions=pd.DataFrame(positions),
        orders=pd.DataFrame(orders),
        trades=pd.DataFrame(trades),
    )


def build_master_flat_spells(master: pd.DataFrame) -> pd.DataFrame:
    """Locate initial waiting and every QQQ sell-fill to buy-fill spell."""

    ready = master.loc[master["ready"]].reset_index(drop=True)
    if len(ready) < 2:
        return pd.DataFrame()
    dates = pd.DatetimeIndex(ready["date"])
    next_date = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    enters = ready[ready["transition"].isin(["enter", "initial_enter"])]
    exits = ready[ready["transition"] == "exit"]
    rows: list[dict[str, Any]] = []

    def add_spell(spell_type: str, start_signal: pd.Timestamp, start_fill: pd.Timestamp, number: int) -> None:
        later = enters[pd.to_datetime(enters["date"]) > start_signal].head(1)
        closed = not later.empty
        end_signal = pd.NaT if later.empty else pd.Timestamp(later.iloc[0]["date"])
        end_fill = pd.NaT if later.empty else next_date.get(end_signal, pd.NaT)
        rows.append(
            {
                "spell_id": f"{spell_type}_{number:02d}",
                "spell_type": spell_type,
                "start_signal_date": start_signal,
                "start_fill_date": start_fill,
                "end_signal_date": end_signal,
                "end_fill_date": end_fill,
                "closed_spell": bool(closed and pd.notna(end_fill)),
                "mark_date": end_fill if closed and pd.notna(end_fill) else dates[-1],
            }
        )

    first_signal = dates[0]
    first_fill = dates[1]
    if not bool(ready.iloc[0]["is_long"]):
        add_spell("initial_wait", first_signal, first_fill, 1)
    for number, exit_row in enumerate(exits.itertuples(index=False), start=1):
        signal = pd.Timestamp(exit_row.date)
        fill = next_date.get(signal)
        if fill is not None:
            add_spell("post_sell", signal, fill, number)
    result = pd.DataFrame(rows)
    if not result.empty:
        result["calendar_days"] = (
            pd.to_datetime(result["mark_date"]) - pd.to_datetime(result["start_fill_date"])
        ).dt.days
    return result


def attribute_spell_overlays(
    spells: pd.DataFrame,
    decisions: pd.DataFrame,
    substitute_frame: pd.DataFrame,
    *,
    substitute_symbol: str,
    policy: ExplicitFillPolicy,
    dotcom_start: pd.Timestamp,
    dotcom_end: pd.Timestamp,
) -> pd.DataFrame:
    """Calculate timed and unconditional substitute returns inside master flat spells."""

    prices = _normalized_frame(substitute_frame, substitute_symbol).set_index("date")
    signal_dates = pd.DatetimeIndex(pd.to_datetime(decisions["date"]))
    next_date = {
        signal_dates[index]: signal_dates[index + 1]
        for index in range(len(signal_dates) - 1)
    }
    desired_by_execution = {
        next_date[pd.Timestamp(row.date)]: str(row.target_asset)
        for row in decisions.itertuples(index=False)
        if pd.Timestamp(row.date) in next_date
    }
    records: list[dict[str, Any]] = []
    for spell in spells.itertuples(index=False):
        start = pd.Timestamp(spell.start_fill_date)
        mark = pd.Timestamp(spell.mark_date)
        closed = bool(spell.closed_spell)
        execution_dates = signal_dates[(signal_dates >= start) & (signal_dates <= mark)]

        def simulate(timed: bool) -> tuple[float, int, int]:
            cash = 1.0
            shares = 0.0
            invested_sessions = 0
            orders = 0
            for date in execution_dates:
                available = date in prices.index
                if timed:
                    want = desired_by_execution.get(date) == substitute_symbol
                else:
                    want = available and (not closed or date < mark)
                if closed and date == mark:
                    want = False
                if want and available and shares <= 1e-15:
                    fill = policy.expected_fill("buy", float(prices.at[date, "open"]))
                    shares = cash / fill
                    cash -= shares * fill
                    orders += 1
                elif not want and shares > 1e-15:
                    fill = policy.expected_fill("sell", float(prices.at[date, "open"]))
                    cash += shares * fill
                    shares = 0.0
                    orders += 1
                if shares > 1e-15:
                    invested_sessions += 1
            if closed:
                equity = cash
            elif shares > 1e-15 and mark in prices.index:
                equity = cash + shares * float(prices.at[mark, "close"])
            else:
                equity = cash
            return (equity - 1.0) * 100.0, orders, invested_sessions

        timed_return, timed_orders, timed_sessions = simulate(True)
        hold_return, hold_orders, hold_sessions = simulate(False)
        records.append(
            {
                "spell_id": spell.spell_id,
                "spell_type": spell.spell_type,
                "substitute_symbol": substitute_symbol,
                "start_fill_date": start,
                "mark_date": mark,
                "closed_spell": closed,
                "calendar_days": int(spell.calendar_days),
                "overlaps_dotcom_bear": bool(start <= dotcom_end and mark >= dotcom_start),
                "timed_return_pct": timed_return,
                "unconditional_return_pct": hold_return,
                "timed_minus_unconditional_pp": timed_return - hold_return,
                "timed_order_count": timed_orders,
                "unconditional_order_count": hold_orders,
                "timed_invested_sessions": timed_sessions,
                "unconditional_invested_sessions": hold_sessions,
            }
        )
    return pd.DataFrame(records)


def compound_return_pct(values: pd.Series) -> float:
    data = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    if data.empty:
        return float("nan")
    return float(((1.0 + data / 100.0).prod() - 1.0) * 100.0)


def summarize_spell_overlays(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scope, selected in (
        ("all_post_sell", events[events["spell_type"] == "post_sell"]),
        (
            "post_sell_excluding_dotcom",
            events[(events["spell_type"] == "post_sell") & ~events["overlaps_dotcom_bear"]],
        ),
        ("initial_wait", events[events["spell_type"] == "initial_wait"]),
    ):
        rows.append(
            {
                "substitute_symbol": events["substitute_symbol"].iloc[0] if len(events) else "",
                "scope": scope,
                "spell_count": int(len(selected)),
                "closed_spell_count": int(selected["closed_spell"].sum()) if len(selected) else 0,
                "timed_compound_return_pct": compound_return_pct(selected["timed_return_pct"]),
                "unconditional_compound_return_pct": compound_return_pct(selected["unconditional_return_pct"]),
                "positive_timed_spell_count": int((selected["timed_return_pct"] > 0).sum()) if len(selected) else 0,
                "median_timed_return_pct": float(selected["timed_return_pct"].median()) if len(selected) else float("nan"),
                "timed_invested_sessions": int(selected["timed_invested_sessions"].sum()) if len(selected) else 0,
            }
        )
    return pd.DataFrame(rows)
