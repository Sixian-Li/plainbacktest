# SMA200 asymmetric-threshold experiment

This directory defines one long-lived research question: after a full SMA200 warmup, buy only on a close crossing above `SMA200 × (1+b%)`, sell after a close below `SMA200 × (1-a%)`, and execute each signal at the next regular-session open.

- `experiment.json` is the authoritative strategy, parameter, cost, reporting, and research-protocol configuration.
- `runs/<run_id>/` contains one immutable concrete execution, including a frozen configuration snapshot, block ledgers, analysis, reports, validation evidence, and source/data fingerprints.
- `runs/legacy_20260808T095538Z/` preserves the exact pre-lifecycle result set; it is historical evidence, not a validated lifecycle run.

Parameter combinations inside a run are called **cases**. A **run** is the complete execution of all configured symbols, costs, cases, analysis, and validation gates.

Start a run from `quant/backtest`:

    .venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid

The command prints a run ID. Pass it to each configured block, then analyze and validate that same run. Existing run directories and completed blocks are never overwritten.
