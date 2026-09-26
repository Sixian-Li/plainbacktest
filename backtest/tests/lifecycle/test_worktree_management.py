from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import manage_worktree


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class WorktreeManagementTest(unittest.TestCase):
    def test_destination_is_visible_and_below_repository_local_pool(self) -> None:
        self.assertEqual(
            manage_worktree.worktree_path("tim-forward-01"),
            manage_worktree.WORKTREE_ROOT / "tim-forward-01",
        )
        self.assertEqual(manage_worktree.WORKTREE_ROOT.name, "worktrees")
        for invalid in ("../escape", "/tmp/escape", "Uppercase", "has space"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                manage_worktree.worktree_path(invalid)

    def test_sparse_checkout_excludes_other_runs_and_includes_assigned_experiment(self) -> None:
        experiment = manage_worktree.experiment_relative_path("TIM/TIM-v0.70__26-08-17__demo")
        patterns = manage_worktree.sparse_patterns(experiment)
        self.assertIn("!/backtest/experiments/*/*/runs/", patterns)
        self.assertEqual(
            patterns[-1],
            "/backtest/experiments/TIM/TIM-v0.70__26-08-17__demo/runs/",
        )
        rsi_experiment = manage_worktree.experiment_relative_path(
            "RSI/RSI-v0.10__26-08-16__demo"
        )
        self.assertEqual(rsi_experiment.parts[0], "RSI")
        for invalid in ("../escape", "TIM", "/TIM/demo", "bad/demo", "TOO-LONG-CODE/demo"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                manage_worktree.experiment_relative_path(invalid)

    def test_bootstrap_links_shared_assets_and_rejects_real_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "quant"
            target = workspace / "worktrees/task"
            source_env = workspace / "backtest/.venv"
            source_data = workspace / "data/processed/daily"
            source_calendar = workspace / "data/processed/calendars/XNYS.csv"
            source_env.mkdir(parents=True)
            source_data.mkdir(parents=True)
            source_calendar.parent.mkdir(parents=True)
            source_calendar.write_text("date\n2026-08-17\n", encoding="utf-8")
            with (
                patch.object(manage_worktree, "WORKSPACE_ROOT", workspace),
                patch.object(
                    manage_worktree,
                    "SHARED_DIRECTORY_PATHS",
                    (Path("backtest/.venv"), Path("data/processed/daily")),
                ),
                patch.object(
                    manage_worktree,
                    "SHARED_FILE_PATTERNS",
                    ("data/processed/calendars/*.csv",),
                ),
            ):
                messages = manage_worktree.bootstrap_shared_paths(target)

            self.assertTrue((target / "backtest/.venv").is_symlink())
            self.assertTrue((target / "data/processed/daily").is_symlink())
            self.assertTrue((target / "data/processed/calendars/XNYS.csv").is_symlink())
            self.assertIn("linked: data/processed/daily", messages)

            copied_target = workspace / "worktrees/copied"
            real_copy = copied_target / "data/processed/daily"
            real_copy.mkdir(parents=True)
            with (
                patch.object(manage_worktree, "WORKSPACE_ROOT", workspace),
                patch.object(
                    manage_worktree,
                    "SHARED_DIRECTORY_PATHS",
                    (Path("data/processed/daily"),),
                ),
                patch.object(manage_worktree, "SHARED_FILE_PATTERNS", ()),
                self.assertRaisesRegex(RuntimeError, "real file/directory"),
            ):
                manage_worktree.bootstrap_shared_paths(copied_target)

    def test_bootstrap_keeps_matching_tracked_shared_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "quant"
            target = workspace / "worktrees/task"
            relative = Path("data/processed/universes/nasdaq100/security_master.csv")
            source = workspace / relative
            tracked = target / relative
            source.parent.mkdir(parents=True)
            tracked.parent.mkdir(parents=True)
            source.write_text("security_id\nA\n", encoding="utf-8")
            tracked.write_text("security_id\nA\n", encoding="utf-8")
            completed = Mock(returncode=0)
            with (
                patch.object(manage_worktree, "WORKSPACE_ROOT", workspace),
                patch.object(manage_worktree, "SHARED_DIRECTORY_PATHS", ()),
                patch.object(
                    manage_worktree,
                    "SHARED_FILE_PATTERNS",
                    ("data/processed/universes/**/security_master.csv",),
                ),
                patch.object(manage_worktree, "_run", return_value=completed),
            ):
                messages = manage_worktree.bootstrap_shared_paths(target)

            self.assertFalse(tracked.is_symlink())
            self.assertIn(f"tracked metadata: {relative}", messages)

    def test_report_entrypoint_points_to_latest_validated_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            experiment = Path(directory) / "TIM-v0.70__26-08-17__demo"
            run_id = "run_20260817T000000Z_demo"
            write_json(experiment / "experiment.json", {"latest_validated_run_id": run_id})
            report = experiment / "runs" / run_id / "report.html"
            report.parent.mkdir(parents=True)
            report.write_text("<html></html>", encoding="utf-8")

            entrypoint = manage_worktree.ensure_report_entrypoint(experiment)
            self.assertIsNotNone(entrypoint)
            assert entrypoint is not None
            self.assertTrue(entrypoint.is_symlink())
            self.assertEqual(entrypoint.readlink(), Path("runs") / run_id / "report.html")
            self.assertEqual(entrypoint.resolve(), report.resolve())

    def test_publication_copies_missing_artifacts_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            run_id = "run_20260817T000000Z_demo"
            config = {"experiment_id": "demo_v1", "latest_validated_run_id": run_id}
            write_json(source / "experiment.json", config)
            write_json(target / "experiment.json", config)
            write_json(source / "runs" / run_id / "validation.json", {"status": "passed"})
            report = source / "runs" / run_id / "report.html"
            report.write_text("<html>validated</html>", encoding="utf-8")

            copied = manage_worktree.copy_publication_artifacts(source, target)
            target_report = target / "runs" / run_id / "report.html"
            self.assertIn(target_report, copied)
            self.assertEqual(target_report.read_text(encoding="utf-8"), report.read_text(encoding="utf-8"))
            self.assertTrue((target / "report.html").is_symlink())

            report.write_text("<html>different</html>", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                manage_worktree.copy_publication_artifacts(source, target)

    def test_code_only_publish_reuses_hash_matched_canonical_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            run_id = "run_20260817T000000Z_demo"
            config = {"experiment_id": "demo_v1", "latest_validated_run_id": run_id}
            write_json(source / "experiment.json", config)
            write_json(target / "experiment.json", config)
            target_report = target / "runs" / run_id / "report.html"
            target_report.parent.mkdir(parents=True)
            target_report.write_text("<html>validated</html>", encoding="utf-8")
            write_json(
                source / "runs" / run_id / "artifact_manifest.json",
                {
                    "artifacts": {
                        "report.html": {"sha256": manage_worktree._sha256(target_report)}
                    }
                },
            )

            self.assertTrue(
                manage_worktree.publication_report_available(source, target, run_id)
            )
            target_report.write_text("<html>different</html>", encoding="utf-8")
            self.assertFalse(
                manage_worktree.publication_report_available(source, target, run_id)
            )


if __name__ == "__main__":
    unittest.main()
