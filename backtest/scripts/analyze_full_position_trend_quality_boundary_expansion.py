#!/usr/bin/env python3
"""Build the v4 report for QQQ P24 four-dimensional boundary expansion."""

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
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.20b.2__26-08-15__qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015"
PARAMETER_LABELS = {
    "long_sma_window": "长期SMA L",
    "long_slope_lookback": "长期斜率回看 K",
    "long_slope_threshold_daily_pct": "F2门槛（%/日）",
    "short_sma_window": "短期SMA S",
}


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame) -> go.Figure:
    cases = [
        ("PARENT_P24", "父P24", "#64748b"),
        ("EXPANDED_B0", "扩边B0", "#94a3b8"),
        ("EXPANDED_B2", "扩边B2", "#2563eb"),
        ("EXPANDED_B4", "扩边B4", "#d97706"),
        ("EXPANDED_P24", "扩边P24", "#0f766e"),
        ("EXPANDED_P245_R120", "扩边P24+F5 R120", "#7c3aed"),
    ]
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.67, 0.33], subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    for case_id, label, color in cases:
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        for row, values, panel, legend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=legend, line={"color": color, "width": 2.2},
                    meta={"series_key": case_id, "panel": panel, "label": label},
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
        height=830, hovermode="x unified", showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55}, uirevision="tim-p24-expanded-performance",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(indicators: pd.DataFrame, daily: pd.DataFrame) -> go.Figure:
    frame = daily[daily["case_id"].eq("EXPANDED_P24")].sort_values("date")
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
        ("short_sma", "扩边代表短SMA", "#d97706"),
        ("long_sma", "扩边代表长期SMA", "#2563eb"),
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
            x=long_frame["date"], y=long_frame["close"], mode="markers", name="扩边P24持仓日",
            marker={"size": 3, "color": "#0f766e", "opacity": 0.45},
            meta={"series_key": "expanded_long_days", "panel": "market", "label": "扩边P24持仓日"},
        )
    )
    figure.update_layout(
        height=600, showlegend=False, yaxis_title="复权价格",
        xaxis={"rangeslider": {"visible": True}}, margin={"l": 65, "r": 25, "t": 45, "b": 55},
        uirevision="tim-p24-expanded-market",
    )
    return figure


def marginal_figure(results: pd.DataFrame, representative: pd.Series) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=2, vertical_spacing=0.14, horizontal_spacing=0.11,
        subplot_titles=list(PARAMETER_LABELS.values()),
    )
    for index, (parameter, label) in enumerate(PARAMETER_LABELS.items()):
        row, col = index // 2 + 1, index % 2 + 1
        for value, frame in results.groupby(parameter, sort=True):
            figure.add_trace(
                go.Box(
                    x=[value] * len(frame), y=frame["worst_subwindow_max_drawdown_pct"],
                    name=str(value), boxpoints=False, marker_color="#60a5fa", showlegend=False,
                    hovertemplate=f"{html.escape(label)}={value}<br>回撤=%{{y:.2f}}%<extra></extra>",
                ), row=row, col=col,
            )
        figure.add_vline(
            x=float(representative[parameter]), line_color="#dc2626", line_width=2, row=row, col=col
        )
        figure.update_yaxes(title_text="最差分段回撤 %", row=row, col=col)
    figure.update_layout(
        height=900, margin={"l": 70, "r": 25, "t": 80, "b": 55},
        uirevision="tim-p24-expanded-marginals",
    )
    return figure


