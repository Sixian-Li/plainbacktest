"""Stateful SMA entry and four-rule exit ablation with a second ledger.

Entry qualification is confirmed only from completed closes.  Once qualified,
the next and subsequent sessions carry a pre-declared intraday buy threshold
equal to the higher of 98% of the provisional SMA30 and SMA200 boundaries.
Exit signals are completed-close conditions filled at the following open.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Literal, Sequence

import numpy as np
import pandas as pd
import pybroker
from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
from pybroker.context import ExecContext

from quantkit.execution import MAX_AFFORDABLE_REQUEST
from quantkit.sma_regime import RegimeReferenceResult


SELL_SMA200_DOWN_3D = "SELL_SMA200_DOWN_3D"
SELL_SHORT_AVG_DROP_015 = "SELL_SHORT_AVG_DROP_GT_0_15PCT_1D"
SELL_CLOSE_BELOW_SMA200 = "SELL_CLOSE_BELOW_SMA200"
SELL_CLOSE_BELOW_SMA30 = "SELL_CLOSE_BELOW_SMA30"
SELL_RULES = (
    SELL_SMA200_DOWN_3D,
    SELL_SHORT_AVG_DROP_015,
    SELL_CLOSE_BELOW_SMA200,
    SELL_CLOSE_BELOW_SMA30,
)

BUY_ROUTE_SHORTS_THEN_SMA200 = "BUY_ROUTE_SHORTS_THEN_SMA200"
BUY_ROUTE_SMA200_THEN_SHORTS = "BUY_ROUTE_SMA200_THEN_SHORTS"
BUY_ROUTE_BOTH = "BUY_ROUTE_BOTH"
BUY_ARMED_PRICE_FILTER = "BUY_ARMED_PRICE_ABOVE_98PCT_SMA30_AND_SMA200"


def _case_id(mask: int) -> str:
    enabled = "_".join(f"r{index + 1}" for index in range(4) if mask & (1 << index))
    return f"mask_{mask:02d}_{enabled or 'none'}"


CASE_FLAGS: dict[str, tuple[bool, bool, bool, bool]] = {
    _case_id(mask): tuple(bool(mask & (1 << index)) for index in range(4))  # type: ignore[misc]
    for mask in range(16)
}


@dataclass(frozen=True)
class EntryExitAblationSpec:
    sell_sma200_down_3d: bool
    sell_short_avg_drop_015: bool
    sell_close_below_sma200: bool
    sell_close_below_sma30: bool
    buy_threshold_below_sma_pct: float = 2.0
    short_avg_drop_threshold_pct: float = -0.15

    def __post_init__(self) -> None:
        if not 0 <= self.buy_threshold_below_sma_pct < 100:
            raise ValueError("buy_threshold_below_sma_pct must be in [0, 100).")
        if not self.short_avg_drop_threshold_pct < 0:
            raise ValueError("short_avg_drop_threshold_pct must be negative.")

    @classmethod
    def from_case_id(cls, case_id: str) -> "EntryExitAblationSpec":
        try:
            flags = CASE_FLAGS[case_id]
        except KeyError as exc:
            raise ValueError(f"Unknown ablation case: {case_id!r}") from exc
        return cls(*flags)

    @property
    def enabled_sell_rules(self) -> tuple[str, ...]:
        return tuple(rule for rule, enabled in zip(SELL_RULES, self.flags) if enabled)

    @property
    def flags(self) -> tuple[bool, bool, bool, bool]:
        return (
            self.sell_sma200_down_3d,
            self.sell_short_avg_drop_015,
            self.sell_close_below_sma200,
            self.sell_close_below_sma30,
        )


def _normalized_rows(data: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if rows["symbol"].nunique() != 1 or rows.duplicated(["symbol", "date"]).any():
        raise ValueError("Data must contain one symbol and unique dates.")
    ohlc = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(ohlc).all() or (ohlc <= 0).any():
        raise ValueError("OHLC must be finite and positive.")
    if (
        (rows["low"] > rows[["open", "close"]].min(axis=1))
        | (rows["high"] < rows[["open", "close"]].max(axis=1))
    ).any():
        raise ValueError("Invalid OHLC envelope.")
    return rows


def _dynamic_scaled_sma_boundary(
    closes: pd.Series, window: int, multiplier: float
) -> pd.Series:
    """Solve p = multiplier * provisional_SMA(window, p) before the session."""
    prior_sum = closes.shift(1).rolling(window - 1, min_periods=window - 1).sum()
    return multiplier * prior_sum / (window - multiplier)


def prepare_entry_exit_ablation_data(
    data: pd.DataFrame,
    *,
    short_windows: Sequence[int] = (25, 30, 35),
    long_window: int = 200,
    buy_short_buffer_pct: float = 2.0,
    buy_long_buffer_pct: float = 2.0,
    r1_decline_days: int = 3,
    r1_min_daily_decline_pct: float = 0.0,
    r3_sell_buffer_pct: float = 0.0,
) -> pd.DataFrame:
    short_windows = tuple(int(value) for value in short_windows)
    if len(short_windows) != 3 or sorted(short_windows) != list(short_windows):
        raise ValueError("short_windows must contain three strictly ordered windows.")
    if short_windows[1] <= 1 or long_window <= 1:
        raise ValueError("SMA windows must be greater than one.")
    if r1_decline_days < 1:
        raise ValueError("r1_decline_days must be positive.")
    for name, value in (
        ("buy_short_buffer_pct", buy_short_buffer_pct),
        ("buy_long_buffer_pct", buy_long_buffer_pct),
        ("r3_sell_buffer_pct", r3_sell_buffer_pct),
    ):
        if not 0 <= float(value) < 100:
            raise ValueError(f"{name} must be in [0, 100).")
    if float(r1_min_daily_decline_pct) < 0:
        raise ValueError("r1_min_daily_decline_pct must be non-negative.")

    rows = _normalized_rows(data)
    close = rows["close"].astype(float)
    for window in (*short_windows, long_window):
        rows[f"sma{window}"] = close.rolling(window, min_periods=window).mean()
    short_columns = [f"sma{window}" for window in short_windows]
    short_reference_column = f"sma{short_windows[1]}"
    long_column = f"sma{long_window}"
    rows["sma_short_avg"] = rows[short_columns].mean(axis=1, skipna=False)

    crossed_columns = []
    for column in (*short_columns, "sma_short_avg"):
        crossed = f"close_crossed_above_{column}"
        rows[crossed] = close.shift(1).le(rows[column].shift(1)) & close.gt(rows[column])
        crossed_columns.append(crossed)
    rows["close_crossed_above_all_short_lines"] = rows[crossed_columns].all(axis=1)
    rows["close_above_all_short_lines"] = pd.concat(
        [close.gt(rows[column]) for column in (*short_columns, "sma_short_avg")], axis=1
    ).all(axis=1)
    rows["close_crossed_above_sma200"] = close.shift(1).le(
        rows[long_column].shift(1)
    ) & close.gt(rows[long_column])
    rows["close_above_sma200"] = close.gt(rows[long_column])

    daily_long_change_pct = rows[long_column].pct_change(fill_method=None) * 100.0
    rows["sma200_down_3d"] = (
        daily_long_change_pct.lt(-float(r1_min_daily_decline_pct))
        .rolling(r1_decline_days, min_periods=r1_decline_days)
        .sum()
        .eq(r1_decline_days)
    )
    rows["sma_short_avg_change_pct"] = (
        rows["sma_short_avg"].pct_change(fill_method=None) * 100.0
    )
    rows["sma_short_avg_drop_gt_0_15pct_1d"] = rows[
        "sma_short_avg_change_pct"
    ].lt(-0.15)
    rows["close_below_sma200"] = close.lt(
        rows[long_column] * (1.0 - float(r3_sell_buffer_pct) / 100.0)
    )
    rows["close_below_sma30"] = close.lt(rows[short_reference_column])

    short_multiplier = 1.0 - float(buy_short_buffer_pct) / 100.0
    long_multiplier = 1.0 - float(buy_long_buffer_pct) / 100.0
    rows["trigger_buy_sma30_98pct"] = _dynamic_scaled_sma_boundary(
        close, short_windows[1], short_multiplier
    )
    rows["trigger_buy_sma200_98pct"] = _dynamic_scaled_sma_boundary(
        close, long_window, long_multiplier
    )
    rows["trigger_buy_combined"] = rows[
        ["trigger_buy_sma30_98pct", "trigger_buy_sma200_98pct"]
    ].max(axis=1, skipna=False)
    rows["strategy_ready"] = rows[
        [*short_columns, long_column, "sma_short_avg", "trigger_buy_combined"]
    ].notna().all(axis=1)
    rows.attrs["entry_exit_parameters"] = {
        "short_windows": list(short_windows),
        "long_window": int(long_window),
        "buy_short_buffer_pct": float(buy_short_buffer_pct),
        "buy_long_buffer_pct": float(buy_long_buffer_pct),
        "r1_decline_days": int(r1_decline_days),
        "r1_min_daily_decline_pct": float(r1_min_daily_decline_pct),
        "r3_sell_buffer_pct": float(r3_sell_buffer_pct),
    }
    return rows


def analysis_slice(
    prepared: pd.DataFrame, *, start: pd.Timestamp | str, end: pd.Timestamp | str
) -> pd.DataFrame:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    result = prepared[
        prepared["strategy_ready"].astype(bool)
        & prepared["date"].between(start, end)
    ].copy()
    if result.empty:
        raise ValueError("Analysis window is empty after SMA warmup.")
    return result.reset_index(drop=True)


def active_sell_signals(row: pd.Series, spec: EntryExitAblationSpec) -> tuple[str, ...]:
    conditions = (
        bool(row["sma200_down_3d"]),
        bool(row["sma_short_avg_drop_gt_0_15pct_1d"]),
        bool(row["close_below_sma200"]),
        bool(row["close_below_sma30"]),
    )
    return tuple(
        rule
        for rule, enabled, condition in zip(SELL_RULES, spec.flags, conditions)
        if enabled and condition
    )


def intraday_buy_fill(
    *, open_: float, high: float, trigger_sma30: float, trigger_sma200: float
) -> tuple[float, Literal["open_gap", "intraday_trigger"], float] | None:
    trigger = max(float(trigger_sma30), float(trigger_sma200))
    if not np.isfinite(trigger) or trigger <= 0:
        return None
    if open_ >= trigger:
        return float(open_), "open_gap", trigger
    if high >= trigger > open_:
        return trigger, "intraday_trigger", trigger
    return None


def _records_frame(records: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def run_reference_entry_exit_ablation(
    data: pd.DataFrame,
    spec: EntryExitAblationSpec,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
) -> RegimeReferenceResult:
    rows = data.reset_index(drop=True)
    if rows.empty or not rows["strategy_ready"].astype(bool).all():
        raise ValueError("Reference data must be nonempty and warmup-complete.")
    symbol = str(rows.iloc[0]["symbol"])
    cash = float(initial_cash)
    shares = 0.0
    pending_sell: tuple[pd.Timestamp, tuple[str, ...]] | None = None
    short_cross_seen = False
    sma200_cross_seen = False
    buy_armed = False
    armed_date: pd.Timestamp | None = None
    armed_reason = ""
    open_trade: dict[str, object] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []

    def reset_entry_state() -> None:
        nonlocal short_cross_seen, sma200_cross_seen, buy_armed, armed_date, armed_reason
        short_cross_seen = False
        sma200_cross_seen = False
        buy_armed = False
        armed_date = None
        armed_reason = ""

    for index, row in rows.iterrows():
        date = pd.Timestamp(row["date"])
        executed = 0
        executed_reason = ""
        fill_source = ""

        if pending_sell is not None:
            signal_date, matched = pending_sell
            fill = float(row["open"])
            order_shares = shares
            cash = shares * fill
            if open_trade is None:
                raise AssertionError("Sell has no open trade.")
            primary = matched[0]
            trade_records.append(
                {
                    **open_trade,
                    "exit_signal_date": signal_date,
                    "exit_date": date,
                    "exit_price": fill,
                    "exit_reason": primary,
                    "exit_matched_signals": "|".join(matched),
                    "pnl": (fill - float(open_trade["entry_price"])) * order_shares,
                    "return_pct": (fill / float(open_trade["entry_price"]) - 1.0) * 100.0,
                }
            )
            order_records.append(
                {
                    "case_id": case_id,
                    "symbol": symbol,
                    "type": "sell",
                    "signal_date": signal_date,
                    "date": date,
                    "reason": primary,
                    "matched_signals": "|".join(matched),
                    "shares": order_shares,
                    "raw_price": fill,
                    "fill_price": fill,
                    "fill_source": "next_open",
                    "theoretical_trigger": fill,
                    "trigger_sma30_98pct": None,
                    "trigger_sma200_98pct": None,
                    "entry_arm_reason": None,
                    "entry_arm_date": None,
                }
            )
            shares = 0.0
            open_trade = None
            pending_sell = None
            executed = -1
            executed_reason = primary
            fill_source = "next_open"

        if shares == 0 and buy_armed:
            fill_event = intraday_buy_fill(
                open_=float(row["open"]),
                high=float(row["high"]),
                trigger_sma30=float(row["trigger_buy_sma30_98pct"]),
                trigger_sma200=float(row["trigger_buy_sma200_98pct"]),
            )
            if fill_event is not None:
                fill, fill_source, combined_trigger = fill_event
                shares = cash / fill
                cash = 0.0
                executed = 1
                executed_reason = BUY_ARMED_PRICE_FILTER
                open_trade = {
                    "case_id": case_id,
                    "symbol": symbol,
                    "entry_signal_date": armed_date,
                    "entry_date": date,
                    "entry_price": fill,
                    "shares": shares,
                    "entry_reason": BUY_ARMED_PRICE_FILTER,
                    "entry_arm_reason": armed_reason,
                }
                order_records.append(
                    {
                        "case_id": case_id,
                        "symbol": symbol,
                        "type": "buy",
                        "signal_date": armed_date,
                        "date": date,
                        "reason": BUY_ARMED_PRICE_FILTER,
                        "matched_signals": BUY_ARMED_PRICE_FILTER,
                        "shares": shares,
                        "raw_price": fill,
                        "fill_price": fill,
                        "fill_source": fill_source,
                        "theoretical_trigger": combined_trigger,
                        "trigger_sma30_98pct": float(row["trigger_buy_sma30_98pct"]),
                        "trigger_sma200_98pct": float(row["trigger_buy_sma200_98pct"]),
                        "entry_arm_reason": armed_reason,
                        "entry_arm_date": armed_date,
                    }
                )
                reset_entry_state()

        sell_signals = active_sell_signals(row, spec)
        signal = 0
        signal_reason = ""
        if sell_signals:
            reset_entry_state()
            if shares > 0 and index < len(rows) - 1:
                pending_sell = (date, sell_signals)
                signal = -1
                signal_reason = sell_signals[0]
        elif shares == 0:
            if bool(row["close_crossed_above_all_short_lines"]):
                short_cross_seen = True
            if bool(row["close_crossed_above_sma200"]):
                sma200_cross_seen = True
            route_a = short_cross_seen and bool(row["close_above_sma200"])
            route_b = sma200_cross_seen and bool(row["close_above_all_short_lines"])
            if route_a or route_b:
                if not buy_armed:
                    armed_date = date
                    if route_a and route_b:
                        armed_reason = BUY_ROUTE_BOTH
                    elif route_a:
                        armed_reason = BUY_ROUTE_SHORTS_THEN_SMA200
                    else:
                        armed_reason = BUY_ROUTE_SMA200_THEN_SHORTS
                buy_armed = True
                signal = 1
                signal_reason = armed_reason

        daily_records.append(
            {
                "case_id": case_id,
                "date": date,
                "symbol": symbol,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "short_cross_seen": int(short_cross_seen),
                "sma200_cross_seen": int(sma200_cross_seen),
                "buy_armed": int(buy_armed),
                "armed_reason": armed_reason,
                "sell_signals": "|".join(sell_signals),
                "signal": signal,
                "signal_reason": signal_reason,
                "executed": executed,
                "executed_reason": executed_reason,
                "fill_source": fill_source,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row["close"]),
                "is_long": int(shares > 0),
            }
        )

    order_columns = [
        "case_id", "symbol", "type", "signal_date", "date", "reason",
        "matched_signals", "shares", "raw_price", "fill_price", "fill_source",
        "theoretical_trigger", "trigger_sma30_98pct", "trigger_sma200_98pct",
        "entry_arm_reason", "entry_arm_date",
    ]
    trade_columns = [
        "case_id", "symbol", "entry_signal_date", "entry_date", "entry_price",
        "shares", "entry_reason", "entry_arm_reason", "exit_signal_date",
        "exit_date", "exit_price", "exit_reason", "exit_matched_signals", "pnl",
        "return_pct",
    ]
    return RegimeReferenceResult(
        daily=_records_frame(daily_records, list(daily_records[0]) if daily_records else []),
        orders=_records_frame(order_records, order_columns),
        trades=_records_frame(trade_records, trade_columns),
    )


def run_pybroker_compiled_entry_exit_ablation(
    data: pd.DataFrame,
    spec: EntryExitAblationSpec,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
):
    reference = run_reference_entry_exit_ablation(
        data, spec, initial_cash=initial_cash, case_id=case_id
    )
    if reference.orders.empty:
        raise ValueError("Formal case has no orders and cannot seed the PyBroker check.")
    bars = data.copy().reset_index(drop=True)
    first_date = pd.Timestamp(reference.orders.iloc[0]["date"])
    first_index = bars.index[bars["date"].eq(first_date)].tolist()[0]
    if first_index == 0:
        raise ValueError("Compiled PyBroker data needs one bar before the first order.")
    engine = bars.iloc[first_index - 1 :].copy().reset_index(drop=True)
    dates = [pd.Timestamp(value) for value in engine["date"]]
    index_by_date = {date: index for index, date in enumerate(dates)}
    signal_by_prior: dict[pd.Timestamp, tuple[str, float]] = {}
    for order in reference.orders.itertuples(index=False):
        fill_date = pd.Timestamp(order.date)
        fill_index = index_by_date[fill_date]
        if fill_index == 0:
            raise AssertionError("Compiled fill has no prior submission bar.")
        prior = dates[fill_index - 1]
        if prior in signal_by_prior:
            raise AssertionError("Two fills share one PyBroker submission bar.")
        signal_by_prior[prior] = (str(order.type), float(order.fill_price))

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
    strategy.add_execution(execute, str(engine.iloc[0]["symbol"]))
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    return result, reference, engine


def compiled_pybroker_daily_state(result, engine: pd.DataFrame) -> pd.DataFrame:
    state = result.portfolio.reset_index()[["date", "cash", "equity"]].copy()
    state["date"] = pd.to_datetime(state["date"])
    shares = pd.Series(0.0, index=state["date"])
    if not result.positions.empty:
        positions = result.positions.reset_index()
        grouped = positions.groupby("date")["long_shares"].sum().astype(float)
        shares.loc[pd.to_datetime(grouped.index)] = grouped.to_numpy()
    state["shares"] = shares.to_numpy(float)
    closes = engine.set_index(pd.to_datetime(engine["date"]))["close"].astype(float)
    state["close"] = state["date"].map(closes)
    state["equity"] = state["cash"].astype(float) + state["shares"] * state["close"]
    state["is_long"] = state["shares"].gt(0).astype(int)
    return state[["date", "cash", "equity", "shares", "is_long", "close"]]
