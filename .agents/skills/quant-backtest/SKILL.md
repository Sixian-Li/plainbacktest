---
name: quant-backtest
description: Run the standardized Quant backtest pipeline from a natural-language strategy through data gating, tested implementation, PyBroker execution, independent-ledger reconciliation, parameter robustness analysis, interactive HTML, experiment registration, automatic strategy-evolution synchronization, and work logging. Use when creating, changing, rerunning, reviewing, or explaining a backtest in this Quant workspace.
---

# Quant Backtest

Turn the user's strategy into a reproducible experiment that a fresh agent can inherit without reverse-engineering prior chat.

## Distributed project

This skill is versioned at `.agents/skills/quant-backtest/` in this repository. Locate the root from the current checkout; do not depend on a user-level skill or the author’s original workspace. Read `README.md` and `backtest/docs/release/scope.md` first. The distribution contains canonical data and experiment definitions, not the complete source archives or historical runs.

For an initial runnable demonstration, use `backtest/.venv/bin/python backtest/scripts/quickstart.py`. Its ledger check is not formal run validation. Formal Git/worktree/run requirements below apply when creating formal research in an initialized repository; do not initialize Git merely to run the example. Do not restore historical run pointers unless the full corresponding artifacts are present.

## Start every task

1. Locate the root containing `catalog.md`, `data/`, and `backtest/`.
2. Read root `catalog.md`, root `log.md`, `backtest/README.md`, `backtest/docs/architecture.md`, `backtest/docs/research_protocol.md`, `backtest/experiments/lineage.json`, the relevant registry section, `experiment.json`, and its latest validated run/report when one exists.
3. Read `references/pipeline.md`. Read data or framework docs only when relevant.
4. Inspect `active_run_id` before allocating a run. If it is `running` or `completed_unvalidated` with the same definition, resume it; do not create a duplicate after a network/session interruption.
5. For a read-only review or explanation, run the targeted checks needed for the claim plus the catalog/workspace audit. Before changing or executing a strategy, run `pip check`, relevant tests, and the audit; finish material implementation with the full test suite.
6. For implementation or execution, use an isolated worktree below the primary repository's visible `worktrees/` pool. Create an experiment task with `cd backtest && .venv/bin/python -m scripts.manage_worktree create <task-name> --experiment <PROGRAM>/<experiment-directory>`. The manager uses sparse checkout and shared symlinks; never create hidden/external worktrees or copy `.venv` or large data into one.

## Define before coding

Write the authoritative `experiment.json` with a natural-language strategy description and exact rules for universe, warmup, indicators, entry/exit semantics, signal time, fill time, sizing, cash/margin, costs, benchmark, dates, and parameters. For `interactive_research_v5`, also write `strategy.plain_language.summary`, `buy`, `sell`, `execution`, and `position` as short complete Chinese sentences that a non-programmer can understand; keep formulas and machine conditions in the exact rule fields, not in this reader-facing story. Also predeclare `research.stage`, hypothesis, primary metric, selection rule, validation plan, promotion criteria, and rejection conditions. Distinguish a level condition from a crossing event. Ask only when an unresolved semantic choice would materially change results.

An experiment is the long-lived research definition. A run is one concrete execution that freezes configuration, data, code, environment, and outputs. A parameter combination inside a run is a case. Do not use these terms interchangeably. Changing strategy semantics creates a new experiment; data/code refreshes normally create a new run under the same experiment. Before starting a new experiment, assign its program, unique display version, full `created_on`, and lineage parent(s) in `experiments/lineage.json`; create its directory as `experiments/<PROGRAM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`. Run retries retain the same research version. For every parent-to-child edge, write `rationale` (why the research changed) and `change_summary` (what rules or intent changed) once in `lineage.json`. Do not manually edit `strategy_evolution.md` or `program_evolution/*.md`: the generator derives parent/child parameter differences and representative run metrics.

## Implement and verify

