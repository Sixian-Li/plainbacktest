from __future__ import annotations

import unittest
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.bear_atr_hard_stop import (
    BearAtrSpec,
    POLICY_ATR_PRICE,
    POLICY_ATR_SMA,
    POLICY_HOLD,
    POLICY_SMA,
    aggregate_group_path,
    compiled_pybroker_path,
    cross_check_policy_paths,
    hard_stop_distance,
    hard_stop_raw_fill,
    run_pybroker_compiled,
    run_reference_policy,
    wilder_atr,
)
from quantkit.execution import ExplicitFillPolicy
from scripts.run_bear24_atr_hard_stop_ablation import build_case_specs, build_scope_summary


def prepared_frame(
    *,
    symbol: str = "AAA",
    opens: list[float],
    closes: list[float],
    lows: list[float] | None = None,
    highs: list[float] | None = None,
    atr: float = 5.0,
) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=len(opens))
    lows = lows or [min(open_, close) - 1.0 for open_, close in zip(opens, closes)]
    highs = highs or [max(open_, close) + 1.0 for open_, close in zip(opens, closes)]
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1_000.0,
            "sma200": 100.0,
            "atr20": atr,
            "indicator_ready": True,
        }
    )


def one_interval(data: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ordinal": 1,
                "interval_id": "bear_1",
                "label": "test bear",
                "severity": "major",
                "start": data.iloc[1]["date"],
                "end": data.iloc[-2]["date"],
            }
        ]
    )


class IndicatorAndStopTest(unittest.TestCase):
    def test_sma_one_is_valid_while_atr_still_requires_two_bars(self) -> None:
        self.assertEqual(BearAtrSpec(sma_window=1).sma_window, 1)
        with self.assertRaisesRegex(ValueError, "sma_window must be at least one"):
            BearAtrSpec(sma_window=0)
        with self.assertRaisesRegex(ValueError, "atr_window must be at least two"):
            BearAtrSpec(atr_window=1)

    def test_wilder_atr_uses_simple_mean_then_recursive_rma(self) -> None:
        high = pd.Series([11.0, 12.0, 13.0, 14.0, 15.0])
        low = pd.Series([9.0, 10.0, 11.0, 12.0, 13.0])
        close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        observed = wilder_atr(high, low, close, 3)
        self.assertTrue(observed.iloc[:2].isna().all())
        np.testing.assert_allclose(observed.iloc[2:].to_numpy(float), [2.0, 2.0, 2.0])

    def test_atr_distance_clips_to_twelve_and_twenty_percent(self) -> None:
        spec = BearAtrSpec()
        self.assertAlmostEqual(hard_stop_distance(1.0, 100.0, spec), 0.12)
        self.assertAlmostEqual(hard_stop_distance(5.0, 100.0, spec), 0.15)
        self.assertAlmostEqual(hard_stop_distance(10.0, 100.0, spec), 0.20)

    def test_standing_stop_uses_gap_open_and_equality_triggers(self) -> None:
        self.assertEqual(
            hard_stop_raw_fill(open_=80.0, low=75.0, stop_line=85.0),
            (80.0, "open_gap"),
        )
        self.assertEqual(
            hard_stop_raw_fill(open_=90.0, low=85.0, stop_line=85.0),
            (85.0, "intraday_stop"),
        )
        self.assertIsNone(hard_stop_raw_fill(open_=90.0, low=85.01, stop_line=85.0))


class PolicyStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = BearAtrSpec()
        self.fill = ExplicitFillPolicy("open", 0.0)

    def run_policy(self, data: pd.DataFrame, policy_id: str):
        return run_reference_policy(
            data,
            one_interval(data),
            policy_id=policy_id,
            initial_cash=100_000.0,
            fill_policy=self.fill,
            spec=self.spec,
            case_id=f"AAA__{policy_id}",
        )

    def test_full_sma_exits_rearms_and_crosses_three_percent_upper(self) -> None:
        data = prepared_frame(
            opens=[100, 104, 105, 96, 100, 104, 106, 107],
            closes=[100, 104, 104, 96, 100, 104, 105, 107],
        )
        path = self.run_policy(data, POLICY_SMA)
        self.assertEqual(
            path.orders["reason"].tolist(),
            ["interval_start_above_sma", "sma_exit", "sma_entry_cross", "interval_end"],
        )
        self.assertEqual(path.orders["type"].tolist(), ["buy", "sell", "buy", "sell"])

    def test_price_line_reentry_ignores_stop_day_and_can_buy_again(self) -> None:
        data = prepared_frame(
            opens=[100, 100, 100, 80, 86, 90, 95, 96],
            closes=[100, 100, 100, 82, 86, 90, 95, 96],
            lows=[99, 99, 90, 79, 85, 89, 94, 95],
        )
        path = self.run_policy(data, POLICY_ATR_PRICE)
        self.assertEqual(
            path.orders["reason"].tolist(),
            ["interval_start", "hard_stop", "price_line_reentry", "interval_end"],
        )
        stop = path.orders[path.orders["reason"] == "hard_stop"].iloc[0]
        reentry = path.orders[path.orders["reason"] == "price_line_reentry"].iloc[0]
        self.assertEqual(stop["fill_source"], "open_gap")
        self.assertGreater(pd.Timestamp(reentry["signal_date"]), pd.Timestamp(stop["date"]))

    def test_sma_reentry_requires_a_post_stop_rearm_then_later_cross(self) -> None:
        data = prepared_frame(
            opens=[100, 100, 100, 80, 102, 104, 110, 111],
            closes=[100, 100, 100, 82, 102, 104, 110, 111],
            lows=[99, 99, 90, 79, 101, 103, 109, 110],
        )
        path = self.run_policy(data, POLICY_ATR_SMA)
        self.assertEqual(
            path.orders["reason"].tolist(),
            ["interval_start", "hard_stop", "post_stop_sma_cross", "interval_end"],
        )
        cross = path.orders[path.orders["reason"] == "post_stop_sma_cross"].iloc[0]
        self.assertEqual(pd.Timestamp(cross["signal_date"]), pd.Timestamp(data.iloc[5]["date"]))

    def test_entry_session_stop_generates_two_ordered_fills_and_pybroker_matches(self) -> None:
        data = prepared_frame(
            opens=[100, 100, 100, 80, 80, 80, 80, 80],
            closes=[100, 100, 84, 80, 80, 80, 80, 80],
            lows=[99, 99, 84, 79, 79, 79, 79, 79],
        )
        reference = self.run_policy(data, POLICY_ATR_PRICE)
        same_day = reference.orders[reference.orders["date"] == data.iloc[2]["date"]]
        self.assertEqual(same_day["type"].tolist(), ["buy", "sell"])
        compiled = run_pybroker_compiled(data, reference, initial_cash=100_000.0)
        actual = compiled_pybroker_path(
            compiled, data, reference, initial_cash=100_000.0
        )
        differences = cross_check_policy_paths(actual, reference)
        self.assertLess(max(differences.values()), 1e-6)

    def test_future_mutation_cannot_change_earlier_orders(self) -> None:
        data = prepared_frame(
            opens=[100, 100, 100, 80, 86, 90, 95, 96],
            closes=[100, 100, 100, 82, 86, 90, 95, 96],
            lows=[99, 99, 90, 79, 85, 89, 94, 95],
        )
        baseline = self.run_policy(data, POLICY_ATR_PRICE)
        changed = data.copy()
        changed.loc[6:, ["open", "high", "low", "close"]] *= 4.0
        rerun = self.run_policy(changed, POLICY_ATR_PRICE)
        cutoff = pd.Timestamp(data.iloc[5]["date"])
        columns = ["type", "signal_date", "date", "reason", "raw_price", "fill_price"]
        pd.testing.assert_frame_equal(
            baseline.orders[baseline.orders["date"] <= cutoff][columns].reset_index(drop=True),
            rerun.orders[rerun.orders["date"] <= cutoff][columns].reset_index(drop=True),
        )


class GroupSleeveTest(unittest.TestCase):
    def test_stopped_member_cash_is_not_redistributed_to_survivor(self) -> None:
        fill = ExplicitFillPolicy("open", 0.0)
        spec = BearAtrSpec()
        stopped = prepared_frame(
            symbol="AAA",
            opens=[100, 100, 100, 80, 80, 80, 80, 80],
            closes=[100, 100, 100, 80, 80, 80, 80, 80],
            lows=[99, 99, 90, 79, 79, 79, 79, 79],
        )
        survivor = prepared_frame(
            symbol="BBB",
            opens=[100, 100, 100, 120, 140, 160, 180, 200],
            closes=[100, 100, 110, 130, 150, 170, 190, 200],
            lows=[99, 99, 99, 119, 139, 159, 179, 199],
            highs=[101, 101, 111, 131, 151, 171, 191, 201],
            atr=1.0,
        )
        intervals = one_interval(stopped)
        paths = {
            symbol: run_reference_policy(
                frame,
                intervals,
                policy_id=POLICY_ATR_PRICE,
                initial_cash=100_000.0,
                fill_policy=fill,
                spec=spec,
                case_id=f"{symbol}__{POLICY_ATR_PRICE}",
            )
            for symbol, frame in {"AAA": stopped, "BBB": survivor}.items()
        }
        group = aggregate_group_path(
            paths,
            ["AAA", "BBB"],
            group_target="GROUP_TEST",
            policy_id=POLICY_ATR_PRICE,
            initial_cash=100_000.0,
        )
        # AAA's half becomes 40k at its gap stop; BBB's half doubles to 100k.
        # Fixed sleeves therefore end at 140k, rather than reallocating AAA cash.
        self.assertAlmostEqual(float(group.daily.iloc[-1]["equity"]), 140_000.0)
        self.assertEqual(
            int(group.interval_schedule.iloc[0]["eligible_member_count"]), 2
        )


class FormalExperimentContractTest(unittest.TestCase):
    def test_case_grid_contains_24_singles_three_groups_and_four_policies(self) -> None:
        path = Path(__file__).parents[3] / (
            "experiments/TIM/"
            "TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json"
        )
        parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
        cases = build_case_specs(parameters)
        self.assertEqual(len(cases), 108)
        self.assertEqual(sum(case.target_type == "single" for case in cases), 96)
        self.assertEqual(sum(case.target_type == "group" for case in cases), 12)
        self.assertEqual(len({case.target for case in cases}), 27)

    def test_ex_2000_scope_removes_only_first_interval_before_compounding(self) -> None:
        returns = pd.DataFrame(
            {
                "ordinal": range(1, 13),
                "severity": ["major", "minor"] * 6,
                "total_return": [1.0, *([0.10] * 11)],
            }
        )
        scopes = build_scope_summary(returns).set_index("scope")
        self.assertEqual(int(scopes.loc["all", "interval_count"]), 12)
        self.assertEqual(int(scopes.loc["all_ex_2000_2002", "interval_count"]), 11)
        self.assertAlmostEqual(
            float(scopes.loc["all_ex_2000_2002", "compound_return"]),
            1.1**11 - 1.0,
        )
        self.assertAlmostEqual(
            float(scopes.loc["all", "compound_return"]),
            2.0 * 1.1**11 - 1.0,
        )


if __name__ == "__main__":
    unittest.main()
