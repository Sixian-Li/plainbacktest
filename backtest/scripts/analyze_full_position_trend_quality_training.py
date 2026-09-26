#!/usr/bin/env python3
"""Build the v4 report for QQQ full-position F2/F4 training and F5 ablation."""

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

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_sma_regime_ablation import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.20b.1__26-08-15__qqq_full_position_sma_trend_quality_training_2000_2015"
PARAMETER_LABELS = {
    "long_sma_window": "长期SMA窗口",
    "long_slope_lookback": "长期斜率回看",
    "long_slope_threshold_daily_pct": "长期斜率门槛（%/日）",
    "short_sma_window": "短期SMA窗口",
    "short_regression_window": "短趋势回归窗口",
    "short_quality_threshold_daily_pct": "短趋势质量门槛（%/日）",
    "entry_confirmation_sessions": "入场连续确认日",
}


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame) -> go.Figure:
    cases = [
        ("REPRESENTATIVE_B0", "仅价格状态 B0", "#94a3b8"),
        ("REPRESENTATIVE_B2", "价格 + F2", "#2563eb"),
        ("REPRESENTATIVE_B4", "价格 + F4", "#d97706"),
        ("REPRESENTATIVE_P24", "核心 P24", "#0f766e"),
        ("REPRESENTATIVE_P245_R60", "P24 + F5（R60）", "#7c3aed"),
    ]
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.67, 0.33], subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    for case_id, label, color in cases:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        if frame.empty:
            continue
        for row, values, panel, legend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=legend, line={"color": color, "width": 2.3},
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                        if row == 1 else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                    ),
                ), row=row, col=1,
            )
    benchmark = benchmark.sort_values("date")
    for row, values, panel, legend in (
        (1, benchmark["equity"], "equity", True),
        (2, drawdown(benchmark["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"], y=values, mode="lines", name="QQQ Buy & Hold",
                showlegend=legend, line={"color": "#111827", "width": 1.8, "dash": "dash"},
                meta={
                    "series_key": "buy_hold", "panel": panel, "label": "QQQ Buy & Hold",
                    "is_benchmark": panel == "equity", "cost_bps": 5,
                },
            ), row=row, col=1,
        )
    figure.update_layout(
        height=820, hovermode="x unified", showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55}, uirevision="tim-f24-performance",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(indicators: pd.DataFrame, daily: pd.DataFrame) -> go.Figure:
    frame = daily[daily["case_id"].eq("REPRESENTATIVE_P24")].sort_values("date")
    long_dates = set(pd.to_datetime(frame.loc[frame["is_long"].astype(bool), "date"]))
    long_frame = indicators[pd.to_datetime(indicators["date"]).isin(long_dates)]
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=indicators["date"], open=indicators["open"], high=indicators["high"],
            low=indicators["low"], close=indicators["close"], name="QQQ复权OHLC",
            increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
        )
    )
    for column, label, color in (
        ("short_sma", "代表短SMA", "#d97706"),
        ("long_sma", "代表长期SMA", "#2563eb"),
    ):
        figure.add_trace(
            go.Scatter(
                x=indicators["date"], y=indicators[column], mode="lines", name=label,
                line={"color": color, "width": 1.4},
                meta={"series_key": column, "panel": "market", "label": label},
            )
        )
    figure.add_trace(
        go.Scattergl(
            x=long_frame["date"], y=long_frame["close"], mode="markers", name="P24持仓日",
            marker={"size": 3, "color": "#0f766e", "opacity": 0.45},
            meta={"series_key": "p24_long_days", "panel": "market", "label": "P24持仓日"},
        )
    )
    figure.update_layout(
        height=600, showlegend=False, yaxis_title="复权价格",
        xaxis={"rangeslider": {"visible": True}}, margin={"l": 65, "r": 25, "t": 45, "b": 55},
        uirevision="tim-f24-market",
    )
    return figure


