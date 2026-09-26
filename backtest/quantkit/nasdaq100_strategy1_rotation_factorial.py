"""Portfolio factorial for the frozen Nasdaq-100 Strategy1 score."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.context import ExecContext

from quantkit.execution import ExplicitFillPolicy
from quantkit.metrics import calculate_metrics


SELECTION_MODES = ("TOP20_90", "ALL_90", "ALL_80")
ALLOCATION_MODES = ("RESTORE_EQUAL", "ENTRY_EXIT_ONLY")
LEDGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class RotationFactorSpec:
    fast_exit: bool
    selection_mode: str
    allocation_mode: str
    rebalance_days: int = 2
    fast_exit_threshold: float = 0.80
    fast_reset_weight: float = 0.20

    def __post_init__(self) -> None:
        if self.selection_mode not in SELECTION_MODES:
            raise ValueError(f"unknown selection mode: {self.selection_mode}")
        if self.allocation_mode not in ALLOCATION_MODES:
            raise ValueError(f"unknown allocation mode: {self.allocation_mode}")
        if self.rebalance_days < 1:
            raise ValueError("rebalance_days must be positive")

    @property
    def threshold(self) -> float:
        return 0.80 if self.selection_mode == "ALL_80" else 0.90

    @property
    def maximum_selected(self) -> int | None:
        return 20 if self.selection_mode == "TOP20_90" else None

    @property
    def case_id(self) -> str:
        allocation = "REBAL" if self.allocation_mode == "RESTORE_EQUAL" else "DRIFT"
        return f"FAST{int(self.fast_exit)}_{self.selection_mode}_{allocation}"


@dataclass(frozen=True)
class RotationFactorResult:
    daily: pd.DataFrame
    positions: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    selections: pd.DataFrame
    metrics: dict[str, Any]
    replay_checks: dict[str, float]


def factor_specs() -> list[RotationFactorSpec]:
    return [
        RotationFactorSpec(bool(fast), selection, allocation)
        for fast in (0, 1)
        for selection in SELECTION_MODES
        for allocation in ALLOCATION_MODES
    ]


def select_snapshot(
    snapshot: pd.DataFrame,
    *,
    selection_mode: str,
    locked: Iterable[str] = (),
) -> pd.DataFrame:
    """Apply strict threshold, deterministic ranking, and optional top-20 cap."""

    required = {
        "security_id", "display_ticker", "strategy1_weight", "in_universe", "real_bar"
    }
    if missing := required.difference(snapshot.columns):
        raise ValueError(f"selection snapshot misses {sorted(missing)}")
    spec = RotationFactorSpec(False, selection_mode, "RESTORE_EQUAL")
    rows = snapshot.copy()
    score = rows["strategy1_weight"].astype(float)
    rows = rows[
        rows["in_universe"].astype(bool)
        & rows["real_bar"].astype(bool)
        & np.isfinite(score)
        & score.gt(spec.threshold)
        & ~rows["security_id"].astype(str).isin(set(map(str, locked)))
    ].copy()
    rows = rows.sort_values(
        ["strategy1_weight", "security_id"],
        ascending=[False, True],
        kind="stable",
    )
    rows["rank"] = np.arange(1, len(rows) + 1)
    if spec.maximum_selected is not None:
        rows = rows.head(spec.maximum_selected)
    return rows.reset_index(drop=True)


def select_all_members(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time all-member equal-weight baseline selection."""

    required = {"security_id", "display_ticker", "in_universe", "real_bar"}
    if missing := required.difference(snapshot.columns):
        raise ValueError(f"member snapshot misses {sorted(missing)}")
    rows = snapshot[
        snapshot["in_universe"].astype(bool) & snapshot["real_bar"].astype(bool)
    ].copy()
    rows = rows.sort_values("security_id", kind="stable")
    rows["rank"] = np.arange(1, len(rows) + 1)
    return rows.reset_index(drop=True)


def _empty_orders() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "id", "date", "signal_date", "symbol", "type", "reason", "fill_timing",
            "reference_price", "fill_price", "shares", "target_shares_after", "sequence",
        ]
    )


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "id", "type", "symbol", "entry_date", "exit_date", "entry", "exit",
            "shares", "pnl", "return_pct", "exit_reason",
        ]
    )


