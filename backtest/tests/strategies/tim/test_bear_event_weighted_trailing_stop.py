import json
import unittest
from pathlib import Path

import pandas as pd

from quantkit.bear_event_sma_portfolio import (
    WeightedTrailingSpec,
    allocate_weighted_transition_weights,
    build_weighted_trailing_event_plan,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.trend_score_portfolio import (
    cross_check_portfolios,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.run_bear_event_weighted_trailing_stop import (
    STOP_MAP,
    parse_case_id,
    slot_multipliers,
)


EXPERIMENT_PATH = Path(__file__).resolve().parents[3] / (
    "experiments/TIM/"
    "TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation/"
    "experiment.json"
)


def synthetic_panel() -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    dates = pd.bdate_range("2020-01-01", periods=12)
    closes = {
        "A": [100, 104, 110, 112, 105, 103, 102, 101, 104, 105, 106, 107],
        "B": [100, 102, 102, 102, 102, 102, 102, 102, 102, 104, 105, 106],
    }
    opens = {symbol: [float(value) for value in values] for symbol, values in closes.items()}
    price_rows = []
    indicator_rows = []
    for symbol in ("A", "B"):
        for index, date in enumerate(dates):
            close = float(closes[symbol][index])
            open_price = float(opens[symbol][index])
            price_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": open_price,
                    "high": max(open_price, close),
                    "low": min(open_price, close),
                    "close": close,
                    "volume": 1000,
                }
            )
            indicator_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "close": close,
                    "sma200": 100.0,
                    "ready": True,
                }
            )
    intervals = [
        {
            "ordinal": 1,
            "interval_id": "bear_1",
            "label": "测试熊市",
            "severity": "major",
            "start": dates[1],
            "end": dates[10],
        }
    ]
    return pd.DataFrame(price_rows), pd.DataFrame(indicator_rows), intervals


class WeightedAllocationTest(unittest.TestCase):
    def test_initial_and_entrant_slots_use_two_for_starred_symbol(self) -> None:
        initial = allocate_weighted_transition_weights(
            pd.Series(dtype=float),
            survivors=[],
            entrants=["A", "B"],
            slot_multipliers={"A": 2.0, "B": 1.0},
        )
        self.assertAlmostEqual(float(initial["A"]), 2.0 / 3.0)
        self.assertAlmostEqual(float(initial["B"]), 1.0 / 3.0)

        later = allocate_weighted_transition_weights(
            pd.Series({"B": 100.0}),
            survivors=["B"],
            entrants=["A"],
            slot_multipliers={"A": 2.0, "B": 1.0},
        )
        self.assertAlmostEqual(float(later["A"]), 2.0 / 3.0)
        self.assertAlmostEqual(float(later["B"]), 1.0 / 3.0)

    def test_lmt_is_one_symbol_with_two_slots_not_two_positions(self) -> None:
        weights = allocate_weighted_transition_weights(
            pd.Series(dtype=float),
            survivors=[],
            entrants=["LMT", "LMT", "WEC"],
            slot_multipliers={"LMT": 2.0, "WEC": 1.0},
        )
        self.assertEqual(weights.index.tolist(), ["LMT", "WEC"])
        self.assertAlmostEqual(float(weights["LMT"]), 2.0 / 3.0)


class FormalExperimentContractTest(unittest.TestCase):
    def test_frozen_universe_cases_and_removed_trade_lock(self) -> None:
        config = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
        parameters = config["parameters"]
        universe = parameters["universe"]
        self.assertEqual(len(universe), 15)
        self.assertEqual(len(set(universe)), 15)
        self.assertEqual(universe.count("LMT"), 1)
        self.assertEqual(
            set(parameters["starred_symbols"]),
            {"EQT", "GIS", "LMT", "ORLY", "AZO"},
        )
        self.assertEqual(parameters["entry_buffer_pct"], 3.0)
        self.assertEqual(parameters["exit_buffer_pct"], 3.0)
        self.assertIsNone(parameters["state_change_lock_band_pct"])
        expected_cases = [
            f"{star_mode}__{stop_id}"
            for star_mode in ("star_off", "star_on")
            for stop_id in STOP_MAP
        ]
        self.assertEqual(parameters["formal_cases"], expected_cases)
        self.assertEqual(
            parameters["trailing_stop_drawdown_pct"],
            [None, 6.0, 8.0, 10.0, 12.0],
        )

    def test_case_parser_and_star_slots_match_frozen_design(self) -> None:
        self.assertEqual(parse_case_id("star_on__stop_08"), ("star_on", "stop_08", 0.08))
        self.assertEqual(parse_case_id("star_off__stop_off"), ("star_off", "stop_off", None))
        with self.assertRaises(ValueError):
            parse_case_id("star_on__stop_07")
        slots = slot_multipliers(
            ["LMT", "WEC"],
            {"LMT"},
            "star_on",
        )
        self.assertEqual(slots, {"LMT": 2.0, "WEC": 1.0})


