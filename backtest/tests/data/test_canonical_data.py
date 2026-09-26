from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from quantkit.paths import BACKTEST_ROOT


PROJECT_ROOT = BACKTEST_ROOT.parent
MANIFEST_PATH = PROJECT_ROOT / "data/processed/manifest.json"
QUALITY_PATH = PROJECT_ROOT / "data/processed/quality_report.json"
REGISTRY_PATH = PROJECT_ROOT / "data/source_registry.yaml"
EXPECTED_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


class CanonicalDataIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The distribution ships an immutable canonical snapshot. Tests read it;
        # rebuilding from optional source archives is a separate operation.
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.quality = json.loads(QUALITY_PATH.read_text(encoding="utf-8"))
        cls.manifest_by_symbol = {
            item["symbol"]: item for item in cls.manifest["datasets"]
        }
        cls.report_by_symbol = {
            item["symbol"]: item for item in cls.quality["reports"]
        }

    def test_registered_symbols_and_roles(self) -> None:
        datasets = {item["symbol"]: item for item in self.registry["datasets"]}
        self.assertEqual(set(datasets), {"QQQ", "VOO", "SPY", "RKLB"})
        self.assertEqual(datasets["QQQ"]["role"], "primary_backtest_input")
        self.assertEqual(datasets["SPY"]["role"], "long_history_proxy_and_validation")
        self.assertEqual(datasets["VOO"]["registered_status"], "provisional")
        self.assertEqual(datasets["RKLB"]["role"], "single_asset_backtest_input")

    def test_expected_quality_gates(self) -> None:
        self.assertEqual(self.report_by_symbol["QQQ"]["effective_status"], "approved")
        self.assertEqual(self.report_by_symbol["SPY"]["effective_status"], "approved")
        self.assertEqual(self.report_by_symbol["RKLB"]["effective_status"], "approved")
        voo = self.report_by_symbol["VOO"]
        self.assertEqual(voo["effective_status"], "quality_failed")
        self.assertEqual(voo["blocking_counts"], {"invalid_ohlc": 1})

    def test_coverage_and_row_counts(self) -> None:
        expected = {
            "QQQ": (6893, "1999-03-10", "2026-08-04"),
            "VOO": (3998, "2010-09-09", "2026-08-04"),
            "SPY": (8435, "1993-01-29", "2026-08-04"),
            "RKLB": (1240, "2021-08-25", "2026-08-04"),
        }
        for symbol, (rows, start, end) in expected.items():
            item = self.manifest_by_symbol[symbol]
            self.assertEqual((item["rows"], item["start"], item["end"]), (rows, start, end))

    def test_canonical_files_have_stable_schema_order_and_hash(self) -> None:
        for symbol, item in self.manifest_by_symbol.items():
            path = PROJECT_ROOT / item["output"]
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["canonical_sha256"])
            with path.open("r", encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                self.assertEqual(reader.fieldnames, EXPECTED_COLUMNS)
                dates = []
                symbols = set()
                for row in reader:
                    dates.append(row["date"])
                    symbols.add(row["symbol"])
            self.assertEqual(dates, sorted(dates))
            self.assertEqual(len(dates), len(set(dates)))
            self.assertEqual(symbols, {symbol})

    def test_source_content_hashes_match_exact_csv_or_zip_member(self) -> None:
        registry_by_symbol = {item["symbol"]: item for item in self.registry["datasets"]}
        missing = [item["source"]["path"] for item in registry_by_symbol.values()
                   if not (PROJECT_ROOT / item["source"]["path"]).is_file()]
        if missing:
            self.skipTest("optional original source archives are not distributed")
        for symbol, manifest_item in self.manifest_by_symbol.items():
            source = registry_by_symbol[symbol]["source"]
            path = PROJECT_ROOT / source["path"]
            if source["kind"] == "csv":
                content = path.read_bytes()
            else:
                with zipfile.ZipFile(path) as archive:
                    content = archive.read(source["member"])
            self.assertEqual(
                hashlib.sha256(content).hexdigest(),
                manifest_item["source_content_sha256"],
            )

    def test_voo_bad_row_is_preserved_and_not_silently_repaired(self) -> None:
        path = PROJECT_ROOT / self.manifest_by_symbol["VOO"]["output"]
        with path.open("r", encoding="utf-8", newline="") as file:
            rows = {row["date"]: row for row in csv.DictReader(file)}
        row = rows["2018-11-15"]
        self.assertEqual(row["open"], "218.706")
        self.assertEqual(row["low"], "218.918")
        self.assertLess(float(row["open"]), float(row["low"]))

    def test_rklb_excludes_spac_predecessor_and_matches_secondary_source(self) -> None:
        report = self.report_by_symbol["RKLB"]
        exclusion = report["exclusions"]["before_canonical_start"]
        self.assertEqual(exclusion["rows"], 187)
        self.assertEqual(exclusion["source_start"], "2020-11-24")
        self.assertEqual(exclusion["source_end"], "2021-08-24")

        primary_path = (
            PROJECT_ROOT
            / "data/2026-08-05多个数据包_rethink/纳斯达克 100 成分股/纳斯达克 100 成分股-拆股及股息调整_20260805.zip"
        )
        secondary_path = (
            PROJECT_ROOT / "data/2026-08-05美股数据_全/日线_前复权_美股数据.zip"
        )
        if not secondary_path.is_file():
            self.skipTest("optional full-market secondary archive is not distributed")
        with zipfile.ZipFile(primary_path) as archive:
            primary_rows = list(
                csv.DictReader(
                    (line.decode("utf-8-sig") for line in archive.open("RKLB.csv"))
                )
            )
        with zipfile.ZipFile(secondary_path) as archive:
            secondary_rows = list(
                csv.DictReader(
                    (line.decode("utf-8-sig") for line in archive.open("RKLB_daily_qfq.csv"))
                )
            )
        primary = {
            row["Date"]: float(row["Close"])
            for row in primary_rows
            if row["Date"] >= "2021-08-25"
        }
        secondary = {
            row["日期"]: float(row["收盘价"])
            for row in secondary_rows
            if row["日期"] >= "2021-08-25"
        }
        self.assertEqual(primary, secondary)
        self.assertEqual(len(primary), 1240)

        expected_sessions = (
            xcals.get_calendar("XNAS")
            .sessions_in_range("2021-08-25", "2026-08-04")
            .tz_localize(None)
        )
        actual_sessions = pd.DatetimeIndex(pd.to_datetime(list(primary)))
        self.assertTrue(actual_sessions.equals(expected_sessions))

    def test_calendar_cross_check_separates_voo_gap(self) -> None:
        analysis = self.quality["calendar_analysis"]
        self.assertEqual(analysis["calendar_status"], "suspect")
        specific = {
            item["date"]: item for item in analysis["dataset_specific_missing_dates"]
        }
        self.assertEqual(specific["2015-04-09"]["missing_symbols"], ["VOO"])
        suspect_dates = {item["date"] for item in analysis["calendar_suspect_dates"]}
        self.assertIn("2025-01-26", suspect_dates)
        self.assertIn("2023-06-20", suspect_dates)

    def test_unchanged_rebuild_is_byte_reproducible(self) -> None:
        if not all((PROJECT_ROOT / item["source"]["path"]).is_file()
                   for item in self.registry["datasets"]):
            self.skipTest("full rebuild requires optional original source archives")
        tracked = (
            MANIFEST_PATH,
            QUALITY_PATH,
            PROJECT_ROOT / "data/processed/quality_report.md",
        )
        before = {path: path.read_bytes() for path in tracked}
        subprocess.run(
            [sys.executable, "scripts/build_canonical_data.py"],
            cwd=BACKTEST_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual({path: path.read_bytes() for path in tracked}, before)


if __name__ == "__main__":
    unittest.main()
