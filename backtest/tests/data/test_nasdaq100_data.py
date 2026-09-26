from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path

from quantkit.nasdaq100_data import (
    Nasdaq100DataError,
    audit_and_build,
    build_membership_intervals,
    candidate_security_id,
    clean_display_ticker,
    directory_manifest,
    parse_source_csv,
    reconstruct_daily_members,
    sha256_file,
)


POLICY = {
    "ohlc_relative_tolerance": 1e-9,
    "outlier_abs_close_return_pct": 50.0,
    "max_issue_examples": 5,
    "blocking_checks": [
        "archive_integrity_failure",
        "archive_member_set_mismatch",
        "extracted_content_mismatch",
        "package_row_key_mismatch",
        "package_in_index_mismatch",
        "package_unadjusted_close_mismatch",
        "unexpected_schema",
        "empty_file",
        "bad_encoding",
        "bad_row",
        "bad_date",
        "duplicate_date",
        "source_order_violation",
        "unstable_symbol",
        "filename_symbol_mismatch",
        "unstable_company_name",
        "bad_number",
        "nonpositive_price",
        "invalid_ohlc",
        "negative_volume",
        "nonpositive_unadjusted_close",
        "bad_in_index",
        "never_in_index",
        "source_mutated_during_build",
    ],
    "warning_checks": [
        "missing_in_index_session",
        "zero_volume",
        "outlier_close_return",
        "member_outlier_close_return",
        "reentered_index",
        "filename_suffix_not_last_in_index_month",
        "daily_member_count_below_100",
        "daily_member_count_above_100",
        "left_censored_history",
    ],
}
SESSIONS = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]


def source_csv(symbol: str, states: list[tuple[str, int]], company: str = "Example Inc") -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(
        [
            "Date",
            "CompanyName",
            "Symbol",
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "Unadjusted Close",
            "InIndex",
        ]
    )
    for index, (day, active) in enumerate(states):
        close = 100 + index
        writer.writerow([day, company, symbol, close, close + 1, close - 1, close, 1000, close, active])
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def directory_digest(path: Path) -> str:
    return str(directory_manifest(path)["directory_sha256"])


