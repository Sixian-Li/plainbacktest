from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT


WORKSPACE_ROOT = BACKTEST_ROOT.parent
REGISTRY_PATH = WORKSPACE_ROOT / "data/sp500_history_registry.json"
MANIFEST_PATH = WORKSPACE_ROOT / "data/processed/universes/sp500/manifest.json"
QUALITY_PATH = WORKSPACE_ROOT / "data/processed/universes/sp500/quality_report.json"
CANONICAL_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Sp500HistoryBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not MANIFEST_PATH.is_file():
            raise unittest.SkipTest(
                "canonical S&P 500 manifest is unavailable; tests never rebuild approved data implicitly"
            )
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.quality = json.loads(QUALITY_PATH.read_text(encoding="utf-8"))

    def read_csv(self, relative: str) -> list[dict[str, str]]:
        with (WORKSPACE_ROOT / relative).open("r", encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))

    def test_frozen_sources_and_existing_etfs_are_unchanged(self) -> None:
        if not all((WORKSPACE_ROOT / self.registry[key]["path"]).is_file()
                   for key in ("price_source", "current_holdings_source")):
            self.skipTest("optional S&P original source archives are not distributed")
        self.assertEqual(
            sha256(WORKSPACE_ROOT / self.registry["price_source"]["path"]),
            self.registry["price_source"]["sha256"],
        )
        self.assertEqual(
            sha256(WORKSPACE_ROOT / self.registry["current_holdings_source"]["path"]),
            self.registry["current_holdings_source"]["sha256"],
        )
        for item in self.registry["frozen_existing_outputs"]:
            self.assertEqual(sha256(WORKSPACE_ROOT / item["path"]), item["sha256"])

    def test_distributed_frozen_etfs_are_unchanged(self) -> None:
        for item in self.registry["frozen_existing_outputs"]:
            self.assertEqual(sha256(WORKSPACE_ROOT / item["path"]), item["sha256"])

    def test_archive_and_price_outputs_are_complete(self) -> None:
        summary = self.manifest["summary"]
        self.assertEqual(summary["instrument_count"], 1299)
        self.assertEqual(summary["price_row_count"], 7377574)
        self.assertEqual(summary["failed_instruments"], 0)
        self.assertEqual(summary["instruments_with_member_price_gaps"], 14)
        self.assertEqual(
            self.manifest["baseline_status"],
            "validated_for_point_in_time_research_with_documented_gaps",
        )
        self.assertEqual(
            sha256(WORKSPACE_ROOT / self.manifest["builder"]),
            self.manifest["builder_sha256"],
        )
        self.assertEqual(len(self.manifest["price_files"]), 1299)
        for item in self.manifest["price_files"]:
            path = WORKSPACE_ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            self.assertEqual(sha256(path), item["canonical_sha256"])

    def test_original_archive_members_match_price_outputs(self) -> None:
        source = WORKSPACE_ROOT / self.registry["price_source"]["path"]
        if not source.is_file():
            self.skipTest("optional S&P original price archive is not distributed")
        with zipfile.ZipFile(source) as archive:
            self.assertEqual(set(archive.namelist()), {Path(item["path"]).name for item in self.manifest["price_files"]})

    def test_boundary_samples_preserve_point_in_time_semantics(self) -> None:
        intervals = self.read_csv(self.manifest["outputs"]["membership_intervals"]["path"])
        by_id: dict[str, list[dict[str, str]]] = {}
        for row in intervals:
            by_id.setdefault(row["instrument_id"], []).append(row)
        self.assertEqual(len(by_id["AAPL"]), 1)
        self.assertEqual(by_id["AAPL"][0]["start_boundary"], "left_censored")
        self.assertEqual(by_id["AAPL"][0]["end_boundary"], "right_censored")
        self.assertEqual(
            [(row["observed_start"], row["observed_end"]) for row in by_id["AMD"]],
            [("1990-01-02", "2013-09-20"), ("2017-03-20", "2026-08-04")],
        )
        self.assertEqual(
            [(row["observed_start"], row["observed_end"]) for row in by_id["CIEN"]],
            [("2001-08-30", "2009-12-18"), ("2026-02-09", "2026-08-04")],
        )
        self.assertEqual(by_id["TSLA"][0]["observed_start"], "2020-12-21")
        self.assertEqual(by_id["BRK.B"][0]["symbol"], "BRK.B")
        self.assertEqual(by_id["TSLA"][0]["previous_observed_date"], "2020-12-18")
        self.assertEqual(by_id["TSLA"][0]["start_gap_sessions"], "0")

    def test_membership_boundaries_disclose_missing_sessions(self) -> None:
        intervals = self.read_csv(self.manifest["outputs"]["membership_intervals"]["path"])
        sw = next(row for row in intervals if row["instrument_id"] == "SW")
        self.assertEqual(sw["previous_observed_date"], "2024-07-03")
        self.assertEqual(sw["observed_start"], "2024-07-08")
        self.assertEqual(sw["start_gap_sessions"], "1")
        self.assertEqual(sw["start_boundary"], "interval_censored_0_to_1")
        self.assertEqual(sw["entry_observed"], "false")
        sbny = next(row for row in intervals if row["instrument_id"] == "SBNY")
        self.assertEqual(sbny["observed_end"], "2023-03-10")
        self.assertEqual(sbny["next_observed_date"], "2023-03-28")
        self.assertEqual(sbny["end_gap_sessions"], "11")
        self.assertEqual(sbny["end_boundary"], "interval_censored_1_to_0")
        self.assertEqual(sbny["exit_observed"], "false")
        self.assertEqual(self.manifest["summary"]["entry_boundaries_with_missing_sessions"], 1)
        self.assertEqual(self.manifest["summary"]["exit_boundaries_with_missing_sessions"], 4)

    def test_missing_prices_inside_membership_are_explicit_warnings(self) -> None:
        self.assertEqual(self.quality["summary"]["warning_issue_counts"]["missing_in_index_session"], 24)
        self.assertEqual(self.quality["summary"]["instruments_with_warning"]["missing_in_index_session"], 14)
        reports = {item["instrument_id"]: item for item in self.quality["reports"]}
        self.assertEqual(reports["FRCB"]["issue_counts"]["missing_in_index_session"], 2)
        self.assertEqual(
            [item["date"] for item in reports["FRCB"]["issue_examples"]["missing_in_index_session"]],
            ["2023-05-01", "2023-05-02"],
        )
        security_master = {
            row["instrument_id"]: row
            for row in self.read_csv(self.manifest["outputs"]["security_master"]["path"])
        }
        self.assertEqual(security_master["FRCB"]["member_price_coverage_status"], "gaps_reported")
        self.assertEqual(security_master["AAPL"]["member_price_coverage_status"], "complete")

    def test_outlier_report_separates_adjacent_member_sessions(self) -> None:
        summary = self.quality["summary"]
        self.assertEqual(summary["warning_issue_counts"]["adjacent_in_index_outlier_close_return"], 692)
        self.assertEqual(summary["instruments_with_warning"]["adjacent_in_index_outlier_close_return"], 337)
        reports = {item["instrument_id"]: item for item in self.quality["reports"]}
        self.assertEqual(reports["AAPL"]["issue_counts"]["adjacent_in_index_outlier_close_return"], 2)

    def test_current_snapshot_reconciliation_is_dated_and_explicit(self) -> None:
        reconciliation = self.manifest["snapshot_reconciliation"]
        self.assertEqual(reconciliation["vendor_snapshot_date"], "2026-08-04")
        self.assertEqual(reconciliation["official_snapshot_date"], "2026-08-18")
        self.assertEqual(reconciliation["vendor_active_count"], 503)
        self.assertEqual(reconciliation["official_active_count"], 503)
        self.assertEqual(reconciliation["matched_count"], 500)
        self.assertEqual(reconciliation["official_only"], ["FERG", "RDDT", "VMRK"])
        self.assertEqual(reconciliation["vendor_only"], ["AVB", "EA", "EQR"])
        current = {row["symbol"]: row for row in self.read_csv(self.manifest["outputs"]["current_constituents"]["path"])}
        self.assertEqual(len(current), 503)
        self.assertEqual(current["FERG"]["vendor_price_available"], "false")
        self.assertEqual(current["FERG"]["link_method"], "unmatched")
        self.assertEqual(current["RDDT"]["vendor_price_available"], "false")
        self.assertEqual(current["VMRK"]["vendor_price_available"], "false")
        self.assertEqual(current["BRK.B"]["vendor_instrument_id"], "BRK.B")
        for stale_symbol in ("AVB", "EA", "EQR"):
            self.assertNotIn(stale_symbol, current)
        exclusions = self.read_csv(self.manifest["outputs"]["snapshot_exclusions"]["path"])
        self.assertEqual({row["ticker"] for row in exclusions}, {"-", "2602335D"})

    def test_canonical_samples_have_stable_schema_and_symbol(self) -> None:
        for symbol in ("AAPL", "AMD", "BRK.B", "CIEN", "TSLA", "AAL-199702"):
            path = WORKSPACE_ROOT / self.registry["outputs"]["price_directory"] / f"{symbol}.csv"
            with path.open("r", encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                self.assertEqual(reader.fieldnames, CANONICAL_COLUMNS)
                rows = list(reader)
            dates = [row["date"] for row in rows]
            self.assertEqual(dates, sorted(dates))
            self.assertEqual(len(dates), len(set(dates)))
            self.assertEqual({row["symbol"] for row in rows}, {symbol})

    def test_output_table_hashes_match_manifest(self) -> None:
        for item in self.manifest["outputs"].values():
            if not isinstance(item, dict) or "path" not in item:
                continue
            path = WORKSPACE_ROOT / item["path"]
            if path.is_symlink():
                continue
            self.assertEqual(sha256(path), item["sha256"])

    def test_full_rebuild_is_byte_reproducible(self) -> None:
        if not all((WORKSPACE_ROOT / self.registry[key]["path"]).is_file()
                   for key in ("price_source", "current_holdings_source")):
            self.skipTest("full rebuild requires optional S&P original source archives")
        tracked = [
            MANIFEST_PATH,
            QUALITY_PATH,
            WORKSPACE_ROOT / self.registry["outputs"]["quality_report_markdown"],
            *(WORKSPACE_ROOT / item["path"] for item in self.manifest["outputs"].values() if isinstance(item, dict) and "path" in item),
        ]
        before = {path: path.read_bytes() for path in tracked}

        with tempfile.TemporaryDirectory(prefix="sp500-history-rebuild-") as temporary:
            isolated_workspace = Path(temporary)
            isolated_registry = isolated_workspace / REGISTRY_PATH.relative_to(WORKSPACE_ROOT)
            isolated_registry.parent.mkdir(parents=True, exist_ok=True)
            isolated_registry.write_bytes(REGISTRY_PATH.read_bytes())

            isolated_manifest = isolated_workspace / MANIFEST_PATH.relative_to(WORKSPACE_ROOT)
            isolated_manifest.parent.mkdir(parents=True, exist_ok=True)
            isolated_manifest.write_bytes(before[MANIFEST_PATH])

            source_paths = [
                WORKSPACE_ROOT / self.registry["price_source"]["path"],
                WORKSPACE_ROOT / self.registry["current_holdings_source"]["path"],
                *(WORKSPACE_ROOT / item["path"] for item in self.registry["frozen_existing_outputs"]),
            ]
            for source in source_paths:
                destination = isolated_workspace / source.relative_to(WORKSPACE_ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(source.resolve())

            subprocess.run(
                [
                    str(BACKTEST_ROOT / ".venv/bin/python"),
                    "scripts/build_sp500_history.py",
                    "--workspace",
                    str(isolated_workspace),
                    "--registry",
                    str(isolated_registry),
                ],
                cwd=BACKTEST_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )

            for canonical_path, expected_bytes in before.items():
                isolated_path = isolated_workspace / canonical_path.relative_to(WORKSPACE_ROOT)
                self.assertEqual(isolated_path.read_bytes(), expected_bytes)
            for item in self.manifest["price_files"]:
                rebuilt_path = isolated_workspace / item["path"]
                self.assertEqual(sha256(rebuilt_path), item["canonical_sha256"])

        self.assertEqual({path: path.read_bytes() for path in tracked}, before)
        for item in self.manifest["price_files"]:
            self.assertEqual(sha256(WORKSPACE_ROOT / item["path"]), item["canonical_sha256"])


if __name__ == "__main__":
    unittest.main()
