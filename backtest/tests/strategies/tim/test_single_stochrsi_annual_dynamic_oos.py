import unittest

import numpy as np
import pandas as pd

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data
from scripts.run_single_stochrsi_annual_dynamic_oos import (
    annual_returns,
    prepare_annual_schedule,
    validate_schedule,
)


class AnnualDynamicStochRsiTests(unittest.TestCase):
    def test_schedule_requires_a_full_gap_year(self):
        valid = [
            {"application_year": 2020, "source_end": "2018-12-31", "period": 14},
            {"application_year": 2021, "source_end": "2019-12-31", "period": 140},
        ]
        self.assertEqual(validate_schedule(valid, 2020, 2021), {2020: 14, 2021: 140})
        invalid = [
            {"application_year": 2020, "source_end": "2019-12-31", "period": 14},
            {"application_year": 2021, "source_end": "2019-12-31", "period": 140},
        ]
        with self.assertRaises(ValueError):
            validate_schedule(invalid, 2020, 2021)

    def test_year_boundary_uses_new_periods_own_prior_state(self):
        dates = pd.bdate_range("2018-01-01", "2021-12-31")
        close = 100 + np.linspace(0, 40, len(dates)) + 5 * np.sin(np.arange(len(dates)) / 7)
        raw = pd.DataFrame({
            "symbol": "QQQ", "date": dates, "open": close * 0.999, "high": close * 1.01,
            "low": close * 0.99, "close": close, "volume": 1_000_000,
        })
        schedule = [
            {"application_year": 2020, "source_end": "2018-12-31", "period": 14},
            {"application_year": 2021, "source_end": "2019-12-31", "period": 140},
        ]
        dynamic, _ = prepare_annual_schedule(
            raw, schedule, analysis_start=pd.Timestamp("2020-01-01"), analysis_end=pd.Timestamp("2021-12-31"),
            buy_threshold=.2, sell_threshold=.8, cost_bps=5,
        )
        fixed_140 = prepare_dual_stochrsi_data(raw, TimingSpec("CROSS", cost_bps=5, periods=(140,), buy_threshold=.2, sell_threshold=.8))
        date = pd.Timestamp("2021-01-01")
        dynamic_row = dynamic[dynamic.date.eq(date)].iloc[0]
        fixed_row = fixed_140[fixed_140.date.eq(date)].iloc[0]
        self.assertEqual(int(dynamic_row.selected_period), 140)
        for column in ("buy_trigger", "sell_trigger", "buy_eligible", "sell_eligible"):
            left, right = dynamic_row[column], fixed_row[column]
            if pd.isna(left) and pd.isna(right):
                continue
            self.assertEqual(left, right)

    def test_calendar_returns_chain_continuous_equity(self):
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2020-01-02", "2020-12-31", "2021-01-04", "2021-12-31"]),
            "equity": [100, 120, 108, 132],
        })
        result = annual_returns(daily, 100)
        self.assertAlmostEqual(result.iloc[0].return_pct, 20)
        self.assertAlmostEqual(result.iloc[1].return_pct, 10)


if __name__ == "__main__":
    unittest.main()
