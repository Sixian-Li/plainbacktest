# Quant Research

**English** | [中文](README.zh-CN.md)

A natural-language strategy backtesting framework powered by an agent and project-local Skills. Turn a trading idea into explicit rules, run the backtest, reconcile it against an independent ledger, and produce an interactive report with traceable inputs and results.

Source code and bundled data: [Sixian-Li/quant-research](https://github.com/Sixian-Li/quant-research).

```text
Strategy in natural language → Agent + Skill clarify the rules → Freeze the experiment
→ Data checks → PyBroker execution + independent ledger → Report and research record
```

Your agent interprets the strategy and implements its rules; the Python framework handles reproducible calculation and validation. The repository includes three Skills, curated local data, research code, and 83 experiment definitions. Start with an offline example, then use your agent to continue the research.

## Run your first example

Clone the repository and run the following commands. The verified environment is macOS with Python 3.13. Installing dependencies requires an internet connection; running the example needs no market-data API, credentials, or live data.

```bash
git clone https://github.com/Sixian-Li/quant-research.git
cd quant-research
python3.13 -m venv backtest/.venv
backtest/.venv/bin/python -m pip install -r backtest/requirements.lock
backtest/.venv/bin/python backtest/scripts/release_data.py check
backtest/.venv/bin/python backtest/scripts/quickstart.py
```

Open the `outputs/example_…/report.html` path printed at the end. Each execution creates a new directory containing the input data, frozen rules, daily account state, orders, metrics, code and data hashes, and reconciliation results.

The default example uses QQQ from 2016-01-01 through 2026-08-04. It holds QQQ when SMA20 is above SMA100 and stays in cash otherwise. Signals are confirmed at the close and executed at the next session's open, with a cost of 5 bps per side. Initial capital is $100,000; fractional shares are allowed, with no leverage. The example uses the framework's PyBroker execution engine, independent ledger, and v5 report template.

Change the parameters or choose another approved symbol:

```bash
backtest/.venv/bin/python backtest/scripts/quickstart.py --symbol SPY --fast 50 --slow 200
```

This exploratory example checks installation, execution, and reconciliation. It does not mark existing experiments as validated or replace parameter robustness and out-of-sample analysis. Moving averages use earlier history for warmup. Adjusted OHLC prices approximate total returns without modeling individual cash dividend payments.

## Research with natural language

Open this directory with an agent that supports project Skills. Have it read [AGENTS.md](AGENTS.md), then describe a strategy. For example:

> Use quant-backtest to study QQQ with an SMA20/SMA100 rule. If the fast SMA is above the slow SMA at the close, hold a fully invested position from the next session's open; otherwise, exit to cash. Define the data range, costs, warmup, and benchmark before implementing the strategy, reconciling the ledgers, and explaining the results.

The project overview and quickstart are available in English and Chinese. Detailed research documents and report text are currently mainly in Chinese.

All three Skills are versioned with the repository, each with a `SKILL.md` entry point:

| Skill | Purpose |
| --- | --- |
| [quant-backtest](.agents/skills/quant-backtest/SKILL.md) | Turn a strategy description into an experiment, execution, independent reconciliation, report, and research record |
| [data-update](.agents/skills/data-update/SKILL.md) | Inspect data snapshots and manage isolated updates and candidate review when needed |
| [quant-tidy](.agents/skills/quant-tidy/SKILL.md) | Maintain directories, experiment lineage, and workspace structure |

These Skills are part of the project. Agents that support `.agents/skills` can discover them directly; other agents can read the relevant `SKILL.md` and its references. They do not depend on the author's global Skill installation. See the [research architecture](backtest/docs/architecture.md) for Git provenance, run lifecycles, validation gates, and worktree conventions used in formal research. The example also works from a downloaded source ZIP.

## What's included

- `backtest/quantkit/`, `scripts/`, and `tests/`: execution, ledgers, strategies, metrics, data handling, and validation.
- `backtest/report_templates/`: self-contained Plotly reports. The default v5 template explains the strategy before presenting results.
- `backtest/experiments/`: 83 definitions and their lineage across three research programs: DER, ROT, and TIM.
- `data/`: 1,303 canonical daily-price CSVs, trading calendars, membership tables, quality evidence, and two original Nasdaq-100 ZIP archives. See the [data documentation](data/README.md).
- `research/`: indicator and market-observation tools without strategy account ledgers.

This is an independent distribution snapshot. Historical runs and large collections of old reports are not bundled, and the active/latest run pointers have been cleared. Historical findings from the source workspace have not been revalidated here. Some experiments require earlier training artifacts or original archives that are not included; see the [distribution scope](backtest/docs/release/scope.md). The source revision and data hashes at the time of copying are recorded in [source_snapshot.json](backtest/docs/release/source_snapshot.json).

This version uses the bundled data by default. `yfinance` is a transitive dependency of PyBroker, but it has not been integrated as this project's default data provider. The quickstart does not call Yahoo. The existing shadow-update tools are optional advanced features.

## Validation and optional labs

```bash
backtest/.venv/bin/python -m pip check
backtest/.venv/bin/python backtest/scripts/release_data.py check
cd backtest
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m scripts.build_research_catalog --check
.venv/bin/python -m scripts.audit_workspace --workspace ..
```

Tests that require original archives absent from this distribution report an explicit skip, not a pass. See the [verification record](backtest/docs/release/verification.md) for results. The distributed test suite does not implicitly rebuild the bundled canonical data.

Two optional Streamlit labs are included. Launch either from the project root:

```bash
backtest/.venv/bin/python -m streamlit run backtest/dual_sma_lab/app.py
backtest/.venv/bin/python -m streamlit run research/indicator_lab/app.py
```

Browser interaction checks and PDF export also require Node.js and Chrome/Chromium. Set `QUANT_CHROME_PATH` to use a specific browser executable. The standard example and Python tests do not require them. Other operating systems have not yet been fully verified.

## Licensing

Original project code, Skills, and documentation are licensed under [MIT](LICENSE). The bundled data under `data/` is licensed under [CC BY 4.0](data/LICENSE); attribution details are in the [data documentation](data/README.md). The data rights holder has confirmed ownership and authorized this license.

Third-party dependencies retain their own licenses. In particular, PyBroker 1.2.12 uses **Apache 2.0 with Commons Clause**, which includes a restriction on selling the software as defined in that license. The dependency stack is therefore not entirely MIT-licensed. See [third-party notices](THIRD_PARTY_NOTICES.md) for the original terms.
