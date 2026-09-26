#!/usr/bin/env python3
"""Build the v5 report for the Nasdaq-100 P24 holding/rebalance factorial."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from quantkit.nasdaq100_p24_portfolio import (
    FACTORIAL_CASES,
    STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
    STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL,
    STRICT_LEVEL_CHANGE_EQUAL,
    STRICT_LEVEL_DAILY_EQUAL,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_nasdaq100_stochrsi_rotation import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.40d.1__26-08-29__nasdaq100_p24_hysteresis_rebalance_ablation"
)
SYMBOL = "NASDAQ100_P24_HYSTERESIS_REBALANCE_2005_2012"
CASE_IDS = tuple(FACTORIAL_CASES)
LABELS = {
    STRICT_LEVEL_DAILY_EQUAL: "严格持有 · 每日等权",
    STRICT_LEVEL_CHANGE_EQUAL: "严格持有 · 名单变化等权",
    STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL: "严格买入/原版退出 · 每日等权",
    STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL: "严格买入/原版退出 · 名单变化等权",
    "QQQ_BUY_HOLD": "QQQ Buy & Hold",
}
COLORS = {
    STRICT_LEVEL_DAILY_EQUAL: "#2563eb",
    STRICT_LEVEL_CHANGE_EQUAL: "#0891b2",
    STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL: "#d97706",
    STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL: "#047857",
    "QQQ_BUY_HOLD": "#111827",
}
EFFECT_PAIRS = (
    ("严格持有：停止名单不变日调仓", STRICT_LEVEL_DAILY_EQUAL, STRICT_LEVEL_CHANGE_EQUAL),
    (
        "滞回持有：停止名单不变日调仓",
        STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL,
        STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
    ),
    (
        "每日等权：加入严格买入/原版退出",
        STRICT_LEVEL_DAILY_EQUAL,
        STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL,
    ),
    (
        "名单变化等权：加入严格买入/原版退出",
        STRICT_LEVEL_CHANGE_EQUAL,
        STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL,
    ),
)


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
        }
    return blocks


def factor_effects(primary: pd.DataFrame) -> pd.DataFrame:
    indexed = primary.set_index("case_id")
    rows = []
    for label, base, changed in EFFECT_PAIRS:
        before = indexed.loc[base]
        after = indexed.loc[changed]
        turnover_before = float(before.turnover_multiple)
        rows.append({
            "effect": label,
            "base_case_id": base,
            "changed_case_id": changed,
            "cagr_change_pp": float(after.cagr_pct - before.cagr_pct),
            "max_drawdown_change_pp": float(after.max_drawdown_pct - before.max_drawdown_pct),
            "sharpe_change": float(after.sharpe - before.sharpe),
            "turnover_change_multiple": float(after.turnover_multiple - before.turnover_multiple),
            "turnover_reduction_pct": (
                float((turnover_before - after.turnover_multiple) / turnover_before * 100.0)
                if turnover_before > 0
                else 0.0
            ),
            "order_count_change": int(after.order_count - before.order_count),
        })
    return pd.DataFrame(rows)


def performance_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("账户净值", "从各自峰值回撤"),
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
                        x=frame["date"],
                        y=values,
                        name=f"{LABELS[case_id]} · {cost:g} bps",
                        showlegend=showlegend,
                        visible=True if cost == 10 else "legendonly",
                        line={
                            "color": COLORS[case_id],
                            "width": 2.2,
                            "dash": "solid" if cost == 10 else "dot",
                        },
                        meta={
                            "series_key": f"{case_id.lower()}_{cost:g}bps",
                            "panel": panel,
                            "label": f"{LABELS[case_id]} · {cost:g} bps",
                            "cost_bps": cost,
                        },
                    ),
                    row=row,
                    col=1,
                )
        qqq = blocks[cost]["benchmark"].sort_values("date")
        for row, values, panel, showlegend in (
            (1, qqq["equity"], "equity", True),
            (2, drawdown(qqq["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=qqq["date"],
                    y=values,
                    name=f"QQQ Buy & Hold · {cost:g} bps",
                    showlegend=showlegend,
                    visible=True if cost == 10 else "legendonly",
                    line={
                        "color": COLORS["QQQ_BUY_HOLD"],
                        "width": 2.8,
                        "dash": "dash" if cost == 10 else "dot",
                    },
                    meta={
                        "series_key": f"qqq_{cost:g}bps",
                        "panel": panel,
                        "label": f"QQQ Buy & Hold · {cost:g} bps",
                        "is_benchmark": panel == "equity" and cost == 10,
                        "cost_bps": cost,
                    },
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=900,
        hovermode="x unified",
        showlegend=False,
        uirevision="ndx-p24-factorial-performance-v1",
    )
    figure.update_yaxes(title_text="10万美元账户净值", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def holdings_figure(block: dict[str, Any]) -> go.Figure:
    daily = block["daily"]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("每日开盘成交后持股数量", "账户总股票暴露"),
    )
    for case_id in CASE_IDS:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["holdings_count"],
                name=LABELS[case_id],
                line={"color": COLORS[case_id], "width": 1.7},
                meta={
                    "series_key": f"{case_id.lower()}_holdings",
                    "panel": "market",
                    "label": LABELS[case_id],
                },
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["gross_exposure"] * 100.0,
                name=LABELS[case_id],
                showlegend=False,
                line={"color": COLORS[case_id], "width": 1.7},
                meta={
                    "series_key": f"{case_id.lower()}_exposure",
                    "panel": "exposure",
                    "label": LABELS[case_id],
                },
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        height=760,
        hovermode="x unified",
        showlegend=False,
        uirevision="ndx-p24-factorial-holdings-v1",
    )
    figure.update_yaxes(title_text="只", row=1, col=1)
    figure.update_yaxes(title_text="%", range=[0, 105], row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def cost_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    primary = blocks[10.0]["metrics"].set_index("case_id")
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("CAGR：0与10 bps", "累计换手倍数（10 bps）"),
    )
    for cost, opacity in ((0.0, 0.42), (10.0, 1.0)):
        rows = blocks[cost]["metrics"].set_index("case_id")
        figure.add_trace(
            go.Bar(
                x=[LABELS[item] for item in CASE_IDS],
                y=[rows.at[item, "cagr_pct"] for item in CASE_IDS],
                name=f"{cost:g} bps",
                opacity=opacity,
                marker_color=[COLORS[item] for item in CASE_IDS],
                meta={"series_key": f"cagr_{cost:g}bps", "panel": "other", "cost_bps": cost},
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Bar(
            x=[LABELS[item] for item in CASE_IDS],
            y=[primary.at[item, "turnover_multiple"] for item in CASE_IDS],
            name="换手",
            marker_color=[COLORS[item] for item in CASE_IDS],
            meta={"series_key": "turnover_10bps", "panel": "other"},
        ),
        row=1,
        col=2,
    )
    figure.update_layout(
        height=590,
        barmode="group",
        showlegend=True,
        uirevision="ndx-p24-factorial-cost-v1",
    )
    figure.update_yaxes(title_text="%", row=1, col=1)
    figure.update_yaxes(title_text="倍", row=1, col=2)
    return figure


def effects_figure(effects: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("结构改变后的CAGR变化", "结构改变后的最大回撤变化"),
    )
    figure.add_trace(
        go.Bar(
            x=effects["effect"],
            y=effects["cagr_change_pp"],
            marker_color="#2563eb",
            name="CAGR变化",
            meta={"series_key": "factor_cagr_effect", "panel": "other"},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=effects["effect"],
            y=effects["max_drawdown_change_pp"],
            marker_color="#047857",
            name="回撤变化",
            meta={"series_key": "factor_drawdown_effect", "panel": "other"},
        ),
        row=1,
        col=2,
    )
    figure.update_layout(height=600, showlegend=False, uirevision="ndx-p24-factor-effects-v1")
    figure.update_yaxes(title_text="百分点", row=1, col=1)
    figure.update_yaxes(title_text="百分点；正值表示回撤变浅", row=1, col=2)
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
        f"<td>{benchmark['cagr_pct']:.2f}%</td>"
        f"<td>{blocks[0.0]['payload']['benchmark']['cagr_pct']:.2f}%</td>"
        f"<td>{benchmark['max_drawdown_pct']:.2f}%</td><td>{benchmark['sharpe']:.3f}</td>"
        "<td>2,012</td><td>—</td><td>1.0</td>"
        f"<td>{benchmark['turnover_multiple']:.1f}×</td><td>{int(benchmark['order_count'])}</td></tr>"
    )
    return (
        '<div style="overflow-x:auto"><table><thead><tr>'
        "<th>路径</th><th>10bps CAGR</th><th>0bps CAGR</th><th>10bps最大回撤</th>"
        "<th>Sharpe</th><th>持仓日</th><th>持仓CAGR</th><th>平均持股</th>"
        "<th>累计换手</th><th>订单</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def effects_table(effects: pd.DataFrame) -> str:
    rows = []
    for row in effects.itertuples(index=False):
        rows.append(
            "<tr>"
            f"<td>{html.escape(row.effect)}</td><td>{row.cagr_change_pp:+.2f}</td>"
            f"<td>{row.max_drawdown_change_pp:+.2f}</td><td>{row.sharpe_change:+.3f}</td>"
            f"<td>{row.turnover_reduction_pct:+.1f}%</td><td>{int(row.order_count_change):+,}</td>"
            "</tr>"
        )
    return (
        '<div style="overflow-x:auto"><table><thead><tr>'
        "<th>结构变化</th><th>CAGR变化</th><th>回撤变化</th><th>Sharpe变化</th>"
        "<th>换手减少</th><th>订单变化</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def markdown_report(blocks: dict[float, dict[str, Any]], effects: pd.DataFrame) -> str:
    primary = blocks[10.0]["metrics"].set_index("case_id")
    zero = blocks[0.0]["metrics"].set_index("case_id")
    benchmark = blocks[10.0]["payload"]["benchmark"]
    best_drawdown = primary["max_drawdown_pct"].idxmax()
    best_cagr = primary["cagr_pct"].idxmax()
    lines = [
        "| 路径 | 10bps CAGR | 0bps CAGR | 最大回撤 | Sharpe | 持仓日 | 持仓CAGR | 平均持股 | 累计换手 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case_id in CASE_IDS:
        p, z = primary.loc[case_id], zero.loc[case_id]
        lines.append(
            f"| {LABELS[case_id]} | {p.cagr_pct:.2f}% | {z.cagr_pct:.2f}% | "
            f"{p.max_drawdown_pct:.2f}% | {p.sharpe:.3f} | {int(p.holding_sessions)} | "
            f"{p.holding_cagr_pct:.2f}% | {p.average_holdings_count:.1f} | "
            f"{p.turnover_multiple:.1f}× |"
        )
    effect_lines = [
        "| 结构变化 | CAGR变化 | 回撤变化 | Sharpe变化 | 换手减少 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in effects.itertuples(index=False):
        effect_lines.append(
            f"| {row.effect} | {row.cagr_change_pp:+.2f}pp | "
            f"{row.max_drawdown_change_pp:+.2f}pp | {row.sharpe_change:+.3f} | "
            f"{row.turnover_reduction_pct:+.1f}% |"
        )
    return f"""# Nasdaq-100 P24滞回与再平衡2×2消融（2005–2012）

