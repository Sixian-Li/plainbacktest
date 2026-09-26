"""Event-conditioned bear-market portfolios with SMA200 hysteresis and trade locks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy


VALID_MODES = ("no_filter", "sma200_hysteresis")
NUMERICAL_SHARE_EPSILON = 1e-12


@dataclass(frozen=True)
class EventSmaSpec:
    sma_window: int = 200
    entry_buffer: float = 0.02
    exit_buffer: float = 0.02
    lock_band: float = 0.10

    def __post_init__(self) -> None:
        if self.sma_window < 2:
            raise ValueError("sma_window must be at least two")
        for name, value in (
            ("entry_buffer", self.entry_buffer),
            ("exit_buffer", self.exit_buffer),
            ("lock_band", self.lock_band),
        ):
            if not np.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"{name} must be finite and in [0, 1)")


@dataclass(frozen=True)
class WeightedTrailingSpec:
    """SMA hysteresis plus an optional Close-based trailing drawdown exit."""

    sma_window: int = 200
    entry_buffer: float = 0.03
    exit_buffer: float = 0.03
    trailing_drawdown: float | None = None

    def __post_init__(self) -> None:
        if self.sma_window < 2:
            raise ValueError("sma_window must be at least two")
        for name, value in (
            ("entry_buffer", self.entry_buffer),
            ("exit_buffer", self.exit_buffer),
        ):
            if not np.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"{name} must be finite and in [0, 1)")
        if self.trailing_drawdown is not None and (
            not np.isfinite(self.trailing_drawdown)
            or not 0 < self.trailing_drawdown < 1
        ):
            raise ValueError("trailing_drawdown must be None or finite in (0, 1)")


@dataclass(frozen=True)
class EventPlan:
    target_shares: pd.DataFrame
    signals: pd.DataFrame
    transitions: pd.DataFrame
    executions: pd.DataFrame
    interval_schedule: pd.DataFrame


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def lock_has_released(anchor: float | None, close: float, band: float) -> bool:
    """Return true only after Close moves strictly outside the anchor's ±band."""

    if anchor is None or not _finite(anchor):
        return True
    price = float(close)
    reference = float(anchor)
    return price < reference * (1.0 - band) or price > reference * (1.0 + band)


def transition_state(
    *,
    active: bool,
    locked: bool,
    anchor: float | None,
    close: float,
    sma: float,
    ready: bool,
    spec: EventSmaSpec,
) -> tuple[bool, bool, str]:
    """Apply the level-based 2% SMA hysteresis after the 10% lock permits it."""

    if not ready or not _finite(close) or not _finite(sma):
        return active, locked, "indicator_not_ready"
    if locked:
        if not lock_has_released(anchor, float(close), spec.lock_band):
            return active, True, "lock_holds"
        locked = False
    price = float(close)
    average = float(sma)
    if active and price < average * (1.0 - spec.exit_buffer):
        return False, False, "exit_below_sma_minus_buffer"
    if not active and price > average * (1.0 + spec.entry_buffer):
        return True, False, "entry_above_sma_plus_buffer"
    return active, False, "hold_active" if active else "stay_inactive"


def allocate_transition_weights(
    current_market_values: pd.Series,
    *,
    survivors: Sequence[str],
    entrants: Sequence[str],
) -> pd.Series:
    """Keep survivor proportions and give every simultaneous entrant 1/n."""

    survivor_names = list(dict.fromkeys(str(symbol) for symbol in survivors))
    entrant_names = list(dict.fromkeys(str(symbol) for symbol in entrants))
    if set(survivor_names).intersection(entrant_names):
        raise ValueError("survivors and entrants must be disjoint")
    final_names = [*survivor_names, *entrant_names]
    weights = pd.Series(0.0, index=final_names, dtype=float)
    count = len(final_names)
    if count == 0:
        return weights
    entrant_weight = 1.0 / count
    for symbol in entrant_names:
        weights.loc[symbol] = entrant_weight
    survivor_budget = len(survivor_names) / count
    if survivor_names:
        values = current_market_values.reindex(survivor_names).fillna(0.0).clip(lower=0.0)
        if float(values.sum()) > 0:
            weights.loc[survivor_names] = values / float(values.sum()) * survivor_budget
        else:
            weights.loc[survivor_names] = survivor_budget / len(survivor_names)
    if not np.isclose(float(weights.sum()), 1.0, atol=1e-12):
        raise AssertionError("transition weights do not sum to one")
    return weights


