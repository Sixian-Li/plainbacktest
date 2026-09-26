"""Transparent metrics computed from standardized daily account state."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def max_drawdown_duration(drawdown: pd.Series) -> tuple[int, int]:
    max_bars = 0
    max_days = 0
    start: pd.Timestamp | None = None
    bars = 0
    for day, value in drawdown.items():
        day = pd.Timestamp(day)
        if value < -1e-15:
            if start is None:
                start = day
                bars = 1
            else:
                bars += 1
            max_bars = max(max_bars, bars)
            max_days = max(max_days, (day - start).days)
        else:
            start = None
            bars = 0
    return max_bars, max_days


def calculate_metrics(
    daily: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    initial_cash: float,
    annual_bars: int = 252,
) -> dict[str, Any]:
    state = daily.copy()
    state["date"] = pd.to_datetime(state["date"])
    state = state.sort_values("date").set_index("date")
    equity = state["equity"].astype(float)
    returns = equity.pct_change().fillna(0.0)
    elapsed_days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = elapsed_days / 365.2425
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    cagr = float((equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0)
    volatility = float(returns.std(ddof=1) * math.sqrt(annual_bars))
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(annual_bars))
        if returns.std(ddof=1) > 0
        else float("nan")
    )
    downside = np.minimum(returns.to_numpy(dtype=float), 0.0)
    downside_dev = float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(annual_bars))
    sortino = (
        float(returns.mean() * annual_bars / downside_dev)
        if downside_dev > 0
        else float("nan")
    )
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    dd_bars, dd_days = max_drawdown_duration(drawdown)

    order_notional = 0.0
    if not orders.empty:
        order_notional = float(
            (orders["shares"].astype(float) * orders["fill_price"].astype(float)).abs().sum()
        )
    closed_trades = len(trades)
    wins = int((trades["pnl"].astype(float) > 0).sum()) if closed_trades else 0
    losses = int((trades["pnl"].astype(float) < 0).sum()) if closed_trades else 0
    exposure = float(state["is_long"].astype(float).mean()) if "is_long" in state else float("nan")

    return {
        "start": equity.index[0].date().isoformat(),
        "end": equity.index[-1].date().isoformat(),
        "bars": len(equity),
        "initial_cash": float(initial_cash),
        "final_equity": float(equity.iloc[-1]),
        "total_return_pct": total_return * 100.0,
        "cagr_pct": cagr * 100.0,
        "annual_volatility_pct": volatility * 100.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_dd * 100.0,
        "max_drawdown_duration_bars": dd_bars,
        "max_drawdown_duration_days": dd_days,
        "order_count": len(orders),
        "closed_trade_count": closed_trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "win_rate_pct": wins / closed_trades * 100.0 if closed_trades else float("nan"),
        "turnover_multiple": order_notional / float(equity.mean()),
        "exposure_pct": exposure * 100.0,
    }


def calculate_holding_period_metrics(
    daily: pd.DataFrame,
    *,
    initial_cash: float,
    annual_bars: int = 252,
) -> dict[str, Any]:
    """Annualize net account growth over sessions with a positive position.

    The position mask is the post-open state recorded for each regular-session
    row. This geometrically compresses zero-return cash sessions; it is a
    descriptive capital-efficiency metric, not a separate backtest or IRR.
    """

    if daily.empty:
        raise ValueError("daily state must not be empty")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if annual_bars <= 0:
        raise ValueError("annual_bars must be positive")
    state = daily.copy()
    if "date" in state:
        state["date"] = pd.to_datetime(state["date"])
        state = state.sort_values("date", kind="stable")
    if "is_long" in state:
        held = state["is_long"].astype(float).gt(0)
    elif "shares" in state:
        held = state["shares"].astype(float).gt(1e-12)
    else:
        raise ValueError("daily state requires is_long or shares")
    if "equity" not in state:
        raise ValueError("daily state requires equity")
    final_equity = float(state["equity"].astype(float).iloc[-1])
    if final_equity < 0:
        raise ValueError("final_equity must not be negative")
    holding_sessions = int(held.sum())
    total_sessions = int(len(state))
    holding_cagr = float("nan")
    if holding_sessions > 0:
        holding_cagr = (
            (final_equity / float(initial_cash)) ** (float(annual_bars) / holding_sessions)
            - 1.0
        ) * 100.0
    return {
        "holding_sessions": holding_sessions,
        "holding_years_252": holding_sessions / float(annual_bars),
        "holding_time_pct": holding_sessions / total_sessions * 100.0,
        "holding_period_cagr_pct": holding_cagr,
    }


def pybroker_daily_state(result, data: pd.DataFrame) -> pd.DataFrame:
    state = result.portfolio.reset_index()[["date", "cash", "equity"]].copy()
    state["date"] = pd.to_datetime(state["date"])
    shares = pd.Series(0.0, index=state["date"])
    if not result.positions.empty:
        pos = result.positions.reset_index()
        grouped = pos.groupby("date")["long_shares"].sum().astype(float)
        shares.loc[pd.to_datetime(grouped.index)] = grouped.to_numpy()
    state["shares"] = shares.to_numpy(dtype=float)
    state["is_long"] = (state["shares"] > 0).astype(int)
    closes = data.set_index(pd.to_datetime(data["date"]))["close"].astype(float)
    state["close"] = state["date"].map(closes)
    return state