def _validate_inputs(
    state: pd.DataFrame,
    price_panel: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex, list[str]]:
    state_required = {
        "date", "security_id", "display_ticker", "strategy1_weight", "stochrsi_100",
        "in_universe",
    }
    panel_required = {
        "date", "symbol", "open", "high", "low", "close", "synthetic_bar",
        "terminal_settlement_proxy",
    }
    if missing := state_required.difference(state.columns):
        raise ValueError(f"state misses {sorted(missing)}")
    if missing := panel_required.difference(price_panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    states = state.copy()
    states["date"] = pd.to_datetime(states["date"]).dt.normalize()
    states["security_id"] = states["security_id"].astype(str)
    if states.duplicated(["date", "security_id"]).any():
        raise ValueError("state contains duplicate security dates")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["symbol"] = panel["symbol"].astype(str)
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("price panel contains duplicate security dates")
    prices = panel[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("price panel requires finite positive OHLC")
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    symbols = sorted(panel["symbol"].unique())
    expected = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    actual = pd.MultiIndex.from_frame(panel[["date", "symbol"]])
    if len(actual) != len(expected) or not actual.sort_values().equals(expected):
        raise ValueError("price panel must be dense across the shared calendar")
    return states, panel, dates, symbols


def _replay_orders(
    daily: pd.DataFrame,
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    initial_cash: float,
) -> dict[str, float]:
    """Independent immutable-fill replay against saved daily cash/shares/equity."""

    symbols = sorted(price_panel["symbol"].astype(str).unique())
    closes = price_panel.pivot(index="date", columns="symbol", values="close")
    grouped = {
        pd.Timestamp(day): frame.sort_values("sequence", kind="stable")
        for day, frame in orders.groupby("date")
    }
    expected_positions = positions.pivot(index="date", columns="symbol", values="shares")
    expected_daily = daily.set_index("date")
    cash = float(initial_cash)
    shares = pd.Series(0.0, index=symbols)
    checks = {"max_abs_cash_difference": 0.0, "max_abs_equity_difference": 0.0,
              "max_abs_position_shares_difference": 0.0}
    for date in expected_daily.index:
        for order in grouped.get(pd.Timestamp(date), _empty_orders()).itertuples(index=False):
            quantity = float(order.shares)
            fill = float(order.fill_price)
            if order.type == "sell":
                cash += quantity * fill
                shares[str(order.symbol)] -= quantity
            elif order.type == "buy":
                cash -= quantity * fill
                shares[str(order.symbol)] += quantity
            else:
                raise AssertionError(f"unknown order side: {order.type}")
        expected = expected_positions.loc[date].reindex(symbols).fillna(0.0)
        equity = cash + float((shares * closes.loc[date].reindex(symbols)).sum())
        checks["max_abs_cash_difference"] = max(
            checks["max_abs_cash_difference"], abs(cash - float(expected_daily.at[date, "cash"]))
        )
        checks["max_abs_equity_difference"] = max(
            checks["max_abs_equity_difference"],
            abs(equity - float(expected_daily.at[date, "equity"])),
        )
        checks["max_abs_position_shares_difference"] = max(
            checks["max_abs_position_shares_difference"],
            float((shares - expected).abs().max()),
        )
    if max(checks.values()) > LEDGER_TOLERANCE:
        raise AssertionError(f"rotation order replay failed: {checks}")
    return checks


def run_rotation_factor(
    state: pd.DataFrame,
    price_panel: pd.DataFrame,
    spec: RotationFactorSpec,
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
    all_member_baseline: bool = False,
) -> RotationFactorResult:
    """Run one causal portfolio path with explicit Open and Close fills."""

    states, panel, dates, symbols = _validate_inputs(state, price_panel)
    if not np.isfinite(initial_cash) or initial_cash <= 0 or cost_bps < 0:
        raise ValueError("initial cash must be positive and cost non-negative")
    impact = float(cost_bps) / 10_000.0
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    synthetic = panel.pivot(index="date", columns="symbol", values="synthetic_bar").reindex(dates).astype(bool)
    terminal = panel.pivot(index="date", columns="symbol", values="terminal_settlement_proxy").reindex(dates).astype(bool)
    display = (
        states[["security_id", "display_ticker"]]
        .drop_duplicates("security_id")
        .set_index("security_id")["display_ticker"]
        .astype(str)
        .to_dict()
    )
    state_by_date = {pd.Timestamp(day): frame for day, frame in states.groupby("date")}
    score = states.pivot(index="date", columns="security_id", values="strategy1_weight").reindex(index=dates, columns=symbols)
    s100 = states.pivot(index="date", columns="security_id", values="stochrsi_100").reindex(index=dates, columns=symbols)

    cash = float(initial_cash)
    shares = pd.Series(0.0, index=symbols)
    lots: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    fast_locked: set[str] = set()
    pending_selection: dict[str, Any] | None = None
    order_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    order_id = 0
    trade_id = 0
    sequence = 0
    unavailable_deferrals = 0

    def record_order(
        *, date: pd.Timestamp, signal_date: pd.Timestamp, symbol: str, side: str,
        reason: str, timing: str, reference: float, quantity: float,
    ) -> None:
        nonlocal cash, order_id, sequence, trade_id
        quantity = min(float(quantity), float(shares[symbol])) if side == "sell" else float(quantity)
        if quantity <= 1e-12:
            return
        fill = reference * (1.0 - impact if side == "sell" else 1.0 + impact)
        if side == "sell":
            remaining = quantity
            while remaining > 1e-12 and lots[symbol]:
                lot = lots[symbol][0]
                matched = min(remaining, float(lot["shares"]))
                pnl = matched * (fill - float(lot["price"]))
                trade_id += 1
                trade_rows.append({
                    "id": trade_id, "type": "long", "symbol": symbol,
                    "entry_date": lot["date"], "exit_date": date,
                    "entry": float(lot["price"]), "exit": fill, "shares": matched,
                    "pnl": pnl, "return_pct": (fill / float(lot["price"]) - 1.0) * 100.0,
                    "exit_reason": reason,
                })
                lot["shares"] = float(lot["shares"]) - matched
                remaining -= matched
                if float(lot["shares"]) <= 1e-12:
                    lots[symbol].pop(0)
            cash += quantity * fill
            shares[symbol] -= quantity
            if shares[symbol] <= 1e-10:
                shares[symbol] = 0.0
        elif side == "buy":
            affordable = max(cash, 0.0) / fill
            quantity = min(quantity, affordable)
            if quantity <= 1e-12:
                return
            cash -= quantity * fill
            shares[symbol] += quantity
            lots[symbol].append({"date": date, "price": fill, "shares": quantity})
        else:
            raise ValueError(f"unknown order side: {side}")
        order_id += 1
        sequence += 1
        order_rows.append({
            "id": order_id, "date": date, "signal_date": signal_date, "symbol": symbol,
            "type": side, "reason": reason, "fill_timing": timing,
            "reference_price": reference, "fill_price": fill, "shares": quantity,
            "target_shares_after": float(shares[symbol]), "sequence": sequence,
        })

    for index, date in enumerate(dates):
        date = pd.Timestamp(date)
        held_from_prior_close = set(shares[shares > 1e-12].index)

        # Execute the previous scheduled Close decision at today's Open.
        if pending_selection is not None:
            signal_date = pd.Timestamp(pending_selection["signal_date"])
            selected = set(map(str, pending_selection["selected"]))
            signal_synthetic = synthetic.loc[signal_date]
            execution_synthetic = synthetic.loc[date]
            if spec.allocation_mode == "RESTORE_EQUAL":
                signal_equity = float(pending_selection["signal_equity"])
                target = pd.Series(0.0, index=symbols)
                if selected:
                    each = 1.0 / len(selected)
                    for symbol in selected:
                        if not bool(signal_synthetic[symbol]):
                            target[symbol] = signal_equity * each / float(closes.at[signal_date, symbol])
                desired = target.copy()
                internal_gap = (signal_synthetic | execution_synthetic) & ~terminal.loc[date]
                changed = (desired - shares).abs() > 1e-12
                unavailable_deferrals += int((internal_gap & changed).sum())
                desired.loc[internal_gap] = shares.loc[internal_gap]
                terminal_buy = terminal.loc[date] & (desired > shares + 1e-12)
                unavailable_deferrals += int(terminal_buy.sum())
                desired.loc[terminal_buy] = shares.loc[terminal_buy]
                for symbol in symbols:
                    quantity = max(float(shares[symbol] - desired[symbol]), 0.0)
                    if quantity > 1e-12:
                        record_order(
                            date=date, signal_date=signal_date, symbol=symbol, side="sell",
                            reason="SCHEDULED_REBALANCE_EXIT", timing="open",
                            reference=float(opens.at[date, symbol]), quantity=quantity,
                        )
                for symbol in symbols:
                    quantity = max(float(desired[symbol] - shares[symbol]), 0.0)
                    if quantity > 1e-12:
                        record_order(
                            date=date, signal_date=signal_date, symbol=symbol, side="buy",
                            reason="SCHEDULED_REBALANCE_ENTRY", timing="open",
                            reference=float(opens.at[date, symbol]), quantity=quantity,
                        )
            else:
                current = set(shares[shares > 1e-12].index)
                exits = sorted(current - selected)
                for symbol in exits:
                    unavailable = (
                        (bool(signal_synthetic[symbol]) or bool(execution_synthetic[symbol]))
                        and not bool(terminal.at[date, symbol])
                    )
                    if unavailable:
                        unavailable_deferrals += 1
                        continue
                    record_order(
                        date=date, signal_date=signal_date, symbol=symbol, side="sell",
                        reason="SCHEDULED_DESELECTION", timing="open",
                        reference=float(opens.at[date, symbol]), quantity=float(shares[symbol]),
                    )
                current = set(shares[shares > 1e-12].index)
                entrants = [
                    symbol for symbol in sorted(selected - current)
                    if not bool(execution_synthetic[symbol]) and not bool(terminal.at[date, symbol])
                ]
                unavailable_deferrals += len(selected - current) - len(entrants)
                for offset, symbol in enumerate(entrants):
                    remaining = len(entrants) - offset
                    budget = max(cash, 0.0) / remaining
                    fill = float(opens.at[date, symbol]) * (1.0 + impact)
                    record_order(
                        date=date, signal_date=signal_date, symbol=symbol, side="buy",
                        reason="SCHEDULED_NEW_ENTRY", timing="open",
                        reference=float(opens.at[date, symbol]), quantity=budget / fill,
                    )
            pending_selection = None

        # Same-Close true downcross applies only to positions carried into the session.
        if spec.fast_exit and index > 0:
            previous_date = pd.Timestamp(dates[index - 1])
            for symbol in sorted(held_from_prior_close):
                if shares[symbol] <= 1e-12 or bool(synthetic.at[date, symbol]):
                    continue
                previous_value = float(s100.at[previous_date, symbol])
                current_value = float(s100.at[date, symbol])
                if (
                    np.isfinite(previous_value)
                    and np.isfinite(current_value)
                    and previous_value >= spec.fast_exit_threshold
                    and current_value < spec.fast_exit_threshold
                ):
                    record_order(
                        date=date, signal_date=date, symbol=symbol, side="sell",
                        reason="FAST_DOWNCROSS_080", timing="close",
                        reference=float(closes.at[date, symbol]), quantity=float(shares[symbol]),
                    )
                    fast_locked.add(symbol)

        # Preserve the inherited reset order: the current mother score can clear a lock.
        if spec.fast_exit:
            for symbol in list(fast_locked):
                current_score = float(score.at[date, symbol])
                if (
                    not bool(synthetic.at[date, symbol])
                    and np.isfinite(current_score)
                    and current_score < spec.fast_reset_weight
                ):
                    fast_locked.remove(symbol)

        market_value = shares * closes.loc[date]
        equity = cash + float(market_value.sum())
        daily_rows.append({
            "date": date, "cash": cash, "shares": float(shares.sum()), "equity": equity,
            "is_long": int(shares.sum() > 1e-12),
            "gross_exposure": float(market_value.sum() / equity) if equity > 0 else 0.0,
            "holdings_count": int((shares > 1e-12).sum()),
            "fast_locked_count": int(len(fast_locked)),
        })
        for symbol in symbols:
            position_rows.append({"date": date, "symbol": symbol, "shares": float(shares[symbol])})

        # Every second completed Close creates the next Open's ordinary selection.
        if index % spec.rebalance_days == 0 and index < len(dates) - 1:
            snapshot = state_by_date.get(date, states.iloc[0:0]).copy()
            if snapshot.empty:
                selected_frame = snapshot
            else:
                snapshot["real_bar"] = ~snapshot["security_id"].map(synthetic.loc[date]).fillna(True)
                selected_frame = (
                    select_all_members(snapshot)
                    if all_member_baseline
                    else select_snapshot(
                        snapshot,
                        selection_mode=spec.selection_mode,
                        locked=fast_locked if spec.fast_exit else (),
                    )
                )
            selected_ids = selected_frame["security_id"].astype(str).tolist() if len(selected_frame) else []
            pending_selection = {
                "signal_date": date,
                "selected": selected_ids,
                "signal_equity": equity,
            }
            for item in selected_frame.itertuples(index=False):
                selection_rows.append({
                    "date": date, "security_id": str(item.security_id),
                    "display_ticker": str(item.display_ticker), "rank": int(item.rank),
                    "strategy1_weight": float(getattr(item, "strategy1_weight", np.nan)),
                })

    daily = pd.DataFrame(daily_rows)
    positions = pd.DataFrame(position_rows)
    orders = pd.DataFrame(order_rows) if order_rows else _empty_orders()
    trades = pd.DataFrame(trade_rows) if trade_rows else _empty_trades()
    selections = pd.DataFrame(
        selection_rows,
        columns=["date", "security_id", "display_ticker", "rank", "strategy1_weight"],
    )
    metrics = calculate_metrics(daily, orders, trades, initial_cash=float(initial_cash))
    metrics.update({
        "case_id": "NDX100_ALL_REBAL" if all_member_baseline else spec.case_id,
        "fast_exit": bool(spec.fast_exit),
        "selection_mode": "ALL_MEMBERS" if all_member_baseline else spec.selection_mode,
        "allocation_mode": spec.allocation_mode,
        "average_holdings_count": float(daily["holdings_count"].mean()),
        "median_holdings_count": float(daily["holdings_count"].median()),
        "maximum_holdings_count": int(daily["holdings_count"].max()),
        "fast_exit_count": int(orders["reason"].eq("FAST_DOWNCROSS_080").sum()),
        "unavailable_target_deferral_count": int(unavailable_deferrals),
    })
    checks = _replay_orders(
        daily, positions, orders, panel, initial_cash=float(initial_cash)
    )
    return RotationFactorResult(daily, positions, orders, trades, selections, metrics, checks)


def case_effects(metrics: pd.DataFrame, baseline_case_id: str) -> pd.DataFrame:
    """Return exact paired deltas for the complete 2x3x2 design."""

    required = {
        "case_id", "fast_exit", "selection_mode", "allocation_mode", "cagr_pct",
        "sharpe", "max_drawdown_pct", "turnover_multiple",
    }
    if missing := required.difference(metrics.columns):
        raise ValueError(f"metrics miss {sorted(missing)}")
    if baseline_case_id not in set(metrics["case_id"]):
        raise ValueError("baseline case is missing")
    baseline = metrics.set_index("case_id").loc[baseline_case_id]
    rows: list[dict[str, Any]] = []
    for item in metrics.itertuples(index=False):
        rows.append({
            "case_id": item.case_id,
            "cagr_delta_vs_baseline1_pp": float(item.cagr_pct) - float(baseline.cagr_pct),
            "sharpe_delta_vs_baseline1": float(item.sharpe) - float(baseline.sharpe),
            "max_drawdown_improvement_vs_baseline1_pp": (
                float(item.max_drawdown_pct) - float(baseline.max_drawdown_pct)
            ),
            "turnover_delta_vs_baseline1": (
                float(item.turnover_multiple) - float(baseline.turnover_multiple)
            ),
        })
    return pd.DataFrame(rows)


def _event_time(date: pd.Timestamp, timing: str) -> pd.Timestamp:
    base = pd.Timestamp(date).normalize()
    if timing == "open":
        return base + pd.Timedelta(hours=9, minutes=30)
    if timing == "close":
        return base + pd.Timedelta(hours=16)
    raise ValueError(f"unknown fill timing: {timing}")


def expanded_execution_panel(price_panel: pd.DataFrame) -> pd.DataFrame:
    """Expand daily bars into deterministic Open and Close replay events.

    This panel is only an execution audit surface.  Strategy signals and daily
    marks remain on the original daily ledger; the two event bars let PyBroker
    reproduce an ordinary Open rebalance followed by a fast Close exit in the
    same security on the same session without collapsing the two fills.
    """

    required = {"date", "symbol", "open", "close"}
    if missing := required.difference(price_panel.columns):
        raise ValueError(f"execution panel misses {sorted(missing)}")
    rows: list[pd.DataFrame] = []
    for timing, clock, price_column in (
        ("open", pd.Timedelta(hours=9, minutes=30), "open"),
        ("close", pd.Timedelta(hours=16), "close"),
    ):
        frame = price_panel[["date", "symbol", price_column]].copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize() + clock
        frame["open"] = frame[price_column].astype(float)
        frame["high"] = frame[price_column].astype(float)
        frame["low"] = frame[price_column].astype(float)
        frame["close"] = frame[price_column].astype(float)
        frame["volume"] = 0.0
        frame["event_timing"] = timing
        rows.append(frame[["date", "symbol", "open", "high", "low", "close", "volume", "event_timing"]])
    return pd.concat(rows, ignore_index=True).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def run_pybroker_order_plan(
    price_panel: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    initial_cash: float,
    cost_bps: float,
) -> tuple[Any, pd.DataFrame]:
    """Replay the immutable mixed Open/Close order plan in PyBroker."""

    panel = expanded_execution_panel(price_panel)
    events = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    previous_event = {events[index]: events[index - 1] for index in range(1, len(events))}
    plan = orders.copy()
    if plan.empty:
        plan = _empty_orders()
    plan["execution_event"] = [
        _event_time(pd.Timestamp(date), str(timing))
        for date, timing in zip(plan["date"], plan["fill_timing"], strict=True)
    ]
    if plan.duplicated(["execution_event", "symbol"]).any():
        duplicate = plan[plan.duplicated(["execution_event", "symbol"], keep=False)]
        raise ValueError(f"multiple orders share one security event: {duplicate.to_dict('records')}")
    plan["submit_event"] = plan["execution_event"].map(previous_event)
    if plan["submit_event"].isna().any():
        raise ValueError("an order has no prior replay event")
    lookup = {
        (pd.Timestamp(item.submit_event), str(item.symbol)): item
        for item in plan.itertuples(index=False)
    }
    open_policy = ExplicitFillPolicy("open", float(cost_bps))
    close_policy = ExplicitFillPolicy("close", float(cost_bps))

    def execute(ctx: ExecContext) -> None:
        item = lookup.get((pd.Timestamp(ctx.dt), str(ctx.symbol)))
        if item is None:
            return
        policy = open_policy if item.fill_timing == "open" else close_policy
        quantity = Decimal(str(float(item.shares)))
        if item.type == "buy":
            ctx.buy_shares = quantity
            # Every event bar is flat OHLC, so either explicit field produces
            # the same raw event price; keep timing-specific policy for costs.
            ctx.buy_fill_price = policy.buy_fill()
            ctx.score = 1.0
        elif item.type == "sell":
            ctx.sell_shares = quantity
            ctx.sell_fill_price = policy.sell_fill()
            ctx.score = 2.0
        else:
            raise AssertionError(f"unknown planned side: {item.type}")

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    symbols = sorted(panel["symbol"].unique())
    config = StrategyConfig(
        initial_cash=float(initial_cash), fee_mode=None, fee_amount=0,
        enable_fractional_shares=True, round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY, max_long_positions=len(symbols),
        buy_delay=1, sell_delay=1, exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.CLOSE, exit_sell_fill_price=PriceType.CLOSE,
        bars_per_year=504, round_test_result=False,
    )
    strategy = Strategy(panel.drop(columns="event_timing"), panel["date"].min(), panel["date"].max(), config)
    strategy.add_execution(execute, symbols)
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    positions = result.positions.reset_index()[["date", "symbol", "long_shares"]].copy()
    return result, positions


def cross_check_pybroker_order_plan(
    result: Any,
    expected_orders: pd.DataFrame,
    *,
    tolerance: float = LEDGER_TOLERANCE,
    numerical_zero_notional: float = 1e-9,
) -> dict[str, float]:
    """Compare PyBroker's mixed-event fills with the independent order plan."""

    actual = result.orders.reset_index().copy()
    expected = expected_orders.copy()
    if actual.empty and expected.empty:
        return {
            "max_abs_order_shares_difference": 0.0,
            "max_abs_order_fill_price_difference": 0.0,
            "max_ignored_numerical_zero_order_notional": 0.0,
        }
    actual["event_date"] = pd.to_datetime(actual["date"])
    expected["event_date"] = [
        _event_time(pd.Timestamp(date), str(timing))
        for date, timing in zip(expected["date"], expected["fill_timing"], strict=True)
    ]
    actual_notional = actual["shares"].astype(float).abs() * actual["fill_price"].astype(float).abs()
    expected_notional = expected["shares"].astype(float).abs() * expected["fill_price"].astype(float).abs()
    ignored = pd.concat([
        actual_notional[actual_notional <= numerical_zero_notional],
        expected_notional[expected_notional <= numerical_zero_notional],
    ])
    actual = actual[actual_notional > numerical_zero_notional].sort_values(
        ["event_date", "type", "symbol"], kind="stable"
    ).reset_index(drop=True)
    expected = expected[expected_notional > numerical_zero_notional].sort_values(
        ["event_date", "type", "symbol"], kind="stable"
    ).reset_index(drop=True)
    if len(actual) != len(expected):
        raise AssertionError(f"PyBroker order count differs: {len(actual)} != {len(expected)}")
    for column in ("event_date", "type", "symbol"):
        if actual[column].tolist() != expected[column].tolist():
            raise AssertionError(f"PyBroker order {column} sequence differs")
    checks = {
        "max_abs_order_shares_difference": (
            float(np.max(np.abs(actual["shares"].to_numpy(float) - expected["shares"].to_numpy(float))))
            if len(actual) else 0.0
        ),
        "max_abs_order_fill_price_difference": (
            float(np.max(np.abs(actual["fill_price"].to_numpy(float) - expected["fill_price"].to_numpy(float))))
            if len(actual) else 0.0
        ),
        "max_ignored_numerical_zero_order_notional": float(ignored.max()) if len(ignored) else 0.0,
    }
    if max(checks["max_abs_order_shares_difference"], checks["max_abs_order_fill_price_difference"]) > tolerance:
        raise AssertionError(f"PyBroker order plan differs: {checks}")
    return checks


def cross_check_pybroker_daily_state(
    result: Any,
    broker_positions: pd.DataFrame,
    expected_daily: pd.DataFrame,
    expected_positions: pd.DataFrame,
    *,
    tolerance: float = LEDGER_TOLERANCE,
) -> dict[str, float]:
    """Compare PyBroker Close-event state with the independent daily ledger."""

    actual = result.portfolio.reset_index()[["date", "cash", "equity"]].copy()
    actual["date"] = pd.to_datetime(actual["date"])
    actual = actual[actual["date"].dt.hour.eq(16)].copy()
    actual["date"] = actual["date"].dt.normalize()
    expected = expected_daily[["date", "cash", "equity"]].copy()
    expected["date"] = pd.to_datetime(expected["date"]).dt.normalize()
    merged = expected.merge(actual, on="date", suffixes=("_expected", "_actual"), validate="one_to_one")
    if len(merged) != len(expected):
        raise AssertionError("PyBroker Close-event daily state is incomplete")
    checks = {
        "max_abs_cash_difference": float(
            np.max(np.abs(merged["cash_expected"] - merged["cash_actual"]))
        ),
        "max_abs_equity_difference": float(
            np.max(np.abs(merged["equity_expected"] - merged["equity_actual"]))
        ),
    }
    dates = pd.DatetimeIndex(expected["date"])
    symbols = sorted(expected_positions["symbol"].astype(str).unique())
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    actual_shares = pd.Series(0.0, index=index)
    if not broker_positions.empty:
        positions = broker_positions.copy()
        positions["date"] = pd.to_datetime(positions["date"])
        positions = positions[positions["date"].dt.hour.eq(16)].copy()
        positions["date"] = positions["date"].dt.normalize()
        observed = positions.set_index(["date", "symbol"])["long_shares"].astype(float)
        actual_shares.loc[observed.index] = observed.to_numpy()
    expected_shares = (
        expected_positions.assign(date=pd.to_datetime(expected_positions["date"]).dt.normalize())
        .set_index(["date", "symbol"])["shares"].astype(float).reindex(index).fillna(0.0)
    )
    checks["max_abs_position_shares_difference"] = float(
        np.max(np.abs(actual_shares.to_numpy(float) - expected_shares.to_numpy(float)))
    )
    if max(checks.values()) > tolerance:
        raise AssertionError(f"PyBroker daily state differs: {checks}")
    return checks
