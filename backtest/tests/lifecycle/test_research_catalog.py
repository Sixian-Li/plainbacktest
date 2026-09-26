import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

from scripts.build_research_catalog import (
    cached_scorecard_rows,
    declared_run_artifact,
    discover_experiments,
    validate_lineage,
)


class ResearchCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backtest_root = BACKTEST_ROOT
        cls.experiments_root = cls.backtest_root / "experiments"
        cls.lineage = json.loads(
            (cls.experiments_root / "lineage.json").read_text(encoding="utf-8")
        )
        cls.experiments = discover_experiments(cls.experiments_root)

    def test_every_experiment_has_one_program_and_unique_display_code(self) -> None:
        programs, nodes = validate_lineage(self.lineage, self.experiments)
        self.assertTrue(
            {"derivative_exit", "uptrend_rotation", "market_timing_hedge"}.issubset(programs)
        )
        self.assertEqual(len(nodes), len(self.experiments))
        self.assertEqual(len({node["display_code"] for node in nodes.values()}), len(nodes))
        for edge in self.lineage["edges"]:
            for field in ("relation", "rationale", "change_summary"):
                self.assertTrue(edge[field].strip(), msg=f"{edge['from']} -> {edge['to']}: {field}")

    def test_generated_catalog_is_current(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            [sys.executable, "-m", "scripts.build_research_catalog", "--check"],
            cwd=self.backtest_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, msg=process.stdout + process.stderr)

    def test_ignored_report_is_discovered_from_tracked_artifact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            (run_root / "artifact_manifest.json").write_text(
                json.dumps({"artifacts": {"report.html": {"sha256": "abc"}}}),
                encoding="utf-8",
            )
            self.assertTrue(declared_run_artifact(run_root, "report.html"))
            self.assertFalse(declared_run_artifact(run_root, "report.pdf"))

    def test_legacy_metrics_can_fall_back_to_same_run_in_tracked_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scorecard = Path(directory) / "scorecard.csv"
            scorecard.write_text(
                "experiment_id,run_id,metric_path,role,label,case_id,window_id,symbol,start,end,cost_bps,total_return_pct,cagr_pct,sharpe,max_drawdown_pct,exposure_pct,order_count\n"
                "legacy_v1,run_1,legacy/0,strategy,stable,C1,,QQQ,2000-01-01,2001-01-01,5,12.5,6.1,0.8,-9.0,75,4\n"
                "legacy_v1,run_2,legacy/1,strategy,other,C2,,QQQ,2001-01-01,2002-01-01,5,10,5,0.7,-10,70,6\n",
                encoding="utf-8",
            )
            rows = cached_scorecard_rows(
                scorecard,
                experiment_id="legacy_v1",
                run_id="run_1",
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["case_id"], "C1")
            self.assertEqual(rows[0]["cagr_pct"], 6.1)

    def test_cached_scorecard_preserves_integer_and_float_spelling_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scorecard = Path(directory) / "scorecard.csv"
            scorecard.write_text(
                "experiment_id,run_id,metric_path,role,label,case_id,window_id,symbol,start,end,cost_bps,total_return_pct,cagr_pct,sharpe,max_drawdown_pct,exposure_pct,order_count\n"
                "legacy_v1,run_1,legacy/0,strategy,stable,C1,,QQQ,2000-01-01,2001-01-01,0.0,12.5,6.1,0.8,-9.0,100.0,4\n",
                encoding="utf-8",
            )
            row = cached_scorecard_rows(
                scorecard,
                experiment_id="legacy_v1",
                run_id="run_1",
            )[0]
            self.assertIsInstance(row["cost_bps"], float)
            self.assertIsInstance(row["exposure_pct"], float)
            self.assertIsInstance(row["order_count"], int)

    def test_scorecard_and_map_cover_the_full_registry(self) -> None:
        with (self.experiments_root / "scorecard.csv").open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual({row["experiment_id"] for row in rows}, set(self.experiments))
        experiments_with_sharpe = {row["experiment_id"] for row in rows if row["sharpe"]}
        for experiment_id in set(self.experiments) - experiments_with_sharpe:
            placeholders = [
                row for row in rows
                if row["experiment_id"] == experiment_id
                and row["label"] == "No normalized summary metric"
            ]
            self.assertTrue(placeholders, msg=f"{experiment_id} has neither metrics nor placeholder")
        map_text = (self.experiments_root / "research_map.html").read_text(encoding="utf-8")
        for code in ("DER-v0.10", "ROT-v0.10", "TIM-v0.10"):
            self.assertIn(code, map_text)
        self.assertIn("qqq_short_sma_derivative", map_text)
        self.assertIn("完整关系边", map_text)

    def test_evolution_history_covers_every_edge_and_shows_result_deltas(self) -> None:
        evolution = (self.experiments_root / "strategy_evolution.md").read_text(encoding="utf-8")
        transition_count = sum(line.startswith("### ") and " → " in line for line in evolution.splitlines())
        self.assertEqual(transition_count, len(self.lineage["edges"]))
        for node in self.lineage["nodes"]:
            self.assertIn(node["display_code"], evolution)
        for phrase in ("为什么改：", "策略修改：", "修改前：", "修改后：", "自动配置差异："):
            self.assertIn(phrase, evolution)
        self.assertIn("completed_unvalidated", evolution)

    def test_each_program_has_an_isolated_generated_evolution_history(self) -> None:
        programs = {item["program_id"]: item for item in self.lineage["programs"]}
        nodes = {item["experiment_id"]: item for item in self.lineage["nodes"]}
        evolution_root = self.experiments_root / "program_evolution"
        expected_files = {f"{program['code']}.md" for program in programs.values()}
        self.assertEqual({path.name for path in evolution_root.glob("*.md")}, expected_files)

        for program_id, program in programs.items():
            path = evolution_root / f"{program['code']}.md"
            text = path.read_text(encoding="utf-8")
            own_codes = {
                node["display_code"] for node in nodes.values() if node["program_id"] == program_id
            }
            other_codes = {
                node["display_code"] for node in nodes.values() if node["program_id"] != program_id
            }
            for code in own_codes:
                self.assertIn(code, text, msg=f"{path.name} is missing {code}")
            for code in other_codes:
                self.assertNotIn(code, text, msg=f"{path.name} unexpectedly contains {code}")

            expected_edges = sum(
                nodes[edge["from"]]["program_id"] == program_id
                and nodes[edge["to"]]["program_id"] == program_id
                for edge in self.lineage["edges"]
            )
            actual_edges = sum(
                line.startswith("### ") and " → " in line for line in text.splitlines()
            )
            self.assertEqual(actual_edges, expected_edges)
            self.assertIn("[总策略演化史](../strategy_evolution.md)", text)
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                target_path = (path.parent / target).resolve()
                available = target_path.is_file()
                if target_path.name == "report.html":
                    available = available or declared_run_artifact(
                        target_path.parent,
                        "report.html",
                    )
                self.assertTrue(available, msg=f"Broken link in {path}: {target}")


if __name__ == "__main__":
    unittest.main()
