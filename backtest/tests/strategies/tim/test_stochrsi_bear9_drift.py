from __future__ import annotations

import unittest

import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.stochrsi_bear9_drift import (
    build_close_cross_state,
    build_drift_target_schedule,
)
from quantkit.trend_score_portfolio import (
    build_target_share_table,
    cross_check_portfolios,
    run_pybroker_portfolio,
    run_reference_portfolio,
)


def _prepared(values_12: list[float], values_61: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=len(values_12))
    return pd.DataFrame(
        {
            "date": dates,
            "stochrsi_12": values_12,
            "prior_stochrsi_12": pd.Series(values_12).shift(),
            "stochrsi_61": values_61,
            "prior_stochrsi_61": pd.Series(values_61).shift(),
        }
    )


def _panel(closes: dict[str, list[float]]) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=len(next(iter(closes.values()))))
    rows = []
    for symbol, values in closes.items():
        for date, close in zip(dates, values, strict=True):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": 100.0,
                    "high": max(100.0, close),
                    "low": min(100.0, close),
                    "close": close,
                    "volume": 1_000.0,
                }
            )
    return pd.DataFrame(rows)


def _flat_state(dates: pd.Series | pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ready": True,
            "is_long": False,
            "transition": "hold_flat",
        }
    )


def test_close_cross_requires_both_periods_on_same_close_and_starts_flat() -> None:
    prepared = _prepared(
        [0.10, 0.25, 0.10, 0.25, 0.90, 0.70],
        [0.10, 0.10, 0.10, 0.25, 0.90, 0.70],
    )
    state = build_close_cross_state(prepared, (12, 61))

    assert state["is_long"].tolist() == [False, False, False, True, True, False]
    assert state["transition"].tolist() == [
        "not_ready",
        "hold_flat",
        "hold_flat",
        "enter",
        "hold_long",
        "exit",
    ]


def test_exact_three_point_drift_does_not_rebalance_but_more_does() -> None:
    exact_panel = _panel({"QQQ": [100.0] * 4, "A": [100.0, 106.0, 106.0, 106.0], "B": [100.0, 94.0, 94.0, 94.0]})
    state = _flat_state(exact_panel["date"].drop_duplicates())
    common = dict(
        timing_state=state,
        case_id="BEAR",
        bear_weights={"A": 0.5, "B": 0.5},
        use_bear=True,
        drift_threshold=0.03,
        initial_cash=100_000.0,
        policy=ExplicitFillPolicy("open", 0.0),
    )
    exact = build_drift_target_schedule(price_panel=exact_panel, **common)
    assert exact["reason"].drop_duplicates().tolist() == ["initial_target"]

    over_panel = exact_panel.copy()
    over_panel.loc[(over_panel["symbol"] == "A") & (over_panel["date"] == over_panel["date"].drop_duplicates().iloc[1]), "close"] = 106.02
    over_panel.loc[(over_panel["symbol"] == "B") & (over_panel["date"] == over_panel["date"].drop_duplicates().iloc[1]), "close"] = 93.98
    over = build_drift_target_schedule(price_panel=over_panel, **common)
    assert over["reason"].drop_duplicates().tolist() == ["initial_target", "drift_rebalance"]
    drift_rows = over[over["reason"] == "drift_rebalance"]
    assert set(drift_rows["symbol"]) == {"A", "B", "QQQ"}
    assert dict(zip(drift_rows["symbol"], drift_rows["target_weight"], strict=True)) == {
        "A": 0.5,
        "B": 0.5,
        "QQQ": 0.0,
    }


def test_unavailable_bear_weight_stays_cash_until_listing() -> None:
    full = _panel({"QQQ": [100.0] * 4, "A": [100.0] * 4, "B": [100.0] * 4})
    first_date = full["date"].drop_duplicates().iloc[0]
    panel = full[~((full["symbol"] == "B") & (full["date"] == first_date))].copy()
    state = _flat_state(panel["date"].drop_duplicates())
    schedule = build_drift_target_schedule(
        state,
        panel,
        case_id="BEAR",
        bear_weights={"A": 0.6, "B": 0.4},
        use_bear=True,
        drift_threshold=0.03,
        initial_cash=100_000.0,
        policy=ExplicitFillPolicy("open", 0.0),
    )
    initial = schedule[schedule["reason"] == "initial_target"].set_index("symbol")
    assert initial.at["A", "target_weight"] == 0.6
    assert initial.at["B", "target_weight"] == 0.0
    listing = schedule[schedule["reason"] == "tradable_set_change"].set_index("symbol")
    assert listing.at["A", "target_weight"] == 0.6
    assert listing.at["B", "target_weight"] == 0.4


def test_generated_targets_reconcile_pybroker_and_independent_ledger() -> None:
    panel = _panel(
        {
            "QQQ": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "A": [100.0, 104.0, 108.0, 101.0, 98.0, 96.0],
            "B": [100.0, 96.0, 92.0, 99.0, 102.0, 104.0],
        }
    )
    dates = panel["date"].drop_duplicates().reset_index(drop=True)
    state = _flat_state(dates)
    state.loc[2:3, "is_long"] = True
    state.loc[2, "transition"] = "enter"
    state.loc[4, "transition"] = "exit"
    policy = ExplicitFillPolicy("open", 5.0)
    schedule = build_drift_target_schedule(
        state,
        panel,
        case_id="BEAR",
        bear_weights={"A": 0.5, "B": 0.5},
        use_bear=True,
        drift_threshold=0.03,
        initial_cash=100_000.0,
        policy=policy,
    )
    targets = build_target_share_table(
        panel,
        schedule,
        case_id="BEAR",
        initial_cash=100_000.0,
        policy=policy,
    )
    result, positions = run_pybroker_portfolio(
        panel,
        targets,
        initial_cash=100_000.0,
        policy=policy,
    )
    reference = run_reference_portfolio(
        panel,
        targets,
        initial_cash=100_000.0,
        policy=policy,
    )
    differences = cross_check_portfolios(result, positions, reference, tolerance=1e-6)
    assert max(differences.values()) < 1e-6


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for function in (
        test_close_cross_requires_both_periods_on_same_close_and_starts_flat,
        test_exact_three_point_drift_does_not_rebalance_but_more_does,
        test_unavailable_bear_weight_stays_cash_until_listing,
        test_generated_targets_reconcile_pybroker_and_independent_ledger,
    ):
        suite.addTest(unittest.FunctionTestCase(function))
    return suite
