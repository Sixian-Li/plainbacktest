"""Experiment configuration and immutable run lifecycle helpers."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_ID_PATTERN = re.compile(r"^run_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}(?:_[0-9]{2})?$")
WRITABLE_RUN_STATUSES = {"running"}
RESUMABLE_RUN_STATUSES = {"running", "completed_unvalidated"}
FINAL_RUN_STATUSES = {"validated", "failed", "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def append_research_event(
    context: "ExperimentContext",
    *,
    event_type: str,
    summary: str,
    run_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a compact lifecycle event without mutating an immutable run."""
    timestamp = utc_now()
    identity = {
        "timestamp": timestamp,
        "event_type": event_type,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "summary": summary,
        "details": details or {},
    }
    event = {
        "schema_version": 1,
        "event_id": f"evt_{timestamp.replace('-', '').replace(':', '')}_{_canonical_hash(identity)[:8]}",
        **identity,
    }
    events_path = context.workspace_root / "backtest/experiments/research_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    return event


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _definition_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Select research-defining fields and exclude mutable run pointers/status."""
    keys = (
        "schema_version",
        "experiment_id",
        "symbols",
        "excluded_symbols",
        "strategy",
        "parameters",
        "cost_scenarios_bps_per_side",
        "initial_cash",
        "financing",
        "benchmark",
        "reporting",
        "research",
    )
    return {key: config[key] for key in keys if key in config}


def validate_experiment_config(config: dict[str, Any]) -> None:
    errors: list[str] = []
    if config.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not str(config.get("experiment_id", "")).strip():
        errors.append("experiment_id is required")
    symbols = config.get("symbols")
    if not isinstance(symbols, list) or not symbols or not all(isinstance(x, str) and x for x in symbols):
        errors.append("symbols must be a non-empty list of names")
    strategy = config.get("strategy")
    if not isinstance(strategy, dict):
        errors.append("strategy must be an object")
    else:
        for field in ("name", "description", "buy_rule", "sell_rule", "signal_time", "execution_time"):
            if not str(strategy.get(field, "")).strip():
                errors.append(f"strategy.{field} is required")
        sma_window = strategy.get("sma_window")
        if sma_window is not None and (not isinstance(sma_window, int) or sma_window < 2):
            errors.append("strategy.sma_window must be an integer >= 2 when provided")
    parameters = config.get("parameters")
    if not isinstance(parameters, dict):
        errors.append("parameters must be an object")
    else:
        grid_fields = {"a_pct", "b_pct", "combination_count_per_surface"}
        if grid_fields.intersection(parameters):
            for field in ("a_pct", "b_pct"):
                values = parameters.get(field)
                if not isinstance(values, list) or not values:
                    errors.append(f"parameters.{field} must be a non-empty list")
                elif len(values) != len(set(values)) or not all(
                    isinstance(x, (int, float)) for x in values
                ):
                    errors.append(f"parameters.{field} must contain unique numeric values")
            a_values = parameters.get("a_pct")
            b_values = parameters.get("b_pct")
            declared_count = parameters.get("combination_count_per_surface")
            if isinstance(a_values, list) and isinstance(b_values, list):
                expected_count = len(a_values) * len(b_values)
                if declared_count != expected_count:
                    errors.append(
                        "parameters.combination_count_per_surface must equal len(a_pct) * len(b_pct)"
                    )
    costs = config.get("cost_scenarios_bps_per_side")
    if not isinstance(costs, list) or not costs or not all(isinstance(x, (int, float)) and x >= 0 for x in costs):
        errors.append("cost_scenarios_bps_per_side must be a non-empty non-negative list")
    if not isinstance(config.get("initial_cash"), (int, float)) or config.get("initial_cash", 0) <= 0:
        errors.append("initial_cash must be positive")
    reporting = config.get("reporting")
    if not isinstance(reporting, dict):
        errors.append("reporting must be an object")
    else:
        for field in ("template_id", "template_path", "reuse_review"):
            if not str(reporting.get(field, "")).strip():
                errors.append(f"reporting.{field} is required")
        if reporting.get("template_id") == "interactive_research_v5" and isinstance(strategy, dict):
            plain_language = strategy.get("plain_language")
            if not isinstance(plain_language, dict):
                errors.append("strategy.plain_language must be an object for interactive_research_v5")
            else:
                for field in ("summary", "buy", "sell", "execution", "position"):
                    if not str(plain_language.get(field, "")).strip():
                        errors.append(f"strategy.plain_language.{field} is required for interactive_research_v5")
    research = config.get("research")
    if not isinstance(research, dict):
        errors.append("research must be an object")
    else:
        for field in (
            "stage",
            "hypothesis",
            "primary_metric",
            "selection_rule",
            "validation_plan",
            "promotion_criteria",
            "rejection_conditions",
        ):
            value = research.get(field)
            if not value or (isinstance(value, str) and not value.strip()):
                errors.append(f"research.{field} is required")
    if errors:
        raise ValueError("Invalid experiment configuration: " + "; ".join(errors))


@dataclass(frozen=True)
class ExperimentContext:
    config_path: Path
    root: Path
    workspace_root: Path
    config: dict[str, Any]
    definition_sha256: str

    @property
    def runs_root(self) -> Path:
        return self.root / "runs"

    def run_root(self, run_id: str) -> Path:
        return self.runs_root / run_id


def load_experiment(path: Path | str) -> ExperimentContext:
    candidate = Path(path).expanduser().resolve()
    config_path = candidate / "experiment.json" if candidate.is_dir() else candidate
    if not config_path.is_file():
        raise FileNotFoundError(f"Experiment configuration not found: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_experiment_config(config)
    root = config_path.parent
    workspace_root = next(
        (
            ancestor
            for ancestor in root.parents
            if (ancestor / "backtest/experiments").is_dir()
            and config_path.is_relative_to(ancestor / "backtest/experiments")
        ),
        None,
    )
    if workspace_root is None:
        raise ValueError(f"Experiment must live below <workspace>/backtest/experiments: {root}")
    return ExperimentContext(
        config_path=config_path,
        root=root,
        workspace_root=workspace_root,
        config=config,
        definition_sha256=_canonical_hash(_definition_payload(config)),
    )


def cost_label(cost_bps: float) -> str:
    value = float(cost_bps)
    if value.is_integer():
        return f"cost_{int(value)}bps"
    return f"cost_{str(value).replace('.', '_')}bps"


def expected_blocks(context: ExperimentContext) -> list[dict[str, Any]]:
    return [
        {
            "block_id": f"{symbol}_{cost_label(float(cost))}",
            "symbol": symbol,
            "cost_bps": float(cost),
            "status": "pending",
        }
        for symbol in context.config["symbols"]
        for cost in context.config["cost_scenarios_bps_per_side"]
    ]


def _git_state(workspace_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str | None:
        process = subprocess.run(
            ["git", *args],
            cwd=workspace_root,
            text=True,
            capture_output=True,
            check=False,
        )
        return process.stdout.strip() if process.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    porcelain = run("status", "--porcelain")
    return {
        "commit": commit,
        "dirty": None if porcelain is None else bool(porcelain),
    }


def start_run(context: ExperimentContext, run_id: str | None = None) -> tuple[str, Path]:
    context = load_experiment(context.config_path)
    current = active_run(context)
    if current is not None and current[2].get("status") in RESUMABLE_RUN_STATUSES:
        active_id = current[0]
        raise RuntimeError(
            f"Experiment already has resumable active run {active_id}; resume it or explicitly mark it "
            "interrupted before creating another run"
        )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    git_state = _git_state(context.workspace_root)
    base_id = run_id or f"run_{timestamp}_{context.definition_sha256[:8]}"
    if not RUN_ID_PATTERN.fullmatch(base_id):
        raise ValueError(f"Invalid run id: {base_id}")
    candidate = base_id
    suffix = 1
    while context.run_root(candidate).exists():
        if run_id is not None:
            raise FileExistsError(f"Run already exists and cannot be overwritten: {candidate}")
        candidate = f"{base_id}_{suffix:02d}"
        suffix += 1
    run_root = context.run_root(candidate)
    run_root.mkdir(parents=True)
    snapshot_path = run_root / "experiment_snapshot.json"
    write_json(snapshot_path, context.config)
    record = {
        "schema_version": 1,
        "run_id": candidate,
        "experiment_id": context.config["experiment_id"],
        "experiment_definition_sha256": context.definition_sha256,
        "experiment_snapshot_sha256": sha256(snapshot_path),
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "validated_at_utc": None,
        "status": "running",
        "git": git_state,
        "expected_blocks": expected_blocks(context),
        "analysis": {"status": "pending"},
        "validation": {"status": "pending"},
    }
    write_json(run_root / "run.json", record)
    updated_config = dict(context.config)
    updated_config["active_run_id"] = candidate
    write_json(context.config_path, updated_config)
    append_research_event(
        context,
        event_type="run_started",
        run_id=candidate,
        summary=f"Started {candidate}.",
        details={"git": git_state, "definition_sha256": context.definition_sha256},
    )
    return candidate, run_root


def load_run(context: ExperimentContext, run_id: str) -> dict[str, Any]:
    path = context.run_root(run_id) / "run.json"
    if not path.is_file():
        raise FileNotFoundError(f"Run record not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def active_run(context: ExperimentContext) -> tuple[str, Path, dict[str, Any]] | None:
    """Return the current active run from the authoritative on-disk config."""
    refreshed = load_experiment(context.config_path)
    run_id = refreshed.config.get("active_run_id")
    if not run_id:
        return None
    record = load_run(refreshed, str(run_id))
    return str(run_id), refreshed.run_root(str(run_id)), record


def resume_active_run(context: ExperimentContext) -> tuple[str, Path, dict[str, Any]]:
    """Resume an interrupted session without allocating a duplicate run directory."""
    refreshed = load_experiment(context.config_path)
    current = active_run(refreshed)
    if current is None:
        raise RuntimeError("Experiment has no active run to resume")
    run_id, run_root, record = current
    status = record.get("status")
    if status not in RESUMABLE_RUN_STATUSES:
        raise RuntimeError(f"Active run {run_id} is not resumable with status {status!r}")
    if record.get("experiment_definition_sha256") != refreshed.definition_sha256:
        raise RuntimeError("Active run uses a different experiment definition; interrupt it before starting a new run")
    snapshot_path = run_root / "experiment_snapshot.json"
    if not snapshot_path.is_file() or sha256(snapshot_path) != record.get("experiment_snapshot_sha256"):
        raise RuntimeError(f"Active run {run_id} has an invalid experiment snapshot")
    return run_id, run_root, record


def assert_run_writable(context: ExperimentContext, run_id: str) -> dict[str, Any]:
    record = load_run(context, run_id)
    if record.get("status") not in WRITABLE_RUN_STATUSES:
        raise RuntimeError(f"Run {run_id} is immutable with status {record.get('status')!r}")
    if record.get("experiment_definition_sha256") != context.definition_sha256:
        raise RuntimeError("Experiment definition changed after this run was started; start a new run")
    snapshot_path = context.run_root(run_id) / "experiment_snapshot.json"
    if sha256(snapshot_path) != record.get("experiment_snapshot_sha256"):
        raise RuntimeError("Experiment snapshot hash mismatch")
    return record


def block_root(context: ExperimentContext, run_id: str, symbol: str, cost_bps: float) -> Path:
    return context.run_root(run_id) / symbol / cost_label(cost_bps)


def reserve_block(context: ExperimentContext, run_id: str, symbol: str, cost_bps: float) -> Path:
    record = assert_run_writable(context, run_id)
    block = next(
        (
            item
            for item in record["expected_blocks"]
            if item["symbol"] == symbol and float(item["cost_bps"]) == float(cost_bps)
        ),
        None,
    )
    if block is None:
        raise ValueError(f"Block is not part of the experiment: {symbol} {cost_bps:g}bps")
    output = block_root(context, run_id, symbol, cost_bps)
    if block.get("status") != "pending" or output.exists():
        raise FileExistsError(f"Block already exists and cannot be overwritten: {output}")
    output.mkdir(parents=True)
    return output


def record_block_complete(
    context: ExperimentContext,
    run_id: str,
    symbol: str,
    cost_bps: float,
    manifest_path: Path,
) -> None:
    record = assert_run_writable(context, run_id)
    block = next(
        item
        for item in record["expected_blocks"]
        if item["symbol"] == symbol and float(item["cost_bps"]) == float(cost_bps)
    )
    if block.get("status") != "pending":
        raise RuntimeError(f"Block was already recorded: {block['block_id']}")
    block.update(
        {
            "status": "completed",
            "completed_at_utc": utc_now(),
            "manifest": str(manifest_path.relative_to(context.run_root(run_id))),
            "manifest_sha256": sha256(manifest_path),
        }
    )
    write_json(context.run_root(run_id) / "run.json", record)


def record_analysis_complete(context: ExperimentContext, run_id: str) -> None:
    record = assert_run_writable(context, run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze an incomplete run: {incomplete}")
    record["analysis"] = {"status": "completed", "completed_at_utc": utc_now()}
    record["status"] = "completed_unvalidated"
    record["completed_at_utc"] = utc_now()
    write_json(context.run_root(run_id) / "run.json", record)
    append_research_event(
        context,
        event_type="analysis_completed",
        run_id=run_id,
        summary=f"Completed analysis for {run_id}; validation remains pending.",
    )


def record_run_failed(
    context: ExperimentContext,
    run_id: str,
    *,
    phase: str,
    reason: str,
) -> None:
    record = load_run(context, run_id)
    if record.get("status") not in {"running", "completed_unvalidated"}:
        raise RuntimeError(f"Run {run_id} cannot fail from status {record.get('status')!r}")
    failure = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "failed_at_utc": utc_now(),
        "phase": phase,
        "reason": reason,
    }
    failure_path = context.run_root(run_id) / "failure.json"
    write_json(failure_path, failure)
    record["status"] = "failed"
    record["completed_at_utc"] = failure["failed_at_utc"]
    record["failure"] = {
        "phase": phase,
        "reason": reason,
        "report": "failure.json",
        "report_sha256": sha256(failure_path),
    }
    write_json(context.run_root(run_id) / "run.json", record)
    append_research_event(
        context,
        event_type="run_failed",
        run_id=run_id,
        summary=f"Marked {run_id} failed during {phase}: {reason}",
        details={"phase": phase, "reason": reason},
    )
    _restore_active_pointer(context, run_id)


def _restore_active_pointer(context: ExperimentContext, run_id: str) -> None:
    """Stop a terminal non-validated run from remaining the active pointer."""
    current = json.loads(context.config_path.read_text(encoding="utf-8"))
    if current.get("active_run_id") != run_id:
        return
    validated = current.get("latest_validated_run_id")
    if validated:
        current["active_run_id"] = validated
    else:
        current.pop("active_run_id", None)
    write_json(context.config_path, current)


def record_run_interrupted(
    context: ExperimentContext,
    run_id: str,
    *,
    reason: str,
) -> None:
    """Record an operational/session interruption, not a research failure."""
    record = load_run(context, run_id)
    if record.get("status") not in RESUMABLE_RUN_STATUSES:
        raise RuntimeError(f"Run {run_id} cannot be interrupted from status {record.get('status')!r}")
    interrupted_at = utc_now()
    interruption = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": run_id,
        "interrupted_at_utc": interrupted_at,
        "reason": reason,
    }
    interruption_path = context.run_root(run_id) / "interruption.json"
    write_json(interruption_path, interruption)
    record["status"] = "interrupted"
    record["completed_at_utc"] = interrupted_at
    record["interruption"] = {
        "reason": reason,
        "report": "interruption.json",
        "report_sha256": sha256(interruption_path),
    }
    write_json(context.run_root(run_id) / "run.json", record)
    append_research_event(
        context,
        event_type="run_interrupted",
        run_id=run_id,
        summary=f"Interrupted {run_id}: {reason}",
        details={"reason": reason},
    )
    _restore_active_pointer(context, run_id)


def prune_interrupted_run(
    context: ExperimentContext,
    run_id: str,
    *,
    superseded_by_run_id: str,
) -> dict[str, int]:
    """Delete an interrupted run only after a validated successor exists."""
    context = load_experiment(context.config_path)
    record = load_run(context, run_id)
    if record.get("status") != "interrupted":
        raise RuntimeError(f"Run {run_id} is not interrupted")
    successor = load_run(context, superseded_by_run_id)
    if successor.get("status") != "validated":
        raise RuntimeError(f"Successor {superseded_by_run_id} is not validated")
    if successor.get("experiment_id") != record.get("experiment_id"):
        raise RuntimeError("Interrupted run and successor belong to different experiments")
    for pointer in ("active_run_id", "latest_validated_run_id"):
        if context.config.get(pointer) == run_id:
            raise RuntimeError(f"Cannot prune run referenced by {pointer}")
    run_root = context.run_root(run_id)
    files = [path for path in run_root.rglob("*") if path.is_file()]
    summary = {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}
    append_research_event(
        context,
        event_type="interrupted_run_pruned",
        run_id=run_id,
        summary=f"Pruned interrupted {run_id}; superseded by {superseded_by_run_id}.",
        details={"superseded_by_run_id": superseded_by_run_id, **summary},
    )
    shutil.rmtree(run_root)
    return summary


def record_validation(context: ExperimentContext, run_id: str, validation: dict[str, Any]) -> None:
    record = load_run(context, run_id)
    if record.get("status") != "completed_unvalidated":
        raise RuntimeError(f"Run {run_id} cannot be validated from status {record.get('status')!r}")
    if validation.get("status") != "passed":
        raise ValueError("Only a passing validation may finalize a run")
    relative_report = Path("runs") / run_id / "report.html"
    report_path = context.config_path.parent / relative_report
    if not report_path.is_file():
        raise FileNotFoundError(f"Validated run has no HTML report: {report_path}")
    report_entrypoint = context.config_path.parent / "report.html"
    if report_entrypoint.exists() and not report_entrypoint.is_symlink():
        raise RuntimeError(f"Experiment report entrypoint must be a symlink: {report_entrypoint}")
    validated_at = utc_now()
    record["validation"] = {
        "status": "passed",
        "validated_at_utc": validated_at,
        "report": "validation.json",
        "report_sha256": sha256(context.run_root(run_id) / "validation.json"),
    }
    record["status"] = "validated"
    record["validated_at_utc"] = validated_at
    write_json(context.run_root(run_id) / "run.json", record)
    updated_config = dict(context.config)
    updated_config["active_run_id"] = run_id
    updated_config["latest_validated_run_id"] = run_id
    write_json(context.config_path, updated_config)
    if report_entrypoint.is_symlink():
        report_entrypoint.unlink()
    report_entrypoint.symlink_to(relative_report)
    append_research_event(
        context,
        event_type="run_validated",
        run_id=run_id,
        summary=f"Validated and locked {run_id}.",
        details={"validation_status": "passed"},
    )
