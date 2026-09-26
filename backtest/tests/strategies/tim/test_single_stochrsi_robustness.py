import unittest
from scripts.run_single_stochrsi_robustness import complete_local_min, period_values, rank_near_misses

class SingleStochRsiRobustnessTest(unittest.TestCase):
    def test_frozen_grid_has_every_integer_period(self):
        self.assertEqual(period_values(14,200,1),list(range(14,201)))

    def test_local_gate_requires_both_neighbors(self):
        import pandas as pd
        rows=pd.DataFrame({"period":[14,15,16],"base_score":[.9,.8,.9]})
        local={r.period:r.base_score for r in rows.itertuples()}
        self.assertTrue(pd.isna(complete_local_min(14,local)))
        self.assertEqual(complete_local_min(15,local),.8)
        self.assertTrue(pd.isna(complete_local_min(16,local)))

    def test_near_miss_prefers_more_gates_over_raw_maximin(self):
        import pandas as pd
        rows=pd.DataFrame([
            {"period":132,"gate_pass_count":3,"maximin_score":.50,"sharpe_win_rate_vs_buy_hold":.25,"cagr_win_rate_vs_buy_hold":.11},
            {"period":36,"gate_pass_count":6,"maximin_score":.44,"sharpe_win_rate_vs_buy_hold":.86,"cagr_win_rate_vs_buy_hold":.58},
        ])
        self.assertEqual(int(rank_near_misses(rows).iloc[0].period),36)

if __name__ == "__main__": unittest.main()