def oat_figure(points: pd.DataFrame) -> go.Figure:
    sweeps = list(dict.fromkeys(points["sweep_id"].astype(str)))
    figure = make_subplots(
        rows=4, cols=2, vertical_spacing=0.11, horizontal_spacing=0.11,
        subplot_titles=[PARAMETER_LABELS[str(points[points["sweep_id"].eq(sweep)].iloc[0]["parameter"])] for sweep in sweeps],
    )
    for index, sweep in enumerate(sweeps):
        row, col = index // 2 + 1, index % 2 + 1
        frame = points[points["sweep_id"].eq(sweep)].sort_values("point_order")
        figure.add_trace(
            go.Scatter(
                x=frame["value"], y=frame["worst_subwindow_max_drawdown_pct"],
                mode="lines+markers", name="最差分段回撤", line={"color": "#dc2626", "width": 2},
                marker={"symbol": ["diamond" if value else "circle" for value in frame["selected_for_joint"]]},
                hovertemplate="值=%{x}<br>最差分段回撤=%{y:.2f}%<extra></extra>",
            ), row=row, col=col,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["value"], y=frame["cagr_pct"], mode="lines+markers",
                name="CAGR", line={"color": "#2563eb", "width": 1.5, "dash": "dot"},
                hovertemplate="值=%{x}<br>CAGR=%{y:.2f}%<extra></extra>",
            ), row=row, col=col,
        )
        figure.update_yaxes(title_text="%", row=row, col=col)
    figure.update_layout(
        height=1320, showlegend=False, margin={"l": 65, "r": 25, "t": 85, "b": 55},
        uirevision="tim-f24-oat",
    )
    return figure


def search_cloud(results: pd.DataFrame, representative: pd.Series) -> go.Figure:
    joint = results[results["stage"].eq("stage_2_joint")]
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=joint["worst_subwindow_max_drawdown_pct"], y=joint["cagr_pct"], mode="markers",
            name="2,187个联合case", marker={
                "size": 6, "opacity": 0.5, "color": joint["ulcer_index_pct"],
                "colorscale": "Viridis_r", "colorbar": {"title": "Ulcer %"},
            },
            customdata=joint[["case_id", "long_sma_window", "short_sma_window", "entry_confirmation_sessions"]],
            hovertemplate=(
                "%{customdata[0]}<br>最差分段回撤=%{x:.2f}%<br>CAGR=%{y:.2f}%"
                "<br>长期SMA=%{customdata[1]}<br>短期SMA=%{customdata[2]}"
                "<br>确认日=%{customdata[3]}<extra></extra>"
            ),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[representative["worst_subwindow_max_drawdown_pct"]], y=[representative["cagr_pct"]],
            mode="markers+text", text=["平台代表"], textposition="top center",
            marker={"size": 16, "symbol": "diamond", "color": "#ef4444"}, showlegend=False,
        )
    )
    figure.add_hline(y=2.0, line_dash="dash", line_color="#64748b")
    figure.update_layout(
        height=700, xaxis_title="四窗最差最大回撤（越右越浅）", yaxis_title="CAGR %",
        margin={"l": 75, "r": 25, "t": 45, "b": 65}, uirevision="tim-f24-cloud",
    )
    return figure


def ablation_figure(formal: pd.DataFrame, subwindows: list[dict[str, str]]) -> go.Figure:
    cases = ["REPRESENTATIVE_B0", "REPRESENTATIVE_B2", "REPRESENTATIVE_B4", "REPRESENTATIVE_P24", "REPRESENTATIVE_P245_R60"]
    labels = ["B0", "B2", "B4", "P24", "P245-R60"]
    chosen = formal.set_index("case_id").loc[cases]
    figure = make_subplots(
        rows=1, cols=2, horizontal_spacing=0.13,
        subplot_titles=("完整期指标", "四个独立子窗口最大回撤"),
    )
    figure.add_trace(
        go.Bar(x=labels, y=chosen["cagr_pct"], name="CAGR %", marker_color="#2563eb"), row=1, col=1
    )
    figure.add_trace(
        go.Bar(x=labels, y=chosen["worst_subwindow_max_drawdown_pct"], name="最差分段回撤 %", marker_color="#dc2626"), row=1, col=1
    )
    for index, item in enumerate(subwindows):
        figure.add_trace(
            go.Bar(
                x=labels, y=chosen[f"{item['window_id']}_max_drawdown_pct"],
                name=item["window_id"], marker_color=["#0f766e", "#0891b2", "#7c3aed", "#d97706"][index],
            ), row=1, col=2,
        )
    figure.update_layout(
        height=620, barmode="group", margin={"l": 65, "r": 25, "t": 70, "b": 60},
        uirevision="tim-f24-ablation",
    )
    figure.update_yaxes(title_text="%", row=1, col=1)
    figure.update_yaxes(title_text="%", row=1, col=2)
    return figure


