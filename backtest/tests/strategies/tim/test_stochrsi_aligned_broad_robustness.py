import json
import unittest
from pathlib import Path

import pandas as pd

from scripts.run_stochrsi_aligned_broad_robustness import aligned_buy_hold, build_windows, score_surface
from scripts.run_stochrsi_cross_period_grid import period_values


class AlignedBroadRobustnessTest(unittest.TestCase):
    def test_grid_and_windows_are_frozen(self):
        path = Path("experiments/TIM/TIM-v0.70a.8__26-08-25__qqq_stochrsi_first_entry_aligned_broad_robust_2000_2015/experiment.json")
        p = json.loads(path.read_text())["parameters"]
        pairs = [(s, l) for s in period_values(p["short_period_start"], p["short_period_end"], p["period_step"]) for l in period_values(p["long_period_start"], p["long_period_end"], p["period_step"])]
        self.assertEqual(len(pairs), 3367)
        raw = pd.read_csv("../data/processed/daily/QQQ.csv", parse_dates=["date"])
        windows = build_windows(raw, p)
        self.assertEqual(len(windows), 132)
        self.assertLessEqual(max(w["end"] for w in windows), pd.Timestamp("2015-12-31"))

    def test_aligned_benchmark_waits_then_uses_exact_strategy_fill(self):
        daily = pd.DataFrame({"date": pd.to_datetime(["2000-01-03", "2000-01-04", "2000-01-05"]), "symbol": ["QQQ"]*3, "close": [100., 110., 120.]})
        orders = pd.DataFrame([{"symbol":"QQQ", "type":"buy", "date":pd.Timestamp("2000-01-04"), "fill_price":105., "raw_fill_price":104.9, "theoretical_trigger":104.8}])
        benchmark, benchmark_orders, _ = aligned_buy_hold(daily, orders, initial_cash=100000)
        self.assertEqual(benchmark.loc[0, "equity"], 100000.)
        self.assertAlmostEqual(benchmark.loc[1, "shares"], 100000./105.)
        self.assertEqual(benchmark_orders.loc[0, "fill_price"], 105.)

    def test_score_uses_case_specific_aligned_benchmark(self):
        rows = []
        for horizon in (3, 5, 7):
            for year in range(2000, 2013):
                window = f"H{horizon}_{year}Q1"
                for short in (20, 21, 22):
                    for long in (87, 88, 89):
                        rows.append({"case_id":f"P{short:03d}_{long:03d}", "short_period":short, "long_period":long, "cohort":f"H{horizon}", "window_id":window, "window_start":f"{year}-01-03", "cagr_pct":10., "sharpe":1., "max_drawdown_pct":-20., "aligned_bh_cagr_pct":9., "aligned_bh_sharpe":.8, "aligned_bh_max_drawdown_pct":-25.})
        p = {"chronological_blocks":[[2000,2004],[2005,2008],[2009,2012]], "period_step":1, "gates":{"minimum_worst_horizon_q25_joint_rank":0., "minimum_worst_time_block_median_joint_rank":0., "minimum_leave_one_start_year_out_q25_joint_rank":0., "minimum_worst_local_3x3_score":0., "minimum_cagr_win_rate_vs_aligned_buy_hold":1., "minimum_sharpe_win_rate_vs_aligned_buy_hold":1., "minimum_drawdown_win_rate_vs_aligned_buy_hold":1.}}
        surface, selection = score_surface(pd.DataFrame(rows), p)
        self.assertEqual(len(surface), 9)
        self.assertIsNotNone(selection["robust_champion"])


if __name__ == "__main__":
    unittest.main()
