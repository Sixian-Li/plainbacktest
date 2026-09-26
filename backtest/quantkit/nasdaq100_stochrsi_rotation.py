"""Point-in-time Nasdaq-100 rotation driven by the frozen Strategy1 weight score."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import solve_close_for_rsi, wilder_components
from quantkit.execution import ExplicitFillPolicy
from quantkit.stochrsi_scaled_pools import ScaledPoolSpec


FULL_EQUAL = "FULL_EQUAL"
BEAR9_FLOOR10 = "BEAR9_FLOOR10"
ALLOCATION_MODES = (FULL_EQUAL, BEAR9_FLOOR10)


@dataclass(frozen=True)
class Strategy1ScoreResult:
    daily: pd.DataFrame
    diagnostics: dict[str, int | float]


def _validated_single_asset(data: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "high", "low", "close", "volume"}
    if missing := required.difference(data.columns):
        raise ValueError(f"price data misses {sorted(missing)}")
    rows = data.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows = rows.sort_values("date", kind="stable").reset_index(drop=True)
    if rows.empty or rows["symbol"].nunique() != 1 or rows["date"].duplicated().any():
        raise ValueError("one non-empty symbol with unique dates is required")
    prices = rows[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC must be finite and positive")
    if (
        (rows["low"].to_numpy(float) > np.minimum(rows["open"], rows["close"]).to_numpy(float))
        | (rows["high"].to_numpy(float) < np.maximum(rows["open"], rows["close"]).to_numpy(float))
    ).any():
        raise ValueError("invalid OHLC envelope")
    return rows


def prepare_strategy1_score_inputs(data: pd.DataFrame, spec: ScaledPoolSpec | None = None) -> pd.DataFrame:
    """Compute only the causal indicator state used by frozen D0_F0_S0.

    The original reusable mother prepares several unused theoretical trigger
    series with row-wise solvers.  This rotation needs only raw StochRSI 42/100
    and the rare fast-drop 100-period theoretical price, so that price is solved
    lazily in the state machine below.  The resulting score is regression-tested
    against the original mother ledger.
    """

    spec = spec or ScaledPoolSpec()
    if spec != ScaledPoolSpec():
        raise ValueError("the cross-sectional score engine only accepts frozen ScaledPoolSpec()")
    rows = _validated_single_asset(data)
    close = rows["close"].astype(float)
    rows["prior_close"] = close.shift(1)
    for period in spec.periods:
        components = wilder_components(close, int(period))
        rsi = components["rsi"]
        rows[f"prior_average_gain_{period}"] = components["average_gain"].shift(1)
        rows[f"prior_average_loss_{period}"] = components["average_loss"].shift(1)
        rows[f"prior_rsi_low_{period}"] = rsi.shift(1).rolling(
            int(period) - 1, min_periods=int(period) - 1
        ).min()
        rows[f"prior_rsi_high_{period}"] = rsi.shift(1).rolling(
            int(period) - 1, min_periods=int(period) - 1
        ).max()
        rolling_low = rsi.rolling(int(period), min_periods=int(period)).min()
        rolling_high = rsi.rolling(int(period), min_periods=int(period)).max()
        span = rolling_high - rolling_low
        stoch = (rsi - rolling_low) / span
        stoch.loc[span.eq(0) & rolling_low.notna()] = 0.5
        rows[f"stochrsi_{period}"] = stoch
        rows[f"prior_stochrsi_{period}"] = stoch.shift(1)
    return rows


def _fast_drop_price(row: Any, period: int, threshold: float) -> float:
    low = float(getattr(row, f"prior_rsi_low_{period}"))
    high = float(getattr(row, f"prior_rsi_high_{period}"))
    target = low + threshold * (high - low)
    values = np.asarray(
        [
            low,
            high,
            target,
            float(row.prior_close),
            float(getattr(row, f"prior_average_gain_{period}")),
            float(getattr(row, f"prior_average_loss_{period}")),
        ],
        dtype=float,
    )
    if not np.isfinite(values).all() or not 0 < target < 100:
        return float("nan")
    return solve_close_for_rsi(
        float(row.prior_close),
        float(getattr(row, f"prior_average_gain_{period}")),
        float(getattr(row, f"prior_average_loss_{period}")),
        period=period,
        target_rsi=target,
    )


def run_strategy1_score(
    prepared: pd.DataFrame,
    *,
    initial_cash: float = 100_000.0,
    spec: ScaledPoolSpec | None = None,
) -> Strategy1ScoreResult:
    """Run the exact D0_F0_S0 state while retaining only score-relevant state."""

    spec = spec or ScaledPoolSpec()
    if spec != ScaledPoolSpec():
        raise ValueError("the cross-sectional score engine only accepts frozen ScaledPoolSpec()")
    required = {
        "date",
        "symbol",
        "close",
        "stochrsi_42",
        "stochrsi_100",
        "prior_stochrsi_42",
        "prior_stochrsi_100",
        "prior_close",
        "prior_average_gain_100",
        "prior_average_loss_100",
        "prior_rsi_low_100",
        "prior_rsi_high_100",
    }
    if missing := required.difference(prepared.columns):
        raise ValueError(f"prepared score input misses {sorted(missing)}")
    if not np.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")

    cash = float(initial_cash)
    shares = 0.0
    pool_a = 0.0
    pool_b = 0.0
    mechanism_a_armed = False
    buy_a: float | None = None
    buy_b: float | None = None
    b2_count = 0
    rows: list[dict[str, Any]] = []
    contribution_total = 0.0
    contribution_count = 0
    fast_drop_count = 0
    sale_count = 0
    buy_count = 0

    def reset_buy_cycle() -> None:
        nonlocal buy_a, buy_b, b2_count
        buy_a = None
        buy_b = None
        b2_count = 0

    def contribute(amount: float) -> None:
        nonlocal cash, contribution_total, contribution_count
        if amount <= 1e-12:
            return
        cash += amount
        contribution_total += amount
        contribution_count += 1

    def buy(amount: float, price: float) -> float:
        nonlocal cash, shares, buy_count
        amount = min(float(amount), cash)
        if amount <= 1e-12:
            return 0.0
        cash -= amount
        shares += amount / price
        buy_count += 1
        return amount

    def sell(quantity: float, price: float) -> float:
        nonlocal cash, shares, sale_count
        quantity = min(float(quantity), shares)
        if quantity <= 1e-12:
            return 0.0
        cash += quantity * price
        shares -= quantity
        if shares <= 1e-10:
            shares = 0.0
        reset_buy_cycle()
        sale_count += 1
        return quantity

    for row in prepared.itertuples(index=False):
        date = pd.Timestamp(row.date)
        close = float(row.close)
        s42 = float(row.stochrsi_42)
        s100 = float(row.stochrsi_100)
        prior42 = float(row.prior_stochrsi_42)
        prior100 = float(row.prior_stochrsi_100)
        equity_before = cash + shares * close
        pretrade_weight = shares * close / equity_before if equity_before > 0 else 0.0
        sale_happened = False
        fast_drop_sale = False
        action = "NONE"

        if mechanism_a_armed:
            fast_drop_sale = prior100 > spec.fast_prior and s100 < spec.fast_current
            normal_downcross = prior100 >= spec.a_zone_low and s100 < spec.a_zone_low
            if fast_drop_sale or normal_downcross:
                price = _fast_drop_price(row, 100, spec.fast_current) if fast_drop_sale else close
                if not np.isfinite(price) or price <= 0:
                    raise ValueError(
                        f"invalid Strategy1 fast-drop price for {row.symbol} on {date.date()}: {price}"
                    )
                quantity = pool_a + spec.a_remaining_sale * max(shares - pool_a, 0.0)
                sold = sell(quantity, price)
                pool_a = 0.0
                mechanism_a_armed = False
                sale_happened = sold > 0
                action = "SELL_A_FAST_030" if fast_drop_sale else "SELL_A_DOWNCROSS_050"
                if fast_drop_sale and sale_happened:
                    fast_drop_count += 1
            elif spec.enable_a_daily_decay and spec.a_zone_low <= s100 <= spec.a_zone_high:
                sold = sell(pool_a * spec.a_daily_sale, close)
                pool_a = max(pool_a - sold, 0.0)
                sale_happened = sold > 0
                action = "SELL_A_DAILY_DECAY" if sale_happened else "A_ZONE_EMPTY"

        if not sale_happened and s100 > spec.a_arm:
            if not mechanism_a_armed:
                mechanism_a_armed = True
                action = "ARM_A"
            pool_b = 0.0
            pool_a = min(shares, pool_a + spec.a_daily_add * shares)
            action = "ADD_POOL_A" if shares > 0 else action
        elif spec.enable_pool_b and not mechanism_a_armed and not sale_happened:
            b_downcross = prior42 >= spec.b_downcross and s42 < spec.b_downcross
            if b_downcross:
                sold = sell(min(pool_b, spec.b_sale_cap * shares), close)
                pool_b = 0.0
                sale_happened = sold > 0
                action = "SELL_B_DOWNCROSS_070" if sale_happened else "CLEAR_POOL_B_EMPTY"
            elif pretrade_weight > spec.b_crowded_weight and s42 > spec.b_arm:
                pool_b = min(shares, pool_b + spec.b_daily_add * max(shares - pool_b, 0.0))
                action = "ADD_POOL_B"

        may_buy = not sale_happened or fast_drop_sale
        if may_buy:
            b2_signal = (s42 < 0.05 and s100 < 0.01) or (s42 < 0.01 and s100 < 0.05)
            b1_signal = s42 < spec.b1_threshold and s100 < spec.b1_threshold
            if b2_signal:
                b2_count += 1
                fraction = spec.b2_cash_fractions[
                    min(b2_count - 1, len(spec.b2_cash_fractions) - 1)
                ]
                desired = cash * fraction
                if spec.enable_b2_floor and b2_count >= 5 and buy_b is not None:
                    desired = max(desired, spec.b2_floor_fraction * buy_b)
                shortfall = max(desired - cash, 0.0)
                contribute(shortfall)
                actual = buy(desired, close)
                if b2_count == 4:
                    buy_b = actual
                if actual > 0:
                    action = f"{action}+BUY_B2" if action != "NONE" else "BUY_B2"
            elif b1_signal:
                desired = cash * spec.b1_cash_fraction
                if spec.enable_b1_floor and buy_a is not None:
                    desired = max(desired, spec.b1_floor_fraction * buy_a)
                shortfall = max(desired - cash, 0.0)
                contribute(shortfall)
                actual = buy(desired, close)
                if buy_a is None and actual > 0:
                    buy_a = actual
                if actual > 0:
                    action = f"{action}+BUY_B1" if action != "NONE" else "BUY_B1"

        if pool_a > shares + 1e-8 or pool_b > shares + 1e-8 or min(pool_a, pool_b, cash, shares) < -1e-8:
            raise AssertionError(f"invalid Strategy1 score state on {date.date()}")
        equity = cash + shares * close
        weight = shares * close / equity if equity > 0 else 0.0
        rows.append(
            {
                "date": date,
                "symbol": str(row.symbol),
                "close": close,
                "strategy1_weight": weight,
                "cash": cash,
                "shares": shares,
                "equity": equity,
                "pool_a_shares": pool_a,
                "pool_b_shares": pool_b,
                "mechanism_a_armed": int(mechanism_a_armed),
                "b2_count": b2_count,
                "action": action,
            }
        )
    return Strategy1ScoreResult(
        daily=pd.DataFrame(rows),
        diagnostics={
            "bars": len(rows),
            "buy_count": buy_count,
            "sale_count": sale_count,
            "fast_drop_sale_count": fast_drop_count,
            "external_contribution_count": contribution_count,
            "external_contribution_total": float(contribution_total),
        },
    )


def shifted_membership_bounds(
    intervals: pd.DataFrame,
    sessions: Sequence[pd.Timestamp] | pd.DatetimeIndex,
) -> pd.DataFrame:
    """Shift every observed membership interval one XNYS session forward."""

    required = {"security_id", "effective_start", "effective_end"}
    if missing := required.difference(intervals.columns):
        raise ValueError(f"membership intervals miss {sorted(missing)}")
    calendar = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().sort_values().unique()
    positions = {day: offset for offset, day in enumerate(calendar)}
    rows: list[dict[str, Any]] = []
    for item in intervals.itertuples(index=False):
        start = pd.Timestamp(item.effective_start).normalize()
        end = pd.Timestamp(item.effective_end).normalize()
        if start not in positions or end not in positions:
            raise ValueError(f"membership boundary is outside XNYS calendar: {start}..{end}")
        shifted_start_index = positions[start] + 1
        shifted_end_index = positions[end] + 1
        if shifted_start_index >= len(calendar):
            continue
        rows.append(
            {
                "security_id": str(item.security_id),
                "known_start": calendar[shifted_start_index],
                "known_end": calendar[min(shifted_end_index, len(calendar) - 1)],
            }
        )
    return pd.DataFrame(rows, columns=["security_id", "known_start", "known_end"])


def membership_flags_for_dates(
    dates: pd.Series,
    bounds: pd.DataFrame,
    security_id: str,
) -> np.ndarray:
    values = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    flags = np.zeros(len(values), dtype=bool)
    selected = bounds[bounds["security_id"] == security_id]
    for item in selected.itertuples(index=False):
        flags |= (values >= pd.Timestamp(item.known_start)) & (values <= pd.Timestamp(item.known_end))
    return flags


def rank_eligible_scores(
    scores: pd.DataFrame,
    calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    *,
    threshold: float = 0.90,
    maximum_selected: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return deterministic top selections and one daily count audit."""

    required = {"date", "security_id", "display_ticker", "strategy1_weight", "in_universe"}
    if missing := required.difference(scores.columns):
        raise ValueError(f"score table misses {sorted(missing)}")
    if not 0 < threshold < 1 or maximum_selected < 1:
        raise ValueError("invalid ranking threshold or maximum")
    rows = scores.copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.normalize()
    rows["eligible"] = rows["in_universe"].astype(bool) & (
        rows["strategy1_weight"].astype(float) > threshold
    )
    eligible = rows[rows["eligible"]].sort_values(
        ["date", "strategy1_weight", "security_id"],
        ascending=[True, False, True],
        kind="stable",
    )
    eligible["rank"] = eligible.groupby("date").cumcount() + 1
    selected = eligible[eligible["rank"] <= maximum_selected].copy().reset_index(drop=True)
    counts = eligible.groupby("date").size().rename("eligible_count")
    selected_counts = selected.groupby("date").size().rename("selected_count")
    audit = pd.DataFrame({"date": pd.DatetimeIndex(pd.to_datetime(calendar)).normalize()})
    audit["eligible_count"] = audit["date"].map(counts).fillna(0).astype(int)
    audit["selected_count"] = audit["date"].map(selected_counts).fillna(0).astype(int)
    if (audit["selected_count"] > maximum_selected).any():
        raise AssertionError("selected count exceeds maximum")
    return selected, audit


