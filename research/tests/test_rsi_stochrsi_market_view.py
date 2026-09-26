from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_rsi_stochrsi_market_view.py"
SPEC = importlib.util.spec_from_file_location("build_rsi_stochrsi_market_view", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RsiStochRsiMarketViewTest(unittest.TestCase):
    def test_wilder_rsi_matches_known_recursion(self) -> None:
        close = pd.Series([1.0, 2.0, 3.0, 2.0, 4.0])
        actual = MODULE.wilder_rsi(close, 3)
        self.assertTrue(actual.iloc[:3].isna().all())
        self.assertAlmostEqual(actual.iloc[3], 66.6666666667, places=9)
        self.assertAlmostEqual(actual.iloc[4], 83.3333333333, places=9)

    def test_prepare_view_prewarms_and_scales_both_indicators(self) -> None:
        rng = np.random.default_rng(7)
        close = 100 + np.cumsum(rng.normal(0.1, 1.0, 180))
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2019-09-01", periods=180, freq="D"),
                "symbol": "QQQ",
                "close": close,
            }
        )
        view = MODULE.prepare_view(
            frame,
            symbol="QQQ",
            start="2020-01-01",
            end="2020-02-20",
            periods=(14, 28),
        )
        self.assertEqual(view.iloc[0]["date"], pd.Timestamp("2020-01-01"))
        for column in ("rsi14", "stochrsi14", "rsi28", "stochrsi28"):
            self.assertTrue(view[column].notna().all(), msg=column)
            self.assertTrue(view[column].between(0.0, 1.0).all(), msg=column)

    def test_html_has_one_checkbox_and_two_traces_per_period(self) -> None:
        periods = MODULE.DEFAULT_PERIODS
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2018-01-01", periods=900, freq="D"),
                "symbol": "QQQ",
                "close": 100 + np.sin(np.arange(900) / 9) * 5 + np.arange(900) / 20,
            }
        )
        view = MODULE.prepare_view(
            frame,
            symbol="QQQ",
            start="2020-01-01",
            end="2020-06-01",
            periods=periods,
        )
        rendered = MODULE.build_html(
            view,
            symbol="QQQ",
            requested_start="2020-01-01",
            requested_end="2020-06-01",
            periods=periods,
            source_label="test.csv",
        )
        self.assertIn(f'name="quant-view" content="{MODULE.VIEW_ID}"', rendered)
        self.assertEqual(rendered.count('data-period="'), len(periods))
        self.assertEqual(rendered.count('data-period="14" checked'), 1)
        for period in periods:
            self.assertIn(f"rsi{period}", rendered)
            self.assertIn(f"stochrsi{period}", rendered)
        self.assertIn("不计算 K/D", rendered)
        self.assertIn("RSI 除以 100", rendered)

    def test_period_parser_rejects_duplicates(self) -> None:
        self.assertEqual(MODULE.parse_periods("14,28,42"), (14, 28, 42))
        with self.assertRaises(ValueError):
            MODULE.parse_periods("14,14")


if __name__ == "__main__":
    unittest.main()
