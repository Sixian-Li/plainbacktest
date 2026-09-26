from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/build_bear_resilience_history_view.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_bear_resilience_history_view", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BearResilienceHistoryViewTest(unittest.TestCase):
    def test_universe_is_exactly_core12_near8_retail4(self) -> None:
        self.assertEqual(len(MODULE.all_symbols()), 24)
        self.assertEqual(len(set(MODULE.all_symbols())), 24)
        self.assertEqual(
            MODULE.all_symbols(),
            (
                "AZO", "TLT", "COR", "EXE", "DVA", "SJM", "SO", "ED",
                "GLD", "CHD", "HRL", "GILD", "HSY", "ORLY", "MO", "WRB",
                "EQT", "LMT", "GIS", "WEC", "DLTR", "DG", "WMT", "TSCO",
            ),
        )

    def test_smas_use_pre_window_history_without_hiding_early_close(self) -> None:
        dates = pd.date_range("2023-01-01", periods=500, freq="D")
        series = pd.Series(
            [100 + index for index in range(500)],
            index=dates,
            name="TEST",
            dtype=float,
        )
        view = MODULE.prepare_asset_view(
            series,
            start="2024-01-01",
            end="2024-02-10",
        )

        self.assertEqual(view.index[0], pd.Timestamp("2024-01-01"))
        self.assertEqual(view.index[-1], pd.Timestamp("2024-02-10"))
        self.assertFalse(math.isnan(view.iloc[0]["sma300"]))
        expected = series.loc[: "2024-01-01"].tail(300).mean()
        self.assertAlmostEqual(view.iloc[0]["sma300"], expected)

    def test_late_listing_keeps_true_first_date_and_sma_warmup_nulls(self) -> None:
        dates = pd.date_range("2025-01-01", periods=320, freq="D")
        series = pd.Series(range(1, 321), index=dates, name="LATE", dtype=float)
        view = MODULE.prepare_asset_view(
            series,
            start="1999-01-01",
            end="2026-08-04",
        )

        self.assertEqual(view.index[0], pd.Timestamp("2025-01-01"))
        self.assertTrue(math.isnan(view.iloc[0]["sma30"]))
        self.assertFalse(math.isnan(view.iloc[-1]["sma300"]))

    def test_interval_loader_preserves_six_major_and_six_minor(self) -> None:
        intervals = []
        for ordinal in range(1, 13):
            start = pd.Timestamp("2000-01-01") + pd.Timedelta(days=ordinal * 20)
            intervals.append(
                {
                    "interval_id": f"bear-{ordinal}",
                    "ordinal": ordinal,
                    "label": f"熊市 {ordinal}",
                    "severity": "major" if ordinal <= 6 else "minor",
                    "start": start.strftime("%Y-%m-%d"),
                    "end": (start + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "intervals.json"
            path.write_text(
                json.dumps({"dataset_id": "test", "intervals": intervals}),
                encoding="utf-8",
            )
            loaded, source = MODULE.load_intervals(path)

        self.assertEqual(source["dataset_id"], "test")
        self.assertEqual(len(loaded), 12)
        self.assertEqual(sum(row["severity"] == "major" for row in loaded), 6)
        self.assertEqual(sum(row["severity"] == "minor" for row in loaded), 6)

    def test_html_has_per_asset_sma_bear_and_axis_controls(self) -> None:
        dates = pd.date_range("2024-01-01", periods=400, freq="D")
        asset = {
            "symbol": "AZO",
            "company_name": "AutoZone",
            "group_id": "core12",
            "group_label": "核心层 12",
            "source_label": "AZO.csv",
            "actual_start": "2024-01-01",
            "actual_end": "2025-02-03",
            "sessions": 400,
            "available_smas": list(MODULE.SMA_WINDOWS),
            "dates": [date.strftime("%Y-%m-%d") for date in dates],
            "close": [float(index + 1) for index in range(400)],
            "smas": {
                str(window): [None] * (window - 1)
                + [float(index + 1) for index in range(400 - window + 1)]
                for window in MODULE.SMA_WINDOWS
            },
        }
        payload = {
            "view_id": MODULE.VIEW_ID,
            "requested_start": "1999-01-01",
            "requested_end": "2026-08-04",
            "sma_windows": list(MODULE.SMA_WINDOWS),
            "default_sma": 200,
            "default_bear_mode": "all",
            "groups": [
                {
                    "group_id": "core12",
                    "label": "核心层 12",
                    "description": "test",
                    "symbols": ["AZO"],
                }
            ],
            "intervals": [
                {
                    "interval_id": "bear-1",
                    "ordinal": 1,
                    "label": "大熊市 01",
                    "severity": "major",
                    "start": "2000-01-01",
                    "end": "2000-02-01",
                    "short_label": "大01",
                }
            ],
            "assets": [asset],
        }
        rendered = MODULE.build_html(payload)

        self.assertIn(f'name="quant-view" content="{MODULE.VIEW_ID}"', rendered)
        self.assertEqual(
            rendered.count('<input type="checkbox" data-control="sma"'), 5
        )
        self.assertIn('data-window="200" checked', rendered)
        self.assertIn('data-control="bear-mode"', rendered)
        self.assertIn('<option value="all" selected>全部熊市</option>', rendered)
        self.assertIn('data-control="log" checked', rendered)
        self.assertIn("window.__bearHistory", rendered)
        self.assertIn("IntersectionObserver", rendered)
        self.assertIn("type:'scatter'", rendered)
        self.assertNotIn("type:'scattergl'", rendered)


if __name__ == "__main__":
    unittest.main()