def slice_heatmaps(results: pd.DataFrame, representative: pd.Series) -> go.Figure:
    first = results[
        results["long_slope_lookback"].eq(representative["long_slope_lookback"])
        & results["long_slope_threshold_daily_pct"].eq(representative["long_slope_threshold_daily_pct"])
    ]
    second = results[
        results["long_sma_window"].eq(representative["long_sma_window"])
        & results["short_sma_window"].eq(representative["short_sma_window"])
    ]
    table_one = first.pivot(index="short_sma_window", columns="long_sma_window", values="worst_subwindow_max_drawdown_pct")
    table_two = second.pivot(index="long_slope_threshold_daily_pct", columns="long_slope_lookback", values="worst_subwindow_max_drawdown_pct")
    figure = make_subplots(
        rows=1, cols=2, horizontal_spacing=0.13,
        subplot_titles=(
            f"固定K={int(representative.long_slope_lookback)}、F2={representative.long_slope_threshold_daily_pct:g}",
            f"固定L={int(representative.long_sma_window)}、S={int(representative.short_sma_window)}",
        ),
    )
    figure.add_trace(
        go.Heatmap(
            x=table_one.columns, y=table_one.index, z=table_one.values,
            colorscale="RdYlGn", colorbar={"title": "回撤 %", "x": 0.46},
            hovertemplate="L=%{x}<br>S=%{y}<br>最差分段回撤=%{z:.2f}%<extra></extra>",
        ), row=1, col=1,
    )
    figure.add_trace(
        go.Heatmap(
            x=table_two.columns, y=table_two.index, z=table_two.values,
            colorscale="RdYlGn", showscale=False,
            hovertemplate="K=%{x}<br>F2=%{y}<br>最差分段回撤=%{z:.2f}%<extra></extra>",
        ), row=1, col=2,
    )
    figure.update_xaxes(title_text="长期SMA L", row=1, col=1)
    figure.update_yaxes(title_text="短期SMA S", row=1, col=1)
    figure.update_xaxes(title_text="斜率回看 K", row=1, col=2)
    figure.update_yaxes(title_text="F2门槛 %/日", row=1, col=2)
    figure.update_layout(
        height=620, margin={"l": 70, "r": 25, "t": 75, "b": 60},
        uirevision="tim-p24-expanded-slices",
    )
    return figure


def search_cloud(results: pd.DataFrame, plateau: pd.DataFrame, representative: pd.Series) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=results["worst_subwindow_max_drawdown_pct"], y=results["cagr_pct"], mode="markers",
            name="扩边网格", marker={
                "size": 5, "opacity": 0.35, "color": results["ulcer_index_pct"],
                "colorscale": "Viridis_r", "colorbar": {"title": "Ulcer %"},
            },
            customdata=results[["case_id", "long_sma_window", "long_slope_lookback", "long_slope_threshold_daily_pct", "short_sma_window"]],
            hovertemplate=(
                "%{customdata[0]}<br>最差分段回撤=%{x:.2f}%<br>CAGR=%{y:.2f}%"
                "<br>L=%{customdata[1]} K=%{customdata[2]} F2=%{customdata[3]} S=%{customdata[4]}<extra></extra>"
            ),
        )
    )
    figure.add_trace(
        go.Scattergl(
            x=plateau["worst_subwindow_max_drawdown_pct"], y=plateau["cagr_pct"], mode="markers",
            name="1pp连通平台候选", marker={"size": 9, "color": "#f59e0b", "opacity": 0.85},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[representative["worst_subwindow_max_drawdown_pct"]], y=[representative["cagr_pct"]],
            mode="markers+text", text=["扩边代表"], textposition="top center",
            marker={"size": 16, "symbol": "diamond", "color": "#dc2626"}, showlegend=False,
        )
    )
    figure.add_hline(y=2.0, line_dash="dash", line_color="#64748b")
    figure.update_layout(
        height=700, xaxis_title="四窗最差最大回撤（越右越浅）", yaxis_title="CAGR %",
        margin={"l": 75, "r": 25, "t": 45, "b": 65}, uirevision="tim-p24-expanded-cloud",
    )
    return figure