1. Reuse `quantkit/` modules and configuration; do not duplicate a strategy per symbol.
2. Add an automated test before changing fragile timing or accounting behavior. Put shared contracts under `tests/core`, `data`, `lifecycle`, or `reporting`; put strategy behavior under `tests/strategies/<der|rot|tim>`. Tests do not use experiment date/version names.
3. Require approved canonical data and save its hash for formal promotion evidence. An explicitly user-authorized exploratory run may use a registered `candidate_pending_review` point-in-time universe only when it freezes the candidate build ID, source/archive hashes, review status, adjustment choice, known gaps, and decision-time membership lag in experiment/run provenance. Keep `research.stage=exploratory`, surface the data limitation before results, and force promotion criteria to fail until the data is approved. Never modify purchased raw data or splice suppliers silently.
4. Force every order to an explicit `OPEN` or `CLOSE`; never allow PyBroker's default middle price into formal results.
5. Use completed bars only. A close-confirmed signal fills no earlier than the next declared bar time.
6. Create a run with `scripts.start_experiment_run` only when no resumable active run exists. Use `--resume-active` after a session interruption. Explicitly mark an abandoned session with `scripts.manage_experiment_run interrupt` before starting a replacement.
7. Reconcile PyBroker cash, shares, equity, dates, sides, shares, and fill prices against the independent reference ledger for every case. Run symbol/cost blocks as checkpoints and save every completed case, not only winners.
8. Reserve `failed` for deterministic code, data, ledger, report, or quality-gate failures. Use `interrupted` for network, process, or agent-session loss. After a validated successor exists, an explicitly authorized `scripts.manage_experiment_run prune --apply` may remove the interrupted directory while keeping the central event.
9. Treat full-sample scans as exploratory. Analyze local neighborhoods, connected high-performance plateaus, boundaries, costs, and risk metrics before naming a stable representative.

## Publish and hand off

Save the required machine artifacts from `references/pipeline.md`. Before building HTML, inspect `backtest/report_templates/` and reuse the closest accepted template through `quantkit.reporting.render_interactive_report`. Use `interactive_research_v5` for every new report. Immediately after the title and before all performance results, explain the frozen strategy in reading order: trading object and intent, when to buy, when to sell, how a signal becomes a fill, position/capital, costs, and benchmark. Use short human sentences from `strategy.plain_language`; do not put JSON, Python dictionaries, code conditions, machine-field tables, or an expanded parameter tree on the HTML/PDF front page. Keep the exact rules and full parameters in the web-only collapsed appendix and `experiment_snapshot.json`; hide that appendix and machine identity in print/PDF. Never mutate historical validated HTML/PDF. Generate the remaining self-contained Plotly report with readable navigation, visible-window Y-axis behavior for K-lines, manual Y controls, warmup markings, trades, equity, drawdown, and parameter surfaces. Keep formal strategy calculation read-only over saved results; label browser-only comparison scenarios explicitly.

For every formal experiment, record the template ID/path and a `reuse_review` decision in `experiment.json`: reuse an existing template, promote stable generally useful improvements into a new/versioned project template, or explain why no template applies. Do not copy a finished experiment HTML into this skill. When template code changes, run its unit/JavaScript tests and a real-browser interaction smoke test before publishing.

Add or update the experiment in `backtest/experiments/index.md` and `backtest/experiments/lineage.json`, append the material result to root `log.md`, and keep root `catalog.md` as a pure file tree. After every strategy research run or registry change, run `scripts.build_research_catalog` and `scripts.build_research_catalog --check` so the grouped registry, generated combined `strategy_evolution.md`, matching `program_evolution/<PROGRAM>.md`, scorecard, and lineage map all match the immutable evidence. Treat any stale generated file or an edge missing `rationale`/`change_summary` as incomplete work. Run the workspace audit; invoke `quant-tidy` only for actual structural reorganization. Finish with `scripts.validate_run`; only a passing run may become `latest_validated_run_id`, and a validated run is immutable. Maintain `experiment/report.html` as a relative link to that latest validated report. Commit the worktree branch, then use `scripts.manage_worktree publish <task-name> --experiment <PROGRAM>/<experiment-directory>` from a clean primary tree; the worktree is not a delivery location. Remove the fully merged worktree after publication. Give a candid limitation summary.

## Guardrails

- Do not call an in-sample winner a future-optimal parameter.
- Do not connect IBKR, enable financing, initialize Git, or expand scope without authorization.
- Do not claim exact corporate-action accounting when using adjusted OHLC without event data.
- Do not hide no-trade runs, failed quality gates, boundary optima, or mismatches.
- Do not turn operational interruptions into experiment versions, strategy evidence, or scorecard rows.
- Do not publish or validate a report whose strategy definition is absent, below its metrics, reconstructed from prose outside the frozen experiment snapshot, or presented as a raw configuration/code dump instead of a human-readable strategy story.
- Do not leave the only copy of a validated report in a worktree or treat a worktree as the canonical experiment store.

## Resources

- Read `references/pipeline.md` for schemas, artifacts, commands, and completion criteria.
- Run the workspace `scripts.validate_run --experiment <experiment-dir> --run-id <run-id>` before declaring a run validated.
