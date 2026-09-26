#!/usr/bin/env python3
"""Analyze and report the QQQ three-factor downside-risk voting experiment."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import (
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from quantkit.trend_risk_vote import (
    DOWNSIDE_VOLATILITY,
    MAJORITY_2_OF_3,
    RISK_VETO,
    STRICT_3_OF_3,
    TOTAL_VOLATILITY,
)
from scripts.run_sma_regime_ablation import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/ROT/ROT-v0.40a.2__26-08-21__qqq_three_factor_downside_risk_training_2000_2015"
)

CASE_ORDER = [
    "BASELINE_P24",
    "CENTER_RISK_VETO_DOWNSIDE",
    "SELECTED_RISK_VETO_DOWNSIDE",
    "SELECTED_RISK_VETO_TOTAL",
    "SELECTED_STRICT_DOWNSIDE",
    "SELECTED_MAJORITY_DOWNSIDE",
]
CASE_LABELS = {
    "BASELINE_P24": "无风险因子P24",
    "CENTER_RISK_VETO_DOWNSIDE": "中心下行风险否决",
    "SELECTED_RISK_VETO_DOWNSIDE": "代表下行风险否决",
    "SELECTED_RISK_VETO_TOTAL": "代表普通波动风险否决",
    "SELECTED_STRICT_DOWNSIDE": "代表严格3/3",
    "SELECTED_MAJORITY_DOWNSIDE": "代表普通2/3",
}


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.08,
        subplot_titles=("账户净值", "回撤"),
    )
    colors = {
        "BASELINE_P24": "#64748b",
        "CENTER_RISK_VETO_DOWNSIDE": "#f59e0b",
        "SELECTED_RISK_VETO_DOWNSIDE": "#0f766e",
        "SELECTED_RISK_VETO_TOTAL": "#2563eb",
        "SELECTED_STRICT_DOWNSIDE": "#7c3aed",
        "SELECTED_MAJORITY_DOWNSIDE": "#dc2626",
    }
    default_visible = {
        "BASELINE_P24",
        "SELECTED_RISK_VETO_DOWNSIDE",
    }
    for case_id in CASE_ORDER:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        visible: bool | str = True if case_id in default_visible else "legendonly"
        for row, values, panel in (
            (1, frame["equity"], "equity"),
            (2, drawdown(frame["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scattergl(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=CASE_LABELS[case_id],
                    line={"color": colors[case_id], "width": 1.7},
                    visible=visible,
                    meta={
                        "series_key": case_id.lower(),
                        "panel": panel,
                        "label": CASE_LABELS[case_id],
                        "is_benchmark": False,
                        "cost_bps": 5,
                    },
                ),
                row=row,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    for row, values, panel in (
        (1, benchmark["equity"], "equity"),
        (2, drawdown(benchmark["equity"]), "drawdown"),
    ):
        figure.add_trace(
            go.Scattergl(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name="QQQ Buy & Hold",
                line={"color": "#111827", "width": 1.5, "dash": "dot"},
                meta={
                    "series_key": "buy_hold",
                    "panel": panel,
                    "label": "QQQ Buy & Hold",
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5,
                },
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        height=850,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision="rot-three-factor-risk-performance",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_risk_figure(indicators: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.68, 0.32],
        vertical_spacing=0.06,
        subplot_titles=("QQQ、长短均线与代表持仓", "下行波动风险比率"),
    )
    figure.add_trace(
        go.Candlestick(
            x=indicators["date"],
            open=indicators["open"],
            high=indicators["high"],
            low=indicators["low"],
            close=indicators["close"],
            name="QQQ复权OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        ),
        row=1,
        col=1,
    )
    for column, label, color in (
        ("long_sma", "长期SMA", "#2563eb"),
        ("short_sma", "短期SMA", "#d97706"),
    ):
        figure.add_trace(
            go.Scatter(
                x=indicators["date"],
                y=indicators[column],
                mode="lines",
                name=label,
                line={"color": color, "width": 1.3},
                meta={"series_key": column, "panel": "market", "label": label},
            ),
            row=1,
            col=1,
        )
    long_frame = indicators[indicators["target_long"].astype(bool)]
    figure.add_trace(
        go.Scattergl(
            x=long_frame["date"],
            y=long_frame["close"],
            mode="markers",
            name="代表持仓日",
            marker={"size": 3, "color": "#0f766e", "opacity": 0.38},
            meta={"series_key": "representative_long", "panel": "market", "label": "代表持仓日"},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=indicators["date"],
            y=indicators["risk_ratio"],
            mode="lines",
            name="下行风险比率",
            line={"color": "#7c3aed", "width": 1.4},
            meta={"series_key": "risk_ratio", "panel": "risk", "label": "下行风险比率"},
        ),
        row=2,
        col=1,
    )
    danger = float(indicators["danger_ratio"].iloc[0])
    recovery = float(indicators["recovery_ratio"].iloc[0])
    figure.add_hline(y=danger, line_dash="dash", line_color="#dc2626", row=2, col=1)
    figure.add_hline(y=recovery, line_dash="dot", line_color="#0f766e", row=2, col=1)
    unsafe = indicators[~indicators["risk_safe"].astype(bool)]
    figure.add_trace(
        go.Scattergl(
            x=unsafe["date"],
            y=unsafe["risk_ratio"],
            mode="markers",
            name="风险危险日",
            marker={"size": 4, "color": "#dc2626", "opacity": 0.55},
            meta={"series_key": "risk_unsafe", "panel": "risk", "label": "风险危险日"},
        ),
        row=2,
        col=1,
    )
    figure.update_layout(
        height=840,
        showlegend=False,
        xaxis2={"rangeslider": {"visible": True}},
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision="rot-three-factor-risk-market",
    )
    figure.update_yaxes(title_text="复权价格", row=1, col=1)
    figure.update_yaxes(title_text="短期/长期", row=2, col=1)
    return figure


def ablation_figure(formal: pd.DataFrame, subwindows: list[dict[str, str]]) -> go.Figure:
    chosen = formal.set_index("case_id").loc[CASE_ORDER]
    labels = [CASE_LABELS[case_id] for case_id in CASE_ORDER]
    figure = make_subplots(
        rows=1,
        cols=2,
        horizontal_spacing=0.12,
        subplot_titles=("完整期风险收益", "四个独立子窗口回撤"),
    )
    figure.add_trace(
        go.Bar(x=labels, y=chosen["cagr_pct"], name="CAGR", marker_color="#2563eb"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=chosen["max_drawdown_pct"],
            name="完整期最大回撤",
            marker_color="#dc2626",
        ),
        row=1,
        col=1,
    )
    colors = ["#0f766e", "#0891b2", "#7c3aed", "#d97706"]
    for index, item in enumerate(subwindows):
        figure.add_trace(
            go.Bar(
                x=labels,
                y=chosen[f"{item['window_id']}_max_drawdown_pct"],
                name=item["window_id"],
                marker_color=colors[index],
            ),
            row=1,
            col=2,
        )
    figure.update_layout(
        height=680,
        barmode="group",
        margin={"l": 65, "r": 25, "t": 70, "b": 110},
        uirevision="rot-three-factor-risk-ablation",
    )
    figure.update_xaxes(tickangle=-25)
    figure.update_yaxes(title_text="%")
    return figure


def best_by_structure(results: pd.DataFrame) -> pd.DataFrame:
    eligible = results[results["passes_guard"].astype(bool)].copy()
    return (
        eligible.sort_values(
            [
                "decision_structure",
                "risk_kind",
                "worst_subwindow_max_drawdown_pct",
                "ulcer_index_pct",
                "case_id",
            ],
            ascending=[True, True, False, True, True],
        )
        .groupby(["decision_structure", "risk_kind"], sort=True, as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def structure_best_figure(best: pd.DataFrame) -> go.Figure:
    structure_labels = {
        STRICT_3_OF_3: "严格3/3",
        MAJORITY_2_OF_3: "普通2/3",
        RISK_VETO: "风险否决",
    }
    risk_labels = {
        DOWNSIDE_VOLATILITY: "下行波动",
        TOTAL_VOLATILITY: "普通波动",
    }
    labels = [
        f"{structure_labels[row.decision_structure]}·{risk_labels[row.risk_kind]}"
        for row in best.itertuples(index=False)
    ]
    colors = [
        "#7c3aed" if row.decision_structure == STRICT_3_OF_3 else
        "#dc2626" if row.decision_structure == MAJORITY_2_OF_3 else "#0f766e"
        for row in best.itertuples(index=False)
    ]
    customdata = best[
        [
            "cagr_pct",
            "max_drawdown_pct",
            "ulcer_index_pct",
            "short_window",
            "long_window",
            "danger_ratio",
        ]
    ]
    figure = go.Figure(
        go.Bar(
            x=labels,
            y=best["worst_subwindow_max_drawdown_pct"],
            marker_color=colors,
            customdata=customdata,
            hovertemplate=(
                "%{x}<br>最差分段回撤=%{y:.2f}%<br>CAGR=%{customdata[0]:.2f}%"
                "<br>完整期回撤=%{customdata[1]:.2f}%<br>Ulcer=%{customdata[2]:.2f}%"
                "<br>短窗=%{customdata[3]} 长窗=%{customdata[4]} 报警=%{customdata[5]}<extra></extra>"
            ),
        )
    )
    figure.add_hline(y=-17.644564, line_dash="dash", line_color="#64748b")
    figure.update_layout(
        height=540,
        xaxis_title="每种结构和风险定义的训练集描述性最好case",
        yaxis_title="四窗最差最大回撤 %（越高越好）",
        margin={"l": 75, "r": 25, "t": 45, "b": 100},
        uirevision="rot-three-factor-risk-structure-best",
    )
    figure.update_xaxes(tickangle=-22)
    return figure


def risk_grid_figure(results: pd.DataFrame, representative: pd.Series) -> go.Figure:
    principal = results[
        results["decision_structure"].eq(RISK_VETO)
        & results["risk_kind"].eq(DOWNSIDE_VOLATILITY)
    ]
    long_windows = sorted(principal["long_window"].unique())
    figure = make_subplots(
        rows=1,
        cols=len(long_windows),
        horizontal_spacing=0.08,
        subplot_titles=[f"长期窗口 {int(value)}日" for value in long_windows],
    )
    for index, value in enumerate(long_windows, start=1):
        frame = principal[principal["long_window"].eq(value)]
        table = frame.pivot(
            index="danger_ratio",
            columns="short_window",
            values="worst_subwindow_max_drawdown_pct",
        )
        figure.add_trace(
            go.Heatmap(
                x=table.columns,
                y=table.index,
                z=table.values,
                colorscale="RdYlGn",
                showscale=index == len(long_windows),
                colorbar={"title": "回撤 %"} if index == len(long_windows) else None,
                hovertemplate="短窗=%{x}<br>报警=%{y}<br>最差分段回撤=%{z:.2f}%<extra></extra>",
            ),
            row=1,
            col=index,
        )
        if int(value) == int(representative["long_window"]):
            figure.add_trace(
                go.Scatter(
                    x=[representative["short_window"]],
                    y=[representative["danger_ratio"]],
                    mode="markers",
                    marker={"size": 16, "symbol": "diamond-open", "color": "#111827"},
                    name="代表",
                    showlegend=False,
                ),
                row=1,
                col=index,
            )
        figure.update_xaxes(title_text="短窗口（日）", row=1, col=index)
        figure.update_yaxes(title_text="报警比率" if index == 1 else "", row=1, col=index)
    figure.update_layout(
        height=520,
        margin={"l": 70, "r": 60, "t": 75, "b": 65},
        uirevision="rot-three-factor-risk-grid",
    )
    return figure


def search_cloud(results: pd.DataFrame, plateau: pd.DataFrame, representative: pd.Series) -> go.Figure:
    principal = results[
        results["decision_structure"].eq(RISK_VETO)
        & results["risk_kind"].eq(DOWNSIDE_VOLATILITY)
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=principal["worst_subwindow_max_drawdown_pct"],
            y=principal["ulcer_index_pct"],
            mode="markers",
            name="下行风险否决网格",
            marker={
                "size": 9,
                "color": principal["cagr_pct"],
                "colorscale": "Viridis",
                "colorbar": {"title": "CAGR %"},
            },
            customdata=principal[["case_id", "short_window", "long_window", "danger_ratio"]],
            hovertemplate=(
                "%{customdata[0]}<br>最差分段回撤=%{x:.2f}%<br>Ulcer=%{y:.2f}%"
                "<br>短窗=%{customdata[1]} 长窗=%{customdata[2]} 报警=%{customdata[3]}<extra></extra>"
            ),
        )
    )
    figure.add_trace(
        go.Scattergl(
            x=plateau["worst_subwindow_max_drawdown_pct"],
            y=plateau["ulcer_index_pct"],
            mode="markers",
            name="1pp平台",
            marker={"size": 12, "color": "#f59e0b"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[representative["worst_subwindow_max_drawdown_pct"]],
            y=[representative["ulcer_index_pct"]],
            mode="markers+text",
            text=["代表"],
            textposition="top center",
            marker={"size": 17, "symbol": "diamond", "color": "#dc2626"},
            showlegend=False,
        )
    )
    figure.update_layout(
        height=650,
        xaxis_title="四窗最差最大回撤（越右越浅）",
        yaxis_title="Ulcer Index %（越低越好）",
        margin={"l": 75, "r": 25, "t": 45, "b": 65},
        uirevision="rot-three-factor-risk-cloud",
    )
    return figure


def promotion_result(
    primary: dict[str, Any],
    zero: dict[str, Any],
    config: dict[str, Any],
) -> tuple[bool, list[str]]:
    selection = config["parameters"]["selection"]
    reasons: list[str] = []
    representative = primary["representative"]
    if primary["representative_on_search_boundary"]:
        reasons.append("代表触及风险参数边界")
    if int(primary["selected_component_cases"]) < int(selection["minimum_connected_component_cases"]):
        reasons.append("连通平台case数不足")
    if int(primary["selected_direct_plateau_neighbors"]) < int(
        selection["minimum_representative_plateau_neighbors"]
    ):
        reasons.append("代表直接平台邻居不足")
    if float(primary["worst_subwindow_drawdown_improvement_vs_baseline_pct_points"]) < 2.0:
        reasons.append("最差分段回撤改善不足2pp")
    if float(primary["full_max_drawdown_improvement_vs_baseline_pct_points"]) < 0.0:
        reasons.append("完整期最大回撤恶化")
    if float(primary["ulcer_index_improvement_vs_baseline_pct_points"]) < 0.0:
        reasons.append("Ulcer Index恶化")
    if float(representative["cagr_pct"]) <= 0.0:
        reasons.append("5bps CAGR不为正")
    if float(zero["worst_subwindow_drawdown_improvement_vs_baseline_pct_points"]) < 0.0:
        reasons.append("0bps下回撤改善方向反转")
    return not reasons, reasons


def markdown_report(
    primary: dict[str, Any],
    formal: pd.DataFrame,
    structure_best: pd.DataFrame,
    promotion: bool,
    reasons: list[str],
) -> str:
    chosen = formal.set_index("case_id")
    baseline = chosen.loc["BASELINE_P24"]
    center = chosen.loc["CENTER_RISK_VETO_DOWNSIDE"]
    representative = chosen.loc["SELECTED_RISK_VETO_DOWNSIDE"]
    strict_downside = structure_best[
        structure_best["decision_structure"].eq(STRICT_3_OF_3)
        & structure_best["risk_kind"].eq(DOWNSIDE_VOLATILITY)
    ].iloc[0]
    strict_total = structure_best[
        structure_best["decision_structure"].eq(STRICT_3_OF_3)
        & structure_best["risk_kind"].eq(TOTAL_VOLATILITY)
    ].iloc[0]
    return f"""# QQQ 长短趋势与下行风险三因子：2000–2015

