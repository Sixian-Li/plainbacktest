"""QQQ timing policies with a fixed-weight Bear9 residual sleeve."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.qqq_flat_substitution import HysteresisSpec, prepare_hysteresis_state


BEAR_CASH = "CASH"
BEAR_ZERO_ONLY = "BEAR_ZERO_ONLY"
BEAR_RESIDUAL = "BEAR_RESIDUAL"
VALID_BEAR_MODES = (BEAR_CASH, BEAR_ZERO_ONLY, BEAR_RESIDUAL)


def classify_layered_weight(close: float, fast_sma: float, slow_sma: float) -> tuple[float, str]:
    """Return the frozen 0/30/70/100 QQQ target and its reader-facing zone."""

    values = np.asarray([close, fast_sma, slow_sma], dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("layered state requires finite positive close and SMAs")
    if slow_sma >= fast_sma:
        return 0.0, "slow_not_below_fast"
    midpoint = (fast_sma + slow_sma) / 2.0
    if close >= fast_sma:
        return 1.0, "above_fast"
    if close >= midpoint:
        return 0.7, "upper_half"
    if close > slow_sma:
        return 0.3, "lower_half"
    return 0.0, "at_or_below_slow"


def prepare_layered_timing(
    frame: pd.DataFrame,
    *,
    fast_window: int = 190,
    slow_window: int = 310,
) -> pd.DataFrame:
    """Build a causal close-confirmed SMA190/SMA310 allocation ladder."""

    if fast_window < 1 or slow_window <= fast_window:
        raise ValueError("layered windows require 0 < fast_window < slow_window")
    required = {"date", "open", "high", "low", "close"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"QQQ frame misses {sorted(missing)}")
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise")
    data = data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        data[column] = pd.to_numeric(data[column], errors="raise")
    if data.empty or not np.isfinite(data[["open", "high", "low", "close"]]).all().all():
        raise ValueError("QQQ frame is empty or non-finite")
    data["fast_sma"] = data["close"].rolling(fast_window, min_periods=fast_window).mean()
    data["slow_sma"] = data["close"].rolling(slow_window, min_periods=slow_window).mean()
    data["midpoint"] = (data["fast_sma"] + data["slow_sma"]) / 2.0
    data["ready"] = data["slow_sma"].notna()
    weights: list[float] = []
    zones: list[str] = []
    for row in data.itertuples(index=False):
        if not bool(row.ready):
            weights.append(0.0)
            zones.append("not_ready")
            continue
        weight, zone = classify_layered_weight(
            float(row.close), float(row.fast_sma), float(row.slow_sma)
        )
        weights.append(weight)
        zones.append(zone)
    data["qqq_weight"] = weights
    data["zone"] = zones
    data["transition"] = data["zone"].ne(data["zone"].shift()) | data["qqq_weight"].ne(
        data["qqq_weight"].shift()
    )
    return data


def prepare_symmetric_timing(
    frame: pd.DataFrame,
    *,
    window: int,
    buffer_pct: float = 3.0,
) -> pd.DataFrame:
    """Build one continuously evaluated SMA ±buffer QQQ timing path."""

    state = prepare_hysteresis_state(
        frame,
        "QQQ",
        HysteresisSpec(
            window=window,
            entry_buffer_pct=buffer_pct,
            exit_buffer_pct=buffer_pct,
            first_valid_level_entry=False,
        ),
    )
    state["qqq_weight"] = state["is_long"].astype(float)
    state["zone"] = np.where(state["is_long"], "long", "flat")
    return state


def case_definitions(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the complete, deterministic 97-case order."""

    intervals = list(parameters["rebalance_intervals_trading_days"])
    if intervals != [None, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30]:
        raise AssertionError("rebalance interval grid changed")
    cases: list[dict[str, Any]] = []

    def append(timing_id: str, bear_mode: str, interval: int | None) -> None:
        suffix = "HOLD" if interval is None else f"RB{int(interval):02d}"
        cases.append(
            {
                "case_id": f"{timing_id}__{bear_mode}__{suffix}",
                "timing_id": timing_id,
                "bear_mode": bear_mode,
                "rebalance_days": interval,
            }
        )

    for window in parameters["symmetric_timing"]["sma_windows"]:
        timing_id = f"SMA{int(window)}"
        append(timing_id, BEAR_CASH, None)
        for interval in intervals:
            append(timing_id, BEAR_RESIDUAL, interval)
    layered_id = "LAYERED_190_310"
    append(layered_id, BEAR_CASH, None)
    for bear_mode in (BEAR_ZERO_ONLY, BEAR_RESIDUAL):
        for interval in intervals:
            append(layered_id, bear_mode, interval)
    if len(cases) != int(parameters["formal_case_count_per_cost"]):
        raise AssertionError(f"formal case count changed: {len(cases)}")
    if len({item["case_id"] for item in cases}) != len(cases):
        raise AssertionError("formal case IDs are not unique")
    return cases


