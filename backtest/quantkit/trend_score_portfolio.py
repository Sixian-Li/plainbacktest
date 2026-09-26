"""Unified multi-asset trend scores and weekly target-weight portfolio ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from pybroker import PositionMode, Strategy, StrategyConfig
from pybroker.context import ExecContext

from quantkit.execution import ExplicitFillPolicy


CASE_IDS = (
    "unified_score",
    "own_score_only",
    "sma200_atr_only",
    "risk_base_no_timing",
)


@dataclass(frozen=True)
class TrendScoreSpec:
    minimum_history_bars: int = 252
    sma_window: int = 200
    atr_window: int = 20
    atr_band_multiple: float = 1.0
    sma_slope_lookback: int = 20
    momentum_windows: tuple[int, int, int] = (63, 126, 252)
    own_weights: tuple[float, float, float, float, float] = (
        0.30,
        0.20,
        0.15,
        0.15,
        0.20,
    )
    market_weights: tuple[float, float, float] = (0.40, 0.30, 0.30)
    combined_weights: tuple[float, float] = (0.70, 0.30)
    score_thresholds: tuple[tuple[float, float], ...] = (
        (80.0, 1.00),
        (65.0, 0.75),
        (45.0, 0.40),
        (30.0, 0.15),
        (0.0, 0.00),
    )
    volatility_window: int = 63
    maximum_single_asset_weight: float = 0.10
    defensive_minimum_own_score: float = 50.0
    minimum_breadth_assets: int = 100

    @classmethod
    def from_parameters(cls, parameters: Mapping[str, Any]) -> "TrendScoreSpec":
        own = parameters["own_score"]
        market = parameters["market_score"]
        combined = parameters["combined_score"]
        thresholds = tuple(
            (float(item["minimum_score"]), float(item["multiplier"]))
            for item in parameters["score_multipliers"]
        )
        spec = cls(
            minimum_history_bars=int(parameters["minimum_history_bars"]),
            sma_window=int(own["sma_window"]),
            atr_window=int(own["atr_window"]),
            atr_band_multiple=float(own["atr_band_multiple"]),
            sma_slope_lookback=int(own["sma_slope_lookback"]),
            momentum_windows=tuple(int(value) for value in own["momentum_windows"]),
            own_weights=(
                float(own["price_vs_sma200_atr_hysteresis_weight"]),
                float(own["sma200_slope_20_weight"]),
                float(own["momentum_63_weight"]),
                float(own["momentum_126_weight"]),
                float(own["momentum_252_weight"]),
            ),
            market_weights=(
                float(market["spy_weight"]),
                float(market["qqq_weight"]),
                float(market["breadth_weight"]),
            ),
            combined_weights=(
                float(combined["own_weight"]),
                float(combined["market_weight"]),
            ),
            score_thresholds=thresholds,
            volatility_window=int(parameters["volatility_window"]),
            maximum_single_asset_weight=float(parameters["maximum_single_asset_weight"]),
            defensive_minimum_own_score=float(parameters["defensive_minimum_own_score"]),
            minimum_breadth_assets=int(market["minimum_breadth_assets"]),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if self.minimum_history_bars < max(self.momentum_windows):
            raise ValueError("minimum_history_bars must cover the longest momentum window")
        if not np.isclose(sum(self.own_weights), 1.0):
            raise ValueError("own score weights must sum to one")
        if not np.isclose(sum(self.market_weights), 1.0):
            raise ValueError("market score weights must sum to one")
        if not np.isclose(sum(self.combined_weights), 1.0):
            raise ValueError("combined score weights must sum to one")
        if not 0 < self.maximum_single_asset_weight <= 1:
            raise ValueError("maximum_single_asset_weight must be in (0, 1]")
        if any(multiplier < 0 or multiplier > 1 for _, multiplier in self.score_thresholds):
            raise ValueError("score multipliers must be between zero and one")
        thresholds = [threshold for threshold, _ in self.score_thresholds]
        if thresholds != sorted(thresholds, reverse=True) or thresholds[-1] != 0:
            raise ValueError("score thresholds must descend and end at zero")


@dataclass(frozen=True)
class PortfolioRun:
    daily: pd.DataFrame
    positions: pd.DataFrame
    orders: pd.DataFrame
    trades: pd.DataFrame


def _normalized_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{symbol} price data misses {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result = result.sort_values("date").drop_duplicates("date", keep="last")
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    if result.empty or result[list(required - {"date"})].isna().any().any():
        raise ValueError(f"{symbol} price data is empty or non-finite")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol} contains non-positive prices")
    result["symbol"] = symbol
    return result.reset_index(drop=True)


def hysteresis_state(
    close: pd.Series,
    sma: pd.Series,
    atr: pd.Series,
    *,
    band_multiple: float,
    initial_state: float = 50.0,
) -> pd.Series:
    """Return 0/50/100 state, retaining the previous state inside the ATR band."""

    values: list[float] = []
    state = float(initial_state)
    for price, average, volatility in zip(close, sma, atr, strict=True):
        if pd.notna(price) and pd.notna(average) and pd.notna(volatility):
            if float(price) >= float(average) + band_multiple * float(volatility):
                state = 100.0
            elif float(price) <= float(average) - band_multiple * float(volatility):
                state = 0.0
        values.append(state if pd.notna(average) and pd.notna(volatility) else np.nan)
    return pd.Series(values, index=close.index, dtype=float)


def prepare_asset_indicators(
    frame: pd.DataFrame,
    symbol: str,
    spec: TrendScoreSpec,
) -> pd.DataFrame:
    data = _normalized_frame(frame, symbol)
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr20"] = true_range.rolling(spec.atr_window, min_periods=spec.atr_window).mean()
    data["sma200"] = data["close"].rolling(
        spec.sma_window, min_periods=spec.sma_window
    ).mean()
    data["hysteresis_score"] = hysteresis_state(
        data["close"],
        data["sma200"],
        data["atr20"],
        band_multiple=spec.atr_band_multiple,
    )
    data["sma_slope_score"] = np.where(
        data["sma200"].notna() & data["sma200"].shift(spec.sma_slope_lookback).notna(),
        np.where(data["sma200"] > data["sma200"].shift(spec.sma_slope_lookback), 100.0, 0.0),
        np.nan,
    )
    momentum_columns: list[str] = []
    for window in spec.momentum_windows:
        column = f"momentum_{window}_score"
        momentum = data["close"].pct_change(window, fill_method=None)
        data[column] = np.where(
            momentum.notna(), np.where(momentum > 0, 100.0, 0.0), np.nan
        )
        momentum_columns.append(column)
    components = ["hysteresis_score", "sma_slope_score", *momentum_columns]
    data["own_score"] = sum(
        data[column] * weight for column, weight in zip(components, spec.own_weights, strict=True)
    )
    data["realized_volatility"] = (
        data["close"].pct_change(fill_method=None).rolling(
            spec.volatility_window, min_periods=spec.volatility_window
        ).std(ddof=1)
        * np.sqrt(252.0)
    )
    data["history_bars"] = np.arange(1, len(data) + 1)
    data["ready"] = (
        (data["history_bars"] >= spec.minimum_history_bars)
        & data["own_score"].notna()
        & data["realized_volatility"].gt(0)
    )
    return data


def build_breadth_series(
    close_by_symbol: Mapping[str, pd.Series],
    *,
    sma_window: int,
    minimum_assets: int,
) -> pd.DataFrame:
    dates = pd.Index(
        sorted({pd.Timestamp(date) for series in close_by_symbol.values() for date in series.index}),
        name="date",
    )
    columns: dict[str, pd.Series] = {}
    for symbol, series in close_by_symbol.items():
        prices = series.copy().astype(float).sort_index()
        average = prices.rolling(sma_window, min_periods=sma_window).mean()
        columns[symbol] = (prices > average).where(average.notna()).reindex(dates)
    above = pd.DataFrame(columns, index=dates)
    count = above.notna().sum(axis=1)
    breadth = above.mean(axis=1, skipna=True) * 100.0
    breadth = breadth.where(count >= minimum_assets)
    return pd.DataFrame(
        {"date": dates, "breadth_score": breadth.to_numpy(), "breadth_asset_count": count.to_numpy()}
    )


def prepare_signal_table(
    asset_frames: Mapping[str, pd.DataFrame],
    spy_frame: pd.DataFrame,
    qqq_frame: pd.DataFrame,
    breadth: pd.DataFrame,
    spec: TrendScoreSpec,
    *,
    defensive_assets: Sequence[str] = ("TLT", "GLD"),
) -> pd.DataFrame:
    prepared = {
        symbol: prepare_asset_indicators(frame, symbol, spec)
        for symbol, frame in asset_frames.items()
    }
    market_prepared = {
        "SPY": prepare_asset_indicators(spy_frame, "SPY", spec),
        "QQQ": prepare_asset_indicators(qqq_frame, "QQQ", spec),
    }
    spy_score = market_prepared["SPY"].set_index("date")["own_score"].rename("spy_score")
    qqq_score = market_prepared["QQQ"].set_index("date")["own_score"].rename("qqq_score")
    breadth_indexed = breadth.copy()
    breadth_indexed["date"] = pd.to_datetime(breadth_indexed["date"])
    breadth_indexed = breadth_indexed.set_index("date")
    dates = market_prepared["SPY"]["date"]
    market = pd.DataFrame({"date": dates}).set_index("date")
    market = market.join(spy_score).join(qqq_score).join(breadth_indexed)
    market["market_score"] = (
        market["spy_score"] * spec.market_weights[0]
        + market["qqq_score"] * spec.market_weights[1]
        + market["breadth_score"] * spec.market_weights[2]
    )

    rows: list[pd.DataFrame] = []
    for symbol, frame in prepared.items():
        item = frame.merge(market.reset_index(), on="date", how="left")
        item["combined_score"] = (
            item["own_score"] * spec.combined_weights[0]
            + item["market_score"] * spec.combined_weights[1]
        )
        item["defensive_asset"] = symbol in defensive_assets
        rows.append(item)
    signals = pd.concat(rows, ignore_index=True).sort_values(["date", "symbol"])
    signals["ready"] = signals["ready"] & signals["market_score"].notna()
    return signals.reset_index(drop=True)


def score_multiplier(score: float, thresholds: Sequence[tuple[float, float]]) -> float:
    if not np.isfinite(score):
        return 0.0
    for threshold, multiplier in thresholds:
        if score >= threshold:
            return float(multiplier)
    return 0.0


def _capped_inverse_volatility(
    volatility: pd.Series,
    *,
    cap: float,
) -> pd.Series:
    values = volatility.astype(float)
    values = values[np.isfinite(values) & (values > 0)]
    if values.empty:
        return pd.Series(dtype=float)
    raw = 1.0 / values
    weights = pd.Series(0.0, index=raw.index, dtype=float)
    remaining = list(raw.index)
    budget = 1.0
    while remaining and budget > 1e-15:
        allocation = raw.loc[remaining] / raw.loc[remaining].sum() * budget
        capped = allocation[allocation > cap + 1e-15]
        if capped.empty:
            weights.loc[remaining] = allocation
            break
        for symbol in capped.index:
            weights.loc[symbol] = cap
            budget -= cap
            remaining.remove(symbol)
    return weights


def last_session_of_week(dates: Sequence[pd.Timestamp]) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values().unique()
    if index.empty:
        return index
    series = pd.Series(index=index, data=index)
    candidates = pd.DatetimeIndex(series.groupby(index.to_period("W-FRI")).max().to_numpy())
    # A truncated data set may end on Monday-Wednesday. That partial week has
    # not reached a normal US week-ending session and must not be promoted to
    # a weekly signal merely because it is the last row available.
    return candidates[candidates.weekday >= 3]


def build_weekly_targets(
    signals: pd.DataFrame,
    calendar_dates: Sequence[pd.Timestamp],
    spec: TrendScoreSpec,
    *,
    analysis_start: str | pd.Timestamp,
    analysis_end: str | pd.Timestamp,
    case_ids: Sequence[str] = CASE_IDS,
) -> pd.DataFrame:
    start = pd.Timestamp(analysis_start)
    end = pd.Timestamp(analysis_end)
    dates = pd.DatetimeIndex(pd.to_datetime(list(calendar_dates)))
    dates = dates[(dates >= start) & (dates <= end)].sort_values().unique()
    rebalance_dates = last_session_of_week(dates)
    indexed = signals.set_index(["date", "symbol"]).sort_index()
    rows: list[dict[str, Any]] = []
    for date in rebalance_dates:
        if date not in indexed.index.get_level_values("date"):
            continue
        snapshot = indexed.loc[date].copy()
        eligible = snapshot[snapshot["ready"] & snapshot["realized_volatility"].gt(0)]
        base = _capped_inverse_volatility(
            eligible["realized_volatility"], cap=spec.maximum_single_asset_weight
        )
        for symbol, item in snapshot.iterrows():
            base_weight = float(base.get(symbol, 0.0))
            for case_id in case_ids:
                if case_id == "unified_score":
                    score = float(item["combined_score"])
                elif case_id == "own_score_only":
                    score = float(item["own_score"])
                elif case_id == "sma200_atr_only":
                    score = float(item["hysteresis_score"])
                elif case_id == "risk_base_no_timing":
                    score = 100.0
                else:
                    raise ValueError(f"Unknown case: {case_id}")
                multiplier = score_multiplier(score, spec.score_thresholds)
                if bool(item["defensive_asset"]) and case_id != "risk_base_no_timing":
                    if not np.isfinite(float(item["own_score"])) or float(item["own_score"]) < spec.defensive_minimum_own_score:
                        multiplier = 0.0
                target_weight = base_weight * multiplier if bool(item["ready"]) else 0.0
                rows.append(
                    {
                        "date": date,
                        "case_id": case_id,
                        "symbol": symbol,
                        "ready": bool(item["ready"]),
                        "own_score": item["own_score"],
                        "market_score": item["market_score"],
                        "combined_score": item["combined_score"],
                        "hysteresis_score": item["hysteresis_score"],
                        "realized_volatility": item["realized_volatility"],
                        "base_weight": base_weight,
                        "score_used": score,
                        "multiplier": multiplier,
                        "target_weight": target_weight,
                    }
                )
    targets = pd.DataFrame(rows)
    if not targets.empty:
        totals = targets.groupby(["date", "case_id"])["target_weight"].sum()
        if (totals > 1 + 1e-12).any():
            raise AssertionError("target portfolio weight exceeds 100%")
        if (targets["target_weight"] > spec.maximum_single_asset_weight + 1e-12).any():
            raise AssertionError("single-asset target exceeds cap")
    return targets


def _aligned_price_panel(
    asset_frames: Mapping[str, pd.DataFrame],
    calendar_dates: Sequence[pd.Timestamp],
    *,
    analysis_start: str | pd.Timestamp,
    analysis_end: str | pd.Timestamp,
) -> pd.DataFrame:
    start = pd.Timestamp(analysis_start)
    end = pd.Timestamp(analysis_end)
    dates = pd.DatetimeIndex(pd.to_datetime(list(calendar_dates)))
    dates = dates[(dates >= start) & (dates <= end)].sort_values().unique()
    rows: list[pd.DataFrame] = []
    for symbol, frame in asset_frames.items():
        data = _normalized_frame(frame, symbol)
        data = data[data["date"].isin(dates)].copy()
        listed_dates = dates[dates >= data["date"].min()]
        if not pd.DatetimeIndex(data["date"]).equals(listed_dates):
            raise ValueError(f"{symbol} has a missing open/close after listing on the common calendar")
        rows.append(data)
    return pd.concat(rows, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)


def align_price_panel(
    asset_frames: Mapping[str, pd.DataFrame],
    calendar_dates: Sequence[pd.Timestamp],
    *,
    analysis_start: str | pd.Timestamp,
    analysis_end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Public wrapper for constructing PyBroker's long multi-asset price frame."""

    return _aligned_price_panel(
        asset_frames,
        calendar_dates,
        analysis_start=analysis_start,
        analysis_end=analysis_end,
    )