## 本次测试

- 完全冻结原P24与严格P24参数，只改变持有状态机和恢复等权频率。
- 所有历史合格股都持有，不排名、不限数量；每日Close确认，下一Open执行；成本只测0和10bps。
- 数据是`candidate_pending_review`，本轮只能用于探索结构，不能晋级。

## 结果

{chr(10).join(lines)}

同期QQQ Buy & Hold在10bps下CAGR为{benchmark['cagr_pct']:.2f}%、最大回撤为{benchmark['max_drawdown_pct']:.2f}%、Sharpe为{benchmark['sharpe']:.3f}。

## 因子作用

{chr(10).join(effect_lines)}

## 判断

- 本窗口回撤最浅的是“{LABELS[best_drawdown]}”，最大回撤{primary.at[best_drawdown, 'max_drawdown_pct']:.2f}%；10bps CAGR最高的是“{LABELS[best_cagr]}”，为{primary.at[best_cagr, 'cagr_pct']:.2f}%。这些都是2005–2012探索结果，不是未来参数选择。
- 停止名单不变日调仓在严格持有路径上减少换手{effects.iloc[0].turnover_reduction_pct:.1f}%，在滞回路径上减少{effects.iloc[1].turnover_reduction_pct:.1f}%；对应CAGR变化分别为{effects.iloc[0].cagr_change_pp:+.2f}和{effects.iloc[1].cagr_change_pp:+.2f}个百分点。
- 严格买入、原P24退出在每日等权下令回撤变化{effects.iloc[2].max_drawdown_change_pp:+.2f}个百分点，在名单变化等权下令回撤变化{effects.iloc[3].max_drawdown_change_pp:+.2f}个百分点；正值表示回撤变浅。
- 四条路径仍应与QQQ和接近全时段的股票暴露一起解释；本实验没有加入市场广度、波动率风险开关或仓位上限。
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
    primary = blocks[10.0]["metrics"]
    primary_indexed = primary.set_index("case_id")
    benchmark = blocks[10.0]["payload"]["benchmark"]
    effects = factor_effects(primary)
    best_drawdown = primary_indexed["max_drawdown_pct"].idxmax()
    best_cagr = primary_indexed["cagr_pct"].idxmax()
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>最浅回撤</h3><p class="metric">{primary_indexed.at[best_drawdown, "max_drawdown_pct"]:.2f}%</p><p>{html.escape(LABELS[best_drawdown])}</p></article>'
        f'<article><h3>最高10bps CAGR</h3><p class="metric">{primary_indexed.at[best_cagr, "cagr_pct"]:.2f}%</p><p>{html.escape(LABELS[best_cagr])}</p></article>'
        f'<article><h3>严格路径换手减少</h3><p class="metric">{effects.iloc[0].turnover_reduction_pct:.1f}%</p><p>停止名单不变日调仓</p></article>'
        f'<article><h3>滞回路径换手减少</h3><p class="metric">{effects.iloc[1].turnover_reduction_pct:.1f}%</p><p>停止名单不变日调仓</p></article>'
        "</div>"
        "<p><strong>结论边界：</strong>本轮只识别两项结构的主效应和交互，不调P24参数，也不把2005–2012表现当作样本外选择。</p>"
        "<h3>完整结果</h3>"
        + result_table(blocks)
        + "<h3>结构作用</h3>"
        + effects_table(effects)
    )
    terminal_notes = ", ".join(
        f"{LABELS[case_id]} {int(primary_indexed.at[case_id, 'terminal_settlement_order_count'])}笔"
        for case_id in CASE_IDS
    )
    report = render_interactive_report(
        title="Nasdaq-100 P24滞回与再平衡2×2消融 2005–2012",
        heading="历史Nasdaq-100成分：严格买入/原版退出与减少无效调仓",
        subtitle="冻结P24参数；不排名、不限数量；每日Close确认、下一Open；0/10 bps",
        summary_html=summary_html,
        notes=[
            "成员身份使用逐证券InIndex区间并统一延迟一个XNYS交易日；没有用今天的成分倒推历史。",
            "严格P24与原P24都连续2日确认；滞回只改变买入后的退出门槛，不允许普通P24从空仓直接买入。",
            "名单变化等权不是延迟信号：新增与退出仍按每日Close确认、下一Open执行，只抑制目标名单完全相同时的价格漂移调仓。",
            "持仓CAGR按实际有至少一只股票的交易日折算，只是资金效率诊断，正式比较仍看日历CAGR。",
            "本轮没有市场风险开关、排名、Top-N、数量上限、固定止损或参数扫描。",
            "历史成分与逐股价格仍是candidate_pending_review；结果不得冒充approved研究。",
            f"终端代理只结算已有持仓，不能新买；10bps各路径为：{terminal_notes}。",
        ],
        figures=[
            ReportFigure(
                "performance",
                "四条结构路径与QQQ净值、回撤",
                performance_figure(blocks),
                "performance",
            ),
            ReportFigure(
                "holdings",
                "持股数量与股票暴露（10 bps）",
                holdings_figure(blocks[10.0]),
                "market",
            ),
            ReportFigure(
                "cost",
                "成本拖累与累计换手",
                cost_figure(blocks),
                "other",
            ),
            ReportFigure(
                "effects",
                "持有政策与再平衡政策的结构作用",
                effects_figure(effects),
                "other",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(blocks, effects)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nNasdaq-100 P24滞回与再平衡2×2消融；详见 `report.html`、`report.pdf` 与 `report.md`。\n",
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
    effects.to_csv(analysis_root / "factor_effects_10bps.csv", index=False, lineterminator="\n")
    cost_effects = primary_indexed[["cagr_pct", "max_drawdown_pct", "turnover_multiple"]].join(
        blocks[0.0]["metrics"].set_index("case_id")[["cagr_pct"]], rsuffix="_0bps"
    ).reset_index()
    cost_effects["cagr_cost_drag_pp"] = (
        cost_effects["cagr_pct"] - cost_effects["cagr_pct_0bps"]
    )
    cost_effects.to_csv(analysis_root / "cost_effects.csv", index=False, lineterminator="\n")
    max_check = max(
        float(value)
        for cost in (0.0, 10.0)
        for case in blocks[cost]["payload"]["max_cross_check_differences"].values()
        for value in case.values()
    )
    best_drawdown_value = float(primary_indexed.at[best_drawdown, "max_drawdown_pct"])
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
            for row in primary.itertuples(index=False)
        },
        "zero_bps_cases": {
            str(row.case_id): json_safe(row._asdict())
            for row in blocks[0.0]["metrics"].itertuples(index=False)
        },
        "factor_effects_10bps": [json_safe(row._asdict()) for row in effects.itertuples(index=False)],
        "benchmark": benchmark,
        "best_observed_drawdown_case": best_drawdown,
        "best_observed_cagr_case": best_cagr,
        "maximum_cross_check_difference": max_check,
        "interpretation": {
            "any_case_shallower_drawdown_than_qqq": best_drawdown_value > float(benchmark["max_drawdown_pct"]),
            "selection_change_reduced_turnover_in_both_holding_policies": bool(
                effects.iloc[0].turnover_reduction_pct > 0 and effects.iloc[1].turnover_reduction_pct > 0
            ),
            "hysteresis_improved_drawdown_in_both_rebalance_policies": bool(
                effects.iloc[2].max_drawdown_change_pp > 0
                and effects.iloc[3].max_drawdown_change_pp > 0
            ),
            "candidate_data_blocks_promotion": True,
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pdf = subprocess.run(
        [
            "node",
            "scripts/print_html_pdf.mjs",
            str(run_root / "report_print.html"),
            str(run_root / "report.pdf"),
            "什么时候买",
            "什么时候卖",
            "信号如何变成成交",
            "成本与比较",
        ],
        cwd=BACKTEST_ROOT,
        text=True,
        capture_output=True,
        check=False,
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
        "backtest/scripts/run_nasdaq100_p24_hysteresis_rebalance.py",
        "backtest/scripts/analyze_nasdaq100_p24_hysteresis_rebalance.py",
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
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    artifacts = {}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            artifacts[str(path.relative_to(run_root))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": artifacts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if load_run(context, args.run_id)["status"] == "running":
        record_analysis_complete(context, args.run_id)
    print(markdown)


if __name__ == "__main__":
    main()
