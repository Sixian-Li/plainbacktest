#!/usr/bin/env python3
"""Analyze and render the fixed QQQ-flat trio substitution study."""

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
import plotly.graph_objects as go

from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_qqq_flat_trio_substitution import FORMAL_CASES, FORMAL_SYMBOL


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution"
)
FORMAL_COST_BPS = 5.0
CASE_LABELS = {
    "QQQ_CASH": "QQQ择时＋现金",
    "QQQ_AZO237": "QQQ择时＋AZO / SMA237",
    "QQQ_TLT160": "QQQ择时＋TLT / SMA160",
    "QQQ_MO110": "QQQ择时＋MO / SMA110",
}
CASE_COLORS = {
    "QQQ_CASH": "#64748b",
    "QQQ_AZO237": "#2563eb",
    "QQQ_TLT160": "#16a34a",
    "QQQ_MO110": "#f59e0b",
}


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in frame.columns:
        if column == "date" or column.endswith("_date"):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def load_block(context: Any, run_id: str, cost: float) -> dict[str, Any]:
    root = block_root(context, run_id, FORMAL_SYMBOL, cost)
    if not (root / "manifest.json").is_file():
        raise FileNotFoundError(f"missing completed block: {root}")
    names = (
        "parameter_results",
        "daily",
        "positions",
        "orders",
        "decisions",
        "master_state",
        "master_flat_spells",
        "substitute_flat_event_returns",
        "flat_spell_summary",
        "qqq_hold_daily",
    )
    result = {name: read_csv(root / f"{name}.csv") for name in names}
    result["root"] = root
    result["metrics_json"] = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    result["manifest"] = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return result


def performance_figure(block: dict[str, Any]) -> go.Figure:
    figure = go.Figure()
    daily = block["daily"]
    for case_id in FORMAL_CASES:
        selected = daily[daily["case_id"] == case_id]
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["equity"],
                name=CASE_LABELS[case_id],
                line={"color": CASE_COLORS[case_id], "width": 2.2 if case_id != "QQQ_CASH" else 1.7},
                meta={
                    "series_key": case_id.lower(),
                    "label": CASE_LABELS[case_id],
                    "panel": "equity",
                    "cost_bps": FORMAL_COST_BPS,
                },
            )
        )
    hold = block["qqq_hold_daily"]
    figure.add_trace(
        go.Scatter(
            x=hold["date"],
            y=hold["equity"],
            name="QQQ Buy & Hold",
            line={"color": "#111827", "width": 1.6, "dash": "dash"},
            meta={
                "series_key": "qqq_hold",
                "label": "QQQ Buy & Hold",
                "panel": "equity",
                "is_benchmark": True,
                "cost_bps": FORMAL_COST_BPS,
            },
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=520,
        hovermode="x unified",
        yaxis={"title": "账户净值（美元，对数）", "type": "log"},
        xaxis={"title": "日期", "rangeslider": {"visible": False}},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 75, "r": 25, "t": 60, "b": 55},
    )
    return figure