def ablation_figure(formal: pd.DataFrame, subwindows: list[dict[str, str]]) -> go.Figure:
    cases = ["PARENT_P24", "EXPANDED_B0", "EXPANDED_B2", "EXPANDED_B4", "EXPANDED_P24", "EXPANDED_P245_R120"]
    labels = ["父P24", "B0", "B2", "B4", "扩边P24", "P24+F5"]
    chosen = formal.set_index("case_id").loc[cases]
    figure = make_subplots(rows=1, cols=2, subplot_titles=("完整期风险收益", "四个独立子窗口回撤"))
    figure.add_trace(go.Bar(x=labels, y=chosen["cagr_pct"], name="CAGR", marker_color="#2563eb"), row=1, col=1)
    figure.add_trace(go.Bar(x=labels, y=chosen["worst_subwindow_max_drawdown_pct"], name="最差分段回撤", marker_color="#dc2626"), row=1, col=1)
    colors = ["#0f766e", "#0891b2", "#7c3aed", "#d97706"]
    for index, item in enumerate(subwindows):
        figure.add_trace(
            go.Bar(x=labels, y=chosen[f"{item['window_id']}_max_drawdown_pct"], name=item["window_id"], marker_color=colors[index]),
            row=1, col=2,
        )
    figure.update_layout(
        height=620, barmode="group", margin={"l": 65, "r": 25, "t": 70, "b": 60},
        uirevision="tim-p24-expanded-ablation",
    )
    figure.update_yaxes(title_text="%")
    return figure


def format_parameters(row: pd.Series) -> str:
    return (
        f"L={int(row.long_sma_window)}, K={int(row.long_slope_lookback)}, "
        f"F2={row.long_slope_threshold_daily_pct:g}%/日，S={int(row.short_sma_window)}；"
        f"W=10、F4=0、C=2"
    )


