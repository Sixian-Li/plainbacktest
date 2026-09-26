import json
import unittest
from pathlib import Path

import pandas as pd

from scripts.run_stochrsi_cross_period_grid import period_values
from scripts.run_stochrsi_supergrid_heatmaps import (
    aligned_benchmark_summary,
    aligned_buy_hold,
    build_windows,
)


class StochRsiSupergridHeatmapsTest(unittest.TestCase):
    def test_supergrid_has_every_valid_pair_and_excludes_zero(self):
        path = Path("experiments/TIM/TIM-v0.70a.9__26-08-26__qqq_stochrsi_supergrid_heatmaps_2000_2015/experiment.json")
        p = json.loads(path.read_text())["parameters"]
        pairs = [(s, l) for s in period_values(p["short_period_start"], p["short_period_end"], p["period_step"]) for l in period_values(p["long_period_start"], p["long_period_end"], p["period_step"]) if s < l]
        self.assertEqual(len(pairs), 6738)
        self.assertEqual(min(s for s, _ in pairs), 2)
        self.assertTrue(all(s < l for s, l in pairs))

    def test_windows_are_exactly_the_2000_2015_training_scope(self):
        path = Path("experiments/TIM/TIM-v0.70a.9__26-08-26__qqq_stochrsi_supergrid_heatmaps_2000_2015/experiment.json")
        p = json.loads(path.read_text())["parameters"]
        raw = pd.read_csv("../data/processed/daily/QQQ.csv", parse_dates=["date"])
        windows = build_windows(raw, p)
        self.assertEqual(len(windows), 132)
        self.assertLessEqual(max(w["end"] for w in windows), pd.Timestamp("2015-12-31"))

    def test_fast_aligned_summary_matches_full_metrics(self):
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-05", "2000-01-06"]),
            "symbol": ["QQQ"] * 4,
            "close": [100., 110., 105., 120.],
        })
        orders = pd.DataFrame([{"symbol":"QQQ", "type":"buy", "date":pd.Timestamp("2000-01-04"), "fill_price":105., "raw_fill_price":104.9, "theoretical_trigger":104.8}])
        _, _, full = aligned_buy_hold(daily, orders, initial_cash=100000)
        fast = aligned_benchmark_summary(daily, orders, initial_cash=100000)
        for field in ("cagr_pct", "sharpe", "max_drawdown_pct"):
            self.assertAlmostEqual(full[field], fast[field], places=12)


if __name__ == "__main__":
    unittest.main()
