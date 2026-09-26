# Standard Quant backtest pipeline

## Stable workspace locations

- Shared data: `data/`
- Backtest project/environment: `backtest/`
- Core modules: `backtest/quantkit/`
- Experiments: `backtest/experiments/<DER|ROT|TIM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`
- Grouped registry: `backtest/experiments/`
- Research lineage/version authority: `backtest/experiments/lineage.json`
- Generated strategy evolution: `backtest/experiments/strategy_evolution.md`
- Generated per-program evolution: `backtest/experiments/program_evolution/DER.md`, `ROT.md`, `TIM.md`
- Unified metric history and flowchart: `backtest/experiments/scorecard.csv`, `research_map.html`
- Append-only research events: `backtest/experiments/research_events.jsonl`
- Work log and tree: `log.md`, `catalog.md`
- Linked worktrees: visible lightweight `worktrees/` via `backtest/scripts/manage_worktree.py`; the primary tree remains the only delivery location

Run Python commands from `backtest` with `.venv/bin/python`.

## Experiment definition

Require these strategy fields in `experiment.json`:

```json
{
  "experiment_id": "stable_unique_id",
  "status": "...",
  "strategy": {
    "name": "machine_name",
    "description": "Natural-language behavior and timing.",
    "buy_rule": "exact boolean/event rule",
    "sell_rule": "exact boolean/event rule",
    "signal_time": "when every input becomes knowable",
    "execution_time": "explicit future open/close",
    "positioning": "sizing and constraints",
    "plain_language": {
      "summary": "One short Chinese sentence explaining the idea without formulas.",
      "buy": "When a person should understand that the strategy buys.",
      "sell": "When a person should understand that the strategy sells.",
      "execution": "Signal confirmation and fill order in ordinary language.",
      "position": "Position and cash behavior in ordinary language."
    }
  }
}
```

Also record symbols, exclusions, warmup, parameters, costs, initial cash, financing, benchmark, and a predeclared research protocol. Data builds, software versions, exact source hashes, and other execution facts belong to the frozen run provenance rather than being rewritten into the experiment definition.

Register the canonical `experiment_id` exactly once in `experiments/lineage.json` with one program, one unique display code such as `DER-v0.20a.1`, a full ISO `created_on`, and explicit lineage edges. The physical folder must be `<PROGRAM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`; the short date is display-only and must agree with `created_on` and `created_at_utc`. Every edge requires `relation`, `rationale`, and `change_summary`. Record intent once in that edge; `scripts.build_research_catalog` automatically compares the parent/child strategy and parameter fields, reads representative run metrics, and regenerates both `strategy_evolution.md` and the matching `program_evolution/<PROGRAM>.md`. Versions describe research evolution; failed, interrupted, or repeated runs do not create a new research version.

## Run lifecycle and interruptions

Before creating a run, inspect `active_run_id`. Resume a matching `running` run, or finish validation for a matching `completed_unvalidated` run. The starter must refuse to allocate a second run while either resumable state is active.

Use `failed` only when a deterministic implementation, data, ledger, report, or quality gate fails. Use `interrupted` when the network, process, machine, or agent session stops the workflow. An interruption is operational history, not research evidence. After a validated successor exists, prune the interrupted directory only through the explicit lifecycle command; retain the compact central event.

Require these research fields: `stage`, `hypothesis`, `primary_metric`, `selection_rule`, `validation_plan`, `promotion_criteria`, and `rejection_conditions`. Follow `backtest/docs/research_protocol.md`; an exploratory full-sample result cannot skip directly to paper trading.

Require a report decision for every formal experiment:

```json
{
  "reporting": {
    "template_id": "versioned_project_template",
    "template_path": "backtest/report_templates/versioned_project_template",
    "reuse_review": "reused | promoted | not_applicable, with a short reason"
  }
}
```

Keep executable templates under `backtest/report_templates/` and their Python assembly code in the project. Use `interactive_research_v5` for new experiments. Every newly generated report must first explain the frozen strategy as a human-readable sequence, while retaining the exact frozen rules and parameters in a collapsed web appendix and `experiment_snapshot.json`. Never expand JSON, dictionaries, code, machine fields, or the parameter tree on the report/PDF front page; the printable version hides the technical appendix. Skills hold the workflow and validation contract, not copied finished HTML.

## Required formal outputs

Create `runs/<run_id>/` with `scripts.start_experiment_run`. It must contain `experiment_snapshot.json` and `run.json`. For each symbol/cost or comparable checkpoint block inside that run, save:

- `parameter_results.csv`: metrics and assumptions for all combinations;
- `orders.csv` and `trades.csv`: consolidated ledgers keyed by case ID;
- daily cash, shares, signals, and equity in CSV or NPZ;
- benchmark daily state and orders;
- `manifest.json`: source hash, timing, runtime, artifact hashes, and maximum reference-ledger differences.

At experiment level, keep the authoritative `experiment.json`; do not create a per-experiment README unless it carries information that cannot live in the registry or config. Inside each run, save `provenance.json`, `artifact_manifest.json`, machine-readable robustness analysis, a concise Markdown report, a self-contained interactive HTML report, and finally `validation.json`. Never overwrite a prior run.

## Completion checks

1. Environment lock matches installed packages and `pip check` passes.
2. All tests pass, including exact warmup and next-bar timing.
3. Every formal case matches the independent ledger within the declared tolerance.
4. Parameter/result counts, case IDs, matrix dimensions, order/trade totals, and hashes reconcile.
5. The K-line can zoom into an early interval and rescale its visible Y range; manual upper/lower controls work.
6. The report records a template/reuse decision and explains the frozen strategy immediately after the title, before summary metrics. For v5, `strategy.plain_language` is complete, the visible strategy flow contains no raw code/JSON or machine-field table, the technical appendix is collapsed on the web and hidden in print, and the exact frozen fields remain available there and in the snapshot. If the template changed, its project tests and a real-browser interaction smoke test pass.
7. The experiment registry and lineage describe the strategy, version, parentage, and artifacts.
8. `scripts.build_research_catalog --check` passes, so `index.md`, combined `strategy_evolution.md`, all `program_evolution/*.md`, `scorecard.csv`, and `research_map.html` match current experiment/run evidence; every lineage edge has a reason and change summary.
9. The root log records the material outcome and the workspace audit passes.
10. `scripts.validate_run` passes; only then set `latest_validated_run_id` and treat the run directory as immutable.
11. No unreferenced run remains in `running` or `completed_unvalidated`; resume it, validate it, or explicitly mark it interrupted.

## Reusable report promotion

At the end of every experiment, review whether any accepted HTML improvement is strategy-independent. Promote only stable interactions and layout into a versioned project template; keep experiment-specific titles, conclusions, series, and data outside it. A reusable performance trace must expose stable metadata such as `series_key`, `panel`, `label`, and one `is_benchmark` series so controls do not depend on trace order.

For `interactive_research_v5`, verify the human strategy story precedes `summary`, the exact-rule/parameter appendix is collapsed and print-hidden, plus series checkboxes, current-visible-range left-edge rebasing, equal-installment DCA, range preservation, and K-line visible-window Y scaling in Chrome. Rebasing and DCA are visualization scenarios over saved daily equity, not new formal backtests; state their capital, cash-interest, schedule, and cost assumptions in the UI.