## 本次测试

- 长期趋势、短期趋势和风险安全构成三个因子；QQQ仍然只在0%和100%仓位之间切换。
- 主候选使用下行波动率风险否决；普通总波动率、严格3/3和普通2/3只作消融。
- 风险参数在2000–2015选择；2016年以后没有参与本实验。

## 5 bps结果

| 方案 | CAGR | 最大回撤 | 四窗最差回撤 | Ulcer | 持仓率 |
|---|---:|---:|---:|---:|---:|
| 无风险因子P24 | {baseline.cagr_pct:.2f}% | {baseline.max_drawdown_pct:.2f}% | {baseline.worst_subwindow_max_drawdown_pct:.2f}% | {baseline.ulcer_index_pct:.2f}% | {baseline.exposure_pct:.2f}% |
| 中心下行风险否决 | {center.cagr_pct:.2f}% | {center.max_drawdown_pct:.2f}% | {center.worst_subwindow_max_drawdown_pct:.2f}% | {center.ulcer_index_pct:.2f}% | {center.exposure_pct:.2f}% |
| 代表下行风险否决 | {representative.cagr_pct:.2f}% | {representative.max_drawdown_pct:.2f}% | {representative.worst_subwindow_max_drawdown_pct:.2f}% | {representative.ulcer_index_pct:.2f}% | {representative.exposure_pct:.2f}% |

