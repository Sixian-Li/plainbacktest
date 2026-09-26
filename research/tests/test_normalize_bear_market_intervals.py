from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/normalize_bear_market_intervals.py"
SPEC = importlib.util.spec_from_file_location("normalize_bear_market_intervals", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NormalizeBearMarketIntervalsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.export_path = root / "export.json"
        self.qqq_path = root / "QQQ.csv"
        self.spy_path = root / "SPY.csv"
        self.export_path.write_text("{}", encoding="utf-8")
        self.qqq_path.write_text("x", encoding="utf-8")
        self.spy_path.write_text("x", encoding="utf-8")
        dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
        self.prices = {
            "QQQ": pd.Series([100.0, 75.0, 80.0, 90.0], index=dates),
            "SPY": pd.Series([100.0, 85.0, 90.0, 95.0], index=dates),
        }

    def test_classifies_with_worst_symbol_and_preserves_boundaries(self) -> None:
        export = {
            "view_id": "test",
            "exported_at": "2026-01-08T00:00:00Z",
            "intervals": [
                {"id": "b", "label": "手画 2", "start": "2026-01-06", "end": "2026-01-07", "note": "小"},
                {"id": "a", "label": "手画 1", "start": "2026-01-02", "end": "2026-01-05", "note": "大"},
            ],
        }
        payload = MODULE.normalize(
            export,
            prices=self.prices,
            major_threshold=0.20,
            source_export=self.export_path,
            source_paths={"QQQ": self.qqq_path, "SPY": self.spy_path},
            generated_at_utc="2026-01-08T00:00:00Z",
        )

        self.assertEqual(payload["counts"], {"total": 2, "major": 1, "minor": 1})
        self.assertEqual(payload["intervals"][0]["start"], "2026-01-02")
        self.assertEqual(payload["intervals"][0]["severity"], "major")
        self.assertEqual(payload["intervals"][0]["classification"]["worst_symbol"], "QQQ")
        self.assertAlmostEqual(payload["intervals"][0]["QQQ"]["max_drawdown"], -0.25)
        self.assertEqual(payload["intervals"][1]["severity"], "minor")
        self.assertEqual(payload["intervals"][1]["note"], "小")

    def test_rejects_overlapping_intervals(self) -> None:
        export = {
            "intervals": [
                {"start": "2026-01-02", "end": "2026-01-06"},
                {"start": "2026-01-05", "end": "2026-01-07"},
            ]
        }
        with self.assertRaisesRegex(ValueError, "overlaps"):
            MODULE.normalize(
                export,
                prices=self.prices,
                major_threshold=0.20,
                source_export=self.export_path,
                source_paths={"QQQ": self.qqq_path, "SPY": self.spy_path},
                generated_at_utc="2026-01-08T00:00:00Z",
            )

    def test_peak_to_trough_policy_removes_leading_and_rebound_sessions(self) -> None:
        export = {
            "intervals": [
                {
                    "id": "a",
                    "label": "手画窗口",
                    "start": "2026-01-02",
                    "end": "2026-01-07",
                }
            ]
        }
        prices = {
            "QQQ": pd.Series([95.0, 100.0, 75.0, 90.0], index=self.prices["QQQ"].index),
            "SPY": pd.Series([100.0, 102.0, 85.0, 95.0], index=self.prices["SPY"].index),
        }

        payload = MODULE.normalize(
            export,
            prices=prices,
            major_threshold=0.20,
            source_export=self.export_path,
            source_paths={"QQQ": self.qqq_path, "SPY": self.spy_path},
            boundary_policy="worst-peak-to-trough",
            generated_at_utc="2026-01-08T00:00:00Z",
        )

        interval = payload["intervals"][0]
        self.assertEqual((interval["start"], interval["end"]), ("2026-01-05", "2026-01-06"))
        self.assertEqual(interval["candidate_window"]["start"], "2026-01-02")
        self.assertEqual(interval["candidate_window"]["end"], "2026-01-07")
        self.assertEqual(interval["boundary_adjustment"]["leading_sessions_removed"], 1)
        self.assertEqual(interval["boundary_adjustment"]["trailing_sessions_removed"], 1)
        self.assertTrue(interval["boundary_adjustment"]["rebound_excluded"])
        self.assertAlmostEqual(interval["QQQ"]["total_return"], -0.25)
        self.assertFalse(payload["subjective_boundaries_preserved"])


if __name__ == "__main__":
    unittest.main()
