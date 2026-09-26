"""QQQ full-position trend-quality strategy and independent reference ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd
import pybroker
from pybroker import Strategy
from pybroker.context import ExecContext

from quantkit.execution import (
    ExplicitFillPolicy,
    assert_orders_match_policy,
    make_strategy_config,
    request_all_affordable_shares,
    request_sell_all,
)
from quantkit.metrics import calculate_metrics


F2_LONG_SMA_SLOPE = "F2_LONG_SMA_SLOPE"
F4_SHORT_TREND_QUALITY = "F4_SHORT_TREND_QUALITY"
F5_QQQ_SPY_RELATIVE_STRENGTH = "F5_QQQ_SPY_RELATIVE_STRENGTH"
ALL_FACTORS = (F2_LONG_SMA_SLOPE, F4_SHORT_TREND_QUALITY, F5_QQQ_SPY_RELATIVE_STRENGTH)
CASE_FACTORS: dict[str, tuple[str, ...]] = {
    "B0": (),
    "B2": (F2_LONG_SMA_SLOPE,),
    "B4": (F4_SHORT_TREND_QUALITY,),
    "P24": (F2_LONG_SMA_SLOPE, F4_SHORT_TREND_QUALITY),
    "P245": (F2_LONG_SMA_SLOPE, F4_SHORT_TREND_QUALITY, F5_QQQ_SPY_RELATIVE_STRENGTH),
}
PARAMETER_COLUMNS = (
    "long_sma_window",
    "long_slope_lookback",
    "long_slope_threshold_daily_pct",
    "short_sma_window",
    "short_regression_window",
    "short_quality_threshold_daily_pct",
    "entry_confirmation_sessions",
    "relative_strength_lookback",
)


@dataclass(frozen=True)
class TrendQualityParameters:
    long_sma_window: int = 200
    long_slope_lookback: int = 20
    long_slope_threshold_daily_pct: float = 0.0
    short_sma_window: int = 30
    short_regression_window: int = 10
    short_quality_threshold_daily_pct: float = 0.0
    entry_confirmation_sessions: int = 1
    relative_strength_lookback: int = 60

    def __post_init__(self) -> None:
        if self.long_sma_window < 2 or self.short_sma_window < 2:
            raise ValueError("SMA windows must be at least 2.")
        if self.long_slope_lookback < 1 or self.relative_strength_lookback < 1:
            raise ValueError("Lookbacks must be positive.")
        if self.short_regression_window < 2:
            raise ValueError("The short regression window must be at least 2.")
        if self.entry_confirmation_sessions < 1:
            raise ValueError("Entry confirmation must be positive.")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "TrendQualityParameters":
        return cls(
            long_sma_window=int(values["long_sma_window"]),
            long_slope_lookback=int(values["long_slope_lookback"]),
            long_slope_threshold_daily_pct=float(values["long_slope_threshold_daily_pct"]),
            short_sma_window=int(values["short_sma_window"]),
            short_regression_window=int(values["short_regression_window"]),
            short_quality_threshold_daily_pct=float(values["short_quality_threshold_daily_pct"]),
            entry_confirmation_sessions=int(values["entry_confirmation_sessions"]),
            relative_strength_lookback=int(values["relative_strength_lookback"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendQualityReferenceResult:
    daily: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame


def rolling_log_slope_r2(values: pd.Series, window: int) -> pd.Series:
    """OLS daily beta times R-squared for completed log values."""
    if window < 2:
        raise ValueError("window must be at least 2")
    raw = np.asarray(values, dtype=float)
    result = np.full(len(raw), np.nan, dtype=float)
    if len(raw) < window:
        return pd.Series(result, index=values.index, name=values.name)
    windows = np.lib.stride_tricks.sliding_window_view(raw, window)
    valid = np.isfinite(windows).all(axis=1) & (windows > 0).all(axis=1)
    if valid.any():
        y = np.log(windows[valid])
        x = np.arange(window, dtype=float)
        x_centered = x - x.mean()
        y_centered = y - y.mean(axis=1, keepdims=True)
        covariance = y_centered @ x_centered
        ssx = float(x_centered @ x_centered)
        ssy = np.square(y_centered).sum(axis=1)
        beta = covariance / ssx
        r_squared = np.divide(
            np.square(covariance),
            ssx * ssy,
            out=np.zeros_like(covariance),
            where=ssy > np.finfo(float).eps,
        )
        scores = beta * r_squared * 100.0
        output_indices = np.flatnonzero(valid) + window - 1
        result[output_indices] = scores
    return pd.Series(result, index=values.index, name=values.name)


def _canonical_symbol(data: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing canonical columns: {sorted(missing)}")
    result = data[data["symbol"].eq(symbol)].copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values("date", kind="stable").reset_index(drop=True)
    if result.empty:
        raise ValueError(f"No {symbol} rows are available.")
    if result["date"].duplicated().any():
        raise ValueError(f"Duplicate {symbol} dates are not allowed.")
    return result


def prepare_common_market_data(qqq: pd.DataFrame, spy: pd.DataFrame) -> pd.DataFrame:
    """Inner-join approved QQQ OHLCV with SPY close on completed sessions."""
    q = _canonical_symbol(qqq, "QQQ")
    s = _canonical_symbol(spy, "SPY")[["date", "close"]].rename(columns={"close": "spy_close"})
    result = q.merge(s, on="date", how="inner", validate="one_to_one")
    if result.empty:
        raise ValueError("QQQ and SPY have no common sessions.")
    result = result.sort_values("date", kind="stable").reset_index(drop=True)
    result["relative_price"] = result["close"].astype(float) / result["spy_close"].astype(float)
    return result


def shared_analysis_slice(
    common: pd.DataFrame,
    *,
    requested_start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    maximum_long_sma_window: int,
    maximum_long_slope_lookback: int,
    maximum_short_sma_window: int,
    maximum_short_regression_window: int,
    maximum_relative_strength_lookback: int,
) -> pd.DataFrame:
    """Use one conservative warmup boundary for every parameter case."""
    first_ready_position = max(
        maximum_long_sma_window + maximum_long_slope_lookback - 1,
        maximum_short_sma_window + maximum_short_regression_window - 2,
        maximum_relative_strength_lookback,
    )
    positions = np.arange(len(common))
    dates = pd.to_datetime(common["date"])
    mask = (
        (positions >= first_ready_position)
        & (dates >= pd.Timestamp(requested_start))
        & (dates <= pd.Timestamp(end))
    )
    result = common.loc[mask].copy().reset_index(drop=True)
    if result.empty:
        raise ValueError("No rows remain after the shared warmup and requested window.")
    return result


class IndicatorRepository:
    """Caches causal indicator arrays for a fixed common QQQ/SPY calendar."""

    def __init__(self, common: pd.DataFrame):
        self.common = common.copy().reset_index(drop=True)
        self._long_sma: dict[int, pd.Series] = {}
        self._long_slope: dict[tuple[int, int], pd.Series] = {}
        self._short_sma: dict[int, pd.Series] = {}
        self._short_quality: dict[tuple[int, int], pd.Series] = {}
        self._relative_strength: dict[int, pd.Series] = {}

    def long_sma(self, window: int) -> pd.Series:
        if window not in self._long_sma:
            self._long_sma[window] = self.common["close"].rolling(window, min_periods=window).mean()
        return self._long_sma[window]

    def long_slope(self, window: int, lookback: int) -> pd.Series:
        key = (window, lookback)
        if key not in self._long_slope:
            sma = self.long_sma(window)
            self._long_slope[key] = (sma / sma.shift(lookback) - 1.0) / lookback * 100.0
        return self._long_slope[key]

    def short_sma(self, window: int) -> pd.Series:
        if window not in self._short_sma:
            self._short_sma[window] = self.common["close"].rolling(window, min_periods=window).mean()
        return self._short_sma[window]

    def short_quality(self, window: int, regression_window: int) -> pd.Series:
        key = (window, regression_window)
        if key not in self._short_quality:
            self._short_quality[key] = rolling_log_slope_r2(
                self.short_sma(window), regression_window
            )
        return self._short_quality[key]

    def relative_strength(self, lookback: int) -> pd.Series:
        if lookback not in self._relative_strength:
            relative = self.common["relative_price"].astype(float)
            self._relative_strength[lookback] = np.log(relative / relative.shift(lookback))
        return self._relative_strength[lookback]

    def case_frame(
        self,
        parameters: TrendQualityParameters,
        case_name: str,
        analysis_dates: Iterable[pd.Timestamp],
    ) -> pd.DataFrame:
        if case_name not in CASE_FACTORS:
            raise ValueError(f"Unknown case: {case_name!r}")
        enabled = CASE_FACTORS[case_name]
        long_sma = self.long_sma(parameters.long_sma_window)
        long_slope = self.long_slope(
            parameters.long_sma_window, parameters.long_slope_lookback
        )
        short_sma = self.short_sma(parameters.short_sma_window)
        short_quality = self.short_quality(
            parameters.short_sma_window, parameters.short_regression_window
        )
        relative_strength = self.relative_strength(parameters.relative_strength_lookback)
        result = self.common.copy()
        result["long_sma"] = long_sma
        result["long_slope_daily_pct"] = long_slope
        result["short_sma"] = short_sma
        result["short_quality_daily_pct"] = short_quality
        result["relative_strength_log_return"] = relative_strength
        result["price_condition"] = result["close"] > result["long_sma"]
        result["f2_condition"] = (
            result["long_slope_daily_pct"] > parameters.long_slope_threshold_daily_pct
        )
        result["f4_condition"] = (
            result["short_quality_daily_pct"] > parameters.short_quality_threshold_daily_pct
        )
        result["f5_condition"] = result["relative_strength_log_return"] > 0.0
        eligible = result["price_condition"].astype(bool)
        if F2_LONG_SMA_SLOPE in enabled:
            eligible &= result["f2_condition"].astype(bool)
        if F4_SHORT_TREND_QUALITY in enabled:
            eligible &= result["f4_condition"].astype(bool)
        if F5_QQQ_SPY_RELATIVE_STRENGTH in enabled:
            eligible &= result["f5_condition"].astype(bool)
        result["eligible"] = eligible.fillna(False)
        requested = pd.DatetimeIndex(pd.to_datetime(list(analysis_dates)))
        result = result[result["date"].isin(requested)].copy().reset_index(drop=True)
        if len(result) != len(requested) or not pd.DatetimeIndex(result["date"]).equals(requested):
            raise ValueError("Analysis dates must be an ordered subset of the common calendar.")
        result["target_long"] = build_target_long(
            result["eligible"].to_numpy(bool), parameters.entry_confirmation_sessions
        )
        return result


def build_target_long(eligible: np.ndarray, confirmation_sessions: int) -> np.ndarray:
    """Convert causal eligibility into close-time desired state."""
    if confirmation_sessions < 1:
        raise ValueError("confirmation_sessions must be positive")
    target = np.zeros(len(eligible), dtype=bool)
    desired = False
    streak = 0
    for index, is_eligible in enumerate(np.asarray(eligible, dtype=bool)):
        if is_eligible:
            streak += 1
        else:
            streak = 0
        if desired and not is_eligible:
            desired = False
        elif not desired and streak >= confirmation_sessions:
            desired = True
        target[index] = desired
    return target


def make_execution(policy: ExplicitFillPolicy) -> Callable[[ExecContext], None]:
    def execute(ctx: ExecContext) -> None:
        target_long = bool(ctx.target_long[-1])
        if ctx.long_pos() is None and target_long:
            request_all_affordable_shares(ctx, policy)
        elif ctx.long_pos() is not None and not target_long:
            request_sell_all(ctx, policy)

    return execute


def run_pybroker_trend_quality(
    data: pd.DataFrame,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
):
    if data.empty or len(data["symbol"].drop_duplicates()) != 1:
        raise ValueError("Trend-quality runner requires one non-empty QQQ frame.")
    pybroker.disable_logging()
    pybroker.disable_progress_bar()
    pybroker.register_columns("target_long")
    config = make_strategy_config(policy, initial_cash=initial_cash)
    start = pd.Timestamp(data["date"].min()).strftime("%Y-%m-%d")
    end = pd.Timestamp(data["date"].max()).strftime("%Y-%m-%d")
    strategy = Strategy(data, start, end, config)
    strategy.add_execution(make_execution(policy), "QQQ")
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    assert_orders_match_policy(result.orders, data, policy)
    return result


def run_reference_trend_quality(
    data: pd.DataFrame,
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
    case_id: str = "case",
) -> TrendQualityReferenceResult:
    if data.empty or data["symbol"].drop_duplicates().tolist() != ["QQQ"]:
        raise ValueError("Reference trend-quality runner requires one non-empty QQQ frame.")
    rows = data.sort_values("date", kind="stable").reset_index(drop=True)
    cash = float(initial_cash)
    shares = 0.0
    pending: tuple[str, pd.Timestamp, str] | None = None
    open_trade: dict[str, Any] | None = None
    daily_records: list[dict[str, Any]] = []
    order_records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []
    previous_target = False
    for index, row in enumerate(rows.itertuples(index=False)):
        date = pd.Timestamp(row.date)
        executed = 0
        if pending is not None:
            side, signal_date, reason = pending
            raw_price = float(getattr(row, policy.timing))
            fill_price = policy.expected_fill(side, raw_price)
            if side == "buy":
                order_shares = cash / fill_price
                shares = order_shares
                cash = 0.0
                executed = 1
                open_trade = {
                    "case_id": case_id,
                    "symbol": "QQQ",
                    "entry_signal_date": signal_date,
                    "entry_date": date,
                    "entry_price": fill_price,
                    "shares": shares,
                }
            else:
                order_shares = shares
                cash = shares * fill_price
                executed = -1
                if open_trade is None:
                    raise AssertionError("Sell executed without an open reference trade.")
                trade_records.append(
                    {
                        **open_trade,
                        "exit_signal_date": signal_date,
                        "exit_date": date,
                        "exit_price": fill_price,
                        "pnl": (fill_price - float(open_trade["entry_price"])) * shares,
                        "return_pct": (fill_price / float(open_trade["entry_price"]) - 1.0) * 100.0,
                    }
                )
                shares = 0.0
                open_trade = None
            order_records.append(
                {
                    "case_id": case_id,
                    "symbol": "QQQ",
                    "type": side,
                    "signal_date": signal_date,
                    "date": date,
                    "reason": reason,
                    "shares": order_shares,
                    "raw_price": raw_price,
                    "fill_price": fill_price,
                    "implicit_cost": abs(fill_price - raw_price) * order_shares,
                }
            )
            pending = None
        target = bool(row.target_long)
        signal = 0
        signal_reason = ""
        if index < len(rows) - 1 and target != previous_target:
            if target:
                pending = ("buy", date, "ALL_ENABLED_STATES_TRUE")
                signal = 1
            else:
                pending = ("sell", date, "ANY_ENABLED_STATE_FALSE")
                signal = -1
            signal_reason = pending[2]
        daily_records.append(
            {
                "case_id": case_id,
                "date": date,
                "symbol": "QQQ",
                "close": float(row.close),
                "eligible": int(bool(getattr(row, "eligible", target))),
                "target_long": int(target),
                "signal": signal,
                "signal_reason": signal_reason,
                "executed": executed,
                "cash": cash,
                "shares": shares,
                "equity": cash + shares * float(row.close),
                "is_long": int(shares > 0),
            }
        )
        previous_target = target
    order_columns = [
        "case_id", "symbol", "type", "signal_date", "date", "reason",
        "shares", "raw_price", "fill_price", "implicit_cost",
    ]
    trade_columns = [
        "case_id", "symbol", "entry_signal_date", "entry_date", "entry_price", "shares",
        "exit_signal_date", "exit_date", "exit_price", "pnl", "return_pct",
    ]
    return TrendQualityReferenceResult(
        daily=pd.DataFrame(daily_records),
        orders=pd.DataFrame(order_records, columns=order_columns),
        trades=pd.DataFrame(trade_records, columns=trade_columns),
    )


def extended_metrics(
    reference: TrendQualityReferenceResult,
    *,
    initial_cash: float,
    subwindows: Iterable[Mapping[str, str]],
) -> dict[str, Any]:
    metrics = calculate_metrics(
        reference.daily, reference.orders, reference.trades, initial_cash=initial_cash
    )
    daily = reference.daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date", kind="stable").reset_index(drop=True)
    equity = daily["equity"].astype(float)
    drawdown_pct = (equity / equity.cummax() - 1.0) * 100.0
    metrics["ulcer_index_pct"] = float(np.sqrt(np.mean(np.square(drawdown_pct))))
    rolling = (equity / equity.shift(63) - 1.0) * 100.0
    metrics["worst_63_session_return_pct"] = float(rolling.min()) if rolling.notna().any() else float("nan")
    monthly = daily.set_index("date")["equity"].resample("ME").last()
    monthly_returns = monthly.pct_change()
    if len(monthly_returns):
        monthly_returns.iloc[0] = monthly.iloc[0] / initial_cash - 1.0
    metrics["losing_month_pct"] = float(monthly_returns.lt(0).mean() * 100.0)
    metrics["losing_month_avoidance_pct"] = 100.0 - metrics["losing_month_pct"]
    worst_drawdown = float("inf")
    for item in subwindows:
        window_id = str(item["window_id"])
        mask = daily["date"].between(pd.Timestamp(item["start"]), pd.Timestamp(item["end"]))
        frame = daily.loc[mask]
        if frame.empty:
            sub_drawdown = float("nan")
            exposure = 0.0
        else:
            sub_equity = frame["equity"].astype(float)
            sub_drawdown = float((sub_equity / sub_equity.cummax() - 1.0).min() * 100.0)
            exposure = float(frame["is_long"].astype(float).mean() * 100.0)
            worst_drawdown = min(worst_drawdown, sub_drawdown)
        metrics[f"{window_id}_max_drawdown_pct"] = sub_drawdown
        metrics[f"{window_id}_exposure_pct"] = exposure
    metrics["worst_subwindow_max_drawdown_pct"] = (
        worst_drawdown if np.isfinite(worst_drawdown) else float("nan")
    )
    return metrics
