#!/usr/bin/env python3
"""Build the v5 HTML/PDF report for the P24 stricter-threshold grid."""

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

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_sma_regime_ablation import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.40b.1__26-08-28__qqq_p24_stricter_threshold_grid_2000_2015"
ORIGINAL_CASE = "F2_020_F4_000"


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def label(row: pd.Series | Any) -> str:
    return f"F2>{float(row.long_slope_threshold_daily_pct):.2f}%，F4>{float(row.short_quality_threshold_daily_pct):.2f}%"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    results: pd.DataFrame,
    representative_case: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.67, 0.33],
        subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    palette = {ORIGINAL_CASE: "#64748b", representative_case: "#0f766e"}
    for metric in results.sort_values(
        ["long_slope_threshold_daily_pct", "short_quality_threshold_daily_pct"]
    ).itertuples(index=False):
        case_id = str(metric.case_id)
        frame = daily[daily["case_id"].eq(case_id)].sort_values("date")
        visible: bool | str = True if case_id in {ORIGINAL_CASE, representative_case} else "legendonly"
        color = palette.get(case_id, "#93c5fd")
        case_label = label(metric)
        for row, values, panel in (
            (1, frame["equity"], "equity"),
            (2, drawdown(frame["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scattergl(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=case_label,
                    line={"color": color, "width": 2.1 if case_id in palette else 1.1},
                    visible=visible,
                    meta={
                        "series_key": case_id.lower(),
                        "panel": panel,
                        "label": case_label,
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
        uirevision="rot-p24-stricter-performance",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(indicators: pd.DataFrame, daily: pd.DataFrame, representative_case: str) -> go.Figure:
    held = daily[
        daily["case_id"].eq(representative_case) & daily["is_long"].astype(bool)
    ]
    held_dates = set(pd.to_datetime(held["date"]))
    long_frame = indicators[pd.to_datetime(indicators["date"]).isin(held_dates)]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.07,
        subplot_titles=("QQQ、SMA180、SMA20与代表持仓", "长期与短期趋势读数"),
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
    for column, text, color in (
        ("long_sma", "SMA180", "#2563eb"),
        ("short_sma", "SMA20", "#d97706"),
    ):
        figure.add_trace(
            go.Scatter(
                x=indicators["date"],
                y=indicators[column],
                mode="lines",
                name=text,
                line={"color": color, "width": 1.3},
                meta={"series_key": column, "panel": "market", "label": text},
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scattergl(
            x=long_frame["date"],
            y=long_frame["close"],
            mode="markers",
            name="代表持仓日",
            marker={"size": 3, "color": "#0f766e", "opacity": 0.4},
            meta={"series_key": "representative_long", "panel": "market", "label": "代表持仓日"},
        ),
        row=1,
        col=1,
    )
    for column, text, color in (
        ("long_slope_daily_pct", "SMA180十日平均涨幅", "#2563eb"),
        ("short_quality_daily_pct", "SMA20十日趋势质量", "#d97706"),
    ):
        figure.add_trace(
            go.Scatter(
                x=indicators["date"],
                y=indicators[column],
                mode="lines",
                name=text,
                line={"color": color, "width": 1.3},
                meta={"series_key": column, "panel": "indicator", "label": text},
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        height=840,
        showlegend=False,
        xaxis2={"rangeslider": {"visible": True}},
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision="rot-p24-stricter-market",
    )
    figure.update_yaxes(title_text="复权价格", row=1, col=1)
    figure.update_yaxes(title_text="%/日", row=2, col=1)
    return figure


def grid_heatmaps(results: pd.DataFrame, representative: pd.Series) -> go.Figure:
    metrics = (
        ("worst_subwindow_max_drawdown_pct", "四窗最差最大回撤", "RdYlGn"),
        ("cagr_pct", "日历CAGR", "Viridis"),
        ("holding_cagr_pct", "持仓CAGR（诊断）", "Viridis"),
    )
    figure = make_subplots(rows=1, cols=3, horizontal_spacing=0.1, subplot_titles=[item[1] for item in metrics])
    for index, (metric, _, colorscale) in enumerate(metrics, start=1):
        table = results.pivot(
            index="short_quality_threshold_daily_pct",
            columns="long_slope_threshold_daily_pct",
            values=metric,
        )
        figure.add_trace(
            go.Heatmap(
                x=table.columns,
                y=table.index,
                z=table.values,
                colorscale=colorscale,
                showscale=index == 3,
                colorbar={"title": "%"} if index == 3 else None,
                text=table.round(2).astype(str).values,
                texttemplate="%{text}",
                hovertemplate="F2>%{x:.2f}%<br>F4>%{y:.2f}%<br>结果=%{z:.2f}%<extra></extra>",
            ),
            row=1,
            col=index,
        )
        figure.add_trace(
            go.Scatter(
                x=[representative["long_slope_threshold_daily_pct"]],
                y=[representative["short_quality_threshold_daily_pct"]],
                mode="markers",
                marker={"size": 15, "symbol": "diamond-open", "color": "#111827"},
                showlegend=False,
            ),
            row=1,
            col=index,
        )
        figure.update_xaxes(title_text="F2长期门槛 %/日", row=1, col=index)
        figure.update_yaxes(title_text="F4短期门槛 %/日" if index == 1 else "", row=1, col=index)
    figure.update_layout(
        height=560,
        margin={"l": 70, "r": 65, "t": 75, "b": 65},
        uirevision="rot-p24-stricter-grid",
    )
    return figure


def holding_figure(results: pd.DataFrame, representative_case: str) -> go.Figure:
    colors = [
        "#0f766e" if case_id == representative_case else "#64748b" if case_id == ORIGINAL_CASE else "#93c5fd"
        for case_id in results["case_id"]
    ]
    figure = go.Figure(
        go.Scatter(
            x=results["holding_sessions"],
            y=results["holding_cagr_pct"],
            mode="markers",
            marker={"size": 13, "color": colors},
            customdata=results[
                [
                    "case_id",
                    "long_slope_threshold_daily_pct",
                    "short_quality_threshold_daily_pct",
                    "exposure_pct",
                    "cagr_pct",
                    "max_drawdown_pct",
                ]
            ],
            hovertemplate=(
                "%{customdata[0]}<br>F2>%{customdata[1]:.2f}% F4>%{customdata[2]:.2f}%"
                "<br>持仓日=%{x}<br>持仓率=%{customdata[3]:.2f}%<br>持仓CAGR=%{y:.2f}%"
                "<br>日历CAGR=%{customdata[4]:.2f}%<br>最大回撤=%{customdata[5]:.2f}%<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=580,
        xaxis_title="实际持仓交易日",
        yaxis_title="持仓CAGR %（仅诊断）",
        margin={"l": 75, "r": 25, "t": 45, "b": 65},
        uirevision="rot-p24-stricter-holding",
    )
    return figure


def result_table(results: pd.DataFrame, representative_case: str) -> str:
    ordered = results.sort_values(
        ["long_slope_threshold_daily_pct", "short_quality_threshold_daily_pct"]
    )
    rows = []
    for row in ordered.itertuples(index=False):
        role = "代表" if row.case_id == representative_case else "原P24" if row.case_id == ORIGINAL_CASE else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(role)}</td><td>{row.long_slope_threshold_daily_pct:.2f}%</td>"
            f"<td>{row.short_quality_threshold_daily_pct:.2f}%</td><td>{row.cagr_pct:.2f}%</td>"
            f"<td>{row.max_drawdown_pct:.2f}%</td><td>{row.worst_subwindow_max_drawdown_pct:.2f}%</td>"
            f"<td>{int(row.holding_sessions)}</td><td>{row.exposure_pct:.2f}%</td>"
            f"<td>{row.holding_cagr_pct:.2f}%</td><td>{int(row.closed_trade_count)}</td>"
            "</tr>"
        )
    return (
        '<div style="overflow-x:auto"><table><thead><tr>'
        "<th>标记</th><th>F2长期门槛</th><th>F4短期门槛</th><th>日历CAGR</th>"
        "<th>最大回撤</th><th>四窗最差回撤</th><th>持仓日</th><th>持仓率</th><th>持仓CAGR</th><th>闭合交易</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def promotion_result(primary: dict[str, Any], zero: dict[str, Any], config: dict[str, Any]) -> tuple[bool, list[str]]:
    selection = config["parameters"]["selection"]
    reasons = []
    if primary["representative_on_search_boundary"]:
        reasons.append("代表触及门槛网格边界")
    if int(primary["selected_component_cases"]) < int(selection["minimum_connected_component_cases"]):
        reasons.append("1pp连通平台case数不足")
    if int(primary["selected_direct_plateau_neighbors"]) < int(selection["minimum_representative_plateau_neighbors"]):
        reasons.append("代表直接平台邻居不足")
    if float(primary["representative"]["cagr_pct"]) <= 0.0:
        reasons.append("5bps日历CAGR不为正")
    if float(zero["representative"]["cagr_pct"]) <= 0.0:
        reasons.append("0bps日历CAGR方向反转")
    return not reasons, reasons


def markdown_report(
    primary: dict[str, Any], results: pd.DataFrame, representative: pd.Series, original: pd.Series,
    promotion: bool, reasons: list[str]
) -> str:
    objective_pass = float(representative.max_drawdown_pct) >= float(original.max_drawdown_pct)
    table = results.sort_values(
        ["long_slope_threshold_daily_pct", "short_quality_threshold_daily_pct"]
    )
    lines = [
        "| F2长期门槛 | F4短期门槛 | 日历CAGR | 最大回撤 | 持仓日 | 持仓率 | 持仓CAGR |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in table.itertuples(index=False):
        lines.append(
            f"| {row.long_slope_threshold_daily_pct:.2f}% | {row.short_quality_threshold_daily_pct:.2f}% | "
            f"{row.cagr_pct:.2f}% | {row.max_drawdown_pct:.2f}% | {int(row.holding_sessions)} | "
            f"{row.exposure_pct:.2f}% | {row.holding_cagr_pct:.2f}% |"
        )
    return f"""# QQQ P24更严格门槛：2000–2015

## 本次测试

- 固定SMA180、长期斜率10日回看、SMA20、短趋势10日回归和连续2日确认，只扫描F2与F4两个门槛。
- F2测试0.02%至0.05%每日，F4测试0至0.10%每日，共16组；0.02%与0是原P24对照。
- 5 bps为主结果；2016年以后未读取。

## 代表与原P24

| 方案 | F2 | F4 | 日历CAGR | 最大回撤 | 四窗最差回撤 | 持仓日 | 持仓率 | 持仓CAGR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 原P24 | {original.long_slope_threshold_daily_pct:.2f}% | {original.short_quality_threshold_daily_pct:.2f}% | {original.cagr_pct:.2f}% | {original.max_drawdown_pct:.2f}% | {original.worst_subwindow_max_drawdown_pct:.2f}% | {int(original.holding_sessions)} | {original.exposure_pct:.2f}% | {original.holding_cagr_pct:.2f}% |
| 训练代表 | {representative.long_slope_threshold_daily_pct:.2f}% | {representative.short_quality_threshold_daily_pct:.2f}% | {representative.cagr_pct:.2f}% | {representative.max_drawdown_pct:.2f}% | {representative.worst_subwindow_max_drawdown_pct:.2f}% | {int(representative.holding_sessions)} | {representative.exposure_pct:.2f}% | {representative.holding_cagr_pct:.2f}% |

## 16组完整结果（5 bps）

{chr(10).join(lines)}

## 持仓口径

- 持仓日是逐日账本中开盘成交后仍持有QQQ的交易日；持仓率为持仓日除以{primary['training_window']['bars']}个共同交易日。
- 持仓CAGR = `(期末净值/初始资金)^(252/持仓日)-1`。它把空仓日压缩掉，容易偏爱低持仓策略，因此只作资金效率诊断，不参与选参。
- 正式收益仍以覆盖全部日历时间的日历CAGR为准。

## 研究判断

- 预注册的2016–2026机械门禁：**{'通过' if promotion else '未通过'}**。{'参数平台、边界、活动度与成本方向门禁全部通过。' if promotion else '未通过原因：' + '；'.join(reasons) + '。'}
- 用户的核心目标“降低完整期回撤”：**{'通过' if objective_pass else '未通过'}**。代表完整期最大回撤为{representative.max_drawdown_pct:.2f}%，原P24为{original.max_drawdown_pct:.2f}%；代表Ulcer为{representative.ulcer_index_pct:.2f}%，原P24为{original.ulcer_index_pct:.2f}%。
- 机械门禁通过不等于策略值得采用：本轮主指标使用独立重置子窗口回撤，未约束跨窗口水下路径。完整期回撤与Ulcer同时恶化，因此本报告不建议据此打开2016–2026锁定期；如要继续，应先在新的冻结实验中修正研究门禁。
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
    primary_root = block_root(context, args.run_id, "QQQ", 5)
    zero_root = block_root(context, args.run_id, "QQQ", 0)
    primary = json.loads((primary_root / "summary.json").read_text(encoding="utf-8"))
    zero = json.loads((zero_root / "summary.json").read_text(encoding="utf-8"))
    results = pd.read_csv(primary_root / "formal_cases.csv")
    results_zero = pd.read_csv(zero_root / "formal_cases.csv")
    daily = pd.read_csv(primary_root / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(primary_root / "buy_hold_daily.csv", parse_dates=["date"])
    indicators = pd.read_csv(primary_root / "representative_indicators.csv", parse_dates=["date"])
    representative_case = str(primary["selection_source_case_id"])
    representative = results[results["case_id"].eq(representative_case)].iloc[0]
    original = results[results["case_id"].eq(ORIGINAL_CASE)].iloc[0]
    promotion, reasons = promotion_result(primary, zero, context.config)
    objective_pass = float(representative.max_drawdown_pct) >= float(original.max_drawdown_pct)

    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>训练代表</h3><p class="metric">F2 {representative.long_slope_threshold_daily_pct:.2f}% · F4 {representative.short_quality_threshold_daily_pct:.2f}%</p><p>5 bps二维平台选择</p></article>'
        f'<article><h3>日历CAGR</h3><p class="metric">{original.cagr_pct:.2f}% → {representative.cagr_pct:.2f}%</p><p>覆盖全部2000–2015时间</p></article>'
        f'<article><h3>最大回撤</h3><p class="metric">{original.max_drawdown_pct:.2f}% → {representative.max_drawdown_pct:.2f}%</p><p>四窗最差 {representative.worst_subwindow_max_drawdown_pct:.2f}%</p></article>'
        f'<article><h3>持仓效率</h3><p class="metric">{int(representative.holding_sessions)}日 · {representative.holding_cagr_pct:.2f}%</p><p>持仓CAGR仅为诊断</p></article>'
        '</div>'
        f'<p><strong>核心目标判断：</strong>{"通过" if objective_pass else "未通过"}。'
        f'代表的完整期最大回撤为{representative.max_drawdown_pct:.2f}%，原P24为{original.max_drawdown_pct:.2f}%；'
        f'机械平台门禁虽为{"通过" if promotion else "未通过"}，但不能掩盖跨窗口完整路径回撤的恶化。</p>'
        '<h3>16组完整结果（5 bps）</h3>'
        + result_table(results, representative_case)
    )
    report = render_interactive_report(
        title="QQQ P24更严格长短趋势门槛 2000–2015",
        heading="QQQ满仓P24：F2×F4严格门槛16组测试",
        subtitle="固定其余语义；主结果5 bps；同时报告实际持仓日、持仓率和持仓CAGR",
        summary_html=summary_html,
        notes=[
            "原P24是F2严格高于0.02%每日且F4严格高于0；本轮只把这两个门槛向更严格方向扩展。",
            "代表先看四个独立子窗口中最差最大回撤，再看二维1个百分点连通平台和Ulcer等风险指标；持仓CAGR不参与选择。",
            "持仓CAGR按252个持仓交易日折算，包含交易成本与全部账户收益，但压缩掉空仓日，不能替代正式日历CAGR。",
            "全部16组在0/5 bps下均由PyBroker与独立逐日账本核对；QQQ仓位始终为0%或100%。",
            "本轮仅使用2000-03-17至2015-12-31；若门禁通过，2016年以后只能在另一个冻结实验中打开。",
            "本轮预注册机械门禁没有要求完整期最大回撤不得恶化；报告将机械门禁与用户的控制回撤目标分开判断，不事后改选代表。",
        ],
        figures=[
            ReportFigure(
                "market-qqq",
                "QQQ、长短均线、代表持仓与趋势读数",
                market_figure(indicators, daily, representative_case),
                "market",
            ),
            ReportFigure(
                "performance-qqq",
                "16组门槛、原P24、代表与Buy & Hold净值",
                performance_figure(daily, benchmark, results, representative_case),
                "performance",
            ),
            ReportFigure(
                "threshold-grid",
                "F2×F4门槛曲面：回撤、日历CAGR与持仓CAGR",
                grid_heatmaps(results, representative),
                "other",
            ),
            ReportFigure(
                "holding-efficiency",
                "实际持仓交易日与持仓CAGR",
                holding_figure(results, representative_case),
                "other",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(primary, results, representative, original, promotion, reasons)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nQQQ P24 F2×F4更严格门槛16组测试；详见 `report.html`、`report.pdf` 与 `report.md`。\n",
        encoding="utf-8",
    )
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    results[
        [
            "case_id", "long_slope_threshold_daily_pct", "short_quality_threshold_daily_pct",
            "cagr_pct", "max_drawdown_pct", "worst_subwindow_max_drawdown_pct",
            "holding_sessions", "holding_years_252", "exposure_pct", "holding_cagr_pct",
            "closed_trade_count", "ulcer_index_pct",
        ]
    ].to_csv(analysis_root / "holding_and_performance_5bps.csv", index=False, lineterminator="\n")
    results_zero[
        [
            "case_id", "long_slope_threshold_daily_pct", "short_quality_threshold_daily_pct",
            "cagr_pct", "max_drawdown_pct", "holding_sessions", "exposure_pct", "holding_cagr_pct",
        ]
    ].to_csv(analysis_root / "holding_and_performance_0bps.csv", index=False, lineterminator="\n")
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "training_window": primary["training_window"],
        "grid_case_count": primary["grid_case_count"],
        "representative_on_search_boundary": primary["representative_on_search_boundary"],
        "representative_boundary_parameters": primary["representative_boundary_parameters"],
        "selected_component_cases": primary["selected_component_cases"],
        "selected_direct_plateau_neighbors": primary["selected_direct_plateau_neighbors"],
        "promotion_gate_pass": promotion,
        "promotion_gate_failures": reasons,
        "economic_drawdown_objective_pass": objective_pass,
        "economic_drawdown_objective_note": (
            "代表完整期最大回撤与Ulcer均优于或等于原P24"
            if objective_pass
            else "机械平台门禁通过，但代表完整期最大回撤与Ulcer均差于原P24，不建议打开锁定期"
        ),
        "original_p24": json_safe(original.to_dict()),
        "representative": json_safe(representative.to_dict()),
        "representative_zero_bps": json_safe(results_zero[results_zero["case_id"].eq(representative_case)].iloc[0].to_dict()),
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

    pdf_process = subprocess.run(
        [
            "node", "scripts/print_html_pdf.mjs", str(run_root / "report_print.html"),
            str(run_root / "report.pdf"), "什么时候买", "什么时候卖", "信号如何变成成交", "持仓CAGR",
        ],
        cwd=BACKTEST_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if pdf_process.returncode != 0:
        raise RuntimeError("PDF generation failed:\n" + "\n".join([pdf_process.stdout, pdf_process.stderr]))
    pdf_payload = json.loads(pdf_process.stdout.split("\n", 1)[1])
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
        "backtest/quantkit/full_position_trend_quality.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_p24_stricter_threshold_grid.py",
        "backtest/scripts/analyze_p24_stricter_threshold_grid.py",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
        "data/processed/daily/SPY.csv",
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
