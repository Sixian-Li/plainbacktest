#!/usr/bin/env python3
"""Analyze and render the fixed QQQ-flat expanded substitution study."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly

from quantkit.experiment import load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
import scripts.analyze_qqq_flat_trio_substitution as shared


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution"
)
FORMAL_COST_BPS = 5.0
SUBSTITUTE_WINDOWS = {
    "AZO": 237,
    "TLT": 160,
    "MO": 110,
    "EQT": 240,
    "WMT": 30,
    "ORLY": 260,
    "LMT": 30,
}
FORMAL_CASES = ("QQQ_CASH",) + tuple(
    f"QQQ_{symbol}{window}" for symbol, window in SUBSTITUTE_WINDOWS.items()
)
CASE_LABELS = {
    "QQQ_CASH": "QQQ择时＋现金",
    **{
        f"QQQ_{symbol}{window}": f"QQQ择时＋{symbol} / SMA{window}"
        for symbol, window in SUBSTITUTE_WINDOWS.items()
    },
}
CASE_COLORS = {
    "QQQ_CASH": "#64748b",
    "QQQ_AZO237": "#2563eb",
    "QQQ_TLT160": "#16a34a",
    "QQQ_MO110": "#f59e0b",
    "QQQ_EQT240": "#dc2626",
    "QQQ_WMT30": "#7c3aed",
    "QQQ_ORLY260": "#0891b2",
    "QQQ_LMT30": "#9333ea",
}


shared.FORMAL_CASES = FORMAL_CASES
shared.CASE_LABELS = CASE_LABELS
shared.CASE_COLORS = CASE_COLORS


def format_pct(value: float) -> str:
    return f"{float(value):+,.1f}%"


def format_pp(value: float) -> str:
    return f"{float(value):+,.1f}pp"


def summary_html(block: dict[str, Any]) -> str:
    metrics = block["parameter_results"].set_index("case_id")
    hold = block["metrics_json"]["qqq_hold_metrics"]
    spells = block["flat_spell_summary"]
    ex = spells[spells["scope"] == "post_sell_excluding_dotcom"].set_index("case_id")
    cards: list[str] = []
    for case_id in FORMAL_CASES[1:]:
        row = metrics.loc[case_id]
        event = ex.loc[case_id]
        cards.append(
            '<article class="result-card">'
            f'<strong>{html.escape(CASE_LABELS[case_id])}</strong>'
            f'<span>全账户总收益：{format_pct(row.total_return_pct)}</span>'
            f'<span>相对QQQ＋现金：{format_pp(row.excess_total_return_vs_qqq_cash_pp)}</span>'
            f'<span>最大回撤：{format_pct(row.max_drawdown_pct)}</span>'
            f'<span>剔除2000后的空仓段复合：{format_pct(event.timed_compound_return_pct)}</span>'
            '</article>'
        )
    rows: list[str] = []
    for case_id in FORMAL_CASES:
        row = metrics.loc[case_id]
        rows.append(
            '<tr>'
            f'<td>{html.escape(CASE_LABELS[case_id])}</td>'
            f'<td>{format_pct(row.total_return_pct)}</td><td>{row.cagr_pct:.2f}%</td>'
            f'<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>'
            f'<td>{row.qqq_exposure_pct:.1f}%</td>'
            f'<td>{row.substitute_exposure_pct:.1f}%</td>'
            f'<td>{row.cash_sessions_pct:.1f}%</td>'
            '</tr>'
        )
    event_rows: list[str] = []
    for case_id in FORMAL_CASES[1:]:
        item = ex.loc[case_id]
        event_rows.append(
            '<tr>'
            f'<td>{html.escape(CASE_LABELS[case_id])}</td>'
            f'<td>{int(item.spell_count)}</td>'
            f'<td>{format_pct(item.timed_compound_return_pct)}</td>'
            f'<td>{format_pct(item.unconditional_compound_return_pct)}</td>'
            f'<td>{int(item.positive_timed_spell_count)}</td>'
            f'<td>{item.median_timed_return_pct:+.2f}%</td>'
            '</tr>'
        )
    return (
        '<section class="result-intro"><h2>先看结论</h2>'
        '<p>本轮只增加候选，不改变QQQ总开关或成交规则。QQQ总开关一共产生23段卖出后的完整空仓；其中5段与2000–2002熊市重叠。正式口径为单边5bps。</p>'
        f'<div class="result-cards">{"".join(cards)}</div>'
        '<h3>全账户：QQQ持仓期＋空仓替代期</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>路径</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>QQQ</th><th>替代品</th><th>现金</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<p>同起点QQQ Buy & Hold：总收益 {format_pct(hold["total_return_pct"])}，CAGR {hold["cagr_pct"]:.2f}%，最大回撤 {hold["max_drawdown_pct"]:.2f}%。</p>'
        '<h3>只看QQQ卖出后的空仓，并剔除2000–2002重叠段</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>替代品</th><th>空仓段</th><th>按自身SMA复合</th><th>该段直接持有复合</th><th>上涨段数</th><th>中位数</th></tr></thead>'
        f'<tbody>{"".join(event_rows)}</tbody></table></div>'
        '<p><strong>解释边界：</strong>所有新增候选和窗口都来自同一完整历史样本的事后复核。这张表可以比较政策表现，不能把胜出者直接当成样本外证据。</p>'
        '</section>'
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if tuple(context.config["parameters"]["formal_cases"]) != FORMAL_CASES:
        raise AssertionError("expanded formal case order changed")
    if context.config["parameters"]["substitute_windows"] != SUBSTITUTE_WINDOWS:
        raise AssertionError("expanded substitute windows changed")
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("analysis requires a running, writable run")
    if len(record.get("expected_blocks", [])) != 2 or any(
        item["status"] != "completed" for item in record["expected_blocks"]
    ):
        raise RuntimeError("both cost blocks must be complete before analysis")
    blocks = {
        float(cost): shared.load_block(context, args.run_id, float(cost))
        for cost in context.config["cost_scenarios_bps_per_side"]
    }
    block = blocks[FORMAL_COST_BPS]
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    all_metrics = pd.concat(
        [item["parameter_results"].assign(cost_bps=cost) for cost, item in blocks.items()],
        ignore_index=True,
    )
    all_events = pd.concat(
        [item["flat_spell_summary"].assign(cost_bps=cost) for cost, item in blocks.items()],
        ignore_index=True,
    )
    all_metrics.to_csv(analysis_root / "case_summary_all_costs.csv", index=False, lineterminator="\n")
    block["parameter_results"].to_csv(
        analysis_root / "case_summary_5bps.csv", index=False, lineterminator="\n"
    )
    all_events.to_csv(
        analysis_root / "flat_spell_summary_all_costs.csv", index=False, lineterminator="\n"
    )
    block["substitute_flat_event_returns"].to_csv(
        analysis_root / "flat_spell_events_5bps.csv", index=False, lineterminator="\n"
    )
    result_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": FORMAL_COST_BPS,
        "case_metrics": json.loads(block["parameter_results"].to_json(orient="records")),
        "qqq_hold_metrics": block["metrics_json"]["qqq_hold_metrics"],
        "flat_spell_summary": json.loads(block["flat_spell_summary"].to_json(orient="records")),
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "assets and windows were selected after full-history bear-market review",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(result_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    figures = [
        ReportFigure(
            "performance-qqq_flat_substitution",
            "八条QQQ择时路径与QQQ持有（5bps）",
            shared.performance_figure(block),
            "performance",
        ),
        ReportFigure(
            "market-qqq",
            "QQQ SMA200±3%总开关与空仓高亮",
            shared.market_figure(block),
            "market",
        ),
        ReportFigure(
            "allocation-share",
            "每条路径实际持有QQQ、替代品与现金的时间",
            shared.exposure_figure(block),
            "generic",
        ),
    ]
    for symbol, window in SUBSTITUTE_WINDOWS.items():
        figures.append(
            ReportFigure(
                f"flat-events-{symbol.lower()}",
                f"{symbol} / SMA{window}：每次QQQ卖出后到重新买入前",
                shared.event_figure(block, symbol),
                "generic",
            )
        )
    notes = [
        "QQQ是唯一总开关：QQQ处于多头状态时只持有QQQ；紫色初始高亮与灰色卖出后高亮期间才允许替代品进入。",
        "七只替代品的自身SMA状态始终连续计算；QQQ空仓时指定替代品若未通过自己的3%上轨，账户保持现金。",
        "所有信号使用完成Close，下一共同交易日调整Open先卖后买；正式结果使用单边5bps，0bps只检查成本方向。",
        "逐空仓段回报只计算替代袖套自身的进出与价格变化；全账户净值还包含其余时间持有QQQ的结果，两者不能直接相加。",
        "剔除2000口径会删除任何与2000-03-24至2002-10-09主观熊市重叠的QQQ空仓段，但该区间从未参与信号。",
        "七只资产和各自窗口均来自同一全历史样本的事后筛选，报告是探索性候选比较，不是未来有效性的样本外证明。",
    ]
    report = render_interactive_report(
        title="QQQ空仓期：七只候选择时接替",
        heading="QQQ该空仓时，哪只候选比现金更有价值？",
        subtitle="QQQ SMA200±3%是总开关；七只候选各用冻结的自身SMA独立接替。",
        summary_html=summary_html(block),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.result-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.result-card{display:flex;flex-direction:column;gap:7px;border:1px solid #dbe3ec;border-radius:12px;padding:15px;background:#fff}.result-card strong{font-size:1.02rem}.result-card span{font-variant-numeric:tabular-nums;color:#334155}.event-scroll{overflow-x:auto}.event-table{width:100%;border-collapse:collapse;margin:12px 0;font-variant-numeric:tabular-nums}.event-table th,.event-table td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}.event-table th:first-child,.event-table td:first-child{text-align:left}@media(max-width:900px){.result-cards{grid-template-columns:1fr}}@media print{.result-card{break-inside:avoid}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="analysis/case_summary_5bps.csv">5bps全账户汇总</a> · '
        '<a download href="analysis/flat_spell_events_5bps.csv">逐QQQ空仓段结果</a> · '
        '<a download href="analysis/flat_spell_summary_all_costs.csv">含/不含2000与0/5bps汇总</a> · '
        '<a download href="QQQ_FLAT_SUBSTITUTION/cost_5bps/orders.csv">完整成交</a>'
        '</p></section>'
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")

    formal = block["parameter_results"].set_index("case_id")
    ex = block["flat_spell_summary"]
    ex = ex[ex["scope"] == "post_sell_excluding_dotcom"].set_index("case_id")
    lines = ["# QQQ空仓期：七只候选择时接替", "", "## 5bps摘要", ""]
    for case_id in FORMAL_CASES:
        row = formal.loc[case_id]
        lines.append(
            f"- {CASE_LABELS[case_id]}：总收益 {row.total_return_pct:+.1f}%，CAGR {row.cagr_pct:.2f}%，最大回撤 {row.max_drawdown_pct:.2f}%。"
        )
    lines.extend(["", "## 剔除2000–2002重叠空仓段", ""])
    for case_id in FORMAL_CASES[1:]:
        row = ex.loc[case_id]
        lines.append(
            f"- {CASE_LABELS[case_id]}：18段择时复合 {row.timed_compound_return_pct:+.1f}%，直接持有复合 {row.unconditional_compound_return_pct:+.1f}%。"
        )
    lines.extend([
        "",
        "## 研究边界",
        "",
        "- QQQ始终是总开关，替代品只在QQQ空仓期出现。",
        "- 资产与窗口来自同一全历史样本的事后筛选，不能视为样本外验证。",
        "- 逐段空仓回报与全账户总收益是不同口径，报告已分开呈现。",
    ])
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tracked = [
        "backtest/quantkit/qqq_flat_substitution.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_qqq_flat_trio_substitution.py",
        "backtest/scripts/analyze_qqq_flat_trio_substitution.py",
        "backtest/scripts/analyze_qqq_flat_expanded_substitution.py",
        "backtest/scripts/finalize_qqq_flat_expanded_substitution.py",
        "backtest/scripts/smoke_qqq_flat_expanded_substitution_report.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_qqq_flat_trio_substitution.py",
        "backtest/tests/strategies/tim/test_qqq_flat_expanded_substitution.py",
        "backtest/experiments/TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "software": {
            "python": platform.python_version(),
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    for relative in block["manifest"]["source_files"]:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run compares seven independently timed substitutes only while QQQ SMA200 timing is flat.

- `report.html` / `report.md`: v5 strategy-first report and concise Chinese summary.
- `report.pdf`: Chrome-printed report after the v5 print gate.
- `analysis/`: full-account and per-QQQ-flat-spell summaries, including the ex-2000 scope.
- `QQQ_FLAT_SUBSTITUTION/`: immutable 0/5 bps ledgers, decisions, positions, orders, master spells, and event returns.
- `provenance.json`: exact source-code, market-data, and bear-label hashes.
- `validation.json`: tests, audit, browser, PDF, reconciliation, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with ten interactive figures")


if __name__ == "__main__":
    main()
