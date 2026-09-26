"""Causal intraday-threshold QQQ strategy with a second independent ledger.

The strategy declares each next-session order from completed closes only.  A
PyBroker fill callback then inspects that next regular-session OHLC bar exactly
once, modelling a pre-placed conditional order rather than a close-generated
same-bar signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Literal

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.common import BarData
from pybroker.context import ExecContext

from quantkit.execution import MAX_AFFORDABLE_REQUEST


SELL_COST_STOP = "SELL_COST_STOP"
SELL_SLOW_TREND = "SELL_SLOW_TREND"
SELL_FAST_DROP = "SELL_FAST_DROP"
SELL_SMA200_CROSS = "SELL_SMA200_CROSS"
BUY_FORCED_REENTRY = "BUY_FORCED_REENTRY"
BUY_SMA200_CROSS = "BUY_SMA200_CROSS"
BUY_SHORT_RECOVERY = "BUY_SHORT_RECOVERY"

SELL_SIGNALS = (
    SELL_COST_STOP,
    SELL_SLOW_TREND,
    SELL_FAST_DROP,
    SELL_SMA200_CROSS,
)
BUY_SIGNALS = (BUY_FORCED_REENTRY, BUY_SMA200_CROSS, BUY_SHORT_RECOVERY)
ALL_SIGNALS = SELL_SIGNALS + BUY_SIGNALS


@dataclass(frozen=True)
class IntradaySmaSpec:
    """Frozen first-pass parameter set discussed with the user."""

    a_negative_days_slow: int = 7
    b_slow_sma_window: int = 130
    c_fast_derivative_pct: float = -0.15
    d_negative_days_fast: int = 3
    e_fallback_sma_window: int = 200
    f_short_sma_windows: tuple[int, int, int] = (25, 30, 35)
    g_short_recovery_below_pct: float = 1.0
    h_reentry_sma_window: int = 200
    l_cost_stop_pct: float = 1.5
    r_forced_rebuy_pct: float = 1.5
    fast_derivative_mode: Literal["short_average", "all_short_smas"] = "short_average"
    fast_drop_enabled: bool = True

    def __post_init__(self) -> None:
        if self.a_negative_days_slow < 2 or self.d_negative_days_fast < 2:
            raise ValueError("Negative-day streaks must be at least 2.")
        if not self.c_fast_derivative_pct < 0:
            raise ValueError("Fast derivative threshold must be negative.")
        if self.fast_derivative_mode not in {"short_average", "all_short_smas"}:
            raise ValueError("fast_derivative_mode must be short_average or all_short_smas.")
        windows = self.f_short_sma_windows
        if len(windows) != 3 or tuple(sorted(windows)) != windows or min(windows) < 2:
            raise ValueError("Short SMA windows must be three increasing values >= 2.")
        if min(self.b_slow_sma_window, self.e_fallback_sma_window, self.h_reentry_sma_window) < 2:
            raise ValueError("Long SMA windows must be >= 2.")
        for name, value in (
            ("g_short_recovery_below_pct", self.g_short_recovery_below_pct),
            ("l_cost_stop_pct", self.l_cost_stop_pct),
            ("r_forced_rebuy_pct", self.r_forced_rebuy_pct),
        ):
            if not np.isfinite(value) or value < 0 or value >= 100:
                raise ValueError(f"{name} must be finite and in [0, 100).")

    @classmethod
    def from_parameters(cls, parameters: dict[str, object]) -> "IntradaySmaSpec":
        return cls(
            a_negative_days_slow=int(parameters["A_negative_days_slow"]),
            b_slow_sma_window=int(parameters["B_slow_sma_window"]),
            c_fast_derivative_pct=float(parameters["C_fast_derivative_pct"]),
            d_negative_days_fast=int(parameters["D_negative_days_fast"]),
            e_fallback_sma_window=int(parameters["E_fallback_sma_window"]),
            f_short_sma_windows=tuple(int(x) for x in parameters["F_short_sma_windows"]),  # type: ignore[arg-type]
            g_short_recovery_below_pct=float(parameters["G_short_recovery_below_pct"]),
            h_reentry_sma_window=int(parameters["H_reentry_sma_window"]),
            l_cost_stop_pct=float(parameters["L_cost_stop_pct"]),
            r_forced_rebuy_pct=float(parameters["R_forced_rebuy_pct"]),
            fast_derivative_mode=str(
                parameters.get("C_fast_derivative_mode", "short_average")
            ),  # type: ignore[arg-type]
            fast_drop_enabled=bool(parameters.get("fast_drop_enabled", True)),
        )


@dataclass(frozen=True)
class Trigger:
    signal: str
    price: float
    formula: str


@dataclass(frozen=True)
class OrderPlan:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    triggers: tuple[Trigger, ...]
    signal_date: pd.Timestamp | None = None
    context: dict[str, float | int | str | bool | None] | None = None


@dataclass(frozen=True)
class FillEvent:
    date: pd.Timestamp
    side: Literal["buy", "sell"]
    primary_signal: str
    matched_signals: tuple[str, ...]
    theoretical_trigger: float
    fill_price: float
    fill_source: Literal["open_gap", "intraday_trigger"]
    trigger_prices: tuple[tuple[str, float], ...]


@dataclass
class IntradayReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame
    signal_plans: pd.DataFrame


@dataclass
class IntradayPyBrokerResult:
    pybroker_result: object
    engine_start: pd.Timestamp


def _normalized_rows(data: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows["symbol"].nunique() != 1:
        raise ValueError("Intraday SMA runner requires exactly one symbol.")
    if rows.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate symbol/date rows are not allowed.")
    ohlc = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError("OHLC must be finite and positive.")
    if ((rows["low"] > rows[["open", "close"]].min(axis=1)) | (rows["high"] < rows[["open", "close"]].max(axis=1))).any():
        raise ValueError("Invalid OHLC envelope.")
    return rows


def _rolling_all_negative(series: pd.Series, count: int) -> pd.Series:
    return series.lt(0).rolling(count, min_periods=count).sum().eq(count)


def prepare_intraday_sma_data(data: pd.DataFrame, spec: IntradaySmaSpec) -> pd.DataFrame:
    """Precompute display indicators and next-bar causal trigger ingredients."""

    rows = _normalized_rows(data)
    if rows["symbol"].nunique() != 1:
        raise ValueError("Exactly one symbol is required.")
    closes = rows["close"].astype(float)
    needed = set(spec.f_short_sma_windows)
    needed.update(
        range(70, 451, 10)
    )
    needed.update((spec.b_slow_sma_window, spec.e_fallback_sma_window, spec.h_reentry_sma_window))
    for window in sorted(needed):
        rows[f"sma{window}"] = closes.rolling(window, min_periods=window).mean()

    short_columns = [f"sma{window}" for window in spec.f_short_sma_windows]
    rows["sma_short_avg"] = rows[short_columns].mean(axis=1, skipna=False)
    for column in (*short_columns, "sma_short_avg"):
        rows[f"{column}_derivative_pct"] = rows[column].pct_change(fill_method=None) * 100.0

    beta = float(np.mean([1.0 / window for window in spec.f_short_sma_windows]))
    alpha_parts = []
    for window in spec.f_short_sma_windows:
        prior_sum = closes.shift(1).rolling(window - 1, min_periods=window - 1).sum()
        alpha_parts.append(prior_sum / window)
    rows["short_alpha"] = sum(alpha_parts) / len(alpha_parts)
    rows["short_beta"] = beta
    previous_short = rows["sma_short_avg"].shift(1)
    rows["trigger_short_derivative_zero"] = (previous_short - rows["short_alpha"]) / beta
    target_multiplier = 1.0 + spec.c_fast_derivative_pct / 100.0
    rows["trigger_sell_fast_drop_short_average"] = (
        previous_short * target_multiplier - rows["short_alpha"]
    ) / beta
    individual_fast_triggers = []
    for window in spec.f_short_sma_windows:
        prior_sum = closes.shift(1).rolling(window - 1, min_periods=window - 1).sum()
        previous_sma = rows[f"sma{window}"].shift(1)
        column = f"trigger_sell_fast_drop_sma{window}"
        rows[column] = window * previous_sma * target_multiplier - prior_sum
        individual_fast_triggers.append(column)
    rows["trigger_sell_fast_drop_all_short_smas"] = rows[individual_fast_triggers].min(
        axis=1,
        skipna=False,
    )
    rows["trigger_sell_fast_drop"] = rows[
        "trigger_sell_fast_drop_all_short_smas"
        if spec.fast_derivative_mode == "all_short_smas"
        else "trigger_sell_fast_drop_short_average"
    ]

    crossing_windows = {
        "trigger_sell_sma130_boundary": spec.b_slow_sma_window,
        "trigger_sell_sma200_cross": spec.e_fallback_sma_window,
        "trigger_buy_sma200_cross": spec.h_reentry_sma_window,
    }
    for column, window in crossing_windows.items():
        rows[column] = closes.shift(1).rolling(window - 1, min_periods=window - 1).sum() / (window - 1)
    rows["trigger_sell_slow_trend"] = rows[
        ["trigger_short_derivative_zero", "trigger_sell_sma130_boundary"]
    ].min(axis=1, skipna=False)

    recovery_multiplier = 1.0 - spec.g_short_recovery_below_pct / 100.0
    rows["trigger_buy_short_recovery"] = (
        recovery_multiplier * rows["short_alpha"] / (1.0 - recovery_multiplier * beta)
    )
    derivative = rows["sma_short_avg_derivative_pct"]
    rows["eligible_sell_slow_streak"] = _rolling_all_negative(
        derivative.shift(1), spec.a_negative_days_slow - 1
    )
    rows["eligible_sell_fast_streak"] = _rolling_all_negative(
        derivative.shift(1), spec.d_negative_days_fast - 1
    )
    rows["eligible_sell_sma200_cross"] = closes.shift(1).ge(
        rows[f"sma{spec.e_fallback_sma_window}"].shift(1)
    )
    rows["eligible_buy_sma200_cross"] = closes.shift(1).lt(
        rows[f"sma{spec.h_reentry_sma_window}"].shift(1)
    )
    rows["eligible_buy_short_recovery"] = closes.shift(1).lt(
        rows["sma_short_avg"].shift(1) * recovery_multiplier
    )
    return rows


def _finite_trigger(signal: str, value: float, formula: str) -> Trigger | None:
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        return None
    return Trigger(signal=signal, price=value, formula=formula)


def build_order_plan(
    row: pd.Series,
    spec: IntradaySmaSpec,
    *,
    is_long: bool,
    cost_basis: float | None,
    last_sell_price: float | None,
    disabled_signals: frozenset[str] = frozenset(),
) -> OrderPlan:
    triggers: list[Trigger] = []
    date = pd.Timestamp(row["date"]).normalize()
    signal_date_value = row.get("prior_date")
    signal_date = None if pd.isna(signal_date_value) else pd.Timestamp(signal_date_value).normalize()
    context = {
        "cost_basis_before": cost_basis,
        "last_sell_price_before": last_sell_price,
        "prior_short_avg": None if pd.isna(row.get("prior_short_avg")) else float(row["prior_short_avg"]),
        "prior_short_avg_derivative_pct": None
        if pd.isna(row.get("prior_short_avg_derivative_pct"))
        else float(row["prior_short_avg_derivative_pct"]),
    }
    if is_long:
        if cost_basis is None:
            raise ValueError("A long state requires a cost basis.")
        if SELL_COST_STOP not in disabled_signals:
            trigger = _finite_trigger(
                SELL_COST_STOP,
                cost_basis * (1.0 - spec.l_cost_stop_pct / 100.0),
                f"cost_basis × (1 - {spec.l_cost_stop_pct:g}%)",
            )
            if trigger:
                triggers.append(trigger)
        if bool(row.get("eligible_sell_slow_streak", False)) and SELL_SLOW_TREND not in disabled_signals:
            trigger = _finite_trigger(
                SELL_SLOW_TREND,
                row["trigger_sell_slow_trend"],
                f"min(current short-average derivative=0, price=provisional SMA{spec.b_slow_sma_window}) after {spec.a_negative_days_slow - 1} completed negative days",
            )
            if trigger:
                triggers.append(trigger)
        if (
            spec.fast_drop_enabled
            and bool(row.get("eligible_sell_fast_streak", False))
            and SELL_FAST_DROP not in disabled_signals
        ):
            if spec.fast_derivative_mode == "all_short_smas":
                fast_formula = (
                    f"all provisional SMA{spec.f_short_sma_windows} derivatives"
                    f" <= {spec.c_fast_derivative_pct:g}% after "
                    f"{spec.d_negative_days_fast - 1} completed negative short-average days"
                )
            else:
                fast_formula = (
                    f"current provisional short-average derivative={spec.c_fast_derivative_pct:g}%"
                    f" after {spec.d_negative_days_fast - 1} completed negative days"
                )
            trigger = _finite_trigger(
                SELL_FAST_DROP,
                row["trigger_sell_fast_drop"],
                fast_formula,
            )
            if trigger:
                triggers.append(trigger)
        if bool(row.get("eligible_sell_sma200_cross", False)) and SELL_SMA200_CROSS not in disabled_signals:
            trigger = _finite_trigger(
                SELL_SMA200_CROSS,
                row["trigger_sell_sma200_cross"],
                f"price=provisional SMA{spec.e_fallback_sma_window} downward crossing",
            )
            if trigger:
                triggers.append(trigger)
        side: Literal["buy", "sell"] = "sell"
    else:
        if last_sell_price is not None and BUY_FORCED_REENTRY not in disabled_signals:
            trigger = _finite_trigger(
                BUY_FORCED_REENTRY,
                last_sell_price * (1.0 + spec.r_forced_rebuy_pct / 100.0),
                f"last_sell_fill × (1 + {spec.r_forced_rebuy_pct:g}%)",
            )
            if trigger:
                triggers.append(trigger)
        if bool(row.get("eligible_buy_sma200_cross", False)) and BUY_SMA200_CROSS not in disabled_signals:
            trigger = _finite_trigger(
                BUY_SMA200_CROSS,
                row["trigger_buy_sma200_cross"],
                f"price=provisional SMA{spec.h_reentry_sma_window} upward crossing",
            )
            if trigger:
                triggers.append(trigger)
        if bool(row.get("eligible_buy_short_recovery", False)) and BUY_SHORT_RECOVERY not in disabled_signals:
            trigger = _finite_trigger(
                BUY_SHORT_RECOVERY,
                row["trigger_buy_short_recovery"],
                f"price=(1-{spec.g_short_recovery_below_pct:g}%) × provisional mean(SMA{spec.f_short_sma_windows}) upward crossing",
            )
            if trigger:
                triggers.append(trigger)
        side = "buy"
    return OrderPlan(
        date=date,
        side=side,
        triggers=tuple(triggers),
        signal_date=signal_date,
        context=context,
    )


def evaluate_order_plan(
    plan: OrderPlan,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> FillEvent | None:
    """Evaluate a predeclared conditional order against one OHLC bar."""

    values = (open_, high, low, close)
    if not np.isfinite(values).all() or min(values) <= 0:
        raise ValueError("Bar OHLC must be finite and positive.")
    if not plan.triggers:
        return None
    if plan.side == "sell":
        ordered = sorted(plan.triggers, key=lambda trigger: (-trigger.price, SELL_SIGNALS.index(trigger.signal)))
        gap = [trigger for trigger in ordered if open_ <= trigger.price]
        if gap:
            primary = ordered[0]
            matched = tuple(trigger.signal for trigger in gap)
            return FillEvent(
                date=plan.date,
                side="sell",
                primary_signal=primary.signal,
                matched_signals=matched,
                theoretical_trigger=primary.price,
                fill_price=float(open_),
                fill_source="open_gap",
                trigger_prices=tuple((trigger.signal, trigger.price) for trigger in ordered),
            )
        touched = [trigger for trigger in ordered if low <= trigger.price < open_]
        if not touched:
            return None
        primary = touched[0]
        matched = tuple(
            trigger.signal for trigger in touched if np.isclose(trigger.price, primary.price, rtol=0, atol=1e-12)
        )
    else:
        ordered = sorted(plan.triggers, key=lambda trigger: (trigger.price, BUY_SIGNALS.index(trigger.signal)))
        gap = [trigger for trigger in ordered if open_ >= trigger.price]
        if gap:
            primary = ordered[0]
            matched = tuple(trigger.signal for trigger in gap)
            return FillEvent(
                date=plan.date,
                side="buy",
                primary_signal=primary.signal,
                matched_signals=matched,
                theoretical_trigger=primary.price,
                fill_price=float(open_),
                fill_source="open_gap",
                trigger_prices=tuple((trigger.signal, trigger.price) for trigger in ordered),
            )
        touched = [trigger for trigger in ordered if high >= trigger.price > open_]
        if not touched:
            return None
        primary = touched[0]
        matched = tuple(
            trigger.signal for trigger in touched if np.isclose(trigger.price, primary.price, rtol=0, atol=1e-12)
        )
    return FillEvent(
        date=plan.date,
        side=plan.side,
        primary_signal=primary.signal,
        matched_signals=matched,
        theoretical_trigger=primary.price,
        fill_price=primary.price,
        fill_source="intraday_trigger",
        trigger_prices=tuple((trigger.signal, trigger.price) for trigger in ordered),
    )


def _analysis_rows(
    data: pd.DataFrame,
    spec: IntradaySmaSpec,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    *,
    allow_incomplete_warmup: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    prepared = prepare_intraday_sma_data(data, spec)
    start = pd.Timestamp(analysis_start).normalize()
    end = pd.Timestamp(analysis_end).normalize()
    matches = prepared.index[prepared["date"] == start].tolist()
    end_matches = prepared.index[prepared["date"] == end].tolist()
    if len(matches) != 1 or len(end_matches) != 1:
        raise ValueError("Analysis start/end must each be one available trading date.")
    start_index = matches[0]
    end_index = end_matches[0]
    warmup = max(
        max(spec.f_short_sma_windows),
        spec.b_slow_sma_window,
        spec.e_fallback_sma_window,
        spec.h_reentry_sma_window,
    )
    minimum_start_index = 0 if allow_incomplete_warmup else max(warmup, 1)
    if start_index < minimum_start_index or end_index < start_index:
        raise ValueError("Insufficient warmup or invalid analysis interval.")
    prepared["prior_date"] = prepared["date"].shift(1)
    prepared["prior_short_avg"] = prepared["sma_short_avg"].shift(1)
    prepared["prior_short_avg_derivative_pct"] = prepared["sma_short_avg_derivative_pct"].shift(1)
    analysis = prepared.iloc[start_index : end_index + 1].copy().reset_index(drop=True)
    engine_start_index = max(start_index - 1, 0)
    engine = prepared.iloc[engine_start_index : end_index + 1].copy().reset_index(drop=True)
    return engine, analysis, start_index


def _plan_record(plan: OrderPlan, event: FillEvent | None) -> dict[str, object]:
    record: dict[str, object] = {
        "date": plan.date,
        "signal_date": plan.signal_date,
        "side": plan.side,
        "candidate_signals": "|".join(trigger.signal for trigger in plan.triggers),
        "candidate_triggers": "|".join(f"{trigger.signal}:{trigger.price:.12g}" for trigger in plan.triggers),
        "filled": event is not None,
    }
    if event is not None:
        record.update(
            {
                "primary_signal": event.primary_signal,
                "matched_signals": "|".join(event.matched_signals),
                "theoretical_trigger": event.theoretical_trigger,
                "fill_price": event.fill_price,
                "fill_source": event.fill_source,
            }
        )
    if plan.context:
        record.update(plan.context)
    return record


def run_reference_intraday_sma(
    data: pd.DataFrame,
    spec: IntradaySmaSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_shares: float | None = None,
    initial_cash: float | None = None,
    initial_position: Literal["long", "flat"] = "long",
    disabled_signals: Iterable[str] = (),
) -> IntradayReferenceResult:
    """Independent direct ledger, intentionally separate from PyBroker."""

    if initial_position not in {"long", "flat"}:
        raise ValueError("initial_position must be long or flat.")
    if initial_position == "long":
        if initial_shares is None or not np.isfinite(initial_shares) or initial_shares <= 0:
            raise ValueError("A long start requires finite positive initial_shares.")
    else:
        if initial_cash is None or not np.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("A flat start requires finite positive initial_cash.")
    disabled = frozenset(disabled_signals)
    unknown = disabled - set(ALL_SIGNALS)
    if unknown:
        raise ValueError(f"Unknown disabled signals: {sorted(unknown)}")
    engine, analysis, _ = _analysis_rows(
        data,
        spec,
        analysis_start,
        analysis_end,
        allow_incomplete_warmup=initial_position == "flat",
    )
    symbol = str(analysis.iloc[0]["symbol"])
    initial_date = pd.Timestamp(analysis.iloc[0]["date"])
    initial_open = float(analysis.iloc[0]["open"])
    shares = float(initial_shares) if initial_position == "long" else 0.0
    cash = 0.0 if initial_position == "long" else float(initial_cash)
    cost_basis: float | None = initial_open if initial_position == "long" else None
    last_sell_price: float | None = None
    order_records: list[dict[str, object]] = []
    if initial_position == "long":
        order_records.append({
            "symbol": symbol,
            "type": "buy",
            "signal_date": pd.Timestamp(engine.iloc[0]["date"]),
            "date": initial_date,
            "shares": shares,
            "fill_price": initial_open,
            "primary_signal": "INITIAL_SEED",
            "matched_signals": "INITIAL_SEED",
            "theoretical_trigger": initial_open,
            "fill_source": "initial_open",
            "cost_basis_before": None,
            "cost_basis_after": initial_open,
            "last_sell_price_before": None,
            "last_sell_price_after": None,
            "is_initial_seed": True,
        })
    daily_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []
    plan_records: list[dict[str, object]] = []
    open_trade: dict[str, object] | None = None
    if initial_position == "long":
        open_trade = {
            "symbol": symbol,
            "entry_signal_date": pd.Timestamp(engine.iloc[0]["date"]),
            "entry_date": initial_date,
            "entry_price": initial_open,
            "shares": shares,
            "entry_signal": "INITIAL_SEED",
        }

    for index, row in analysis.iterrows():
        current_date = pd.Timestamp(row["date"])
        seeded_today = initial_position == "long" and index == 0
        executed = 1 if seeded_today else 0
        primary_signal = "INITIAL_SEED" if seeded_today else ""
        if initial_position == "flat" or index > 0:
            before_cost = cost_basis
            before_sell = last_sell_price
            plan = build_order_plan(
                row,
                spec,
                is_long=shares > 0,
                cost_basis=cost_basis,
                last_sell_price=last_sell_price,
                disabled_signals=disabled,
            )
            event = evaluate_order_plan(
                plan,
                open_=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            plan_records.append(_plan_record(plan, event))
            if event is not None:
                primary_signal = event.primary_signal
                if event.side == "sell":
                    if shares <= 0 or open_trade is None:
                        raise AssertionError("Independent ledger attempted to sell while flat.")
                    order_shares = shares
                    cash = order_shares * event.fill_price
                    pnl = (event.fill_price - float(open_trade["entry_price"])) * order_shares
                    trade_records.append(
                        {
                            **open_trade,
                            "exit_signal_date": plan.signal_date,
                            "exit_date": current_date,
                            "exit_price": event.fill_price,
                            "exit_signal": event.primary_signal,
                            "exit_matched_signals": "|".join(event.matched_signals),
                            "pnl": pnl,
                            "return_pct": (event.fill_price / float(open_trade["entry_price"]) - 1.0) * 100.0,
                        }
                    )
                    shares = 0.0
                    cost_basis = None
                    last_sell_price = event.fill_price
                    open_trade = None
                    executed = -1
                else:
                    if shares > 0:
                        raise AssertionError("Independent ledger attempted to buy while long.")
                    shares = cash / event.fill_price
                    cash = 0.0
                    order_shares = shares
                    cost_basis = event.fill_price
                    open_trade = {
                        "symbol": symbol,
                        "entry_signal_date": plan.signal_date,
                        "entry_date": current_date,
                        "entry_price": event.fill_price,
                        "shares": shares,
                        "entry_signal": event.primary_signal,
                    }
                    executed = 1
                order_records.append(
                    {
                        "symbol": symbol,
                        "type": event.side,
                        "signal_date": plan.signal_date,
                        "date": current_date,
                        "shares": order_shares,
                        "fill_price": event.fill_price,
                        "primary_signal": event.primary_signal,
                        "matched_signals": "|".join(event.matched_signals),
                        "trigger_formula": next(
                            trigger.formula
                            for trigger in plan.triggers
                            if trigger.signal == event.primary_signal
                        ),
                        "theoretical_trigger": event.theoretical_trigger,
                        "fill_source": event.fill_source,
                        "cost_basis_before": before_cost,
                        "cost_basis_after": cost_basis,
                        "last_sell_price_before": before_sell,
                        "last_sell_price_after": last_sell_price,
                        "is_initial_seed": False,
                    }
                )
        daily_records.append(
            {
                "date": current_date,
                "symbol": symbol,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "executed": executed,
                "primary_signal": primary_signal,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
                "cost_basis": cost_basis,
                "last_sell_price": last_sell_price,
            }
        )
    return IntradayReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records),
        trades=pd.DataFrame(trade_records),
        signal_plans=pd.DataFrame(plan_records),
    )


def run_pybroker_intraday_sma(
    data: pd.DataFrame,
    spec: IntradaySmaSpec,
    *,
    analysis_start: pd.Timestamp | str,
    analysis_end: pd.Timestamp | str,
    initial_shares: float | None = None,
    initial_cash: float | None = None,
    initial_position: Literal["long", "flat"] = "long",
    disabled_signals: Iterable[str] = (),
) -> IntradayPyBrokerResult:
    """Run predeclared next-bar conditional orders in PyBroker.

    Positive/low limit sentinels make an untouched one-day conditional order
    expire without a fill.  A triggered callback returns either the opening gap
    price or the exact declared threshold.
    """

    if initial_position not in {"long", "flat"}:
        raise ValueError("initial_position must be long or flat.")
    if initial_position == "long":
        if initial_shares is None or not np.isfinite(initial_shares) or initial_shares <= 0:
            raise ValueError("A long start requires finite positive initial_shares.")
    else:
        if initial_cash is None or not np.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("A flat start requires finite positive initial_cash.")
    disabled = frozenset(disabled_signals)
    unknown = disabled - set(ALL_SIGNALS)
    if unknown:
        raise ValueError(f"Unknown disabled signals: {sorted(unknown)}")
    engine, analysis, _ = _analysis_rows(
        data,
        spec,
        analysis_start,
        analysis_end,
        allow_incomplete_warmup=initial_position == "flat",
    )
    initial_date = pd.Timestamp(analysis.iloc[0]["date"])
    initial_open = float(analysis.iloc[0]["open"])
    initial_equity = (
        float(initial_shares) * initial_open
        if initial_position == "long"
        else float(initial_cash)
    )
    symbol = str(analysis.iloc[0]["symbol"])
    row_by_date = {pd.Timestamp(row.date): row for row in analysis.itertuples(index=False)}
    analysis_dates = analysis["date"].tolist()
    next_date = {
        pd.Timestamp(current): pd.Timestamp(following)
        for current, following in zip(analysis_dates, analysis_dates[1:])
    }
    runtime: dict[str, object] = {
        "cost_basis": None,
        "last_sell_price": None,
        "pending_event": None,
    }
    buy_reject_limit = Decimal("100000000000")
    sell_reject_limit = Decimal("0.000000001")

    def conditional_fill(plan: OrderPlan):
        def fill(_symbol: str, bar: BarData) -> float:
            event = evaluate_order_plan(
                plan,
                open_=float(bar.open[-1]),
                high=float(bar.high[-1]),
                low=float(bar.low[-1]),
                close=float(bar.close[-1]),
            )
            runtime["pending_event"] = event
            if event is None:
                return 100000000001.0 if plan.side == "buy" else 0.0000000001
            return event.fill_price

        return fill

    pre_start = pd.Timestamp(engine.iloc[0]["date"])

    def submit(ctx: ExecContext, plan: OrderPlan) -> None:
        if not plan.triggers:
            return
        if plan.side == "sell":
            ctx.sell_all_shares()
            ctx.sell_fill_price = conditional_fill(plan)
            ctx.sell_limit_price = sell_reject_limit
        else:
            ctx.buy_shares = MAX_AFFORDABLE_REQUEST
            ctx.buy_fill_price = conditional_fill(plan)
            ctx.buy_limit_price = buy_reject_limit

    def execute(ctx: ExecContext) -> None:
        current = pd.Timestamp(ctx.dt).normalize()
        pending = runtime.get("pending_event")
        if isinstance(pending, FillEvent) and pending.date == current:
            if pending.side == "sell":
                runtime["cost_basis"] = None
                runtime["last_sell_price"] = pending.fill_price
            else:
                runtime["cost_basis"] = pending.fill_price
            runtime["pending_event"] = None

        if current == pre_start:
            if initial_position == "long":
                ctx.buy_shares = Decimal(str(initial_shares))
                ctx.buy_fill_price = PriceType.OPEN
            else:
                target = pd.Series(row_by_date[initial_date]._asdict())
                submit(
                    ctx,
                    build_order_plan(
                        target,
                        spec,
                        is_long=False,
                        cost_basis=None,
                        last_sell_price=None,
                        disabled_signals=disabled,
                    ),
                )
            return
        if initial_position == "long" and current == initial_date and runtime["cost_basis"] is None:
            runtime["cost_basis"] = initial_open
        following = next_date.get(current)
        if following is None:
            return
        target = pd.Series(row_by_date[following]._asdict())
        plan = build_order_plan(
            target,
            spec,
            is_long=ctx.long_pos() is not None,
            cost_basis=None if runtime["cost_basis"] is None else float(runtime["cost_basis"]),
            last_sell_price=None
            if runtime["last_sell_price"] is None
            else float(runtime["last_sell_price"]),
            disabled_signals=disabled,
        )
        submit(ctx, plan)

    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    config = StrategyConfig(
        initial_cash=initial_equity,
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
        pd.Timestamp(engine["date"].min()).date().isoformat(),
        pd.Timestamp(engine["date"].max()).date().isoformat(),
        config,
    )
    strategy.add_execution(execute, symbol)
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    return IntradayPyBrokerResult(pybroker_result=result, engine_start=pre_start)
