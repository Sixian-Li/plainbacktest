from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from scripts.audit_workspace import (
    CATALOG_REQUIRED_PATHS,
    audit,
    declared_run_artifact,
    should_audit_run_artifacts,
)


class WorkspaceAuditTests(unittest.TestCase):
    def test_sparse_run_accepts_report_declared_by_artifact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            (run_root / "artifact_manifest.json").write_text(
                '{"artifacts": {"report.html": {"sha256": "abc"}}}',
                encoding="utf-8",
            )
            self.assertTrue(declared_run_artifact(run_root, "report.html"))
            self.assertFalse(declared_run_artifact(run_root, "report.pdf"))

    def test_sparse_checkout_only_skips_wholly_unmaterialized_runs_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            experiment_dir = Path(directory) / "experiment"
            experiment_dir.mkdir()

            self.assertFalse(
                should_audit_run_artifacts(experiment_dir, sparse_checkout=True)
            )
            self.assertTrue(
                should_audit_run_artifacts(experiment_dir, sparse_checkout=False)
            )

            (experiment_dir / "runs").mkdir()
            self.assertTrue(
                should_audit_run_artifacts(experiment_dir, sparse_checkout=True)
            )

    def test_sparse_worktree_root_ds_store_is_not_a_primary_workspace_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".DS_Store").write_bytes(b"finder")
            (workspace / "AGENTS.md").write_text("# Agent handoff\n", encoding="utf-8")
            (workspace / "log.md").write_text(
                "# Quant 工作日志\n\n## 2026-08-24\n\n- handoff\n",
                encoding="utf-8",
            )
            catalog_lines = "\n".join(CATALOG_REQUIRED_PATHS)
            (workspace / "catalog.md").write_text(
                f"# Quant 文件目录\n\n```text\n{catalog_lines}\n```\n",
                encoding="utf-8",
            )
            experiments_root = workspace / "backtest/experiments"
            experiments_root.mkdir(parents=True)
            (experiments_root / "index.md").write_text("# Experiments\n", encoding="utf-8")

            with unittest.mock.patch(
                "scripts.audit_workspace.is_sparse_checkout", return_value=True
            ):
                errors = audit(workspace, require_log_date="2026-08-24")

            self.assertFalse(any("Stray macOS metadata" in error for error in errors))

    def test_root_agents_handoff_is_allowed_and_cataloged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "AGENTS.md").write_text("# Agent handoff\n", encoding="utf-8")
            (workspace / "log.md").write_text(
                "# Quant 工作日志\n\n## 2026-08-14\n\n- handoff\n",
                encoding="utf-8",
            )
            catalog_lines = "\n".join(CATALOG_REQUIRED_PATHS)
            (workspace / "catalog.md").write_text(
                f"# Quant 文件目录\n\n```text\n{catalog_lines}\n```\n",
                encoding="utf-8",
            )
            experiments_root = workspace / "backtest/experiments"
            experiments_root.mkdir(parents=True)
            (experiments_root / "index.md").write_text("# Experiments\n", encoding="utf-8")

            errors = audit(workspace, require_log_date="2026-08-14")

            self.assertFalse(any("root Markdown" in error for error in errors))
            self.assertFalse(any("catalog.md does not list required path" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
