#!/usr/bin/env python3
"""Build the report for QQQ R1+R3 parameter training on 2000-2015."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

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


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.20a.1__26-08-14__qqq_sma_r1_r3_parameter_training_2000_2015"


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def performance_figure(
    daily: pd.DataFrame, benchmark: pd.DataFrame, formal: pd.DataFrame
) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.68, 0.32], subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    colors = {"FROZEN_BASELINE": "#64748b", "TRAINING_PLATEAU_REPRESENTATIVE": "#0f766e"}
    labels = {"FROZEN_BASELINE": "R1+R3原参数", "TRAINING_PLATEAU_REPRESENTATIVE": "训练平台代表"}
    for record in formal.itertuples(index=False):
        frame = daily[daily["case_id"].eq(record.case_id)].sort_values("date")
        reason = str(record.selection_reason)
        label = labels[reason]
        for row, values, panel, legend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=legend, line={"color": colors[reason], "width": 2.8},
                    meta={"series_key": str(record.case_id), "panel": panel, "label": label},
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
                showlegend=legend, line={"color": "#111827", "width": 2, "dash": "dash"},
                meta={
                    "series_key": "buy_hold", "panel": panel, "label": "QQQ Buy & Hold",
                    "is_benchmark": panel == "equity", "cost_bps": 0,
                },
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                    if row == 1 else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                ),
            ), row=row, col=1,
        )
    figure.update_layout(
        height=790, margin={"l": 65, "r": 25, "t": 65, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="r1-r3-training-performance",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def market_figure(
    daily: pd.DataFrame, formal: pd.DataFrame, indicators: pd.DataFrame
) -> go.Figure:
    representative_id = str(
        formal[formal["selection_reason"].eq("TRAINING_PLATEAU_REPRESENTATIVE")].iloc[0]["case_id"]
    )
    frame = daily[daily["case_id"].eq(representative_id)].sort_values("date")
    market_indicators = indicators[indicators["date"].isin(frame["date"])].sort_values("date")
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=market_indicators["date"], open=market_indicators["open"], high=market_indicators["high"],
            low=market_indicators["low"], close=market_indicators["close"], name="QQQ复权OHLC",
            increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
        )
    )
    for column, label, color in (
        ("sma30", "SMA30", "#d97706"),
        ("sma200", "SMA200", "#2563eb"),
    ):
        figure.add_trace(
            go.Scatter(
                x=market_indicators["date"], y=market_indicators[column], mode="lines",
                name=label, line={"color": color, "width": 1.3},
                meta={"series_key": column, "panel": "market", "label": label},
            )
        )
    long_frame = frame[frame["is_long"].astype(bool)]
    figure.add_trace(
        go.Scatter(
            x=long_frame["date"], y=long_frame["close"], mode="markers", name="平台代表持仓日",
            marker={"color": "#0f766e", "size": 3, "opacity": 0.5},
            meta={"series_key": "long_days", "panel": "market", "label": "平台代表持仓日"},
        )
    )
    figure.update_layout(
        height=560, margin={"l": 65, "r": 25, "t": 45, "b": 55},
        xaxis={"rangeslider": {"visible": True}}, yaxis_title="复权价格",
        showlegend=False, uirevision="r1-r3-training-market",
    )
    return figure


def oat_figure(points: pd.DataFrame) -> go.Figure:
    sweeps = list(dict.fromkeys(points["sweep_id"].astype(str)))
    figure = make_subplots(
        rows=4, cols=2, vertical_spacing=0.11, horizontal_spacing=0.11,
        subplot_titles=[str(points[points["sweep_id"].eq(item)].iloc[0]["parameter_label"]) for item in sweeps],
    )
    for index, sweep in enumerate(sweeps):
        row, col = index // 2 + 1, index % 2 + 1
        frame = points[points["sweep_id"].eq(sweep)].sort_values("point_order")
        figure.add_trace(
            go.Scatter(
                x=frame["value"], y=frame["max_drawdown_pct"], mode="lines+markers",
                name="最大回撤", line={"color": "#dc2626", "width": 2},
                hovertemplate="参数=%{x}<br>最大回撤=%{y:.2f}%<extra></extra>",
            ), row=row, col=col,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["value"], y=frame["total_return_pct"], mode="lines+markers",
                name="总收益", line={"color": "#2563eb", "width": 1.6, "dash": "dot"},
                hovertemplate="参数=%{x}<br>总收益=%{y:.2f}%<extra></extra>",
            ), row=row, col=col,
        )
        figure.update_xaxes(title_text="参数值", row=row, col=col)
        figure.update_yaxes(title_text="回撤/收益 %", row=row, col=col)
    figure.update_layout(
        height=1300, margin={"l": 65, "r": 25, "t": 80, "b": 60},
        showlegend=False, uirevision="r1-r3-training-oat",
    )
    return figure


def search_cloud(results: pd.DataFrame, formal: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for stage, color in (
        ("stage_1_oat", "#94a3b8"),
        ("stage_2_local", "#60a5fa"),
        ("stage_3_joint", "#f59e0b"),
    ):
        frame = results[results["stage"].eq(stage)]
        figure.add_trace(
            go.Scattergl(
                x=frame["max_drawdown_pct"], y=frame["total_return_pct"],
                mode="markers", name=stage,
                marker={"color": color, "size": 5, "opacity": 0.45},
                customdata=frame[["case_id", "long_window", "r3_sell_buffer_pct"]],
                hovertemplate=(
                    "%{customdata[0]}<br>最大回撤=%{x:.2f}%<br>总收益=%{y:.2f}%"
                    "<br>长均线=%{customdata[1]}<br>R3缓冲=%{customdata[2]}%<extra></extra>"
                ),
            )
        )
    for record in formal.itertuples(index=False):
        figure.add_trace(
            go.Scatter(
                x=[record.max_drawdown_pct], y=[record.total_return_pct], mode="markers+text",
                text=["原参数" if record.selection_reason == "FROZEN_BASELINE" else "平台代表"],
                textposition="top center", showlegend=False,
                marker={"size": 15, "symbol": "diamond", "color": "#111827"},
                hovertemplate="最大回撤=%{x:.2f}%<br>总收益=%{y:.2f}%<extra></extra>",
            )
        )
    figure.update_layout(
        height=700, xaxis_title="最大回撤（越右越浅）", yaxis_title="总收益 %",
        margin={"l": 70, "r": 25, "t": 45, "b": 65},
        uirevision="r1-r3-training-cloud",
    )
    return figure


def markdown_report(summary: dict, formal: pd.DataFrame) -> str:
    baseline = formal[formal["selection_reason"].eq("FROZEN_BASELINE")].iloc[0]
    rep = formal[formal["selection_reason"].eq("TRAINING_PLATEAU_REPRESENTATIVE")].iloc[0]
    return f"""# QQQ R1+R3 参数训练：2000–2015

