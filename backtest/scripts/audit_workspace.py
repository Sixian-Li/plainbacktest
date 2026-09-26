#!/usr/bin/env python3
"""Audit the Quant workspace structure, log, catalog, and experiment registry."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from datetime import date
from pathlib import Path


ROOT_MARKDOWN = {"AGENTS.md", "catalog.md", "log.md"}
OPTIONAL_ROOT_MARKDOWN = {"README.md", "THIRD_PARTY_NOTICES.md"}
FORBIDDEN_ROOT_ENTRIES = {"experiments", "quantkit", "scripts", "tests", ".venv"}
RUN_STATUSES = {"running", "completed_unvalidated", "validated", "failed", "interrupted"}
CATALOG_REQUIRED_PATHS = (
    "├── AGENTS.md",
    "├── catalog.md",
    "├── log.md",
    "├── worktrees/",
    "├── data/",
    "└── backtest/",
    "    ├── quantkit/",
    "    ├── scripts/",
    "    ├── tests/",
    "    │   ├── core/",
    "    │   ├── data/",
    "    │   ├── lifecycle/",
    "    │   ├── reporting/",
    "    │   └── strategies/",
    "    ├── docs/",
    "    └── experiments/",
    "        ├── index.md",
    "        ├── lineage.json",
    "        ├── strategy_evolution.md",
    "        ├── program_evolution/",
    "        │   ├── DER.md",
    "        │   ├── ROT.md",
    "        │   └── TIM.md",
    "        ├── scorecard.csv",
    "        ├── research_map.html",
    "        ├── research_events.jsonl",
    "        ├── DER/",
    "        ├── ROT/",
    "        └── TIM/",
)

EXPERIMENT_DIRECTORY = re.compile(
    r"^(?P<code>[A-Z][A-Z0-9]{1,7})-v[^_]+__(?P<date>\d{2}-\d{2}-\d{2})__(?P<slug>[a-z0-9][a-z0-9_]*)$"
)


def discover_experiment_configs(experiments_root: Path) -> list[Path]:
    """Find canonical definitions without treating immutable run snapshots as experiments."""
    return sorted(
        path
        for path in experiments_root.rglob("experiment.json")
        if "runs" not in path.relative_to(experiments_root).parts
    )


def is_sparse_checkout(workspace: Path) -> bool:
    """Return whether Git intentionally leaves paths unmaterialized in this worktree."""
    process = subprocess.run(
        ["git", "config", "--bool", "core.sparseCheckout"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    return process.returncode == 0 and process.stdout.strip().lower() == "true"


def should_audit_run_artifacts(experiment_dir: Path, *, sparse_checkout: bool) -> bool:
    """Audit runs unless Git deliberately omitted the experiment's whole runs tree."""
    return not sparse_checkout or (experiment_dir / "runs").is_dir()


def declared_run_artifact(run_root: Path, relative_path: str) -> bool:
    """Recognize an immutable ignored artifact declared by its tracked manifest."""

    manifest_path = run_root / "artifact_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return relative_path in manifest.get("artifacts", {})


