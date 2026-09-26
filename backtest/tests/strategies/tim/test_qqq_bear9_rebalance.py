from __future__ import annotations

import json
import unittest
from pathlib import Path

import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.qqq_bear9_rebalance import (
    BEAR_CASH,
    BEAR_RESIDUAL,
    BEAR_ZERO_ONLY,
    bear_sleeve_weight,
    build_target_weight_schedule,
    case_definitions,
    classify_layered_weight,
)
from quantkit.trend_score_portfolio import (
    build_target_share_table,
    cross_check_portfolios,
    run_pybroker_portfolio,
    run_reference_portfolio,
)


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness/experiment.json"
)


def panel(dates: pd.DatetimeIndex, b_starts: int = 0) -> pd.DataFrame:
    rows = []
    for symbol, base, start in (("QQQ", 100.0, 0), ("A", 50.0, 0), ("B", 30.0, b_starts)):
        for offset, date in enumerate(dates[start:], start=start):
            close = base + offset
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000,
                }
            )
    return pd.DataFrame(rows)


def state(dates: pd.DatetimeIndex, weights: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": dates, "qqq_weight": weights, "zone": [f"z{value}" for value in weights]}
    )


class LayeredStateTest(unittest.TestCase):
    def test_frozen_four_level_classification(self) -> None:
        self.assertEqual(classify_layered_weight(120, 110, 100), (1.0, "above_fast"))
        self.assertEqual(classify_layered_weight(108, 110, 100), (0.7, "upper_half"))
        self.assertEqual(classify_layered_weight(102, 110, 100), (0.3, "lower_half"))
        self.assertEqual(classify_layered_weight(99, 110, 100), (0.0, "at_or_below_slow"))
        self.assertEqual(classify_layered_weight(120, 100, 110), (0.0, "slow_not_below_fast"))

    def test_equality_boundaries_are_conservative_and_deterministic(self) -> None:
        self.assertEqual(classify_layered_weight(110, 110, 100)[0], 1.0)
        self.assertEqual(classify_layered_weight(105, 110, 100)[0], 0.7)
        self.assertEqual(classify_layered_weight(100, 110, 100)[0], 0.0)
        self.assertEqual(classify_layered_weight(110, 110, 110)[0], 0.0)


class CaseContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))

    def test_frozen_bear9_and_case_count(self) -> None:
        parameters = self.config["parameters"]
        self.assertEqual(
            list(parameters["bear9_weights"]),
            ["AZO", "SO", "ED", "ORLY", "MO", "WRB", "DLTR", "DG", "WMT"],
        )
        self.assertAlmostEqual(sum(parameters["bear9_weights"].values()), 1.0)
        self.assertFalse(parameters["bear9_individual_sma_enabled"])
        cases = case_definitions(parameters)
        self.assertEqual(len(cases), 97)
        self.assertEqual(sum(item["timing_id"].startswith("SMA") for item in cases), 70)
        self.assertEqual(sum(item["timing_id"] == "LAYERED_190_310" for item in cases), 27)

    def test_bear_modes_translate_residual_allocation(self) -> None:
        self.assertEqual(bear_sleeve_weight(0.3, BEAR_CASH), 0.0)
        self.assertEqual(bear_sleeve_weight(0.3, BEAR_ZERO_ONLY), 0.0)
        self.assertEqual(bear_sleeve_weight(0.0, BEAR_ZERO_ONLY), 1.0)
        self.assertAlmostEqual(bear_sleeve_weight(0.3, BEAR_RESIDUAL), 0.7)


class RebalanceScheduleTest(unittest.TestCase):
    def test_no_rebalance_still_adds_newly_tradable_member(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=6)
        schedule = build_target_weight_schedule(
            state(dates, [0.0] * len(dates)),
            panel(dates, b_starts=2),
            case_id="X",
            bear_weights={"A": 0.5, "B": 0.5},
            bear_mode=BEAR_RESIDUAL,
            rebalance_days=None,
        )
        signals = schedule.drop_duplicates("date").set_index("date")
        self.assertEqual(signals.index.tolist(), [dates[0], dates[2]])
        first = schedule[schedule["date"] == dates[0]].set_index("symbol")
        self.assertEqual(float(first.loc["A", "target_weight"]), 0.5)
        self.assertEqual(float(first.loc["B", "target_weight"]), 0.0)
        self.assertEqual(float(first.loc["A", "cash_target_weight"]), 0.5)
        self.assertEqual(signals.loc[dates[2], "reason"], "tradable_member_change")

    def test_two_day_clock_resets_after_state_change(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=7)
        schedule = build_target_weight_schedule(
            state(dates, [0.0, 0.0, 0.0, 0.3, 0.3, 0.3, 0.3]),
            panel(dates),
            case_id="X",
            bear_weights={"A": 0.5, "B": 0.5},
            bear_mode=BEAR_RESIDUAL,
            rebalance_days=2,
        )
        signals = schedule.drop_duplicates("date").set_index("date")
        self.assertEqual(signals.index.tolist(), [dates[0], dates[2], dates[3], dates[5]])
        self.assertEqual(signals.loc[dates[3], "reason"], "allocation_state_change")
        self.assertEqual(signals.loc[dates[5], "reason"], "periodic_rebalance")

    def test_generated_targets_match_pybroker_and_reference(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=8)
        prices = panel(dates)
        targets = build_target_weight_schedule(
            state(dates, [1.0, 0.0, 0.0, 0.0, 0.7, 0.7, 0.3, 1.0]),
            prices,
            case_id="X",
            bear_weights={"A": 0.5, "B": 0.5},
            bear_mode=BEAR_RESIDUAL,
            rebalance_days=2,
        )
        policy = ExplicitFillPolicy("open", 5.0)
        target_shares = build_target_share_table(
            prices,
            targets,
            case_id="X",
            initial_cash=100_000.0,
            policy=policy,
        )
        result, positions = run_pybroker_portfolio(
            prices, target_shares, initial_cash=100_000.0, policy=policy
        )
        reference = run_reference_portfolio(
            prices, target_shares, initial_cash=100_000.0, policy=policy
        )
        differences = cross_check_portfolios(result, positions, reference, tolerance=1e-6)
        self.assertLessEqual(max(differences.values()), 1e-6)


if __name__ == "__main__":
    unittest.main()