## 训练设计

- 训练数据仅为 2000-01-03 至 2015-12-31；2016年以后没有参与选参。
- 共保存 {sum(summary['case_counts'].values()):,} 个case：OAT {summary['case_counts']['stage_1_oat']:,}、局部联合 {summary['case_counts']['stage_2_local']:,}、最终联合 {summary['case_counts']['stage_3_joint']:,}。
- 最大回撤优先；总收益至少为基线70%，订单不超过140且至少10笔闭合交易。

## 训练结果

| 方案 | 短线 | 长线 | 买入宽限(短/长) | R1(日数/日降幅) | R3缓冲 | 总收益 | CAGR | Sharpe | 最大回撤 | 订单 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 原参数 | 25/30/35 | {int(baseline.long_window)} | {baseline.buy_short_buffer_pct:.2f}%/{baseline.buy_long_buffer_pct:.2f}% | {int(baseline.r1_decline_days)}/{baseline.r1_min_daily_decline_pct:.3f}% | {baseline.r3_sell_buffer_pct:.2f}% | {baseline.total_return_pct:.2f}% | {baseline.cagr_pct:.2f}% | {baseline.sharpe:.3f} | {baseline.max_drawdown_pct:.2f}% | {int(baseline.order_count)} |
| 平台代表 | {int(rep.short_center-rep.short_spacing)}/{int(rep.short_center)}/{int(rep.short_center+rep.short_spacing)} | {int(rep.long_window)} | {rep.buy_short_buffer_pct:.2f}%/{rep.buy_long_buffer_pct:.2f}% | {int(rep.r1_decline_days)}/{rep.r1_min_daily_decline_pct:.3f}% | {rep.r3_sell_buffer_pct:.2f}% | {rep.total_return_pct:.2f}% | {rep.cagr_pct:.2f}% | {rep.sharpe:.3f} | {rep.max_drawdown_pct:.2f}% | {int(rep.order_count)} |

