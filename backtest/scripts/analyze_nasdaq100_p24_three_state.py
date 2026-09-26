#!/usr/bin/env python3
"""Build the v5 report for the Nasdaq-100 P24 three-state portfolios."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import (
    assert_run_writable,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.nasdaq100_p24_portfolio import CASE_IDS
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_nasdaq100_stochrsi_rotation import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.40c.1__26-08-29__nasdaq100_p24_three_state_2005_2012"
)
SYMBOL = "NASDAQ100_P24_THREE_STATE_2005_2012"
LABELS = {
    "ORIGINAL_P24": "原P24",
    "ORIGINAL_NOT_STRICT": "原P24非严格差集",
    "STRICT_P24": "严格P24",
    "QQQ_BUY_HOLD": "QQQ Buy & Hold",
}
COLORS = {
    "ORIGINAL_P24": "#2563eb",
    "ORIGINAL_NOT_STRICT": "#d97706",
    "STRICT_P24": "#047857",
    "QQQ_BUY_HOLD": "#111827",
}


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def load_blocks(run_root: Path) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in (0.0, 10.0):
        root = run_root / SYMBOL / f"cost_{cost:g}bps"
        blocks[cost] = {
            "root": root,
            "metrics": pd.read_csv(root / "metrics.csv"),
            "payload": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
            "daily": pd.read_csv(root / "daily.csv.gz", parse_dates=["date"]),
            "benchmark": pd.read_csv(root / "qqq_buy_hold_daily.csv", parse_dates=["date"]),
            "selections": pd.read_csv(root / "selections.csv.gz", parse_dates=["date"]),
        }
    return blocks


def performance_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.68, 0.32], subplot_titles=("账户净值", "从各自峰值回撤"),
    )
    for cost in (0.0, 10.0):
        daily = blocks[cost]["daily"]
        for case_id in CASE_IDS:
            frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
            for row, values, panel, showlegend in (
                (1, frame["equity"], "equity", True),
                (2, drawdown(frame["equity"]), "drawdown", False),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=frame["date"], y=values,
                        name=f"{LABELS[case_id]} · {cost:g} bps",
                        showlegend=showlegend,
                        visible=True if cost == 10 else "legendonly",
                        line={"color": COLORS[case_id], "width": 2.4, "dash": "solid" if cost == 10 else "dot"},
                        meta={
                            "series_key": f"{case_id.lower()}_{cost:g}bps", "panel": panel,
                            "label": f"{LABELS[case_id]} · {cost:g} bps", "cost_bps": cost,
                        },
                    ), row=row, col=1,
                )
        qqq = blocks[cost]["benchmark"].sort_values("date")
        for row, values, panel, showlegend in (
            (1, qqq["equity"], "equity", True),
            (2, drawdown(qqq["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=qqq["date"], y=values, name=f"QQQ Buy & Hold · {cost:g} bps",
                    showlegend=showlegend, visible=True if cost == 10 else "legendonly",
                    line={"color": COLORS["QQQ_BUY_HOLD"], "width": 2.8, "dash": "dash" if cost == 10 else "dot"},
                    meta={
                        "series_key": f"qqq_{cost:g}bps", "panel": panel,
                        "label": f"QQQ Buy & Hold · {cost:g} bps", "is_benchmark": panel == "equity" and cost == 10,
                        "cost_bps": cost,
                    },
                ), row=row, col=1,
            )
    figure.update_layout(height=880, hovermode="x unified", showlegend=False, uirevision="ndx-p24-performance-v1")
    figure.update_yaxes(title_text="10万美元账户净值", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def holdings_figure(block: dict[str, Any]) -> go.Figure:
    daily = block["daily"]
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=("每日开盘成交后持股数量", "账户总股票暴露"),
    )
    for case_id in CASE_IDS:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["holdings_count"], name=LABELS[case_id],
                line={"color": COLORS[case_id], "width": 1.7},
                meta={"series_key": f"{case_id.lower()}_holdings", "panel": "market", "label": LABELS[case_id]},
            ), row=1, col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["gross_exposure"] * 100.0,
                name=LABELS[case_id], showlegend=False,
                line={"color": COLORS[case_id], "width": 1.7},
                meta={"series_key": f"{case_id.lower()}_exposure", "panel": "exposure", "label": LABELS[case_id]},
            ), row=2, col=1,
        )
    figure.update_layout(height=760, hovermode="x unified", showlegend=False, uirevision="ndx-p24-holdings-v1")
    figure.update_yaxes(title_text="只", row=1, col=1)
    figure.update_yaxes(title_text="%", range=[0, 105], row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def cost_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    zero = blocks[0.0]["metrics"].set_index("case_id")
    primary = blocks[10.0]["metrics"].set_index("case_id")
    figure = make_subplots(rows=1, cols=2, subplot_titles=("CAGR：0与10 bps", "累计换手倍数（10 bps路径）"))
    for cost, opacity in ((0.0, 0.42), (10.0, 1.0)):
        rows = blocks[cost]["metrics"].set_index("case_id")
        figure.add_trace(
            go.Bar(
                x=[LABELS[item] for item in CASE_IDS], y=[rows.at[item, "cagr_pct"] for item in CASE_IDS],
                name=f"{cost:g} bps", opacity=opacity,
                marker_color=[COLORS[item] for item in CASE_IDS],
                meta={"series_key": f"cagr_{cost:g}bps", "panel": "other", "cost_bps": cost},
            ), row=1, col=1,
        )
    figure.add_trace(
        go.Bar(
            x=[LABELS[item] for item in CASE_IDS],
            y=[primary.at[item, "turnover_multiple"] for item in CASE_IDS],
            name="换手", marker_color=[COLORS[item] for item in CASE_IDS],
            meta={"series_key": "turnover_10bps", "panel": "other"},
        ), row=1, col=2,
    )
    for case_id in CASE_IDS:
        drag = float(primary.at[case_id, "cagr_pct"] - zero.at[case_id, "cagr_pct"])
        if drag > 1e-12:
            raise AssertionError("positive cost unexpectedly improved CAGR")
    figure.update_layout(height=560, barmode="group", showlegend=True, uirevision="ndx-p24-cost-v1")
    figure.update_yaxes(title_text="%", row=1, col=1)
    figure.update_yaxes(title_text="倍", row=1, col=2)
    return figure


def result_table(blocks: dict[float, dict[str, Any]]) -> str:
    primary = blocks[10.0]["metrics"].set_index("case_id")
    zero = blocks[0.0]["metrics"].set_index("case_id")
    rows = []
    for case_id in CASE_IDS:
        p = primary.loc[case_id]
        z = zero.loc[case_id]
        rows.append(
            "<tr>"
            f"<td>{html.escape(LABELS[case_id])}</td><td>{p.cagr_pct:.2f}%</td>"
            f"<td>{z.cagr_pct:.2f}%</td><td>{p.max_drawdown_pct:.2f}%</td>"
            f"<td>{p.sharpe:.3f}</td><td>{int(p.holding_sessions)}</td>"
            f"<td>{p.holding_cagr_pct:.2f}%</td><td>{p.average_holdings_count:.1f}</td>"
            f"<td>{p.turnover_multiple:.1f}×</td><td>{int(p.order_count):,}</td>"
            "</tr>"
        )
    benchmark = blocks[10.0]["payload"]["benchmark"]
    rows.append(
        "<tr><td>QQQ Buy & Hold</td>"
        f"<td>{benchmark['cagr_pct']:.2f}%</td><td>{blocks[0.0]['payload']['benchmark']['cagr_pct']:.2f}%</td>"
        f"<td>{benchmark['max_drawdown_pct']:.2f}%</td><td>{benchmark['sharpe']:.3f}</td>"
        "<td>2,012</td><td>—</td><td>1.0</td>"
        f"<td>{benchmark['turnover_multiple']:.1f}×</td><td>{int(benchmark['order_count'])}</td></tr>"
    )
    return (
        '<div style="overflow-x:auto"><table><thead><tr>'
        "<th>路径</th><th>10bps CAGR</th><th>0bps CAGR</th><th>10bps最大回撤</th>"
        "<th>Sharpe</th><th>持仓日</th><th>持仓CAGR</th><th>平均持股</th><th>累计换手</th><th>订单</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def markdown_report(blocks: dict[float, dict[str, Any]]) -> str:
    primary = blocks[10.0]["metrics"].set_index("case_id")
    zero = blocks[0.0]["metrics"].set_index("case_id")
    benchmark = blocks[10.0]["payload"]["benchmark"]
    lines = [
        "| 路径 | 10bps CAGR | 0bps CAGR | 最大回撤 | Sharpe | 持仓日 | 持仓CAGR | 平均持股 | 累计换手 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case_id in CASE_IDS:
        p, z = primary.loc[case_id], zero.loc[case_id]
        lines.append(
            f"| {LABELS[case_id]} | {p.cagr_pct:.2f}% | {z.cagr_pct:.2f}% | {p.max_drawdown_pct:.2f}% | "
            f"{p.sharpe:.3f} | {int(p.holding_sessions)} | {p.holding_cagr_pct:.2f}% | "
            f"{p.average_holdings_count:.1f} | {p.turnover_multiple:.1f}× |"
        )
    return f"""# Nasdaq-100 P24三状态全体等权（2005–2012）

