from __future__ import annotations

import argparse
import copy
import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.data_update import (
    CONFIG_PATH,
    UpdateError,
    analyze_candidate,
    build_status,
    build_purchase_manifest,
    choose_tiingo_rotation,
    completed_session_on_or_before,
    config,
    corporate_action_reviews,
    copy_purchase,
    cross_check_window_start,
    evaluate_cross_check_coverage,
    expected_sessions,
    load_twelve_archive_data,
    prepare_twelve_resume_data,
    quick_check,
    qualify_shadow_report,
    run_deep_process_checks,
    shadow_campaign_status,
    safe_purchase_source,
    select_symbols,
    shadow_markdown,
)


class DataUpdateContractTest(unittest.TestCase):
    def test_registry_hard_disables_production_writes(self) -> None:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIs(payload["production_writes_enabled"], False)
        self.assertIs(payload["purchased_import"]["auto_merge_into_approved_data"], False)
        self.assertIs(payload["nasdaq100_candidate"]["auto_promote_to_approved"], False)
        self.assertIsNone(payload["nasdaq100_candidate"]["production_price_output"])
        self.assertIs(payload["sp500_candidate"]["auto_promote_to_approved"], False)
        self.assertIsNone(payload["sp500_candidate"]["production_price_output"])
        self.assertIs(payload["sp500_candidate"]["existing_approved_baseline_unchanged"], True)

    def test_completed_session_does_not_treat_open_session_as_complete(self) -> None:
        # 2026-08-13 14:00 UTC is before the US regular close.
        now = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(
            completed_session_on_or_before(date(2026, 8, 13), now_utc=now),
            date(2026, 8, 12),
        )

    def test_completed_session_accepts_session_after_close(self) -> None:
        now = datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(
            completed_session_on_or_before(date(2026, 8, 13), now_utc=now),
            date(2026, 8, 13),
        )

    def test_symbol_selection_rejects_non_current_security(self) -> None:
        current = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        args = select_symbols("AAPL", None, current)
        self.assertEqual([item["symbol"] for item in args], ["AAPL"])
        with self.assertRaises(UpdateError):
            select_symbols("EA", None, current)

    def test_tiingo_rotation_preserves_budget_and_changes_monthly_start(self) -> None:
        payload = config()
        symbols = [f"S{index:03d}" for index in range(503)]
        first, first_plan = choose_tiingo_rotation(symbols, payload, date(2026, 8, 12), "rotate")
        second, second_plan = choose_tiingo_rotation(symbols, payload, date(2026, 9, 12), "rotate")
        self.assertLessEqual(len(first), payload["shadow_update"]["tiingo_daily_batch_size"])
        self.assertLessEqual(first_plan["monthly_unique_after"], payload["shadow_update"]["tiingo_monthly_unique_budget"])
        self.assertNotEqual(first_plan["monthly_rotation_offset"], second_plan["monthly_rotation_offset"])
        self.assertNotEqual(first[0], second[0])

    def test_partial_twelve_archive_reports_reusable_and_missing_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            archive = workspace / "data/raw/public/market_data_updates/run_partial/twelve_data"
            archive.mkdir(parents=True)
            (archive / "incremental_batch_001.json").write_text(
                json.dumps(
                    {
                        "AAPL": {
                            "status": "ok",
                            "values": [
                                {
                                    "datetime": "2026-08-13",
                                    "open": "100",
                                    "high": "102",
                                    "low": "99",
                                    "close": "101",
                                    "volume": "1000",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("scripts.data_update.WORKSPACE_ROOT", workspace):
                data, batches, failures, missing = load_twelve_archive_data(
                    "run_partial",
                    ["AAPL", "MSFT"],
                )
            self.assertEqual(set(data), {"AAPL"})
            self.assertEqual(data["AAPL"][-1]["date"], "2026-08-13")
            self.assertEqual(len(batches), 1)
            self.assertEqual(failures, [])
            self.assertEqual(missing, ["MSFT"])

    def test_resume_can_refresh_one_symbol_without_reusing_its_archive(self) -> None:
        archived = {
            "AAPL": [{"date": "2026-08-20"}],
            "CBOE": [{"date": "2026-08-20"}],
            "MSFT": [{"date": "2026-08-19"}],
        }
        reusable, issues, missing = prepare_twelve_resume_data(
            archived,
            ["AAPL", "CBOE", "MSFT"],
            date(2026, 8, 20),
            {"CBOE"},
        )
        self.assertEqual(set(reusable), {"AAPL"})
        self.assertEqual(missing, ["CBOE", "MSFT"])
        self.assertEqual([item["symbol"] for item in issues], ["MSFT"])
        self.assertEqual(set(archived), {"AAPL", "CBOE", "MSFT"})

    def test_bootstrap_cross_check_uses_only_complete_candidate_period(self) -> None:
        settings = copy.deepcopy(config()["shadow_update"])
        analysis = {
            "kind": "bootstrap",
            "candidate_start_date": "2026-08-18",
        }
        desired_end = date(2026, 8, 20)
        start = cross_check_window_start(analysis, desired_end, settings)
        self.assertEqual(start, date(2026, 8, 18))
        expected = expected_sessions(start, desired_end)
        passed, required_common, required_stable, complete = evaluate_cross_check_coverage(
            analysis,
            expected,
            expected,
            len(expected),
            "2026-08-20",
            desired_end,
            settings,
        )
        self.assertTrue(passed)
        self.assertEqual(required_common, 3)
        self.assertEqual(required_stable, 3)
        self.assertTrue(complete)
        passed, _required_common, _required_stable, complete = evaluate_cross_check_coverage(
            analysis,
            expected,
            expected[:-1],
            len(expected) - 1,
            "2026-08-19",
            desired_end,
            settings,
        )
        self.assertFalse(passed)
        self.assertFalse(complete)

    def test_shadow_markdown_records_resume_provenance(self) -> None:
        report = {
            "run_id": "run_resumed",
            "desired_end_session": "2026-08-13",
            "summary": {
                "symbols": 2,
                "ready": 2,
                "review": 0,
                "fetch_failed": 0,
                "candidate_rows": 14,
                "cross_checked": 1,
                "cross_check_passed": 1,
            },
            "twelve_data": {
                "resume_source_run_id": "run_partial",
                "replay_source_run_id": None,
                "resume_source_issues": [],
                "resume_reused_symbols": ["AAPL"],
                "refresh_symbols": ["MSFT"],
                "batches": [
                    {"symbols": ["AAPL"], "replay": True},
                    {"symbols": ["MSFT"], "credits": 1},
                ],
            },
            "symbols": [],
        }
        rendered = shadow_markdown(report)
        self.assertIn("`run_partial`", rendered)
        self.assertIn("复用 1 个标的", rendered)
        self.assertIn("强制重抓：MSFT", rendered)

    def test_shadow_markdown_lists_cross_check_review_even_when_candidate_is_ready(self) -> None:
        report = {
            "run_id": "run_cross_check_review",
            "desired_end_session": "2026-08-20",
            "summary": {
                "symbols": 1,
                "ready": 1,
                "review": 0,
                "fetch_failed": 0,
                "candidate_rows": 12,
                "cross_checked": 1,
                "cross_check_passed": 0,
            },
            "twelve_data": {
                "resume_source_run_id": None,
                "replay_source_run_id": None,
                "batches": [],
            },
            "symbols": [
                {
                    "symbol": "CBOE",
                    "kind": "incremental",
                    "status": "ready_candidate",
                    "provider_last_date": "2026-08-20",
                    "candidate_rows": 12,
                    "cross_check_status": "review",
                }
            ],
        }
        rendered = shadow_markdown(report)
        self.assertIn("CBOE", rendered)
        self.assertIn("tiingo_cross_check_review", rendered)

    def test_purchase_preview_hashes_and_inspects_csv_without_copying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "purchase"
            source.mkdir()
            (source / "prices.csv").write_text("date,symbol,close\n2026-08-12,AAPL,302.25\n", encoding="utf-8")
            manifest = build_purchase_manifest(source, deep=True)
            self.assertEqual(manifest["file_count"], 1)
            self.assertEqual(manifest["inspection_failures"], [])
            self.assertEqual(manifest["files"][0]["inspection"]["columns"], ["date", "symbol", "close"])

    def test_purchase_source_rejects_managed_workspace(self) -> None:
        with self.assertRaises(UpdateError):
            safe_purchase_source(str(CONFIG_PATH), CONFIG_PATH.parent / "raw" / "purchased")

    def test_purchase_apply_is_immutable_registered_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = root / "external" / "purchase"
            source.mkdir(parents=True)
            (source / "prices.csv").write_text(
                "date,symbol,close\n2026-08-12,AAPL,302.25\n",
                encoding="utf-8",
            )
            registry = workspace / "data/purchased_import_registry.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "policy": {
                            "copied_sources_are_immutable": True,
                            "auto_merge_into_approved_data": False,
                            "default_status": "pending_review",
                        },
                        "batches": [],
                    }
                ),
                encoding="utf-8",
            )
            payload = copy.deepcopy(config())
            manifest = build_purchase_manifest(source, deep=True)
            with mock.patch("scripts.data_update.WORKSPACE_ROOT", workspace):
                first = copy_purchase(source, manifest, "test-provider", "2026-08-12", "daily", payload)
                second = copy_purchase(source, manifest, "test-provider", "2026-08-12", "daily", payload)
            self.assertEqual(first["status"], "imported_pending_review")
            self.assertEqual(second["status"], "already_imported")
            imported = workspace / first["batch"]["destination"] / "prices.csv"
            self.assertTrue(imported.is_file())
            self.assertEqual(imported.stat().st_mode & 0o222, 0)
            registered = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(len(registered["batches"]), 1)
            self.assertEqual(registered["batches"][0]["status"], "pending_review")
            self.assertIs(registered["batches"][0]["auto_merged"], False)

    def test_candidate_gate_detects_a_single_missing_overlap_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            prices = workspace / "data/processed/daily/equities"
            prices.mkdir(parents=True)
            approved_dates = expected_sessions(date(2026, 6, 22), date(2026, 8, 4))
            provider_dates = expected_sessions(date(2026, 6, 22), date(2026, 8, 12))
            values = {day: 100.0 + index for index, day in enumerate(provider_dates)}
            with (prices / "AAPL.csv").open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=("date", "symbol", "open", "high", "low", "close", "volume"),
                )
                writer.writeheader()
                for day in approved_dates:
                    close = values[day]
                    writer.writerow(
                        {
                            "date": day,
                            "symbol": "AAPL",
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1000,
                        }
                    )
            provider_rows = [
                {
                    "date": day,
                    "open": values[day],
                    "high": values[day],
                    "low": values[day],
                    "close": values[day],
                    "volume": 1000.0,
                }
                for day in provider_dates
                if day != approved_dates[-5]
            ]
            payload = copy.deepcopy(config())
            constituent = {
                "symbol": "AAPL",
                "vendor_instrument_id": "AAPL",
                "vendor_price_available": "true",
            }
            with mock.patch("scripts.data_update.WORKSPACE_ROOT", workspace):
                analysis, candidate = analyze_candidate(
                    constituent,
                    provider_rows,
                    date(2026, 8, 12),
                    payload,
                )
            self.assertEqual(analysis["status"], "review")
            self.assertIn("overlap_date_mismatch", analysis["review_reasons"])
            self.assertEqual(analysis["missing_overlap_dates"], [approved_dates[-5]])
            self.assertEqual(len(candidate), 6)

    def test_candidate_gate_blocks_undocumented_adjustment_basis_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            prices = workspace / "data/processed/daily/equities"
            prices.mkdir(parents=True)
            approved_dates = expected_sessions(date(2026, 6, 22), date(2026, 8, 4))
            provider_dates = expected_sessions(date(2026, 6, 22), date(2026, 8, 12))
            with (prices / "AAPL.csv").open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=("date", "symbol", "open", "high", "low", "close", "volume"),
                )
                writer.writeheader()
                for index, day in enumerate(approved_dates):
                    close = 100.0 + index
                    writer.writerow(
                        {
                            "date": day,
                            "symbol": "AAPL",
                            "open": close,
                            "high": close,
                            "low": close,
                            "close": close,
                            "volume": 1000,
                        }
                    )
            provider_rows = []
            for index, day in enumerate(provider_dates):
                close = 100.0 + index
                if day < approved_dates[-15]:
                    close *= 0.5
                provider_rows.append(
                    {
                        "date": day,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": 1000.0,
                    }
                )
            payload = copy.deepcopy(config())
            payload.pop("corporate_action_reviews", None)
            constituent = {
                "symbol": "AAPL",
                "vendor_instrument_id": "AAPL",
                "vendor_price_available": "true",
            }
            with mock.patch("scripts.data_update.WORKSPACE_ROOT", workspace):
                analysis, _candidate = analyze_candidate(
                    constituent,
                    provider_rows,
                    date(2026, 8, 12),
                    payload,
                )
            self.assertEqual(analysis["status"], "review")
            self.assertGreaterEqual(analysis["stable_tail_sessions"], 10)
            self.assertIn(
                "undocumented_historical_adjustment_basis_change",
                analysis["review_reasons"],
            )

    def test_campaign_requires_warning_and_bootstrap_symbols_in_cross_check(self) -> None:
        payload = copy.deepcopy(config())
        payload["universe"]["expected_equities"] = 2
        payload["shadow_validation_campaign"]["required_tiingo_cross_checks_per_session"] = 2
        report = {
            "scope": {"kind": "full_current_universe", "selected_count": 2},
            "summary": {
                "symbols": 2,
                "fetch_failed": 0,
                "cross_check_passed": 2,
                "production_rows_written": 0,
            },
            "production_writes_enabled": False,
            "twelve_data": {"symbol_failures": []},
            "tiingo": {"requested_symbols": ["AAPL", "MSFT"], "failures": []},
            "symbols": [
                {
                    "symbol": "AAPL",
                    "status": "ready_candidate",
                    "warnings": ["historical_adjustment_basis_change"],
                },
                {"symbol": "MSFT", "status": "ready_bootstrap_candidate"},
            ],
        }
        qualified, reasons = qualify_shadow_report(report, payload)
        self.assertTrue(qualified, reasons)
        report["tiingo"]["requested_symbols"] = ["GOOG", "AMZN"]
        qualified, reasons = qualify_shadow_report(report, payload)
        self.assertFalse(qualified)
        self.assertTrue(any(reason.startswith("adjustment_warnings_not_cross_checked") for reason in reasons))
        self.assertTrue(any(reason.startswith("bootstrap_candidates_not_cross_checked") for reason in reasons))

    def test_corporate_action_reviews_have_primary_evidence(self) -> None:
        reviews = corporate_action_reviews(config())
        self.assertEqual(set(reviews), {"APH", "HON", "HST"})
        for review in reviews.values():
            self.assertTrue(review["covered_return_outlier_dates"])
            self.assertTrue(all(event["url"].startswith("https://") for event in review["events"]))

    def test_campaign_counts_only_the_latest_consecutive_xnys_streak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            reports = workspace / "updates"
            sessions = ("2026-08-03", "2026-08-04", "2026-08-06", "2026-08-07")
            symbols = [
                {"symbol": "AAPL", "status": "ready_candidate", "cross_check_status": "pass"}
            ]
            for index, session in enumerate(sessions):
                run = reports / f"run_{index}"
                run.mkdir(parents=True)
                (run / "report.json").write_text(
                    json.dumps(
                        {
                            "run_id": f"run_{index}",
                            "generated_at_utc": f"2026-08-{index + 3:02d}T12:00:00Z",
                            "desired_end_session": session,
                            "scope": {"kind": "full_current_universe", "selected_count": 1},
                            "summary": {
                                "symbols": 1,
                                "fetch_failed": 0,
                                "cross_check_passed": 1,
                                "production_rows_written": 0,
                            },
                            "production_writes_enabled": False,
                            "twelve_data": {"symbol_failures": []},
                            "tiingo": {"requested_symbols": ["AAPL"], "failures": []},
                            "symbols": symbols,
                        }
                    ),
                    encoding="utf-8",
                )
            payload = copy.deepcopy(config())
            payload["universe"]["expected_equities"] = 1
            payload["shadow_update"]["candidate_output"] = "updates"
            payload["shadow_validation_campaign"]["required_tiingo_cross_checks_per_session"] = 1
            payload["shadow_validation_campaign"]["target_consecutive_completed_sessions"] = 3
            with mock.patch("scripts.data_update.WORKSPACE_ROOT", workspace):
                campaign = shadow_campaign_status(payload)
            self.assertEqual(campaign["qualified_distinct_sessions"], 4)
            self.assertEqual(campaign["current_streak_sessions"], ["2026-08-06", "2026-08-07"])
            self.assertEqual(campaign["current_consecutive_sessions"], 2)
            self.assertFalse(campaign["complete"])

    def test_status_exposes_shadow_scope_and_approved_price_range(self) -> None:
        # Exercise status rendering with a deterministic archived report fixture;
        # installing the release does not imply a prior online update campaign.
        shadow = {"scope": {"kind": "partial_current_universe", "selected_count": 4,
                            "current_universe_count": 503}}
        with mock.patch("scripts.data_update.verify_pointer", side_effect=[(None, []), (shadow, [])]), \
             mock.patch("scripts.data_update.credential", return_value=None):
            status = build_status()
        self.assertLessEqual(
            status["universe"]["earliest_approved_date"],
            status["universe"]["latest_approved_date"],
        )
        self.assertIs(status["production_writes_enabled"], False)
        self.assertIs(status["schedule"]["installed"], False)
        scope = status["latest_shadow_update"]["scope"]
        self.assertIn(scope["kind"], {"partial_current_universe", "full_current_universe"})
        self.assertLessEqual(scope["selected_count"], scope["current_universe_count"])

    def test_status_exposes_strategy_data_capability_boundaries(self) -> None:
        with mock.patch("scripts.data_update.credential", return_value=None):
            status = build_status()
        assets = {item["symbol"]: item for item in status["tracked_assets"]["assets"]}
        self.assertEqual(status["tracked_assets"]["required_for_current_research"], ["QQQ", "SPY"])
        self.assertEqual(assets["QQQ"]["registered_status"], "approved")
        self.assertEqual(assets["SPY"]["registered_status"], "approved")
        self.assertTrue(assets["QQQ"]["latest_date"])
        self.assertTrue(assets["SPY"]["latest_date"])
        self.assertEqual(assets["RKLB"]["registered_status"], "approved")
        self.assertEqual(assets["RKLB"]["latest_date"], "2026-08-04")
        self.assertIs(status["tracked_assets"]["shadow_update_enabled"], False)
        self.assertEqual(status["capabilities"]["sp500_current_member_shadow_update"], "ready")
        self.assertEqual(status["capabilities"]["benchmark_etf_shadow_update"], "not_implemented")
        self.assertEqual(status["capabilities"]["nasdaq100_point_in_time_universe"], "candidate_pending_review")
        self.assertEqual(status["capabilities"]["sp500_point_in_time_candidate_reaudit"], "candidate_pending_review")
        self.assertIs(status["nasdaq100_candidate"]["approved"], False)
        self.assertIs(status["sp500_candidate"]["approved"], False)
        self.assertIs(status["sp500_candidate"]["existing_approved_baseline_unchanged"], True)

    def test_deep_hash_gate_covers_every_approved_price_file(self) -> None:
        report = quick_check(verify_hashes=True)
        by_name = {item["name"]: item for item in report["checks"]}
        self.assertEqual(by_name["approved_price_hashes"]["status"], "pass")
        self.assertIn("1299 approved price files", by_name["approved_price_hashes"]["detail"])

    def test_deep_process_checks_continue_after_an_independent_failure(self) -> None:
        completed = [
            mock.Mock(returncode=0, stdout="inventory ok", stderr=""),
            mock.Mock(returncode=1, stdout="audit failed", stderr=""),
            mock.Mock(returncode=0, stdout="tests ok", stderr=""),
        ]
        with mock.patch("scripts.data_update.subprocess.run", side_effect=completed) as run:
            results = run_deep_process_checks()
        self.assertEqual(run.call_count, 3)
        self.assertEqual([item["name"] for item in results], ["raw_inventory", "workspace_audit", "unit_tests"])
        self.assertEqual([item["status"] for item in results], ["pass", "fail", "pass"])


if __name__ == "__main__":
    unittest.main()
