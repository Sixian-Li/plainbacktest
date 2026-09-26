#!/usr/bin/env python3
"""Build the detailed report for the QQQ robust SMA200 a/b selection run."""

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
from plotly.subplots import make_subplots

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.intraday_sma_threshold import prepare_intraday_threshold_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_intraday_sma200_threshold_grid import drawdown, market_figure
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection"

REASON_LABELS = {
    "FINAL_STABLE_REPRESENTATIVE": "最终稳定代表",
    "FULL_HISTORY_CAGR_MAX": "全历史 CAGR 冠军",
    "FULL_HISTORY_SHARPE_MAX": "全历史 Sharpe 冠军",
    "ROLLING_5Y_Q25_MAX": "S5 机械冠军",
    "RESTART_10Y_Q25_MAX": "S10 机械冠军",
    "PREDECLARED_REFERENCE_ANCHOR": "预登记参考点",
}
REASON_COLORS = {
    "FINAL_STABLE_REPRESENTATIVE": "#0f766e",
    "FULL_HISTORY_CAGR_MAX": "#dc2626",
    "FULL_HISTORY_SHARPE_MAX": "#7c3aed",
    "ROLLING_5Y_Q25_MAX": "#2563eb",
    "RESTART_10Y_Q25_MAX": "#d97706",
    "PREDECLARED_REFERENCE_ANCHOR": "#475569",
}


def primary_reason(selection_reason: str) -> str:
    reasons = str(selection_reason).split("|")
    for preferred in REASON_LABELS:
        if preferred in reasons:
            return preferred
    return reasons[0]