def market_figure(block: dict[str, Any]) -> go.Figure:
    state = block["master_state"]
    orders = block["orders"]
    orders = orders[(orders["case_id"] == "QQQ_CASH") & (orders["symbol"] == "QQQ")]
    spells = block["master_flat_spells"]
    figure = go.Figure()
    market_meta = {"panel": "market", "control_group": "qqq_master", "control_group_label": "QQQ总开关"}
    figure.add_trace(
        go.Candlestick(
            x=state["date"], open=state["open"], high=state["high"], low=state["low"], close=state["close"],
            name="QQQ复权OHLC",
            increasing={"line": {"color": "#16a34a"}},
            decreasing={"line": {"color": "#dc2626"}},
            meta={**market_meta, "series_key": "qqq_ohlc", "label": "QQQ复权OHLC"},
        )
    )
    for column, label, color in (
        ("sma200", "SMA200", "#f59e0b"),
        ("upper_rail", "上轨 +3%", "#16a34a"),
        ("lower_rail", "下轨 -3%", "#dc2626"),
    ):
        figure.add_trace(
            go.Scatter(
                x=state["date"], y=state[column], name=label,
                line={"color": color, "width": 1.4 if column == "sma200" else 1.0},
                meta={**market_meta, "series_key": f"qqq_{column}", "label": label},
            )
        )
    for side, label, color, shape in (
        ("buy", "QQQ买入", "#16a34a", "triangle-up"),
        ("sell", "QQQ卖出", "#dc2626", "triangle-down"),
    ):
        selected = orders[orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=selected["date"], y=selected["raw_price"], mode="markers", name=label,
                marker={"color": color, "size": 8, "symbol": shape},
                meta={**market_meta, "series_key": f"qqq_{side}", "label": label},
            )
        )
    for spell in spells.itertuples(index=False):
        color = "rgba(147,51,234,0.13)" if spell.spell_type == "initial_wait" else "rgba(148,163,184,0.15)"
        figure.add_vrect(x0=spell.start_fill_date, x1=spell.mark_date, fillcolor=color, line_width=0, layer="below")
    figure.update_layout(
        template="plotly_white", height=570, hovermode="x unified",
        yaxis={"title": "QQQ复权价格"},
        xaxis={
            "title": "日期", "rangeslider": {"visible": False},
            "rangeselector": {"buttons": [
                {"count": 1, "label": "1年", "step": "year", "stepmode": "backward"},
                {"count": 5, "label": "5年", "step": "year", "stepmode": "backward"},
                {"count": 10, "label": "10年", "step": "year", "stepmode": "backward"},
                {"step": "all", "label": "全部"},
            ]},
        },
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 70, "r": 25, "t": 55, "b": 55},
    )
    return figure


def exposure_figure(block: dict[str, Any]) -> go.Figure:
    metrics = block["parameter_results"].set_index("case_id")
    figure = go.Figure()
    for column, label, color in (
        ("qqq_exposure_pct", "持有QQQ", "#2563eb"),
        ("substitute_exposure_pct", "持有替代品", "#16a34a"),
        ("cash_sessions_pct", "现金", "#cbd5e1"),
    ):
        figure.add_trace(
            go.Bar(
                x=[CASE_LABELS[case] for case in FORMAL_CASES],
                y=[float(metrics.at[case, column]) for case in FORMAL_CASES],
                name=label,
                marker_color=color,
                meta={"series_key": column, "label": label},
            )
        )
    figure.update_layout(
        template="plotly_white", height=430, barmode="stack",
        yaxis={"title": "交易日占比", "range": [0, 100], "ticksuffix": "%"},
        xaxis={"title": "账户路径"}, legend={"orientation": "h", "y": 1.1},
        margin={"l": 65, "r": 25, "t": 45, "b": 80},
    )
    return figure


