from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from quantkit.nasdaq100_strategy1_energy_factor import (
    assign_energy_buckets,
    bootstrap_month_blocks,
    build_forward_events,
    daily_rank_ic,
    enrich_energy_states,
)


BUCKETS = [
    {"bucket_id": "E00_20", "lower": 0.0, "upper": 0.2, "upper_inclusive": False},
    {"bucket_id": "E20_50", "lower": 0.2, "upper": 0.5, "upper_inclusive": False},
    {"bucket_id": "E50_80", "lower": 0.5, "upper": 0.8, "upper_inclusive": False},
    {"bucket_id": "E80_90", "lower": 0.8, "upper": 0.9, "upper_inclusive": False},
    {"bucket_id": "E90_95", "lower": 0.9, "upper": 0.95, "upper_inclusive": False},
    {"bucket_id": "E95_99", "lower": 0.95, "upper": 0.99, "upper_inclusive": False},
    {"bucket_id": "E99_100", "lower": 0.99, "upper": 1.0, "upper_inclusive": True},
]


class EnergyStateTests(unittest.TestCase):
    def test_bucket_boundaries_are_frozen_and_non_overlapping(self) -> None:
        scores = pd.Series([0.0, 0.199999, 0.2, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0])
        actual = assign_energy_buckets(scores, BUCKETS).astype(str).tolist()
        self.assertEqual(
            actual,
            [
                "E00_20", "E00_20", "E20_50", "E50_80", "E80_90",
                "E90_95", "E95_99", "E99_100", "E99_100",
            ],
        )

    def test_direction_and_high_cross_use_exact_xnys_lags(self) -> None:
        calendar = pd.bdate_range("2020-01-02", periods=9)
        state = pd.DataFrame(
            {
                "date": calendar,
                "security_id": "A",
                "strategy1_weight": [0.10, 0.20, 0.30, 0.40, 0.90, 0.91, 0.95, 0.95, 0.89],
                "in_universe": True,
            }
        )
        enriched = enrich_energy_states(
            state,
            calendar,
            buckets=BUCKETS,
            direction_lookback=5,
            flat_tolerance=1e-12,
            high_threshold=0.90,
            percentile_window=252,
            percentile_minimum=2,
        )
        self.assertEqual(enriched.loc[5, "high_state"], "CROSS_ABOVE_90")
        self.assertEqual(enriched.loc[5, "direction"], "RISING")
        self.assertEqual(enriched.loc[7, "direction"], "RISING")
        self.assertEqual(enriched.loc[8, "high_state"], "CROSS_BELOW_90")
        self.assertFalse(bool(enriched.loc[4, "high_energy"]))
        self.assertTrue(bool(enriched.loc[5, "high_energy"]))

    def test_own_history_percentile_is_causal(self) -> None:
        calendar = pd.bdate_range("2020-01-02", periods=5)
        state = pd.DataFrame(
            {
                "date": calendar,
                "security_id": "A",
                "strategy1_weight": [0.4, 0.2, 0.6, 0.1, 0.8],
                "in_universe": True,
            }
        )
        enriched = enrich_energy_states(
            state,
            calendar,
            buckets=BUCKETS,
            direction_lookback=2,
            flat_tolerance=1e-12,
            high_threshold=0.90,
            percentile_window=3,
            percentile_minimum=2,
        )
        self.assertTrue(np.isnan(enriched.loc[0, "own_history_percentile"]))
        self.assertAlmostEqual(enriched.loc[1, "own_history_percentile"], 0.5)
        self.assertAlmostEqual(enriched.loc[2, "own_history_percentile"], 1.0)
        self.assertAlmostEqual(enriched.loc[3, "own_history_percentile"], 1 / 3)
        self.assertAlmostEqual(enriched.loc[4, "own_history_percentile"], 1.0)


