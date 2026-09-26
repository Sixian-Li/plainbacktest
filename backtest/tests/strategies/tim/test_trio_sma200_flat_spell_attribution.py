from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quantkit.execution import ExplicitFillPolicy
from quantkit.reference import run_reference_threshold
from quantkit.sma_flat_spells import attribute_flat_spells, summarize_flat_spells
from quantkit.sma_threshold import SmaThresholdSpec, analysis_slice, prepare_sma_data


BACKTEST_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution/experiment.json"
)


def price_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=9)
    opens = [100.0, 101.0, 103.0, 104.0, 90.0, 88.0, 92.0, 106.0, 108.0]
    closes = [100.0, 102.0, 104.0, 90.0, 88.0, 91.0, 105.0, 107.0, 109.0]
    return pd.DataFrame(
        {
            "symbol": "TEST",
            "date": dates,
            "open": opens,
            "high": np.asarray(opens) + 2.0,
            "low": np.asarray(opens) - 3.0,
            "close": closes,
            "volume": 1_000,
        }
    )


def order_frame(*, open_final: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=9)
    rows = [
        {
            "symbol": "TEST",
            "type": "buy",
            "signal_date": dates[1],
            "date": dates[2],
            "raw_price": 103.0,
            "fill_price": 103.1,
        },
        {
            "symbol": "TEST",
            "type": "sell",
            "signal_date": dates[3],
            "date": dates[4],
            "raw_price": 90.0,
            "fill_price": 89.9,
        },
    ]
    if not open_final:
        rows.append(
            {
                "symbol": "TEST",
                "type": "buy",
                "signal_date": dates[6],
                "date": dates[7],
                "raw_price": 106.0,
                "fill_price": 106.1,
            }
        )
    return pd.DataFrame(rows)


class FlatSpellAttributionTest(unittest.TestCase):
    def test_sell_session_is_included_and_reentry_session_is_excluded(self) -> None:
        prices = price_frame()
        events = attribute_flat_spells(prices, order_frame(), horizons=[2, 4])
        self.assertEqual(len(events), 1)
        event = events.iloc[0]
        self.assertTrue(bool(event["closed_spell"]))
        self.assertEqual(int(event["flat_sessions"]), 3)
        self.assertEqual(pd.Timestamp(event["sell_fill_date"]), prices.iloc[4]["date"])
        self.assertEqual(pd.Timestamp(event["next_buy_fill_date"]), prices.iloc[7]["date"])
        self.assertAlmostEqual(
            float(event["asset_return_while_flat_pct"]), (106.0 / 90.0 - 1.0) * 100.0
        )
        # The re-entry day's Low=103 is excluded; path Lows are 87, 85, 89.
        self.assertAlmostEqual(
            float(event["min_low_vs_sell_open_pct"]), (85.0 / 90.0 - 1.0) * 100.0
        )
        self.assertTrue(bool(event["complete_horizon_4"]))
        self.assertAlmostEqual(
            float(event["end_close_return_pct_2"]), (91.0 / 90.0 - 1.0) * 100.0
        )

    def test_open_final_spell_marks_to_last_close_and_incomplete_horizon_is_missing(self) -> None:
        prices = price_frame()
        events = attribute_flat_spells(prices, order_frame(open_final=True), horizons=[5, 6])
        event = events.iloc[0]
        self.assertFalse(bool(event["closed_spell"]))
        self.assertEqual(int(event["flat_sessions"]), 5)
        self.assertAlmostEqual(
            float(event["asset_return_while_flat_pct"]), (109.0 / 90.0 - 1.0) * 100.0
        )
        self.assertTrue(bool(event["complete_horizon_5"]))
        self.assertFalse(bool(event["complete_horizon_6"]))
        self.assertTrue(np.isnan(float(event["end_close_return_pct_6"])))

    def test_bear_overlap_is_attribution_only(self) -> None:
        prices = price_frame()
        intervals = pd.DataFrame(
            [
                {
                    "ordinal": 1,
                    "label": "toy major",
                    "severity": "major",
                    "start": prices.iloc[4]["date"],
                    "end": prices.iloc[5]["date"],
                }
            ]
        )
        plain = attribute_flat_spells(prices, order_frame(), horizons=[2])
        attributed = attribute_flat_spells(
            prices, order_frame(), horizons=[2], bear_intervals=intervals
        )
        self.assertAlmostEqual(
            float(plain.iloc[0]["asset_return_while_flat_pct"]),
            float(attributed.iloc[0]["asset_return_while_flat_pct"]),
        )
        self.assertEqual(int(attributed.iloc[0]["major_bear_sessions"]), 2)
        self.assertEqual(int(attributed.iloc[0]["non_bear_sessions"]), 1)

    def test_future_mutation_after_reentry_cannot_rewrite_closed_spell(self) -> None:
        prices = price_frame()
        baseline = attribute_flat_spells(prices, order_frame(), horizons=[2])
        mutated = prices.copy()
        mutated.loc[8, ["open", "high", "low", "close"]] = [999.0, 1000.0, 998.0, 999.0]
        changed = attribute_flat_spells(mutated, order_frame(), horizons=[2])
        for column in (
            "asset_return_while_flat_pct",
            "min_low_vs_sell_open_pct",
            "max_high_vs_sell_open_pct",
            "end_close_return_pct_2",
        ):
            self.assertAlmostEqual(float(baseline.iloc[0][column]), float(changed.iloc[0][column]))

    def test_summary_keeps_both_avoided_losses_and_missed_gains(self) -> None:
        events = pd.DataFrame(
            {
                "closed_spell": [True, True, False],
                "asset_return_while_flat_pct": [-20.0, 10.0, -5.0],
                "flat_sessions": [20, 10, 5],
            }
        )
        summary = summarize_flat_spells(events)
        self.assertEqual(summary["avoided_loss_count"], 2)
        self.assertEqual(summary["missed_gain_or_flat_count"], 1)
        self.assertAlmostEqual(summary["closed_negative_share_pct"], 50.0)
        self.assertAlmostEqual(summary["closed_compound_asset_return_pct"], -12.0)


