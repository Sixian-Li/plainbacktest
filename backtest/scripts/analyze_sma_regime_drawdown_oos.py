#!/usr/bin/env python3
"""Report the trained peak-drawdown threshold and its frozen QQQ OOS test."""

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


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos"
COLORS = {
    "base": "#2563eb",
    "fixed": "#d97706",
    "selected": "#0f766e",
    "buy_hold": "#111827",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def case_definitions(selected_pct: float, *, window: str) -> list[dict[str, str]]:
    prefix = "train" if window == "train" else "test"
    return [
        {
            "case_id": f"{prefix}_base_no_stop",
            "series_key": "base",
            "label": "基础策略：条件 1+3，无回撤止损",
        },
        {
            "case_id": f"{prefix}_{'stop' if window == 'train' else 'fixed'}_8pct",
            "series_key": "fixed",
            "label": "固定 8% 回撤止损",
        },
        {
            "case_id": f"{prefix}_{'stop' if window == 'train' else 'selected'}_{selected_pct:g}pct",
            "series_key": "selected",
            "label": f"训练期所选 {selected_pct:g}% 回撤止损",
        },
    ]


def metrics_row(results: pd.DataFrame, case_id: str) -> dict[str, Any]:
    found = results[results["case_id"] == case_id]
    if len(found) != 1:
        raise ValueError(f"Expected one result for {case_id}, found {len(found)}.")
    return found.iloc[0].to_dict()


def build_market_figure(
    indicators: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    selected_case_id: str,
    selected_pct: float,
    test_start: pd.Timestamp,
) -> go.Figure:
    market = indicators[indicators["date"] >= test_start].copy()
    selected_orders = orders[orders["case_id"] == selected_case_id].copy()
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.74, 0.26],
        subplot_titles=(
            f"QQQ 测试期复权 OHLC、SMA200/250/300 与所选 {selected_pct:g}% 策略成交",
            "三条长期 SMA 相对前一交易日变化（%）",
        ),
    )
    figure.add_trace(
        go.Candlestick(
            x=market["date"], open=market["open"], high=market["high"],
            low=market["low"], close=market["close"], name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
        ),
        row=1,
        col=1,
    )
    sma_colors = {200: "#2563eb", 250: "#7c3aed", 300: "#d97706"}
    for window in (200, 250, 300):
        figure.add_trace(
            go.Scatter(
                x=market["date"], y=market[f"sma{window}"], mode="lines",
                name=f"SMA{window}", line={"color": sma_colors[window], "width": 1.5},
                meta={"series_key": f"sma{window}", "panel": "market", "label": f"SMA{window}"},
            ),
            row=1,
            col=1,
        )
    for side, label, color, symbol in (
        ("buy", "所选策略买入", "#087f5b", "triangle-up"),
        ("sell", "所选策略卖出", "#c92a2a", "triangle-down"),
    ):
        subset = selected_orders[selected_orders["type"] == side]
        custom = subset[["signal_date", "reason"]].astype(str).to_numpy()
        figure.add_trace(
            go.Scatter(
                x=subset["date"], y=subset["raw_price"], customdata=custom,
                mode="markers", name=label,
                marker={"color": color, "symbol": symbol, "size": 9},
                meta={"series_key": f"selected_{side}", "panel": "market", "label": label},
                hovertemplate=(
                    f"{label}<br>成交 %{{x|%Y-%m-%d}} Open $%{{y:.4f}}"
                    "<br>信号 %{customdata[0]} Close<br>%{customdata[1]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    for window in (200, 250, 300):
        derivative = market[f"sma{window}"].pct_change(fill_method=None) * 100.0
        figure.add_trace(
            go.Scatter(
                x=market["date"], y=derivative, mode="lines", name=f"SMA{window} 日变化",
                line={"color": sma_colors[window], "width": 1.2},
                meta={
                    "series_key": f"sma{window}_derivative",
                    "panel": "market",
                    "label": f"SMA{window} 日变化率",
                },
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.4f}%<extra></extra>",
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        height=830, margin={"l": 65, "r": 25, "t": 60, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="qqq-sma-peak-dd-market-v1",
    )
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def build_threshold_response(train_results: pd.DataFrame, selected_pct: float) -> go.Figure:
    grid = train_results[train_results["stop_pct"].notna()].sort_values("stop_pct")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.13,
        subplot_titles=("训练期最大回撤（越高、越接近 0 越好）", "训练期总收益（只报告，不参与首要选择）"),
    )
    figure.add_trace(
        go.Scatter(
            x=grid["stop_pct"], y=grid["max_drawdown_pct"], mode="lines+markers",
            name="最大回撤", line={"color": "#0f766e", "width": 2.5},
            marker={"size": 8}, hovertemplate="阈值 %{x:.0f}%<br>最大回撤 %{y:.2f}%<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=grid["stop_pct"], y=grid["total_return_pct"], mode="lines+markers",
            name="总收益", line={"color": "#7c3aed", "width": 2.2},
            marker={"size": 8}, hovertemplate="阈值 %{x:.0f}%<br>总收益 %{y:.2f}%<extra></extra>",
        ),
        row=2,
        col=1,
    )
    for row in (1, 2):
        figure.add_vline(x=8, line={"color": COLORS["fixed"], "dash": "dot", "width": 1.5}, row=row, col=1)
        figure.add_vline(x=selected_pct, line={"color": COLORS["selected"], "dash": "dash", "width": 2}, row=row, col=1)
    figure.add_annotation(x=8, y=1.05, xref="x", yref="y domain", text="固定 8%", showarrow=False, font={"color": COLORS["fixed"]})
    figure.add_annotation(x=selected_pct, y=0.93, xref="x", yref="y domain", text=f"所选 {selected_pct:g}%", showarrow=False, font={"color": COLORS["selected"]})
    figure.update_layout(
        height=650, margin={"l": 70, "r": 30, "t": 65, "b": 60},
        hovermode="x unified", showlegend=False, uirevision="qqq-sma-peak-dd-response-v1",
    )
    figure.update_yaxes(title_text="%", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(title_text="持仓峰值回撤止损阈值（%）", dtick=1, row=2, col=1)
    return figure


def build_performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    definitions: list[dict[str, str]],
    *,
    window: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("账户净值（每个窗口独立从 100,000 美元现金开始）", "从各自历史峰值回撤"),
    )
    for definition in definitions:
        frame = daily[
            (daily["window_id"] == window) & (daily["case_id"] == definition["case_id"])
        ].sort_values("date")
        dash = "dash" if definition["series_key"] == "selected" else "solid"
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=definition["label"],
                    showlegend=showlegend,
                    line={"color": COLORS[definition["series_key"]], "width": 2.2, "dash": dash},
                    meta={
                        "series_key": definition["series_key"],
                        "panel": panel,
                        "label": definition["label"],
                    },
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                        if row == 1 else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                    ),
                ),
                row=row,
                col=1,
            )
    hold = benchmark[benchmark["window_id"] == window].sort_values("date")
    for row, values, panel, showlegend in (
        (1, hold["equity"], "equity", True),
        (2, drawdown(hold["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=hold["date"], y=values, mode="lines", name="QQQ Buy & Hold",
                showlegend=showlegend, line={"color": COLORS["buy_hold"], "width": 2.0, "dash": "dot"},
                meta={
                    "series_key": "buy_hold", "panel": panel, "label": "QQQ Buy & Hold",
                    "is_benchmark": panel == "equity", "cost_bps": 0,
                },
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                    if row == 1 else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        height=790, margin={"l": 65, "r": 25, "t": 65, "b": 55},
        hovermode="x unified", showlegend=False, uirevision=f"qqq-sma-peak-dd-{window}-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def compact_rows(
    results: pd.DataFrame,
    definitions: list[dict[str, str]],
    benchmark_row: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for definition in definitions:
        rows.append({"name": definition["label"], **metrics_row(results, definition["case_id"])})
    rows.append({"name": "QQQ Buy & Hold", **benchmark_row})
    return rows


def table_body(rows: list[dict[str, Any]]) -> str:
    return "".join(
        "<tr>"
        f"<td>{html.escape(str(row['name']))}</td>"
        f"<td>{float(row['total_return_pct']):.2f}%</td>"
        f"<td>{float(row['cagr_pct']):.3f}%</td>"
        f"<td>{float(row['sharpe']):.3f}</td>"
        f"<td>{float(row['max_drawdown_pct']):.2f}%</td>"
        f"<td>{float(row['exposure_pct']):.2f}%</td>"
        f"<td>{int(row['order_count'])}</td>"
        "</tr>"
        for row in rows
    )


def result_table(title: str, rows: list[dict[str, Any]]) -> str:
    return (
        f"<h3>{html.escape(title)}</h3><table><thead><tr><th>版本</th><th>总收益</th>"
        "<th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>持仓率</th><th>成交</th>"
        f"</tr></thead><tbody>{table_body(rows)}</tbody></table>"
    )


def summary_html(summary: dict[str, Any], train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> str:
    selected = summary["selection"]
    helped_text = "有帮助" if selected["test_helped_max_drawdown"] else "没有帮助"
    fixed_text = "有帮助" if summary["fixed_8_helped_max_drawdown"] else "没有帮助"
    return f"""
<h2>样本外结论</h2>
<p>只按训练期最大回撤选择，5%～15% 中冻结的是 <strong>{selected['selected_stop_pct']:g}%</strong>。放到完全独立的 2005–2026 测试期后，基础策略最大回撤为 <strong>{summary['test_base']['max_drawdown_pct']:.2f}%</strong>，所选阈值为 <strong>{summary['test_selected']['max_drawdown_pct']:.2f}%</strong>：按预声明的唯一标准，<strong>{helped_text}</strong>（改善 {selected['test_max_drawdown_delta_pct_points']:+.2f} 个百分点）。固定 8% 的测试期最大回撤为 <strong>{summary['test_fixed_8']['max_drawdown_pct']:.2f}%</strong>，因此 8% 本身<strong>{fixed_text}</strong>。</p>
{result_table('训练期：仅用于选择阈值', train_rows)}
{result_table('测试期：阈值已冻结', test_rows)}
<h2>口径</h2>
<p>基础策略已经去掉条件 2，只保留 Close&gt;SMA200 与 SMA200&gt;SMA250&gt;SMA300。每次入场后，用实际入场 Open 成交价及随后完成的每日 Close 维护峰值；当前 Close 回撤严格大于阈值才发出卖出信号，下一交易日 Open 清仓。测试窗口从新现金开始，不继承训练期仓位或峰值。</p>
"""


def markdown_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 版本 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 成交 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {float(row['total_return_pct']):.2f}% | "
            f"{float(row['cagr_pct']):.3f}% | {float(row['sharpe']):.3f} | "
            f"{float(row['max_drawdown_pct']):.2f}% | {float(row['exposure_pct']):.2f}% | "
            f"{int(row['order_count'])} |"
        )
    return lines


def markdown_report(
    summary: dict[str, Any],
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    grid: pd.DataFrame,
) -> str:
    selected = summary["selection"]
    helped = "有帮助" if selected["test_helped_max_drawdown"] else "没有帮助"
    lines = [
        "# QQQ 条件 1+3 策略：持仓峰值回撤止损训练与样本外检验",
        "",
        f"- Run：`{summary['run_id']}`",
        f"- 训练期实际观察：{summary['train_actual_start']}～{summary['train_actual_end']}（仅选阈值）",
        f"- 测试期实际观察：{summary['test_actual_start']}～{summary['test_actual_end']}（独立现金、阈值冻结）",
        f"- 训练期所选阈值：**{selected['selected_stop_pct']:g}%**；测试期按最大回撤判断：**{helped}**",
        "- 基础条件：Close>SMA200 且 SMA200>SMA250>SMA300；已去掉条件 2",
        "- 止损：单次持仓内峰值到完成 Close 的回撤严格大于阈值，下一交易日 Open 卖出；无冷却期",
        "",
        "## 训练期重点版本",
        "",
        *markdown_table(train_rows),
        "",
        "## 5%～15% 训练响应",
        "",
        "| 阈值 | 总收益 | CAGR | Sharpe | 最大回撤 | 止损卖出 | 成交 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in grid.sort_values("stop_pct").iterrows():
        marker = " **← 所选**" if float(row["stop_pct"]) == float(selected["selected_stop_pct"]) else ""
        lines.append(
            f"| {float(row['stop_pct']):g}%{marker} | {float(row['total_return_pct']):.2f}% | "
            f"{float(row['cagr_pct']):.3f}% | {float(row['sharpe']):.3f} | "
            f"{float(row['max_drawdown_pct']):.2f}% | {int(row['stop_exit_count'])} | "
            f"{int(row['order_count'])} |"
        )
    lines.extend(
        [
            "",
            "## 2005–2026 样本外",
            "",
            *markdown_table(test_rows),
            "",
            "## 判定与边界",
            "",
            f"所选阈值相对无止损基础策略的样本外最大回撤变化为 {selected['test_max_drawdown_delta_pct_points']:+.2f} 个百分点；正数表示回撤减轻。固定 8% 也单独报告，避免把训练所选值与最初提出的 8% 混为一谈。",
            "",
            "- 这里只有一个阈值参数，因此图是 5%～15% 一维响应曲线，不是二维曲面。",
            "- 用户给出的训练起点是 1999 年；SMA300 与共同预热完成后，实际可交易起点才开始。训练截至 2004-12-31，测试从 2005 年首个交易日起，两段不重叠。",
            "- 最大回撤是唯一选择和成败指标；收益、Sharpe、持仓率和成交数只用于显示代价。",
            "- 本次为单一时间切分且零成本。即使样本外回撤改善，也不足以直接晋级模拟盘；仍需费用、滑点及多个不重叠窗口验证。",
        ]
    )
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
        raise RuntimeError(f"Cannot analyze an incomplete run: {incomplete}")

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    symbol = context.config["symbols"][0]
    block = block_root(context, args.run_id, symbol, 0.0)
    results = pd.read_csv(block / "parameter_results.csv")
    train_results = pd.read_csv(block / "train_parameter_results.csv")
    test_results = pd.read_csv(block / "test_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    benchmark_results = pd.read_csv(block / "benchmark_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["signal_date", "date"])
    indicators = pd.read_csv(block / "indicators.csv", parse_dates=["date"])
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    selection = metrics["selection"]
    selected_pct = float(selection["selected_stop_pct"])
    train_defs = case_definitions(selected_pct, window="train")
    test_defs = case_definitions(selected_pct, window="test")
    train_benchmark = benchmark_results[benchmark_results["window_id"] == "train"].iloc[0].to_dict()
    test_benchmark = benchmark_results[benchmark_results["window_id"] == "test"].iloc[0].to_dict()
    train_rows = compact_rows(train_results, train_defs, train_benchmark)
    test_rows = compact_rows(test_results, test_defs, test_benchmark)
    test_base = metrics_row(test_results, "test_base_no_stop")
    test_fixed = metrics_row(test_results, "test_fixed_8pct")
    test_selected = metrics_row(test_results, f"test_selected_{selected_pct:g}pct")
    fixed_delta = float(test_fixed["max_drawdown_pct"] - test_base["max_drawdown_pct"])
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "purpose": "train-only peak drawdown threshold selection and independent QQQ OOS test",
        "train_actual_start": metrics["train_actual_start"],
        "train_actual_end": metrics["train_actual_end"],
        "test_actual_start": metrics["test_actual_start"],
        "test_actual_end": metrics["test_actual_end"],
        "selection": selection,
        "fixed_8_helped_max_drawdown": fixed_delta > 0,
        "fixed_8_test_max_drawdown_delta_pct_points": fixed_delta,
        "test_base": test_base,
        "test_fixed_8": test_fixed,
        "test_selected": test_selected,
        "training_comparison_rows": train_rows,
        "test_comparison_rows": test_rows,
        "max_cross_check_differences": metrics["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    selected_test_case = f"test_selected_{selected_pct:g}pct"
    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            "测试期 QQQ、长期均线与所选策略成交点",
            build_market_figure(
                indicators,
                orders,
                selected_case_id=selected_test_case,
                selected_pct=selected_pct,
                test_start=pd.Timestamp(metrics["test_actual_start"]),
            ),
            "market",
        ),
        ReportFigure(
            "threshold-response",
            "训练期 5%～15% 回撤阈值响应曲线",
            build_threshold_response(train_results, selected_pct),
            "other",
        ),
        ReportFigure(
            "training-performance",
            "训练期重点版本净值与回撤",
            build_performance_figure(daily, benchmark, train_defs, window="train"),
            "other",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            "2005–2026 样本外净值与回撤",
            build_performance_figure(daily, benchmark, test_defs, window="test"),
            "performance",
        ),
    ]
    (run_root / "report.md").write_text(
        markdown_report(
            summary,
            train_rows,
            test_rows,
            train_results[train_results["stop_pct"].notna()].copy(),
        ),
        encoding="utf-8",
    )
    report = render_interactive_report(
        title="QQQ 条件 1+3：持仓峰值回撤阈值训练与样本外检验",
        heading="QQQ 条件 1+3：回撤止损是否真的有帮助？",
        subtitle="1999 起始样本只选择 5%～15% 阈值；2005–2026 独立账户检验所选阈值与固定 8%。",
        summary_html=summary_html(summary, train_rows, test_rows),
        notes=[
            "基础策略只保留条件 1 和条件 3；条件 2 已完全关闭。",
            "训练实际起点受 SMA300 共同预热约束；训练截至 2004-12-31，测试从 2005 年首个交易日起，严格不重叠。",
            "峰值止损只用实际入场价和已完成 Close，严格超过阈值后下一交易日 Open 卖出；每次新持仓重置峰值。",
            "最大回撤是唯一的训练选择目标和样本外成败标准；其他指标只展示代价。",
            "固定 8% 与训练所选阈值都独立报告；若二者相同，曲线重合是预期结果。",
            "零成本且只有一次时间切分；不能直接用于模拟盘或实盘。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reference.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/sma_regime.py",
        "backtest/quantkit/sma_regime_drawdown.py",
        "backtest/scripts/run_sma_regime_ablation.py",
        "backtest/scripts/run_sma_regime_drawdown_oos.py",
        "backtest/scripts/analyze_sma_regime_drawdown_oos.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/rot/test_sma_regime.py",
        "backtest/tests/strategies/rot/test_sma_regime_drawdown.py",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        f"data/processed/daily/{symbol}.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(),
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

This immutable run selects a QQQ peak-to-Close drawdown threshold on training data and tests it on an independent later window.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable selection and OOS verdict.
- `QQQ/cost_0bps/`: training grid, frozen selection, three OOS cases, two independent ledgers, benchmark, indicators and hashes.
- `provenance.json`: exact source, dependency, template and data hashes.
- `validation.json`: mandatory tests, audit, hash checks and real-browser evidence.
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
