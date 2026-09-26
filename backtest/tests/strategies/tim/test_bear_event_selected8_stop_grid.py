import unittest

import pandas as pd

from quantkit.bear_event_sma_portfolio import (
    WeightedTrailingSpec,
    build_weighted_trailing_event_plan,
)
from quantkit.execution import ExplicitFillPolicy
from quantkit.trend_score_portfolio import (
    pybroker_daily_state,
    run_pybroker_portfolio,
    run_reference_portfolio,
)
from scripts.analyze_bear_event_selected8_stop_grid import (
    find_absolute_positive_plateaus,
    find_stable_plateaus,
)
from scripts.run_bear_event_selected8_stop_grid import (
    PORTFOLIO_TARGET,
    build_case_specs,
    build_case_price_panel,
    build_scope_summary,
)


UNIVERSE = ["LMT", "EQT", "ORLY", "AZO", "TLT", "COR", "SO", "HRL"]


def synthetic_single_plan() -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, object]]]:
    dates = pd.bdate_range("2020-01-01", periods=12)
    closes = [100.0, 104.0, 110.0, 112.0, 105.0, 102.0, 102.0, 102.0, 104.0, 105.0, 106.0, 107.0]
    prices = pd.DataFrame(
        {
            "date": dates,
            "symbol": "LMT",
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1000,
        }
    )
    indicators = pd.DataFrame(
        {
            "date": dates,
            "symbol": "LMT",
            "close": closes,
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
    return prices, indicators, intervals


class SelectedEightGridContractTest(unittest.TestCase):
    def test_case_grid_has_nine_targets_and_every_integer_stop(self) -> None:
        parameters = {
            "universe": UNIVERSE,
            "targets": [*UNIVERSE, PORTFOLIO_TARGET],
            "portfolio_target": PORTFOLIO_TARGET,
            "trailing_stop_drawdown_pct": [None, *[float(value) for value in range(3, 21)]],
            "formal_case_count_per_cost": 171,
        }
        cases = build_case_specs(parameters)
        self.assertEqual(len(cases), 171)
        self.assertEqual(cases[0].case_id, "LMT__stop_off")
        self.assertEqual(cases[1].case_id, "LMT__stop_03")
        self.assertEqual(cases[-1].case_id, "PORTFOLIO_8__stop_20")
        self.assertEqual(cases[0].symbols, ("LMT",))
        self.assertEqual(cases[-1].symbols, tuple(UNIVERSE))
        self.assertTrue(all(case.slot_multipliers == {symbol: 1.0 for symbol in case.symbols} for case in cases))

    def test_scope_summary_compounds_and_excludes_first_bear(self) -> None:
        intervals = pd.DataFrame(
            [
                {"ordinal": 1, "interval_id": "b1", "severity": "major", "interval_return": 0.50},
                {"ordinal": 2, "interval_id": "b2", "severity": "major", "interval_return": -0.10},
                {"ordinal": 3, "interval_id": "b3", "severity": "minor", "interval_return": 0.00},
            ]
        )
        summary = build_scope_summary(intervals).set_index("scope")
        self.assertAlmostEqual(float(summary.loc["all", "compound_return"]), 0.35)
        self.assertAlmostEqual(float(summary.loc["major", "compound_return"]), 0.35)
        self.assertEqual(float(summary.loc["minor", "compound_return"]), 0.0)
        self.assertAlmostEqual(float(summary.loc["all_ex_2000_2002", "compound_return"]), -0.10)

    def test_prelisting_single_asset_keeps_shared_calendar_as_unavailable(self) -> None:
        dates = pd.bdate_range("2020-01-01", periods=5)
        source = pd.DataFrame(
            {
                "date": dates[2:],
                "symbol": "TLT",
                "open": [100.0, 101.0, 102.0],
                "high": [100.0, 101.0, 102.0],
                "low": [100.0, 101.0, 102.0],
                "close": [100.0, 101.0, 102.0],
                "volume": [1000, 1000, 1000],
            }
        )
        panel = build_case_price_panel(source, pd.DatetimeIndex(dates), ("TLT",))
        self.assertEqual(panel["date"].tolist(), dates.tolist())
        self.assertTrue(panel.loc[:1, ["open", "high", "low", "close"]].isna().all().all())
        self.assertTrue((panel.loc[2:, "symbol"] == "TLT").all())
        indicators = pd.DataFrame(
            {
                "date": dates,
                "symbol": "TLT",
                "close": [float("nan"), float("nan"), 100.0, 101.0, 102.0],
                "sma200": [float("nan")] * 5,
                "ready": False,
            }
        )
        intervals = [
            {
                "ordinal": 1,
                "interval_id": "prelisting",
                "label": "上市前熊市",
                "severity": "major",
                "start": dates[0],
                "end": dates[1],
            }
        ]
        policy = ExplicitFillPolicy("open", 0.0)
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["TLT"],
            initial_cash=100_000.0,
            policy=policy,
            spec=WeightedTrailingSpec(sma_window=2),
            slot_multipliers={"TLT": 1.0},
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
        actual_daily = pybroker_daily_state(result)
        self.assertTrue((positions["shares"] == 0.0).all())
        pd.testing.assert_series_equal(
            actual_daily["equity"].reset_index(drop=True),
            reference.daily["equity"].reset_index(drop=True),
            check_names=False,
        )
        summary = build_scope_summary(
            pd.DataFrame(
                {
                    "ordinal": [1],
                    "severity": ["major"],
                    "total_return": [
                        actual_daily.iloc[2]["equity"]
                        / actual_daily.iloc[0]["equity"]
                        - 1.0
                    ],
                }
            )
        )
        self.assertEqual(float(summary.loc[summary["scope"] == "all", "compound_return"].iloc[0]), 0.0)

    def test_single_symbol_uses_full_weight_and_can_reenter_after_stop(self) -> None:
        panel, indicators, intervals = synthetic_single_plan()
        plan = build_weighted_trailing_event_plan(
            panel,
            indicators,
            intervals,
            symbols=["LMT"],
            initial_cash=100_000.0,
            policy=ExplicitFillPolicy("open", 0.0),
            spec=WeightedTrailingSpec(
                sma_window=2,
                entry_buffer=0.03,
                exit_buffer=0.03,
                trailing_drawdown=0.06,
            ),
            slot_multipliers={"LMT": 1.0},
        )
        entries = plan.transitions[plan.transitions["new_active"]]
        self.assertEqual(entries["signal_date"].tolist(), [pd.bdate_range("2020-01-01", periods=12)[1], pd.bdate_range("2020-01-01", periods=12)[8]])
        self.assertTrue((plan.target_shares.loc[plan.target_shares["target_weight"] > 0, "target_weight"] == 1.0).all())
        forced = plan.transitions[plan.transitions["reason"] == "forced_exit_peak_drawdown"]
        self.assertEqual(len(forced), 1)


class StablePlateauTest(unittest.TestCase):
    def test_requires_three_adjacent_thresholds_to_beat_both_baselines(self) -> None:
        rows = []
        for scope, values in {
            "all": {None: 0.00, 3: -0.01, 4: 0.02, 5: 0.03, 6: 0.04, 7: -0.02},
            "all_ex_2000_2002": {None: -0.10, 3: -0.11, 4: -0.08, 5: -0.07, 6: -0.06, 7: -0.12},
        }.items():
            for threshold, value in values.items():
                rows.append(
                    {
                        "target": PORTFOLIO_TARGET,
                        "scope": scope,
                        "stop_id": "stop_off" if threshold is None else f"stop_{threshold:02d}",
                        "trailing_drawdown_pct": threshold,
                        "compound_return": value,
                    }
                )
        plateaus = find_stable_plateaus(pd.DataFrame(rows), PORTFOLIO_TARGET)
        self.assertEqual(plateaus, [(4, 6)])
        self.assertEqual(
            find_absolute_positive_plateaus(pd.DataFrame(rows), PORTFOLIO_TARGET),
            [],
        )

    def test_absolute_positive_plateau_requires_post_2000_profit(self) -> None:
        rows = []
        for scope, values in {
            "all": {None: 0.00, 4: 0.02, 5: 0.03, 6: 0.04},
            "all_ex_2000_2002": {None: -0.10, 4: 0.01, 5: 0.02, 6: 0.03},
        }.items():
            for threshold, value in values.items():
                rows.append(
                    {
                        "target": PORTFOLIO_TARGET,
                        "scope": scope,
                        "stop_id": "stop_off" if threshold is None else f"stop_{threshold:02d}",
                        "trailing_drawdown_pct": threshold,
                        "compound_return": value,
                    }
                )
        self.assertEqual(
            find_absolute_positive_plateaus(pd.DataFrame(rows), PORTFOLIO_TARGET),
            [(4, 6)],
        )


if __name__ == "__main__":
    unittest.main()
