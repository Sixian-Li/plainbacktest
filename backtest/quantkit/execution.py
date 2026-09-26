"""Explicit order execution policies used by every backtest adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal, Union

import numpy as np
import pandas as pd
from pybroker import PositionMode, PriceType, StrategyConfig
from pybroker.common import BarData
from pybroker.context import ExecContext


FillValue = Union[PriceType, Callable[[str, BarData], float]]
MAX_AFFORDABLE_REQUEST = Decimal("1e30")


@dataclass(frozen=True)
class ExplicitFillPolicy:
    """Requires an explicit bar field and applies symmetric adverse cost.

    ``cost_bps`` is applied to the fill price: buys pay more and sells receive
    less. This avoids PyBroker's default MIDDLE fill and also avoids its fee
    model allowing a fully invested buy to leave cash negative by the fee.
    """

    timing: Literal["open", "close"]
    cost_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.timing not in ("open", "close"):
            raise ValueError("Fill timing must be explicitly 'open' or 'close'.")
        if not np.isfinite(self.cost_bps) or self.cost_bps < 0:
            raise ValueError("cost_bps must be a finite non-negative number.")
        if self.cost_bps >= 10_000:
            raise ValueError("cost_bps must be below 10,000 bps.")

    @property
    def cost_rate(self) -> float:
        return self.cost_bps / 10_000.0

    def _base_price_type(self) -> PriceType:
        return PriceType.OPEN if self.timing == "open" else PriceType.CLOSE

    def _adverse_callable(self, side: Literal["buy", "sell"]) -> Callable[[str, BarData], float]:
        multiplier = 1.0 + self.cost_rate if side == "buy" else 1.0 - self.cost_rate
        field = self.timing

        def fill_price(_symbol: str, bar: BarData) -> float:
            values = getattr(bar, field)
            if values is None or not len(values):
                raise ValueError(f"Missing {field} data for explicit fill.")
            return float(values[-1]) * multiplier

        return fill_price

    def buy_fill(self) -> FillValue:
        return self._base_price_type() if self.cost_bps == 0 else self._adverse_callable("buy")

    def sell_fill(self) -> FillValue:
        return self._base_price_type() if self.cost_bps == 0 else self._adverse_callable("sell")

    def expected_fill(self, side: str, raw_price: float) -> float:
        if side == "buy":
            return raw_price * (1.0 + self.cost_rate)
        if side == "sell":
            return raw_price * (1.0 - self.cost_rate)
        raise ValueError(f"Unknown order side: {side!r}")


def make_strategy_config(
    policy: ExplicitFillPolicy,
    *,
    initial_cash: float = 100_000.0,
    return_signals: bool = False,
) -> StrategyConfig:
    """Builds the only supported PyBroker configuration for this project."""

    return StrategyConfig(
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
        # Explicit even though exit_on_last_bar is disabled. This prevents a
        # later config change from silently reintroducing MIDDLE.
        exit_cover_fill_price=policy.buy_fill(),
        exit_sell_fill_price=policy.sell_fill(),
        bars_per_year=252,
        return_signals=return_signals,
        round_test_result=False,
    )


def request_all_affordable_shares(ctx: ExecContext, policy: ExplicitFillPolicy) -> None:
    """Requests more shares than affordable so PyBroker clamps at fill price."""

    if ctx.long_pos() is not None:
        raise ValueError("Cannot submit all-in buy while already long.")
    ctx.buy_shares = MAX_AFFORDABLE_REQUEST
    ctx.buy_fill_price = policy.buy_fill()


def request_sell_all(ctx: ExecContext, policy: ExplicitFillPolicy) -> None:
    if ctx.long_pos() is None:
        raise ValueError("Cannot submit sell-all while flat.")
    ctx.sell_all_shares()
    ctx.sell_fill_price = policy.sell_fill()


def assert_orders_match_policy(
    orders: pd.DataFrame,
    bars: pd.DataFrame,
    policy: ExplicitFillPolicy,
    *,
    atol: float = 1e-9,
) -> None:
    """Proves every filled order used the declared bar field and cost."""

    if orders.empty:
        return
    order_frame = orders.reset_index().copy()
    if missing := {"symbol", "date", "type", "fill_price"}.difference(order_frame.columns):
        raise ValueError(f"orders miss required execution fields: {sorted(missing)}")
    price_frame = bars[["symbol", "date", policy.timing]].copy()
    price_frame["date"] = pd.to_datetime(price_frame["date"])
    if price_frame.duplicated(["symbol", "date"]).any():
        raise ValueError("bars contain duplicate symbol/date rows")
    order_frame["date"] = pd.to_datetime(order_frame["date"])
    merged = order_frame.merge(
        price_frame,
        on=["symbol", "date"],
        how="left",
        validate="many_to_one",
    )
    if merged[policy.timing].isna().any():
        missing_rows = merged.loc[
            merged[policy.timing].isna(), ["symbol", "date", "type"]
        ].head(10)
        raise AssertionError(f"orders have no matching {policy.timing} bar: {missing_rows.to_dict('records')}")
    if not merged["type"].isin(["buy", "sell"]).all():
        raise ValueError("orders contain unsupported side")
    multipliers = np.where(
        merged["type"].eq("buy"),
        1.0 + policy.cost_rate,
        1.0 - policy.cost_rate,
    )
    expected = merged[policy.timing].to_numpy(float) * multipliers
    actual = merged["fill_price"].to_numpy(float)
    matches = np.isclose(actual, expected, rtol=0.0, atol=atol)
    if not matches.all():
        offset = int(np.flatnonzero(~matches)[0])
        row = merged.iloc[offset]
        raise AssertionError(
            f"{row.symbol} {row.date} {row.type} filled at {row.fill_price}; "
            f"expected explicit {policy.timing} fill {expected[offset]}."
        )
