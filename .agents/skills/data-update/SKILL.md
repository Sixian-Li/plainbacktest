---
name: data-update
description: Operate and assess the Quant workspace market-data layer through the tested data-update CLI. Use when checking QQQ/SPY or constituent freshness, reviewing supported versus missing data capabilities, diagnosing quality, running a SPY-current-member shadow update, inspecting update reports, or previewing/importing purchased CSV/ZIP/JSON/XLSX/XLS data without silently changing approved datasets.
---

# Data Update

Use the workspace CLI as the single execution path. Keep update logic in the tested project code; do not reimplement provider calls in the skill.

## Distributed project

This skill is versioned at `.agents/skills/data-update/` in this repository. Locate the root from the current checkout; do not depend on a user-level skill or the author’s original workspace. Read `README.md` and `backtest/docs/release/scope.md` first. The distribution contains canonical data and experiment definitions, not the complete source archives or historical runs.

For the bundled snapshot, begin with `backtest/.venv/bin/python backtest/scripts/release_data.py status` and `check`. These are offline and read-only. The live CLI below is for optional data operations; its source/archive audit may report omitted historical files. Do not rebuild canonical data implicitly. Data reuse is authorized under `data/LICENSE`; quality-failed and pending-review products keep their original quality gates.

## Locate and inspect

1. Locate the Quant root containing `catalog.md`, `log.md`, `data/`, and `backtest/`.
2. Read `data/data_update_registry.json` before any mutating operation.
3. Run commands from the Quant root with:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py <command>
```

4. Never display API keys. The CLI resolves them from environment variables or macOS Keychain.

Read [contracts.md](references/contracts.md) when interpreting statuses, running a full update, or importing purchased files.

## Route the request

### Check the last update or health

Run:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py status
backtest/.venv/bin/python backtest/scripts/data_update.py check
```

Use `check --deep` only for an explicit full audit, after material data work, or before enabling promotion. It verifies large raw files and runs the full test suite, so expect it to take longer.

Report separately:

- approved database date;
- latest completed XNYS session;
- current-member coverage;
- tracked QQQ/SPY/VOO dates and whether their shadow update exists;
- capability boundaries, especially production promotion, Nasdaq-100 point-in-time membership, and hedge-asset coverage;
- latest source-validation and shadow-update run;
- warnings versus blocking failures;
- whether production writes and scheduling are enabled.

### Manually update

Run an isolated shadow update:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py shadow-update
```

For a fast diagnostic, pass `--symbols AAPL,FERG,BRK.B,BF.B --tiingo all`. For a requested full current-universe run, omit `--symbols` and `--limit`; warn that Twelve Data's free 8-credit/minute limit makes 503 symbols take roughly 63 minutes for the single adjusted-price request.

Treat exit code 2 as “review required,” not automatically as a crash. Open the `report_json` referenced by `data/processed/updates/sp500_shadow/latest.json` and explain the exact symbol statuses.

For a campaign rerun after code-only fixes, use immutable provider archives instead of spending primary-source credits again. `--replay-twelve-run RUN_ID` reuses a complete Twelve Data archive; `--replay-tiingo-runs RUN_ID[,RUN_ID...]` reuses available Tiingo payloads and fetches only missing required symbols. Never use replay to stand in for a new completed XNYS session.

If a live run stops after writing only some Twelve Data batches, preserve that raw run and resume into a new run ID:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py shadow-update \
  --resume-twelve-run PARTIAL_RUN_ID \
  --run-id NEW_RUN_ID
```

Resume reuses valid archived symbols, fetches only missing or stale symbols, and never modifies the source archive. Inspect `resume_source_issues` in the new report. Do not call a partial transport failure a completed campaign day.

If one archived symbol contains a transient latest-session value that later fails the independent cross-check, preserve the archive and refetch only that selected symbol while resuming:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py shadow-update \
  --resume-twelve-run SOURCE_RUN_ID \
  --refresh-symbols CBOE \
  --replay-tiingo-runs SOURCE_RUN_ID \
  --run-id NEW_RUN_ID
```

Use `--refresh-symbols` only with `--resume-twelve-run`. Require the replacement payload to pass the unchanged candidate and cross-source gates; do not use this option to overwrite or hide the original failed evidence.

Do not edit `data/processed/daily/equities/`. While `production_writes_enabled=false`, no command may promote candidates into the approved database.

### Import newly purchased data

Require the exact source path. Always preview first:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py import-purchased \
  --source /exact/external/path --deep
```

Summarize file count, bytes, root SHA256, formats, and inspection failures. Apply only after the user explicitly authorizes copying that exact source and supplies or confirms provider and acquisition date:

```bash
backtest/.venv/bin/python backtest/scripts/data_update.py import-purchased \
  --source /exact/external/path \
  --provider provider-name \
  --acquired-date YYYY-MM-DD \
  --label optional-label \
  --apply
```

Applied files remain immutable under `data/raw/purchased/` with status `pending_review`. Never infer permission to merge them into approved prices, rebuild historical membership, or delete the external source.

## Finish safely

1. Inspect the generated machine report and human-readable report when present.
2. Run `check`; use `check --deep` after an applied purchase import or material implementation change.
3. If material workspace files changed, also follow `quant-tidy` for `catalog.md`, `log.md`, audit, and tests.
4. State exactly what was updated, what stayed shadow/pending, the last covered session, and any user action required.
