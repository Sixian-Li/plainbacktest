import json
import tempfile
import unittest
from pathlib import Path

from quantkit.paths import BACKTEST_ROOT

from quantkit.experiment import (
    active_run,
    assert_run_writable,
    load_experiment,
    load_run,
    prune_interrupted_run,
    record_analysis_complete,
    record_block_complete,
    record_run_failed,
    record_run_interrupted,
    record_validation,
    resume_active_run,
    reserve_block,
    start_run,
    validate_experiment_config,
    write_json,
)


def config() -> dict:
    return {
        "schema_version": 1,
        "experiment_id": "example_v1",
        "created_at_utc": "2026-08-08T00:00:00+00:00",
        "status": "exploratory",
        "symbols": ["QQQ"],
        "excluded_symbols": {},
        "strategy": {
            "name": "sma200_asymmetric_threshold",
            "description": "Wait for a complete SMA warmup, then trade explicit threshold crossings at the next open.",
            "buy_rule": "cross above upper threshold",
            "sell_rule": "close below lower threshold",
            "signal_time": "close",
            "execution_time": "next open",
            "sma_window": 200,
        },
        "parameters": {
            "a_pct": [0, 0.25],
            "b_pct": [0, 0.25],
            "combination_count_per_surface": 4,
        },
        "cost_scenarios_bps_per_side": [5],
        "initial_cash": 100000,
        "financing": "disabled",
        "benchmark": "buy and hold",
        "reporting": {
            "template_id": "test",
            "template_path": "backtest/report_templates/interactive_research_v1",
            "reuse_review": "test only",
        },
        "research": {
            "stage": "exploratory",
            "hypothesis": "A broad parameter plateau is preferable to an isolated winner.",
            "primary_metric": "CAGR with drawdown and Sharpe as constraints",
            "selection_rule": "largest top-decile plateau representative",
            "validation_plan": "predeclared walk-forward validation",
            "promotion_criteria": "robust plateau and acceptable sample-out performance",
            "rejection_conditions": "isolated optimum or material ledger mismatch",
        },
    }


class ExperimentLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        root = self.workspace / "backtest/experiments/example"
        (self.workspace / "backtest/report_templates/interactive_research_v1").mkdir(parents=True)
        root.mkdir(parents=True)
        write_json(root / "experiment.json", config())
        self.context = load_experiment(root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v5_report_requires_plain_language_strategy_story(self) -> None:
        candidate = config()
        candidate["reporting"]["template_id"] = "interactive_research_v5"
        candidate["reporting"]["template_path"] = "backtest/report_templates/interactive_research_v5"
        with self.assertRaisesRegex(ValueError, "strategy.plain_language"):
            validate_experiment_config(candidate)
        candidate["strategy"]["plain_language"] = {
            "summary": "用长期趋势决定是否持有 QQQ。",
            "buy": "价格重新站上长期趋势后买入。",
            "sell": "价格跌回长期趋势下方后卖出。",
            "execution": "收盘确认，下一交易日开盘成交。",
            "position": "只在满仓 QQQ 与全现金之间切换。",
        }
        validate_experiment_config(candidate)

    def test_mutable_status_and_run_pointers_do_not_change_definition_hash(self) -> None:
        original = self.context.definition_sha256
        edited = config()
        edited["status"] = "archived"
        edited["active_run_id"] = "run_example"
        write_json(self.context.config_path, edited)
        self.assertEqual(load_experiment(self.context.config_path).definition_sha256, original)

    def test_run_snapshot_and_duplicate_block_protection(self) -> None:
        run_id, run_root = start_run(self.context)
        self.assertTrue((run_root / "experiment_snapshot.json").is_file())
        events = (self.workspace / "backtest/experiments/research_events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(json.loads(events[-1])["event_type"], "run_started")
        output = reserve_block(self.context, run_id, "QQQ", 5)
        with self.assertRaises(FileExistsError):
            reserve_block(self.context, run_id, "QQQ", 5)
        manifest = output / "manifest.json"
        write_json(manifest, {"status": "complete"})
        record_block_complete(self.context, run_id, "QQQ", 5, manifest)
        self.assertEqual(load_run(self.context, run_id)["expected_blocks"][0]["status"], "completed")

    def test_active_run_must_be_resumed_instead_of_duplicated(self) -> None:
        run_id, run_root = start_run(self.context)
        with self.assertRaisesRegex(RuntimeError, "resume it"):
            start_run(self.context)
        resumed_id, resumed_root, record = resume_active_run(self.context)
        self.assertEqual(resumed_id, run_id)
        self.assertEqual(resumed_root, run_root)
        self.assertEqual(record["status"], "running")
        self.assertEqual(active_run(self.context)[0], run_id)

    def test_interrupted_run_can_be_replaced_and_explicitly_pruned(self) -> None:
        interrupted_id, interrupted_root = start_run(self.context)
        record_run_interrupted(self.context, interrupted_id, reason="agent session lost network")
        self.assertEqual(load_run(self.context, interrupted_id)["status"], "interrupted")
        self.assertTrue((interrupted_root / "interruption.json").is_file())
        with self.assertRaises(RuntimeError):
            assert_run_writable(self.context, interrupted_id)

        replacement_id, replacement_root = start_run(self.context)
        output = reserve_block(self.context, replacement_id, "QQQ", 5)
        manifest = output / "manifest.json"
        write_json(manifest, {"status": "complete"})
        record_block_complete(self.context, replacement_id, "QQQ", 5, manifest)
        record_analysis_complete(self.context, replacement_id)
        validation = {"status": "passed"}
        write_json(replacement_root / "validation.json", validation)
        (replacement_root / "report.html").write_text("<html></html>", encoding="utf-8")
        record_validation(self.context, replacement_id, validation)

        refreshed = load_experiment(self.context.config_path)
        summary = prune_interrupted_run(
            refreshed,
            interrupted_id,
            superseded_by_run_id=replacement_id,
        )
        self.assertGreaterEqual(summary["files"], 3)
        self.assertFalse(interrupted_root.exists())

    def test_validated_run_is_immutable(self) -> None:
        run_id, run_root = start_run(self.context)
        output = reserve_block(self.context, run_id, "QQQ", 5)
        manifest = output / "manifest.json"
        write_json(manifest, {"status": "complete"})
        record_block_complete(self.context, run_id, "QQQ", 5, manifest)
        record_analysis_complete(self.context, run_id)
        validation = {"status": "passed"}
        write_json(run_root / "validation.json", validation)
        (run_root / "report.html").write_text("<html></html>", encoding="utf-8")
        record_validation(self.context, run_id, validation)
        self.assertEqual(load_run(self.context, run_id)["status"], "validated")
        report_entrypoint = self.context.config_path.parent / "report.html"
        self.assertTrue(report_entrypoint.is_symlink())
        self.assertEqual(report_entrypoint.resolve(), (run_root / "report.html").resolve())
        events = [
            json.loads(line)
            for line in (self.workspace / "backtest/experiments/research_events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [event["event_type"] for event in events],
            ["run_started", "analysis_completed", "run_validated"],
        )
        with self.assertRaises(RuntimeError):
            assert_run_writable(self.context, run_id)

    def test_definition_change_blocks_existing_run(self) -> None:
        run_id, _ = start_run(self.context)
        edited = json.loads(self.context.config_path.read_text(encoding="utf-8"))
        edited["strategy"]["sma_window"] = 180
        write_json(self.context.config_path, edited)
        changed_context = load_experiment(self.context.config_path)
        with self.assertRaises(RuntimeError):
            assert_run_writable(changed_context, run_id)

    def test_failed_run_records_reason_and_becomes_immutable(self) -> None:
        run_id, run_root = start_run(self.context)
        record_run_failed(
            self.context,
            run_id,
            phase="parameter_validation",
            reason="negative values were rejected",
        )
        record = load_run(self.context, run_id)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure"]["phase"], "parameter_validation")
        self.assertTrue((run_root / "failure.json").is_file())
        with self.assertRaises(RuntimeError):
            assert_run_writable(self.context, run_id)

    def test_completed_unvalidated_run_can_fail_report_review(self) -> None:
        run_id, run_root = start_run(self.context)
        output = reserve_block(self.context, run_id, "QQQ", 5)
        manifest = output / "manifest.json"
        write_json(manifest, {"status": "complete"})
        record_block_complete(self.context, run_id, "QQQ", 5, manifest)
        record_analysis_complete(self.context, run_id)
        record_run_failed(
            self.context,
            run_id,
            phase="report_review",
            reason="the generated report contained a misleading label",
        )
        self.assertEqual(load_run(self.context, run_id)["status"], "failed")
        self.assertTrue((run_root / "failure.json").is_file())

    def test_non_grid_strategy_does_not_require_sma_or_ab_parameters(self) -> None:
        edited = config()
        edited["strategy"].pop("sma_window")
        edited["strategy"]["name"] = "manual_scheduled_all_in_out"
        edited["parameters"] = {
            "initial_date": "2026-01-16",
            "initial_shares": 100,
            "trade_schedule": [
                {"date": "2026-01-20", "side": "sell"},
                {"date": "2026-01-21", "side": "buy"},
            ],
        }
        write_json(self.context.config_path, edited)
        loaded = load_experiment(self.context.config_path)
        self.assertEqual(loaded.config["parameters"]["initial_shares"], 100)

    def test_logical_category_directories_do_not_break_workspace_discovery(self) -> None:
        nested = self.workspace / "backtest/experiments/derivative_exit/example_nested"
        nested.mkdir(parents=True)
        write_json(nested / "experiment.json", config())
        loaded = load_experiment(nested)
        self.assertEqual(loaded.workspace_root, self.workspace.resolve())


class FormalExperimentGridTest(unittest.TestCase):
    def test_expanded_grid_is_inclusive_and_uses_quarter_percent_steps(self) -> None:
        experiment = BACKTEST_ROOT / "experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid"
        context = load_experiment(experiment)
        expected = [-3.0 + 0.25 * index for index in range(33)]
        self.assertEqual(context.config["parameters"]["a_pct"], expected)
        self.assertEqual(context.config["parameters"]["b_pct"], expected)
        self.assertEqual(context.config["parameters"]["combination_count_per_surface"], 1089)

    def test_intraday_sma200_grid_contract_is_frozen(self) -> None:
        experiment = (
            BACKTEST_ROOT
            / "experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025"
        )
        context = load_experiment(experiment)
        parameters = context.config["parameters"]
        self.assertEqual(context.config["symbols"], ["QQQ"])
        self.assertEqual(parameters["analysis_start"], "2021-01-04")
        self.assertEqual(parameters["analysis_end"], "2025-12-31")
        self.assertEqual(
            parameters["a_pct_range"],
            {"start": -20.0, "stop": 20.0, "step": 0.25},
        )
        self.assertEqual(
            parameters["b_pct_range"],
            {"start": -20.0, "stop": 20.0, "step": 0.25},
        )
        self.assertEqual(parameters["correction_scenarios_pct"], [None, 5.0, 10.0])
        self.assertEqual(parameters["combination_count_per_mode"], 25_921)
        self.assertEqual(parameters["combination_count_per_cost"], 77_763)
        self.assertEqual(parameters["total_case_count_all_costs"], 155_526)
        self.assertEqual(context.config["cost_scenarios_bps_per_side"], [0, 5])
        self.assertEqual(parameters["max_trades_per_session"], 1)

    def test_intraday_sma200_historical_reruns_change_only_period_context(self) -> None:
        root = BACKTEST_ROOT / "experiments"
        baseline = load_experiment(
            root
            / "TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025"
        ).config
        periods = {
            "TIM/TIM-v0.20a.2__26-08-13__qqq_intraday_sma200_threshold_grid_2000_2004": (
                "2000-01-03",
                "2004-12-31",
            ),
            "TIM/TIM-v0.20a.3__26-08-13__qqq_intraday_sma200_threshold_grid_2010_2014": (
                "2010-01-04",
                "2014-12-31",
            ),
        }
        for experiment_name, expected_dates in periods.items():
            config = load_experiment(root / experiment_name).config
            self.assertEqual(config["symbols"], baseline["symbols"])
            self.assertEqual(config["strategy"], baseline["strategy"])
            self.assertEqual(
                config["cost_scenarios_bps_per_side"],
                baseline["cost_scenarios_bps_per_side"],
            )
            self.assertEqual(config["initial_cash"], baseline["initial_cash"])
            self.assertEqual(config["financing"], baseline["financing"])
            for field in (
                "a_pct_range",
                "b_pct_range",
                "correction_scenarios_pct",
                "correction_constraint",
                "max_trades_per_session",
                "combination_count_per_mode",
                "combination_count_per_cost",
                "total_case_count_all_costs",
            ):
                self.assertEqual(
                    config["parameters"][field],
                    baseline["parameters"][field],
                    msg=f"{experiment_name}: {field}",
                )
            self.assertEqual(
                (
                    config["parameters"]["analysis_start"],
                    config["parameters"]["analysis_end"],
                ),
                expected_dates,
            )


if __name__ == "__main__":
    unittest.main()
