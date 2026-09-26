"""Close-confirmed dual-StochRSI timing with an optional Bear9 drift sleeve."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy


def build_close_cross_state(
    prepared: pd.DataFrame,
    periods: Sequence[int],
    *,
    buy_threshold: float = 0.2,
    sell_threshold: float = 0.8,
    analysis_dates: Sequence[pd.Timestamp] | pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Build a path that starts flat and changes only on simultaneous close crosses."""

    periods = tuple(int(period) for period in periods)
    if len(periods) != 2 or len(set(periods)) != 2 or min(periods) < 2:
        raise ValueError("periods must contain two distinct integers >= 2")
    if not 0 <= buy_threshold < sell_threshold <= 1:
        raise ValueError("thresholds must satisfy 0 <= buy < sell <= 1")
    required = {"date"}
    for period in periods:
        required.update({f"stochrsi_{period}", f"prior_stochrsi_{period}"})
    if missing := required.difference(prepared.columns):
        raise ValueError(f"prepared data misses {sorted(missing)}")

    rows = prepared.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values("date").drop_duplicates("date", keep="last")
    if analysis_dates is not None:
        dates = pd.DatetimeIndex(pd.to_datetime(analysis_dates)).normalize().sort_values().unique()
        rows = rows[rows["date"].isin(dates)].copy()
        if not pd.DatetimeIndex(rows["date"]).equals(dates):
            raise ValueError("prepared data does not cover every requested analysis date")
    if rows.empty:
        raise ValueError("analysis state is empty")

    indicator_columns = [
        column
        for period in periods
        for column in (f"stochrsi_{period}", f"prior_stochrsi_{period}")
    ]
    rows["ready"] = np.isfinite(rows[indicator_columns].to_numpy(float)).all(axis=1)
    rows["is_long"] = False
    rows["transition"] = "not_ready"
    state = False
    for index in rows.index:
        if not bool(rows.at[index, "ready"]):
            continue
        prior = [float(rows.at[index, f"prior_stochrsi_{period}"]) for period in periods]
        current = [float(rows.at[index, f"stochrsi_{period}"]) for period in periods]
        transition = "hold_long" if state else "hold_flat"
        if not state and all(value < buy_threshold for value in prior) and all(
            value >= buy_threshold for value in current
        ):
            state = True
            transition = "enter"
        elif state and all(value > sell_threshold for value in prior) and all(
            value <= sell_threshold for value in current
        ):
            state = False
            transition = "exit"
        rows.at[index, "is_long"] = state
        rows.at[index, "transition"] = transition
    rows["target_asset"] = np.where(rows["is_long"], "QQQ", "CASH")
    return rows.reset_index(drop=True)


