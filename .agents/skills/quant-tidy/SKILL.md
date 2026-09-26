---
name: quant-tidy
description: Audit and reorganize the Quant multi-project workspace. Use when moving, renaming, deduplicating, or cleaning Quant files; adding or moving a project; repairing catalog.md, log.md, lineage, path, or environment drift; or checking structural integrity. Routine backtest creation and publication belong to quant-backtest.
---

# Quant Tidy

Keep the workspace understandable to a fresh agent without turning the root into a documentation dump.

## Distributed project

This skill is versioned at `.agents/skills/quant-tidy/` in this repository. Locate the root from the current checkout; do not depend on a user-level skill or the author’s original workspace. Read `README.md` and `backtest/docs/release/scope.md` first. The distribution contains canonical data and experiment definitions, not the complete source archives or historical runs.

This directory is an independent distribution, not a linked worktree. Its own data and environment may be real directories. The shared-link requirement below applies only to linked worktrees created from this repository. Root `README.md`, `LICENSE`, and `THIRD_PARTY_NOTICES.md` are intended distribution files.

## Workflow

1. Locate the workspace root by finding `catalog.md`, `log.md`, `data/`, and `backtest/`. Read `catalog.md` and `log.md` first.
2. Read `references/workspace-contract.md`. Preserve shared data and project boundaries defined there.
3. Read `backtest/docs/architecture.md` and `backtest/experiments/lineage.json`, then run `backtest/.venv/bin/python -m scripts.audit_workspace` from `backtest/`. If unavailable, run `scripts/audit_quant_workspace.py --workspace <root>` from this skill.
4. Inspect the proposed changes before moving files. Never modify purchased raw data. Keep unrelated user changes.
5. Put shared datasets under `data/`; put every backtest-only environment, dependency, source, test, document, and experiment under `backtest/`.
6. Keep canonical experiments below `backtest/experiments/<PROGRAM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`, with a matching full `created_on` in lineage. Keep tests below `tests/core`, `data`, `lifecycle`, `reporting`, or `strategies/{der,rot,tim}`. After changes, update all hard-coded paths, imports, Markdown links, manifests, hashes, and reproducibility commands. Search for old paths with `rg`.
7. Keep root `catalog.md` to one fenced text tree plus its title. Give every important entry a one-sentence purpose; put no status, result, decision, or plan there.
8. Append one concise dated item to root `log.md` for each material outcome. Keep each item near 100 Chinese characters or shorter when one sentence suffices.
9. When structural work touches experiments, confirm every experiment remains registered in both `backtest/experiments/index.md` and `lineage.json`, with exactly one program, one unique display version, and complete parent edges. Confirm `active_run_id` and `latest_validated_run_id` point to real lifecycle runs; treat unreferenced `running` or `completed_unvalidated` directories as unfinished cleanup, not as new research nodes.
10. Run `.venv/bin/python -m scripts.build_research_catalog` after structural experiment/run changes, then run it again with `--check`. Never hand-edit generated `strategy_evolution.md`, `program_evolution/*.md`, `scorecard.csv`, or `research_map.html`.
11. Re-run the workspace audit and the relevant project tests. Report unresolved failures; do not silently weaken checks.
12. Keep every linked Agent worktree below the primary repository's visible `worktrees/` pool. Use `backtest/scripts/manage_worktree.py`; treat the primary tree as the only canonical delivery location. Before removing a legacy or stale worktree, prove that its branch and validated artifacts are preserved in the primary tree. Shared data and environments must be symlinks, never copies.

## Guardrails

- Do not initialize Git, connect brokers, or delete raw data unless the user explicitly requests it.
- Do not put research conclusions in `catalog.md`.
- Do not create one explanatory Markdown file per experiment by default; use the central experiment registry and machine config.
- Do not claim the workspace is tidy while old root paths, stale links, or unregistered experiments remain.
- Do not move generated run output back to the experiment root or edit a validated run. Preserve pre-lifecycle outputs under an explicitly labeled legacy directory.
- Do not call a network or agent-session interruption a strategy failure. Resume the active run when possible; prune an interrupted run only after a validated successor exists and the user has authorized cleanup.
- Do not leave canonical experiments flat. Physical program/version/date classification and `lineage.json` must agree. Move an experiment with its entire `runs/` subtree, update live references, and never rewrite the contents of a validated run.
- Do not place linked Quant worktrees in hidden, temporary, or external directories, and never overwrite or tolerate an existing real data/environment directory while bootstrapping shared links.

## Resources

- Read `references/workspace-contract.md` for the directory contract and record formats.
- Run `scripts/audit_quant_workspace.py` when the workspace copy of the audit is missing or suspect.
