#!/usr/bin/env python3
"""Run mandatory correctness gates and finalize one immutable experiment run."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantkit.experiment import load_experiment, load_run, record_validation, sha256, write_json


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid"
LEDGER_TOLERANCE = 1e-6


def verify_hashed_files(
    root: Path,
    artifacts: dict[str, dict[str, Any]],
    errors: list[str],
    *,
    label: str,
) -> None:
    for relative, metadata in artifacts.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"{label}: missing {relative}")
        elif path.stat().st_size != metadata.get("bytes"):
            errors.append(f"{label}: byte size mismatch for {relative}")
        elif sha256(path) != metadata.get("sha256"):
            errors.append(f"{label}: SHA256 mismatch for {relative}")


def run_gate(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    combined = "\n".join(part.strip() for part in (process.stdout, process.stderr) if part.strip())
    return {
        "name": name,
        "status": "passed" if process.returncode == 0 else "failed",
        "returncode": process.returncode,
        "command": command,
        "output": combined[-12000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_root = context.run_root(args.run_id)
    record = load_run(context, args.run_id)
    if record.get("status") != "completed_unvalidated":
        raise RuntimeError(
            f"Run must be completed_unvalidated, found {record.get('status')!r}"
        )

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    git_state = record.get("git", {})
    if not git_state.get("commit"):
        errors.append("run has no Git commit provenance")
    if git_state.get("dirty") is not False:
        errors.append("run was not started from a clean Git worktree")
    snapshot = run_root / "experiment_snapshot.json"
    if not snapshot.is_file() or sha256(snapshot) != record.get("experiment_snapshot_sha256"):
        errors.append("experiment snapshot is missing or its hash changed")

    for block in record.get("expected_blocks", []):
        manifest_relative = block.get("manifest")
        if block.get("status") != "completed" or not manifest_relative:
            errors.append(f"incomplete block: {block.get('block_id')}")
            continue
        manifest_path = run_root / manifest_relative
        if not manifest_path.is_file() or sha256(manifest_path) != block.get("manifest_sha256"):
            errors.append(f"block manifest is missing or changed: {block.get('block_id')}")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("experiment_run_id") != args.run_id:
            errors.append(f"block run ID mismatch: {block.get('block_id')}")
        verify_hashed_files(
            manifest_path.parent,
            manifest.get("artifacts", {}),
            errors,
            label=block.get("block_id", "block"),
        )
        differences = manifest.get("max_cross_check_differences", {})
        for metric, value in differences.items():
            if value is None or abs(float(value)) > LEDGER_TOLERANCE:
                errors.append(
                    f"independent ledger mismatch in {block.get('block_id')}: {metric}={value}"
                )

    artifact_path = run_root / "artifact_manifest.json"
    if not artifact_path.is_file():
        errors.append("missing artifact_manifest.json")
    else:
        artifact_manifest = json.loads(artifact_path.read_text(encoding="utf-8"))
        verify_hashed_files(
            run_root,
            artifact_manifest.get("artifacts", {}),
            errors,
            label="run artifact",
        )

    provenance_path = run_root / "provenance.json"
    if not provenance_path.is_file():
        errors.append("missing provenance.json")
    else:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        verify_hashed_files(
            context.workspace_root,
            provenance.get("source_files", {}),
            errors,
            label="source provenance",
        )

    report_path = run_root / "report.html"
    template_id = context.config["reporting"]["template_id"]
    if not report_path.is_file():
        errors.append("missing report.html")
    else:
        report_text = report_path.read_text(encoding="utf-8")
        if f'name="quant-report-template" content="{template_id}"' not in report_text:
            errors.append("report template metadata does not match experiment configuration")
        strategy_position = report_text.find('id="strategy-definition"')
        summary_position = report_text.find('id="summary"')
        if strategy_position < 0 or summary_position < 0 or strategy_position > summary_position:
            errors.append("report must place the exact strategy definition before all results")
        elif template_id == "interactive_research_v5":
            strategy_section = report_text[strategy_position:summary_position]
            for marker in (
                'class="strategy-flow"',
                "什么时候买",
                "什么时候卖",
                "信号如何变成成交",
            ):
                if marker not in strategy_section:
                    errors.append(f"v5 report strategy narrative is missing: {marker}")
            if "<pre" in strategy_section.lower():
                errors.append("v5 report must not dump code/JSON in the strategy narrative")
            if '<details open class="strategy-parameters"' in strategy_section:
                errors.append("v5 report parameters must be collapsed by default")
        snapshot_config = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.is_file() else {}
        frozen_strategy = snapshot_config.get("strategy", {})
        for field in ("name", "description", "buy_rule", "sell_rule", "signal_time", "execution_time"):
            value = str(frozen_strategy.get(field, ""))
            if value and html.escape(value) not in report_text:
                errors.append(f"report strategy definition does not reproduce frozen strategy.{field}")
        print_report = run_root / "report_print.html"
        if template_id == "interactive_research_v5" and print_report.is_file():
            print_text = print_report.read_text(encoding="utf-8")
            if "<pre" in print_text.lower():
                errors.append("v5 printable report must not contain expanded code/JSON blocks")
    if not (run_root / "report.md").is_file():
        errors.append("missing report.md")

    python = sys.executable
    checks.extend(
        [
            run_gate("dependency_integrity", [python, "-m", "pip", "check"], BACKTEST_ROOT),
            run_gate(
                "unit_tests",
                [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
                BACKTEST_ROOT,
            ),
            run_gate(
                "workspace_audit",
                [python, "-m", "scripts.audit_workspace", "--require-log-date", datetime.now().date().isoformat()],
                BACKTEST_ROOT,
            ),
        ]
    )
    for symbol in context.config["symbols"]:
        specialized_smoke = BACKTEST_ROOT / "scripts" / (
            f"smoke_{context.config['experiment_id'].removesuffix('_v1')}_report.mjs"
        )
        browser_command = (
            ["node", str(specialized_smoke.relative_to(BACKTEST_ROOT)), str(report_path)]
            if specialized_smoke.is_file()
            else [
                "node",
                "scripts/smoke_report_ui.mjs",
                str(report_path),
                f"performance-{symbol.lower()}",
            ]
        )
        checks.append(
            run_gate(
                f"browser_ui_{symbol}",
                browser_command,
                BACKTEST_ROOT,
            )
        )
    errors.extend(
        f"gate failed: {check['name']} (return code {check['returncode']})"
        for check in checks
        if check["status"] != "passed"
    )

    validation = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "validated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "passed" if not errors else "failed",
        "independent_ledger_tolerance": LEDGER_TOLERANCE,
        "errors": errors,
        "gates": checks,
    }
    write_json(run_root / "validation.json", validation)
    if errors:
        print("Run validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    record_validation(context, args.run_id, validation)
    print("Run validation: PASS")
    print(f"- experiment: {context.config['experiment_id']}")
    print(f"- run: {args.run_id}")
    print("- status: validated and immutable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
