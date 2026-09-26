from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_sma_market_view.py"
SPEC = importlib.util.spec_from_file_location("build_sma_market_view", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SmaMarketViewTest(unittest.TestCase):
    def test_sma_average_and_daily_percentage_changes(self) -> None:
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=8, freq="D"),
                "symbol": ["QQQ"] * 8,
                "close": list(range(1, 9)),
            }
        )
        view = MODULE.prepare_view(
            frame,
            symbol="QQQ",
            start="2026-01-01",
            end="2026-01-08",
            windows=(2, 3, 4),
        )

        jan_5 = view.loc[view["date"] == pd.Timestamp("2026-01-05")].iloc[0]
        self.assertEqual(jan_5["sma2"], 4.5)
        self.assertEqual(jan_5["sma3"], 4.0)
        self.assertEqual(jan_5["sma4"], 3.5)
        self.assertEqual(jan_5["sma_average"], 4.0)
        self.assertAlmostEqual(jan_5["sma3_change_pct"], (4.0 / 3.0 - 1) * 100)
        self.assertAlmostEqual(
            jan_5["sma_average_change_pct"], (4.0 / 3.0 - 1) * 100
        )

    def test_full_interval_keeps_close_rows_before_sma_warmup(self) -> None:
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2026-01-01", periods=8, freq="D"),
                "symbol": ["QQQ"] * 8,
                "close": list(range(1, 9)),
            }
        )
        view = MODULE.prepare_view(
            frame,
            symbol="QQQ",
            start="2026-01-01",
            end="2026-01-08",
            windows=(2, 3, 4),
        )

        self.assertEqual(len(view), 8)
        self.assertEqual(view.iloc[0]["close"], 1)
        self.assertTrue(math.isnan(view.iloc[0]["sma4"]))
        self.assertTrue(math.isnan(view.iloc[3]["sma4_change_pct"]))
        self.assertFalse(math.isnan(view.iloc[4]["sma4_change_pct"]))

    def test_html_has_four_independent_derivative_checkboxes(self) -> None:
        windows = MODULE.sma_windows(25, 35, 5)
        overlay_windows = MODULE.sma_windows(70, 450, 10)
        self.assertEqual(windows, (25, 30, 35))
        self.assertEqual(len(overlay_windows), 39)
        self.assertEqual((overlay_windows[0], overlay_windows[-1]), (70, 450))
        frame = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=500, freq="D"),
                "symbol": ["QQQ"] * 500,
                "close": [100 + index / 10 for index in range(500)],
            }
        )
        view = MODULE.prepare_view(
            frame,
            symbol="QQQ",
            start="2024-01-01",
            end="2025-05-14",
            windows=windows,
            overlay_windows=overlay_windows,
        )
        rendered = MODULE.build_html(
            view,
            symbol="QQQ",
            requested_start="2024-01-01",
            requested_end="2025-05-14",
            windows=windows,
            overlay_windows=overlay_windows,
            source_label="test.csv",
        )

        self.assertIn('name="quant-view" content="close_sma_cluster_derivative_v4"', rendered)
        self.assertEqual(rendered.count('data-group="derivative" checked'), 4)
        self.assertEqual(rendered.count('data-group="overlay">'), 39)
        self.assertNotIn('data-group="overlay" checked', rendered)
        self.assertIn('data-series="sma70"', rendered)
        self.assertIn('data-series="sma450"', rendered)
        for key in (
            "sma25_change_pct",
            "sma30_change_pct",
            "sma35_change_pct",
            "sma_average_change_pct",
        ):
            self.assertIn(f'data-series="{key}"', rendered)
        self.assertIn("相对前一交易日变化", rendered)
        self.assertIn("(今日值 / 前一交易日值 − 1) × 100%", rendered)
        self.assertNotIn("Open/Close", rendered)


if __name__ == "__main__":
    unittest.main()