class WeightedTrailingPlanTest(unittest.TestCase):
    def test_removed_ten_percent_lock_allows_immediate_sma_exit(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        dates = pd.bdate_range("2020-01-01", periods=12)
        execution_day = dates[2]
        panel.loc[
            (panel["symbol"] == "A") & (panel["date"] == execution_day),
            ["open", "high", "low", "close"],
        ] = [104.0, 104.0, 96.0, 96.0]
        indicators.loc[
            (indicators["symbol"] == "A") & (indicators["date"] == execution_day),
            "close",
        ] = 96.0
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=WeightedTrailingSpec(sma_window=2, entry_buffer=0.03, exit_buffer=0.03),
            slot_multipliers={"A": 1.0, "B": 1.0},
        )
        exit_row = plan.transitions[
            (plan.transitions["signal_date"] == execution_day)
            & (plan.transitions["symbol"] == "A")
        ].iloc[0]
        self.assertEqual(exit_row["reason"], "exit_below_sma_minus_buffer")

    def test_passive_rebalance_does_not_reset_running_peak(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        dates = pd.bdate_range("2020-01-01", periods=12)
        earlier_cross = dates[8]
        panel.loc[
            (panel["symbol"] == "B") & (panel["date"] == earlier_cross),
            ["open", "high", "low", "close"],
        ] = [104.0, 104.0, 104.0, 104.0]
        indicators.loc[
            (indicators["symbol"] == "B") & (indicators["date"] == earlier_cross),
            "close",
        ] = 104.0
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=WeightedTrailingSpec(sma_window=2, trailing_drawdown=0.20),
            slot_multipliers={"A": 2.0, "B": 1.0},
        )
        b_entry_execution = dates[9]
        passive_a = plan.executions[
            (plan.executions["execution_date"] == b_entry_execution)
            & (plan.executions["symbol"] == "A")
        ].iloc[0]
        self.assertEqual(passive_a["reason"], "proportional_resize")
        self.assertEqual(passive_a["state_transition"], "none")
        after_rebalance = plan.signals[
            (plan.signals["date"] == b_entry_execution)
            & (plan.signals["symbol"] == "A")
        ].iloc[0]
        self.assertAlmostEqual(float(after_rebalance["running_peak_close"]), 112.0)

    def test_peak_drawdown_forces_exit_and_requires_new_upper_cross(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=WeightedTrailingSpec(
                sma_window=2,
                entry_buffer=0.03,
                exit_buffer=0.03,
                trailing_drawdown=0.06,
            ),
            slot_multipliers={"A": 2.0, "B": 1.0},
        )
        transitions = plan.transitions.set_index(["signal_date", "symbol"])
        dates = pd.bdate_range("2020-01-01", periods=12)
        self.assertEqual(
            transitions.loc[(dates[4], "A"), "reason"],
            "forced_exit_peak_drawdown",
        )
        self.assertAlmostEqual(
            float(transitions.loc[(dates[4], "A"), "running_peak_close"]),
            112.0,
        )
        self.assertLess(
            float(transitions.loc[(dates[4], "A"), "drawdown_from_peak"]),
            -0.06,
        )
        later_entries = plan.transitions[
            (plan.transitions["symbol"] == "A")
            & (plan.transitions["signal_date"] > dates[4])
            & (plan.transitions["new_active"])
        ]
        self.assertEqual(later_entries["signal_date"].tolist(), [dates[8]])
        self.assertEqual(later_entries["reason"].tolist(), ["entry_cross_above_sma_plus_buffer"])

    def test_threshold_is_strict_and_high_watermark_uses_completed_close(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        panel.loc[(panel["symbol"] == "A") & (panel["date"] == pd.bdate_range("2020-01-01", periods=12)[4]), "close"] = 112.0 * 0.94
        indicators.loc[(indicators["symbol"] == "A") & (indicators["date"] == pd.bdate_range("2020-01-01", periods=12)[4]), "close"] = 112.0 * 0.94
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=WeightedTrailingSpec(sma_window=2, trailing_drawdown=0.06),
            slot_multipliers={"A": 1.0, "B": 1.0},
        )
        equal_day = pd.bdate_range("2020-01-01", periods=12)[4]
        row = plan.signals[(plan.signals["date"] == equal_day) & (plan.signals["symbol"] == "A")].iloc[0]
        self.assertNotEqual(row["reason"], "forced_exit_peak_drawdown")

    def test_targets_match_pybroker_and_independent_ledger(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        policy = ExplicitFillPolicy("open", 5.0)
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            initial_cash=100_000.0,
            policy=policy,
            spec=WeightedTrailingSpec(sma_window=2, trailing_drawdown=0.08),
            slot_multipliers={"A": 2.0, "B": 1.0},
        )
        result, positions = run_pybroker_portfolio(
            panel,
            plan.target_shares,
            initial_cash=100_000.0,
            policy=policy,
        )
        reference = run_reference_portfolio(
            panel,
            plan.target_shares,
            initial_cash=100_000.0,
            policy=policy,
        )
        differences = cross_check_portfolios(result, positions, reference, tolerance=1e-6)
        self.assertLessEqual(max(differences.values()), 1e-6)

    def test_future_prices_cannot_change_prior_targets(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        kwargs = {
            "intervals": intervals,
            "symbols": ["A", "B"],
            "initial_cash": 100_000.0,
            "policy": ExplicitFillPolicy("open", 0.0),
            "spec": WeightedTrailingSpec(sma_window=2, trailing_drawdown=0.08),
            "slot_multipliers": {"A": 2.0, "B": 1.0},
        }
        original = build_weighted_trailing_event_plan(panel, indicators, **kwargs)
        changed_panel = panel.copy()
        changed_indicators = indicators.copy()
        cutoff = pd.bdate_range("2020-01-01", periods=12)[7]
        changed_panel.loc[changed_panel["date"] >= cutoff, ["open", "high", "low", "close"]] *= 3.0
        changed_indicators.loc[changed_indicators["date"] >= cutoff, "close"] *= 3.0
        changed = build_weighted_trailing_event_plan(changed_panel, changed_indicators, **kwargs)
        columns = ["signal_date", "symbol", "target_weight", "target_shares"]
        left = original.target_shares[original.target_shares["signal_date"] < cutoff][columns].reset_index(drop=True)
        right = changed.target_shares[changed.target_shares["signal_date"] < cutoff][columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)


if __name__ == "__main__":
    unittest.main()