class FrozenExperimentContractTest(unittest.TestCase):
    def test_fixed_trio_has_no_parameter_scan(self) -> None:
        config = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
        self.assertEqual(config["symbols"], ["MO", "AZO", "TLT"])
        self.assertEqual(config["parameters"]["targets"], ["MO", "AZO", "TLT"])
        self.assertEqual(config["parameters"]["sma_window"], 200)
        self.assertEqual(config["parameters"]["entry_buffer_pct"], 3.0)
        self.assertEqual(config["parameters"]["exit_buffer_pct"], 3.0)
        self.assertFalse(config["parameters"]["first_valid_sma_bar_can_enter"])
        self.assertEqual(config["parameters"]["formal_path_count"], 6)
        self.assertNotIn("sma_window_range", config["parameters"])

    def test_reference_engine_reenters_only_on_true_upper_cross(self) -> None:
        raw = pd.DataFrame(
            {
                "symbol": "TEST",
                "date": pd.bdate_range("2024-01-02", periods=12),
                "open": [100, 100, 109, 110, 82, 80, 108, 110, 82, 80, 108, 110],
                "high": [101, 101, 111, 111, 83, 81, 111, 111, 83, 81, 111, 111],
                "low": [99, 99, 108, 109, 79, 79, 107, 109, 79, 79, 107, 109],
                "close": [100, 100, 110, 110, 80, 80, 110, 110, 80, 80, 110, 110],
                "volume": 1000,
            }
        )
        data = analysis_slice(prepare_sma_data(raw, window=2), 2)
        spec = SmaThresholdSpec(a_pct=3.0, b_pct=3.0, window=2)
        result = run_reference_threshold(data, spec, ExplicitFillPolicy("open", 0.0))
        sides = result.orders["type"].tolist()
        self.assertEqual(sides[:3], ["buy", "sell", "buy"])
        self.assertGreaterEqual(sides.count("buy"), 2)
        # The first valid SMA row is only prior state; first fill is later.
        self.assertGreater(pd.Timestamp(result.orders.iloc[0]["date"]), pd.Timestamp(data.iloc[1]["date"]))


if __name__ == "__main__":
    unittest.main()
