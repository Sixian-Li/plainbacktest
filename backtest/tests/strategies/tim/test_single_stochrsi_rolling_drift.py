import unittest

import pandas as pd

from scripts.analyze_single_stochrsi_rolling_drift import drift_statistics, response_figure
from scripts.run_single_stochrsi_rolling_drift import period_values, rolling_windows


class SingleStochRsiRollingDriftTests(unittest.TestCase):
    def test_period_grid_matches_frozen_request(self):
        values = period_values(14, 210, 7)
        self.assertEqual(values[:3], [14, 21, 28])
        self.assertEqual(values[-2:], [203, 210])
        self.assertEqual(len(values), 29)

    def test_period_grid_rejects_non_divisible_bounds(self):
        with self.assertRaises(ValueError):
            period_values(14, 209, 7)

    def test_windows_use_exclusive_end_label_year(self):
        dates = pd.DataFrame({"date": pd.to_datetime([
            "2005-01-03", "2009-12-31", "2010-01-04", "2014-12-31", "2015-01-02", "2019-12-31"
        ])})
        windows = rolling_windows(dates, start_year=2005, last_start_year=2015, interval_years=5)
        self.assertEqual(windows[0]["window_id"], "W2005_2010")
        self.assertEqual(windows[0]["start"], pd.Timestamp("2005-01-03"))
        self.assertEqual(windows[0]["end"], pd.Timestamp("2009-12-31"))
        self.assertEqual(windows[-1]["window_id"], "W2015_2020")
        self.assertEqual(windows[-1]["end"], pd.Timestamp("2019-12-31"))

    def test_drift_statistics_count_direction_changes(self):
        best = pd.DataFrame({"start_year": [2005, 2006, 2007, 2008], "best": [14, 21, 21, 14]})
        result = drift_statistics(best, "best", "test")
        self.assertEqual(result["up_steps"], 1)
        self.assertEqual(result["down_steps"], 1)
        self.assertEqual(result["flat_steps"], 1)
        self.assertEqual(result["direction_changes"], 1)
        self.assertFalse(result["monotonic_non_decreasing"])

    def test_response_chart_exposes_eleven_window_series(self):
        rows = []
        for year in range(2005, 2016):
            for period in (14, 21):
                rows.append({
                    "window_id": f"W{year}_{year + 5}", "window_label": f"{year}–{year + 5}",
                    "period": period, "cagr_pct": period / 10, "total_return_pct": period,
                    "sharpe": period / 100, "exposure_pct": 50,
                })
        figure = response_figure(pd.DataFrame(rows), "cagr_pct", "CAGR")
        toggles = [trace for trace in figure.data if trace.meta.get("panel") == "market"]
        self.assertEqual(len(toggles), 11)
        self.assertEqual(len({trace.meta["series_key"] for trace in toggles}), 11)


if __name__ == "__main__":
    unittest.main()