def event_figure(block: dict[str, Any], symbol: str) -> go.Figure:
    events = block["substitute_flat_event_returns"]
    events = events[(events["substitute_symbol"] == symbol) & (events["spell_type"] == "post_sell")].copy()
    labels = events["start_fill_date"].dt.strftime("%Y-%m-%d")
    colors = ["#7c3aed" if value else "#2563eb" for value in events["overlaps_dotcom_bear"]]
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=labels, y=events["timed_return_pct"], name="按自身SMA择时",
            marker_color=colors,
            customdata=np.column_stack([events["mark_date"].dt.strftime("%Y-%m-%d"), events["timed_invested_sessions"]]),
            hovertemplate="开始 %{x}<br>结束 %{customdata[0]}<br>择时回报 %{y:.2f}%<br>持有交易日 %{customdata[1]}<extra></extra>",
            meta={"series_key": f"{symbol.lower()}_timed_events", "label": "按自身SMA择时"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=labels, y=events["unconditional_return_pct"], name="空仓期直接持有",
            mode="lines+markers", line={"color": "#111827", "width": 1.6, "dash": "dash"},
            marker={"size": 6},
            meta={"series_key": f"{symbol.lower()}_unconditional_events", "label": "空仓期直接持有"},
        )
    )
    figure.add_hline(y=0, line_color="#64748b", line_width=1)
    figure.update_layout(
        template="plotly_white", height=450, hovermode="x unified",
        yaxis={"title": "该段替代仓回报", "ticksuffix": "%"},
        xaxis={"title": "QQQ卖出后的空仓开始日", "tickangle": -45},
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 70, "r": 25, "t": 50, "b": 105},
    )
    return figure


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
    for case_id in ("QQQ_AZO237", "QQQ_TLT160", "QQQ_MO110"):
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
    rows = []
    for case_id in FORMAL_CASES:
        row = metrics.loc[case_id]
        rows.append(
            '<tr>'
            f'<td>{html.escape(CASE_LABELS[case_id])}</td>'
            f'<td>{format_pct(row.total_return_pct)}</td><td>{row.cagr_pct:.2f}%</td>'
            f'<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>'
            f'<td>{row.qqq_exposure_pct:.1f}%</td><td>{row.substitute_exposure_pct:.1f}%</td><td>{row.cash_sessions_pct:.1f}%</td>'
            '</tr>'
        )
    event_rows = []
    for item in ex.itertuples():
        event_rows.append(
            '<tr>'
            f'<td>{html.escape(CASE_LABELS[item.Index])}</td><td>{int(item.spell_count)}</td>'
            f'<td>{format_pct(item.timed_compound_return_pct)}</td>'
            f'<td>{format_pct(item.unconditional_compound_return_pct)}</td>'
            f'<td>{int(item.positive_timed_spell_count)}</td><td>{item.median_timed_return_pct:+.2f}%</td>'
            '</tr>'
        )
    return (
        '<section class="result-intro"><h2>先看结论</h2>'
        '<p>QQQ总开关一共产生23段卖出后的完整空仓；其中5段与2000–2002熊市重叠。'
        '下面把全账户长期结果和仅在QQQ空仓内发生的替代仓回报分开，正式口径为单边5bps。</p>'
        f'<div class="result-cards">{"".join(cards)}</div>'
        '<h3>全账户：QQQ持仓期＋空仓替代期</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>路径</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>QQQ</th><th>替代品</th><th>现金</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<p>同起点QQQ Buy & Hold：总收益 {format_pct(hold["total_return_pct"])}，CAGR {hold["cagr_pct"]:.2f}%，最大回撤 {hold["max_drawdown_pct"]:.2f}%。</p>'
        '<h3>只看QQQ卖出后的空仓，并剔除2000–2002重叠段</h3>'
        '<div class="event-scroll"><table class="event-table"><thead><tr><th>替代品</th><th>空仓段</th><th>按自身SMA复合</th><th>该段直接持有复合</th><th>上涨段数</th><th>中位数</th></tr></thead>'
        f'<tbody>{"".join(event_rows)}</tbody></table></div>'
        '<p><strong>关键区别：</strong>TLT的自身SMA过滤在剔除2000后优于空仓期直接持有；AZO和MO虽然替代本身非常赚钱，但它们的自身SMA过滤反而删掉了一部分收益。这个实验支持“拿它们接替现金”，不等于三个自身均线都同样有效。</p>'
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
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("analysis requires a running, writable run")
    if len(record.get("expected_blocks", [])) != 2 or any(item["status"] != "completed" for item in record["expected_blocks"]):
        raise RuntimeError("both cost blocks must be complete before analysis")
    blocks = {float(cost): load_block(context, args.run_id, float(cost)) for cost in context.config["cost_scenarios_bps_per_side"]}
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
    block["parameter_results"].to_csv(analysis_root / "case_summary_5bps.csv", index=False, lineterminator="\n")
    all_events.to_csv(analysis_root / "flat_spell_summary_all_costs.csv", index=False, lineterminator="\n")
    block["substitute_flat_event_returns"].to_csv(analysis_root / "flat_spell_events_5bps.csv", index=False, lineterminator="\n")
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
        ReportFigure("performance-qqq_flat_substitution", "四条QQQ择时路径与QQQ持有（5bps）", performance_figure(block), "performance"),
        ReportFigure("market-qqq", "QQQ SMA200±3%总开关与空仓高亮", market_figure(block), "market"),
        ReportFigure("allocation-share", "每条路径实际持有QQQ、替代品与现金的时间", exposure_figure(block), "generic"),
    ]
    for symbol, window in (("AZO", 237), ("TLT", 160), ("MO", 110)):
        figures.append(
            ReportFigure(
                f"flat-events-{symbol.lower()}",
                f"{symbol} / SMA{window}：每次QQQ卖出后到重新买入前",
                event_figure(block, symbol),
                "generic",
            )
        )
    notes = [
        "QQQ是唯一总开关：QQQ处于多头状态时只持有QQQ；紫色初始高亮与灰色卖出后高亮期间才允许替代品进入。",
        "AZO/SMA237、TLT/SMA160、MO/SMA110的状态始终连续计算；QQQ空仓时替代品若未通过自己的3%上轨，账户保持现金。",
        "所有信号使用完成Close，下一共同交易日调整Open先卖后买；正式结果使用单边5bps，0bps只检查成本方向。",
        "逐空仓段回报只计算替代袖套自身的进出与价格变化；全账户净值还包含其余时间持有QQQ的结果，两者不能直接相加。",
        "剔除2000口径会删除任何与2000-03-24至2002-10-09主观熊市重叠的QQQ空仓段，但该区间从未参与信号。",
        "三只资产和237/160/110窗口均来自同一全历史样本的事后筛选，报告是探索性政策归因，不是未来有效性的样本外证明。",
    ]
    report = render_interactive_report(
        title="QQQ空仓期：AZO、TLT、MO择时接替",
        heading="QQQ该空仓时，持有现金还是换入防守标的？",
        subtitle="QQQ SMA200±3%是总开关；AZO/SMA237、TLT/SMA160、MO/SMA110分别接替。",
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
    lines = ["# QQQ空仓期：AZO、TLT、MO择时接替", "", "## 5bps摘要", ""]
    for case_id in FORMAL_CASES:
        row = formal.loc[case_id]
        lines.append(f"- {CASE_LABELS[case_id]}：总收益 {row.total_return_pct:+.1f}%，CAGR {row.cagr_pct:.2f}%，最大回撤 {row.max_drawdown_pct:.2f}%。")
    lines.extend(["", "## 剔除2000–2002重叠空仓段", ""])
    for case_id in ("QQQ_AZO237", "QQQ_TLT160", "QQQ_MO110"):
        row = ex.loc[case_id]
        lines.append(f"- {CASE_LABELS[case_id]}：18段择时复合 {row.timed_compound_return_pct:+.1f}%，直接持有复合 {row.unconditional_compound_return_pct:+.1f}%。")
    lines.extend([
        "", "## 研究边界", "",
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
        "backtest/scripts/finalize_qqq_flat_trio_substitution.py",
        "backtest/scripts/smoke_qqq_flat_trio_substitution_report.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_qqq_flat_trio_substitution.py",
        "backtest/experiments/TIM/TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json",
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
        "software": {"python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for relative in block["manifest"]["source_files"]:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run tests three independently timed substitutes only while QQQ SMA200 timing is flat.

- `report.html` / `report.md`: v5 strategy-first report and concise Chinese summary.
- `report.pdf`: Chrome-printed report after the v5 print gate.
- `analysis/`: full-account and per-QQQ-flat-spell summaries, including the ex-2000 scope.
- `QQQ_FLAT_SUBSTITUTION/`: immutable 0/5 bps ledgers, decisions, positions, orders, master spells, and event returns.
- `provenance.json`: exact source-code, market-data, and bear-label hashes.
- `validation.json`: tests, audit, browser, PDF, reconciliation, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with six interactive figures")


if __name__ == "__main__":
    main()