def candidate_label(row: pd.Series) -> str:
    reason = primary_reason(str(row["selection_reason"]))
    return f"{REASON_LABELS.get(reason, reason)}（a={row['a_pct']:.2f}%, b={row['b_pct']:.2f}%）"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    formal: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("六类正式候选与 QQQ Buy & Hold", "从各自历史峰值回撤"),
    )
    for _, row in formal.iterrows():
        case_id = str(row["case_id"])
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        reason = primary_reason(str(row["selection_reason"]))
        label = candidate_label(row)
        color = REASON_COLORS.get(reason, "#64748b")
        width = 3.0 if reason == "FINAL_STABLE_REPRESENTATIVE" else 1.8
        for panel, values, row_number, showlegend in (
            ("equity", frame["equity"], 1, True),
            ("drawdown", drawdown(frame["equity"]), 2, False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    line={"color": color, "width": width},
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    label = "QQQ Buy & Hold（2000-01-03 Open，5 bps）"
    for panel, values, row_number, showlegend in (
        ("equity", benchmark["equity"], 1, True),
        ("drawdown", drawdown(benchmark["equity"]), 2, False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=label,
                showlegend=showlegend,
                line={"color": "#111827", "width": 2.0, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel,
                    "label": label,
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5,
                },
                hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
            ),
            row=row_number,
            col=1,
        )
    figure.update_layout(
        height=820,
        margin={"l": 65, "r": 25, "t": 75, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma200-robust-selection-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def metric_surfaces_figure(results: pd.DataFrame, formal: pd.DataFrame) -> go.Figure:
    metrics = (
        ("rolling_5y_cagr_q25_pct", "S5：滚动五年 CAGR 第25百分位", "S5 %"),
        ("restart_10y_cagr_q25_pct", "S10：十年空仓重启 CAGR 第25百分位", "S10 %"),
        ("cagr_pct", "全历史 CAGR", "CAGR %"),
        ("sharpe", "全历史 Sharpe", "Sharpe"),
    )
    figure = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[item[1] for item in metrics],
        horizontal_spacing=0.10,
        vertical_spacing=0.14,
    )
    for index, (metric, _, label) in enumerate(metrics):
        row_number = index // 2 + 1
        column = index % 2 + 1
        pivot = results.pivot(index="a_pct", columns="b_pct", values=metric).sort_index().sort_index(axis=1)
        figure.add_trace(
            go.Heatmap(
                x=pivot.columns,
                y=pivot.index,
                z=pivot.to_numpy(float),
                colorscale="Turbo",
                colorbar={
                    "title": label,
                    "len": 0.36,
                    "x": 0.45 if column == 1 else 1.02,
                    "y": 0.78 if row_number == 1 else 0.22,
                },
                hovertemplate=f"a=%{{y:.2f}}%<br>b=%{{x:.2f}}%<br>{label}=%{{z:.4f}}<extra></extra>",
            ),
            row=row_number,
            col=column,
        )
        for _, candidate in formal.iterrows():
            reason = primary_reason(str(candidate["selection_reason"]))
            if reason == "FINAL_STABLE_REPRESENTATIVE":
                symbol, size = "circle-open", 14
            elif (
                metric == "cagr_pct" and "FULL_HISTORY_CAGR_MAX" in str(candidate["selection_reason"])
            ) or (
                metric == "sharpe" and "FULL_HISTORY_SHARPE_MAX" in str(candidate["selection_reason"])
            ) or (
                metric == "rolling_5y_cagr_q25_pct" and "ROLLING_5Y_Q25_MAX" in str(candidate["selection_reason"])
            ) or (
                metric == "restart_10y_cagr_q25_pct" and "RESTART_10Y_Q25_MAX" in str(candidate["selection_reason"])
            ):
                symbol, size = "x", 12
            elif reason == "PREDECLARED_REFERENCE_ANCHOR":
                symbol, size = "diamond-open", 11
            else:
                continue
            figure.add_trace(
                go.Scatter(
                    x=[candidate["b_pct"]],
                    y=[candidate["a_pct"]],
                    mode="markers",
                    marker={
                        "symbol": symbol,
                        "size": size,
                        "color": "white",
                        "line": {"color": "#111827", "width": 2},
                    },
                    showlegend=False,
                    hovertemplate=f"{candidate_label(candidate)}<extra></extra>",
                ),
                row=row_number,
                col=column,
            )
        figure.update_xaxes(title_text="b（%）", row=row_number, col=column)
        figure.update_yaxes(title_text="a（%）", row=row_number, col=column)
    figure.update_layout(
        height=1080,
        margin={"l": 65, "r": 100, "t": 90, "b": 60},
        showlegend=False,
    )
    return figure


def gate_surface_figure(results: pd.DataFrame, summary: dict[str, Any]) -> go.Figure:
    pivot = results.pivot(index="a_pct", columns="b_pct", values="rolling_5y_cagr_q25_pct").sort_index().sort_index(axis=1)
    status = np.zeros(pivot.shape, dtype=float)
    indexed = results.set_index(["a_pct", "b_pct"])
    for row_index, a_pct in enumerate(pivot.index):
        for column, b_pct in enumerate(pivot.columns):
            record = indexed.loc[(a_pct, b_pct)]
            if bool(record["passes_identifiability"]):
                status[row_index, column] = 1
            if bool(record["passes_restart_gate"]):
                status[row_index, column] = 2
    component = summary["plateau"]["largest_component"]["cells"]
    component_set = {(float(item["a_pct"]), float(item["b_pct"])) for item in component}
    for row_index, a_pct in enumerate(pivot.index):
        for column, b_pct in enumerate(pivot.columns):
            if (float(a_pct), float(b_pct)) in component_set:
                status[row_index, column] = 3
    representative = summary["plateau"]["representative"]
    figure = go.Figure(
        go.Heatmap(
            x=pivot.columns,
            y=pivot.index,
            z=status,
            zmin=0,
            zmax=3,
            colorscale=[
                [0.0, "#e2e8f0"],
                [0.32, "#e2e8f0"],
                [0.34, "#bfdbfe"],
                [0.65, "#bfdbfe"],
                [0.67, "#fde68a"],
                [0.98, "#fde68a"],
                [1.0, "#10b981"],
            ],
            colorbar={
                "title": "筛选阶段",
                "tickvals": [0, 1, 2, 3],
                "ticktext": ["未识别", "可识别", "S10门禁", "最终平台"],
            },
            hovertemplate="a=%{y:.2f}%<br>b=%{x:.2f}%<br>阶段=%{z:.0f}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[representative["b_pct"]],
            y=[representative["a_pct"]],
            mode="markers",
            marker={"symbol": "circle-open", "size": 16, "color": "white", "line": {"color": "#111827", "width": 3}},
            name="最终代表",
            hovertemplate="最终稳定代表<br>a=%{y:.2f}%<br>b=%{x:.2f}%<extra></extra>",
        )
    )
    figure.update_layout(height=700, margin={"l": 65, "r": 100, "t": 50, "b": 60}, showlegend=False)
    figure.update_xaxes(title_text="b（%）")
    figure.update_yaxes(title_text="a（%）")
    return figure


def window_comparison_figure(
    rolling: pd.DataFrame,
    restart: pd.DataFrame,
    formal: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        vertical_spacing=0.16,
        subplot_titles=("连续状态：22个滚动五年 CAGR", "独立空仓：17个十年起点 CAGR"),
    )
    screen_to_formal = dict(zip(formal["screen_case_id"], formal["case_id"], strict=True))
    for _, candidate in formal.iterrows():
        screen_id = str(candidate["screen_case_id"])
        reason = primary_reason(str(candidate["selection_reason"]))
        label = candidate_label(candidate)
        color = REASON_COLORS.get(reason, "#64748b")
        width = 3.0 if reason == "FINAL_STABLE_REPRESENTATIVE" else 1.6
        left = rolling[rolling["case_id"] == screen_id].sort_values("window_start_year")
        right = restart[restart["case_id"] == screen_id].sort_values("start_year")
        figure.add_trace(
            go.Scatter(
                x=[f"{int(a)}–{int(b)}" for a, b in zip(left["window_start_year"], left["window_end_year"], strict=True)],
                y=left["cagr_pct"],
                mode="lines+markers",
                name=label,
                line={"color": color, "width": width},
                marker={"size": 5},
                showlegend=True,
                hovertemplate=f"{label}<br>%{{x}}<br>CAGR %{{y:.3f}}%<extra></extra>",
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=right["start_year"],
                y=right["cagr_pct"],
                mode="lines+markers",
                name=label,
                line={"color": color, "width": width},
                marker={"size": 5},
                showlegend=False,
                hovertemplate=f"{label}<br>起点 %{{x}}<br>十年 CAGR %{{y:.3f}}%<extra></extra>",
            ),
            row=2,
            col=1,
        )
    figure.add_hline(y=0, line={"color": "#64748b", "dash": "dash"}, row=1, col=1)
    figure.add_hline(y=0, line={"color": "#64748b", "dash": "dash"}, row=2, col=1)
    figure.update_layout(
        height=900,
        margin={"l": 70, "r": 25, "t": 80, "b": 85},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.12},
    )
    figure.update_yaxes(title_text="CAGR（%）", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="CAGR（%）", fixedrange=False, row=2, col=1)
    return figure


def pbo_figure(splits: pd.DataFrame, summary: dict[str, Any]) -> go.Figure:
    figure = make_subplots(rows=1, cols=2, subplot_titles=("CSCV 样本外排名分布", "训练 Sharpe 与补集 Sharpe"))
    figure.add_trace(
        go.Histogram(
            x=splits["oos_rank_percentile"] * 100.0,
            nbinsx=25,
            marker={"color": "#2563eb"},
            hovertemplate="样本外排名 %{x:.1f}百分位<br>切分数 %{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_vline(x=50, line={"color": "#dc2626", "dash": "dash"}, row=1, col=1)
    figure.add_trace(
        go.Scatter(
            x=splits["train_sharpe"],
            y=splits["test_sharpe"],
            mode="markers",
            marker={
                "size": 6,
                "opacity": 0.55,
                "color": splits["oos_rank_percentile"],
                "colorscale": "RdYlGn",
                "cmin": 0,
                "cmax": 1,
                "colorbar": {"title": "OOS排名"},
            },
            hovertemplate="训练 Sharpe %{x:.3f}<br>补集 Sharpe %{y:.3f}<extra></extra>",
        ),
        row=1,
        col=2,
    )
    figure.update_layout(height=650, margin={"l": 65, "r": 90, "t": 75, "b": 55}, showlegend=False)
    figure.update_xaxes(title_text="样本外排名百分位", row=1, col=1)
    figure.update_yaxes(title_text="切分数", row=1, col=1)
    figure.update_xaxes(title_text="训练 Sharpe", row=1, col=2)
    figure.update_yaxes(title_text="补集 Sharpe", row=1, col=2)
    figure.add_annotation(
        text=f"PBO={summary['pbo']:.1%}；924个切分",
        xref="paper", yref="paper", x=0.22, y=1.08, showarrow=False,
    )
    return figure


def comparison_table(formal: pd.DataFrame) -> str:
    lines = [
        "| 角色 | a | b | S5 | S10 | 全历史 CAGR | Sharpe | 最大回撤 | 成交 | 普通买/卖 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in formal.iterrows():
        lines.append(
            f"| {REASON_LABELS.get(primary_reason(str(row['selection_reason'])), row['selection_reason'])} | "
            f"{row['a_pct']:.2f}% | {row['b_pct']:.2f}% | "
            f"{row['rolling_5y_cagr_q25_pct']:.3f}% | {row['restart_10y_cagr_q25_pct']:.3f}% | "
            f"{row['cagr_pct']:.3f}% | {row['sharpe']:.3f} | {row['max_drawdown_pct']:.2f}% | "
            f"{int(row['order_count'])} | {int(row['buy_sma_count'])}/{int(row['sell_sma_count'])} |"
        )
    return "\n".join(lines)


def markdown_report(
    run_id: str,
    summary: dict[str, Any],
    formal: pd.DataFrame,
) -> str:
    final = summary["final_representative"]
    plateau = summary["plateau"]["largest_component"]
    gate = summary["final_gate"]
    dsr = summary["dsr"]
    benchmark = summary["benchmark_metrics"]
    verdict = "通过全部统计门禁，可冻结为前向候选" if gate["overall_pass"] else "未通过全部门禁，不能称为可信固定参数"
    lines = [
        "# QQQ SMA200 a/b 全历史稳健选参",
        "",
        f"> Run `{run_id}`；2000-01-03～2025-12-31 完整历史用于探索性选择，2026年以后才是不可重调的前向期。",
        "",
        "## 结论",
        "",
        f"- 最终稳定代表：**a={final['a_pct']:.2f}%、b={final['b_pct']:.2f}%、c=d=5%**。",
        f"- S5={final['rolling_5y_cagr_q25_pct']:.3f}%，S10={final['restart_10y_cagr_q25_pct']:.3f}%，全历史 CAGR={final['cagr_pct']:.3f}%，Sharpe={final['sharpe']:.3f}，最大回撤={final['max_drawdown_pct']:.2f}%。",
        f"- 最大稳定平台：a={plateau['a_min']:.2f}%～{plateau['a_max']:.2f}%、b={plateau['b_min']:.2f}%～{plateau['b_max']:.2f}%，{plateau['cell_count']}格；结构门禁={'通过' if gate['plateau_structural_pass'] else '失败'}。",
        f"- PBO={summary['pbo']['pbo']:.2%}（门槛≤20%）：{'通过' if gate['pbo_pass'] else '失败'}。",
        f"- DSR（有效试验数 {summary['effective_trials']['effective_trial_count']:.1f}）={dsr['effective_trials']['probability']:.2%}（门槛≥95%）：{'通过' if gate['dsr_effective_trials_pass'] else '失败'}。",
        f"- 最终判定：**{verdict}**。若失败，不改选第二名。",
        "",
        "## 正式候选对照",
        "",
        comparison_table(formal),
        "",
        "## 多重检验敏感性",
        "",
        f"- DSR·相关性折算有效试验数：{dsr['effective_trials']['probability']:.2%}。",
        f"- DSR·当前6,909组全部独立试验：{dsr['current_grid_trials']['probability']:.2%}。",
        f"- DSR·473,487次保守历史上界：{dsr['conservative_historical_upper_bound']['probability']:.2%}。",
        f"- QQQ Buy & Hold：CAGR {benchmark['cagr_pct']:.3f}%，Sharpe {benchmark['sharpe']:.3f}，最大回撤 {benchmark['max_drawdown_pct']:.2f}%。",
        "",
        "## 口径与边界",
        "",
        "- S5来自22个滚动五年窗口；只在窗口起点重置绩效基数，不重置持仓、现金、成本价或纠错锚点。",
        "- S10来自2000～2016各年首个交易日独立空仓启动的17个十年回测，用作起始日敏感性门禁。",
        "- 全历史 CAGR 与 Sharpe 参数面完整展示，其机械冠军也经双账本正式核验，但不能覆盖 S5 主选参规则。",
        "- PBO/DSR是多重尝试诊断，不把本轮同一历史变成样本外证据。",
        "- 使用拆股及股息调整 OHLC，是总回报价格近似；不是原始成交价与现金股息的账户级重放。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)
    results = pd.read_csv(block / "parameter_results.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv")
    rolling = pd.read_csv(block / "rolling_5y_results.csv")
    restart = pd.read_csv(block / "restart_10y_results.csv")
    pbo_splits = pd.read_csv(block / "pbo_splits.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {
        **selection,
        "created_at_utc": created_at,
        "formal_candidates": json_safe(formal.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    final_case = str(selection["final_formal_case_id"])
    final_row = formal[formal["case_id"] == final_case].iloc[0]
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    prices = prepare_intraday_threshold_data(raw, int(context.config["strategy"]["sma_window"]))
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    final_orders = orders[orders["case_id"] == final_case].copy()
    final_plans = plans[plans["case_id"] == final_case].copy()

    gate = selection["final_gate"]
    verdict = "全部门禁通过" if gate["overall_pass"] else "至少一项门禁失败，不能冻结为可信固定参数"
    compact = (
        f"<p>完整扫描 <strong>{len(results):,}</strong> 组；最终稳定代表 "
        f"a=<strong>{final_row['a_pct']:.2f}%</strong>、b=<strong>{final_row['b_pct']:.2f}%</strong>。"
        f"S5 <strong>{final_row['rolling_5y_cagr_q25_pct']:.3f}%</strong>，S10 "
        f"<strong>{final_row['restart_10y_cagr_q25_pct']:.3f}%</strong>，全历史 CAGR "
        f"<strong>{final_row['cagr_pct']:.3f}%</strong>，Sharpe <strong>{final_row['sharpe']:.3f}</strong>。"
        f"PBO <strong>{selection['pbo']['pbo']:.2%}</strong>，有效试验数 DSR "
        f"<strong>{selection['dsr']['effective_trials']['probability']:.2%}</strong>。"
        f"最终判定：<strong>{html.escape(verdict)}</strong>。</p>"
    )
    figures = [
        ReportFigure("performance-qqq", "正式候选全历史净值与回撤", performance_figure(daily, benchmark, formal), "performance"),
        ReportFigure("metric-surfaces", "S5 / S10 / 全历史 CAGR / 全历史 Sharpe 参数面", metric_surfaces_figure(results, formal), "generic"),
        ReportFigure("selection-gates", "可识别性、S10门禁与最终稳定平台", gate_surface_figure(results, selection), "generic"),
        ReportFigure("window-comparison", "正式候选的滚动五年与十年起点敏感性", window_comparison_figure(rolling, restart, formal), "generic"),
        ReportFigure("pbo-diagnostics", "PBO：924个 CSCV 切分", pbo_figure(pbo_splits, selection["pbo"]), "generic"),
        ReportFigure(
            "market-qqq",
            "最终稳定代表的动态阈值、纠错线与成交",
            market_figure(
                prices,
                final_plans,
                final_orders,
                a_pct=float(final_row["a_pct"]),
                b_pct=float(final_row["b_pct"]),
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ SMA200 a/b 全历史稳健选参",
        heading="QQQ SMA200 a/b 全历史稳健选参",
        subtitle="连续状态滚动五年下分位为主；十年空仓起点、局部平台、PBO/DSR 为门禁；全历史 CAGR 与 Sharpe 为详细参照。",
        summary_html=compact,
        notes=[
            "固定 c=d=5%、单边5 bps、初始空仓；a=-2%～10%、b=-25%～10%，步长均为0.25%。",
            "S5的22个五年窗口只重置绩效基数，不重置策略状态；S10的17个十年窗口分别空仓重启。",
            "全历史 CAGR 和 Sharpe 冠军只作参照，不能覆盖预登记的 S5 稳定平台选择规则。",
            "PBO和DSR用于揭示反复尝试风险；2000～2025仍是探索数据，2026以后才可作为连续前向期。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, selection, formal), encoding="utf-8"
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_threshold.py",
        "backtest/quantkit/intraday_sma_threshold_search.py",
        "backtest/quantkit/intraday_sma_threshold_selection.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/surface.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/analyze_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_intraday_sma200_robust_selection.py",
        "backtest/scripts/analyze_intraday_sma200_robust_selection.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(),
            "numba": __import__("numba").__version__,
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
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run contains the QQQ SMA200 robust a/b selection experiment.

- `report.html` / `report.md`: detailed parameter, window and multiple-testing reports.
- `analysis/summary.json`: machine-readable final selection and gate results.
- `QQQ/cost_5bps/`: all 6,909 cases, 22 rolling windows, 17 restarts, 924 CSCV splits, formal ledgers and diagnostic moments.
- `provenance.json` / `validation.json`: source and correctness evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