def rebalance_dates(
    calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    interval: int,
) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().sort_values().unique()
    if interval < 1:
        raise ValueError("rebalance interval must be positive")
    return dates[:: int(interval)]


def case_definitions(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    intervals = [int(value) for value in parameters["rebalance_intervals_trading_days"]]
    if intervals != [1, 2, 3, 5]:
        raise AssertionError("rebalance interval grid changed")
    cases = [
        {
            "case_id": f"RB{interval:02d}_{mode}",
            "rebalance_days": interval,
            "allocation_mode": mode,
        }
        for mode in ALLOCATION_MODES
        for interval in intervals
    ]
    expected = list(parameters["formal_case_ids_per_cost"])
    if [item["case_id"] for item in cases] != expected:
        raise AssertionError("formal case order changed")
    if len(cases) != int(parameters["formal_case_count_per_cost"]):
        raise AssertionError("formal case count changed")
    return cases


def build_target_weights(
    selected: pd.DataFrame,
    calendar: Sequence[pd.Timestamp] | pd.DatetimeIndex,
    *,
    case_id: str,
    rebalance_days: int,
    allocation_mode: str,
    bear_weights: Mapping[str, float],
    bear_available: Mapping[str, pd.Series],
) -> pd.DataFrame:
    """Create a sparse complete target schedule at every rebalance Close."""

    if allocation_mode not in ALLOCATION_MODES:
        raise ValueError(f"unknown allocation mode: {allocation_mode}")
    weights = pd.Series({str(key): float(value) for key, value in bear_weights.items()})
    if (weights < 0).any() or not np.isclose(float(weights.sum()), 1.0, atol=1e-12):
        raise ValueError("Bear9 weights must be non-negative and sum to one")
    chosen = selected.copy()
    chosen["date"] = pd.to_datetime(chosen["date"]).dt.normalize()
    grouped = {date: frame for date, frame in chosen.groupby("date")}
    rows: list[dict[str, Any]] = []
    for date in rebalance_dates(calendar, rebalance_days):
        date_row_start = len(rows)
        frame = grouped.get(date, chosen.iloc[0:0])
        count = len(frame)
        if count > 20:
            raise AssertionError("selection exceeds top20")
        if allocation_mode == FULL_EQUAL:
            stock_weight = 1.0 / count if count else 0.0
            bear_sleeve = 0.0
        elif count >= 10:
            stock_weight = 1.0 / count
            bear_sleeve = 0.0
        else:
            stock_weight = 0.10
            bear_sleeve = 1.0 - count / 10.0
        for item in frame.itertuples(index=False):
            rows.append(
                {
                    "case_id": case_id,
                    "date": date,
                    "instrument_id": str(item.security_id),
                    "asset_type": "nasdaq100_member",
                    "display_ticker": str(item.display_ticker),
                    "target_weight": stock_weight,
                    "strategy1_weight": float(item.strategy1_weight),
                    "rank": int(item.rank),
                    "eligible_count": int(count),
                    "bear_sleeve_target": bear_sleeve,
                }
            )
        available_bear_total = 0.0
        for symbol, member_weight in weights.items():
            availability = bear_available.get(symbol)
            available = bool(availability.get(date, False)) if availability is not None else False
            target = bear_sleeve * float(member_weight) if available else 0.0
            if target <= 0:
                continue
            available_bear_total += target
            rows.append(
                {
                    "case_id": case_id,
                    "date": date,
                    "instrument_id": f"BEAR9__{symbol}",
                    "asset_type": "bear9",
                    "display_ticker": symbol,
                    "target_weight": target,
                    "strategy1_weight": np.nan,
                    "rank": np.nan,
                    "eligible_count": int(count),
                    "bear_sleeve_target": bear_sleeve,
                }
            )
        if len(rows) == date_row_start:
            rows.append(
                {
                    "case_id": case_id,
                    "date": date,
                    "instrument_id": "CASH",
                    "asset_type": "cash_sentinel",
                    "display_ticker": "CASH",
                    "target_weight": 0.0,
                    "strategy1_weight": np.nan,
                    "rank": np.nan,
                    "eligible_count": int(count),
                    "bear_sleeve_target": bear_sleeve,
                }
            )
        total = stock_weight * count + available_bear_total
        if total > 1.0 + 1e-12:
            raise AssertionError("target weights exceed long-only budget")
    columns = [
        "case_id",
        "date",
        "instrument_id",
        "asset_type",
        "display_ticker",
        "target_weight",
        "strategy1_weight",
        "rank",
        "eligible_count",
        "bear_sleeve_target",
    ]
    return pd.DataFrame(rows, columns=columns)


def build_rotation_target_share_table(
    price_panel: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    case_id: str,
    initial_cash: float,
    policy: ExplicitFillPolicy,
) -> pd.DataFrame:
    """Close-size next-Open targets while freezing temporarily unavailable assets."""

    required_panel = {
        "date",
        "symbol",
        "open",
        "close",
        "synthetic_bar",
        "terminal_settlement_proxy",
    }
    if missing := required_panel.difference(price_panel.columns):
        raise ValueError(f"price panel misses {sorted(missing)}")
    panel = price_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("price panel contains duplicate instrument dates")
    dates = pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    opens = panel.pivot(index="date", columns="symbol", values="open").reindex(dates)
    closes = panel.pivot(index="date", columns="symbol", values="close").reindex(dates)
    synthetic = (
        panel.pivot(index="date", columns="symbol", values="synthetic_bar")
        .reindex(index=dates, columns=closes.columns)
        .fillna(1)
        .astype(bool)
    )
    terminal = (
        panel.pivot(index="date", columns="symbol", values="terminal_settlement_proxy")
        .reindex(index=dates, columns=closes.columns)
        .fillna(0)
        .astype(bool)
    )
    requested = targets[targets["case_id"] == case_id].copy()
    requested["date"] = pd.to_datetime(requested["date"]).dt.normalize()
    target = (
        requested.pivot(index="date", columns="symbol", values="target_weight")
        .reindex(columns=closes.columns)
    )
    signal_to_execution = {dates[index]: dates[index + 1] for index in range(len(dates) - 1)}
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
        has_signal_price = signal_close.notna()
        weighted = has_signal_price & weights.notna()
        desired.loc[weighted] = (
            equity
            * weights.loc[weighted].astype(float)
            / signal_close.loc[weighted].astype(float)
        )
        signal_internal_gap = synthetic.loc[signal_date] & ~terminal.loc[signal_date]
        execution_internal_gap = synthetic.loc[execution_date] & ~terminal.loc[execution_date]
        deferred = signal_internal_gap | execution_internal_gap
        deferred_change = deferred & ((desired - current_shares).abs() > 1e-12)
        desired.loc[deferred] = current_shares.loc[deferred]
        terminal_buy = terminal.loc[execution_date] & (desired > current_shares + 1e-12)
        deferred_change |= terminal_buy
        desired.loc[terminal_buy] = current_shares.loc[terminal_buy]

        execution_open = opens.loc[execution_date]
        sell_delta = (current_shares - desired).clip(lower=0)
        terminal_sell = terminal.loc[execution_date] & (sell_delta > 1e-12)
        for symbol in sorted(closes.columns):
            shares = float(sell_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("sell", float(execution_open[symbol]))
            if not np.isfinite(fill):
                raise ValueError(f"{symbol} has no execution Open on {execution_date.date()}")
            cash += shares * fill
            current_shares[symbol] -= shares
        buy_delta = (desired - current_shares).clip(lower=0)
        for symbol in sorted(closes.columns):
            shares = float(buy_delta[symbol])
            if shares <= 1e-12:
                continue
            fill = policy.expected_fill("buy", float(execution_open[symbol]))
            if not np.isfinite(fill):
                raise ValueError(f"{symbol} has no execution Open on {execution_date.date()}")
            filled = min(shares, max(cash, 0.0) / fill)
            cash -= filled * fill
            current_shares[symbol] += filled
        for symbol in closes.columns:
            rows.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "target_weight": float(weights.get(symbol, 0.0))
                    if pd.notna(weights.get(symbol, np.nan))
                    else 0.0,
                    "target_shares": float(desired[symbol]),
                    "execution_deferred_unavailable": int(deferred_change[symbol]),
                    "terminal_settlement_execution": int(terminal_sell[symbol]),
                }
            )
    return pd.DataFrame(rows)