def allocate_weighted_transition_weights(
    current_market_values: pd.Series,
    *,
    survivors: Sequence[str],
    entrants: Sequence[str],
    slot_multipliers: Mapping[str, float],
) -> pd.Series:
    """Extend the parent allocation rule with one- or two-slot entrants."""

    survivor_names = list(dict.fromkeys(str(symbol) for symbol in survivors))
    entrant_names = list(dict.fromkeys(str(symbol) for symbol in entrants))
    if set(survivor_names).intersection(entrant_names):
        raise ValueError("survivors and entrants must be disjoint")
    final_names = [*survivor_names, *entrant_names]
    weights = pd.Series(0.0, index=final_names, dtype=float)
    if not final_names:
        return weights
    slots: dict[str, float] = {}
    for symbol in final_names:
        value = float(slot_multipliers.get(symbol, 1.0))
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"slot multiplier for {symbol} must be positive")
        slots[symbol] = value
    total_slots = float(sum(slots.values()))
    entrant_budget = 0.0
    for symbol in entrant_names:
        weight = slots[symbol] / total_slots
        weights.loc[symbol] = weight
        entrant_budget += weight
    survivor_budget = 1.0 - entrant_budget
    if survivor_names:
        values = current_market_values.reindex(survivor_names).fillna(0.0).clip(lower=0.0)
        if float(values.sum()) > 0:
            weights.loc[survivor_names] = values / float(values.sum()) * survivor_budget
        else:
            survivor_slots = pd.Series(
                {symbol: slots[symbol] for symbol in survivor_names},
                dtype=float,
            )
            weights.loc[survivor_names] = (
                survivor_slots / float(survivor_slots.sum()) * survivor_budget
            )
    if not np.isclose(float(weights.sum()), 1.0, atol=1e-12):
        raise AssertionError("weighted transition weights do not sum to one")
    return weights


