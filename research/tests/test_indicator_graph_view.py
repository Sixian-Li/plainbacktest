from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "build_indicator_graph_view.py"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("build_indicator_graph_view", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IndicatorGraphViewTest(unittest.TestCase):
    def frame(self, rows: int = 500) -> pd.DataFrame:
        close = 100 + np.sin(np.arange(rows) / 7) * 3 + np.arange(rows) / 10
        return pd.DataFrame(
            {
                "date": pd.date_range("2020-01-01", periods=rows, freq="D"),
                "symbol": "TEST",
                "close": close,
            }
        )

    def test_requested_ranges_are_exact(self) -> None:
        self.assertEqual(
            MODULE.integer_range(10, 350, 10, label="SMA"),
            tuple(range(10, 351, 10)),
        )
        self.assertEqual(
            MODULE.integer_range(14, 210, 14, label="StochRSI"),
            tuple(range(14, 211, 14)),
        )
        with self.assertRaises(ValueError):
            MODULE.integer_range(14, 211, 14, label="StochRSI")

    def test_prepare_view_keeps_history_and_warmup_nulls(self) -> None:
        frame = self.frame()
        view = MODULE.prepare_view(
            frame,
            symbol="TEST",
            sma_windows=(10, 350),
            stochrsi_periods=(14, 210),
        )
        self.assertEqual(len(view), 500)
        self.assertTrue(view.iloc[:9]["sma10"].isna().all())
        self.assertAlmostEqual(view.iloc[9]["sma10"], frame.iloc[:10]["close"].mean())
        self.assertTrue(view.iloc[:349]["sma350"].isna().all())
        self.assertFalse(pd.isna(view.iloc[349]["sma350"]))
        self.assertTrue(view.iloc[:27]["stochrsi14"].isna().all())
        self.assertTrue(view["stochrsi14"].dropna().between(0.0, 1.0).all())
        self.assertTrue(view["stochrsi210"].dropna().between(0.0, 1.0).all())

    def test_figure_has_all_selectable_traces_and_defaults(self) -> None:
        sma_windows = MODULE.integer_range(10, 350, 10, label="SMA")
        periods = MODULE.integer_range(14, 210, 14, label="StochRSI")
        view = MODULE.prepare_view(
            self.frame(),
            symbol="TEST",
            sma_windows=sma_windows,
            stochrsi_periods=periods,
        )
        figure = MODULE.build_figure(
            view,
            symbol="TEST",
            sma_windows=sma_windows,
            stochrsi_periods=periods,
        )
        self.assertEqual(len(figure.data), 51)
        self.assertEqual(figure.data[0].meta["series_key"], "close")
        visible_sma = {
            int(trace.meta["series_key"].removeprefix("sma"))
            for trace in figure.data
            if trace.meta["group"] == "sma" and trace.visible is True
        }
        visible_stoch = {
            int(trace.meta["series_key"].removeprefix("stochrsi"))
            for trace in figure.data
            if trace.meta["group"] == "stochrsi" and trace.visible is True
        }
        self.assertEqual(visible_sma, set(MODULE.DEFAULT_VISIBLE_SMA))
        self.assertEqual(visible_stoch, set(MODULE.DEFAULT_VISIBLE_STOCHRSI))
        self.assertEqual(figure.layout.yaxis.type, "log")
        self.assertEqual(tuple(figure.layout.yaxis2.range), (-0.03, 1.03))

    def test_html_has_checkbox_controls_and_no_table(self) -> None:
        sma_windows = MODULE.integer_range(10, 350, 10, label="SMA")
        periods = MODULE.integer_range(14, 210, 14, label="StochRSI")
        view = MODULE.prepare_view(
            self.frame(),
            symbol="TEST",
            sma_windows=sma_windows,
            stochrsi_periods=periods,
        )
        rendered = MODULE.build_html(
            view,
            symbol="TEST",
            sma_windows=sma_windows,
            stochrsi_periods=periods,
            source_label="test.csv",
        )
        self.assertIn(f'name="quant-view" content="{MODULE.VIEW_ID}"', rendered)
        self.assertEqual(rendered.count('data-series="'), 51)
        self.assertEqual(rendered.count('data-default="true" checked'), 9)
        self.assertIn('data-group-action="sma-all"', rendered)
        self.assertIn('data-group-action="stochrsi-none"', rendered)
        self.assertIn("价格对数轴", rendered)
        self.assertNotIn("<table", rendered)
        self.assertNotIn("分页", rendered)

    def test_invalid_close_is_rejected(self) -> None:
        frame = self.frame(40)
        frame.loc[5, "close"] = 0
        with self.assertRaises(ValueError):
            MODULE.prepare_view(
                frame,
                symbol="TEST",
                sma_windows=(10,),
                stochrsi_periods=(14,),
            )


if __name__ == "__main__":
    unittest.main()