class Nasdaq100ParsingTest(unittest.TestCase):
    def test_bom_crlf_suffix_and_interval_reentry_are_preserved(self) -> None:
        content = source_csv(
            "AABA-201910",
            [
                ("2026-01-02", 1),
                ("2026-01-05", 0),
                ("2026-01-06", 1),
                ("2026-01-07", 1),
            ],
        )
        parsed = parse_source_csv("AABA-201910.csv", content, SESSIONS, POLICY)
        self.assertTrue(parsed["line_endings"]["bom_utf8"])
        self.assertEqual(parsed["line_endings"]["bare_lf_lines"], 0)
        self.assertEqual(parsed["metadata"]["display_ticker"], "AABA")
        self.assertEqual(parsed["metadata"]["filename_exit_suffix"], "201910")
        self.assertEqual(parsed["metadata"]["membership_interval_count"], 2)
        self.assertEqual(parsed["issue_counts"]["reentered_index"], 1)
        self.assertNotEqual(candidate_security_id("AABA-201910.csv"), candidate_security_id("AABA.csv"))

    def test_duplicate_dates_are_blocking_evidence(self) -> None:
        parsed = parse_source_csv(
            "ABC.csv",
            source_csv("ABC", [("2026-01-02", 1), ("2026-01-02", 1)]),
            SESSIONS,
            POLICY,
        )
        self.assertEqual(parsed["issue_counts"]["duplicate_date"], 1)
        self.assertEqual(parsed["issue_counts"]["source_order_violation"], 1)

    def test_missing_price_while_member_is_reported_without_inventing_an_exit(self) -> None:
        parsed = parse_source_csv(
            "ABC.csv",
            source_csv("ABC", [("2026-01-02", 1), ("2026-01-06", 1), ("2026-01-07", 0)]),
            SESSIONS,
            POLICY,
        )
        self.assertEqual(parsed["metadata"]["membership_interval_count"], 1)
        self.assertEqual(parsed["intervals"][0]["missing_price_sessions"], "2026-01-05")
        self.assertEqual(parsed["issue_counts"]["missing_in_index_session"], 1)

    def test_daily_reconstruction_supports_100_101_and_multiple_share_classes(self) -> None:
        intervals = []
        for index in range(99):
            symbol = f"S{index:03d}"
            intervals.append(
                {
                    "security_id": candidate_security_id(f"{symbol}.csv"),
                    "source_symbol": symbol,
                    "effective_start": "2026-01-02",
                    "effective_end": "2026-01-05",
                    "source_file": f"{symbol}.csv",
                }
            )
        for symbol, start in (("GOOG", "2026-01-02"), ("GOOGL", "2026-01-05")):
            intervals.append(
                {
                    "security_id": candidate_security_id(f"{symbol}.csv"),
                    "source_symbol": symbol,
                    "effective_start": start,
                    "effective_end": "2026-01-05",
                    "source_file": f"{symbol}.csv",
                }
            )
        daily = reconstruct_daily_members(intervals, SESSIONS[:2])
        self.assertEqual(len(daily["2026-01-02"]), 100)
        self.assertEqual(len(daily["2026-01-05"]), 101)
        self.assertIn("GOOG", daily["2026-01-05"])
        self.assertIn("GOOGL", daily["2026-01-05"])
        self.assertNotEqual(candidate_security_id("GOOG.csv"), candidate_security_id("GOOGL.csv"))

    def test_interval_builder_keeps_exit_then_reentry_separate(self) -> None:
        rows = [
            {"date": day, "in_index": active}
            for day, active in zip(SESSIONS, (1, 0, 1, 1))
        ]
        intervals = build_membership_intervals("ABC.csv", "ABC", "id", rows, SESSIONS)
        self.assertEqual([(item["effective_start"], item["effective_end"]) for item in intervals], [("2026-01-02", "2026-01-02"), ("2026-01-06", "2026-01-07")])


