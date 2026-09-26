"""Causal attribution for flat spells created by an SMA timing strategy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd


BASE_COLUMNS = [
    "symbol",
    "spell_index",
    "sell_signal_date",
    "sell_fill_date",
    "sell_raw_open",
    "sell_effective_fill",
    "closed_spell",
    "next_buy_signal_date",
    "next_buy_fill_date",
    "next_buy_raw_open",
    "next_buy_effective_fill",
    "mark_date",
    "mark_price",
    "flat_sessions",
    "calendar_days",
    "asset_return_while_flat_pct",
    "min_low_date",
    "min_low_vs_sell_open_pct",
    "max_high_date",
    "max_high_vs_sell_open_pct",
    "outcome",
    "major_bear_sessions",
    "minor_bear_sessions",
    "non_bear_sessions",
    "bear_overlap_labels",
]


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{label} misses required columns: {sorted(missing)}")


def _empty_events(horizons: Iterable[int]) -> pd.DataFrame:
    columns = list(BASE_COLUMNS)
    for horizon in horizons:
        columns.extend(
            [
                f"available_sessions_{horizon}",
                f"complete_horizon_{horizon}",
                f"end_close_return_pct_{horizon}",
                f"min_low_return_pct_{horizon}",
                f"max_high_return_pct_{horizon}",
            ]
        )
    return pd.DataFrame(columns=columns)


def _bear_session_labels(
    dates: pd.Series,
    bear_intervals: pd.DataFrame | None,
) -> tuple[int, int, int, str]:
    if bear_intervals is None or bear_intervals.empty or dates.empty:
        return 0, 0, int(len(dates)), ""
    _require_columns(bear_intervals, {"start", "end", "severity"}, "bear_intervals")
    values = pd.to_datetime(dates).reset_index(drop=True)
    major = pd.Series(False, index=values.index)
    minor = pd.Series(False, index=values.index)
    labels: list[str] = []
    for row in bear_intervals.itertuples(index=False):
        start = pd.Timestamp(row.start)
        end = pd.Timestamp(row.end)
        overlap = (values >= start) & (values <= end)
        if not bool(overlap.any()):
            continue
        severity = str(row.severity)
        if severity == "major":
            major |= overlap
        elif severity == "minor":
            minor |= overlap
        else:
            raise ValueError(f"Unknown bear severity: {severity}")
        ordinal = getattr(row, "ordinal", "")
        label = getattr(row, "label", "")
        labels.append(f"{ordinal}:{severity}:{label}".strip(":"))
    # Intervals are expected not to overlap, but major wins deterministically
    # if a malformed attribution input does overlap. This never changes signals.
    minor &= ~major
    major_count = int(major.sum())
    minor_count = int(minor.sum())
    non_bear_count = int(len(values) - major_count - minor_count)
    return major_count, minor_count, non_bear_count, " | ".join(labels)


def attribute_flat_spells(
    analysis: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    horizons: Iterable[int] = (5, 10, 20, 60),
    bear_intervals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Describe every sell-fill to next-buy-fill cash interval.

    The sell-fill session is included because the position is already cash from
    that session's Open. A closed spell ends at the next buy Open, so the
    re-entry session after that Open is excluded from path Low/High statistics.
    """

    horizon_values = tuple(int(value) for value in horizons)
    if not horizon_values or any(value <= 0 for value in horizon_values):
        raise ValueError("horizons must contain positive integers")
    if len(set(horizon_values)) != len(horizon_values):
        raise ValueError("horizons must be unique")
    _require_columns(
        analysis,
        {"symbol", "date", "open", "high", "low", "close"},
        "analysis",
    )
    if orders.empty:
        return _empty_events(horizon_values)
    _require_columns(
        orders,
        {"symbol", "type", "signal_date", "date", "raw_price", "fill_price"},
        "orders",
    )

    prices = analysis[["symbol", "date", "open", "high", "low", "close"]].copy()
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.sort_values("date", kind="stable").reset_index(drop=True)
    if prices["date"].duplicated().any():
        raise ValueError("analysis contains duplicate dates")
    symbols = prices["symbol"].astype(str).drop_duplicates().tolist()
    if len(symbols) != 1:
        raise ValueError("flat-spell attribution requires one symbol")
    symbol = symbols[0]
    if not np.isfinite(prices[["open", "high", "low", "close"]].to_numpy(float)).all():
        raise ValueError("analysis OHLC must be finite")
    if (prices[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("analysis OHLC must be positive")

    ordered = orders.copy()
    ordered["date"] = pd.to_datetime(ordered["date"])
    ordered["signal_date"] = pd.to_datetime(ordered["signal_date"])
    ordered = ordered.sort_values(["date", "type"], kind="stable").reset_index(drop=True)
    if set(ordered["symbol"].astype(str)) != {symbol}:
        raise ValueError("orders symbol does not match analysis")
    sides = ordered["type"].astype(str).tolist()
    if sides and sides[0] != "buy":
        raise ValueError("first timing order must be a buy")
    if any(left == right for left, right in zip(sides, sides[1:])):
        raise ValueError("timing orders must alternate buy and sell")

    index_by_date = {date: index for index, date in enumerate(prices["date"])}
    records: list[dict[str, Any]] = []
    sells = ordered[ordered["type"] == "sell"]
    for spell_index, sell in enumerate(sells.itertuples(index=False), start=1):
        sell_date = pd.Timestamp(sell.date)
        if sell_date not in index_by_date:
            raise ValueError(f"sell date is outside analysis: {sell_date.date()}")
        start_index = index_by_date[sell_date]
        later_buy = ordered[(ordered["type"] == "buy") & (ordered["date"] > sell_date)].head(1)
        closed = not later_buy.empty
        buy = None if later_buy.empty else later_buy.iloc[0]
        if closed:
            buy_date = pd.Timestamp(buy["date"])
            if buy_date not in index_by_date:
                raise ValueError(f"buy date is outside analysis: {buy_date.date()}")
            end_index = index_by_date[buy_date]
            if end_index <= start_index:
                raise ValueError("next buy must follow sell")
            path = prices.iloc[start_index:end_index]
            mark_date = buy_date
            mark_price = float(buy["raw_price"])
        else:
            path = prices.iloc[start_index:]
            mark_date = pd.Timestamp(prices.iloc[-1]["date"])
            mark_price = float(prices.iloc[-1]["close"])
        if path.empty:
            raise AssertionError("flat spell path cannot be empty")

        sell_raw = float(sell.raw_price)
        low_index = path["low"].astype(float).idxmin()
        high_index = path["high"].astype(float).idxmax()
        major, minor, non_bear, bear_labels = _bear_session_labels(
            path["date"], bear_intervals
        )
        asset_return = (mark_price / sell_raw - 1.0) * 100.0
        record: dict[str, Any] = {
            "symbol": symbol,
            "spell_index": spell_index,
            "sell_signal_date": pd.Timestamp(sell.signal_date),
            "sell_fill_date": sell_date,
            "sell_raw_open": sell_raw,
            "sell_effective_fill": float(sell.fill_price),
            "closed_spell": bool(closed),
            "next_buy_signal_date": pd.NaT if buy is None else pd.Timestamp(buy["signal_date"]),
            "next_buy_fill_date": pd.NaT if buy is None else pd.Timestamp(buy["date"]),
            "next_buy_raw_open": np.nan if buy is None else float(buy["raw_price"]),
            "next_buy_effective_fill": np.nan if buy is None else float(buy["fill_price"]),
            "mark_date": mark_date,
            "mark_price": mark_price,
            "flat_sessions": int(len(path)),
            "calendar_days": int((mark_date - sell_date).days),
            "asset_return_while_flat_pct": asset_return,
            "min_low_date": pd.Timestamp(prices.loc[low_index, "date"]),
            "min_low_vs_sell_open_pct": (
                float(prices.loc[low_index, "low"]) / sell_raw - 1.0
            )
            * 100.0,
            "max_high_date": pd.Timestamp(prices.loc[high_index, "date"]),
            "max_high_vs_sell_open_pct": (
                float(prices.loc[high_index, "high"]) / sell_raw - 1.0
            )
            * 100.0,
            "outcome": "avoided_loss" if asset_return < 0 else "missed_gain_or_flat",
            "major_bear_sessions": major,
            "minor_bear_sessions": minor,
            "non_bear_sessions": non_bear,
            "bear_overlap_labels": bear_labels,
        }
        for horizon in horizon_values:
            remaining = len(prices) - start_index
            available = min(horizon, remaining)
            complete = remaining >= horizon
            forward = prices.iloc[start_index : start_index + available]
            record[f"available_sessions_{horizon}"] = int(available)
            record[f"complete_horizon_{horizon}"] = bool(complete)
            if complete:
                record[f"end_close_return_pct_{horizon}"] = (
                    float(forward.iloc[-1]["close"]) / sell_raw - 1.0
                ) * 100.0
                record[f"min_low_return_pct_{horizon}"] = (
                    float(forward["low"].min()) / sell_raw - 1.0
                ) * 100.0
                record[f"max_high_return_pct_{horizon}"] = (
                    float(forward["high"].max()) / sell_raw - 1.0
                ) * 100.0
            else:
                record[f"end_close_return_pct_{horizon}"] = np.nan
                record[f"min_low_return_pct_{horizon}"] = np.nan
                record[f"max_high_return_pct_{horizon}"] = np.nan
        records.append(record)
    return pd.DataFrame(records, columns=_empty_events(horizon_values).columns)


def summarize_flat_spells(events: pd.DataFrame) -> dict[str, Any]:
    """Aggregate complete event history without discarding missed rebounds."""

    if events.empty:
        return {
            "spell_count": 0,
            "closed_spell_count": 0,
            "open_spell_count": 0,
            "avoided_loss_count": 0,
            "missed_gain_or_flat_count": 0,
            "closed_negative_share_pct": None,
            "closed_compound_asset_return_pct": None,
            "median_closed_asset_return_pct": None,
            "worst_closed_asset_return_pct": None,
            "best_closed_asset_return_pct": None,
            "total_flat_sessions": 0,
        }
    closed = events[events["closed_spell"].astype(bool)].copy()
    closed_returns = closed["asset_return_while_flat_pct"].astype(float)
    compound = (
        (float(np.prod(1.0 + closed_returns.to_numpy() / 100.0)) - 1.0) * 100.0
        if len(closed_returns)
        else None
    )
    return {
        "spell_count": int(len(events)),
        "closed_spell_count": int(len(closed)),
        "open_spell_count": int((~events["closed_spell"].astype(bool)).sum()),
        "avoided_loss_count": int((events["asset_return_while_flat_pct"] < 0).sum()),
        "missed_gain_or_flat_count": int((events["asset_return_while_flat_pct"] >= 0).sum()),
        "closed_negative_share_pct": (
            float((closed_returns < 0).mean() * 100.0) if len(closed_returns) else None
        ),
        "closed_compound_asset_return_pct": compound,
        "median_closed_asset_return_pct": (
            float(closed_returns.median()) if len(closed_returns) else None
        ),
        "worst_closed_asset_return_pct": (
            float(closed_returns.min()) if len(closed_returns) else None
        ),
        "best_closed_asset_return_pct": (
            float(closed_returns.max()) if len(closed_returns) else None
        ),
        "total_flat_sessions": int(events["flat_sessions"].sum()),
    }
