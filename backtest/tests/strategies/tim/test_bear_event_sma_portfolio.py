import unittest

import pandas as pd

from quantkit.bear_event_sma_portfolio import (
    EventSmaSpec,
    allocate_transition_weights,
    build_event_plan,
    lock_has_released,
    transition_state,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.trend_score_portfolio import (
    cross_check_portfolios,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.run_bear_event_sma_portfolios import build_common_calendar
from scripts.analyze_bear_event_sma_portfolios import evaluate_promotion_gates


def synthetic_panel() -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    dates = pd.bdate_range("2020-01-01", periods=9)
    closes = {
        "A": [100.0, 101.0, 97.0, 95.0, 89.0, 89.0, 92.0, 94.0, 95.0],
        "B": [100.0, 100.0, 100.0, 103.0, 103.0, 104.0, 104.0, 103.0, 102.0],
    }
    opens = {
        "A": [100.0, 100.0, 100.0, 100.0, 100.0, 89.0, 92.0, 94.0, 95.0],
        "B": [100.0] * len(dates),
    }
    price_rows: list[dict[str, object]] = []
    indicator_rows: list[dict[str, object]] = []
    for symbol in ("A", "B"):
        for index, date in enumerate(dates):
            price_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": opens[symbol][index],
                    "high": max(opens[symbol][index], closes[symbol][index]),
                    "low": min(opens[symbol][index], closes[symbol][index]),
                    "close": closes[symbol][index],
                    "volume": 1_000,
                }
            )
            indicator_rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "close": closes[symbol][index],
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
            "end": dates[7],
        }
    ]
    return pd.DataFrame(price_rows), pd.DataFrame(indicator_rows), intervals


class EventSmaStateTest(unittest.TestCase):
    def test_lock_boundary_is_inclusive_and_releases_only_outside_ten_percent(self) -> None:
        self.assertFalse(lock_has_released(100.0, 90.0, 0.10))
        self.assertFalse(lock_has_released(100.0, 110.0, 0.10))
        self.assertTrue(lock_has_released(100.0, 89.99, 0.10))
        self.assertTrue(lock_has_released(100.0, 110.01, 0.10))

    def test_locked_position_cannot_exit_until_price_leaves_anchor_band(self) -> None:
        spec = EventSmaSpec(sma_window=2, entry_buffer=0.02, exit_buffer=0.02, lock_band=0.10)
        active, locked, reason = transition_state(
            active=True,
            locked=True,
            anchor=100.0,
            close=95.0,
            sma=100.0,
            ready=True,
            spec=spec,
        )
        self.assertTrue(active)
        self.assertTrue(locked)
        self.assertEqual(reason, "lock_holds")
        active, locked, reason = transition_state(
            active=True,
            locked=True,
            anchor=100.0,
            close=89.0,
            sma=100.0,
            ready=True,
            spec=spec,
        )
        self.assertFalse(active)
        self.assertFalse(locked)
        self.assertEqual(reason, "exit_below_sma_minus_buffer")

    def test_simultaneous_entry_preserves_incumbent_value_ratio(self) -> None:
        weights = allocate_transition_weights(
            pd.Series({"A": 200.0, "B": 100.0, "C": 0.0}),
            survivors=["A", "B"],
            entrants=["C"],
        )
        self.assertAlmostEqual(float(weights["A"]), 4.0 / 9.0)
        self.assertAlmostEqual(float(weights["B"]), 2.0 / 9.0)
        self.assertAlmostEqual(float(weights["C"]), 1.0 / 3.0)
        self.assertAlmostEqual(float(weights.sum()), 1.0)


