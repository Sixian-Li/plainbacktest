"""Bear-event policy ablation with fixed entry-ATR hard stops.

The signal state machine is intentionally independent from PyBroker. It first
compiles explicit fills, including same-session standing-stop fills, and then
PyBroker replays those fills with its own portfolio accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy, MAX_AFFORDABLE_REQUEST


POLICY_HOLD = "A_hold"
POLICY_SMA = "B_sma200_full"
POLICY_ATR_PRICE = "C_atr_stop_price_reentry"
POLICY_ATR_SMA = "D_atr_stop_sma_reentry"
POLICIES = (POLICY_HOLD, POLICY_SMA, POLICY_ATR_PRICE, POLICY_ATR_SMA)
ATR_POLICIES = (POLICY_ATR_PRICE, POLICY_ATR_SMA)
SHARE_EPSILON = 1e-12


@dataclass(frozen=True)
class BearAtrSpec:
    sma_window: int = 200
    sma_buffer: float = 0.03
    atr_window: int = 20
    atr_multiplier: float = 3.0
    stop_min: float = 0.12
    stop_max: float = 0.20

    def __post_init__(self) -> None:
        if self.sma_window < 1:
            raise ValueError("sma_window must be at least one")
        if self.atr_window < 2:
            raise ValueError("atr_window must be at least two")
        if not np.isfinite(self.sma_buffer) or not 0 <= self.sma_buffer < 1:
            raise ValueError("sma_buffer must be finite and in [0, 1)")
        if not np.isfinite(self.atr_multiplier) or self.atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be finite and positive")
        if not 0 < self.stop_min <= self.stop_max < 1:
            raise ValueError("stop bounds must satisfy 0 < min <= max < 1")


@dataclass(frozen=True)
class PolicyPath:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame
    interval_schedule: pd.DataFrame
    positions: pd.DataFrame


@dataclass(frozen=True)
class CompiledPyBrokerPath:
    result: Any
    engine: pd.DataFrame
    engine_to_canonical: Mapping[pd.Timestamp, pd.Timestamp]


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def wilder_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int,
) -> pd.Series:
    """Return Wilder ATR initialized by the first simple-mean true range."""

    if window < 2:
        raise ValueError("window must be at least two")
    high_values = pd.Series(high, dtype=float).reset_index(drop=True)
    low_values = pd.Series(low, dtype=float).reset_index(drop=True)
    close_values = pd.Series(close, dtype=float).reset_index(drop=True)
    if not (len(high_values) == len(low_values) == len(close_values)):
        raise ValueError("OHLC series must have equal length")
    previous_close = close_values.shift(1)
    true_range = pd.concat(
        [
            high_values - low_values,
            (high_values - previous_close).abs(),
            (low_values - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    atr = pd.Series(np.nan, index=true_range.index, dtype=float)
    if len(true_range) < window:
        return atr
    initial = true_range.iloc[:window]
    if not np.isfinite(initial.to_numpy(dtype=float)).all():
        return atr
    atr.iloc[window - 1] = float(initial.mean())
    for index in range(window, len(true_range)):
        current = float(true_range.iloc[index])
        previous = float(atr.iloc[index - 1])
        if np.isfinite(current) and np.isfinite(previous):
            atr.iloc[index] = (previous * (window - 1) + current) / window
    return atr


def prepare_symbol_data(
    raw: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    spec: BearAtrSpec,
) -> pd.DataFrame:
    """Calculate indicators on real history, then expose pre-listing gaps as NaN."""

    required = {"date", "symbol", "open", "high", "low", "close"}
    if missing := required.difference(raw.columns):
        raise ValueError(f"canonical data miss {sorted(missing)}")
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    if frame["symbol"].nunique() != 1:
        raise ValueError("prepare_symbol_data accepts one symbol")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    ohlc = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError("available OHLC must be finite and positive")
    if (
        (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
    ).any():
        raise ValueError("invalid OHLC envelope")
    frame["sma200"] = frame["close"].rolling(
        spec.sma_window,
        min_periods=spec.sma_window,
    ).mean()
    frame["atr20"] = wilder_atr(
        frame["high"], frame["low"], frame["close"], spec.atr_window
    ).to_numpy(dtype=float)
    frame["indicator_ready"] = frame[["sma200", "atr20"]].notna().all(axis=1)
    symbol = str(frame.iloc[0]["symbol"])
    result = frame.set_index("date").reindex(pd.DatetimeIndex(calendar)).reset_index()
    result = result.rename(columns={"index": "date"})
    result["symbol"] = symbol
    return result


def hard_stop_distance(atr: float, effective_entry_fill: float, spec: BearAtrSpec) -> float:
    if not _finite(atr) or float(atr) <= 0:
        raise ValueError("ATR must be finite and positive")
    if not _finite(effective_entry_fill) or float(effective_entry_fill) <= 0:
        raise ValueError("entry fill must be finite and positive")
    raw = spec.atr_multiplier * float(atr) / float(effective_entry_fill)
    return float(np.clip(raw, spec.stop_min, spec.stop_max))


def hard_stop_raw_fill(
    *,
    open_: float,
    low: float,
    stop_line: float,
) -> tuple[float, Literal["open_gap", "intraday_stop"]] | None:
    """Evaluate a standing long stop; equality triggers."""

    if float(open_) <= float(stop_line):
        return float(open_), "open_gap"
    if float(low) <= float(stop_line) < float(open_):
        return float(stop_line), "intraday_stop"
    return None


def normalize_intervals(
    intervals: Sequence[Mapping[str, Any]] | pd.DataFrame,
    calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
    frame = intervals.copy() if isinstance(intervals, pd.DataFrame) else pd.DataFrame(intervals)
    required = {"ordinal", "interval_id", "label", "severity", "start", "end"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"bear intervals miss {sorted(missing)}")
    result = frame.copy()
    result["start"] = pd.to_datetime(result["start"], errors="raise").dt.normalize()
    result["end"] = pd.to_datetime(result["end"], errors="raise").dt.normalize()
    result = result.sort_values("ordinal").reset_index(drop=True)
    dates = pd.DatetimeIndex(calendar)
    next_date = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
    previous_end: pd.Timestamp | None = None
    schedule_rows: list[dict[str, Any]] = []
    for row in result.itertuples(index=False):
        start = pd.Timestamp(row.start)
        end = pd.Timestamp(row.end)
        if start not in dates or end not in dates or end not in next_date:
            raise ValueError(f"interval {row.interval_id} lacks a required common session")
        if previous_end is not None and start <= previous_end:
            raise ValueError("bear intervals must be strictly non-overlapping")
        previous_end = end
        schedule_rows.append(
            {
                "ordinal": int(row.ordinal),
                "interval_id": str(row.interval_id),
                "label": str(row.label),
                "severity": str(row.severity),
                "start": start,
                "end": end,
                "entry_execution_date": next_date[start],
                "exit_execution_date": next_date[end],
                "eligible_at_start": False,
                "initial_active_count": 0,
                "mid_bear_transition_count": 0,
            }
        )
    return pd.DataFrame(schedule_rows)


def _empty_records(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def run_reference_policy(
    prepared: pd.DataFrame,
    intervals: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    policy_id: str,
    initial_cash: float,
    fill_policy: ExplicitFillPolicy,
    spec: BearAtrSpec,
    case_id: str,
) -> PolicyPath:
    """Run one symbol over the complete common calendar without future inputs."""

    if policy_id not in POLICIES:
        raise ValueError(f"unknown policy: {policy_id}")
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    data = prepared.copy().sort_values("date").reset_index(drop=True)
    dates = pd.DatetimeIndex(pd.to_datetime(data["date"]))
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("calendar must be unique and increasing")
    symbol = str(data.iloc[0]["symbol"])
    schedule = normalize_intervals(intervals, dates)
    start_rows = {
        pd.Timestamp(row.start): index
        for index, row in schedule.iterrows()
    }
    next_date = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}

    cash = float(initial_cash)
    shares = 0.0
    pending: dict[str, Any] | None = None
    current_schedule_index: int | None = None
    interval_eligible = False
    sma_armed = False
    stop_line: float | None = None
    stop_distance: float | None = None
    stop_atr: float | None = None
    reentry_stop_line: float | None = None
    last_stop_date: pd.Timestamp | None = None
    open_trade: dict[str, Any] | None = None

    daily_records: list[dict[str, Any]] = []
    order_records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []
    signal_records: list[dict[str, Any]] = []

    def record_sell(
        *,
        date: pd.Timestamp,
        signal_date: pd.Timestamp,
        raw_price: float,
        source: str,
        reason: str,
        interval_id: str,
        theoretical_stop: float | None = None,
    ) -> None:
        nonlocal cash, shares, open_trade, stop_line, stop_distance, stop_atr
        if shares <= SHARE_EPSILON or open_trade is None:
            raise AssertionError("sell requires an open long trade")
        effective = fill_policy.expected_fill("sell", float(raw_price))
        sold = float(shares)
        cash += sold * effective
        entry_price = float(open_trade["entry_price"])
        trade_records.append(
            {
                **open_trade,
                "exit_signal_date": signal_date,
                "exit_date": date,
                "exit_price": effective,
                "exit_reason": reason,
                "shares": sold,
                "pnl": (effective - entry_price) * sold,
                "return_pct": (effective / entry_price - 1.0) * 100.0,
            }
        )
        order_records.append(
            {
                "case_id": case_id,
                "policy_id": policy_id,
                "interval_id": interval_id,
                "symbol": symbol,
                "type": "sell",
                "signal_date": signal_date,
                "date": date,
                "reason": reason,
                "shares": sold,
                "raw_price": float(raw_price),
                "fill_price": effective,
                "fill_source": source,
                "stop_line": theoretical_stop,
                "stop_distance_pct": (
                    float(stop_distance) * 100.0 if stop_distance is not None else np.nan
                ),
                "atr_at_entry": stop_atr,
            }
        )
        shares = 0.0
        open_trade = None
        stop_line = None
        stop_distance = None
        stop_atr = None

    def execute_pending(date: pd.Timestamp, row: pd.Series, item: dict[str, Any]) -> str:
        nonlocal cash, shares, open_trade, stop_line, stop_distance, stop_atr
        nonlocal sma_armed, reentry_stop_line
        raw_open = float(row["open"])
        side = str(item["side"])
        if side == "sell":
            record_sell(
                date=date,
                signal_date=pd.Timestamp(item["signal_date"]),
                raw_price=raw_open,
                source="next_open",
                reason=str(item["reason"]),
                interval_id=str(item["interval_id"]),
            )
            sma_armed = False
            return str(item["reason"])
        if side != "buy" or shares > SHARE_EPSILON:
            raise AssertionError("invalid pending buy")
        effective = fill_policy.expected_fill("buy", raw_open)
        bought = cash / effective
        cash = 0.0
        shares = bought
        stop_line = None
        stop_distance = None
        stop_atr = None
        if policy_id in ATR_POLICIES:
            stop_atr = float(item["atr20"])
            stop_distance = hard_stop_distance(stop_atr, effective, spec)
            stop_line = effective * (1.0 - stop_distance)
            reentry_stop_line = None
        sma_armed = False
        open_trade = {
            "case_id": case_id,
            "policy_id": policy_id,
            "interval_id": str(item["interval_id"]),
            "symbol": symbol,
            "entry_signal_date": pd.Timestamp(item["signal_date"]),
            "entry_date": date,
            "entry_price": effective,
            "entry_reason": str(item["reason"]),
        }
        order_records.append(
            {
                "case_id": case_id,
                "policy_id": policy_id,
                "interval_id": str(item["interval_id"]),
                "symbol": symbol,
                "type": "buy",
                "signal_date": pd.Timestamp(item["signal_date"]),
                "date": date,
                "reason": str(item["reason"]),
                "shares": bought,
                "raw_price": raw_open,
                "fill_price": effective,
                "fill_source": "next_open",
                "stop_line": stop_line,
                "stop_distance_pct": (
                    float(stop_distance) * 100.0 if stop_distance is not None else np.nan
                ),
                "atr_at_entry": stop_atr,
            }
        )
        return str(item["reason"])

    for _, row in data.iterrows():
        date = pd.Timestamp(row["date"])
        executed_reason = ""
        stop_filled_today = False
        if pending is not None:
            if pd.Timestamp(pending["execution_date"]) != date:
                raise AssertionError("pending instruction missed its execution date")
            if not _finite(row["open"]):
                raise ValueError(f"{symbol} lacks pending execution Open on {date.date()}")
            executed_reason = execute_pending(date, row, pending)
            pending = None

        if shares > SHARE_EPSILON and policy_id in ATR_POLICIES:
            if stop_line is None or not _finite(row["open"]) or not _finite(row["low"]):
                raise AssertionError("ATR long position lost its active stop or OHLC")
            raw_stop = hard_stop_raw_fill(
                open_=float(row["open"]),
                low=float(row["low"]),
                stop_line=float(stop_line),
            )
            if raw_stop is not None:
                raw_price, source = raw_stop
                interval_id = str(open_trade["interval_id"]) if open_trade else ""
                old_line = float(stop_line)
                record_sell(
                    date=date,
                    signal_date=date,
                    raw_price=raw_price,
                    source=source,
                    reason="hard_stop",
                    interval_id=interval_id,
                    theoretical_stop=old_line,
                )
                reentry_stop_line = old_line
                last_stop_date = date
                sma_armed = False
                executed_reason = "hard_stop"
                stop_filled_today = True
                if current_schedule_index is not None:
                    schedule.loc[current_schedule_index, "mid_bear_transition_count"] += 1

        start_index = start_rows.get(date)
        if start_index is not None:
            if shares > SHARE_EPSILON or pending is not None or current_schedule_index is not None:
                raise AssertionError("bear interval starts from a non-flat state")
            current_schedule_index = int(start_index)
            sma_armed = False
            stop_line = stop_distance = stop_atr = reentry_stop_line = None
            last_stop_date = None
            interval_eligible = bool(row.get("indicator_ready", False)) and all(
                _finite(row[column]) for column in ("open", "high", "low", "close", "sma200", "atr20")
            )
            schedule.loc[current_schedule_index, "eligible_at_start"] = interval_eligible
            close = float(row["close"]) if _finite(row["close"]) else np.nan
            upper = (
                float(row["sma200"]) * (1.0 + spec.sma_buffer)
                if _finite(row["sma200"])
                else np.nan
            )
            should_buy = interval_eligible and (
                policy_id != POLICY_SMA or close > upper
            )
            if policy_id == POLICY_SMA and interval_eligible and close <= upper:
                sma_armed = True
            if should_buy:
                interval_id = str(schedule.loc[current_schedule_index, "interval_id"])
                pending = {
                    "side": "buy",
                    "signal_date": date,
                    "execution_date": next_date[date],
                    "reason": "interval_start" if policy_id != POLICY_SMA else "interval_start_above_sma",
                    "interval_id": interval_id,
                    "atr20": float(row["atr20"]),
                }
                schedule.loc[current_schedule_index, "initial_active_count"] = 1

        if current_schedule_index is not None:
            interval = schedule.loc[current_schedule_index]
            interval_id = str(interval["interval_id"])
            close = float(row["close"]) if _finite(row["close"]) else np.nan
            sma = float(row["sma200"]) if _finite(row["sma200"]) else np.nan
            upper = sma * (1.0 + spec.sma_buffer) if _finite(sma) else np.nan
            lower = sma * (1.0 - spec.sma_buffer) if _finite(sma) else np.nan
            close_reason = ""
            is_end = date == pd.Timestamp(interval["end"])
            if is_end:
                if shares > SHARE_EPSILON:
                    pending = {
                        "side": "sell",
                        "signal_date": date,
                        "execution_date": next_date[date],
                        "reason": "interval_end",
                        "interval_id": interval_id,
                    }
                    close_reason = "interval_end"
                else:
                    close_reason = "interval_end_flat"
            elif interval_eligible and not stop_filled_today and pending is None:
                if policy_id == POLICY_SMA:
                    if shares > SHARE_EPSILON and close < lower:
                        pending = {
                            "side": "sell",
                            "signal_date": date,
                            "execution_date": next_date[date],
                            "reason": "sma_exit",
                            "interval_id": interval_id,
                        }
                        schedule.loc[current_schedule_index, "mid_bear_transition_count"] += 1
                        close_reason = "sma_exit"
                    elif shares <= SHARE_EPSILON:
                        if close <= upper:
                            sma_armed = True
                            close_reason = "sma_rearmed"
                        elif sma_armed and close > upper:
                            pending = {
                                "side": "buy",
                                "signal_date": date,
                                "execution_date": next_date[date],
                                "reason": "sma_entry_cross",
                                "interval_id": interval_id,
                                "atr20": float(row["atr20"]),
                            }
                            schedule.loc[current_schedule_index, "mid_bear_transition_count"] += 1
                            close_reason = "sma_entry_cross"
                elif policy_id == POLICY_ATR_PRICE and shares <= SHARE_EPSILON:
                    if (
                        last_stop_date is not None
                        and date > last_stop_date
                        and reentry_stop_line is not None
                        and close > reentry_stop_line
                    ):
                        pending = {
                            "side": "buy",
                            "signal_date": date,
                            "execution_date": next_date[date],
                            "reason": "price_line_reentry",
                            "interval_id": interval_id,
                            "atr20": float(row["atr20"]),
                        }
                        schedule.loc[current_schedule_index, "mid_bear_transition_count"] += 1
                        close_reason = "price_line_reentry"
                elif policy_id == POLICY_ATR_SMA and shares <= SHARE_EPSILON:
                    if last_stop_date is not None and date > last_stop_date:
                        if close <= upper:
                            sma_armed = True
                            close_reason = "post_stop_sma_rearmed"
                        elif sma_armed and close > upper:
                            pending = {
                                "side": "buy",
                                "signal_date": date,
                                "execution_date": next_date[date],
                                "reason": "post_stop_sma_cross",
                                "interval_id": interval_id,
                                "atr20": float(row["atr20"]),
                            }
                            schedule.loc[current_schedule_index, "mid_bear_transition_count"] += 1
                            close_reason = "post_stop_sma_cross"

            signal_records.append(
                {
                    "case_id": case_id,
                    "policy_id": policy_id,
                    "interval_id": interval_id,
                    "label": str(interval["label"]),
                    "severity": str(interval["severity"]),
                    "date": date,
                    "symbol": symbol,
                    "eligible_at_start": interval_eligible,
                    "close": close,
                    "sma200": sma,
                    "sma_upper": upper,
                    "sma_lower": lower,
                    "atr20": row["atr20"],
                    "shares": shares,
                    "sma_armed": sma_armed,
                    "active_stop_line": stop_line,
                    "reentry_stop_line": reentry_stop_line,
                    "last_stop_date": last_stop_date,
                    "pending_side": pending["side"] if pending is not None else "",
                    "reason": close_reason or executed_reason or "hold",
                }
            )
            if is_end:
                current_schedule_index = None
                interval_eligible = False
                sma_armed = False
                reentry_stop_line = None
                last_stop_date = None

        close_value = float(row["close"]) if _finite(row["close"]) else np.nan
        market_value = shares * close_value if _finite(close_value) else 0.0
        equity = cash + market_value
        daily_records.append(
            {
                "case_id": case_id,
                "policy_id": policy_id,
                "date": date,
                "symbol": symbol,
                "cash": cash,
                "shares": shares,
                "close": close_value,
                "market_value": market_value,
                "equity": equity,
                "gross_exposure": market_value / equity if equity > 0 else 0.0,
                "is_long": int(shares > SHARE_EPSILON),
            }
        )

    if pending is not None or shares > SHARE_EPSILON or open_trade is not None:
        raise AssertionError("event path did not finish flat")
    order_columns = (
        "case_id", "policy_id", "interval_id", "symbol", "type", "signal_date",
        "date", "reason", "shares", "raw_price", "fill_price", "fill_source",
        "stop_line", "stop_distance_pct", "atr_at_entry",
    )
    trade_columns = (
        "case_id", "policy_id", "interval_id", "symbol", "entry_signal_date",
        "entry_date", "entry_price", "entry_reason", "exit_signal_date", "exit_date",
        "exit_price", "exit_reason", "shares", "pnl", "return_pct",
    )
    signal_columns = tuple(signal_records[0]) if signal_records else ()
    daily = pd.DataFrame(daily_records)
    positions = daily[["date", "symbol", "shares"]].copy()
    return PolicyPath(
        daily=daily,
        orders=(pd.DataFrame(order_records) if order_records else _empty_records(order_columns)).loc[:, order_columns],
        trades=(pd.DataFrame(trade_records) if trade_records else _empty_records(trade_columns)).loc[:, trade_columns],
        signals=(pd.DataFrame(signal_records) if signal_records else _empty_records(signal_columns)),
        interval_schedule=schedule.reset_index(drop=True),
        positions=positions,
    )


def run_pybroker_compiled(
    prepared: pd.DataFrame,
    reference: PolicyPath,
    *,
    initial_cash: float,
) -> CompiledPyBrokerPath:
    """Replay explicit reference fills through PyBroker portfolio accounting."""

    available = prepared.dropna(subset=["open", "high", "low", "close"]).copy()
    if available.empty:
        raise ValueError("symbol has no available OHLC")
    available["date"] = pd.to_datetime(available["date"])
    orders = reference.orders.reset_index(drop=True)
    grouped = {
        pd.Timestamp(date): group.to_dict("records")
        for date, group in orders.groupby(pd.to_datetime(orders["date"]), sort=False)
    }
    expanded: list[dict[str, Any]] = []
    engine_orders: list[dict[str, Any]] = []
    engine_to_canonical: dict[pd.Timestamp, pd.Timestamp] = {}
    for row in available.to_dict("records"):
        canonical = pd.Timestamp(row["date"])
        day_orders = grouped.get(canonical, [])
        if len(day_orders) > 2:
            raise AssertionError("PyBroker compiler supports at most two fills per session")
        if len(day_orders) == 2:
            synthetic_date = canonical - pd.Timedelta(hours=1)
            synthetic = dict(row)
            synthetic["date"] = synthetic_date
            raw = float(day_orders[0]["raw_price"])
            for column in ("open", "high", "low", "close"):
                synthetic[column] = raw
            expanded.append(synthetic)
            engine_to_canonical[synthetic_date] = canonical
            first = dict(day_orders[0])
            first["engine_date"] = synthetic_date
            engine_orders.append(first)
            second = dict(day_orders[1])
            second["engine_date"] = canonical
            engine_orders.append(second)
        else:
            for item in day_orders:
                compiled = dict(item)
                compiled["engine_date"] = canonical
                engine_orders.append(compiled)
        expanded.append(row)
        engine_to_canonical[canonical] = canonical
    engine = pd.DataFrame(expanded).sort_values("date").reset_index(drop=True)
    dates = [pd.Timestamp(value) for value in engine["date"]]
    index_by_date = {date: index for index, date in enumerate(dates)}
    signal_by_prior: dict[pd.Timestamp, tuple[str, float]] = {}
    for order in engine_orders:
        fill_date = pd.Timestamp(order["engine_date"])
        fill_index = index_by_date[fill_date]
        if fill_index == 0:
            raise AssertionError("compiled fill has no prior submission bar")
        prior = dates[fill_index - 1]
        if prior in signal_by_prior:
            raise AssertionError("compiled fills share one submission bar")
        signal_by_prior[prior] = (str(order["type"]), float(order["fill_price"]))

    import pybroker
    from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
    from pybroker.context import ExecContext

    def execute(ctx: ExecContext) -> None:
        instruction = signal_by_prior.get(pd.Timestamp(ctx.dt))
        if instruction is None:
            return
        side, fill_price = instruction
        if side == "buy":
            ctx.buy_shares = MAX_AFFORDABLE_REQUEST
            ctx.buy_fill_price = Decimal(str(fill_price))
        else:
            ctx.sell_all_shares()
            ctx.sell_fill_price = Decimal(str(fill_price))

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = StrategyConfig(
        initial_cash=initial_cash,
        fee_mode=None,
        fee_amount=0,
        enable_fractional_shares=True,
        round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY,
        max_long_positions=1,
        buy_delay=1,
        sell_delay=1,
        exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.OPEN,
        exit_sell_fill_price=PriceType.OPEN,
        bars_per_year=252,
        return_signals=False,
        round_test_result=False,
    )
    strategy = Strategy(
        engine,
        pd.Timestamp(engine["date"].min()).isoformat(),
        pd.Timestamp(engine["date"].max()).isoformat(),
        config,
    )
    strategy.add_execution(execute, str(available.iloc[0]["symbol"]))
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    if not result.orders.empty:
        result.orders.loc[:, "date"] = pd.to_datetime(result.orders["date"]).map(
            engine_to_canonical
        )
    if not result.trades.empty:
        for column in ("entry_date", "exit_date"):
            result.trades.loc[:, column] = pd.to_datetime(result.trades[column]).map(
                engine_to_canonical
            )
    return CompiledPyBrokerPath(result, engine, engine_to_canonical)


def compiled_pybroker_path(
    compiled: CompiledPyBrokerPath,
    prepared: pd.DataFrame,
    reference: PolicyPath,
    *,
    initial_cash: float,
) -> PolicyPath:
    """Collapse synthetic engine bars and restore pre-listing all-cash dates."""

    result = compiled.result
    state = result.portfolio.reset_index()[["date", "cash", "equity"]].copy()
    state["engine_date"] = pd.to_datetime(state["date"])
    state["date"] = state["engine_date"].map(compiled.engine_to_canonical)
    shares_by_engine: dict[pd.Timestamp, float] = {}
    if not result.positions.empty:
        positions = result.positions.reset_index()
        shares_by_engine = {
            pd.Timestamp(date): float(value)
            for date, value in positions.groupby("date")["long_shares"].sum().items()
        }
    state["shares"] = state["engine_date"].map(shares_by_engine).fillna(0.0)
    state = state.sort_values("engine_date").groupby("date", as_index=False).tail(1)
    calendar = pd.DatetimeIndex(pd.to_datetime(prepared["date"]))
    state = state.set_index("date").reindex(calendar)
    state["cash"] = state["cash"].ffill().fillna(float(initial_cash))
    state["shares"] = state["shares"].ffill().fillna(0.0)
    close = prepared.set_index(pd.to_datetime(prepared["date"]))["close"].astype(float)
    state["close"] = close.reindex(calendar)
    state["market_value"] = np.where(
        state["shares"].to_numpy(dtype=float) > SHARE_EPSILON,
        state["shares"].to_numpy(dtype=float) * state["close"].fillna(0.0).to_numpy(dtype=float),
        0.0,
    )
    state["equity"] = state["cash"].astype(float) + state["market_value"].astype(float)
    state["gross_exposure"] = np.where(
        state["equity"] > 0,
        state["market_value"] / state["equity"],
        0.0,
    )
    state["is_long"] = (state["shares"] > SHARE_EPSILON).astype(int)
    state = state.reset_index().rename(columns={"index": "date"})
    state.insert(0, "case_id", str(reference.daily.iloc[0]["case_id"]))
    state.insert(1, "policy_id", str(reference.daily.iloc[0]["policy_id"]))
    state.insert(3, "symbol", str(reference.daily.iloc[0]["symbol"]))

    actual_orders = result.orders.reset_index().copy()
    actual_orders["date"] = pd.to_datetime(actual_orders["date"])
    if len(actual_orders) != len(reference.orders):
        raise AssertionError("PyBroker order count differs from reference")
    formal_orders = reference.orders.copy().reset_index(drop=True)
    if len(formal_orders):
        if actual_orders["type"].astype(str).tolist() != formal_orders["type"].astype(str).tolist():
            raise AssertionError("PyBroker order sides differ from reference")
        if pd.to_datetime(actual_orders["date"]).tolist() != pd.to_datetime(formal_orders["date"]).tolist():
            raise AssertionError("PyBroker order dates differ from reference")
        formal_orders.loc[:, "shares"] = actual_orders["shares"].to_numpy(dtype=float)
        formal_orders.loc[:, "fill_price"] = actual_orders["fill_price"].to_numpy(dtype=float)

    pybroker_trades = result.trades.reset_index(drop=True).copy()
    formal_trades = reference.trades.copy().reset_index(drop=True)
    if len(pybroker_trades) != len(formal_trades):
        raise AssertionError("PyBroker closed-trade count differs from reference")
    if len(formal_trades):
        for column in ("entry_date", "exit_date"):
            if pd.to_datetime(pybroker_trades[column]).tolist() != pd.to_datetime(
                formal_trades[column]
            ).tolist():
                raise AssertionError(f"PyBroker trade {column} differs from reference")
        formal_trades.loc[:, "entry_price"] = pybroker_trades["entry"].to_numpy(dtype=float)
        formal_trades.loc[:, "exit_price"] = pybroker_trades["exit"].to_numpy(dtype=float)
        formal_trades.loc[:, "shares"] = pybroker_trades["shares"].to_numpy(dtype=float)
        formal_trades.loc[:, "pnl"] = pybroker_trades["pnl"].to_numpy(dtype=float)
        formal_trades.loc[:, "return_pct"] = (
            formal_trades["exit_price"].astype(float)
            / formal_trades["entry_price"].astype(float)
            - 1.0
        ) * 100.0
    positions = state[["date", "symbol", "shares"]].copy()
    return PolicyPath(
        daily=state.loc[:, reference.daily.columns],
        orders=formal_orders,
        trades=formal_trades,
        signals=reference.signals.copy(),
        interval_schedule=reference.interval_schedule.copy(),
        positions=positions,
    )


def cross_check_policy_paths(
    actual: PolicyPath,
    reference: PolicyPath,
    *,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    if pd.to_datetime(actual.daily["date"]).tolist() != pd.to_datetime(reference.daily["date"]).tolist():
        raise AssertionError("daily calendars differ")
    differences: dict[str, float] = {}
    for column in ("cash", "shares", "equity"):
        actual_values = actual.daily[column].to_numpy(dtype=float)
        reference_values = reference.daily[column].to_numpy(dtype=float)
        if np.isnan(actual_values).all() and np.isnan(reference_values).all():
            diff = 0.0
        else:
            diff = float(
                np.nanmax(np.abs(actual_values - reference_values))
            )
        differences[f"daily_{column}"] = diff
        if diff > tolerance:
            raise AssertionError(f"daily {column} differs by {diff}")
    if len(actual.orders) != len(reference.orders):
        raise AssertionError("order counts differ")
    for column in ("shares", "fill_price"):
        if len(actual.orders):
            diff = float(
                np.max(
                    np.abs(
                        actual.orders[column].to_numpy(dtype=float)
                        - reference.orders[column].to_numpy(dtype=float)
                    )
                )
            )
        else:
            diff = 0.0
        differences[f"orders_{column}"] = diff
        if diff > tolerance:
            raise AssertionError(f"orders {column} differs by {diff}")
    if len(actual.trades) != len(reference.trades):
        raise AssertionError("trade counts differ")
    for column in ("entry_date", "exit_date"):
        if pd.to_datetime(actual.trades[column]).tolist() != pd.to_datetime(
            reference.trades[column]
        ).tolist():
            raise AssertionError(f"trade {column} differs")
    for column in ("entry_price", "exit_price", "shares", "pnl"):
        if len(actual.trades):
            diff = float(
                np.max(
                    np.abs(
                        actual.trades[column].to_numpy(dtype=float)
                        - reference.trades[column].to_numpy(dtype=float)
                    )
                )
            )
        else:
            diff = 0.0
        differences[f"trades_{column}"] = diff
        if diff > tolerance:
            raise AssertionError(f"trades {column} differs by {diff}")
    actual_positions = actual.positions.pivot(
        index="date", columns="symbol", values="shares"
    ).fillna(0.0)
    reference_positions = reference.positions.pivot(
        index="date", columns="symbol", values="shares"
    ).fillna(0.0)
    columns = sorted(set(actual_positions.columns).union(reference_positions.columns))
    position_diff = float(
        np.max(
            np.abs(
                actual_positions.reindex(columns=columns, fill_value=0.0).to_numpy(dtype=float)
                - reference_positions.reindex(columns=columns, fill_value=0.0).to_numpy(dtype=float)
            )
        )
    )
    differences["position_shares"] = position_diff
    if position_diff > tolerance:
        raise AssertionError(f"position shares differ by {position_diff}")
    return differences


def aggregate_group_path(
    member_paths: Mapping[str, PolicyPath],
    members: Sequence[str],
    *,
    group_target: str,
    policy_id: str,
    initial_cash: float,
) -> PolicyPath:
    """Re-equalize eligible independent member sleeves at each bear start."""

    member_list = list(dict.fromkeys(str(symbol) for symbol in members))
    if not member_list or set(member_list).difference(member_paths):
        raise ValueError("group members are missing paths")
    base = member_paths[member_list[0]]
    dates = pd.DatetimeIndex(pd.to_datetime(base.daily["date"]))
    schedule = base.interval_schedule.copy().reset_index(drop=True)
    cash_values = np.full(len(dates), np.nan, dtype=float)
    equity_values = np.full(len(dates), np.nan, dtype=float)
    exposure_values = np.full(len(dates), np.nan, dtype=float)
    position_values = np.zeros((len(dates), len(member_list)), dtype=float)
    date_index = {date: index for index, date in enumerate(dates)}
    current_equity = float(initial_cash)
    cursor = 0
    order_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    signal_frames: list[pd.DataFrame] = []

    for schedule_index, interval in schedule.iterrows():
        start = pd.Timestamp(interval["start"])
        exit_date = pd.Timestamp(interval["exit_execution_date"])
        start_index = date_index[start]
        exit_index = date_index[exit_date]
        if cursor < start_index:
            cash_values[cursor:start_index] = current_equity
            equity_values[cursor:start_index] = current_equity
            exposure_values[cursor:start_index] = 0.0
        eligible: list[str] = []
        for symbol in member_list:
            member_schedule = member_paths[symbol].interval_schedule
            row = member_schedule[member_schedule["interval_id"] == interval["interval_id"]]
            if len(row) != 1:
                raise AssertionError("member schedule differs from group schedule")
            if bool(row.iloc[0]["eligible_at_start"]):
                eligible.append(symbol)
        schedule.loc[schedule_index, "eligible_at_start"] = bool(eligible)
        schedule.loc[schedule_index, "eligible_member_count"] = len(eligible)
        schedule.loc[schedule_index, "eligible_members"] = ",".join(eligible)
        schedule.loc[schedule_index, "initial_active_count"] = int(
            sum(
                int(
                    member_paths[symbol].interval_schedule.loc[
                        member_paths[symbol].interval_schedule["interval_id"]
                        == interval["interval_id"],
                        "initial_active_count",
                    ].iloc[0]
                )
                for symbol in eligible
            )
        )
        schedule.loc[schedule_index, "mid_bear_transition_count"] = int(
            sum(
                int(
                    member_paths[symbol].interval_schedule.loc[
                        member_paths[symbol].interval_schedule["interval_id"]
                        == interval["interval_id"],
                        "mid_bear_transition_count",
                    ].iloc[0]
                )
                for symbol in eligible
            )
        )
        if not eligible:
            cash_values[start_index : exit_index + 1] = current_equity
            equity_values[start_index : exit_index + 1] = current_equity
            exposure_values[start_index : exit_index + 1] = 0.0
            cursor = exit_index + 1
            continue
        allocation = current_equity / len(eligible)
        for symbol in eligible:
            path = member_paths[symbol]
            member_daily = path.daily.set_index(pd.to_datetime(path.daily["date"]))
            member_start = float(member_daily.loc[start, "equity"])
            scale = allocation / member_start
            selected = member_daily.loc[start:exit_date]
            cash_values[start_index : exit_index + 1] = np.nan_to_num(
                cash_values[start_index : exit_index + 1], nan=0.0
            ) + selected["cash"].to_numpy(dtype=float) * scale
            equity_values[start_index : exit_index + 1] = np.nan_to_num(
                equity_values[start_index : exit_index + 1], nan=0.0
            ) + selected["equity"].to_numpy(dtype=float) * scale
            market = (
                selected["equity"].to_numpy(dtype=float)
                - selected["cash"].to_numpy(dtype=float)
            ) * scale
            exposure_values[start_index : exit_index + 1] = np.nan_to_num(
                exposure_values[start_index : exit_index + 1], nan=0.0
            ) + market
            member_position = path.positions.set_index(pd.to_datetime(path.positions["date"]))
            position_values[start_index : exit_index + 1, member_list.index(symbol)] = (
                member_position.loc[start:exit_date, "shares"].to_numpy(dtype=float) * scale
            )
            interval_id = str(interval["interval_id"])
            member_orders = path.orders[path.orders["interval_id"] == interval_id].copy()
            if not member_orders.empty:
                member_orders["shares"] = member_orders["shares"].astype(float) * scale
                member_orders.insert(0, "member_symbol", symbol)
                order_frames.append(member_orders)
            member_trades = path.trades[path.trades["interval_id"] == interval_id].copy()
            if not member_trades.empty:
                member_trades["shares"] = member_trades["shares"].astype(float) * scale
                member_trades["pnl"] = member_trades["pnl"].astype(float) * scale
                member_trades.insert(0, "member_symbol", symbol)
                trade_frames.append(member_trades)
            member_signals = path.signals[path.signals["interval_id"] == interval_id].copy()
            if not member_signals.empty:
                member_signals.insert(0, "member_symbol", symbol)
                signal_frames.append(member_signals)
        current_equity = float(equity_values[exit_index])
        cursor = exit_index + 1
    if cursor < len(dates):
        cash_values[cursor:] = current_equity
        equity_values[cursor:] = current_equity
        exposure_values[cursor:] = 0.0
    if np.isnan(cash_values).any() or np.isnan(equity_values).any() or np.isnan(exposure_values).any():
        raise AssertionError("group aggregation left missing daily state")
    gross = np.divide(
        exposure_values,
        equity_values,
        out=np.zeros_like(exposure_values),
        where=equity_values > 0,
    )
    case_id = f"{group_target}__{policy_id}"
    daily = pd.DataFrame(
        {
            "case_id": case_id,
            "policy_id": policy_id,
            "date": dates,
            "symbol": group_target,
            "cash": cash_values,
            "shares": np.nan,
            "close": np.nan,
            "market_value": exposure_values,
            "equity": equity_values,
            "gross_exposure": gross,
            "is_long": (exposure_values > 1e-9).astype(int),
        }
    )
    positions = pd.DataFrame(
        {
            "date": np.repeat(dates.to_numpy(), len(member_list)),
            "symbol": np.tile(np.array(member_list, dtype=object), len(dates)),
            "shares": position_values.reshape(-1),
        }
    )
    orders = pd.concat(order_frames, ignore_index=True) if order_frames else base.orders.iloc[0:0].copy()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else base.trades.iloc[0:0].copy()
    signals = pd.concat(signal_frames, ignore_index=True) if signal_frames else base.signals.iloc[0:0].copy()
    for frame in (orders, trades, signals):
        if not frame.empty:
            frame.loc[:, "case_id"] = case_id
            frame.loc[:, "policy_id"] = policy_id
            frame.loc[:, "symbol"] = group_target
    return PolicyPath(daily, orders, trades, signals, schedule, positions)