def _price_matrices(price_panel: pd.DataFrame) -> tuple[pd.DatetimeIndex, list[str], pd.DataFrame, pd.DataFrame]:
    required = {"date", "symbol", "open", "close"}
    if missing := required.difference(price_panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("price panel contains duplicate symbol dates")
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    symbols = sorted(panel["symbol"].unique())
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(index=dates, columns=symbols)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(index=dates, columns=symbols)
    return dates, symbols, opens, closes


def _weights_for_state(
    *,
    is_long: bool,
    use_bear: bool,
    closes: pd.Series,
    symbols: Sequence[str],
    bear_weights: Mapping[str, float],
) -> pd.Series:
    weights = pd.Series(0.0, index=list(symbols), dtype=float)
    if is_long:
        if "QQQ" not in weights.index or not np.isfinite(float(closes.get("QQQ", np.nan))):
            raise ValueError("QQQ must be tradable on every analysis date")
        weights["QQQ"] = 1.0
    elif use_bear:
        for symbol, weight in bear_weights.items():
            if symbol in weights.index and np.isfinite(float(closes.get(symbol, np.nan))):
                weights[symbol] = float(weight)
    return weights


def build_drift_target_schedule(
    timing_state: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    case_id: str,
    bear_weights: Mapping[str, float],
    use_bear: bool,
    drift_threshold: float,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> pd.DataFrame:
    """Emit close-sized targets, including strict Bear9 total-account drift restores.

    The internal shadow ledger uses exactly the same next-open, sell-before-buy
    convention as ``build_target_share_table`` so drift is measured from the
    portfolio that the downstream execution engines will receive.
    """

    if policy.timing != "open":
        raise ValueError("drift schedule requires next-open execution")
    if not case_id:
        raise ValueError("case_id is required")
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if not np.isfinite(drift_threshold) or drift_threshold <= 0:
        raise ValueError("drift_threshold must be finite and positive")
    if set(bear_weights).intersection({"QQQ"}):
        raise ValueError("Bear sleeve cannot contain QQQ")
    weights_array = np.asarray(list(bear_weights.values()), dtype=float)
    if not len(weights_array) or not np.isfinite(weights_array).all() or (weights_array < 0).any():
        raise ValueError("bear weights must be finite and non-negative")
    if not np.isclose(float(weights_array.sum()), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("bear weights must sum to one")

    dates, symbols, opens, closes = _price_matrices(price_panel)
    state = timing_state.copy()
    state["date"] = pd.to_datetime(state["date"]).dt.normalize()
    if missing := {"date", "ready", "is_long", "transition"}.difference(state.columns):
        raise ValueError(f"timing state misses {sorted(missing)}")
    state = state.set_index("date").reindex(dates)
    if state[["ready", "is_long", "transition"]].isna().any().any():
        raise ValueError("timing state must cover the complete price calendar")

    cash = float(initial_cash)
    current = pd.Series(0.0, index=symbols, dtype=float)
    pending: pd.Series | None = None
    prior_long: bool | None = None
    prior_available: tuple[str, ...] | None = None
    rows: list[dict[str, Any]] = []

    def execute_pending(date: pd.Timestamp) -> None:
        nonlocal cash, current, pending
        if pending is None:
            return
        execution_open = opens.loc[date]
        for symbol in symbols:
            shares = max(float(current[symbol] - pending[symbol]), 0.0)
            if shares <= 1e-12:
                continue
            raw = float(execution_open[symbol])
            if not np.isfinite(raw):
                raise ValueError(f"{symbol} is unavailable on scheduled execution {date.date()}")
            cash += shares * policy.expected_fill("sell", raw)
            current[symbol] -= shares
        for symbol in symbols:
            shares = max(float(pending[symbol] - current[symbol]), 0.0)
            if shares <= 1e-12:
                continue
            raw = float(execution_open[symbol])
            if not np.isfinite(raw):
                raise ValueError(f"{symbol} is unavailable on scheduled execution {date.date()}")
            fill = policy.expected_fill("buy", raw)
            filled = min(shares, max(cash, 0.0) / fill)
            cash -= filled * fill
            current[symbol] += filled
        pending = None

    for offset, date in enumerate(dates):
        execute_pending(date)
        current_close = closes.loc[date]
        is_long = bool(state.at[date, "is_long"])
        target_weights = _weights_for_state(
            is_long=is_long,
            use_bear=use_bear,
            closes=current_close,
            symbols=symbols,
            bear_weights=bear_weights,
        )
        available = tuple(symbol for symbol in bear_weights if target_weights.get(symbol, 0.0) > 0)
        market_values = current * current_close.fillna(0.0)
        equity = cash + float(market_values.sum())
        actual_weights = market_values / equity if equity > 0 else market_values * 0.0
        eligible_drifts = {
            symbol: abs(float(actual_weights.get(symbol, 0.0)) - float(target_weights.get(symbol, 0.0)))
            for symbol in available
        }
        max_drift = max(eligible_drifts.values(), default=0.0)

        reason: str | None = None
        if prior_long is None:
            reason = "initial_target"
        elif is_long != prior_long:
            reason = "timing_enter" if is_long else "timing_exit"
        elif use_bear and not is_long and available != prior_available:
            reason = "tradable_set_change"
        elif use_bear and not is_long and max_drift - drift_threshold > 1e-12:
            reason = "drift_rebalance"

        if reason is not None and offset < len(dates) - 1:
            desired = pd.Series(0.0, index=symbols, dtype=float)
            tradable = current_close.notna() & target_weights.notna()
            desired.loc[tradable] = (
                equity
                * target_weights.loc[tradable].astype(float)
                / current_close.loc[tradable].astype(float)
            )
            pending = desired
            for symbol in symbols:
                rows.append(
                    {
                        "case_id": case_id,
                        "date": date,
                        "execution_date": dates[offset + 1],
                        "symbol": symbol,
                        "target_weight": float(target_weights[symbol]),
                        "reason": reason,
                        "is_long": int(is_long),
                        "max_bear_weight_drift": float(max_drift),
                        "trigger_symbol": max(eligible_drifts, key=eligible_drifts.get) if eligible_drifts else "",
                    }
                )
        prior_long = is_long
        prior_available = available

    columns = [
        "case_id",
        "date",
        "execution_date",
        "symbol",
        "target_weight",
        "reason",
        "is_long",
        "max_bear_weight_drift",
        "trigger_symbol",
    ]
    return pd.DataFrame(rows, columns=columns)


def add_realized_exposures(
    daily: pd.DataFrame,
    positions: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    bear_symbols: Sequence[str],
) -> pd.DataFrame:
    """Attach realized QQQ, Bear9, and cash percentages to account state."""

    result = daily.copy()
    result["date"] = pd.to_datetime(result["date"])
    pos = positions.copy()
    pos["date"] = pd.to_datetime(pos["date"])
    shares = pos.pivot(index="date", columns="symbol", values="shares").reindex(result["date"])
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(result["date"])
    values = shares.fillna(0.0) * closes.fillna(0.0)
    equity = result.set_index("date")["equity"].astype(float)
    qqq_value = values.get("QQQ", pd.Series(0.0, index=values.index))
    present_bear = [symbol for symbol in bear_symbols if symbol in values.columns]
    bear_value = (
        values[present_bear].sum(axis=1)
        if present_bear
        else pd.Series(0.0, index=values.index)
    )
    result["qqq_exposure_pct"] = (qqq_value / equity * 100.0).to_numpy()
    result["bear9_exposure_pct"] = (bear_value / equity * 100.0).to_numpy()
    result["cash_exposure_pct"] = (
        result["cash"].astype(float) / result["equity"].astype(float) * 100.0
    )
    return result
