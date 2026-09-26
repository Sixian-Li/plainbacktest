"""Two-measure SMA regime ablation with an intraday drawdown lockout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from quantkit.sma_regime import (
    CONDITION_PRICE_ABOVE_SMA200,
    CONDITION_SMA_ORDERING,
    RegimeReferenceResult,
)


CONDITION_SHORT_SMAS_RISING_1D = "sma25_sma30_sma35_all_rising_1d"
ENTRY_MAIN_CONDITIONS = "ENTRY_MAIN_CONDITIONS"
EXIT_MAIN_CONDITIONS = "EXIT_MAIN_CONDITIONS"
EXIT_INTRADAY_DRAWDOWN = "EXIT_INTRADAY_DRAWDOWN_3PCT"
REENTRY_RECOVER_SELL_PRICE = "REENTRY_RECOVER_SELL_PRICE"
REENTRY_SMA200_RESET = "REENTRY_SMA200_RESET"
CASE_FLAGS: dict[str, tuple[bool, bool]] = {
    "base": (False, False),
    "measure_1": (True, False),
    "measure_2": (False, True),
    "measures_1_2": (True, True),
}


@dataclass(frozen=True)
class ReentryAblationSpec:
    measure_1: bool
    measure_2: bool
    drawdown_stop_pct: float = 3.0
    deep_reset_pct_below_sma200: float = 5.0

    def __post_init__(self) -> None:
        for name, value in (
            ("drawdown_stop_pct", self.drawdown_stop_pct),
            ("deep_reset_pct_below_sma200", self.deep_reset_pct_below_sma200),
        ):
            if not np.isfinite(value) or not 0 < value < 100:
                raise ValueError(f"{name} must be finite and strictly between 0 and 100.")

    @classmethod
    def from_case_id(cls, case_id: str) -> "ReentryAblationSpec":
        try:
            measure_1, measure_2 = CASE_FLAGS[case_id]
        except KeyError as exc:
            raise ValueError(f"Unknown ablation case: {case_id!r}") from exc
        return cls(measure_1=measure_1, measure_2=measure_2)


def prepare_reentry_ablation_data(data: pd.DataFrame) -> pd.DataFrame:
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
    close = rows["close"].astype(float)
    for window in (25, 30, 35, 200, 250, 300):
        rows[f"sma{window}"] = close.rolling(window, min_periods=window).mean()
    rows[CONDITION_PRICE_ABOVE_SMA200] = close > rows["sma200"]
    rows[CONDITION_SMA_ORDERING] = (
        (rows["sma200"] > rows["sma250"]) & (rows["sma250"] > rows["sma300"])
    )
    rows[CONDITION_SHORT_SMAS_RISING_1D] = np.logical_and.reduce(
        [rows[f"sma{window}"].diff().gt(0) for window in (25, 30, 35)]
    )
    rows["regime_ready"] = rows[
        ["sma25", "sma30", "sma35", "sma200", "sma250", "sma300"]
    ].notna().all(axis=1)
    return rows


def main_conditions(row: pd.Series, spec: ReentryAblationSpec) -> bool:
    eligible = bool(row[CONDITION_PRICE_ABOVE_SMA200]) and bool(row[CONDITION_SMA_ORDERING])
    if spec.measure_1:
        eligible = eligible and bool(row[CONDITION_SHORT_SMAS_RISING_1D])
    return eligible


def intraday_stop_fill(
    *,
    open_: float,
    low: float,
    known_peak: float,
    stop_pct: float,
) -> tuple[float, Literal["open_gap", "intraday_trigger"]] | None:
    trigger = known_peak * (1.0 - stop_pct / 100.0)
    if open_ <= trigger:
        return float(open_), "open_gap"
    if low <= trigger < open_:
        return float(trigger), "intraday_trigger"
    return None


def advance_reset_stage(
    stage: int,
    *,
    previous_close: float | None,
    previous_sma200: float | None,
    close: float,
    sma200: float,
    deep_pct: float,
) -> int:
    """Advance only on completed closes: below, deep-touch, then upward cross."""
    below = close < sma200
    deep = close <= sma200 * (1.0 - deep_pct / 100.0)
    upward_cross = (
        previous_close is not None
        and previous_sma200 is not None
        and previous_close <= previous_sma200
        and close > sma200
    )
    if stage == 0 and below:
        stage = 1
    if stage == 1 and deep:
        stage = 2
    elif stage == 2 and upward_cross:
        stage = 3
    return stage


def analysis_slice(
    prepared: pd.DataFrame,
    *,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
) -> pd.DataFrame:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    result = prepared[
        prepared["regime_ready"].astype(bool)
        & (prepared["date"] >= start)
        & (prepared["date"] <= end)
    ].copy()
    if result.empty:
        raise ValueError("Analysis window is empty after SMA warmup.")
    return result.reset_index(drop=True)


def _records_frame(records: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def run_reference_reentry_ablation(
    data: pd.DataFrame,
    spec: ReentryAblationSpec,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
) -> RegimeReferenceResult:
    rows = data.reset_index(drop=True)
    if rows.empty or not rows["regime_ready"].astype(bool).all():
        raise ValueError("Reference data must be nonempty and warmup-complete.")
    symbol = str(rows.iloc[0]["symbol"])
    cash = float(initial_cash)
    shares = 0.0
    known_peak: float | None = None
    locked_sell_price: float | None = None
    reset_stage = 0
    pending: tuple[Literal["buy", "sell"], pd.Timestamp, str] | None = None
    previous_close: float | None = None
    previous_sma200: float | None = None
    open_trade: dict[str, object] | None = None
    daily_records: list[dict[str, object]] = []
    order_records: list[dict[str, object]] = []
    trade_records: list[dict[str, object]] = []

    for index, row in rows.iterrows():
        date = pd.Timestamp(row["date"])
        executed = 0
        executed_reason = ""
        fill_source = ""
        if pending is not None:
            side, signal_date, reason = pending
            fill = float(row["open"])
            if side == "buy":
                shares = cash / fill
                cash = 0.0
                known_peak = fill
                locked_sell_price = None
                reset_stage = 0
                order_shares = shares
                executed = 1
                open_trade = {
                    "case_id": case_id, "symbol": symbol, "entry_signal_date": signal_date,
                    "entry_date": date, "entry_price": fill, "shares": shares,
                    "entry_reason": reason,
                }
            else:
                order_shares = shares
                cash = shares * fill
                if open_trade is None:
                    raise AssertionError("Main-condition sell has no open trade.")
                trade_records.append(
                    {
                        **open_trade, "exit_signal_date": signal_date, "exit_date": date,
                        "exit_price": fill, "exit_reason": reason,
                        "pnl": (fill - float(open_trade["entry_price"])) * order_shares,
                        "return_pct": (fill / float(open_trade["entry_price"]) - 1.0) * 100.0,
                    }
                )
                shares = 0.0
                known_peak = None
                open_trade = None
                # Main-condition exits do not activate measure 2's special
                # lockout; only a 3% intraday drawdown exit does.
                locked_sell_price = None
                reset_stage = 0
                executed = -1
            executed_reason = reason
            fill_source = "next_open"
            order_records.append(
                {
                    "case_id": case_id, "symbol": symbol, "type": side,
                    "signal_date": signal_date, "date": date, "reason": reason,
                    "shares": order_shares, "raw_price": fill, "fill_price": fill,
                    "fill_source": fill_source, "theoretical_trigger": fill,
                    "known_peak_before": None, "locked_sell_price_after": locked_sell_price,
                    "reset_stage_after": reset_stage,
                }
            )
            pending = None

        # The predeclared stop for today's session uses only the entry fill and
        # completed closes observed before today's open. Today's high cannot
        # lift the trigger and today's close is added only after intraday fills.
        stop_fill = None
        peak_before = known_peak
        if shares > 0 and spec.measure_2:
            if known_peak is None:
                raise AssertionError("Long state lost its known peak.")
            stop_fill = intraday_stop_fill(
                open_=float(row["open"]), low=float(row["low"]), known_peak=known_peak,
                stop_pct=spec.drawdown_stop_pct,
            )
        if stop_fill is not None:
            fill, fill_source = stop_fill
            order_shares = shares
            cash = shares * fill
            locked_sell_price = fill
            shares = 0.0
            known_peak = None
            reset_stage = 0
            executed = -1
            executed_reason = EXIT_INTRADAY_DRAWDOWN
            if open_trade is None:
                raise AssertionError("Stop sell has no open trade.")
            trade_records.append(
                {
                    **open_trade, "exit_signal_date": date, "exit_date": date,
                    "exit_price": fill, "exit_reason": EXIT_INTRADAY_DRAWDOWN,
                    "pnl": (fill - float(open_trade["entry_price"])) * order_shares,
                    "return_pct": (fill / float(open_trade["entry_price"]) - 1.0) * 100.0,
                }
            )
            open_trade = None
            order_records.append(
                {
                    "case_id": case_id, "symbol": symbol, "type": "sell",
                    "signal_date": date, "date": date, "reason": EXIT_INTRADAY_DRAWDOWN,
                    "shares": order_shares, "raw_price": fill, "fill_price": fill,
                    "fill_source": fill_source,
                    "theoretical_trigger": float(peak_before) * (1.0 - spec.drawdown_stop_pct / 100.0),
                    "known_peak_before": peak_before,
                    "locked_sell_price_after": locked_sell_price,
                    "reset_stage_after": reset_stage,
                }
            )

        close = float(row["close"])
        sma200 = float(row["sma200"])
        eligible = main_conditions(row, spec)
        if shares > 0:
            if known_peak is None:
                raise AssertionError("Long state lost its known peak.")
            known_peak = max(known_peak, close)
        elif locked_sell_price is not None:
            reset_stage = advance_reset_stage(
                reset_stage,
                previous_close=previous_close,
                previous_sma200=previous_sma200,
                close=close,
                sma200=sma200,
                deep_pct=spec.deep_reset_pct_below_sma200,
            )

        signal = 0
        signal_reason = ""
        if index < len(rows) - 1:
            if shares > 0 and not eligible:
                signal = -1
                signal_reason = EXIT_MAIN_CONDITIONS
                pending = ("sell", date, EXIT_MAIN_CONDITIONS)
            if shares == 0 and eligible:
                if locked_sell_price is None:
                    pending = ("buy", date, ENTRY_MAIN_CONDITIONS)
                    signal = 1
                    signal_reason = ENTRY_MAIN_CONDITIONS
                elif close >= locked_sell_price:
                    pending = ("buy", date, REENTRY_RECOVER_SELL_PRICE)
                    signal = 1
                    signal_reason = REENTRY_RECOVER_SELL_PRICE
                elif reset_stage >= 3:
                    pending = ("buy", date, REENTRY_SMA200_RESET)
                    signal = 1
                    signal_reason = REENTRY_SMA200_RESET

        daily_records.append(
            {
                "case_id": case_id, "date": date, "symbol": symbol,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": close, "eligible": int(eligible),
                "known_peak": known_peak, "locked_sell_price": locked_sell_price,
                "reset_stage": reset_stage, "signal": signal, "signal_reason": signal_reason,
                "executed": executed, "executed_reason": executed_reason,
                "cash": cash, "shares": shares, "equity": cash + shares * close,
                "is_long": int(shares > 0),
            }
        )
        previous_close = close
        previous_sma200 = sma200

    order_columns = [
        "case_id", "symbol", "type", "signal_date", "date", "reason", "shares",
        "raw_price", "fill_price", "fill_source", "theoretical_trigger",
        "known_peak_before", "locked_sell_price_after", "reset_stage_after",
    ]
    trade_columns = [
        "case_id", "symbol", "entry_signal_date", "entry_date", "entry_price", "shares",
        "entry_reason", "exit_signal_date", "exit_date", "exit_price", "exit_reason",
        "pnl", "return_pct",
    ]
    return RegimeReferenceResult(
        daily=_records_frame(daily_records, list(daily_records[0]) if daily_records else []),
        orders=_records_frame(order_records, order_columns),
        trades=_records_frame(trade_records, trade_columns),
    )


def compile_execution_bars(
    data: pd.DataFrame,
    spec: ReentryAblationSpec,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
) -> tuple[RegimeReferenceResult, pd.DataFrame]:
    """Compile the independent state machine into explicit per-day fills.

    PyBroker then executes only these already-computed explicit prices. This is
    a genuinely separate portfolio engine check: the reference owns strategy
    state and expected cash/shares, while PyBroker owns its own portfolio.
    """
    reference = run_reference_reentry_ablation(
        data, spec, initial_cash=initial_cash, case_id=case_id
    )
    bars = data.copy().reset_index(drop=True)
    return reference, bars


def run_pybroker_compiled_reentry_ablation(
    data: pd.DataFrame,
    spec: ReentryAblationSpec,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
):
    reference, bars = compile_execution_bars(
        data, spec, initial_cash=initial_cash, case_id=case_id
    )
    if reference.orders.empty:
        raise ValueError("Formal case has no orders and cannot seed the compiled PyBroker check.")
    first = reference.orders.iloc[0]
    if first["type"] != "buy":
        raise AssertionError("First formal order must be a buy.")
    first_date = pd.Timestamp(first["date"])
    first_index = bars.index[bars["date"] == first_date].tolist()[0]
    if first_index == 0:
        raise ValueError("Compiled PyBroker data needs one bar before the first buy.")
    engine = bars.iloc[first_index - 1 :].copy().reset_index(drop=True)
    # PyBroker supports at most one order per symbol/session. A next-Open buy
    # can legitimately hit the already-known 3% intraday stop later that same
    # session. Insert an engine-only synthetic bar at the same price immediately
    # before the real bar, so both fills remain ordered and independently
    # reconciled without changing the canonical reference ledger.
    from datetime import timedelta

    reference_orders = reference.orders.reset_index(drop=True).copy()
    duplicate_dates = set(
        pd.to_datetime(reference_orders.loc[reference_orders.duplicated("date", keep=False), "date"])
    )
    expanded: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    order_groups = {
        pd.Timestamp(date): group.to_dict("records")
        for date, group in reference_orders.groupby(pd.to_datetime(reference_orders["date"]), sort=False)
    }
    for row in engine.to_dict("records"):
        date = pd.Timestamp(row["date"])
        day_orders = order_groups.get(date, [])
        if len(day_orders) == 2:
            first_order = day_orders[0]
            synthetic = dict(row)
            synthetic_date = date - timedelta(hours=1)
            synthetic["date"] = synthetic_date
            synthetic_price = float(first_order["fill_price"])
            for field in ("open", "high", "low", "close"):
                synthetic[field] = synthetic_price
            expanded.append(synthetic)
            expanded.append(row)
            first_order = dict(first_order)
            first_order["engine_date"] = synthetic_date
            order_rows.append(first_order)
            second_order = dict(day_orders[1])
            second_order["engine_date"] = date
            order_rows.append(second_order)
        else:
            expanded.append(row)
            for order in day_orders:
                order = dict(order)
                order["engine_date"] = date
                order_rows.append(order)
    if not duplicate_dates:
        order_rows = [
            {**order, "engine_date": pd.Timestamp(order["date"])}
            for order in reference_orders.to_dict("records")
        ]
    engine = pd.DataFrame(expanded).sort_values("date").reset_index(drop=True)
    dates = engine["date"].tolist()
    index_by_date = {pd.Timestamp(date): index for index, date in enumerate(dates)}
    signal_by_prior: dict[pd.Timestamp, tuple[str, float]] = {}
    for order in order_rows:
        fill_date = pd.Timestamp(order["engine_date"])
        fill_index = index_by_date[fill_date]
        if fill_index == 0:
            raise AssertionError("Compiled fill has no prior submission bar.")
        prior = pd.Timestamp(dates[fill_index - 1])
        if prior in signal_by_prior:
            raise AssertionError("Compiled fills share one PyBroker submission bar.")
        signal_by_prior[prior] = (str(order["type"]), float(order["fill_price"]))

    from decimal import Decimal
    import pybroker
    from pybroker import PositionMode, PriceType, Strategy, StrategyConfig
    from pybroker.context import ExecContext
    from quantkit.execution import MAX_AFFORDABLE_REQUEST

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
        initial_cash=initial_cash, fee_mode=None, fee_amount=0,
        enable_fractional_shares=True, round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY, max_long_positions=1,
        buy_delay=1, sell_delay=1, exit_on_last_bar=False,
        exit_cover_fill_price=PriceType.OPEN, exit_sell_fill_price=PriceType.OPEN,
        bars_per_year=252, return_signals=False, round_test_result=False,
    )
    strategy = Strategy(
        engine, pd.Timestamp(engine["date"].min()).isoformat(),
        pd.Timestamp(engine["date"].max()).isoformat(), config,
    )
    strategy.add_execution(execute, str(engine.iloc[0]["symbol"]))
    pybroker_result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    # Map engine-only timestamps back to canonical fill dates for cross-checks.
    if not pybroker_result.orders.empty:
        mapping = {pd.Timestamp(order["engine_date"]): pd.Timestamp(order["date"]) for order in order_rows}
        pybroker_result.orders.loc[:, "date"] = pd.to_datetime(pybroker_result.orders["date"]).map(mapping)
    if not pybroker_result.trades.empty:
        mapping = {pd.Timestamp(order["engine_date"]): pd.Timestamp(order["date"]) for order in order_rows}
        for column in ("entry_date", "exit_date"):
            pybroker_result.trades.loc[:, column] = pd.to_datetime(pybroker_result.trades[column]).map(mapping)
    return pybroker_result, reference, engine


def compiled_pybroker_daily_state(
    pybroker_result,
    engine: pd.DataFrame,
    canonical: pd.DataFrame,
) -> pd.DataFrame:
    """Collapse engine-only intraday split bars back to canonical daily state."""
    state = pybroker_result.portfolio.reset_index()[["date", "cash", "equity"]].copy()
    state["engine_date"] = pd.to_datetime(state["date"])
    synthetic_dates = pd.to_datetime(engine.loc[engine["date"].dt.hour.ne(0), "date"])
    synthetic_set = set(synthetic_dates)
    state["date"] = state["engine_date"].apply(
        lambda value: value.normalize() + pd.Timedelta(days=1)
        if value in synthetic_set else value.normalize()
    )
    state = state.sort_values("engine_date").groupby("date", as_index=False).tail(1)
    shares_by_engine: dict[pd.Timestamp, float] = {}
    if not pybroker_result.positions.empty:
        positions = pybroker_result.positions.reset_index()
        shares_by_engine = {
            pd.Timestamp(date): float(value)
            for date, value in positions.groupby("date")["long_shares"].sum().items()
        }
    state["shares"] = state["engine_date"].map(shares_by_engine).fillna(0.0)
    closes = canonical.copy()
    closes["date"] = pd.to_datetime(closes["date"]).dt.normalize()
    close_by_date = closes.set_index("date")["close"].astype(float)
    state["close"] = state["date"].map(close_by_date)
    state["equity"] = state["cash"].astype(float) + state["shares"] * state["close"]
    state["is_long"] = (state["shares"] > 0).astype(int)
    return state[["date", "cash", "equity", "shares", "is_long", "close"]].reset_index(drop=True)
