from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.metrics import pybroker_daily_state
from quantkit.sma_recovery_probation import (
    CONFIRMED_SMA_SELL,
    FORCED_PROBATION_FAILURE,
    FORCED_REBUY,
    ORDINARY_PROBATION_FAILURE,
    ORDINARY_SMA_BUY,
    RecoveryState,
    SmaRecoveryProbationSpec,
    decide_after_close,
    run_pybroker_sma_recovery_probation,
    run_reference_sma_recovery_probation,
)
from quantkit.sma_recovery_probation_search import (
    build_recovery_grid_cases,
    screen_recovery_probation_cases,
    screen_recovery_probation_cases_reference,
)
from scripts.run_intraday_sma_backtest import cross_check


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015/experiment.json"
)


def market_from_closes(
    closes: list[float],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    if opens is None:
        open_ = np.r_[close[0], close[:-1]]
    else:
        open_ = np.asarray(opens, dtype=float)
    if highs is None:
        high = np.maximum(open_, close) + 0.4
    else:
        high = np.asarray(highs, dtype=float)
    if lows is None:
        low = np.minimum(open_, close) - 0.4
    else:
        low = np.asarray(lows, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2020-01-02", periods=len(close)),
            "symbol": "QQQ",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(len(close), 1_000_000.0),
        }
    )


def oscillating_market(periods: int = 140) -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 100.0 + 7.0 * np.sin(index / 4.1) + 0.04 * index
    open_ = np.r_[close[0], close[:-1]] + 0.3 * np.sin(index / 2.7)
    high = np.maximum(open_, close) + 1.2
    low = np.minimum(open_, close) - 1.2
    return market_from_closes(
        close.tolist(),
        opens=open_.tolist(),
        highs=high.tolist(),
        lows=low.tolist(),
    )


