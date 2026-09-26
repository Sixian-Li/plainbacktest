"""Point-in-time Nasdaq-100 portfolios for original, strict, and difference P24 states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quantkit.full_position_trend_quality import build_target_long, rolling_log_slope_r2
from quantkit.metrics import calculate_metrics


ORIGINAL_P24 = "ORIGINAL_P24"
ORIGINAL_NOT_STRICT = "ORIGINAL_NOT_STRICT"
STRICT_P24 = "STRICT_P24"
CASE_IDS = (ORIGINAL_P24, ORIGINAL_NOT_STRICT, STRICT_P24)
STRICT_LEVEL_DAILY_EQUAL = "STRICT_LEVEL_DAILY_EQUAL"
STRICT_LEVEL_CHANGE_EQUAL = "STRICT_LEVEL_CHANGE_EQUAL"
STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL = "STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL"
STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL = "STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL"
HOLD_STRICT_LEVEL = "STRICT_LEVEL"
HOLD_STRICT_ENTRY_ORIGINAL_EXIT = "STRICT_ENTRY_ORIGINAL_EXIT"
REBALANCE_DAILY_EQUAL = "DAILY_EQUAL"
REBALANCE_SELECTION_CHANGE_EQUAL = "SELECTION_CHANGE_EQUAL"
FACTORIAL_CASES = {
    STRICT_LEVEL_DAILY_EQUAL: {
        "holding_policy": HOLD_STRICT_LEVEL,
        "rebalance_policy": REBALANCE_DAILY_EQUAL,
    },
    STRICT_LEVEL_CHANGE_EQUAL: {
        "holding_policy": HOLD_STRICT_LEVEL,
        "rebalance_policy": REBALANCE_SELECTION_CHANGE_EQUAL,
    },
    STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL: {
        "holding_policy": HOLD_STRICT_ENTRY_ORIGINAL_EXIT,
        "rebalance_policy": REBALANCE_DAILY_EQUAL,
    },
    STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL: {
        "holding_policy": HOLD_STRICT_ENTRY_ORIGINAL_EXIT,
        "rebalance_policy": REBALANCE_SELECTION_CHANGE_EQUAL,
    },
}
LEDGER_TOLERANCE = 1e-6
MINIMUM_ORDER_NOTIONAL = 1e-9


@dataclass(frozen=True)
class P24PortfolioResult:
    daily: pd.DataFrame
    positions: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    selections: pd.DataFrame
    metrics: dict[str, Any]
    replay_checks: dict[str, float]


def prepare_security_state(
    raw: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    parameters: Mapping[str, Any],
) -> pd.DataFrame:
    """Compute both P24 target states without inheriting a pre-window streak."""

    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    if missing := required.difference(raw.columns):
        raise ValueError(f"price frame misses {sorted(missing)}")
    if raw.empty or raw["symbol"].nunique() != 1:
        raise ValueError("one non-empty security is required")
    rows = raw.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values("date", kind="stable").drop_duplicates("date", keep="last")
    fixed = parameters["fixed_parameters"]
    original = parameters["original_thresholds"]
    strict = parameters["strict_thresholds"]
    close = rows["close"].astype(float)
    long_window = int(fixed["long_sma_window"])
    long_lookback = int(fixed["long_slope_lookback"])
    short_window = int(fixed["short_sma_window"])
    regression_window = int(fixed["short_regression_window"])
    confirmation = int(fixed["entry_confirmation_sessions"])
    rows["long_sma"] = close.rolling(long_window, min_periods=long_window).mean()
    rows["long_slope_daily_pct"] = (
        rows["long_sma"] / rows["long_sma"].shift(long_lookback) - 1.0
    ) / long_lookback * 100.0
    rows["short_sma"] = close.rolling(short_window, min_periods=short_window).mean()
    rows["short_quality_daily_pct"] = rolling_log_slope_r2(
        rows["short_sma"], regression_window
    )
    indexed = rows.set_index("date").reindex(calendar)
    indexed["real_bar"] = indexed["close"].notna()
    price_condition = indexed["close"] > indexed["long_sma"]
    original_eligible = (
        price_condition
        & indexed["long_slope_daily_pct"].gt(float(original["long_slope_threshold_daily_pct"]))
        & indexed["short_quality_daily_pct"].gt(float(original["short_quality_threshold_daily_pct"]))
        & indexed["real_bar"]
    ).fillna(False)
    strict_eligible = (
        price_condition
        & indexed["long_slope_daily_pct"].gt(float(strict["long_slope_threshold_daily_pct"]))
        & indexed["short_quality_daily_pct"].gt(float(strict["short_quality_threshold_daily_pct"]))
        & indexed["real_bar"]
    ).fillna(False)
    indexed["original_eligible"] = original_eligible
    indexed["strict_eligible"] = strict_eligible
    indexed["original_target_long"] = build_target_long(
        original_eligible.to_numpy(bool), confirmation
    )
    indexed["strict_target_long"] = build_target_long(
        strict_eligible.to_numpy(bool), confirmation
    )
    if (indexed["strict_target_long"] & ~indexed["original_target_long"]).any():
        raise AssertionError("strict P24 must be a subset of original P24")
    indexed["difference_target_long"] = (
        indexed["original_target_long"] & ~indexed["strict_target_long"]
    )
    indexed["symbol"] = str(rows.iloc[0]["symbol"])
    return indexed.reset_index(names="date")


def selected_ids(snapshot: pd.DataFrame, case_id: str) -> list[str]:
    if case_id not in CASE_IDS:
        raise ValueError(f"unknown case: {case_id}")
    required = {
        "security_id", "in_universe", "real_bar", "original_target_long",
        "strict_target_long", "difference_target_long",
    }
    if missing := required.difference(snapshot.columns):
        raise ValueError(f"selection snapshot misses {sorted(missing)}")
    column = {
        ORIGINAL_P24: "original_target_long",
        ORIGINAL_NOT_STRICT: "difference_target_long",
        STRICT_P24: "strict_target_long",
    }[case_id]
    chosen = snapshot[
        snapshot["in_universe"].astype(bool)
        & snapshot["real_bar"].astype(bool)
        & snapshot[column].astype(bool)
    ]
    return sorted(chosen["security_id"].astype(str).tolist())


def factorial_selected_ids(
    snapshot: pd.DataFrame,
    holding_policy: str,
    previous_selected: set[str],
) -> list[str]:
    """Apply either a strict level or strict-entry/original-exit state policy."""

    required = {
        "security_id", "in_universe", "real_bar", "original_target_long",
        "strict_target_long",
    }
    if missing := required.difference(snapshot.columns):
        raise ValueError(f"factorial selection snapshot misses {sorted(missing)}")
    if holding_policy not in {HOLD_STRICT_LEVEL, HOLD_STRICT_ENTRY_ORIGINAL_EXIT}:
        raise ValueError(f"unknown holding policy: {holding_policy}")
    available = snapshot["in_universe"].astype(bool) & snapshot["real_bar"].astype(bool)
    original = available & snapshot["original_target_long"].astype(bool)
    strict = available & snapshot["strict_target_long"].astype(bool)
    if (strict & ~original).any():
        raise AssertionError("strict P24 must remain a subset of original P24")
    strict_ids = set(snapshot.loc[strict, "security_id"].astype(str))
    if holding_policy == HOLD_STRICT_LEVEL:
        return sorted(strict_ids)
    original_ids = set(snapshot.loc[original, "security_id"].astype(str))
    return sorted(strict_ids | (set(previous_selected) & original_ids))


def _empty_orders() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "id", "date", "signal_date", "symbol", "type", "reason", "fill_timing",
        "reference_price", "fill_price", "shares", "target_shares_after", "sequence",
    ])


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "id", "type", "symbol", "entry_date", "exit_date", "entry", "exit",
        "shares", "pnl", "return_pct", "exit_reason",
    ])


def _validate_inputs(
    state: pd.DataFrame, price_panel: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex, list[str]]:
    state_required = {
        "date", "security_id", "display_ticker", "in_universe", "real_bar",
        "original_target_long", "strict_target_long", "difference_target_long",
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
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["symbol"] = panel["symbol"].astype(str)
    if states.duplicated(["date", "security_id"]).any() or panel.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate security dates are not allowed")
    prices = panel[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("price panel requires finite positive OHLC")
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    symbols = sorted(panel["symbol"].unique())
    expected = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    actual = pd.MultiIndex.from_frame(panel[["date", "symbol"]]).sort_values()
    if len(actual) != len(expected) or not actual.equals(expected):
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
    checks = {
        "max_abs_cash_difference": 0.0,
        "max_abs_equity_difference": 0.0,
        "max_abs_position_shares_difference": 0.0,
    }
    for date in expected_daily.index:
        for order in grouped.get(pd.Timestamp(date), _empty_orders()).itertuples(index=False):
            quantity = float(order.shares)
            if order.type == "sell":
                cash += quantity * float(order.fill_price)
                shares[str(order.symbol)] -= quantity
            elif order.type == "buy":
                cash -= quantity * float(order.fill_price)
                shares[str(order.symbol)] += quantity
            else:
                raise AssertionError(f"unknown order side: {order.type}")
        expected = expected_positions.loc[date].reindex(symbols).fillna(0.0)
        equity = cash + float((shares * closes.loc[date].reindex(symbols)).sum())
        checks["max_abs_cash_difference"] = max(
            checks["max_abs_cash_difference"], abs(cash - float(expected_daily.at[date, "cash"]))
        )
        checks["max_abs_equity_difference"] = max(
            checks["max_abs_equity_difference"], abs(equity - float(expected_daily.at[date, "equity"]))
        )
        checks["max_abs_position_shares_difference"] = max(
            checks["max_abs_position_shares_difference"], float((shares - expected).abs().max())
        )
    if max(checks.values()) > LEDGER_TOLERANCE:
        raise AssertionError(f"P24 portfolio replay failed: {checks}")
    return checks


def _run_p24_portfolio_impl(
    state: pd.DataFrame,
    price_panel: pd.DataFrame,
    case_id: str,
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
    holding_policy: str | None = None,
    rebalance_policy: str = REBALANCE_DAILY_EQUAL,
) -> P24PortfolioResult:
    """Daily Close selection with explicit next-Open equal-weight execution policy."""

    states, panel, dates, symbols = _validate_inputs(state, price_panel)
    if holding_policy is None and case_id not in CASE_IDS:
        raise ValueError("invalid legacy P24 case")
    if holding_policy is not None and holding_policy not in {
        HOLD_STRICT_LEVEL, HOLD_STRICT_ENTRY_ORIGINAL_EXIT,
    }:
        raise ValueError("invalid holding policy")
    if rebalance_policy not in {REBALANCE_DAILY_EQUAL, REBALANCE_SELECTION_CHANGE_EQUAL}:
        raise ValueError("invalid rebalance policy")
    if initial_cash <= 0 or cost_bps < 0:
        raise ValueError("invalid case, initial cash, or cost")
    impact = float(cost_bps) / 10_000.0
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    synthetic = panel.pivot(index="date", columns="symbol", values="synthetic_bar").reindex(dates).astype(bool)
    terminal = panel.pivot(index="date", columns="symbol", values="terminal_settlement_proxy").reindex(dates).astype(bool)
    state_by_date = {pd.Timestamp(day): frame for day, frame in states.groupby("date")}
    display = (
        states[["security_id", "display_ticker"]].drop_duplicates("security_id")
        .set_index("security_id")["display_ticker"].astype(str).to_dict()
    )
    cash = float(initial_cash)
    shares = pd.Series(0.0, index=symbols)
    lots: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    pending: dict[str, Any] | None = None
    order_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    order_id = trade_id = sequence = unavailable_deferrals = terminal_settlements = 0
    logical_selected: set[str] = set()
    previous_signal_selected: set[str] | None = None
    retry_rebalance = False
    rebalance_execution_count = 0
    selection_change_count = 0
    skipped_unchanged_selection_count = 0

    def order(
        date: pd.Timestamp,
        signal_date: pd.Timestamp,
        symbol: str,
        side: str,
        reference: float,
        quantity: float,
    ) -> None:
        nonlocal cash, order_id, trade_id, sequence, terminal_settlements
        quantity = min(float(quantity), float(shares[symbol])) if side == "sell" else float(quantity)
        if quantity <= 0.0 or quantity * float(reference) <= MINIMUM_ORDER_NOTIONAL:
            return
        fill = reference * (1.0 - impact if side == "sell" else 1.0 + impact)
        reason_prefix = (
            "DAILY_EQUAL_WEIGHT"
            if rebalance_policy == REBALANCE_DAILY_EQUAL
            else "SELECTION_CHANGE_EQUAL_WEIGHT"
        )
        reason = f"{reason_prefix}_{'EXIT' if side == 'sell' else 'ENTRY'}"
        if side == "sell":
            remaining = quantity
            while remaining > 1e-12 and lots[symbol]:
                lot = lots[symbol][0]
                matched = min(remaining, float(lot["shares"]))
                trade_id += 1
                trade_rows.append({
                    "id": trade_id, "type": "long", "symbol": symbol,
                    "entry_date": lot["date"], "exit_date": date,
                    "entry": float(lot["price"]), "exit": fill, "shares": matched,
                    "pnl": matched * (fill - float(lot["price"])),
                    "return_pct": (fill / float(lot["price"]) - 1.0) * 100.0,
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
            if bool(terminal.at[date, symbol]):
                terminal_settlements += 1
        elif side == "buy":
            quantity = min(quantity, max(cash, 0.0) / fill)
            if quantity <= 0.0 or quantity * fill <= MINIMUM_ORDER_NOTIONAL:
                return
            cash -= quantity * fill
            shares[symbol] += quantity
            lots[symbol].append({"date": date, "price": fill, "shares": quantity})
        else:
            raise ValueError(f"unknown order side: {side}")
        order_id += 1
        sequence += 1
        order_rows.append({
            "id": order_id, "date": date, "signal_date": signal_date,
            "symbol": symbol, "type": side, "reason": reason, "fill_timing": "open",
            "reference_price": reference, "fill_price": fill, "shares": quantity,
            "target_shares_after": float(shares[symbol]), "sequence": sequence,
        })

    for index, date_value in enumerate(dates):
        date = pd.Timestamp(date_value)
        if pending is not None:
            signal_date = pd.Timestamp(pending["signal_date"])
            selected = set(map(str, pending["selected"]))
            if bool(pending["execute_rebalance"]):
                rebalance_execution_count += 1
                target = pd.Series(0.0, index=symbols)
                if selected:
                    each = 1.0 / len(selected)
                    for symbol in selected:
                        target[symbol] = (
                            float(pending["signal_equity"])
                            * each
                            / float(closes.at[signal_date, symbol])
                        )
                deferred = (synthetic.loc[signal_date] | synthetic.loc[date]) & ~terminal.loc[date]
                changed = (target - shares).abs() > 1e-12
                unavailable_deferrals += int((deferred & changed).sum())
                target.loc[deferred] = shares.loc[deferred]
                terminal_buy = terminal.loc[date] & (target > shares + 1e-12)
                unavailable_deferrals += int(terminal_buy.sum())
                target.loc[terminal_buy] = shares.loc[terminal_buy]
                retry_rebalance = bool((deferred & changed).any() or terminal_buy.any())
                for symbol in symbols:
                    quantity = max(float(shares[symbol] - target[symbol]), 0.0)
                    if quantity > 1e-12:
                        order(date, signal_date, symbol, "sell", float(opens.at[date, symbol]), quantity)
                for symbol in symbols:
                    quantity = max(float(target[symbol] - shares[symbol]), 0.0)
                    if quantity > 1e-12:
                        order(date, signal_date, symbol, "buy", float(opens.at[date, symbol]), quantity)
            else:
                retry_rebalance = False
            pending = None
        market_value = shares * closes.loc[date]
        equity = cash + float(market_value.sum())
        daily_rows.append({
            "date": date, "cash": cash, "shares": float(shares.sum()), "equity": equity,
            "is_long": int((shares > 1e-12).any()),
            "gross_exposure": float(market_value.sum() / equity) if equity > 0 else 0.0,
            "holdings_count": int((shares > 1e-12).sum()),
        })
        for symbol in symbols:
            position_rows.append({"date": date, "symbol": symbol, "shares": float(shares[symbol])})
        if index < len(dates) - 1:
            snapshot = state_by_date.get(date, states.iloc[0:0])
            if holding_policy is None:
                chosen = selected_ids(snapshot, case_id) if len(snapshot) else []
            else:
                chosen = (
                    factorial_selected_ids(snapshot, holding_policy, logical_selected)
                    if len(snapshot)
                    else []
                )
                logical_selected = set(chosen)
            chosen_set = set(chosen)
            selection_changed = (
                previous_signal_selected is None or chosen_set != previous_signal_selected
            )
            if selection_changed:
                selection_change_count += 1
            execute_rebalance = (
                rebalance_policy == REBALANCE_DAILY_EQUAL
                or selection_changed
                or retry_rebalance
            )
            if not execute_rebalance:
                skipped_unchanged_selection_count += 1
            pending = {
                "signal_date": date,
                "selected": chosen,
                "signal_equity": equity,
                "execute_rebalance": execute_rebalance,
            }
            previous_signal_selected = chosen_set
            for rank, symbol in enumerate(chosen, start=1):
                selection_rows.append({
                    "date": date, "security_id": symbol,
                    "display_ticker": display.get(symbol, symbol), "rank": rank,
                })
    daily = pd.DataFrame(daily_rows)
    positions = pd.DataFrame(position_rows)
    orders = pd.DataFrame(order_rows) if order_rows else _empty_orders()
    trades = pd.DataFrame(trade_rows) if trade_rows else _empty_trades()
    selections = pd.DataFrame(
        selection_rows, columns=["date", "security_id", "display_ticker", "rank"]
    )
    metrics = calculate_metrics(daily, orders, trades, initial_cash=float(initial_cash))
    held = int(daily["is_long"].sum())
    holding_cagr = (
        ((float(daily.iloc[-1]["equity"]) / float(initial_cash)) ** (252.0 / held) - 1.0) * 100.0
        if held else float("nan")
    )
    metrics.update({
        "case_id": case_id,
        "holding_sessions": held,
        "holding_years_252": held / 252.0,
        "holding_cagr_pct": holding_cagr,
        "average_holdings_count": float(daily["holdings_count"].mean()),
        "median_holdings_count": float(daily["holdings_count"].median()),
        "minimum_positive_holdings_count": int(daily.loc[daily["holdings_count"].gt(0), "holdings_count"].min()) if held else 0,
        "maximum_holdings_count": int(daily["holdings_count"].max()),
        "selected_security_sessions": int(len(selections)),
        "closed_lot_win_rate_pct": float(trades["pnl"].gt(0).mean() * 100.0) if len(trades) else float("nan"),
        "unavailable_target_deferral_count": int(unavailable_deferrals),
        "terminal_settlement_order_count": int(terminal_settlements),
        "holding_policy": holding_policy or f"LEGACY_{case_id}",
        "rebalance_policy": rebalance_policy,
        "rebalance_execution_count": int(rebalance_execution_count),
        "selection_change_count": int(selection_change_count),
        "skipped_unchanged_selection_count": int(skipped_unchanged_selection_count),
    })
    checks = _replay_orders(daily, positions, orders, panel, initial_cash=float(initial_cash))
    return P24PortfolioResult(daily, positions, orders, trades, selections, metrics, checks)


def run_p24_portfolio(
    state: pd.DataFrame,
    price_panel: pd.DataFrame,
    case_id: str,
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
) -> P24PortfolioResult:
    """Preserve the parent experiment's daily equal-weight level-state semantics."""

    return _run_p24_portfolio_impl(
        state,
        price_panel,
        case_id,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
    )


def run_p24_factorial_portfolio(
    state: pd.DataFrame,
    price_panel: pd.DataFrame,
    case_id: str,
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
) -> P24PortfolioResult:
    """Run one frozen holding-policy by rebalance-policy factorial case."""

    try:
        case = FACTORIAL_CASES[case_id]
    except KeyError as exc:
        raise ValueError(f"unknown P24 factorial case: {case_id}") from exc
    return _run_p24_portfolio_impl(
        state,
        price_panel,
        case_id,
        initial_cash=initial_cash,
        cost_bps=cost_bps,
        holding_policy=case["holding_policy"],
        rebalance_policy=case["rebalance_policy"],
    )
