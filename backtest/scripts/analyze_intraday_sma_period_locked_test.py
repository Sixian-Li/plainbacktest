#!/usr/bin/env python3
"""Build the report for the locked 2016+ QQQ SMA-period neighborhood test."""

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

from quantkit.experiment import (
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.intraday_sma_period_cross import prepare_sma_period_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_intraday_sma200_threshold_grid import drawdown
from scripts.analyze_intraday_sma_period_cross_grid import market_figure
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026"
)
ANCHOR_COLOR = "#2563eb"
BENCHMARK_COLOR = "#111827"
BUY_OAT_COLOR = "#16a34a"
SELL_OAT_COLOR = "#dc2626"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    anchor_case_id: str,
    anchor_buy: int,
    anchor_sell: int,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=("锁定锚点与四个一步扰动", "从各自历史峰值回撤"),
    )
    case_parameters = (
        (anchor_buy, anchor_sell, "锁定 310/190", ANCHOR_COLOR, True),
        (anchor_buy - 10, anchor_sell, "买入SMA -10", "#0f766e", "legendonly"),
        (anchor_buy + 10, anchor_sell, "买入SMA +10", "#22c55e", "legendonly"),
        (anchor_buy, anchor_sell - 10, "卖出SMA -10", "#b45309", "legendonly"),
        (anchor_buy, anchor_sell + 10, "卖出SMA +10", "#f59e0b", "legendonly"),
    )
    for buy_window, sell_window, label, color, visible in case_parameters:
        selected = daily[
            (daily["buy_window"] == buy_window) & (daily["sell_window"] == sell_window)
        ].sort_values("date")
        if selected.empty:
            raise AssertionError(f"Missing daily ledger for {buy_window}/{sell_window}.")
        case_id = str(selected.iloc[0]["case_id"])
        if buy_window == anchor_buy and sell_window == anchor_sell and case_id != anchor_case_id:
            raise AssertionError("The displayed anchor case ID does not match the frozen anchor.")
        full_label = f"{label}（买{buy_window}/卖{sell_window}）"
        for panel, values, row_number, showlegend in (
            ("equity", selected["equity"], 1, True),
            ("drawdown", drawdown(selected["equity"]), 2, False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=selected["date"],
                    y=values,
                    mode="lines",
                    name=full_label,
                    showlegend=showlegend,
                    visible=visible,
                    line={"color": color, "width": 3.0 if case_id == anchor_case_id else 1.8},
                    meta={"series_key": case_id, "panel": panel, "label": full_label},
                    hovertemplate=f"{full_label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    benchmark_label = "QQQ Buy & Hold（2016-01-04 Open，5 bps）"
    for panel, values, row_number, showlegend in (
        ("equity", benchmark["equity"], 1, True),
        ("drawdown", drawdown(benchmark["equity"]), 2, False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=benchmark_label,
                showlegend=showlegend,
                line={"color": BENCHMARK_COLOR, "width": 2.1, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel,
                    "label": benchmark_label,
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5,
                },
                hovertemplate=(
                    f"{benchmark_label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>"
                ),
            ),
            row=row_number,
            col=1,
        )
    figure.update_layout(
        height=840,
        margin={"l": 65, "r": 25, "t": 75, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-period-locked-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def surface_figure(results: pd.DataFrame, *, anchor_buy: int, anchor_sell: int) -> go.Figure:
    definitions = (
        ("cagr_pct", "CAGR（%）", "RdYlGn"),
        ("sharpe", "Sharpe", "RdYlGn"),
        ("max_drawdown_pct", "最大回撤（%）", "RdYlGn"),
        ("order_count", "成交次数", "Blues"),
    )
    figure = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=[item[1] for item in definitions],
        horizontal_spacing=0.12,
        vertical_spacing=0.14,
    )
    for index, (field, _label, colorscale) in enumerate(definitions):
        row_number = index // 2 + 1
        column = index % 2 + 1
        pivot = (
            results.pivot(index="buy_window", columns="sell_window", values=field)
            .sort_index()
            .sort_index(axis=1)
        )
        figure.add_trace(
            go.Heatmap(
                x=pivot.columns,
                y=pivot.index,
                z=pivot.to_numpy(float),
                colorscale=colorscale,
                colorbar={"len": 0.34, "y": 0.79 if row_number == 1 else 0.21},
                hovertemplate=(
                    f"买入SMA=%{{y}}<br>卖出SMA=%{{x}}<br>{field}=%{{z:.4f}}<extra></extra>"
                ),
            ),
            row=row_number,
            col=column,
        )
        figure.add_trace(
            go.Scatter(
                x=[anchor_sell],
                y=[anchor_buy],
                mode="markers",
                marker={
                    "symbol": "star",
                    "size": 15,
                    "color": "white",
                    "line": {"color": "#111827", "width": 2},
                },
                showlegend=False,
                hovertemplate="锁定锚点 310/190<extra></extra>",
            ),
            row=row_number,
            col=column,
        )
        figure.update_xaxes(title_text="卖出 SMA", row=row_number, col=column)
        figure.update_yaxes(title_text="买入 SMA", row=row_number, col=column)
    figure.update_layout(height=930, margin={"l": 70, "r": 110, "t": 85, "b": 60})
    return figure


def oat_figure(
    buy_oat: pd.DataFrame,
    sell_oat: pd.DataFrame,
    *,
    anchor_buy: int,
    anchor_sell: int,
) -> go.Figure:
    definitions = (
        ("cagr_pct", "CAGR", "%"),
        ("sharpe", "Sharpe", ""),
        ("max_drawdown_pct", "最大回撤", "%"),
        ("order_count", "成交次数", "次"),
    )
    figure = make_subplots(rows=2, cols=2, subplot_titles=[item[1] for item in definitions])
    for index, (field, _label, unit) in enumerate(definitions):
        row_number = index // 2 + 1
        column = index % 2 + 1
        for frame, x_field, label, color, fixed in (
            (
                buy_oat.sort_values("buy_window"),
                "buy_window",
                f"变动买入SMA（卖出固定{anchor_sell}）",
                BUY_OAT_COLOR,
                anchor_buy,
            ),
            (
                sell_oat.sort_values("sell_window"),
                "sell_window",
                f"变动卖出SMA（买入固定{anchor_buy}）",
                SELL_OAT_COLOR,
                anchor_sell,
            ),
        ):
            symbols = ["star" if int(value) == fixed else "circle" for value in frame[x_field]]
            figure.add_trace(
                go.Scatter(
                    x=frame[x_field],
                    y=frame[field],
                    mode="lines+markers",
                    name=label,
                    legendgroup=x_field,
                    showlegend=index == 0,
                    line={"color": color, "width": 2.2},
                    marker={"symbol": symbols, "size": [12 if s == "star" else 7 for s in symbols]},
                    hovertemplate=(
                        f"{label}<br>周期=%{{x}}<br>{field}=%{{y:.4f}} {unit}<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
        figure.update_xaxes(title_text="SMA 周期", row=row_number, col=column)
        figure.update_yaxes(title_text=unit, row=row_number, col=column)
    figure.update_layout(
        height=850,
        margin={"l": 65, "r": 25, "t": 80, "b": 70},
        legend={"orientation": "h", "y": -0.10},
    )
    return figure


def subperiod_figure(
    anchor: pd.Series,
    benchmark_periods: dict[str, Any],
) -> go.Figure:
    periods = (
        ("P1_2016_2019", "2016–2019"),
        ("P2_2020_2022", "2020–2022"),
        ("P3_2023_2026", "2023–2026-08-04"),
    )
    labels = [label for _, label in periods]
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=labels,
            y=[float(anchor[f"{period_id}_cagr_pct"]) for period_id, _ in periods],
            name="锁定 310/190",
            marker={"color": ANCHOR_COLOR},
            hovertemplate="%{x}<br>锁定策略 CAGR=%{y:.3f}%<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=[float(benchmark_periods[f"{period_id}_cagr_pct"]) for period_id, _ in periods],
            name="QQQ Buy & Hold",
            marker={"color": BENCHMARK_COLOR},
            hovertemplate="%{x}<br>Buy & Hold CAGR=%{y:.3f}%<extra></extra>",
        )
    )
    figure.add_hline(y=0, line={"color": "#64748b", "dash": "dash"})
    figure.update_layout(
        barmode="group",
        height=540,
        margin={"l": 65, "r": 25, "t": 55, "b": 65},
        yaxis_title="CAGR（%）",
        legend={"orientation": "h", "y": -0.15},
    )
    return figure


def distance_figure(distance: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=3,
        subplot_titles=("CAGR 随距离", "Sharpe 随距离", "最大回撤随距离"),
        horizontal_spacing=0.10,
    )
    definitions = (
        ("median_cagr_pct", "minimum_cagr_pct", "%", 1),
        ("median_sharpe", "minimum_sharpe", "", 2),
        ("median_max_drawdown_pct", "worst_max_drawdown_pct", "%", 3),
    )
    for median_field, worst_field, unit, column in definitions:
        figure.add_trace(
            go.Scatter(
                x=distance["chebyshev_steps"],
                y=distance[median_field],
                mode="lines+markers",
                name="同距离中位数",
                legendgroup="median",
                showlegend=column == 1,
                line={"color": ANCHOR_COLOR, "width": 2.3},
                hovertemplate=f"距离=%{{x}}步<br>中位数=%{{y:.4f}} {unit}<extra></extra>",
            ),
            row=1,
            col=column,
        )
        figure.add_trace(
            go.Scatter(
                x=distance["chebyshev_steps"],
                y=distance[worst_field],
                mode="lines+markers",
                name="同距离最差值",
                legendgroup="worst",
                showlegend=column == 1,
                line={"color": "#dc2626", "width": 1.9, "dash": "dash"},
                hovertemplate=f"距离=%{{x}}步<br>最差值=%{{y:.4f}} {unit}<extra></extra>",
            ),
            row=1,
            col=column,
        )
        figure.update_xaxes(title_text="距310/190的切比雪夫步数", row=1, col=column)
        figure.update_yaxes(title_text=unit, row=1, col=column)
    figure.update_layout(
        height=560,
        margin={"l": 65, "r": 25, "t": 70, "b": 80},
        legend={"orientation": "h", "y": -0.18},
    )
    return figure


def local_table(local: pd.DataFrame, anchor_case_id: str) -> str:
    lines = [
        "| 买/卖 SMA | CAGR | Sharpe | 最大回撤 | 成交 | 普通买/卖 | c买回 | 身份 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in local.sort_values(["buy_window", "sell_window"]).itertuples(index=False):
        identity = "锁定锚点" if str(row.case_id) == anchor_case_id else "扰动"
        lines.append(
            f"| {int(row.buy_window)}/{int(row.sell_window)} | {row.cagr_pct:.3f}% | "
            f"{row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | {int(row.order_count)} | "
            f"{int(row.buy_sma_count)}/{int(row.sell_sma_count)} | "
            f"{int(row.forced_rebuy_count)} | {identity} |"
        )
    return "\n".join(lines)


def markdown_report(
    run_id: str,
    selection: dict[str, Any],
    local: pd.DataFrame,
) -> str:
    anchor = selection["locked_anchor"]
    benchmark = selection["benchmark_metrics"]
    gates = selection["predeclared_gates"]
    local_gate = gates["local_3x3_stability"]
    effectiveness = gates["anchor_effectiveness"]
    verdict = (
        "锚点通过预登记效果与局部稳定性门禁，但不解除父实验PBO/DSR阻断"
        if gates["diagnostic_support_pass"]
        else "锚点未同时通过预登记效果与局部稳定性门禁"
    )
    full = selection["full_neighborhood_distribution"]
    ranks = selection["locked_anchor_ranks_within_diagnostic_surface"]
    return "\n".join(
        [
            "# QQQ 独立买卖 SMA 周期锁定样本外",
            "",
            f"> Run `{run_id}`；2016-01-04～2026-08-04；锁定c=3%、d关闭、买310/卖190；81组扰动不选冠军。",
            "",
            "## 结论",
            "",
            f"- 最终判定：**{verdict}**。",
            f"- 锁定310/190：CAGR {anchor['cagr_pct']:.3f}%，Sharpe {anchor['sharpe']:.3f}，最大回撤 {anchor['max_drawdown_pct']:.2f}%，成交 {int(anchor['order_count'])} 次。",
            f"- 同期QQQ Buy & Hold：CAGR {benchmark['cagr_pct']:.3f}%，Sharpe {benchmark['sharpe']:.3f}，最大回撤 {benchmark['max_drawdown_pct']:.2f}%。",
            f"- 效果门禁：{'通过' if effectiveness['pass'] else '未通过'}；CAGR保留率 {effectiveness['buy_hold_cagr_retention']:.1%}，Sharpe差 {effectiveness['sharpe_advantage']:+.3f}，最大回撤改善 {effectiveness['max_drawdown_improvement_percentage_points']:+.2f} 个百分点。",
            f"- 局部3×3门禁：{'通过' if local_gate['pass'] else '未通过'}；9组{'全部' if local_gate['all_positive_cagr'] else '并非全部'}正CAGR，CAGR中位数/锚点 {local_gate['median_cagr_fraction_of_anchor']:.1%}，最差Sharpe相对锚点 {local_gate['worst_sharpe_relative_to_anchor']:+.3f}。",
            f"- 全81组CAGR范围 {full['cagr_pct']['minimum']:.3f}%～{full['cagr_pct']['maximum']:.3f}%；310/190的CAGR排名 {ranks['cagr_pct']['rank']}/81、Sharpe排名 {ranks['sharpe']['rank']}/81。排名只描述扰动差异，不用于换参。",
            "- 两套编译账本核对全部81组，且81组都经PyBroker与独立Python账本正式复核。",
            "",
            "## 锁定锚点附近3×3",
            "",
            local_table(local, str(gates["anchor_case_id"])),
            "",
            "## 研究边界",
            "",
            "- 2015年属于父实验训练/探索期，所以真正样本外从2016-01-04开始；2016年前行情只预热均线，不继承仓位或纠错锚。",
            "- 270～350 × 150～230是预登记稳定性扰动，不是81次重新选参；本报告不会把其中最高点称为新参数。",
            "- 父实验PBO=75.54%且DSR未达95%，即便本轮表现支持310/190，也不能据此直接晋级实盘或宣称未来最优。",
            "- 使用拆股及股息调整OHLC，是总回报价格近似，不是原始成交价、现金股息与份额变化的账户级重放。",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [
        item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)

    results = pd.read_csv(block / "parameter_results.csv")
    formal = pd.read_csv(block / "formal_candidate_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    buy_oat = pd.read_csv(block / "buy_oat.csv")
    sell_oat = pd.read_csv(block / "sell_oat.csv")
    local = pd.read_csv(block / "local_3x3.csv")
    distance = pd.read_csv(block / "distance_summary.csv")
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    if len(results) != 81 or len(formal) != 81 or len(local) != 9:
        raise AssertionError("Locked report requires 81 formal cases and a complete local 3x3.")
    if bool(selection["winner_selection_performed"]):
        raise AssertionError("The locked test must not perform holdout winner selection.")

    anchor = pd.Series(selection["locked_anchor"])
    anchor_case_id = str(selection["predeclared_gates"]["anchor_case_id"])
    anchor_buy = int(anchor["buy_window"])
    anchor_sell = int(anchor["sell_window"])
    if (anchor_buy, anchor_sell) != (310, 190):
        raise AssertionError("The report anchor is not the frozen 310/190 case.")

    extrema: dict[str, Any] = {}
    for label, field in (
        ("highest_cagr", "cagr_pct"),
        ("highest_sharpe", "sharpe"),
        ("least_negative_drawdown", "max_drawdown_pct"),
        ("lowest_cagr", "cagr_pct"),
    ):
        index = results[field].idxmin() if label == "lowest_cagr" else results[field].idxmax()
        extrema[label] = json_safe(results.loc[index].to_dict())
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        **selection,
        "created_at_utc": created_at,
        "descriptive_extrema_not_selected": extrema,
        "local_3x3_cases": json_safe(local.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    gates = selection["predeclared_gates"]
    anchor_effectiveness = gates["anchor_effectiveness"]
    local_stability = gates["local_3x3_stability"]
    verdict = (
        "样本外效果与局部稳定性均通过；仍不得因父实验PBO/DSR失败而晋级"
        if gates["diagnostic_support_pass"]
        else "样本外效果与局部稳定性没有同时通过"
    )
    compact = (
        f"<p>唯一主测试为 <strong>c=3%、d关闭、买SMA310/卖SMA190</strong>；"
        f"CAGR <strong>{float(anchor['cagr_pct']):.3f}%</strong>、Sharpe "
        f"<strong>{float(anchor['sharpe']):.3f}</strong>、最大回撤 "
        f"<strong>{float(anchor['max_drawdown_pct']):.2f}%</strong>。"
        f"效果门禁<strong>{'通过' if anchor_effectiveness['pass'] else '未通过'}</strong>，"
        f"局部3×3门禁<strong>{'通过' if local_stability['pass'] else '未通过'}</strong>；"
        f"最终判定：<strong>{html.escape(verdict)}</strong>。81组扰动未用于重新选冠军。</p>"
    )

    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    prices = prepare_sma_period_data(raw, [anchor_buy, anchor_sell])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    anchor_orders = orders[orders["case_id"] == anchor_case_id].copy()
    anchor_plans = plans[plans["case_id"] == anchor_case_id].copy()

    figures = [
        ReportFigure(
            "performance-qqq",
            "锁定310/190的净值与回撤；四个一步扰动可从图例开启",
            performance_figure(
                daily,
                benchmark,
                anchor_case_id=anchor_case_id,
                anchor_buy=anchor_buy,
                anchor_sell=anchor_sell,
            ),
            "performance",
        ),
        ReportFigure(
            "period-surface",
            "81组预登记扰动：完整指标面（星号始终是锁定310/190）",
            surface_figure(results, anchor_buy=anchor_buy, anchor_sell=anchor_sell),
            "generic",
        ),
        ReportFigure(
            "oat-sensitivity",
            "一次只改变买入或卖出SMA的敏感度",
            oat_figure(
                buy_oat,
                sell_oat,
                anchor_buy=anchor_buy,
                anchor_sell=anchor_sell,
            ),
            "generic",
        ),
        ReportFigure(
            "fixed-subperiods",
            "连续继承状态下的三个固定分段CAGR",
            subperiod_figure(anchor, selection["benchmark_subperiod_metrics"]),
            "generic",
        ),
        ReportFigure(
            "distance-stability",
            "离310/190越远时，同距离参数的中位数与最差值",
            distance_figure(distance),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            "锁定310/190：QQQ、两条SMA、盘前动态触发线与实际成交",
            market_figure(
                prices,
                anchor_plans,
                anchor_orders,
                buy_window=anchor_buy,
                sell_window=anchor_sell,
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ 独立买卖 SMA 周期锁定样本外",
        heading="QQQ 独立买卖 SMA 周期锁定样本外",
        subtitle="2016-01-04～2026-08-04；锁定c=3%、d关闭、买310/卖190；9×9扰动只检查稳定性。",
        summary_html=compact,
        notes=[
            "310/190在查看2016年以后结果前已经固定；270～350 × 150～230的80个其他组合不具备替换权。",
            "2016年前批准数据只预热最长SMA350；所有case从2016-01-04独立空仓开始，不继承父实验状态。",
            "普通买卖线在开盘前用完成历史精确求解；跳空越线按Open，否则按日内理论线；单边5 bps，每日最多一笔。",
            "本轮不在保留期计算PBO/DSR来挑参数；父实验PBO/DSR失败仍是晋级阻断条件。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, selection, local),
        encoding="utf-8",
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_period_cross.py",
        "backtest/quantkit/intraday_sma_period_cross_search.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_intraday_sma_period_cross_grid.py",
        "backtest/scripts/analyze_intraday_sma_period_cross_grid.py",
        "backtest/scripts/run_intraday_sma_period_locked_test.py",
        "backtest/scripts/analyze_intraday_sma_period_locked_test.py",
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

This immutable run contains the locked QQQ 310/190 test and its predeclared 9x9 neighborhood.

- `report.html` / `report.md`: locked-anchor outcome and perturbation diagnostics.
- `analysis/summary.json`: machine-readable gates, anchor, benchmark and descriptive extrema.
- `QQQ/cost_5bps/`: all 81 metrics and all 81 PyBroker/Python reconciled ledgers.
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
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
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