class RecoveryProbationTimingTest(unittest.TestCase):
    def test_forced_probation_adopts_ordinary_boundary_after_true_long_cross(self) -> None:
        spec = SmaRecoveryProbationSpec(4, 2, 3.0, 5.0)
        row = pd.Series(
            {
                "date": pd.Timestamp("2020-02-03"),
                "prior_close": 99.0,
                "close": 103.0,
                "prior_sma_4": 100.0,
                "sma_4": 102.0,
                "sma_2": 105.0,
            }
        )
        next_row = pd.Series(
            {
                "date": pd.Timestamp("2020-02-04"),
                "prior_sum_2": 103.0,
            }
        )
        decision = decide_after_close(
            row,
            next_row,
            spec,
            state=RecoveryState.FORCED_PROBATION,
            bear_sell_anchor=100.0,
            c_available=False,
        )
        self.assertEqual(decision.state, RecoveryState.ORDINARY_PROBATION)
        self.assertEqual(decision.state_event, "forced_to_ordinary_probation")
        self.assertIsNone(decision.plan)

    def test_forced_failure_has_priority_over_same_close_confirmation(self) -> None:
        spec = SmaRecoveryProbationSpec(4, 2, 3.0, 5.0)
        row = pd.Series(
            {
                "date": pd.Timestamp("2020-02-03"),
                "prior_close": 101.0,
                "close": 99.0,
                "prior_sma_4": 100.0,
                "sma_4": 98.0,
                "sma_2": 98.0,
            }
        )
        next_row = pd.Series(
            {
                "date": pd.Timestamp("2020-02-04"),
                "prior_sum_2": 99.0,
            }
        )
        decision = decide_after_close(
            row,
            next_row,
            spec,
            state=RecoveryState.FORCED_PROBATION,
            bear_sell_anchor=100.0,
            c_available=False,
        )
        self.assertEqual(decision.state, RecoveryState.FORCED_PROBATION)
        self.assertIsNotNone(decision.plan)
        assert decision.plan is not None
        self.assertEqual(decision.plan.signal, FORCED_PROBATION_FAILURE)

    def test_intraday_wick_does_not_buy_and_close_cross_fills_next_open(self) -> None:
        market = market_from_closes(
            [10.0, 10.0, 10.0, 9.0, 9.2, 10.5, 9.0, 8.7],
            opens=[10.0, 10.0, 10.0, 10.0, 9.0, 9.2, 11.0, 8.8],
            highs=[10.4, 10.4, 10.4, 10.2, 12.0, 10.8, 11.2, 9.0],
            lows=[9.6, 9.6, 9.6, 8.8, 8.9, 9.1, 8.8, 8.5],
        )
        spec = SmaRecoveryProbationSpec(
            buy_window=4,
            sell_window=2,
            forced_rebuy_pct=3.0,
            cost_bps=5.0,
        )
        result = run_reference_sma_recovery_probation(
            market,
            spec,
            analysis_start=market.iloc[4]["date"],
            analysis_end=market.iloc[-1]["date"],
        )
        self.assertEqual(result.orders.iloc[0]["primary_signal"], ORDINARY_SMA_BUY)
        self.assertEqual(result.orders.iloc[0]["date"], market.iloc[6]["date"])
        self.assertEqual(result.orders.iloc[0]["fill_source"], "next_open")
        self.assertAlmostEqual(result.orders.iloc[0]["raw_fill_price"], 11.0)
        self.assertNotIn(market.iloc[4]["date"], set(result.orders["date"]))

    def test_failed_ordinary_recovery_exits_at_next_open(self) -> None:
        market = market_from_closes(
            [10.0, 10.0, 10.0, 9.0, 9.2, 10.5, 9.0, 8.7],
            opens=[10.0, 10.0, 10.0, 10.0, 9.0, 9.2, 11.0, 8.8],
            highs=[10.4, 10.4, 10.4, 10.2, 12.0, 10.8, 11.2, 9.0],
            lows=[9.6, 9.6, 9.6, 8.8, 8.9, 9.1, 8.8, 8.5],
        )
        result = run_reference_sma_recovery_probation(
            market,
            SmaRecoveryProbationSpec(4, 2, 3.0, 5.0),
            analysis_start=market.iloc[4]["date"],
            analysis_end=market.iloc[-1]["date"],
        )
        self.assertEqual(result.orders.iloc[1]["primary_signal"], ORDINARY_PROBATION_FAILURE)
        self.assertEqual(result.orders.iloc[1]["date"], market.iloc[7]["date"])
        self.assertEqual(result.orders.iloc[1]["fill_source"], "next_open")
        self.assertAlmostEqual(result.orders.iloc[1]["raw_fill_price"], 8.8)

    def test_probation_confirms_then_short_sma_sells_intraday(self) -> None:
        market = market_from_closes(
            [10.0, 10.0, 10.0, 9.0, 9.2, 10.5, 11.0, 10.7],
            opens=[10.0, 10.0, 10.0, 10.0, 9.0, 9.2, 10.8, 11.2],
            highs=[10.4, 10.4, 10.4, 10.2, 9.4, 10.8, 11.2, 11.3],
            lows=[9.6, 9.6, 9.6, 8.8, 8.9, 9.1, 10.6, 10.5],
        )
        result = run_reference_sma_recovery_probation(
            market,
            SmaRecoveryProbationSpec(4, 2, 3.0, 5.0),
            analysis_start=market.iloc[4]["date"],
            analysis_end=market.iloc[-1]["date"],
        )
        self.assertEqual(result.orders.iloc[0]["primary_signal"], ORDINARY_SMA_BUY)
        self.assertEqual(result.orders.iloc[1]["primary_signal"], CONFIRMED_SMA_SELL)
        self.assertEqual(result.orders.iloc[1]["date"], market.iloc[7]["date"])
        self.assertEqual(result.orders.iloc[1]["fill_source"], "intraday_trigger")
        self.assertAlmostEqual(result.orders.iloc[1]["raw_fill_price"], 11.0)

    def test_forced_rebuy_failure_does_not_refresh_or_repeat_c(self) -> None:
        market = market_from_closes(
            [10.0, 10.0, 10.0, 9.0, 9.2, 10.5, 11.0, 10.7, 10.8, 10.5, 10.7],
            opens=[10.0, 10.0, 10.0, 10.0, 9.0, 9.2, 10.8, 11.2, 10.8, 10.6, 10.5],
            highs=[10.4, 10.4, 10.4, 10.2, 9.4, 10.8, 11.2, 11.3, 11.5, 10.8, 11.0],
            lows=[9.6, 9.6, 9.6, 8.8, 8.9, 9.1, 10.6, 10.5, 10.6, 10.3, 10.4],
        )
        result = run_reference_sma_recovery_probation(
            market,
            SmaRecoveryProbationSpec(4, 2, 3.0, 5.0),
            analysis_start=market.iloc[4]["date"],
            analysis_end=market.iloc[-1]["date"],
        )
        signals = result.orders["primary_signal"].tolist()
        self.assertEqual(signals.count(FORCED_REBUY), 1)
        self.assertEqual(signals.count(FORCED_PROBATION_FAILURE), 1)
        self.assertEqual(signals[-1], FORCED_PROBATION_FAILURE)