def _target_share_table(
    price_panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    case_id: str,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> pd.DataFrame:
    dates = pd.DatetimeIndex(price_panel["date"].drop_duplicates().sort_values())
    opens = price_panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = price_panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    target = (
        targets[targets["case_id"] == case_id]
        .pivot(index="date", columns="symbol", values="target_weight")
        .reindex(columns=closes.columns)
    )
    signal_to_execution = {dates[i]: dates[i + 1] for i in range(len(dates) - 1)}
    current_shares = pd.Series(0.0, index=closes.columns)
    cash = float(initial_cash)
    rows: list[dict[str, Any]] = []
    for signal_date, weights in target.iterrows():
        execution_date = signal_to_execution.get(pd.Timestamp(signal_date))
        if execution_date is None:
            continue
        signal_close = closes.loc[signal_date]
        equity = cash + float((current_shares * signal_close.fillna(0.0)).sum())
        desired = pd.Series(0.0, index=closes.columns)
        tradable = signal_close.notna() & weights.notna()
        desired.loc[tradable] = (
            equity * weights.loc[tradable].astype(float) / signal_close.loc[tradable].astype(float)
        )
        execution_open = opens.loc[execution_date]
        sell_delta = (current_shares - desired).clip(lower=0)
        for symbol in sorted(closes.columns):
            shares = float(sell_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("sell", float(execution_open[symbol]))
            cash += shares * fill
            current_shares[symbol] -= shares
        buy_delta = (desired - current_shares).clip(lower=0)
        for symbol in sorted(closes.columns):
            shares = float(buy_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("buy", float(execution_open[symbol]))
            affordable = max(cash, 0.0) / fill
            filled = min(shares, affordable)
            cash -= filled * fill
            current_shares[symbol] += filled
        for symbol in closes.columns:
            rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "target_weight": float(weights.get(symbol, 0.0) or 0.0),
                    "target_shares": float(desired[symbol]),
                }
            )
    return pd.DataFrame(rows)


def build_target_share_table(
    price_panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    case_id: str,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> pd.DataFrame:
    """Public wrapper for close-sized targets that execute on the next open."""

    return _target_share_table(
        price_panel,
        targets,
        case_id=case_id,
        initial_cash=initial_cash,
        policy=policy,
    )


def run_pybroker_portfolio(
    price_panel: pd.DataFrame,
    target_shares: pd.DataFrame,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> tuple[Any, pd.DataFrame]:
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    symbols = sorted(panel["symbol"].unique())
    lookup = {
        (pd.Timestamp(row.signal_date), str(row.symbol)): float(row.target_shares)
        for row in target_shares.itertuples(index=False)
    }

    def execute(ctx: ExecContext) -> None:
        key = (pd.Timestamp(ctx.dt), ctx.symbol)
        if key not in lookup:
            return
        desired = lookup[key]
        position = ctx.long_pos()
        current = float(position.shares) if position is not None else 0.0
        delta = desired - current
        if delta > 1e-12:
            ctx.buy_shares = Decimal(str(delta))
            ctx.buy_fill_price = policy.buy_fill()
            ctx.score = float(len(symbols) - symbols.index(ctx.symbol))
        elif delta < -1e-12:
            ctx.sell_shares = Decimal(str(-delta))
            ctx.sell_fill_price = policy.sell_fill()
            ctx.score = float(len(symbols) - symbols.index(ctx.symbol))

    config = StrategyConfig(
        initial_cash=initial_cash,
        fee_mode=None,
        fee_amount=0,
        enable_fractional_shares=True,
        round_fill_price=False,
        position_mode=PositionMode.LONG_ONLY,
        max_long_positions=len(symbols),
        buy_delay=1,
        sell_delay=1,
        exit_on_last_bar=False,
        exit_cover_fill_price=policy.buy_fill(),
        exit_sell_fill_price=policy.sell_fill(),
        bars_per_year=252,
        round_test_result=False,
    )
    start = panel["date"].min()
    end = panel["date"].max()
    strategy = Strategy(panel, start, end, config)
    strategy.add_execution(execute, symbols)
    result = strategy.backtest(calc_bootstrap=False, disable_parallel=True)
    positions = _pybroker_position_state(result, panel)
    return result, positions


def _pybroker_position_state(result: Any, price_panel: pd.DataFrame) -> pd.DataFrame:
    dates = pd.DatetimeIndex(price_panel["date"].drop_duplicates().sort_values())
    symbols = sorted(price_panel["symbol"].unique())
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    shares = pd.Series(0.0, index=index)
    if not result.positions.empty:
        frame = result.positions.reset_index()
        observed = frame.set_index(["date", "symbol"])["long_shares"].astype(float)
        shares.loc[observed.index] = observed.to_numpy()
    return shares.rename("shares").reset_index()


def run_reference_portfolio(
    price_panel: pd.DataFrame,
    target_shares: pd.DataFrame,
    *,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> PortfolioRun:
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    symbols = sorted(panel["symbol"].unique())
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    by_execution = {
        pd.Timestamp(date): frame.set_index("symbol")["target_shares"].astype(float)
        for date, frame in target_shares.groupby("execution_date")
    }
    current = pd.Series(0.0, index=symbols)
    cash = float(initial_cash)
    lots: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    order_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    order_id = 0
    trade_id = 0

    for date in dates:
        if date in by_execution:
            desired = by_execution[date].reindex(symbols).fillna(0.0)
            for symbol in symbols:
                shares = max(float(current[symbol] - desired[symbol]), 0.0)
                if shares <= 1e-12:
                    continue
                fill = policy.expected_fill("sell", float(opens.at[date, symbol]))
                remaining = shares
                pnl = 0.0
                entry_value = 0.0
                while remaining > 1e-12 and lots[symbol]:
                    lot = lots[symbol][0]
                    matched = min(remaining, float(lot["shares"]))
                    entry_value += matched * float(lot["price"])
                    lot_pnl = matched * (fill - float(lot["price"]))
                    pnl += lot_pnl
                    trade_id += 1
                    trade_rows.append(
                        {
                            "id": trade_id,
                            "type": "long",
                            "symbol": symbol,
                            "entry_date": lot["date"],
                            "exit_date": date,
                            "entry": float(lot["price"]),
                            "exit": fill,
                            "shares": matched,
                            "pnl": lot_pnl,
                            "return_pct": (fill / float(lot["price"]) - 1.0) * 100.0,
                        }
                    )
                    lot["shares"] = float(lot["shares"]) - matched
                    remaining -= matched
                    if float(lot["shares"]) <= 1e-12:
                        lots[symbol].pop(0)
                cash += shares * fill
                current[symbol] -= shares
                order_id += 1
                order_rows.append(
                    {
                        "id": order_id,
                        "date": date,
                        "symbol": symbol,
                        "type": "sell",
                        "fill_price": fill,
                        "shares": shares,
                    }
                )
            for symbol in symbols:
                shares = max(float(desired[symbol] - current[symbol]), 0.0)
                if shares <= 1e-12:
                    continue
                fill = policy.expected_fill("buy", float(opens.at[date, symbol]))
                filled = min(shares, max(cash, 0.0) / fill)
                if filled <= 1e-12:
                    continue
                cash -= filled * fill
                current[symbol] += filled
                lots[symbol].append({"date": date, "price": fill, "shares": filled})
                order_id += 1
                order_rows.append(
                    {
                        "id": order_id,
                        "date": date,
                        "symbol": symbol,
                        "type": "buy",
                        "fill_price": fill,
                        "shares": filled,
                    }
                )
        market_values = current * closes.loc[date].fillna(0.0)
        equity = cash + float(market_values.sum())
        daily_rows.append(
            {
                "date": date,
                "cash": cash,
                "shares": float(current.sum()),
                "equity": equity,
                "is_long": int(current.sum() > 1e-12),
                "gross_exposure": float(market_values.sum() / equity) if equity > 0 else 0.0,
            }
        )
        for symbol in symbols:
            position_rows.append({"date": date, "symbol": symbol, "shares": float(current[symbol])})
    return PortfolioRun(
        daily=pd.DataFrame(daily_rows),
        positions=pd.DataFrame(position_rows),
        orders=pd.DataFrame(order_rows),
        trades=pd.DataFrame(trade_rows),
    )


def pybroker_daily_state(result: Any) -> pd.DataFrame:
    frame = result.portfolio.reset_index()[["date", "cash", "equity", "market_value"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["shares"] = 0.0
    if not result.positions.empty:
        shares = result.positions.reset_index().groupby("date")["long_shares"].sum().astype(float)
        frame["shares"] = frame["date"].map(shares).fillna(0.0)
    frame["is_long"] = (frame["shares"] > 1e-12).astype(int)
    # In PyBroker's portfolio output ``market_value`` is total marked account
    # value rather than long security value. For this long-only, unfinanced
    # portfolio the invested fraction is exactly (equity - cash) / equity.
    frame["gross_exposure"] = np.where(
        frame["equity"].astype(float) > 0,
        (frame["equity"].astype(float) - frame["cash"].astype(float))
        / frame["equity"].astype(float),
        0.0,
    )
    return frame.drop(columns="market_value")


def cross_check_portfolios(
    result: Any,
    pybroker_positions: pd.DataFrame,
    reference: PortfolioRun,
    *,
    tolerance: float = 1e-6,
    numerical_zero_notional: float = 1e-12,
) -> dict[str, float]:
    actual_daily = pybroker_daily_state(result)
    expected_daily = reference.daily
    differences: dict[str, float] = {}
    for column in ("cash", "equity"):
        if len(actual_daily) != len(expected_daily):
            raise AssertionError(f"daily {column} length mismatch")
        maximum = float(
            np.max(
                np.abs(
                    actual_daily[column].to_numpy(float)
                    - expected_daily[column].to_numpy(float)
                )
            )
        )
        if not np.isfinite(maximum):
            raise AssertionError(f"daily {column} difference is non-finite")
        differences[f"max_abs_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"daily {column} differs by {maximum}")
    actual_positions = pybroker_positions.sort_values(["date", "symbol"]).reset_index(drop=True)
    expected_positions = reference.positions.sort_values(["date", "symbol"]).reset_index(drop=True)
    maximum = float(
        np.max(
            np.abs(
                actual_positions["shares"].to_numpy(float)
                - expected_positions["shares"].to_numpy(float)
            )
        )
    )
    if not np.isfinite(maximum):
        raise AssertionError("position shares difference is non-finite")
    differences["max_abs_position_shares_difference"] = maximum
    if maximum > tolerance:
        raise AssertionError(f"position shares differ by {maximum}")

    actual_orders = result.orders.reset_index()
    expected_orders = reference.orders.copy()
    if numerical_zero_notional < 0 or not np.isfinite(numerical_zero_notional):
        raise ValueError("numerical_zero_notional must be finite and non-negative")
    numerical_zero_notional = min(tolerance, numerical_zero_notional)
    actual_notional = (actual_orders["shares"].astype(float) * actual_orders["fill_price"].astype(float)).abs()
    expected_notional = (expected_orders["shares"].astype(float) * expected_orders["fill_price"].astype(float)).abs()
    ignored_notional = pd.concat(
        [actual_notional[actual_notional <= numerical_zero_notional], expected_notional[expected_notional <= numerical_zero_notional]]
    )
    differences["max_ignored_numerical_zero_order_notional"] = (
        float(ignored_notional.max()) if len(ignored_notional) else 0.0
    )
    actual_orders = actual_orders[actual_notional > numerical_zero_notional].sort_values(
        ["date", "type", "symbol"]
    ).reset_index(drop=True)
    expected_orders = expected_orders[expected_notional > numerical_zero_notional].sort_values(
        ["date", "type", "symbol"]
    ).reset_index(drop=True)
    if len(actual_orders) != len(expected_orders):
        raise AssertionError(f"order count mismatch: {len(actual_orders)} != {len(expected_orders)}")
    for column in ("date", "type", "symbol"):
        left = pd.to_datetime(actual_orders[column]).tolist() if column == "date" else actual_orders[column].tolist()
        right = pd.to_datetime(expected_orders[column]).tolist() if column == "date" else expected_orders[column].tolist()
        if left != right:
            raise AssertionError(f"order {column} sequence differs")
    for column in ("shares", "fill_price"):
        maximum = float(
            np.max(
                np.abs(
                    actual_orders[column].to_numpy(float)
                    - expected_orders[column].to_numpy(float)
                )
            )
        ) if len(actual_orders) else 0.0
        if not np.isfinite(maximum):
            raise AssertionError(f"order {column} difference is non-finite")
        differences[f"max_abs_order_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"order {column} differs by {maximum}")

    # PyBroker and the float reference can split the same fractional-share sell
    # across FIFO lots differently at sub-tolerance residuals. The accounting
    # invariant is the aggregate shares and realized PnL for each symbol/exit
    # date, not an identical count of microscopic lot fragments.
    actual_trades = result.trades.reset_index()
    expected_trades = reference.trades.copy()

    def trade_notionals(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(index=frame.index, dtype=float)
        return (
            frame["shares"].astype(float).abs()
            * frame[["entry", "exit"]].astype(float).abs().max(axis=1)
        )

    actual_trade_notional = trade_notionals(actual_trades)
    expected_trade_notional = trade_notionals(expected_trades)
    ignored_trade_notional = pd.concat(
        [
            actual_trade_notional[
                actual_trade_notional <= numerical_zero_notional
            ],
            expected_trade_notional[
                expected_trade_notional <= numerical_zero_notional
            ],
        ]
    )
    differences["max_ignored_numerical_zero_trade_notional"] = (
        float(ignored_trade_notional.max()) if len(ignored_trade_notional) else 0.0
    )
    actual_trades = actual_trades[
        actual_trade_notional > numerical_zero_notional
    ].copy()
    expected_trades = expected_trades[
        expected_trade_notional > numerical_zero_notional
    ].copy()
    if actual_trades.empty and expected_trades.empty:
        differences["max_abs_trade_aggregate_shares_difference"] = 0.0
        differences["max_abs_trade_aggregate_pnl_difference"] = 0.0
        return differences
    if actual_trades.empty != expected_trades.empty:
        raise AssertionError("one ledger has trades while the other is empty")
    trade_keys = ["exit_date", "symbol"]
    actual_aggregate = actual_trades.groupby(trade_keys, as_index=False)[["shares", "pnl"]].sum()
    expected_aggregate = expected_trades.groupby(trade_keys, as_index=False)[["shares", "pnl"]].sum()
    aggregate = actual_aggregate.merge(
        expected_aggregate,
        on=trade_keys,
        how="outer",
        suffixes=("_actual", "_expected"),
        indicator=True,
    )
    if not (aggregate["_merge"] == "both").all():
        unmatched = aggregate.loc[
            aggregate["_merge"] != "both", [*trade_keys, "_merge"]
        ].to_dict("records")
        raise AssertionError(
            f"trade aggregate symbol/exit-date keys differ: {unmatched[:10]}"
        )
    for column in ("shares", "pnl"):
        maximum = float(
            np.max(
                np.abs(
                    aggregate[f"{column}_actual"].to_numpy(float)
                    - aggregate[f"{column}_expected"].to_numpy(float)
                )
            )
        ) if len(aggregate) else 0.0
        if not np.isfinite(maximum):
            raise AssertionError(f"trade aggregate {column} difference is non-finite")
        differences[f"max_abs_trade_aggregate_{column}_difference"] = maximum
        if maximum > tolerance:
            raise AssertionError(f"trade aggregate {column} differs by {maximum}")
    return differences
