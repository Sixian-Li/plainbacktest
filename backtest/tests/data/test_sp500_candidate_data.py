from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from quantkit.nasdaq100_data import (
    PointInTimeDataError,
    audit_and_build,
    candidate_security_id,
    reconstruct_daily_members,
    registry_sessions,
    sha256_file,
)


SESSIONS = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
BLOCKING = [
    "archive_integrity_failure",
    "archive_member_set_mismatch",
    "archive_member_count_mismatch",
    "archive_uncompressed_size_mismatch",
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
]
WARNINGS = [
    "missing_in_index_session",
    "zero_volume",
    "outlier_close_return",
    "member_outlier_close_return",
    "reentered_index",
    "filename_suffix_not_last_in_index_month",
    "daily_member_count_below_500",
    "daily_member_count_above_500",
    "left_censored_history",
]


def source_csv(symbol: str, states: list[tuple[str, int]], company: str) -> bytes:
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


class SP500CandidateContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.source_root = self.workspace / "data/raw_sp500_evidence"
        self.source_root.mkdir(parents=True)
        calendar = self.workspace / "data/processed/calendars/XNYS.csv"
        calendar.parent.mkdir(parents=True)
        calendar.write_text(
            "date,open_utc,close_utc,is_early_close\n"
            + "".join(f"{day},,,False\n" for day in SESSIONS),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_registry(
        self,
        split_members: dict[str, bytes],
        dividend_members: dict[str, bytes],
        *,
        auto_approve: bool = False,
    ) -> Path:
        package_metadata = {}
        for variant, name, members in (
            ("split_only", "split.zip", split_members),
            ("split_and_dividend", "dividend.zip", dividend_members),
        ):
            path = self.source_root / name
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for member, content in members.items():
                    archive.writestr(member, content)
            with zipfile.ZipFile(path) as archive:
                uncompressed = sum(item.file_size for item in archive.infolist())
            package_metadata[variant] = {
                "path": f"data/raw_sp500_evidence/{name}",
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "archive_members": len(members),
                "uncompressed_bytes": uncompressed,
            }
        outputs = {
            name: f"data/processed/universes/sp500/pending_review/{filename}"
            for name, filename in {
                "security_master": "security_master.csv",
                "membership_intervals": "membership_intervals.csv",
                "membership_daily_counts": "membership_daily_counts.csv",
                "membership_changes": "membership_changes.csv",
                "source_manifest": "source_manifest.json",
                "quality_report_json": "quality_report.json",
                "quality_report_markdown": "quality_report.md",
            }.items()
        }
        outputs["directory"] = "data/processed/universes/sp500/pending_review"
        registry = {
            "schema_version": 1,
            "dataset_id": "test_sp500_candidate",
            "index_display_name": "S&P 500",
            "review_status": "pending_review",
            "production_writes_enabled": False,
            "source_root": "data/raw_sp500_evidence",
            "provider": "unknown",
            "license_or_authorization": "unknown",
            "acquired_date": "unknown",
            "filename_snapshot_token": "unknown",
            "security_id_prefix": "SPX-CAND",
            "active_snapshot_date": "2026-01-07",
            "nominal_company_count": 500,
            "sample_dates": SESSIONS,
            "calendar": {"path": "data/processed/calendars/XNYS.csv"},
            "packages": package_metadata,
            "extracted_evidence": None,
            "source_schema": [],
            "membership_contract": {},
            "identity_contract": {},
            "quality_policy": {
                "ohlc_relative_tolerance": 1e-9,
                "outlier_abs_close_return_pct": 50.0,
                "max_issue_examples": 10,
                "blocking_checks": BLOCKING,
                "warning_checks": WARNINGS,
            },
            "official_cross_checks": [],
            "outputs": outputs,
            "promotion_policy": {"auto_promote_to_approved": auto_approve},
        }
        path = self.workspace / "data/sp500_candidate_registry.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def test_no_extracted_copy_is_required_and_raw_evidence_is_immutable(self) -> None:
        members = {
            "OLD-202601.csv": source_csv(
                "OLD-202601",
                [("2026-01-02", 1), ("2026-01-05", 0), ("2026-01-06", 1), ("2026-01-07", 1)],
                "Same Company Common",
            ),
            "NEW.csv": source_csv(
                "NEW",
                [("2026-01-02", 0), ("2026-01-05", 1), ("2026-01-06", 1), ("2026-01-07", 1)],
                "Same Company Common",
            ),
        }
        registry = self.make_registry(members, members)
        approved_sentinel = self.workspace / "data/processed/daily/equities/APPROVED.csv"
        approved_sentinel.parent.mkdir(parents=True)
        approved_sentinel.write_text("do-not-touch\n", encoding="utf-8")
        before = {
            path.relative_to(self.source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source_root.rglob("*")
            if path.is_file()
        }
        report = audit_and_build(registry, self.workspace, write_candidate=True)
        after = {
            path.relative_to(self.source_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(approved_sentinel.read_text(encoding="utf-8"), "do-not-touch\n")
        self.assertEqual(report["package_comparison"]["extracted_comparison_status"], "not_applicable_no_extracted_source")
        self.assertTrue(report["source_immutability_verified"])
        self.assertEqual(len(report["identity_review"]["same_company_name_multiple_source_files"]), 1)
        manifest = json.loads(
            (self.workspace / "data/processed/universes/sp500/pending_review/source_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["status"], "candidate_pending_review")
        self.assertIs(manifest["approved"], False)
        self.assertIsNone(manifest["extracted_evidence"])
        self.assertEqual(set(manifest["quality_outputs"]), {"quality_report_json", "quality_report_markdown"})
        for output in manifest["quality_outputs"].values():
            self.assertEqual(sha256_file(self.workspace / output["path"]), output["sha256"])
        with (self.workspace / "data/processed/universes/sp500/pending_review/security_master.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            rows = list(csv.DictReader(source))
        self.assertTrue(all(row["security_id"].startswith("SPX-CAND-") for row in rows))
        self.assertEqual({row["membership_interval_count"] for row in rows}, {"1", "2"})
        self.assertTrue(all("not auto-merged" in row["anomaly_notes"] for row in rows))

    def test_package_membership_difference_blocks_candidate(self) -> None:
        split = source_csv("ABC", [("2026-01-02", 1), ("2026-01-05", 1)], "ABC Inc")
        dividend = source_csv("ABC", [("2026-01-02", 1), ("2026-01-05", 0)], "ABC Inc")
        registry = self.make_registry({"ABC.csv": split}, {"ABC.csv": dividend})
        report = audit_and_build(registry, self.workspace, write_candidate=False)
        self.assertEqual(report["status"], "blocked_candidate_build")
        self.assertEqual(report["blocking_issue_counts"]["package_in_index_mismatch"], 1)
        with self.assertRaises(PointInTimeDataError):
            audit_and_build(registry, self.workspace, write_candidate=True)

    def test_candidate_cannot_enable_automatic_approval(self) -> None:
        member = source_csv("ABC", [("2026-01-02", 1)], "ABC Inc")
        registry = self.make_registry({"ABC.csv": member}, {"ABC.csv": member}, auto_approve=True)
        with self.assertRaises(PointInTimeDataError):
            audit_and_build(registry, self.workspace, write_candidate=True)

    def test_security_line_count_can_be_500_then_501(self) -> None:
        intervals = [
            {
                "security_id": candidate_security_id(f"S{index}.csv", "SPX-CAND"),
                "source_symbol": f"S{index}",
                "effective_start": "2026-01-02",
                "effective_end": "2026-01-05",
                "source_file": f"S{index}.csv",
            }
            for index in range(500)
        ]
        intervals.append(
            {
                "security_id": candidate_security_id("SECOND.B.csv", "SPX-CAND"),
                "source_symbol": "SECOND.B",
                "effective_start": "2026-01-05",
                "effective_end": "2026-01-05",
                "source_file": "SECOND.B.csv",
            }
        )
        daily = reconstruct_daily_members(intervals, SESSIONS[:2])
        self.assertEqual(len(daily["2026-01-02"]), 500)
        self.assertEqual(len(daily["2026-01-05"]), 501)

    def test_registry_calendar_can_cover_1990_without_checked_in_calendar(self) -> None:
        sessions = registry_sessions(
            self.workspace,
            {
                "calendar": {
                    "source": "exchange_calendars",
                    "name": "XNYS",
                    "start": "1990-01-01",
                    "end": "1990-01-05",
                }
            },
        )
        self.assertEqual(sessions, ["1990-01-02", "1990-01-03", "1990-01-04", "1990-01-05"])


if __name__ == "__main__":
    unittest.main()