def promotion_result(primary: dict[str, Any], zero: dict[str, Any], config: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    selection = config["parameters"]["selection"]
    rep = primary["representative"]
    if primary["representative_on_search_boundary"]:
        reasons.append("代表仍触及扩展边界")
    if int(primary["selected_component_cases"]) < int(selection["minimum_connected_component_cases"]):
        reasons.append("连通平台case数不足")
    if int(primary["selected_direct_plateau_neighbors"]) < int(selection["minimum_representative_plateau_neighbors"]):
        reasons.append("代表直接平台邻居不足")
    if float(rep["cagr_pct"]) < float(selection["minimum_cagr_pct"]):
        reasons.append("CAGR门禁失败")
    if float(primary["worst_subwindow_drawdown_improvement_vs_parent_pct_points"]) < -1.0:
        reasons.append("最差分段回撤比父代表恶化超过1pp")
    if float(primary["ulcer_index_improvement_vs_parent_pct_points"]) < -1.0:
        reasons.append("Ulcer Index比父代表恶化超过1pp")
    if float(zero["worst_subwindow_drawdown_improvement_vs_parent_pct_points"]) < -1.0:
        reasons.append("0bps下风险方向反转")
    return not reasons, reasons


def markdown_report(primary: dict[str, Any], zero: dict[str, Any], formal: pd.DataFrame, promotion: bool, reasons: list[str]) -> str:
    parent = formal[formal["case_id"].eq("PARENT_P24")].iloc[0]
    rep = formal[formal["case_id"].eq("EXPANDED_P24")].iloc[0]
    f5 = formal[formal["case_id"].eq("EXPANDED_P245_R120")].iloc[0]
    return f"""# QQQ P24 四维边界扩展：2000–2015

## 本次测试

- 完全保留父实验P24的0%/100%仓位、Close确认、下一Open、W10/F4门槛0/C2和2000-03-17共同起点。
- 只扩展四个触边参数：L=120–200、K=1–20、F2门槛0.01%–0.05%/日、S=8–30，共4,032组；K=1是合法自然下限。
- 5 bps选择代表；0 bps只复跑冻结代表。F5只在新P24冻结后固定R120消融。

## 5 bps结果

| 方案 | 参数 | CAGR | 最大回撤 | 四窗最差回撤 | Ulcer | 持仓率 |
|---|---|---:|---:|---:|---:|---:|
| 父P24 | {format_parameters(parent)} | {parent.cagr_pct:.2f}% | {parent.max_drawdown_pct:.2f}% | {parent.worst_subwindow_max_drawdown_pct:.2f}% | {parent.ulcer_index_pct:.2f}% | {parent.exposure_pct:.2f}% |
| 扩边P24 | {format_parameters(rep)} | {rep.cagr_pct:.2f}% | {rep.max_drawdown_pct:.2f}% | {rep.worst_subwindow_max_drawdown_pct:.2f}% | {rep.ulcer_index_pct:.2f}% | {rep.exposure_pct:.2f}% |
| 扩边P24+F5 R120 | 固定扩边P24参数 | {f5.cagr_pct:.2f}% | {f5.max_drawdown_pct:.2f}% | {f5.worst_subwindow_max_drawdown_pct:.2f}% | {f5.ulcer_index_pct:.2f}% | {f5.exposure_pct:.2f}% |

1pp平台共{primary['one_pp_plateau_case_count']}个case、{primary['component_count']}个连通分量；代表所在分量{primary['selected_component_cases']}个case，直接邻居{primary['selected_direct_plateau_neighbors']}个。代表边界参数：{', '.join(primary['representative_boundary_parameters']) if primary['representative_boundary_parameters'] else '无'}。

## 研究判断

- 2016–2026锁定测试门禁：**{'通过' if promotion else '未通过'}**。
- {'全部预声明稳定性与风险门禁通过，可以另建锁定测试实验。' if promotion else '未通过原因：' + '；'.join(reasons) + '。'}
- 0 bps只用于成本方向检查，不参与参数选择；当前固定代表在0 bps下四窗最差回撤为 {zero['representative']['worst_subwindow_max_drawdown_pct']:.2f}%。
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
    primary_root = block_root(context, args.run_id, "QQQ", float(context.config["primary_cost_bps_per_side"]))
    zero_root = block_root(context, args.run_id, "QQQ", 0)
    primary = json.loads((primary_root / "summary.json").read_text(encoding="utf-8"))
    zero = json.loads((zero_root / "summary.json").read_text(encoding="utf-8"))
    formal = pd.read_csv(primary_root / "formal_cases.csv")
    formal_zero = pd.read_csv(zero_root / "formal_cases.csv")
    daily = pd.read_csv(primary_root / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(primary_root / "buy_hold_daily.csv", parse_dates=["date"])
    indicators = pd.read_csv(primary_root / "representative_indicators.csv", parse_dates=["date"])
    results = pd.read_csv(primary_root / "parameter_results.csv")
    plateau = pd.read_csv(primary_root / "plateau_results.csv")
    representative = formal[formal["case_id"].eq("EXPANDED_P24")].iloc[0]
    parent = formal[formal["case_id"].eq("PARENT_P24")].iloc[0]
    f5 = formal[formal["case_id"].eq("EXPANDED_P245_R120")].iloc[0]
    promotion, reasons = promotion_result(primary, zero, context.config)
    f5_gate = (
        float(f5["worst_subwindow_max_drawdown_pct"]) > float(representative["worst_subwindow_max_drawdown_pct"])
        and float(f5["ulcer_index_pct"]) < float(representative["ulcer_index_pct"])
        and float(f5["cagr_pct"]) > 0.0
    )
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>扩边case</h3><p class="metric">{primary["grid_case_count"]:,}</p><p>四维完整联合网格</p></article>'
        f'<article><h3>最差分段回撤</h3><p class="metric">{parent.worst_subwindow_max_drawdown_pct:.2f}% → {representative.worst_subwindow_max_drawdown_pct:.2f}%</p><p>变化 {primary["worst_subwindow_drawdown_improvement_vs_parent_pct_points"]:+.2f}pp</p></article>'
        f'<article><h3>平台结构</h3><p class="metric">{primary["selected_component_cases"]} / {primary["selected_direct_plateau_neighbors"]}</p><p>分量case / 直接邻居</p></article>'
        f'<article><h3>晋级判断</h3><p class="metric">{"通过" if promotion else "未通过"}</p><p>{"可另建锁定测试" if promotion else html.escape("；".join(reasons))}</p></article>'
        '</div>'
        f'<p><strong>扩边代表：</strong>{html.escape(format_parameters(representative))}</p>'
        f'<p><strong>边界：</strong>{html.escape(", ".join(primary["representative_boundary_parameters"]) if primary["representative_boundary_parameters"] else "无")}；'
        f'<strong>F5 R120：</strong>{"通过固定消融双风险门禁" if f5_gate else "未通过固定消融双风险门禁"}。</p>'
    )
    report = render_interactive_report(
        title="QQQ P24 四维边界扩展 2000–2015",
        heading="QQQ满仓F2/F4：四个触边参数的扩大搜索与连通平台检验",
        subtitle="与父实验保持同一起点；主结果5 bps；2016年以后未参与任何选择",
        summary_html=summary_html,
        notes=[
            "3,024组来自四个触边维度的完整笛卡尔积，其他参数固定为父代表。",
            "1pp平台使用四维单轴相邻索引定义Manhattan连通；非边界点优先。",
            "F5固定R120在P24代表冻结后才执行，不参与扩边选参。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ价格、扩边代表均线与持仓区间", market_figure(indicators, daily), "market"),
            ReportFigure("performance-qqq", "父P24、扩边消融、F5与Buy & Hold", performance_figure(daily, benchmark), "performance"),
            ReportFigure("ablation-comparison", "父子策略与四段回撤", ablation_figure(formal, context.config["parameters"]["robustness_subwindows"]), "other"),
            ReportFigure("expanded-slices", "扩边代表所在的两张二维风险切片", slice_heatmaps(results, representative), "other"),
            ReportFigure("expanded-marginals", "四个扩边参数的全联合边际分布", marginal_figure(results, representative), "other"),
            ReportFigure("expanded-search-cloud", "3,024组收益—最差分段回撤与1pp平台", search_cloud(results, plateau, representative), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(primary, zero, formal, promotion, reasons)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nQQQ P24四维边界扩展；详见 `report.html`。\n",
        encoding="utf-8",
    )
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "training_window": primary["training_window"],
        "grid_case_count": primary["grid_case_count"],
        "eligible_case_count": primary["eligible_case_count"],
        "one_pp_plateau_case_count": primary["one_pp_plateau_case_count"],
        "component_count": primary["component_count"],
        "selected_component_cases": primary["selected_component_cases"],
        "selected_direct_plateau_neighbors": primary["selected_direct_plateau_neighbors"],
        "representative_on_search_boundary": primary["representative_on_search_boundary"],
        "representative_boundary_parameters": primary["representative_boundary_parameters"],
        "promotion_gate_pass": promotion,
        "promotion_gate_failures": reasons,
        "f5_ablation_gate_pass": f5_gate,
        "parent": parent.to_dict(),
        "representative": representative.to_dict(),
        "fixed_f5_ablation": f5.to_dict(),
        "representative_zero_bps": formal_zero[formal_zero["case_id"].eq("EXPANDED_P24")].iloc[0].to_dict(),
        "parent_zero_bps": formal_zero[formal_zero["case_id"].eq("PARENT_P24")].iloc[0].to_dict(),
        "risk_deltas": {
            "five_bps_worst_subwindow_drawdown_improvement_vs_parent_pct_points": primary["worst_subwindow_drawdown_improvement_vs_parent_pct_points"],
            "five_bps_ulcer_index_improvement_vs_parent_pct_points": primary["ulcer_index_improvement_vs_parent_pct_points"],
            "zero_bps_worst_subwindow_drawdown_improvement_vs_parent_pct_points": zero["worst_subwindow_drawdown_improvement_vs_parent_pct_points"],
            "zero_bps_ulcer_index_improvement_vs_parent_pct_points": zero["ulcer_index_improvement_vs_parent_pct_points"],
        },
        "benchmark": primary["benchmark"],
        "max_cross_check_differences": {
            "cost_5bps": primary["max_cross_check_differences"],
            "cost_0bps": zero["max_cross_check_differences"],
        },
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
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
        "backtest/quantkit/trend_quality_boundary.py",
        "backtest/scripts/run_full_position_trend_quality_training.py",
        "backtest/scripts/run_full_position_trend_quality_boundary_expansion.py",
        "backtest/scripts/analyze_full_position_trend_quality_boundary_expansion.py",
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
