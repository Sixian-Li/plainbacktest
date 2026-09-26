"""Single-security attribution for the frozen Nasdaq-100 Strategy1 >90% gate."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np
import pandas as pd

from quantkit.metrics import calculate_metrics
from quantkit.stochrsi_position_gates import holding_period_cagr_pct


LEDGER_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SingleSecurityGateResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    exposed_returns: pd.DataFrame
    exits: pd.DataFrame
    metrics: dict[str, object]
    cross_checks: dict[str, float]


def _validate_price_frame(price_frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "synthetic_bar",
        "terminal_settlement_proxy",
    }
    missing = required - set(price_frame.columns)
    if missing:
        raise ValueError(f"price frame misses columns: {sorted(missing)}")
    frame = price_frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    if frame.empty:
        raise ValueError("price frame is empty")
    if frame["symbol"].nunique() != 1:
        raise ValueError("price frame must contain exactly one symbol")
    if frame["date"].duplicated().any():
        raise ValueError("price frame contains duplicate dates")
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("price frame contains missing OHLC values")
    if (frame[["open", "high", "low", "close"]].astype(float) <= 0).any().any():
        raise ValueError("price frame contains non-positive OHLC values")
    return frame


def _active_sharpe(returns: pd.Series, annual_bars: int = 252) -> float:
    values = pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(values) < 2:
        return float("nan")
    deviation = float(values.std(ddof=1))
    if deviation <= 0:
        return float("nan")
    return float(values.mean() / deviation * math.sqrt(annual_bars))


def _post_exit_rows(
    frame: pd.DataFrame,
    sell_orders: list[dict[str, object]],
    *,
    horizons: Iterable[int],
) -> pd.DataFrame:
    horizons = tuple(sorted({int(value) for value in horizons}))
    if any(value <= 0 for value in horizons):
        raise ValueError("post-exit horizons must be positive")
    date_to_index = {pd.Timestamp(day): index for index, day in enumerate(frame["date"])}
    rows: list[dict[str, object]] = []
    for order in sell_orders:
        exit_date = pd.Timestamp(order["date"])
        exit_index = date_to_index[exit_date]
        row: dict[str, object] = {
            "symbol": str(order["symbol"]),
            "exit_date": exit_date,
            "signal_date": pd.Timestamp(order["signal_date"]),
            "exit_fill": float(order["fill_price"]),
            "terminal_settlement_exit": int(order["terminal_settlement_proxy"]),
        }
        for horizon in horizons:
            return_key = f"forward_close_return_{horizon}d_pct"
            rebound_key = f"max_high_rebound_{horizon}d_pct"
            target_index = exit_index + horizon
            if int(order["terminal_settlement_proxy"]) or target_index >= len(frame):
                row[return_key] = np.nan
                row[rebound_key] = np.nan
                continue
            target = frame.iloc[target_index]
            window = frame.iloc[exit_index + 1 : target_index + 1]
            target_is_real = int(target["synthetic_bar"]) == 0
            real_window = window[window["synthetic_bar"].astype(int).eq(0)]
            if not target_is_real or real_window.empty:
                row[return_key] = np.nan
                row[rebound_key] = np.nan
                continue
            exit_fill = float(order["fill_price"])
            row[return_key] = (float(target["close"]) / exit_fill - 1.0) * 100.0
            row[rebound_key] = (
                float(real_window["high"].astype(float).max()) / exit_fill - 1.0
            ) * 100.0
        rows.append(row)
    columns = [
        "symbol",
        "exit_date",
        "signal_date",
        "exit_fill",
        "terminal_settlement_exit",
        *[
            name
            for horizon in horizons
            for name in (
                f"forward_close_return_{horizon}d_pct",
                f"max_high_rebound_{horizon}d_pct",
            )
        ],
    ]
    return pd.DataFrame(rows, columns=columns)


def run_single_security_gate(
    price_frame: pd.DataFrame,
    eligible_dates: Iterable[pd.Timestamp | str],
    *,
    initial_cash: float = 100_000.0,
    cost_bps: float = 0.0,
    post_exit_horizons: Iterable[int] = (5, 10, 20, 60),
) -> SingleSecurityGateResult:
    """Run one all-in/all-cash gate using the prior completed Close signal.

    Position changes require a real prior signal bar and a real execution bar.
    The first terminal carry bar may execute a sell, but never a buy.
    """

    frame = _validate_price_frame(price_frame)
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    symbol = str(frame.iloc[0]["symbol"])
    eligible = {pd.Timestamp(value).normalize() for value in eligible_dates}
    frame_dates = set(pd.DatetimeIndex(frame["date"]))
    if not eligible.issubset(frame_dates):
        raise ValueError("eligible dates must be present in the dense price frame")

    impact = float(cost_bps) / 10_000.0
    cash = float(initial_cash)
    shares = 0.0
    entry: dict[str, object] | None = None
    daily_rows: list[dict[str, object]] = []
    orders: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    active_rows: list[dict[str, object]] = []

    previous: pd.Series | None = None
    previous_close = float("nan")
    reconstructed_equity = float(initial_cash)
    max_reconstruction_difference = 0.0
    deferred_change_count = 0

    for current in frame.itertuples(index=False):
        date = pd.Timestamp(current.date)
        current_open = float(current.open)
        current_close = float(current.close)
        current_real = int(current.synthetic_bar) == 0
        terminal_proxy = int(current.terminal_settlement_proxy) == 1
        held_from_prior_close = shares > 1e-12
        action = "hold"
        signal_date = pd.NaT
        desired_long = False
        signal_real = False
        active_return = 0.0
        active_component = "cash"

        if previous is not None:
            signal_date = pd.Timestamp(previous["date"])
            desired_long = signal_date in eligible
            signal_real = int(previous["synthetic_bar"]) == 0
            wants_change = desired_long != held_from_prior_close
            executable_buy = signal_real and current_real
            executable_sell = signal_real and (current_real or terminal_proxy)

            if wants_change and desired_long and executable_buy:
                fill = current_open * (1.0 + impact)
                quantity = cash / fill
                shares = quantity
                cash = 0.0
                action = "buy"
                entry = {
                    "date": date,
                    "signal_date": signal_date,
                    "fill_price": fill,
                    "shares": quantity,
                }
                orders.append(
                    {
                        "date": date,
                        "signal_date": signal_date,
                        "symbol": symbol,
                        "type": "buy",
                        "fill_price": fill,
                        "shares": quantity,
                        "terminal_settlement_proxy": 0,
                    }
                )
                active_return = current_close / fill - 1.0
                active_component = "entry_open_to_close"
            elif wants_change and not desired_long and executable_sell:
                fill = current_open * (1.0 - impact)
                if entry is None:
                    raise AssertionError("sell encountered without an open entry")
                quantity = shares
                cash = quantity * fill
                shares = 0.0
                action = "sell"
                sell_order = {
                    "date": date,
                    "signal_date": signal_date,
                    "symbol": symbol,
                    "type": "sell",
                    "fill_price": fill,
                    "shares": quantity,
                    "terminal_settlement_proxy": int(terminal_proxy),
                }
                orders.append(sell_order)
                trades.append(
                    {
                        "symbol": symbol,
                        "entry_date": entry["date"],
                        "entry_signal_date": entry["signal_date"],
                        "entry_price": entry["fill_price"],
                        "exit_date": date,
                        "exit_signal_date": signal_date,
                        "exit_price": fill,
                        "shares": quantity,
                        "pnl": (fill - float(entry["fill_price"])) * quantity,
                        "return_pct": (fill / float(entry["fill_price"]) - 1.0) * 100.0,
                        "terminal_settlement_exit": int(terminal_proxy),
                    }
                )
                entry = None
                active_return = fill / previous_close - 1.0
                active_component = "exit_prior_close_to_open"
            elif wants_change:
                deferred_change_count += 1

        if action == "hold" and held_from_prior_close:
            active_return = current_close / previous_close - 1.0
            active_component = "held_close_to_close"

        equity = cash + shares * current_close
        if active_component != "cash":
            reconstructed_equity *= 1.0 + active_return
            active_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "component": active_component,
                    "return": active_return,
                    "return_pct": active_return * 100.0,
                }
            )
        max_reconstruction_difference = max(
            max_reconstruction_difference, abs(equity - reconstructed_equity)
        )
        daily_rows.append(
            {
                "date": date,
                "symbol": symbol,
                "cash": cash,
                "shares": shares,
                "equity": equity,
                "is_long": int(shares > 1e-12),
                "signal_date": signal_date,
                "signal_eligible": int(desired_long),
                "signal_real": int(signal_real),
                "execution_real": int(current_real),
                "terminal_settlement_proxy": int(terminal_proxy),
                "action": action,
                "active_return": active_return if active_component != "cash" else np.nan,
                "active_return_component": active_component,
            }
        )
        previous = pd.Series(current._asdict())
        previous_close = current_close

    daily = pd.DataFrame(daily_rows)
    order_frame = pd.DataFrame(
        orders,
        columns=[
            "date",
            "signal_date",
            "symbol",
            "type",
            "fill_price",
            "shares",
            "terminal_settlement_proxy",
        ],
    )
    trade_frame = pd.DataFrame(
        trades,
        columns=[
            "symbol",
            "entry_date",
            "entry_signal_date",
            "entry_price",
            "exit_date",
            "exit_signal_date",
            "exit_price",
            "shares",
            "pnl",
            "return_pct",
            "terminal_settlement_exit",
        ],
    )
    active = pd.DataFrame(
        active_rows,
        columns=["date", "symbol", "component", "return", "return_pct"],
    )
    exits = _post_exit_rows(
        frame,
        [row for row in orders if row["type"] == "sell"],
        horizons=post_exit_horizons,
    )

    base_metrics = calculate_metrics(
        daily,
        order_frame,
        trade_frame,
        initial_cash=float(initial_cash),
    )
    holding_time = float(base_metrics["exposure_pct"])
    base_metrics.update(
        {
            "holding_time_pct": holding_time,
            "holding_sessions": int(daily["is_long"].sum()),
            "holding_return_observations": int(len(active)),
            "holding_period_cagr_pct": holding_period_cagr_pct(
                float(base_metrics["cagr_pct"]), holding_time
            ),
            "holding_return_sharpe": _active_sharpe(active["return"]),
            "entry_count": int((order_frame["type"] == "buy").sum()) if not order_frame.empty else 0,
            "exit_count": int((order_frame["type"] == "sell").sum()) if not order_frame.empty else 0,
            "terminal_settlement_exit_count": int(
                order_frame.loc[order_frame["type"].eq("sell"), "terminal_settlement_proxy"].sum()
            ) if not order_frame.empty else 0,
            "deferred_change_count": int(deferred_change_count),
        }
    )
    for horizon in sorted({int(value) for value in post_exit_horizons}):
        return_column = f"forward_close_return_{horizon}d_pct"
        rebound_column = f"max_high_rebound_{horizon}d_pct"
        valid_returns = exits[return_column].dropna().astype(float)
        valid_rebounds = exits[rebound_column].dropna().astype(float)
        base_metrics.update(
            {
                f"post_exit_{horizon}d_count": int(len(valid_returns)),
                f"post_exit_{horizon}d_mean_return_pct": float(valid_returns.mean()) if len(valid_returns) else np.nan,
                f"post_exit_{horizon}d_median_return_pct": float(valid_returns.median()) if len(valid_returns) else np.nan,
                f"post_exit_{horizon}d_positive_pct": float((valid_returns > 0).mean() * 100.0) if len(valid_returns) else np.nan,
                f"post_exit_{horizon}d_mean_max_rebound_pct": float(valid_rebounds.mean()) if len(valid_rebounds) else np.nan,
            }
        )

    cross_checks = {
        "max_daily_equity_reconstruction_difference": float(max_reconstruction_difference),
        "final_equity_reconstruction_difference": float(
            abs(float(daily.iloc[-1]["equity"]) - reconstructed_equity)
        ),
    }
    if any(value > LEDGER_TOLERANCE for value in cross_checks.values()):
        raise AssertionError(f"single-security ledger reconciliation failed: {cross_checks}")
    return SingleSecurityGateResult(
        daily=daily,
        orders=order_frame,
        trades=trade_frame,
        exposed_returns=active,
        exits=exits,
        metrics=base_metrics,
        cross_checks=cross_checks,
    )