def audit(workspace: Path, *, require_log_date: str | None = None) -> list[str]:
    errors: list[str] = []
    sparse_checkout = is_sparse_checkout(workspace)
    if not (workspace / "catalog.md").is_file():
        return ["Missing catalog.md at workspace root."]

    root_markdown = {path.name for path in workspace.glob("*.md")}
    unexpected_markdown = sorted(root_markdown - ROOT_MARKDOWN - OPTIONAL_ROOT_MARKDOWN)
    if unexpected_markdown:
        errors.append(f"Unexpected root Markdown files: {unexpected_markdown}")
    missing_markdown = sorted(ROOT_MARKDOWN - root_markdown)
    if missing_markdown:
        errors.append(f"Missing root Markdown files: {missing_markdown}")

    forbidden = sorted(name for name in FORBIDDEN_ROOT_ENTRIES if (workspace / name).exists())
    if forbidden:
        errors.append(f"Backtest-only entries remain at workspace root: {forbidden}")

    if (workspace / ".git").exists():
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        listing = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        if common.returncode == 0 and listing.returncode == 0:
            primary_root = Path(common.stdout.strip()).resolve().parent
            allowed_pool = primary_root / "worktrees"
            shared_links = (
                Path("backtest/.venv"),
                Path("data/raw"),
                Path("data/processed/daily"),
                Path("data/2026-08-05多个数据包_rethink"),
                Path("data/2026-08-05美股数据_全"),
            )
            for line in listing.stdout.splitlines():
                if not line.startswith("worktree "):
                    continue
                registered = Path(line.removeprefix("worktree ")).resolve()
                if registered != primary_root and not registered.is_relative_to(allowed_pool):
                    errors.append(
                        f"Registered worktree must be below {allowed_pool}: {registered}"
                    )
                    continue
                if registered == primary_root:
                    continue
                for relative in shared_links:
                    target = registered / relative
                    source = primary_root / relative
                    if not source.exists():
                        continue
                    if not target.exists():
                        errors.append(f"Worktree lacks shared link: {target}")
                    elif not target.is_symlink():
                        errors.append(
                            f"Worktree shared path must be a symlink, not a copy: "
                            f"{target}"
                        )
                    elif target.resolve() != source.resolve():
                        errors.append(f"Worktree shared link points outside the primary data: {target}")

    legacy_pool = workspace / ".worktrees"
    if legacy_pool.exists():
        errors.append(f"Legacy hidden worktree pool must be removed: {legacy_pool}")

    stray_metadata = []
    root_ds_store = workspace / ".DS_Store"
    if root_ds_store.is_file() and not sparse_checkout:
        stray_metadata.append(root_ds_store)
    for managed_root in (
        workspace / "data/processed",
        workspace / "data/docs",
        workspace / "research",
        workspace / "backtest",
    ):
        if managed_root.is_dir():
            stray_metadata.extend(
                path
                for path in managed_root.rglob(".DS_Store")
                if not path.is_relative_to(workspace / "backtest/.venv")
            )
    data_ds_store = workspace / "data/.DS_Store"
    if data_ds_store.is_file():
        stray_metadata.append(data_ds_store)
    if stray_metadata:
        relative = sorted(str(path.relative_to(workspace)) for path in stray_metadata)
        errors.append(f"Stray macOS metadata files: {relative}")

    catalog = (workspace / "catalog.md").read_text(encoding="utf-8")
    if not catalog.startswith("# Quant 文件目录\n\n```text\n") or not catalog.rstrip().endswith("```"):
        errors.append("catalog.md must contain only its title and one fenced text tree.")
    if catalog.count("```text") != 1 or catalog.count("```") != 2:
        errors.append("catalog.md must contain exactly one fenced file tree.")
    for required in CATALOG_REQUIRED_PATHS:
        if required not in catalog:
            errors.append(f"catalog.md does not list required path: {required}")

    log_path = workspace / "log.md"
    if log_path.is_file():
        log = log_path.read_text(encoding="utf-8")
        if "# Quant 工作日志" not in log:
            errors.append("log.md has an unexpected title.")
        expected_date = require_log_date or date.today().isoformat()
        if require_log_date and f"## {expected_date}" not in log:
            errors.append(f"log.md has no entry for required date {expected_date}.")

    experiments_root = workspace / "backtest/experiments"
    registry_path = experiments_root / "index.md"
    if not registry_path.is_file():
        errors.append("Missing backtest/experiments/index.md.")
        return errors
    registry = registry_path.read_text(encoding="utf-8")
    lineage_path = experiments_root / "lineage.json"
    evolution_path = experiments_root / "strategy_evolution.md"
    program_evolution_root = experiments_root / "program_evolution"
    scorecard_path = experiments_root / "scorecard.csv"
    map_path = experiments_root / "research_map.html"
    events_path = experiments_root / "research_events.jsonl"
    for path in (lineage_path, evolution_path, scorecard_path, map_path, events_path):
        if not path.is_file():
            errors.append(f"Missing {path.relative_to(workspace)}.")

    experiment_ids: set[str] = set()
    experiment_paths: dict[str, Path] = {}
    experiment_configs_by_id: dict[str, dict] = {}
    experiment_configs = discover_experiment_configs(experiments_root)
    for config_path in experiment_configs:
        experiment_dir = config_path.parent
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Experiment {experiment_dir.name} has invalid JSON: {exc}")
            continue
        experiment_id = str(config.get("experiment_id", "")).strip()
        if not experiment_id:
            errors.append(f"Experiment {experiment_dir.name} has no experiment_id.")
        elif experiment_id in experiment_ids:
            errors.append(f"Duplicate experiment_id: {experiment_id}.")
        elif experiment_id not in registry:
            errors.append(f"Experiment {experiment_id} is absent from experiments/index.md.")
        if experiment_id:
            experiment_ids.add(experiment_id)
            experiment_paths[experiment_id] = config_path
            experiment_configs_by_id[experiment_id] = config
        strategy = config.get("strategy", {})
        description = str(strategy.get("description", "")).strip()
        if len(description) < 40:
            errors.append(f"Experiment {experiment_id or experiment_dir.name} lacks a useful strategy.description.")
        for field in ("buy_rule", "sell_rule", "signal_time", "execution_time"):
            if not str(strategy.get(field, "")).strip():
                errors.append(f"Experiment {experiment_id or experiment_dir.name} lacks strategy.{field}.")
        reporting = config.get("reporting", {})
        for field in ("template_id", "template_path", "reuse_review"):
            if not str(reporting.get(field, "")).strip():
                errors.append(f"Experiment {experiment_id or experiment_dir.name} lacks reporting.{field}.")
        template_path = str(reporting.get("template_path", "")).strip()
        if template_path and not (workspace / template_path).is_dir():
            errors.append(
                f"Experiment {experiment_id or experiment_dir.name} references missing report template {template_path}."
            )
        research = config.get("research", {})
        for field in (
            "stage",
            "hypothesis",
            "primary_metric",
            "selection_rule",
            "validation_plan",
            "promotion_criteria",
            "rejection_conditions",
        ):
            if not research.get(field):
                errors.append(f"Experiment {experiment_id or experiment_dir.name} lacks research.{field}.")
        audit_run_artifacts = should_audit_run_artifacts(
            experiment_dir,
            sparse_checkout=sparse_checkout,
        )
        if audit_run_artifacts:
            for pointer in ("active_run_id", "latest_validated_run_id"):
                run_id = config.get(pointer)
                if not run_id:
                    continue
                run_root = experiment_dir / "runs" / run_id
                record_path = run_root / "run.json"
                if not record_path.is_file():
                    errors.append(f"Experiment {experiment_id or experiment_dir.name} has missing {pointer} {run_id}.")
                    continue
                try:
                    run_record = json.loads(record_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(f"Run {run_id} has invalid run.json: {exc}")
                    continue
                if pointer == "latest_validated_run_id" and run_record.get("status") != "validated":
                    errors.append(f"Run {run_id} is latest_validated_run_id but is not validated.")
                if run_record.get("status") == "validated" and not (run_root / "validation.json").is_file():
                    errors.append(f"Validated run {run_id} has no validation.json.")

            latest_validated_run_id = str(config.get("latest_validated_run_id") or "")
            if latest_validated_run_id:
                expected_report = Path("runs") / latest_validated_run_id / "report.html"
                report_path = experiment_dir / expected_report
                report_entrypoint = experiment_dir / "report.html"
                if not report_path.is_file() and not declared_run_artifact(
                    report_path.parent, "report.html"
                ):
                    errors.append(
                        f"Experiment {experiment_id or experiment_dir.name} latest validated run lacks report.html."
                    )
                elif not report_entrypoint.is_symlink():
                    errors.append(
                        f"Experiment {experiment_id or experiment_dir.name} lacks a report.html symlink."
                    )
                elif report_entrypoint.readlink() != expected_report:
                    errors.append(
                        f"Experiment {experiment_id or experiment_dir.name} report.html points to "
                        f"{report_entrypoint.readlink()}, expected {expected_report}."
                    )

        runs_root = experiment_dir / "runs"
        if runs_root.is_dir():
            for run_root in sorted(path for path in runs_root.iterdir() if path.is_dir()):
                run_id = run_root.name
                if run_id.startswith("legacy_"):
                    continue
                if not run_id.startswith("run_"):
                    errors.append(f"Unexpected directory below runs/: {run_id}.")
                    continue
                record_path = run_root / "run.json"
                snapshot_path = run_root / "experiment_snapshot.json"
                if not record_path.is_file():
                    errors.append(f"Run directory {run_id} has no run.json.")
                    continue
                if not snapshot_path.is_file():
                    errors.append(f"Run {run_id} has no experiment_snapshot.json.")
                try:
                    run_record = json.loads(record_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(f"Run {run_id} has invalid run.json: {exc}")
                    continue
                run_status = run_record.get("status")
                if run_record.get("run_id") != run_id:
                    errors.append(f"Run directory {run_id} disagrees with run.json run_id.")
                if run_record.get("experiment_id") != experiment_id:
                    errors.append(f"Run {run_id} belongs to a different experiment_id.")
                if run_status not in RUN_STATUSES:
                    errors.append(f"Run {run_id} has unknown status {run_status!r}.")
                if run_status in {"running", "completed_unvalidated"} and config.get("active_run_id") != run_id:
                    errors.append(
                        f"Run {run_id} is orphaned with nonterminal status {run_status}; "
                        "resume it or explicitly mark it interrupted."
                    )
                if run_status == "validated" and not (run_root / "validation.json").is_file():
                    errors.append(f"Validated run {run_id} has no validation.json.")
                if run_status == "failed" and not (run_root / "failure.json").is_file():
                    errors.append(f"Failed run {run_id} has no failure.json.")
                if run_status == "interrupted" and not (run_root / "interruption.json").is_file():
                    errors.append(f"Interrupted run {run_id} has no interruption.json.")

    lineage_nodes: dict[str, dict] = {}
    lineage_codes: set[str] = set()
    lineage_programs: set[str] = set()
    if lineage_path.is_file():
        try:
            lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid backtest/experiments/lineage.json: {exc}")
        else:
            lineage_programs = {
                str(item.get("program_id", "")).strip() for item in lineage.get("programs", [])
            }
            for node in lineage.get("nodes", []):
                experiment_id = str(node.get("experiment_id", "")).strip()
                display_code = str(node.get("display_code", "")).strip()
                if not experiment_id or experiment_id in lineage_nodes:
                    errors.append(f"Missing or duplicate lineage experiment_id: {experiment_id!r}.")
                    continue
                if not display_code or display_code in lineage_codes:
                    errors.append(f"Missing or duplicate lineage display_code: {display_code!r}.")
                if node.get("program_id") not in lineage_programs:
                    errors.append(f"Lineage node {experiment_id} references an unknown program.")
                created_on = str(node.get("created_on", "")).strip()
                try:
                    parsed_created_on = date.fromisoformat(created_on)
                except ValueError:
                    errors.append(f"Lineage node {experiment_id} has invalid created_on {created_on!r}.")
                    parsed_created_on = None
                config = experiment_configs_by_id.get(experiment_id, {})
                config_created_on = str(config.get("created_at_utc", ""))[:10]
                if parsed_created_on and config_created_on != created_on:
                    errors.append(
                        f"Lineage node {experiment_id} created_on {created_on} disagrees with "
                        f"experiment created_at_utc {config_created_on}."
                    )
                lineage_nodes[experiment_id] = node
                lineage_codes.add(display_code)
            missing = sorted(experiment_ids - set(lineage_nodes))
            stale = sorted(set(lineage_nodes) - experiment_ids)
            if missing or stale:
                errors.append(f"lineage.json mismatch; missing={missing}, stale={stale}.")
            program_codes = {
                str(item.get("program_id", "")).strip(): str(item.get("code", "")).strip()
                for item in lineage.get("programs", [])
            }
            for experiment_id, node in lineage_nodes.items():
                config_path = experiment_paths.get(experiment_id)
                if config_path is None:
                    continue
                relative = config_path.relative_to(experiments_root)
                code = program_codes.get(str(node.get("program_id", "")), "")
                created_on = str(node.get("created_on", ""))
                display_code = str(node.get("display_code", ""))
                expected_prefix = f"{display_code}__{created_on[2:]}__"
                match = EXPERIMENT_DIRECTORY.fullmatch(config_path.parent.name)
                if (
                    len(relative.parts) != 3
                    or relative.parts[0] != code
                    or not config_path.parent.name.startswith(expected_prefix)
                    or match is None
                    or match.group("code") != code
                ):
                    errors.append(
                        f"Experiment {experiment_id} must live at "
                        f"experiments/{code}/{expected_prefix}<slug>/, found {relative}."
                    )
            edge_keys: set[tuple[str, str]] = set()
            for edge in lineage.get("edges", []):
                source, target = edge.get("from"), edge.get("to")
                if source not in lineage_nodes or target not in lineage_nodes or source == target:
                    errors.append(f"Invalid lineage edge: {edge}.")
                edge_key = (str(source), str(target))
                if edge_key in edge_keys:
                    errors.append(f"Duplicate lineage edge: {edge_key}.")
                edge_keys.add(edge_key)
                for field in ("relation", "rationale", "change_summary"):
                    if not str(edge.get(field, "")).strip():
                        errors.append(f"Lineage edge {edge_key} lacks {field}.")

    if evolution_path.is_file():
        evolution_text = evolution_path.read_text(encoding="utf-8")
        missing_codes = sorted(code for code in lineage_codes if code not in evolution_text)
        if missing_codes:
            errors.append(f"strategy_evolution.md is missing display codes: {missing_codes}.")
        for phrase in ("为什么改", "策略修改", "自动配置差异", "修改前", "修改后"):
            if phrase not in evolution_text:
                errors.append(f"strategy_evolution.md lacks required section label: {phrase}.")

    if lineage_nodes:
        program_codes = {
            str(item.get("program_id", "")).strip(): str(item.get("code", "")).strip()
            for item in lineage.get("programs", [])
        }
        expected_program_files = {
            program_evolution_root / f"{code}.md" for code in program_codes.values() if code
        }
        actual_program_files = (
            set(program_evolution_root.glob("*.md")) if program_evolution_root.is_dir() else set()
        )
        missing_files = sorted(
            str(path.relative_to(workspace)) for path in expected_program_files - actual_program_files
        )
        stale_files = sorted(
            str(path.relative_to(workspace)) for path in actual_program_files - expected_program_files
        )
        if missing_files or stale_files:
            errors.append(f"Program evolution files mismatch; missing={missing_files}, stale={stale_files}.")
        for program_id, code in program_codes.items():
            path = program_evolution_root / f"{code}.md"
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            own_codes = {
                str(node.get("display_code", ""))
                for node in lineage_nodes.values()
                if node.get("program_id") == program_id
            }
            other_codes = lineage_codes - own_codes
            missing_codes = sorted(item for item in own_codes if item not in text)
            mixed_codes = sorted(item for item in other_codes if item in text)
            if missing_codes or mixed_codes:
                errors.append(
                    f"{path.relative_to(workspace)} program isolation mismatch; "
                    f"missing={missing_codes}, mixed={mixed_codes}."
                )

    if scorecard_path.is_file():
        with scorecard_path.open(encoding="utf-8", newline="") as file:
            scorecard_ids = {row.get("experiment_id", "") for row in csv.DictReader(file)}
        missing = sorted(experiment_ids - scorecard_ids)
        stale = sorted((scorecard_ids - {""}) - experiment_ids)
        if missing or stale:
            errors.append(f"scorecard.csv mismatch; missing={missing}, stale={stale}.")

    if map_path.is_file():
        map_text = map_path.read_text(encoding="utf-8")
        missing_codes = sorted(code for code in lineage_codes if code not in map_text)
        if missing_codes:
            errors.append(f"research_map.html is missing display codes: {missing_codes}.")

    if events_path.is_file():
        for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid research_events.jsonl line {line_number}: {exc}")
                continue
            for field in ("timestamp", "event_type", "summary"):
                if not str(event.get(field, "")).strip():
                    errors.append(f"research_events.jsonl line {line_number} lacks {field}.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Quant workspace root.",
    )
    parser.add_argument("--require-log-date", help="Require a YYYY-MM-DD heading in log.md.")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    errors = audit(workspace, require_log_date=args.require_log_date)
    if errors:
        print("Workspace audit: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Workspace audit: PASS")
    print(f"- root: {workspace}")
    print(f"- experiments: {len(discover_experiment_configs(workspace / 'backtest/experiments'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