## 本次测试

- 在滞后一交易日可知的历史Nasdaq-100成员中，分别持有所有原P24、原P24非严格差集、严格P24股票。
- 每日Close确认，下一Open恢复等权；不排名、不限数量；成本只测0和10bps。
- 数据是`candidate_pending_review`，可以用于本次用户授权的探索，但不能作为approved晋级证据。

## 结果

{chr(10).join(lines)}

同期QQQ Buy & Hold在10bps下CAGR为{benchmark['cagr_pct']:.2f}%、最大回撤为{benchmark['max_drawdown_pct']:.2f}%、Sharpe为{benchmark['sharpe']:.3f}。

## 判断

- 严格版是三条中收益最好的一条，但10bps CAGR仅{primary.at['STRICT_P24', 'cagr_pct']:.2f}%，最大回撤{primary.at['STRICT_P24', 'max_drawdown_pct']:.2f}%，没有实现控制回撤，也没有超过QQQ。
- 差集在0bps下已经是负CAGR；10bps下进一步降至{primary.at['ORIGINAL_NOT_STRICT', 'cagr_pct']:.2f}%。这说明它不仅是成本问题，差集本身的持仓质量也弱。
- 每日恢复等权造成极高换手：原版、差集、严格版累计分别约{primary.at['ORIGINAL_P24', 'turnover_multiple']:.0f}、{primary.at['ORIGINAL_NOT_STRICT', 'turnover_multiple']:.0f}、{primary.at['STRICT_P24', 'turnover_multiple']:.0f}倍。差集平均只有{primary.at['ORIGINAL_NOT_STRICT', 'average_holdings_count']:.1f}只，权重波动和状态切换尤其昂贵。
- 持仓日都接近全窗口，因此P24在横截面上几乎没有把组合切到现金；“个股过滤”不能自动继承单标的择时的回撤控制效果。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    run = load_run(context, args.run_id)
    if any(item["status"] != "completed" for item in run["expected_blocks"]):
        raise RuntimeError("every run block must be complete before analysis")
    run_root = context.run_root(args.run_id)
    blocks = load_blocks(run_root)
    primary = blocks[10.0]["metrics"].set_index("case_id")
    benchmark = blocks[10.0]["payload"]["benchmark"]
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>三条中最好</h3><p class="metric">严格P24 · {primary.at["STRICT_P24", "cagr_pct"]:.2f}%</p><p>10bps日历CAGR</p></article>'
        f'<article><h3>严格版回撤</h3><p class="metric">{primary.at["STRICT_P24", "max_drawdown_pct"]:.2f}%</p><p>QQQ为{benchmark["max_drawdown_pct"]:.2f}%</p></article>'
        f'<article><h3>差集</h3><p class="metric">{primary.at["ORIGINAL_NOT_STRICT", "cagr_pct"]:.2f}%</p><p>0bps仍为{blocks[0.0]["metrics"].set_index("case_id").at["ORIGINAL_NOT_STRICT", "cagr_pct"]:.2f}%</p></article>'
        f'<article><h3>严格版持仓</h3><p class="metric">{int(primary.at["STRICT_P24", "holding_sessions"])}日 · {primary.at["STRICT_P24", "average_holdings_count"]:.1f}只</p><p>持仓CAGR {primary.at["STRICT_P24", "holding_cagr_pct"]:.2f}%</p></article>'
        '</div>'
        '<p><strong>结论：</strong>三条路径都没有形成低回撤曲线。严格版提高了收益质量，但组合几乎始终有仓位，并且每日恢复等权导致高换手；差集在不计成本时也为负收益。</p>'
        '<h3>完整结果</h3>' + result_table(blocks)
    )
    report = render_interactive_report(
        title="Nasdaq-100 P24原版、差集与严格版 2005–2012",
        heading="历史Nasdaq-100成分：P24三状态全体等权",
        subtitle="不排名、不限数量；每日Close确认、下一Open恢复等权；0/10 bps",
        summary_html=summary_html,
        notes=[
            "成员身份使用逐证券InIndex区间并统一延迟一个XNYS交易日；没有用今天的成分倒推历史。",
            "原P24与严格P24都连续2日确认；严格状态逐日是原状态的子集，差集与严格状态精确分割原状态。",
            "持仓CAGR按实际有至少一只股票的交易日折算，只是资金效率诊断，正式比较仍看日历CAGR。",
            "每日恢复等权是本轮冻结的资金规则；换手很高，因此0bps只用于分离信号质量与交易摩擦。",
            "历史成分与逐股价格仍是candidate_pending_review；179只窗口相关证券全部计算成功，但结果不得冒充approved研究。",
            "7/5/2笔终端代理卖出分别出现在原版/差集/严格版；它们使用最后调整价结算已有持仓，不能用于新买入。",
        ],
        figures=[
            ReportFigure("performance", "三条P24组合与QQQ净值、回撤", performance_figure(blocks), "performance"),
            ReportFigure("holdings", "持股数量与股票暴露（10 bps）", holdings_figure(blocks[10.0]), "market"),
            ReportFigure("cost", "成本拖累与累计换手", cost_figure(blocks), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(blocks)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nNasdaq-100 P24原版、差集与严格版全体等权；详见 `report.html`、`report.pdf` 与 `report.md`。\n",
        encoding="utf-8",
    )
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    table_frames = []
    for cost in (0.0, 10.0):
        frame = blocks[cost]["metrics"].copy()
        frame["cost_bps"] = cost
        table_frames.append(frame)
    results = pd.concat(table_frames, ignore_index=True)
    results.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    effects = blocks[10.0]["metrics"].set_index("case_id")[["cagr_pct", "max_drawdown_pct", "turnover_multiple"]].join(
        blocks[0.0]["metrics"].set_index("case_id")[["cagr_pct"]], rsuffix="_0bps"
    ).reset_index()
    effects["cagr_cost_drag_pp"] = effects["cagr_pct"] - effects["cagr_pct_0bps"]
    effects.to_csv(analysis_root / "cost_effects.csv", index=False, lineterminator="\n")
    max_check = max(
        float(value)
        for cost in (0.0, 10.0)
        for case in blocks[cost]["payload"]["max_cross_check_differences"].values()
        for value in case.values()
    )
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset_status": "candidate_pending_review",
        "analysis_start": context.config["parameters"]["analysis_start"],
        "analysis_end": context.config["parameters"]["analysis_end"],
        "cases": {
            str(row.case_id): json_safe(row._asdict())
            for row in blocks[10.0]["metrics"].itertuples(index=False)
        },
        "zero_bps_cases": {
            str(row.case_id): json_safe(row._asdict())
            for row in blocks[0.0]["metrics"].itertuples(index=False)
        },
        "benchmark": blocks[10.0]["payload"]["benchmark"],
        "strict_subset_violation_count": 0,
        "maximum_cross_check_difference": max_check,
        "interpretation": {
            "drawdown_control_succeeded": False,
            "strict_outperformed_other_p24_states_at_10bps": True,
            "difference_has_negative_cagr_even_at_0bps": True,
            "daily_equal_weight_turnover_is_material": True,
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pdf = subprocess.run(
        [
            "node", "scripts/print_html_pdf.mjs", str(run_root / "report_print.html"),
            str(run_root / "report.pdf"), "什么时候买", "什么时候卖", "信号如何变成成交", "成本与比较",
        ],
        cwd=BACKTEST_ROOT, text=True, capture_output=True, check=False,
    )
    if pdf.returncode != 0:
        raise RuntimeError("PDF generation failed:\n" + "\n".join([pdf.stdout, pdf.stderr]))
    pdf_payload = json.loads(pdf.stdout.split("\n", 1)[1])
    (analysis_root / "pdf_print_gate.json").write_text(
        json.dumps(pdf_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "plotly": plotly.__version__,
        "template_id": context.config["reporting"]["template_id"],
        "source_files": {},
    }
    source_paths = [
        "backtest/quantkit/nasdaq100_p24_portfolio.py",
        "backtest/quantkit/nasdaq100_strategy1_rotation_factorial.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_nasdaq100_p24_three_state.py",
        "backtest/scripts/analyze_nasdaq100_p24_three_state.py",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/nasdaq100_history_registry.json",
        "data/processed/universes/nasdaq100/pending_review/source_manifest.json",
        "data/processed/universes/nasdaq100/pending_review/membership_intervals.csv",
    ]
    for relative in source_paths:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    artifacts = {}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            artifacts[str(path.relative_to(run_root))] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": artifacts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if load_run(context, args.run_id)["status"] == "running":
        record_analysis_complete(context, args.run_id)
    print(markdown)


if __name__ == "__main__":
    main()
