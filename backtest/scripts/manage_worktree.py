#!/usr/bin/env python3
"""Create, publish, inspect, and remove lightweight Quant agent worktrees."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def _primary_workspace_root() -> Path:
    script_workspace = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=script_workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode == 0:
        return Path(process.stdout.strip()).resolve().parent
    return script_workspace


WORKSPACE_ROOT = _primary_workspace_root()
WORKTREE_ROOT = WORKSPACE_ROOT / "worktrees"
LEGACY_WORKTREE_ROOT = WORKSPACE_ROOT / ".worktrees"
SHARED_DIRECTORY_PATHS = (
    Path("backtest/.venv"),
    Path("data/raw"),
    Path("data/processed/daily"),
    Path("data/2026-08-05多个数据包_rethink"),
    Path("data/2026-08-05美股数据_全"),
)
SHARED_FILE_PATTERNS = (
    "data/processed/calendars/*.csv",
    "data/processed/universes/**/security_master.csv",
    "data/processed/universes/**/snapshot_exclusions.csv",
    "data/processed/universes/**/snapshot_reconciliation.csv",
    "data/processed/universes/**/membership_intervals.csv",
)
SPARSE_BASE_PATTERNS = (
    "/*",
    "!/backtest/experiments/*/*/runs/",
)
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
PROGRAM_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def _run(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        input=input_text,
        capture_output=True,
        check=check,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def worktree_path(name: str) -> Path:
    """Return the visible repository-local destination for a named worktree."""

    if not SAFE_NAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("worktree name must use lowercase letters, digits, dot, dash or underscore")
    return WORKTREE_ROOT / name


def experiment_relative_path(value: str | None) -> Path | None:
    """Validate a path relative to backtest/experiments for sparse checkout and publish."""

    if value in (None, ""):
        return None
    relative = Path(str(value))
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or PROGRAM_CODE.fullmatch(relative.parts[0]) is None
    ):
        raise ValueError("experiment must be PROGRAM/experiment-directory")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("experiment path may not traverse outside backtest/experiments")
    return relative


def sparse_patterns(experiment: Path | None = None) -> tuple[str, ...]:
    """Exclude every historical run except the one experiment assigned to this worktree."""

    patterns = list(SPARSE_BASE_PATTERNS)
    if experiment is not None:
        patterns.append(f"/backtest/experiments/{experiment.as_posix()}/runs/")
    return tuple(patterns)


def configure_sparse_checkout(target_root: Path, experiment: Path | None = None) -> None:
    patterns = "\n".join(sparse_patterns(experiment)) + "\n"
    _run(
        ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
        cwd=target_root,
        input_text=patterns,
    )
    _run(["git", "checkout"], cwd=target_root)


def _ensure_shared_link(source: Path, target: Path, relative: Path) -> str:
    if target.is_symlink():
        if target.resolve() != source.resolve():
            raise RuntimeError(f"wrong shared link target: {relative}")
        return f"already linked: {relative}"
    if target.exists():
        raise RuntimeError(
            f"shared path is a real file/directory instead of a link: {relative}; "
            "refusing to keep or overwrite a data copy"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())
    return f"linked: {relative}"


def bootstrap_shared_paths(target_root: Path) -> list[str]:
    """Link approved shared assets and reject real data/environment copies."""

    messages: list[str] = []
    for relative in SHARED_DIRECTORY_PATHS:
        source = WORKSPACE_ROOT / relative
        if not source.exists():
            messages.append(f"skip missing source: {relative}")
            continue
        messages.append(_ensure_shared_link(source, target_root / relative, relative))

    linked_files: set[Path] = set()
    for pattern in SHARED_FILE_PATTERNS:
        for source in sorted(WORKSPACE_ROOT.glob(pattern)):
            if not source.is_file():
                continue
            relative = source.relative_to(WORKSPACE_ROOT)
            if relative in linked_files:
                continue
            linked_files.add(relative)
            target = target_root / relative
            if target.is_file() and not target.is_symlink():
                tracked = _run(
                    ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
                    cwd=target_root,
                    check=False,
                ).returncode == 0
                if tracked:
                    if _sha256(source) != _sha256(target):
                        raise RuntimeError(f"tracked shared metadata differs from primary: {relative}")
                    messages.append(f"tracked metadata: {relative}")
                    continue
            messages.append(_ensure_shared_link(source, target, relative))
    return messages


def create_worktree(
    name: str,
    branch: str,
    start_point: str,
    *,
    experiment: Path | None = None,
) -> Path:
    destination = worktree_path(name)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"destination already exists: {destination}")
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)

    branch_exists = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=WORKSPACE_ROOT,
        check=False,
    ).returncode == 0
    command = ["git", "worktree", "add", "--no-checkout"]
    if not branch_exists:
        command.extend(["-b", branch])
    command.append(str(destination))
    command.append(branch if branch_exists else start_point)
    _run(command, cwd=WORKSPACE_ROOT)
    configure_sparse_checkout(destination, experiment)
    return destination


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_report_entrypoint(experiment_root: Path) -> Path | None:
    """Expose the latest validated report at the experiment root without duplicating HTML."""

    config_path = experiment_root / "experiment.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"missing experiment.json: {experiment_root}")
    run_id = str(_load_json(config_path).get("latest_validated_run_id") or "")
    if not run_id:
        return None
    relative_target = Path("runs") / run_id / "report.html"
    target = experiment_root / relative_target
    if not target.is_file():
        raise FileNotFoundError(f"latest validated report is missing: {target}")
    entrypoint = experiment_root / "report.html"
    if entrypoint.is_symlink():
        if Path(entrypoint.readlink()) != relative_target:
            raise RuntimeError(f"report entrypoint points to the wrong run: {entrypoint}")
        return entrypoint
    if entrypoint.exists():
        raise RuntimeError(f"report entrypoint must be a symlink, found real file: {entrypoint}")
    entrypoint.symlink_to(relative_target)
    return entrypoint


def refresh_report_entrypoints() -> list[Path]:
    created: list[Path] = []
    experiments_root = WORKSPACE_ROOT / "backtest/experiments"
    for config in sorted(experiments_root.glob("*/*/experiment.json")):
        entrypoint = ensure_report_entrypoint(config.parent)
        if entrypoint is not None:
            created.append(entrypoint)
    return created


def copy_publication_artifacts(source_experiment: Path, target_experiment: Path) -> list[Path]:
    """Copy missing ignored artifacts without ever overwriting canonical evidence."""

    source_config = _load_json(source_experiment / "experiment.json")
    target_config = _load_json(target_experiment / "experiment.json")
    if source_config.get("experiment_id") != target_config.get("experiment_id"):
        raise RuntimeError("source and target experiment IDs differ")
    run_id = str(source_config.get("latest_validated_run_id") or "")
    if not run_id:
        raise RuntimeError("experiment has no latest_validated_run_id")
    validation_path = source_experiment / "runs" / run_id / "validation.json"
    validation = _load_json(validation_path)
    if validation.get("status") != "passed":
        raise RuntimeError(f"latest run is not validated: {run_id}")

    copied: list[Path] = []
    for source in sorted(source_experiment.rglob("*")):
        if source.is_dir() or source == source_experiment / "report.html":
            continue
        relative = source.relative_to(source_experiment)
        target = target_experiment / relative
        if source.is_symlink():
            continue
        if target.exists():
            if not target.is_file() or _sha256(source) != _sha256(target):
                raise RuntimeError(f"refusing to overwrite differing canonical artifact: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target)
    ensure_report_entrypoint(target_experiment)
    return copied


def publication_report_available(
    source_experiment: Path,
    target_experiment: Path,
    run_id: str,
) -> bool:
    """Accept a sparse source when canonical already has the declared report.

    Ignored HTML is not materialized by sparse checkout.  A code-only task may
    still be assigned to an existing validated experiment, so publishing can
    reuse the canonical report only when the experiment/run identities match
    and its hash matches the source run's tracked artifact manifest.
    """

    source_report = source_experiment / "runs" / run_id / "report.html"
    if source_report.is_file():
        return True
    source_config_path = source_experiment / "experiment.json"
    target_config_path = target_experiment / "experiment.json"
    manifest_path = source_experiment / "runs" / run_id / "artifact_manifest.json"
    target_report = target_experiment / "runs" / run_id / "report.html"
    if not all(path.is_file() for path in (source_config_path, target_config_path, manifest_path, target_report)):
        return False
    source_config = _load_json(source_config_path)
    target_config = _load_json(target_config_path)
    if (
        source_config.get("experiment_id") != target_config.get("experiment_id")
        or str(target_config.get("latest_validated_run_id") or "") != run_id
    ):
        return False
    artifact = _load_json(manifest_path).get("artifacts", {}).get("report.html", {})
    expected_hash = str(artifact.get("sha256") or "")
    return bool(expected_hash) and _sha256(target_report) == expected_hash


def _branch_for_worktree(destination: Path) -> str:
    process = _run(["git", "branch", "--show-current"], cwd=destination)
    branch = process.stdout.strip()
    if not branch:
        raise RuntimeError(f"detached worktree cannot be published: {destination}")
    return branch


def publish_worktree(name: str, experiment: Path) -> list[Path]:
    """Merge a clean worktree and publish its complete validated experiment to the primary tree."""

    destination = worktree_path(name)
    if not destination.is_dir():
        raise FileNotFoundError(f"registered worktree directory not found: {destination}")
    if _run(["git", "status", "--porcelain"], cwd=destination).stdout.strip():
        raise RuntimeError("worktree has tracked or untracked changes; commit them before publish")
    if _run(["git", "status", "--porcelain"], cwd=WORKSPACE_ROOT).stdout.strip():
        raise RuntimeError("primary workspace is not clean; publish requires serial integration")

    source_experiment = destination / "backtest/experiments" / experiment
    source_config = _load_json(source_experiment / "experiment.json")
    run_id = str(source_config.get("latest_validated_run_id") or "")
    target_experiment = WORKSPACE_ROOT / "backtest/experiments" / experiment
    if not run_id or not publication_report_available(
        source_experiment,
        target_experiment,
        run_id,
    ):
        raise RuntimeError("worktree has no complete latest validated HTML report to publish")

    branch = _branch_for_worktree(destination)
    merge = _run(["git", "merge", "--no-edit", branch], cwd=WORKSPACE_ROOT, check=False)
    if merge.returncode != 0:
        _run(["git", "merge", "--abort"], cwd=WORKSPACE_ROOT, check=False)
        raise RuntimeError(f"automatic merge failed; resolve through an integration task:\n{merge.stderr}")

    copied = copy_publication_artifacts(source_experiment, target_experiment)
    python = WORKSPACE_ROOT / "backtest/.venv/bin/python"
    _run([str(python), "-m", "scripts.build_research_catalog"], cwd=WORKSPACE_ROOT / "backtest")
    _run([str(python), "-m", "scripts.build_research_catalog", "--check"], cwd=WORKSPACE_ROOT / "backtest")
    _run([str(python), "-m", "scripts.audit_workspace"], cwd=WORKSPACE_ROOT / "backtest")
    publication_paths = [
        "backtest/experiments/index.md",
        "backtest/experiments/lineage.json",
        "backtest/experiments/program_evolution",
        "backtest/experiments/research_events.jsonl",
        "backtest/experiments/research_map.html",
        "backtest/experiments/scorecard.csv",
        "backtest/experiments/strategy_evolution.md",
        str((target_experiment / "report.html").relative_to(WORKSPACE_ROOT)),
        "log.md",
    ]
    _run(["git", "add", "--", *publication_paths], cwd=WORKSPACE_ROOT)
    staged = _run(["git", "diff", "--cached", "--quiet"], cwd=WORKSPACE_ROOT, check=False)
    if staged.returncode != 0:
        _run(
            ["git", "commit", "-m", f"publish: {source_config['experiment_id']}"],
            cwd=WORKSPACE_ROOT,
        )
    return copied


def remove_worktree(name: str) -> Path:
    """Remove a clean, fully merged worktree while retaining its branch."""

    destination = worktree_path(name)
    if not destination.is_dir():
        raise FileNotFoundError(f"registered worktree directory not found: {destination}")
    if _run(["git", "status", "--porcelain"], cwd=destination).stdout.strip():
        raise RuntimeError("worktree has tracked or untracked changes")
    head = _run(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip()
    merged = _run(
        ["git", "merge-base", "--is-ancestor", head, "HEAD"],
        cwd=WORKSPACE_ROOT,
        check=False,
    )
    if merged.returncode != 0:
        raise RuntimeError("worktree branch is not fully merged into the primary HEAD")
    _run(["git", "worktree", "remove", "--force", str(destination)], cwd=WORKSPACE_ROOT)
    return destination


def worktree_status() -> list[str]:
    listing = _run(["git", "worktree", "list", "--porcelain"], cwd=WORKSPACE_ROOT).stdout
    lines: list[str] = []
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line.removeprefix("worktree ")).resolve()
        if path == WORKSPACE_ROOT:
            continue
        location = "visible" if path.is_relative_to(WORKTREE_ROOT) else "legacy/outside"
        lines.append(f"{location}: {path}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a lightweight visible worktree.")
    create.add_argument("name")
    create.add_argument("--branch", help="Defaults to codex/<name>.")
    create.add_argument("--start-point", default="HEAD")
    create.add_argument("--experiment", help="PROGRAM/directory; includes only this experiment's runs.")

    bootstrap = subparsers.add_parser("bootstrap", help="Add missing shared links and reject copies.")
    bootstrap.add_argument("name")

    publish = subparsers.add_parser("publish", help="Merge and publish one validated experiment.")
    publish.add_argument("name")
    publish.add_argument("--experiment", required=True, help="PROGRAM/experiment-directory")

    remove = subparsers.add_parser("remove", help="Remove a clean, fully merged worktree.")
    remove.add_argument("name")

    subparsers.add_parser("refresh-reports", help="Create visible latest-report links for all experiments.")
    subparsers.add_parser("status", help="List visible and legacy registered worktrees.")

    args = parser.parse_args()
    if args.command == "create":
        experiment = experiment_relative_path(args.experiment)
        destination = create_worktree(
            args.name,
            args.branch or f"codex/{args.name}",
            args.start_point,
            experiment=experiment,
        )
        print(destination)
        for message in bootstrap_shared_paths(destination):
            print(message)
    elif args.command == "bootstrap":
        destination = worktree_path(args.name)
        if not destination.is_dir():
            raise FileNotFoundError(f"registered worktree directory not found: {destination}")
        print(destination)
        for message in bootstrap_shared_paths(destination):
            print(message)
    elif args.command == "publish":
        experiment = experiment_relative_path(args.experiment)
        assert experiment is not None
        copied = publish_worktree(args.name, experiment)
        print(f"published: {experiment}; copied artifacts: {len(copied)}")
    elif args.command == "remove":
        print(f"removed: {remove_worktree(args.name)}")
    elif args.command == "refresh-reports":
        print(f"report entrypoints: {len(refresh_report_entrypoints())}")
    elif args.command == "status":
        for line in worktree_status():
            print(line)


if __name__ == "__main__":
    main()