def f5_figure(f5: pd.DataFrame) -> go.Figure:
    frame = f5.sort_values("relative_strength_lookback")
    figure = make_subplots(rows=1, cols=2, subplot_titles=("F5回看期与风险", "F5回看期与收益"))
    figure.add_trace(
        go.Bar(x=frame["relative_strength_lookback"], y=frame["worst_subwindow_max_drawdown_pct"], marker_color="#dc2626", name="最差分段回撤"), row=1, col=1
    )
    figure.add_trace(
        go.Bar(x=frame["relative_strength_lookback"], y=frame["ulcer_index_pct"], marker_color="#7c3aed", name="Ulcer Index"), row=1, col=1
    )
    figure.add_trace(
        go.Bar(x=frame["relative_strength_lookback"], y=frame["cagr_pct"], marker_color="#2563eb", name="CAGR"), row=1, col=2
    )
    figure.update_layout(
        height=560, barmode="group", margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision="tim-f5-ablation",
    )
    figure.update_xaxes(title_text="QQQ/SPY 动量回看交易日")
    figure.update_yaxes(title_text="%")
    return figure


def fmt_params(row: pd.Series) -> str:
    return (
        f"L={int(row.long_sma_window)}, K={int(row.long_slope_lookback)}, "
        f"F2门槛={row.long_slope_threshold_daily_pct:g}%/日；"
        f"S={int(row.short_sma_window)}, W={int(row.short_regression_window)}, "
        f"F4门槛={row.short_quality_threshold_daily_pct:g}%/日；C={int(row.entry_confirmation_sessions)}"
    )


