import unittest

from scripts.run_stochrsi_multihorizon_robustness import build_holdout_window, build_windows, score_surface


class MultiHorizonRobustnessTest(unittest.TestCase):
    def test_narrow_2000_2015_grid_and_windows_are_frozen(self):
        import json
        from pathlib import Path
        import pandas as pd
        from scripts.run_stochrsi_cross_period_grid import period_values
        path=Path("experiments/TIM/TIM-v0.70a.7__26-08-25__qqq_stochrsi_narrow_robust_2000_2015/experiment.json")
        p=json.loads(path.read_text())["parameters"]
        pairs=[(s,l) for s in period_values(p["short_period_start"],p["short_period_end"],p["period_step"]) for l in period_values(p["long_period_start"],p["long_period_end"],p["period_step"])]
        self.assertEqual(len(pairs),121)
        raw=pd.read_csv("../data/processed/daily/QQQ.csv",parse_dates=["date"])
        windows=build_windows(raw,p)
        self.assertEqual(len(windows),132)
        self.assertLessEqual(max(w["end"] for w in windows),pd.Timestamp("2015-12-31"))

    def test_frozen_scope_has_141_windows(self):
        import pandas as pd
        raw = pd.read_csv("../data/processed/daily/QQQ.csv", parse_dates=["date"])
        params = {"research_start": "2010-01-04", "horizons_years": [3, 5, 7],
                  "horizon_last_start": {"3":"2023-09-30","5":"2021-09-30","7":"2019-09-30"},
                  "expected_windows": {"3":55,"5":47,"7":39}}
        windows = build_windows(raw, params)
        self.assertEqual(len(windows), 141)
        self.assertEqual({h: sum(w["horizon_years"] == h for w in windows) for h in (3,5,7)}, {3:55,5:47,7:39})

    def test_scoring_recovers_horizon_from_saved_cohort(self):
        import pandas as pd
        rows=[]; benchmarks=[]
        for horizon in (3,5,7):
            for year in range(2010,2024):
                window=f"H{horizon}_{year}Q1"
                benchmarks.append({"window_id":window,"cagr_pct":5.0,"sharpe":0.5,"max_drawdown_pct":-30.0})
                for short in (14,16,18):
                    for long in (70,72,74):
                        rows.append({"case_id":f"P{short:03d}_{long:03d}","short_period":short,"long_period":long,"cohort":f"H{horizon}","window_id":window,"window_start":f"{year}-01-02","cagr_pct":10.0,"sharpe":1.0,"max_drawdown_pct":-20.0})
        params={"chronological_blocks":[[2010,2013],[2014,2017],[2018,2023]],"period_step":2,"gates":{"minimum_worst_horizon_q25_joint_rank":0,"minimum_worst_time_block_median_joint_rank":0,"minimum_leave_one_start_year_out_q25_joint_rank":0,"minimum_worst_local_3x3_score":0,"minimum_cagr_win_rate_vs_buy_hold":0,"minimum_sharpe_win_rate_vs_buy_hold":0,"minimum_drawdown_win_rate_vs_buy_hold":0}}
        surface, selection=score_surface(pd.DataFrame(rows),pd.DataFrame(benchmarks),params)
        self.assertEqual(len(surface),9)
        self.assertIsNotNone(selection["robust_champion"])

    def test_2005_2020_training_never_uses_holdout(self):
        import pandas as pd
        raw=pd.read_csv("../data/processed/daily/QQQ.csv",parse_dates=["date"])
        params={"research_start":"2005-01-03","horizons_years":[3,5,7],"horizon_last_start":{"3":"2017-12-31","5":"2015-12-31","7":"2013-12-31"},"expected_windows":{"3":52,"5":44,"7":36},"holdout_start":"2021-01-04","holdout_end":"2026-08-04"}
        training=build_windows(raw,params); holdout=build_holdout_window(raw,params)
        self.assertEqual(len(training),132)
        self.assertLessEqual(max(w["end"] for w in training),pd.Timestamp("2020-12-31"))
        self.assertEqual(holdout[0]["start"],pd.Timestamp("2021-01-04"))


if __name__ == "__main__":
    unittest.main()