代表风险参数：短窗口{int(representative.short_window)}日、长窗口{int(representative.long_window)}日、报警比率{representative.danger_ratio:g}、恢复比率{representative.recovery_ratio:g}。代表所在1pp分量有{primary['selected_component_cases']}个case，直接邻居{primary['selected_direct_plateau_neighbors']}个，边界参数为{', '.join(primary['representative_boundary_parameters']) if primary['representative_boundary_parameters'] else '无'}。

## 研究判断

- 2016–2026锁定测试门禁：**{'通过' if promotion else '未通过'}**。
- {'预声明的风险与平台门禁全部通过。' if promotion else '未通过原因：' + '；'.join(reasons) + '。'}
- 描述性线索：严格3/3的下行波动版本在自身网格内最好case为最差分段回撤{strict_downside.worst_subwindow_max_drawdown_pct:.2f}%、CAGR {strict_downside.cagr_pct:.2f}%；普通总波动版本为{strict_total.worst_subwindow_max_drawdown_pct:.2f}%、CAGR {strict_total.cagr_pct:.2f}%。这些case不属于本轮预注册主选择，也未通过独立PyBroker正式候选核对，只能作为后续新实验线索。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run = load_run(context, args.run_id)
    if any(item["status"] != "completed" for item in run["expected_blocks"]):
        raise RuntimeError("Every run block must be complete before analysis.")
    run_root = context.run_root(args.run_id)
    primary_root = block_root(
        context,
        args.run_id,
        "QQQ",
        float(context.config["primary_cost_bps_per_side"]),
    )
    zero_root = block_root(context, args.run_id, "QQQ", 0)
    primary = json.loads((primary_root / "summary.json").read_text(encoding="utf-8"))
    zero = json.loads((zero_root / "summary.json").read_text(encoding="utf-8"))
    formal = pd.read_csv(primary_root / "formal_cases.csv")
    formal_zero = pd.read_csv(zero_root / "formal_cases.csv")
    daily = pd.read_csv(primary_root / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(primary_root / "buy_hold_daily.csv", parse_dates=["date"])
    indicators = pd.read_csv(primary_root / "representative_indicators.csv", parse_dates=["date"])
    results = pd.read_csv(primary_root / "risk_grid_results.csv")
    plateau = pd.read_csv(primary_root / "plateau_results.csv")
    structure_best = best_by_structure(results)
    representative = formal[
        formal["case_id"].eq("SELECTED_RISK_VETO_DOWNSIDE")
    ].iloc[0]
    baseline = formal[formal["case_id"].eq("BASELINE_P24")].iloc[0]
    promotion, reasons = promotion_result(primary, zero, context.config)
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>风险case</h3><p class="metric">{primary["risk_grid_case_count"]}</p><p>三种结构与两种波动定义</p></article>'
        f'<article><h3>最差分段回撤</h3><p class="metric">{baseline.worst_subwindow_max_drawdown_pct:.2f}% → {representative.worst_subwindow_max_drawdown_pct:.2f}%</p><p>改善 {primary["worst_subwindow_drawdown_improvement_vs_baseline_pct_points"]:+.2f}pp</p></article>'
        f'<article><h3>风险报警</h3><p class="metric">{int(representative.risk_alarm_count)}</p><p>代表策略危险状态切换</p></article>'
        f'<article><h3>晋级判断</h3><p class="metric">{"通过" if promotion else "未通过"}</p><p>{"可以另建锁定测试" if promotion else html.escape("；".join(reasons))}</p></article>'
        '</div>'
        f'<p><strong>代表风险窗口：</strong>近期{int(representative.short_window)}日、长期{int(representative.long_window)}日；'
        f'<strong>风险状态：</strong>报警比率{representative.danger_ratio:g}、恢复比率{representative.recovery_ratio:g}。</p>'
    )
    report = render_interactive_report(
        title="QQQ 长短趋势与下行风险三因子 2000–2015",
        heading="QQQ满仓三因子：下行波动率风险否决与投票消融",
        subtitle="趋势参数保持父代表不变；主结果5 bps；2016年以后未参与选择",
        summary_html=summary_html,
        notes=[
            "主候选只在下行波动率风险否决的27组中选参，其他结构和普通总波动率不参与代表选择。",
            "报警和恢复使用不同阈值；风险解除后仍需长短趋势重新满足才买回。",
            "所有信号在完成收盘后确认，订单在下一交易日开盘执行，QQQ持仓始终为0%或100%。",
        ],
        figures=[
            ReportFigure(
                "market-qqq",
                "QQQ、长短均线、代表持仓与下行风险状态",
                market_risk_figure(indicators),
                "market",
            ),
            ReportFigure(
                "performance-qqq",
                "三因子结构、无风险P24与Buy & Hold净值",
                performance_figure(daily, benchmark),
                "performance",
            ),
            ReportFigure(
                "factor-ablation",
                "风险定义与投票结构消融",
                ablation_figure(formal, context.config["parameters"]["robustness_subwindows"]),
                "other",
            ),
            ReportFigure(
                "structure-best",
                "每种结构和风险定义的描述性最好case",
                structure_best_figure(structure_best),
                "other",
            ),
            ReportFigure(
                "risk-grid",
                "下行风险否决的三维参数切片",
                risk_grid_figure(results, representative),
                "other",
            ),
            ReportFigure(
                "risk-search-cloud",
                "下行风险否决的回撤—Ulcer平台",
                search_cloud(results, plateau, representative),
                "other",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(primary, formal, structure_best, promotion, reasons)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nQQQ长短趋势与下行风险三因子消融；详见 `report.html`。\n",
        encoding="utf-8",
    )
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    structure_best.to_csv(
        analysis_root / "structure_kind_descriptive_best.csv",
        index=False,
        lineterminator="\n",
    )
    analysis_summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "training_window": primary["training_window"],
        "risk_grid_case_count": primary["risk_grid_case_count"],
        "selected_component_cases": primary["selected_component_cases"],
        "selected_direct_plateau_neighbors": primary["selected_direct_plateau_neighbors"],
        "representative_on_search_boundary": primary["representative_on_search_boundary"],
        "representative_boundary_parameters": primary["representative_boundary_parameters"],
        "promotion_gate_pass": promotion,
        "promotion_gate_failures": reasons,
        "baseline": json_safe(baseline.to_dict()),
        "representative": json_safe(representative.to_dict()),
        "representative_zero_bps": json_safe(
            formal_zero[
                formal_zero["case_id"].eq("SELECTED_RISK_VETO_DOWNSIDE")
            ].iloc[0].to_dict()
        ),
        "formal_ablation": primary["formal_ablation"],
        "structure_kind_descriptive_best": [
            json_safe(record) for record in structure_best.to_dict("records")
        ],
        "risk_deltas": {
            "five_bps_worst_subwindow_drawdown_improvement_vs_baseline_pct_points": primary[
                "worst_subwindow_drawdown_improvement_vs_baseline_pct_points"
            ],
            "five_bps_full_max_drawdown_improvement_vs_baseline_pct_points": primary[
                "full_max_drawdown_improvement_vs_baseline_pct_points"
            ],
            "five_bps_ulcer_index_improvement_vs_baseline_pct_points": primary[
                "ulcer_index_improvement_vs_baseline_pct_points"
            ],
            "zero_bps_worst_subwindow_drawdown_improvement_vs_baseline_pct_points": zero[
                "worst_subwindow_drawdown_improvement_vs_baseline_pct_points"
            ],
        },
        "benchmark": primary["benchmark"],
        "max_cross_check_differences": {
            "cost_5bps": primary["max_cross_check_differences"],
            "cost_0bps": zero["max_cross_check_differences"],
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(analysis_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
        "backtest/quantkit/full_position_trend_quality.py",
        "backtest/quantkit/trend_risk_vote.py",
        "backtest/scripts/run_trend_risk_vote_training.py",
        "backtest/scripts/analyze_trend_risk_vote_training.py",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/processed/daily/QQQ.csv",
    ]
    for relative in source_paths:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
            artifacts[str(path.relative_to(run_root))] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "artifacts": artifacts},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if load_run(context, args.run_id)["status"] == "running":
        record_analysis_complete(context, args.run_id)
    print(markdown)


if __name__ == "__main__":
    main()