## 结论边界

- 这是训练集结果，不是未来有效性证明。平台代表必须冻结后用2016年以后数据测试。
- 零交易成本、复权日线和OHLC触及假设会高估可实现结果。
- 最强改善主要来自长均线周期上移；其他参数在训练平台附近相对不敏感。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run = load_run(context, args.run_id)
    if any(item["status"] != "completed" for item in run["expected_blocks"]):
        raise RuntimeError("Run block is incomplete.")
    run_root = context.run_root(args.run_id)
    block = block_root(context, args.run_id, "QQQ", 0)
    results = pd.read_csv(block / "parameter_results.csv")
    points = pd.read_csv(block / "stage_1_oat_points.csv")
    formal = pd.read_csv(block / "formal_cases.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    from quantkit.sma_entry_exit_ablation import prepare_entry_exit_ablation_data
    indicators = prepare_entry_exit_ablation_data(raw)
    summary = json.loads((block / "summary.json").read_text(encoding="utf-8"))
    baseline = formal[formal["selection_reason"].eq("FROZEN_BASELINE")].iloc[0]
    rep = formal[formal["selection_reason"].eq("TRAINING_PLATEAU_REPRESENTATIVE")].iloc[0]
    summary_html = (
        '<div class="summary-grid">'
        f'<article><h3>训练case</h3><p class="metric">{len(results):,}</p><p>三阶段细搜索</p></article>'
        f'<article><h3>最大回撤</h3><p class="metric">{baseline.max_drawdown_pct:.2f}% → {rep.max_drawdown_pct:.2f}%</p><p>改善 {rep.max_drawdown_pct-baseline.max_drawdown_pct:.2f}pp</p></article>'
        f'<article><h3>总收益</h3><p class="metric">{baseline.total_return_pct:.2f}% → {rep.total_return_pct:.2f}%</p><p>训练集</p></article>'
        f'<article><h3>代表参数</h3><p class="metric">SMA{int(rep.long_window)}</p><p>R3缓冲 {rep.r3_sell_buffer_pct:.2f}%</p></article>'
        '</div>'
    )
    report = render_interactive_report(
        title="QQQ R1+R3 参数训练 2000–2015",
        heading="QQQ R1+R3 参数训练：回撤优先的细步长搜索",
        subtitle="2000-01-03至2015-12-31训练集；2016年以后完全未参与选择",
        summary_html=summary_html,
        notes=[
            "平台代表是训练集代表，不是未来最优参数。",
            "回撤优先，并施加基线70%收益、订单上限和最少闭合交易门禁。",
            "零成本；复权日线；动态阈值按Open缺口或High触及成交。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ价格与平台代表持仓区间", market_figure(daily, formal, indicators), "market"),
            ReportFigure("performance-qqq", "基线、平台代表与Buy & Hold", performance_figure(daily, benchmark, formal), "performance"),
            ReportFigure("oat-responses", "八个参数的单参数细步长响应", oat_figure(points), "other"),
            ReportFigure("search-cloud", "全部case的收益—回撤分布", search_cloud(results, formal), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    markdown = markdown_report(summary, formal)
    (run_root / "report.md").write_text(markdown, encoding="utf-8")
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\n2000–2015 QQQ R1+R3细步长参数训练；详见 `report.html` 与 `report.md`。\n",
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
        "backtest/quantkit/sma_entry_exit_ablation.py",
        "backtest/quantkit/sma_entry_exit_training.py",
        "backtest/scripts/run_sma_entry_exit_parameter_training.py",
        "backtest/scripts/analyze_sma_entry_exit_parameter_training.py",
        "backtest/report_templates/interactive_research_v3/page.html",
        "backtest/report_templates/interactive_research_v3/styles.css",
        "backtest/report_templates/interactive_research_v3/interactions.js",
        "data/processed/daily/QQQ.csv",
    ]
    for relative in source_paths:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    artifacts = {}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifacts[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps({"schema_version": 1, "artifacts": artifacts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if load_run(context, args.run_id)["status"] == "running":
        record_analysis_complete(context, args.run_id)
    print(markdown)


if __name__ == "__main__":
    main()
