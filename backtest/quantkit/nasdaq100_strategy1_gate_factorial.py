"""Factorial single-security gates around the frozen Nasdaq-100 Strategy1 score."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from quantkit.metrics import calculate_metrics
from quantkit.stochrsi_position_gates import holding_period_cagr_pct


LEDGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class GateFactorSpec:
    mechanical_stop: bool = False
    strict_entry: bool = False
    fast_exit: bool = False
    gate_threshold: float = 0.90
    stop_loss_pct: float = 0.05
    stop_reset_weight: float = 0.10
    strict_stochrsi_threshold: float = 0.20
    fast_exit_threshold: float = 0.80
    fast_reset_weight: float = 0.20

    @property
    def case_id(self) -> str:
        return (
            f"STOP{int(self.mechanical_stop)}_"
            f"STRICT{int(self.strict_entry)}_FAST{int(self.fast_exit)}"
        )


@dataclass(frozen=True)
class GateFactorResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    active_returns: pd.DataFrame
    metrics: dict[str, Any]
    cross_checks: dict[str, float]


def factorial_specs() -> list[GateFactorSpec]:
    return [
        GateFactorSpec(bool(stop), bool(strict), bool(fast))
        for stop in (0, 1)
        for strict in (0, 1)
        for fast in (0, 1)
    ]


def _validate(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date", "symbol", "open", "high", "low", "close", "synthetic_bar",
        "terminal_settlement_proxy", "strategy1_weight", "stochrsi_42", "stochrsi_100",
    }
    if missing := required.difference(frame.columns):
        raise ValueError(f"factorial input misses columns: {sorted(missing)}")
    rows = frame.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values("date", kind="stable").reset_index(drop=True)
    if rows.empty or rows["symbol"].nunique() != 1 or rows["date"].duplicated().any():
        raise ValueError("one non-empty symbol with unique dates is required")
    prices = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC must be finite and positive")
    state = rows[["strategy1_weight", "stochrsi_42", "stochrsi_100"]].to_numpy(float)
    if np.isinf(state).any():
        raise ValueError("signal state cannot contain infinity")
    finite = state[np.isfinite(state)]
    if len(finite) and ((finite < -1e-12) | (finite > 1.0 + 1e-12)).any():
        raise ValueError("signal state must stay in the 0-1 range after warm-up")
    return rows


def _active_sharpe(returns: pd.Series, annual_bars: int = 252) -> float:
    values = pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(values) < 2:
        return float("nan")
    deviation = float(values.std(ddof=1))
    return float(values.mean() / deviation * math.sqrt(annual_bars)) if deviation > 0 else float("nan")


def _empty_orders() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "signal_date", "symbol", "type", "reason", "fill_price", "shares",
        "reference_price", "terminal_settlement_proxy", "sequence",
    ])


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "symbol", "entry_date", "entry_signal_date", "entry_price", "exit_date",
        "exit_signal_date", "exit_price", "shares", "pnl", "return_pct", "exit_reason",
    ])


def _replay_orders(
    frame: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    initial_cash: float,
) -> tuple[float, float, float]:
    """Independently replay immutable fills and compare each Close account state."""

    grouped = {day: part.sort_values("sequence") for day, part in orders.groupby("date")}
    cash = float(initial_cash)
    shares = 0.0
    maximum_equity_difference = 0.0
    maximum_cash_difference = 0.0
    maximum_share_difference = 0.0
    for row in frame.itertuples(index=False):
        date = pd.Timestamp(row.date)
        for order in grouped.get(date, _empty_orders()).itertuples(index=False):
            quantity = float(order.shares)
            fill = float(order.fill_price)
            if order.type == "buy":
                cash -= quantity * fill
                shares += quantity
            elif order.type == "sell":
                cash += quantity * fill
                shares -= quantity
            else:
                raise AssertionError(f"unknown replay side: {order.type}")
        maximum_equity_difference = max(
            maximum_equity_difference,
            abs((cash + shares * float(row.close)) - float(row.equity)),
        )
        maximum_cash_difference = max(maximum_cash_difference, abs(cash - float(row.cash)))
        maximum_share_difference = max(maximum_share_difference, abs(shares - float(row.shares)))
    return maximum_equity_difference, maximum_cash_difference, maximum_share_difference


def run_factorial_gate(
    state_frame: pd.DataFrame,
    spec: GateFactorSpec,
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
) -> GateFactorResult:
    """Run one all-stock/all-cash path using prior-Close gates and explicit overrides.

    Normal gate transitions use the prior completed real Close and current real Open.
    A stop can act from the session after entry using adjusted Open/Low.  A fast exit
    can act at the current adjusted Close only for a position carried into the day.
    """

    frame = _validate(state_frame)
    if initial_cash <= 0 or cost_bps < 0:
        raise ValueError("initial cash must be positive and cost must be non-negative")
    impact = float(cost_bps) / 10_000.0
    cash = float(initial_cash)
    shares = 0.0
    entry: dict[str, Any] | None = None
    stop_locked = False
    fast_locked = False
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    active_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous: pd.Series | None = None
    previous_close = float("nan")
    sequence = 0
    deferred_change_count = 0

    def execute_buy(date: pd.Timestamp, signal_date: pd.Timestamp, reference: float) -> None:
        nonlocal cash, shares, entry, sequence
        fill = reference * (1.0 + impact)
        quantity = cash / fill
        shares = quantity
        cash = 0.0
        sequence += 1
        entry = {
            "date": date, "signal_date": signal_date, "fill_price": fill, "shares": quantity,
        }
        orders.append({
            "date": date, "signal_date": signal_date, "symbol": str(frame.iloc[0]["symbol"]),
            "type": "buy", "reason": "GATE_ENTRY", "fill_price": fill, "shares": quantity,
            "reference_price": reference, "terminal_settlement_proxy": 0, "sequence": sequence,
        })

    def execute_sell(
        date: pd.Timestamp,
        signal_date: pd.Timestamp,
        reference: float,
        reason: str,
        terminal_proxy: int = 0,
    ) -> None:
        nonlocal cash, shares, entry, sequence
        if entry is None or shares <= 1e-12:
            raise AssertionError("sell encountered without an open entry")
        fill = reference * (1.0 - impact)
        quantity = shares
        cash += quantity * fill
        shares = 0.0
        sequence += 1
        orders.append({
            "date": date, "signal_date": signal_date, "symbol": str(frame.iloc[0]["symbol"]),
            "type": "sell", "reason": reason, "fill_price": fill, "shares": quantity,
            "reference_price": reference, "terminal_settlement_proxy": int(terminal_proxy),
            "sequence": sequence,
        })
        trades.append({
            "symbol": str(frame.iloc[0]["symbol"]), "entry_date": entry["date"],
            "entry_signal_date": entry["signal_date"], "entry_price": entry["fill_price"],
            "exit_date": date, "exit_signal_date": signal_date, "exit_price": fill,
            "shares": quantity, "pnl": (fill - float(entry["fill_price"])) * quantity,
            "return_pct": (fill / float(entry["fill_price"]) - 1.0) * 100.0,
            "exit_reason": reason,
        })
        entry = None

    for current in frame.itertuples(index=False):
        date = pd.Timestamp(current.date)
        current_real = int(current.synthetic_bar) == 0
        terminal_proxy = int(current.terminal_settlement_proxy) == 1
        held_from_prior_close = shares > 1e-12
        action = "HOLD"
        active_return = float("nan")
        active_component = "cash"
        signal_date = pd.NaT
        prior_score = float("nan")
        prior_entry_ok = False

        if previous is not None:
            signal_date = pd.Timestamp(previous["date"])
            signal_real = int(previous["synthetic_bar"]) == 0
            prior_score = float(previous["strategy1_weight"])
            desired_long = signal_real and np.isfinite(prior_score) and prior_score > spec.gate_threshold
            prior_entry_ok = desired_long
            if spec.strict_entry:
                prior_entry_ok = prior_entry_ok and (
                    float(previous["stochrsi_42"]) > spec.strict_stochrsi_threshold
                    and float(previous["stochrsi_100"]) > spec.strict_stochrsi_threshold
                )
            executable_buy = signal_real and current_real and not terminal_proxy
            executable_sell = signal_real and (current_real or terminal_proxy)
            if held_from_prior_close and not desired_long and executable_sell:
                execute_sell(
                    date, signal_date, float(current.open), "GATE_EXIT", int(terminal_proxy)
                )
                action = "SELL_GATE_OPEN"
                active_return = float(orders[-1]["fill_price"]) / previous_close - 1.0
                active_component = "exit_prior_close_to_open"
            elif not held_from_prior_close and prior_entry_ok and not stop_locked and not fast_locked:
                if executable_buy:
                    execute_buy(date, signal_date, float(current.open))
                    action = "BUY_GATE_OPEN"
                    active_return = float(current.close) / float(entry["fill_price"]) - 1.0
                    active_component = "entry_open_to_close"
                else:
                    deferred_change_count += 1
            elif held_from_prior_close and not desired_long and not executable_sell:
                deferred_change_count += 1

        # The mechanical stop starts only after the entry session and precedes Close exits.
        if (
            spec.mechanical_stop
            and held_from_prior_close
            and shares > 1e-12
            and current_real
            and entry is not None
        ):
            stop_reference = float(entry["fill_price"]) * (1.0 - spec.stop_loss_pct)
            stop_fill_reference = float("nan")
            reason = ""
            if float(current.open) <= stop_reference:
                stop_fill_reference = float(current.open)
                reason = "STOP_GAP_OPEN"
            elif float(current.low) <= stop_reference:
                stop_fill_reference = stop_reference
                reason = "STOP_INTRADAY"
            if np.isfinite(stop_fill_reference):
                execute_sell(date, date, stop_fill_reference, reason)
                stop_locked = True
                action = f"SELL_{reason}"
                active_return = float(orders[-1]["fill_price"]) / previous_close - 1.0
                active_component = "exit_prior_close_to_stop"

        # Fast exit is a true >=0.80 to <0.80 Close crossing for a carried position.
        if (
            spec.fast_exit
            and held_from_prior_close
            and shares > 1e-12
            and current_real
            and previous is not None
            and int(previous["synthetic_bar"]) == 0
            and float(previous["stochrsi_100"]) >= spec.fast_exit_threshold
            and float(current.stochrsi_100) < spec.fast_exit_threshold
        ):
            execute_sell(date, date, float(current.close), "FAST_DOWNCROSS_080")
            fast_locked = True
            action = "SELL_FAST_DOWNCROSS_CLOSE"
            active_return = float(orders[-1]["fill_price"]) / previous_close - 1.0
            active_component = "exit_close_to_close"

        if action == "HOLD" and held_from_prior_close:
            active_return = float(current.close) / previous_close - 1.0
            active_component = "held_close_to_close"

        # Locks reset on the current completed mother score, never on the outer position.
        current_score = float(current.strategy1_weight)
        if current_real and np.isfinite(current_score):
            if stop_locked and current_score < spec.stop_reset_weight:
                stop_locked = False
            if fast_locked and current_score < spec.fast_reset_weight:
                fast_locked = False

        equity = cash + shares * float(current.close)
        if active_component != "cash":
            active_rows.append({
                "date": date, "symbol": str(current.symbol), "component": active_component,
                "return": active_return, "return_pct": active_return * 100.0,
            })
        daily_rows.append({
            "date": date, "symbol": str(current.symbol), "cash": cash, "shares": shares,
            "close": float(current.close), "equity": equity, "is_long": int(shares > 1e-12),
            "strategy1_weight": current_score,
            "stochrsi_42": float(current.stochrsi_42), "stochrsi_100": float(current.stochrsi_100),
            "signal_date": signal_date, "signal_strategy1_weight": prior_score,
            "strict_entry_signal": int(prior_entry_ok), "stop_locked": int(stop_locked),
            "fast_locked": int(fast_locked), "synthetic_bar": int(current.synthetic_bar),
            "terminal_settlement_proxy": int(terminal_proxy), "action": action,
            "active_return": active_return, "active_return_component": active_component,
        })
        previous = pd.Series(current._asdict())
        previous_close = float(current.close)

    daily = pd.DataFrame(daily_rows)
    order_frame = pd.DataFrame(orders) if orders else _empty_orders()
    trade_frame = pd.DataFrame(trades) if trades else _empty_trades()
    active = pd.DataFrame(active_rows, columns=["date", "symbol", "component", "return", "return_pct"])
    base = calculate_metrics(daily, order_frame, trade_frame, initial_cash=float(initial_cash))
    holding_time = float(base["exposure_pct"])
    base.update({
        "case_id": spec.case_id,
        "holding_time_pct": holding_time,
        "holding_sessions": int(daily["is_long"].sum()),
        "holding_return_observations": int(len(active)),
        "holding_period_cagr_pct": holding_period_cagr_pct(float(base["cagr_pct"]), holding_time),
        "holding_return_sharpe": _active_sharpe(active["return"]),
        "entry_count": int((order_frame["type"] == "buy").sum()),
        "exit_count": int((order_frame["type"] == "sell").sum()),
        "stop_exit_count": int(order_frame["reason"].astype(str).str.startswith("STOP").sum()),
        "fast_exit_count": int(order_frame["reason"].eq("FAST_DOWNCROSS_080").sum()),
        "gate_exit_count": int(order_frame["reason"].eq("GATE_EXIT").sum()),
        "deferred_change_count": int(deferred_change_count),
    })
    replay = _replay_orders(daily, order_frame, initial_cash=initial_cash)
    checks = {
        "max_daily_equity_replay_difference": replay[0],
        "max_daily_cash_replay_difference": replay[1],
        "max_daily_share_replay_difference": replay[2],
    }
    if any(value > LEDGER_TOLERANCE for value in checks.values()):
        raise AssertionError(f"factorial ledger reconciliation failed: {checks}")
    return GateFactorResult(daily, order_frame, trade_frame, active, base, checks)