class EventSmaPlanTest(unittest.TestCase):
    def test_common_calendar_ignores_prelisting_absence_but_removes_later_gap(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=5)
        spy = pd.DataFrame({"date": dates})
        frames = {
            "A": pd.DataFrame({"date": dates}),
            "B": pd.DataFrame({"date": [dates[1], dates[2], dates[4]]}),
        }
        calendar, exclusions = build_common_calendar(
            spy,
            frames,
            start=dates[0],
            end=dates[-1],
        )
        self.assertEqual(calendar.tolist(), [dates[0], dates[1], dates[2], dates[4]])
        self.assertEqual(exclusions[["date", "symbol"]].to_dict("records"), [
            {"date": dates[3], "symbol": "B"}
        ])

    def test_no_filter_only_sets_start_and_end_targets(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        plan = build_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            mode="no_filter",
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=EventSmaSpec(sma_window=2),
        )
        signal_dates = sorted(pd.to_datetime(plan.target_shares["signal_date"].unique()))
        self.assertEqual(signal_dates, [pd.Timestamp(intervals[0]["start"]), pd.Timestamp(intervals[0]["end"])])
        initial = plan.target_shares[
            plan.target_shares["signal_date"] == pd.Timestamp(intervals[0]["start"])
        ].set_index("symbol")
        self.assertAlmostEqual(float(initial.loc["A", "target_weight"]), 0.5)
        self.assertAlmostEqual(float(initial.loc["B", "target_weight"]), 0.5)

    def test_filtered_plan_honors_initial_level_lock_and_proportional_resize(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        plan = build_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            mode="sma200_hysteresis",
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=EventSmaSpec(sma_window=2, entry_buffer=0.02, exit_buffer=0.02, lock_band=0.10),
        )
        transitions = plan.transitions.set_index(["signal_date", "symbol"])
        dates = pd.bdate_range("2020-01-01", periods=9)
        self.assertEqual(transitions.loc[(dates[1], "A"), "reason"], "initial_above_sma")
        self.assertEqual(transitions.loc[(dates[3], "B"), "reason"], "entry_above_sma_plus_buffer")
        self.assertEqual(transitions.loc[(dates[4], "A"), "reason"], "exit_below_sma_minus_buffer")
        resize = plan.target_shares[
            plan.target_shares["signal_date"] == dates[3]
        ].set_index("symbol")
        self.assertAlmostEqual(float(resize.loc["A", "target_weight"]), 0.5)
        self.assertAlmostEqual(float(resize.loc["B", "target_weight"]), 0.5)
        a_entry = plan.executions[
            (plan.executions["symbol"] == "A")
            & (plan.executions["state_transition"] == "entry")
        ].iloc[0]
        a_resize = plan.executions[
            (plan.executions["symbol"] == "A")
            & (plan.executions["signal_date"] == dates[3])
        ].iloc[0]
        self.assertEqual(float(a_entry["lock_anchor_after"]), 100.0)
        self.assertEqual(float(a_resize["lock_anchor_after"]), 100.0)
        self.assertFalse(bool(a_resize["anchor_updated"]))

    def test_future_prices_cannot_change_prior_targets(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        kwargs = {
            "intervals": intervals,
            "symbols": ["A", "B"],
            "mode": "sma200_hysteresis",
            "initial_cash": 100_000.0,
            "policy": ExplicitFillPolicy("open", 0.0),
            "spec": EventSmaSpec(sma_window=2),
        }
        original = build_event_plan(panel, indicators, **kwargs)
        changed_panel = panel.copy()
        changed_indicators = indicators.copy()
        cutoff = pd.bdate_range("2020-01-01", periods=9)[5]
        changed_panel.loc[changed_panel["date"] >= cutoff, ["open", "high", "low", "close"]] *= 3.0
        changed_indicators.loc[changed_indicators["date"] >= cutoff, "close"] *= 3.0
        changed = build_event_plan(changed_panel, changed_indicators, **kwargs)
        columns = ["signal_date", "symbol", "target_weight", "target_shares"]
        left = original.target_shares[original.target_shares["signal_date"] < cutoff][columns].reset_index(drop=True)
        right = changed.target_shares[changed.target_shares["signal_date"] < cutoff][columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right)

    def test_generated_targets_match_pybroker_and_independent_ledger(self) -> None:
        panel, indicators, intervals = synthetic_panel()
        policy = ExplicitFillPolicy("open", 5.0)
        plan = build_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["A", "B"],
            mode="sma200_hysteresis",
            initial_cash=100_000.0,
            policy=policy,
            spec=EventSmaSpec(sma_window=2),
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


class EventSmaReportGateTest(unittest.TestCase):
    def test_major_minor_gate_excludes_the_three_all_scope_rows(self) -> None:
        rows = []
        for universe_id in ("u1", "u2", "u3"):
            for scope in ("major", "minor", "all"):
                rows.append(
                    {
                        "universe_id": universe_id,
                        "scope": scope,
                        "improvement": 0.1 if scope == "all" else -0.1,
                    }
                )
        five = pd.DataFrame(rows)
        five.loc[
            (five["universe_id"] == "u1") & (five["scope"] == "major"),
            "improvement",
        ] = 0.1
        gates = evaluate_promotion_gates(five.copy(), five)
        self.assertEqual(gates["paired_improvement_count"], 1)
        self.assertFalse(gates["promotion_passed"])


if __name__ == "__main__":
    unittest.main()