def markdown_report(summary: dict[str, Any], center: pd.Series, rep: pd.Series, b0: pd.Series, f5: pd.DataFrame) -> str:
    improvement = float(rep.worst_subwindow_max_drawdown_pct - b0.worst_subwindow_max_drawdown_pct)
    boundary = summary["representative_boundary_parameters"]
    promotion = (
        improvement >= 3.0
        and float(rep.cagr_pct) >= 2.0
        and not bool(boundary)
        and int(summary["joint_one_pp_plateau_case_count"]) > 1
    )
    useful_f5 = f5[
        f5["worst_subwindow_max_drawdown_pct"].astype(float).gt(float(rep.worst_subwindow_max_drawdown_pct))
        & f5["ulcer_index_pct"].astype(float).lt(float(rep.ulcer_index_pct))
        & f5["cagr_pct"].astype(float).gt(0.0)
    ].sort_values(["ulcer_index_pct", "worst_subwindow_max_drawdown_pct"], ascending=[True, False])
    f5_sentence = (
        f"F5中有 {len(useful_f5)} 个回看期同时改善最差分段回撤与Ulcer Index；其中R{int(useful_f5.iloc[0].relative_strength_lookback)}为"
        f"CAGR {useful_f5.iloc[0].cagr_pct:.2f}%、最差分段回撤 {useful_f5.iloc[0].worst_subwindow_max_drawdown_pct:.2f}%、"
        f"Ulcer {useful_f5.iloc[0].ulcer_index_pct:.2f}%。这只构成后续研究线索，不改变P24参数。"
        if not useful_f5.empty else
        "没有F5回看期同时改善最差分段回撤与Ulcer Index并保持正CAGR。"
    )
    return f"""# QQQ 满仓 F2/F4 回撤优先训练：2000–2015

## 本次测试

- QQQ只能在0%与100%仓位之间切换，不使用波动率缩放、3%止损或8%止损。
- B0只要求Close>SMA_L；B2加入长期SMA平均日斜率；B4加入短SMA的OLS斜率×R²；核心P24同时启用F2和F4。
- 任一启用条件失效，当日Close发出信号，下一共同交易日Open清仓；重新全部满足后可再次满仓。
- P24参数只用2000–2015选择；F5（QQQ/SPY相对强弱）在P24冻结后才消融，不参与核心参数选择。

## 5 bps训练结果

| 方案 | CAGR | 最大回撤 | 四窗最差回撤 | Ulcer Index | 持仓率 | 闭合交易 |
|---|---:|---:|---:|---:|---:|---:|
| 中心P24 | {center.cagr_pct:.2f}% | {center.max_drawdown_pct:.2f}% | {center.worst_subwindow_max_drawdown_pct:.2f}% | {center.ulcer_index_pct:.2f}% | {center.exposure_pct:.2f}% | {int(center.closed_trade_count)} |
| 平台代表B0 | {b0.cagr_pct:.2f}% | {b0.max_drawdown_pct:.2f}% | {b0.worst_subwindow_max_drawdown_pct:.2f}% | {b0.ulcer_index_pct:.2f}% | {b0.exposure_pct:.2f}% | {int(b0.closed_trade_count)} |
| 平台代表P24 | {rep.cagr_pct:.2f}% | {rep.max_drawdown_pct:.2f}% | {rep.worst_subwindow_max_drawdown_pct:.2f}% | {rep.ulcer_index_pct:.2f}% | {rep.exposure_pct:.2f}% | {int(rep.closed_trade_count)} |

代表参数：{fmt_params(rep)}。

P24相对相同参数B0的四窗最差回撤改善为 {improvement:.2f} 个百分点。1pp平台有 {summary['joint_one_pp_plateau_case_count']} 个case；搜索边界参数为 {', '.join(boundary) if boundary else '无'}。

## 研究判断

- 后续独立2016–2026锁定测试门禁：**{'通过' if promotion else '未通过'}**。
- 当前结果是训练集内选择，不是未来有效性证明。{'代表落在搜索边界，因此必须扩展训练搜索或接受结构尚未稳定，不能直接做样本外晋级。' if boundary else '代表未触及搜索边界。'}
- {f5_sentence}
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
    primary = block_root(context, args.run_id, "QQQ", float(context.config["primary_cost_bps_per_side"]))
    sensitivity = block_root(context, args.run_id, "QQQ", 0)
    formal = pd.read_csv(primary / "formal_cases.csv")
    formal_zero = pd.read_csv(sensitivity / "formal_cases.csv")
    daily = pd.read_csv(primary / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(primary / "buy_hold_daily.csv", parse_dates=["date"])
    indicators = pd.read_csv(primary / "representative_indicators.csv", parse_dates=["date"])
    points = pd.read_csv(primary / "stage_1_oat_points.csv")
    stable = pd.read_csv(primary / "stable_value_selection.csv")
    points = points.merge(
        stable[["sweep_id", "point_order", "selected_for_joint", "within_stable_band", "connected_gate_pass"]],
        on=["sweep_id", "point_order"], how="left", validate="one_to_one",
    )
    results = pd.read_csv(primary / "parameter_results.csv")
    f5 = pd.read_csv(primary / "f5_ablation_results.csv")
    block_summary = json.loads((primary / "summary.json").read_text(encoding="utf-8"))
    center = formal[formal["case_id"].eq("CENTER_P24")].iloc[0]
    rep = formal[formal["case_id"].eq("REPRESENTATIVE_P24")].iloc[0]
    b0 = formal[formal["case_id"].eq("REPRESENTATIVE_B0")].iloc[0]
    zero_rep = formal_zero[formal_zero["case_id"].eq("REPRESENTATIVE_P24")].iloc[0]
    zero_b0 = formal_zero[formal_zero["case_id"].eq("REPRESENTATIVE_B0")].iloc[0]
    improvement = float(rep.worst_subwindow_max_drawdown_pct - b0.worst_subwindow_max_drawdown_pct)
    zero_improvement = float(zero_rep.worst_subwindow_max_drawdown_pct - zero_b0.worst_subwindow_max_drawdown_pct)
    promotion = (
        improvement >= 3.0
        and float(rep.cagr_pct) >= 2.0
        and int(block_summary["joint_one_pp_plateau_case_count"]) > 1
        and not bool(block_summary["representative_on_search_boundary"])
        and zero_improvement > 0.0
    )
    useful_f5 = f5[
        f5["worst_subwindow_max_drawdown_pct"].astype(float).gt(float(rep.worst_subwindow_max_drawdown_pct))
        & f5["ulcer_index_pct"].astype(float).lt(float(rep.ulcer_index_pct))
        & f5["cagr_pct"].astype(float).gt(0.0)
    ].sort_values(["ulcer_index_pct", "worst_subwindow_max_drawdown_pct"], ascending=[True, False])
    f5_gate = not useful_f5.empty
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>联合搜索</h3><p class="metric">{int(block_summary["case_counts"].get("stage_2_joint", 0)):,}</p><p>2000–2015训练case</p></article>'
        f'<article><h3>最差分段回撤</h3><p class="metric">{b0.worst_subwindow_max_drawdown_pct:.2f}% → {rep.worst_subwindow_max_drawdown_pct:.2f}%</p><p>改善 {improvement:.2f}pp</p></article>'
        f'<article><h3>P24 CAGR</h3><p class="metric">{rep.cagr_pct:.2f}%</p><p>5 bps，满仓/空仓</p></article>'
        f'<article><h3>晋级判断</h3><p class="metric">{"通过" if promotion else "暂不通过"}</p><p>{"可冻结做OOS" if promotion else "代表触边或门禁不足"}</p></article>'
        '</div>'
        f'<p><strong>平台代表参数：</strong>{html.escape(fmt_params(rep))}</p>'
        f'<p><strong>0 bps敏感性：</strong>P24 CAGR {zero_rep.cagr_pct:.2f}%，相对B0最差分段回撤改善 {zero_improvement:.2f}pp。</p>'
        f'<p><strong>F5消融：</strong>{"存在同时改善两项风险指标且保持正CAGR的回看期；最佳风险诊断为R" + str(int(useful_f5.iloc[0].relative_strength_lookback)) if f5_gate else "没有回看期通过预声明的双风险改善条件"}。F5不参与P24选参。</p>'
    )
    report = render_interactive_report(
        title="QQQ 满仓 F2/F4 回撤优先训练 2000–2015",
        heading="QQQ 0%/100% 满仓切换：长期斜率、短趋势质量与相对强弱消融",
        subtitle="仅用2000–2015训练；主结果5 bps；2016年以后未参与任何参数选择",
        summary_html=summary_html,
        notes=[
            "中心参数OAT每维保留前三个回撤优先值，随后完整运行最多3^7个联合case。",
            "F5在P24参数冻结后才运行，不能反向影响核心参数。",
            "这是训练集研究；代表若位于搜索边界，即使指标改善也不能直接晋级样本外。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ价格、代表均线与P24持仓区间", market_figure(indicators, daily), "market"),
            ReportFigure("performance-qqq", "B0/B2/B4/P24/P245与Buy & Hold", performance_figure(daily, benchmark), "performance"),
            ReportFigure("ablation-comparison", "因子消融与四段回撤", ablation_figure(formal, context.config["parameters"]["robustness_subwindows"]), "other"),
            ReportFigure("oat-responses", "七个参数的OAT响应（菱形为联合搜索保留值）", oat_figure(points), "other"),
            ReportFigure("joint-search-cloud", "联合搜索的收益—最差分段回撤分布", search_cloud(results, rep), "other"),
            ReportFigure("f5-ablation", "冻结P24后的QQQ/SPY相对强弱消融", f5_figure(f5), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(block_summary, center, rep, b0, f5)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nQQQ满仓F2/F4回撤优先训练与F5消融；详见 `report.html`。\n",
        encoding="utf-8",
    )
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "training_window": block_summary["training_window"],
        "case_counts": block_summary["case_counts"],
        "oat_connected_1pp_diagnostic_pass": block_summary["oat_connected_1pp_diagnostic_pass"],
        "joint_eligible_case_count": block_summary["joint_eligible_case_count"],
        "joint_one_pp_plateau_case_count": block_summary["joint_one_pp_plateau_case_count"],
        "representative_on_search_boundary": block_summary["representative_on_search_boundary"],
        "representative_boundary_parameters": block_summary["representative_boundary_parameters"],
        "promotion_gate_pass": promotion,
        "promotion_gate_reason": (
            "P24 meets drawdown, return, plateau, cost, and non-boundary gates."
            if promotion else "Training representative fails at least one predeclared promotion gate; see boundary and comparison fields."
        ),
        "f5_ablation_gate_pass": f5_gate,
        "f5_ablation_qualifying_lookbacks": (
            useful_f5["relative_strength_lookback"].astype(int).tolist() if f5_gate else []
        ),
        "center_p24": center.to_dict(),
        "representative": rep.to_dict(),
        "representative_b0": b0.to_dict(),
        "representative_zero_bps": zero_rep.to_dict(),
        "representative_b0_zero_bps": zero_b0.to_dict(),
        "worst_subwindow_drawdown_improvement_vs_b0_pct_points": improvement,
        "zero_bps_worst_subwindow_drawdown_improvement_vs_b0_pct_points": zero_improvement,
        "f5_ablation": f5.to_dict("records"),
        "benchmark": json.loads((primary / "summary.json").read_text(encoding="utf-8"))["benchmark"],
        "max_cross_check_differences": {
            "cost_5bps": json.loads((primary / "summary.json").read_text(encoding="utf-8"))["max_cross_check_differences"],
            "cost_0bps": json.loads((sensitivity / "summary.json").read_text(encoding="utf-8"))["max_cross_check_differences"],
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
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
        "backtest/scripts/run_full_position_trend_quality_training.py",
        "backtest/scripts/analyze_full_position_trend_quality_training.py",
        "backtest/report_templates/interactive_research_v4/page.html",
        "backtest/report_templates/interactive_research_v4/styles.css",
        "backtest/report_templates/interactive_research_v4/interactions.js",
        "data/processed/daily/QQQ.csv",
        "data/processed/daily/SPY.csv",
    ]
    for relative in source_paths:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifacts: dict[str, dict[str, Any]] = {}
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
