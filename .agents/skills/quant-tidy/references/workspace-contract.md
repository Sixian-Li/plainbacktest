# Quant workspace contract

## Root responsibilities

```text
quant/
├── worktrees/       visible lightweight Agent worktrees, ignored by Git
├── catalog.md        pure file-tree navigation
├── log.md            short chronological work record
├── data/             shared immutable/raw and standardized market data
└── backtest/         everything used only by the backtest project
```

Future projects such as factor research or learning materials get their own top-level directories only when real content exists. Do not pre-create empty project trees.

## Catalog

Allow only the title `# Quant 文件目录` followed by one fenced `text` tree. Collapse large raw/generated collections with directory entries rather than listing thousands of files. Use one short purpose comment per important path.

## Log

Use `## YYYY-MM-DD` headings and bullets. Record material outcomes, not chat transcripts. Mention what changed and the result; keep each bullet concise.

## Experiment registry and lineage

Maintain `backtest/experiments/index.md`. Give each experiment one section containing its exact ID, date/status, natural-language strategy, universe/data, signal and execution timing, parameter/cost assumptions, one-sentence result, correctness evidence, and artifact links.

Maintain `backtest/experiments/lineage.json` as the machine authority for research programs, display versions, full creation dates, parent/child edges, and View associations. Every canonical experiment must appear exactly once and every `display_code` must be unique. Every edge requires `relation`, `rationale`, and `change_summary`; this is the only manually recorded source for why and how a strategy changed. Store experiments physically at `backtest/experiments/<PROGRAM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`; move an existing experiment together with its entire run subtree, without rewriting validated run content.

Rebuild `index.md`, combined `strategy_evolution.md`, `program_evolution/{DER,ROT,TIM}.md`, `scorecard.csv`, and `research_map.html` with `backtest/scripts/build_research_catalog.py`. Never hand-edit either combined or per-program evolution files: both combine the one-time edge intent with automatic parent/child config differences and current representative run results. `scorecard.csv` is the long-form metric record for the current latest validated run (or the active run when none is validated). `research_events.jsonl` is append-only and records lifecycle/reorganization events; root `log.md` remains the concise human handoff.

Every experiment directory must contain one authoritative `experiment.json`. Under `strategy`, require `description`, `buy_rule`, `sell_rule`, `signal_time`, and `execution_time`. Under `reporting`, require the versioned project template ID/path and a short reuse review. Under `research`, require the stage, hypothesis, primary metric, selection rule, validation plan, promotion criteria, and rejection conditions.

Store concrete executions at `runs/<run_id>/`. Each formal run has a frozen experiment snapshot, run state, provenance, block manifests, reports, artifact hashes, and validation evidence. Parameter points are cases, not runs. `active_run_id` may identify work in progress; `latest_validated_run_id` must point only to a validated immutable run. A network or agent-session loss is `interrupted`, not `failed`; never leave an unreferenced `running` or `completed_unvalidated` directory. Reports are run outputs, not temporary reports; reusable source stays under `backtest/report_templates/`. Every newly generated formal HTML report must show the exact frozen strategy immediately after its title and before all metrics. When a latest validated HTML exists, maintain `experiment/report.html` as a relative symlink to `runs/<latest_validated_run_id>/report.html` so the user has a stable visible entry point without weakening run immutability.

Classify backtest tests under `core`, `data`, `lifecycle`, `reporting`, and `strategies/{der,rot,tim}`. Use the stable `quantkit.paths` roots rather than deriving project roots from each test file's directory depth.

## Backtest environment

Keep `.venv`, `requirements.in`, and `requirements.lock` inside `backtest/`. Run tools from `backtest` so imports and relative commands remain stable.

Create linked worktrees only through `backtest/scripts/manage_worktree.py`; all registrations except the primary worktree must resolve below the primary repository's visible `worktrees/` directory. Use sparse checkout so unrelated historical run trees are absent. Shared datasets and `.venv` must be links to the primary tree: refuse an existing real directory or file instead of copying it or silently skipping it. A worktree is execution isolation, not delivery; publish each validated experiment and its ignored artifacts to the primary tree, rebuild the catalog, then remove the fully merged worktree while retaining its branch.