class Nasdaq100CandidateBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.source_root = self.workspace / "data/raw_evidence"
        self.extracted = self.source_root / "dividend_extracted"
        self.extracted.mkdir(parents=True)
        calendar = self.workspace / "data/processed/calendars/XNYS.csv"
        calendar.parent.mkdir(parents=True)
        calendar.write_text("date,open_utc,close_utc,is_early_close\n" + "".join(f"{day},,,False\n" for day in SESSIONS), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_registry(
        self,
        split_members: dict[str, bytes],
        dividend_members: dict[str, bytes],
        *,
        extracted_override: dict[str, bytes] | None = None,
    ) -> Path:
        split_zip = self.source_root / "split.zip"
        dividend_zip = self.source_root / "dividend.zip"
        for path, members in ((split_zip, split_members), (dividend_zip, dividend_members)):
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
        extracted_members = extracted_override if extracted_override is not None else dividend_members
        for name, content in extracted_members.items():
            path = self.extracted / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        registry = {
            "schema_version": 1,
            "dataset_id": "test_ndx_candidate",
            "review_status": "pending_review",
            "production_writes_enabled": False,
            "source_root": "data/raw_evidence",
            "provider": "unknown",
            "license_or_authorization": "unknown",
            "acquired_date": "unknown",
            "filename_snapshot_token": "unknown",
            "packages": {
                "split_only": {
                    "path": "data/raw_evidence/split.zip",
                    "sha256": sha256_file(split_zip),
                    "size_bytes": split_zip.stat().st_size,
                },
                "split_and_dividend": {
                    "path": "data/raw_evidence/dividend.zip",
                    "sha256": sha256_file(dividend_zip),
                    "size_bytes": dividend_zip.stat().st_size,
                },
            },
            "extracted_evidence": {
                "path": "data/raw_evidence/dividend_extracted",
                "directory_sha256": directory_digest(self.extracted),
            },
            "source_schema": [
                "Date", "CompanyName", "Symbol", "Open", "High", "Low", "Close", "Volume", "Unadjusted Close", "InIndex"
            ],
            "membership_contract": {},
            "identity_contract": {},
            "quality_policy": deepcopy(POLICY),
            "official_cross_checks": [],
            "outputs": {
                "directory": "data/processed/universes/nasdaq100/pending_review",
                "security_master": "data/processed/universes/nasdaq100/pending_review/security_master.csv",
                "membership_intervals": "data/processed/universes/nasdaq100/pending_review/membership_intervals.csv",
                "membership_daily_counts": "data/processed/universes/nasdaq100/pending_review/membership_daily_counts.csv",
                "membership_changes": "data/processed/universes/nasdaq100/pending_review/membership_changes.csv",
                "source_manifest": "data/processed/universes/nasdaq100/pending_review/source_manifest.json",
                "quality_report_json": "data/processed/universes/nasdaq100/pending_review/quality_report.json",
                "quality_report_markdown": "data/processed/universes/nasdaq100/pending_review/quality_report.md",
            },
            "promotion_policy": {"auto_promote_to_approved": False},
        }
        path = self.workspace / "data/nasdaq100_history_registry.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def test_build_is_raw_immutable_and_candidate_never_approved(self) -> None:
        members = {
            "GOOG.csv": source_csv("GOOG", [("2026-01-02", 1), ("2026-01-05", 1)], "Alphabet Inc Class C"),
            "GOOGL.csv": source_csv("GOOGL", [("2026-01-02", 0), ("2026-01-05", 1)], "Alphabet Inc Class A"),
        }
        registry = self.make_registry(members, members)
        before = {path.relative_to(self.source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source_root.rglob("*") if path.is_file()}
        report = audit_and_build(registry, self.workspace, write_candidate=True)
        after = {path.relative_to(self.source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.source_root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(report["source_immutability_verified"])
        manifest = json.loads((self.workspace / "data/processed/universes/nasdaq100/pending_review/source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "candidate_pending_review")
        self.assertIs(manifest["approved"], False)
        self.assertIs(manifest["promotion_policy"]["auto_promote_to_approved"], False)
        with (self.workspace / "data/processed/universes/nasdaq100/pending_review/security_master.csv").open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual({row["identity_status"] for row in rows}, {"separate_share_class"})

    def test_zip_and_extracted_content_mismatch_blocks_candidate(self) -> None:
        original = source_csv("ABC", [("2026-01-02", 1), ("2026-01-05", 1)])
        changed = source_csv("ABC", [("2026-01-02", 1), ("2026-01-05", 0)])
        registry = self.make_registry(
            {"ABC.csv": original},
            {"ABC.csv": original},
            extracted_override={"ABC.csv": changed},
        )
        report = audit_and_build(registry, self.workspace, write_candidate=False)
        self.assertEqual(report["status"], "blocked_candidate_build")
        self.assertEqual(report["blocking_issue_counts"]["extracted_content_mismatch"], 1)
        with self.assertRaises(Nasdaq100DataError):
            audit_and_build(registry, self.workspace, write_candidate=True)

    def test_auto_approval_contract_is_rejected(self) -> None:
        members = {"ABC.csv": source_csv("ABC", [("2026-01-02", 1)])}
        registry_path = self.make_registry(members, members)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["promotion_policy"]["auto_promote_to_approved"] = True
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(Nasdaq100DataError):
            audit_and_build(registry_path, self.workspace, write_candidate=True)

    def test_archive_member_set_mismatch_is_reported(self) -> None:
        a = source_csv("A", [("2026-01-02", 1)])
        b = source_csv("B", [("2026-01-02", 1)])
        registry = self.make_registry({"A.csv": a}, {"A.csv": a, "B.csv": b})
        report = audit_and_build(registry, self.workspace, write_candidate=False)
        self.assertEqual(report["status"], "blocked_candidate_build")
        self.assertEqual(report["blocking_issue_counts"]["archive_member_set_mismatch"], 1)


if __name__ == "__main__":
    unittest.main()