def prepare_event_indicators(
    asset_frames: Mapping[str, pd.DataFrame],
    spec: EventSmaSpec,
) -> pd.DataFrame:
    """Build causal SMA inputs from each asset's full available history."""

    rows: list[pd.DataFrame] = []
    for symbol, raw in asset_frames.items():
        required = {"date", "close"}
        if missing := required.difference(raw.columns):
            raise ValueError(f"{symbol} price data misses {sorted(missing)}")
        frame = raw[["date", "close"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        frame["close"] = pd.to_numeric(frame["close"], errors="raise")
        frame = frame.sort_values("date").drop_duplicates("date", keep="last")
        if frame.empty or (frame["close"] <= 0).any() or frame["close"].isna().any():
            raise ValueError(f"{symbol} contains invalid Close data")
        frame["sma200"] = frame["close"].rolling(
            spec.sma_window,
            min_periods=spec.sma_window,
        ).mean()
        frame["ready"] = frame["sma200"].notna()
        frame["symbol"] = str(symbol)
        rows.append(frame)
    if not rows:
        raise ValueError("asset_frames cannot be empty")
    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )


def _normalize_intervals(
    intervals: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = intervals.copy() if isinstance(intervals, pd.DataFrame) else pd.DataFrame(intervals)
    required = {"interval_id", "label", "severity", "start", "end"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"bear intervals miss {sorted(missing)}")
    result = frame.copy()
    result["start"] = pd.to_datetime(result["start"], errors="raise")
    result["end"] = pd.to_datetime(result["end"], errors="raise")
    if "ordinal" not in result:
        result["ordinal"] = np.arange(1, len(result) + 1)
    result = result.sort_values(["start", "end"]).reset_index(drop=True)
    if result.empty:
        raise ValueError("bear intervals cannot be empty")
    if (result["end"] < result["start"]).any():
        raise ValueError("bear interval ends before it starts")
    previous_end: pd.Timestamp | None = None
    for row in result.itertuples(index=False):
        if previous_end is not None and pd.Timestamp(row.start) <= previous_end:
            raise ValueError("bear intervals must be strictly non-overlapping")
        previous_end = pd.Timestamp(row.end)
    return result


def _normalize_price_panel(price_panel: pd.DataFrame, symbols: Sequence[str]) -> pd.DataFrame:
    required = {"date", "symbol", "open", "high", "low", "close"}
    if missing := required.difference(price_panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="raise")
    panel["symbol"] = panel["symbol"].astype(str)
    panel = panel[panel["symbol"].isin(symbols)].copy()
    for column in ("open", "high", "low", "close"):
        panel[column] = pd.to_numeric(panel[column], errors="raise")
    panel = panel.sort_values(["date", "symbol"]).drop_duplicates(
        ["date", "symbol"], keep="last"
    )
    missing_symbols = sorted(set(symbols).difference(panel["symbol"].unique()))
    if missing_symbols:
        raise ValueError(f"price panel has no rows for {missing_symbols}")
    return panel.reset_index(drop=True)


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def build_event_plan(
    price_panel: pd.DataFrame,
    indicators: pd.DataFrame,
    intervals: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    symbols: Sequence[str],
    mode: str,
    initial_cash: float,
    policy: ExplicitFillPolicy,
    spec: EventSmaSpec,
    slot_multipliers: Mapping[str, float] | None = None,
    require_entry_cross: bool = False,
    trailing_drawdown: float | None = None,
    use_trade_lock: bool = True,
) -> EventPlan:
    """Compile causal Close signals into next-Open target shares and lock anchors."""

    if mode not in VALID_MODES:
        raise ValueError(f"unknown event mode: {mode}")
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    symbol_list = list(dict.fromkeys(str(symbol) for symbol in symbols))
    if not symbol_list:
        raise ValueError("symbols cannot be empty")
    multipliers = {
        symbol: float((slot_multipliers or {}).get(symbol, 1.0))
        for symbol in symbol_list
    }
    if any(not np.isfinite(value) or value <= 0 for value in multipliers.values()):
        raise ValueError("all slot multipliers must be finite and positive")
    if trailing_drawdown is not None and (
        not np.isfinite(trailing_drawdown) or not 0 < trailing_drawdown < 1
    ):
        raise ValueError("trailing_drawdown must be None or finite in (0, 1)")
    panel = _normalize_price_panel(price_panel, symbol_list)
    interval_frame = _normalize_intervals(intervals)
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    next_date = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    for row in interval_frame.itertuples(index=False):
        if pd.Timestamp(row.start) not in dates or pd.Timestamp(row.end) not in dates:
            raise ValueError(f"interval {row.interval_id} boundary is absent from price calendar")
        if pd.Timestamp(row.end) not in next_date:
            raise ValueError(f"interval {row.interval_id} has no next session for liquidation")

    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(
        index=dates, columns=symbol_list
    )
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(
        index=dates, columns=symbol_list
    )
    indicator_frame = indicators.copy()
    required_indicator = {"date", "symbol", "sma200", "ready"}
    if missing := required_indicator.difference(indicator_frame.columns):
        raise ValueError(f"indicator table misses {sorted(missing)}")
    indicator_frame["date"] = pd.to_datetime(indicator_frame["date"], errors="raise")
    indicator_frame["symbol"] = indicator_frame["symbol"].astype(str)
    indicator_frame = indicator_frame[indicator_frame["symbol"].isin(symbol_list)].copy()
    sma = indicator_frame.pivot(index="date", columns="symbol", values="sma200").reindex(
        index=dates, columns=symbol_list
    )
    ready = indicator_frame.pivot(index="date", columns="symbol", values="ready").reindex(
        index=dates, columns=symbol_list
    ).fillna(False).astype(bool)

    starts = {
        pd.Timestamp(row.start): row
        for row in interval_frame.itertuples(index=False)
    }
    current_interval: Any | None = None
    active = {symbol: False for symbol in symbol_list}
    locked = {symbol: False for symbol in symbol_list}
    anchors = {symbol: np.nan for symbol in symbol_list}
    entry_armed = {symbol: False for symbol in symbol_list}
    running_peaks = {symbol: np.nan for symbol in symbol_list}
    current_shares = pd.Series(0.0, index=symbol_list, dtype=float)
    cash = float(initial_cash)
    pending: dict[str, Any] | None = None

    target_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    schedules: dict[str, dict[str, Any]] = {}

    def execute_pending(date: pd.Timestamp, item: dict[str, Any]) -> None:
        nonlocal cash, current_shares
        desired = item["desired"].reindex(symbol_list).fillna(0.0).astype(float)
        transition_by_symbol = item["transitions"]

        def execute_one(symbol: str, side: str, requested: float) -> None:
            nonlocal cash, current_shares
            if requested <= NUMERICAL_SHARE_EPSILON:
                return
            market_open = opens.at[date, symbol]
            if not _finite(market_open):
                raise ValueError(f"{symbol} lacks execution Open on {date.date()}")
            fill = policy.expected_fill(side, float(market_open))
            filled = float(requested)
            if side == "sell":
                filled = min(filled, float(current_shares[symbol]))
                cash += filled * fill
                current_shares.loc[symbol] -= filled
            else:
                filled = min(filled, max(cash, 0.0) / fill)
                cash -= filled * fill
                if abs(cash) <= 1e-9:
                    cash = 0.0
                current_shares.loc[symbol] += filled
            transition = transition_by_symbol.get(symbol)
            anchor_before = anchors[symbol]
            anchor_updated = False
            transition_name = "none"
            reason = "proportional_resize"
            if transition is not None:
                transition_name = "entry" if transition["new_active"] else "exit"
                reason = str(transition["reason"])
                if (
                    filled > NUMERICAL_SHARE_EPSILON
                    and bool(transition["lock_on_fill"])
                    and ((side == "buy" and transition_name == "entry") or (side == "sell" and transition_name == "exit"))
                ):
                    anchors[symbol] = fill
                    locked[symbol] = True
                    anchor_updated = True
                if filled > NUMERICAL_SHARE_EPSILON:
                    if side == "buy" and transition_name == "entry":
                        entry_armed[symbol] = False
                        if trailing_drawdown is not None:
                            running_peaks[symbol] = float(fill)
                    elif side == "sell" and transition_name == "exit":
                        entry_armed[symbol] = False
                        running_peaks[symbol] = np.nan
            execution_rows.append(
                {
                    "interval_id": item["interval_id"],
                    "mode": mode,
                    "signal_date": item["signal_date"],
                    "execution_date": date,
                    "symbol": symbol,
                    "side": side,
                    "shares_requested": requested,
                    "shares_filled": filled,
                    "fill_price": fill,
                    "state_transition": transition_name,
                    "reason": reason,
                    "anchor_before": anchor_before,
                    "lock_anchor_after": anchors[symbol],
                    "anchor_updated": anchor_updated,
                    "cash_after": cash,
                }
            )

        for symbol in sorted(symbol_list):
            execute_one(
                symbol,
                "sell",
                max(float(current_shares[symbol] - desired[symbol]), 0.0),
            )
        for symbol in sorted(symbol_list):
            execute_one(
                symbol,
                "buy",
                max(float(desired[symbol] - current_shares[symbol]), 0.0),
            )

    for date in dates:
        if pending is not None:
            if pd.Timestamp(pending["execution_date"]) != date:
                raise AssertionError("pending target did not reach its declared execution date")
            execute_pending(date, pending)
            pending = None

        close_row = closes.loc[date]
        sma_row = sma.loc[date]
        ready_row = ready.loc[date]
        drawdowns = {symbol: np.nan for symbol in symbol_list}
        if trailing_drawdown is not None:
            for symbol in symbol_list:
                if (
                    current_interval is not None
                    and active[symbol]
                    and float(current_shares[symbol]) > NUMERICAL_SHARE_EPSILON
                    and _finite(close_row[symbol])
                ):
                    previous_peak = running_peaks[symbol]
                    running_peaks[symbol] = (
                        max(float(previous_peak), float(close_row[symbol]))
                        if _finite(previous_peak)
                        else float(close_row[symbol])
                    )
                    drawdowns[symbol] = (
                        float(close_row[symbol]) / float(running_peaks[symbol]) - 1.0
                    )
        current_values = current_shares * close_row.fillna(0.0)
        close_equity = cash + float(current_values.sum())
        current_weights = (
            current_values / close_equity if close_equity > 0 else pd.Series(0.0, index=symbol_list)
        )
        reasons = {symbol: "outside_bear_window" for symbol in symbol_list}
        old_active = active.copy()
        target_weights: pd.Series | None = None
        transition_map: dict[str, dict[str, Any]] = {}
        interval_for_rows: Any | None = current_interval
        is_start = date in starts
        is_end = False

        if is_start:
            if current_interval is not None:
                raise AssertionError("a new bear interval started before the prior interval ended")
            current_interval = starts[date]
            interval_for_rows = current_interval
            for symbol in symbol_list:
                active[symbol] = False
                locked[symbol] = False
                anchors[symbol] = np.nan
                entry_armed[symbol] = False
                running_peaks[symbol] = np.nan
            old_active = active.copy()
            selected: list[str] = []
            for symbol in symbol_list:
                has_price = _finite(close_row[symbol])
                if mode == "no_filter":
                    include = has_price
                    reason = "initial_available_no_filter" if include else "not_yet_listed"
                else:
                    upper = (
                        float(sma_row[symbol]) * (1.0 + spec.entry_buffer)
                        if _finite(sma_row[symbol])
                        else np.nan
                    )
                    include = (
                        has_price
                        and bool(ready_row[symbol])
                        and _finite(sma_row[symbol])
                        and float(close_row[symbol])
                        > (upper if require_entry_cross else float(sma_row[symbol]))
                    )
                    reason = (
                        "initial_above_sma_plus_buffer"
                        if include and require_entry_cross
                        else "initial_above_sma"
                        if include
                        else "initial_not_above_entry_level_or_not_ready"
                        if require_entry_cross
                        else "initial_not_above_sma_or_not_ready"
                    )
                    entry_armed[symbol] = bool(
                        require_entry_cross
                        and has_price
                        and bool(ready_row[symbol])
                        and _finite(upper)
                        and float(close_row[symbol]) <= float(upper)
                    )
                active[symbol] = bool(include)
                reasons[symbol] = reason
                if include:
                    selected.append(symbol)
                    transition_map[symbol] = {
                        "old_active": False,
                        "new_active": True,
                        "reason": reason,
                        "lock_on_fill": mode == "sma200_hysteresis" and use_trade_lock,
                    }
            target_weights = pd.Series(0.0, index=symbol_list, dtype=float)
            if selected:
                if slot_multipliers is None:
                    target_weights.loc[selected] = 1.0 / len(selected)
                else:
                    selected_slots = pd.Series(
                        {symbol: multipliers[symbol] for symbol in selected},
                        dtype=float,
                    )
                    target_weights.loc[selected] = selected_slots / float(selected_slots.sum())
            schedules[str(current_interval.interval_id)] = {
                "ordinal": int(current_interval.ordinal),
                "interval_id": str(current_interval.interval_id),
                "label": str(current_interval.label),
                "severity": str(current_interval.severity),
                "start": pd.Timestamp(current_interval.start),
                "end": pd.Timestamp(current_interval.end),
                "entry_execution_date": next_date[date],
                "exit_execution_date": next_date[pd.Timestamp(current_interval.end)],
                "initial_active_count": len(selected),
                "mid_bear_transition_count": 0,
            }
        elif current_interval is not None:
            interval_for_rows = current_interval
            is_end = date == pd.Timestamp(current_interval.end)
            if is_end:
                for symbol in symbol_list:
                    if active[symbol]:
                        reasons[symbol] = "interval_end"
                        transition_map[symbol] = {
                            "old_active": True,
                            "new_active": False,
                            "reason": "interval_end",
                            "lock_on_fill": False,
                        }
                    else:
                        reasons[symbol] = "interval_end_already_flat"
                    active[symbol] = False
                    locked[symbol] = False
                    entry_armed[symbol] = False
                    running_peaks[symbol] = np.nan
                target_weights = pd.Series(0.0, index=symbol_list, dtype=float)
            elif mode == "sma200_hysteresis":
                for symbol in symbol_list:
                    if use_trade_lock:
                        new_active, new_locked, reason = transition_state(
                            active=active[symbol],
                            locked=locked[symbol],
                            anchor=anchors[symbol],
                            close=close_row[symbol],
                            sma=sma_row[symbol],
                            ready=bool(ready_row[symbol]),
                            spec=spec,
                        )
                    elif (
                        not bool(ready_row[symbol])
                        or not _finite(close_row[symbol])
                        or not _finite(sma_row[symbol])
                    ):
                        new_active = active[symbol]
                        new_locked = False
                        reason = "indicator_not_ready"
                    else:
                        price = float(close_row[symbol])
                        average = float(sma_row[symbol])
                        upper = average * (1.0 + spec.entry_buffer)
                        lower = average * (1.0 - spec.exit_buffer)
                        new_active = active[symbol]
                        new_locked = False
                        if active[symbol]:
                            forced = bool(
                                trailing_drawdown is not None
                                and _finite(drawdowns[symbol])
                                and float(drawdowns[symbol]) < -float(trailing_drawdown)
                            )
                            if forced:
                                new_active = False
                                reason = "forced_exit_peak_drawdown"
                            elif price < lower:
                                new_active = False
                                reason = "exit_below_sma_minus_buffer"
                            else:
                                reason = "hold_active"
                        elif require_entry_cross:
                            if price <= upper:
                                entry_armed[symbol] = True
                                reason = "entry_rearmed_below_or_at_upper"
                            elif entry_armed[symbol]:
                                new_active = True
                                entry_armed[symbol] = False
                                reason = "entry_cross_above_sma_plus_buffer"
                            else:
                                reason = "stay_inactive_waiting_for_rearm"
                        elif price > upper:
                            new_active = True
                            reason = "entry_above_sma_plus_buffer"
                        else:
                            reason = "stay_inactive"
                    reasons[symbol] = reason
                    locked[symbol] = new_locked
                    if new_active != active[symbol]:
                        if not new_active:
                            entry_armed[symbol] = False
                        transition_map[symbol] = {
                            "old_active": active[symbol],
                            "new_active": new_active,
                            "reason": reason,
                            "lock_on_fill": use_trade_lock,
                        }
                    active[symbol] = new_active
                if transition_map:
                    survivors = [
                        symbol
                        for symbol in symbol_list
                        if active[symbol] and old_active[symbol]
                    ]
                    entrants = [
                        symbol
                        for symbol in symbol_list
                        if active[symbol] and not old_active[symbol]
                    ]
                    if slot_multipliers is None:
                        allocated = allocate_transition_weights(
                            current_values,
                            survivors=survivors,
                            entrants=entrants,
                        )
                    else:
                        allocated = allocate_weighted_transition_weights(
                            current_values,
                            survivors=survivors,
                            entrants=entrants,
                            slot_multipliers=multipliers,
                        )
                    target_weights = pd.Series(0.0, index=symbol_list, dtype=float)
                    target_weights.loc[allocated.index] = allocated
                    schedules[str(current_interval.interval_id)][
                        "mid_bear_transition_count"
                    ] += len(transition_map)
            else:
                for symbol in symbol_list:
                    reasons[symbol] = "no_filter_hold"

        if interval_for_rows is not None:
            for symbol in symbol_list:
                transition = transition_map.get(symbol)
                if transition is not None:
                    transition_rows.append(
                        {
                            "interval_id": str(interval_for_rows.interval_id),
                            "label": str(interval_for_rows.label),
                            "severity": str(interval_for_rows.severity),
                            "mode": mode,
                            "signal_date": date,
                            "execution_date": next_date[date],
                            "symbol": symbol,
                            "old_active": bool(transition["old_active"]),
                            "new_active": bool(transition["new_active"]),
                            "reason": str(transition["reason"]),
                            "close": close_row[symbol],
                            "sma200": sma_row[symbol],
                            "locked_before_fill": bool(locked[symbol]),
                            "lock_anchor_before": anchors[symbol],
                            "entry_armed": bool(entry_armed[symbol]),
                            "slot_multiplier": float(multipliers[symbol]),
                            "running_peak_close": running_peaks[symbol],
                            "drawdown_from_peak": drawdowns[symbol],
                        }
                    )
                signal_rows.append(
                    {
                        "interval_id": str(interval_for_rows.interval_id),
                        "label": str(interval_for_rows.label),
                        "severity": str(interval_for_rows.severity),
                        "mode": mode,
                        "date": date,
                        "symbol": symbol,
                        "close": close_row[symbol],
                        "sma200": sma_row[symbol],
                        "entry_level": float(sma_row[symbol]) * (1.0 + spec.entry_buffer)
                        if _finite(sma_row[symbol])
                        else np.nan,
                        "exit_level": float(sma_row[symbol]) * (1.0 - spec.exit_buffer)
                        if _finite(sma_row[symbol])
                        else np.nan,
                        "ready": bool(ready_row[symbol]),
                        "active_before": bool(old_active[symbol]),
                        "active_after": bool(active[symbol]),
                        "locked": bool(locked[symbol]),
                        "lock_anchor": anchors[symbol],
                        "entry_armed": bool(entry_armed[symbol]),
                        "slot_multiplier": float(multipliers[symbol]),
                        "running_peak_close": running_peaks[symbol],
                        "drawdown_from_peak": drawdowns[symbol],
                        "reason": reasons[symbol],
                        "current_weight": float(current_weights.get(symbol, 0.0)),
                        "new_target_weight": float(target_weights[symbol])
                        if target_weights is not None
                        else np.nan,
                    }
                )

        if target_weights is not None:
            total = float(target_weights.sum())
            if total > 1.0 + 1e-12 or (target_weights < -1e-15).any():
                raise AssertionError("target weights violate the long-only 100% budget")
            execution_date = next_date.get(date)
            if execution_date is None:
                raise ValueError(f"signal on {date.date()} has no next execution session")
            desired = pd.Series(0.0, index=symbol_list, dtype=float)
            for symbol in symbol_list:
                weight = float(target_weights[symbol])
                price = close_row[symbol]
                if weight > 0:
                    if not _finite(price) or float(price) <= 0:
                        raise ValueError(f"positive target for unavailable {symbol} on {date.date()}")
                    desired.loc[symbol] = close_equity * weight / float(price)
                target_rows.append(
                    {
                        "interval_id": str(interval_for_rows.interval_id),
                        "label": str(interval_for_rows.label),
                        "severity": str(interval_for_rows.severity),
                        "mode": mode,
                        "signal_date": date,
                        "execution_date": execution_date,
                        "symbol": symbol,
                        "target_weight": weight,
                        "target_shares": float(desired[symbol]),
                    }
                )
            pending = {
                "interval_id": str(interval_for_rows.interval_id),
                "signal_date": date,
                "execution_date": execution_date,
                "desired": desired,
                "transitions": transition_map,
            }

        if is_end:
            current_interval = None

    if pending is not None:
        raise AssertionError("final target was not executed inside the supplied price panel")
    if any(float(value) < -1e-8 for value in current_shares):
        raise AssertionError("planner produced negative shares")
    if cash < -1e-8:
        raise AssertionError("planner produced negative cash")

    target_columns = (
        "interval_id",
        "label",
        "severity",
        "mode",
        "signal_date",
        "execution_date",
        "symbol",
        "target_weight",
        "target_shares",
    )
    signal_columns = (
        "interval_id",
        "label",
        "severity",
        "mode",
        "date",
        "symbol",
        "close",
        "sma200",
        "entry_level",
        "exit_level",
        "ready",
        "active_before",
        "active_after",
        "locked",
        "lock_anchor",
        "entry_armed",
        "slot_multiplier",
        "running_peak_close",
        "drawdown_from_peak",
        "reason",
        "current_weight",
        "new_target_weight",
    )
    transition_columns = (
        "interval_id",
        "label",
        "severity",
        "mode",
        "signal_date",
        "execution_date",
        "symbol",
        "old_active",
        "new_active",
        "reason",
        "close",
        "sma200",
        "locked_before_fill",
        "lock_anchor_before",
        "entry_armed",
        "slot_multiplier",
        "running_peak_close",
        "drawdown_from_peak",
    )
    execution_columns = (
        "interval_id",
        "mode",
        "signal_date",
        "execution_date",
        "symbol",
        "side",
        "shares_requested",
        "shares_filled",
        "fill_price",
        "state_transition",
        "reason",
        "anchor_before",
        "lock_anchor_after",
        "anchor_updated",
        "cash_after",
    )
    target_frame = pd.DataFrame(target_rows) if target_rows else _empty_frame(target_columns)
    signal_frame = pd.DataFrame(signal_rows) if signal_rows else _empty_frame(signal_columns)
    transition_frame = (
        pd.DataFrame(transition_rows) if transition_rows else _empty_frame(transition_columns)
    )
    execution_frame = (
        pd.DataFrame(execution_rows) if execution_rows else _empty_frame(execution_columns)
    )
    schedule_frame = pd.DataFrame(schedules.values()).sort_values("ordinal").reset_index(drop=True)
    return EventPlan(
        target_shares=target_frame.loc[:, target_columns].reset_index(drop=True),
        signals=signal_frame.loc[:, signal_columns].reset_index(drop=True),
        transitions=transition_frame.loc[:, transition_columns].reset_index(drop=True),
        executions=execution_frame.loc[:, execution_columns].reset_index(drop=True),
        interval_schedule=schedule_frame,
    )


def build_weighted_trailing_event_plan(
    price_panel: pd.DataFrame,
    indicators: pd.DataFrame,
    intervals: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    symbols: Sequence[str],
    initial_cash: float,
    policy: ExplicitFillPolicy,
    spec: WeightedTrailingSpec,
    slot_multipliers: Mapping[str, float],
) -> EventPlan:
    """Build the no-lock, re-armed, weighted trailing-stop event path."""

    return build_event_plan(
        price_panel,
        indicators,
        intervals,
        symbols=symbols,
        mode="sma200_hysteresis",
        initial_cash=initial_cash,
        policy=policy,
        spec=EventSmaSpec(
            sma_window=spec.sma_window,
            entry_buffer=spec.entry_buffer,
            exit_buffer=spec.exit_buffer,
            lock_band=0.0,
        ),
        slot_multipliers=slot_multipliers,
        require_entry_cross=True,
        trailing_drawdown=spec.trailing_drawdown,
        use_trade_lock=False,
    )


def calculate_interval_returns(
    daily: pd.DataFrame,
    schedule: pd.DataFrame,
) -> pd.DataFrame:
    """Measure each event from the start signal Close through next-Open liquidation."""

    required = {"date", "equity"}
    if missing := required.difference(daily.columns):
        raise ValueError(f"daily account table misses {sorted(missing)}")
    account = daily.copy()
    account["date"] = pd.to_datetime(account["date"], errors="raise")
    equity = account.set_index("date")["equity"].astype(float)
    rows: list[dict[str, Any]] = []
    for interval in schedule.itertuples(index=False):
        start = pd.Timestamp(interval.start)
        exit_date = pd.Timestamp(interval.exit_execution_date)
        if start not in equity.index or exit_date not in equity.index:
            raise ValueError(f"daily account does not cover {interval.interval_id}")
        start_equity = float(equity.loc[start])
        end_equity = float(equity.loc[exit_date])
        rows.append(
            {
                "ordinal": int(interval.ordinal),
                "interval_id": str(interval.interval_id),
                "label": str(interval.label),
                "severity": str(interval.severity),
                "start": start,
                "end": pd.Timestamp(interval.end),
                "entry_execution_date": pd.Timestamp(interval.entry_execution_date),
                "exit_execution_date": exit_date,
                "initial_active_count": int(interval.initial_active_count),
                "mid_bear_transition_count": int(interval.mid_bear_transition_count),
                "start_equity": start_equity,
                "end_equity": end_equity,
                "total_return": end_equity / start_equity - 1.0,
            }
        )
    return pd.DataFrame(rows)


def compound_returns(values: Sequence[float] | pd.Series) -> float:
    finite = pd.Series(values, dtype=float).dropna()
    if finite.empty:
        return np.nan
    return float((1.0 + finite).prod() - 1.0)


def summarize_interval_returns(interval_returns: pd.DataFrame) -> pd.DataFrame:
    """Return major, minor, and all-bear compound results for one case."""

    rows: list[dict[str, Any]] = []
    for scope, subset in (
        ("major", interval_returns[interval_returns["severity"] == "major"]),
        ("minor", interval_returns[interval_returns["severity"] == "minor"]),
        ("all", interval_returns),
    ):
        returns = subset["total_return"].astype(float)
        rows.append(
            {
                "scope": scope,
                "interval_count": int(len(subset)),
                "positive_interval_count": int((returns > 0).sum()),
                "compound_return": compound_returns(returns),
                "mean_return": float(returns.mean()) if len(returns) else np.nan,
                "worst_interval_return": float(returns.min()) if len(returns) else np.nan,
                "best_interval_return": float(returns.max()) if len(returns) else np.nan,
            }
        )
    return pd.DataFrame(rows)