class RecoveryProbationGridTest(unittest.TestCase):
    def test_frozen_experiment_builds_1444_unique_cases(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        cases = build_recovery_grid_cases(config["parameters"])
        self.assertEqual(len(cases), 1_444)
        self.assertFalse(cases.duplicated(["buy_window", "sell_window"]).any())
        self.assertEqual(cases.iloc[0]["case_id"], "CASE_0001")
        self.assertEqual(cases.iloc[-1]["case_id"], "CASE_1444")

    def test_two_compiled_ledgers_match_every_toy_case(self) -> None:
        market = oscillating_market()
        cases = pd.DataFrame(
            [
                {"case_id": "CASE_0001", "buy_window": 4, "sell_window": 2},
                {"case_id": "CASE_0002", "buy_window": 4, "sell_window": 3},
                {"case_id": "CASE_0003", "buy_window": 5, "sell_window": 2},
                {"case_id": "CASE_0004", "buy_window": 5, "sell_window": 3},
            ]
        )
        kwargs = {
            "analysis_start": market.iloc[5]["date"],
            "analysis_end": market.iloc[-1]["date"],
            "cost_bps": 5.0,
            "initial_cash": 100_000.0,
            "forced_rebuy_pct": 3.0,
            "pbo_block_count": 4,
        }
        primary = screen_recovery_probation_cases(market, cases, **kwargs)
        reference = screen_recovery_probation_cases_reference(market, cases, **kwargs)
        columns = [column for column in primary.metrics if column not in {"case_id", "identifiable"}]
        np.testing.assert_allclose(
            primary.metrics[columns].to_numpy(float),
            reference.metrics[columns].to_numpy(float),
            rtol=0,
            atol=1e-9,
            equal_nan=True,
        )


class RecoveryProbationLedgerTest(unittest.TestCase):
    def test_pybroker_matches_independent_reference(self) -> None:
        market = oscillating_market()
        start = pd.Timestamp(market.iloc[5]["date"])
        end = pd.Timestamp(market.iloc[-1]["date"])
        spec = SmaRecoveryProbationSpec(5, 3, 3.0, 5.0)
        pybroker_run = run_pybroker_sma_recovery_probation(
            market,
            spec,
            analysis_start=start,
            analysis_end=end,
        )
        engine = market[
            (market["date"] >= pybroker_run.engine_start) & (market["date"] <= end)
        ].copy()
        actual = pybroker_daily_state(pybroker_run.pybroker_result, engine)
        actual = actual[actual["date"] >= start].reset_index(drop=True)
        reference = run_reference_sma_recovery_probation(
            market,
            spec,
            analysis_start=start,
            analysis_end=end,
        )
        differences = cross_check(
            pybroker_run.pybroker_result,
            actual,
            reference,
            tolerance=1e-6,
        )
        self.assertLessEqual(max(differences.values(), default=0.0), 1e-6)
        self.assertLessEqual(reference.orders.groupby("date").size().max(), 1)


if __name__ == "__main__":
    unittest.main()