class ForwardEventTests(unittest.TestCase):
    def _panel(self, calendar: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
        rows = []
        for symbol, base in (("A", 10.0), ("B", 20.0)):
            for index, date in enumerate(calendar):
                open_price = base + index
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "open": open_price,
                        "high": open_price + 1.0,
                        "low": open_price - 1.0,
                        "close": open_price + 0.5,
                        "synthetic_bar": 0,
                        "prelisting_proxy": 0,
                        "terminal_settlement_proxy": 0,
                    }
                )
        qqq = pd.DataFrame(
            {
                "date": calendar,
                "open": np.arange(len(calendar), dtype=float) + 100.0,
                "high": np.arange(len(calendar), dtype=float) + 101.0,
                "low": np.arange(len(calendar), dtype=float) + 99.0,
                "close": np.arange(len(calendar), dtype=float) + 100.5,
            }
        )
        return pd.DataFrame(rows), qqq

    def test_signal_close_enters_next_open_and_exits_requested_close(self) -> None:
        calendar = pd.bdate_range("2020-01-02", periods=8)
        panel, qqq = self._panel(calendar)
        state = pd.DataFrame(
            {
                "date": [calendar[0]],
                "security_id": ["A"],
                "strategy1_weight": [0.95],
                "energy_bucket": ["E95_99"],
                "direction": ["UNAVAILABLE"],
                "high_energy": [True],
                "high_state": ["HIGH_RECENT_UNAVAILABLE"],
                "own_history_percentile": [np.nan],
            }
        )
        events, exclusions, checks = build_forward_events(
            state, panel, qqq, calendar, horizons=(5,), cost_bps=0.0
        )
        self.assertEqual(len(exclusions), 0)
        self.assertEqual(events.loc[0, "entry_date"], calendar[1])
        self.assertEqual(events.loc[0, "exit_date"], calendar[5])
        self.assertAlmostEqual(events.loc[0, "security_return"], 15.5 / 11.0 - 1.0)
        self.assertAlmostEqual(events.loc[0, "maximum_favorable_excursion"], 16.0 / 11.0 - 1.0)
        self.assertAlmostEqual(events.loc[0, "maximum_adverse_excursion"], 10.0 / 11.0 - 1.0)
        self.assertLess(max(checks.values()), 1e-12)

    def test_synthetic_entry_is_excluded_not_deferred(self) -> None:
        calendar = pd.bdate_range("2020-01-02", periods=8)
        panel, qqq = self._panel(calendar)
        panel.loc[(panel["symbol"] == "A") & (panel["date"] == calendar[1]), "synthetic_bar"] = 1
        state = pd.DataFrame(
            {
                "date": [calendar[0]], "security_id": ["A"], "strategy1_weight": [0.5],
                "energy_bucket": ["E50_80"], "direction": ["UNAVAILABLE"],
                "high_energy": [False], "high_state": ["NOT_HIGH_UNAVAILABLE"],
                "own_history_percentile": [np.nan],
            }
        )
        events, exclusions, _ = build_forward_events(
            state, panel, qqq, calendar, horizons=(5,), cost_bps=0.0
        )
        self.assertTrue(events.empty)
        self.assertEqual(exclusions.loc[0, "reason"], "ENTRY_NOT_REAL")

    def test_terminal_event_ends_at_last_real_close_and_matches_qqq_interval(self) -> None:
        calendar = pd.bdate_range("2020-01-02", periods=8)
        panel, qqq = self._panel(calendar)
        mask = (panel["symbol"] == "A") & (panel["date"] > calendar[3])
        panel.loc[mask, "synthetic_bar"] = 1
        panel.loc[mask, "terminal_settlement_proxy"] = 1
        panel.loc[mask, ["open", "high", "low", "close"]] = [13.5, 13.5, 13.5, 13.5]
        state = pd.DataFrame(
            {
                "date": [calendar[0]], "security_id": ["A"], "strategy1_weight": [0.5],
                "energy_bucket": ["E50_80"], "direction": ["UNAVAILABLE"],
                "high_energy": [False], "high_state": ["NOT_HIGH_UNAVAILABLE"],
                "own_history_percentile": [np.nan],
            }
        )
        events, _, _ = build_forward_events(
            state, panel, qqq, calendar, horizons=(5,), cost_bps=0.0
        )
        self.assertEqual(events.loc[0, "exit_date"], calendar[3])
        self.assertTrue(bool(events.loc[0, "terminal_truncated"]))
        self.assertAlmostEqual(events.loc[0, "qqq_return"], 103.5 / 101.0 - 1.0)
        self.assertAlmostEqual(events.loc[0, "maximum_favorable_excursion"], 14.0 / 11.0 - 1.0)
        self.assertAlmostEqual(events.loc[0, "maximum_adverse_excursion"], 10.0 / 11.0 - 1.0)


class StatisticalTests(unittest.TestCase):
    def test_daily_rank_ic_honors_minimum_cross_section(self) -> None:
        dates = pd.to_datetime(["2020-01-02"] * 3 + ["2020-01-03"] * 2)
        events = pd.DataFrame(
            {
                "signal_date": dates,
                "horizon": 5,
                "strategy1_weight": [0.1, 0.5, 0.9, 0.2, 0.8],
                "own_history_percentile": [0.2, 0.5, 0.8, 0.4, 0.6],
                "excess_return": [-0.1, 0.0, 0.1, -0.2, 0.2],
            }
        )
        actual = daily_rank_ic(events, minimum_securities=3)
        self.assertEqual(len(actual), 2)
        raw = actual[actual["factor_variant"].eq("RAW_SCORE")]
        self.assertEqual(len(raw), 1)
        self.assertAlmostEqual(raw.iloc[0]["rank_ic"], 1.0)

    def test_month_block_bootstrap_is_deterministic(self) -> None:
        daily = pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=90),
                "spread": np.linspace(-0.02, 0.03, 90),
            }
        )
        first = bootstrap_month_blocks(daily, value_column="spread", replications=200, seed=7)
        second = bootstrap_month_blocks(daily, value_column="spread", replications=200, seed=7)
        self.assertEqual(first, second)
        self.assertLess(first["ci_lower"], first["mean"])
        self.assertGreater(first["ci_upper"], first["mean"])


if __name__ == "__main__":
    unittest.main()