def bear_sleeve_weight(qqq_weight: float, bear_mode: str) -> float:
    """Translate a master QQQ target into the allowed Bear9 sleeve size."""

    if bear_mode not in VALID_BEAR_MODES:
        raise ValueError(f"unknown Bear9 mode: {bear_mode}")
    if not np.isfinite(qqq_weight) or qqq_weight < -1e-12 or qqq_weight > 1.0 + 1e-12:
        raise ValueError("QQQ weight must be between zero and one")
    qqq_weight = float(np.clip(qqq_weight, 0.0, 1.0))
    if bear_mode == BEAR_CASH:
        return 0.0
    if bear_mode == BEAR_ZERO_ONLY:
        return 1.0 if qqq_weight <= 1e-12 else 0.0
    return 1.0 - qqq_weight


def build_target_weight_schedule(
    timing_state: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    case_id: str,
    bear_weights: Mapping[str, float],
    bear_mode: str,
    rebalance_days: int | None,
) -> pd.DataFrame:
    """Emit close-confirmed target weights only when policy requires a trade."""

    required = {"date", "qqq_weight", "zone"}
    if missing := required.difference(timing_state.columns):
        raise ValueError(f"timing state misses {sorted(missing)}")
    weights = pd.Series({str(key): float(value) for key, value in bear_weights.items()})
    if (weights < 0).any() or not np.isclose(float(weights.sum()), 1.0, atol=1e-12):
        raise ValueError("Bear9 weights must be non-negative and sum to one")
    if rebalance_days is not None and int(rebalance_days) < 1:
        raise ValueError("rebalance_days must be positive or None")
    if bear_mode == BEAR_CASH and rebalance_days is not None:
        raise ValueError("cash mode cannot have a Bear9 rebalance interval")

    state = timing_state.copy()
    state["date"] = pd.to_datetime(state["date"])
    state = state.sort_values("date").drop_duplicates("date", keep="last")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(state["date"])
    symbols = ["QQQ", *weights.index.tolist()]
    if "QQQ" not in closes.columns:
        raise ValueError("price panel misses QQQ")
    rows: list[dict[str, Any]] = []
    last_emit_index: int | None = None
    last_top_level: tuple[float, float] | None = None
    last_available: tuple[str, ...] | None = None

    for index, item in enumerate(state.itertuples(index=False)):
        date = pd.Timestamp(item.date)
        qqq_weight = float(item.qqq_weight)
        sleeve_weight = bear_sleeve_weight(qqq_weight, bear_mode)
        available = tuple(
            symbol
            for symbol in weights.index
            if symbol in closes.columns and pd.notna(closes.at[date, symbol])
        )
        top_level = (round(qqq_weight, 12), round(sleeve_weight, 12))
        reason: str | None = None
        if last_emit_index is None:
            reason = "initial_target"
        elif top_level != last_top_level:
            reason = "allocation_state_change"
        elif sleeve_weight > 0 and available != last_available:
            reason = "tradable_member_change"
        elif (
            sleeve_weight > 0
            and rebalance_days is not None
            and index - last_emit_index >= int(rebalance_days)
        ):
            reason = "periodic_rebalance"
        if reason is None:
            continue

        targets = pd.Series(0.0, index=symbols, dtype=float)
        targets.loc["QQQ"] = qqq_weight
        for symbol in available:
            targets.loc[symbol] = sleeve_weight * float(weights[symbol])
        if float(targets.sum()) > 1.0 + 1e-12:
            raise AssertionError("target weights exceed the long-only budget")
        for symbol in symbols:
            rows.append(
                {
                    "case_id": case_id,
                    "date": date,
                    "symbol": symbol,
                    "target_weight": float(targets[symbol]),
                    "qqq_weight": qqq_weight,
                    "bear_weight": sleeve_weight,
                    "cash_target_weight": 1.0 - float(targets.sum()),
                    "zone": str(item.zone),
                    "reason": reason,
                    "rebalance_days": rebalance_days,
                    "tradable_bear_count": len(available),
                }
            )
        last_emit_index = index
        last_top_level = top_level
        last_available = available
    return pd.DataFrame(rows)


def add_realized_exposures(
    daily: pd.DataFrame,
    positions: pd.DataFrame,
    price_panel: pd.DataFrame,
    *,
    bear_symbols: Sequence[str],
) -> pd.DataFrame:
    """Attach realized QQQ, Bear9, and cash fractions to daily account state."""

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
    bear_value = values[present_bear].sum(axis=1) if present_bear else pd.Series(0.0, index=values.index)
    result["qqq_exposure_pct"] = (qqq_value / equity * 100.0).to_numpy()
    result["bear9_exposure_pct"] = (bear_value / equity * 100.0).to_numpy()
    result["cash_exposure_pct"] = (result["cash"].astype(float) / result["equity"].astype(float) * 100.0)
    return result
