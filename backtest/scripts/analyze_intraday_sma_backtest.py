#!/usr/bin/env python3
"""Build the formal interactive report for the QQQ intraday SMA OR strategy."""

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
from plotly import colors
from plotly.subplots import make_subplots

from quantkit.experiment import (
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.intraday_sma import IntradaySmaSpec, prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021"
SIGNAL_LABELS = {
    "INITIAL_SEED": "初始持仓",
    "SELL_COST_STOP": "成本线 -1.5% 强止损",
    "SELL_SLOW_TREND": "短均线连续转弱且跌破 SMA130",
    "SELL_FAST_DROP": "短均线变化率达到 -0.15%",
    "SELL_SMA200_CROSS": "下穿 SMA200 兜底",
    "BUY_FORCED_REENTRY": "卖出价 +1.5% 强制买回",
    "BUY_SMA200_CROSS": "上穿 SMA200",
    "BUY_SHORT_RECOVERY": "上穿短均线平均值下方 1%",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def money(value: float) -> str:
    return f"${value:,.2f}"


def number(value: float, digits: int = 4) -> str:
    return f"{value:,.{digits}f}"


def build_market_figure(
    prices: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    short_windows: tuple[int, int, int] = (25, 30, 35),
    symbol: str = "QQQ",
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.73, 0.27],
        subplot_titles=(
            f"{symbol} 复权 OHLC、均线与成交原因",
            f"SMA{short_windows[0]} / {short_windows[1]} / {short_windows[2]} 及其平均值相对前一日变化（%）",
        ),
    )
    figure.add_trace(
        go.Candlestick(
            x=prices["date"],
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            name=f"{symbol} 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        ),
        row=1,
        col=1,
    )
    common_meta = {
        "panel": "market",
        "control_group": "core_sma",
        "control_group_label": "价格、短均线与成交",
    }
    figure.add_trace(
        go.Scatter(
            x=prices["date"],
            y=prices["close"],
            mode="lines",
            name=f"{symbol} Close",
            line={"color": "#111827", "width": 1.8},
            meta={**common_meta, "series_key": f"{symbol.lower()}_close", "label": f"{symbol} Close"},
        ),
        row=1,
        col=1,
    )
    short_colors = dict(zip(short_windows, ("#2563eb", "#f59e0b", "#16a34a"), strict=True))
    for window, color in short_colors.items():
        figure.add_trace(
            go.Scatter(
                x=prices["date"],
                y=prices[f"sma{window}"],
                mode="lines",
                name=f"SMA{window}",
                line={"color": color, "width": 1.35},
                meta={**common_meta, "series_key": f"sma{window}", "label": f"SMA{window}"},
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=prices["date"],
            y=prices["sma_short_avg"],
            mode="lines",
            name=f"SMA{short_windows[0]}/{short_windows[1]}/{short_windows[2]} 平均",
            line={"color": "#7c3aed", "width": 2.2, "dash": "dash"},
            meta={
                **common_meta,
                "series_key": "sma_short_avg",
                "label": f"SMA{short_windows[0]}/{short_windows[1]}/{short_windows[2]} 平均",
            },
        ),
        row=1,
        col=1,
    )

    long_windows = tuple(range(70, 451, 10))
    palette = colors.sample_colorscale(
        "Turbo", [0.05 + 0.9 * index / (len(long_windows) - 1) for index in range(len(long_windows))]
    )
    for window, color in zip(long_windows, palette, strict=True):
        figure.add_trace(
            go.Scatter(
                x=prices["date"],
                y=prices[f"sma{window}"],
                mode="lines",
                name=f"SMA{window}",
                line={"color": color, "width": 0.95},
                opacity=0.7,
                visible="legendonly",
                meta={
                    "series_key": f"sma{window}",
                    "panel": "market",
                    "label": f"SMA{window}",
                    "control_group": "long_sma",
                    "control_group_label": "SMA70–450（间隔 10）",
                },
            ),
            row=1,
            col=1,
        )

    marker_orders = orders[~orders["is_initial_seed"].astype(bool)].copy()
    for side, color, marker, label in (
        ("buy", "#087f5b", "triangle-up", "买入成交"),
        ("sell", "#c92a2a", "triangle-down", "卖出成交"),
    ):
        subset = marker_orders[marker_orders["type"] == side]
        custom = np.column_stack(
            [
                subset["primary_signal"].map(SIGNAL_LABELS),
                subset["matched_signals"],
                subset["theoretical_trigger"].map(lambda value: f"{value:.4f}"),
                subset["fill_source"],
            ]
        ) if len(subset) else np.empty((0, 4))
        figure.add_trace(
            go.Scatter(
                x=subset["date"],
                y=subset["fill_price"],
                customdata=custom,
                mode="markers",
                name=label,
                marker={"color": color, "symbol": marker, "size": 10},
                meta={
                    "series_key": f"trade_{side}",
                    "panel": "market",
                    "label": label,
                    "control_group": "core_sma",
                    "control_group_label": "价格、短均线与成交",
                },
                hovertemplate=(
                    f"{label}<br>%{{x|%Y-%m-%d}}<br>成交 $%{{y:.4f}}"
                    "<br>主因 %{customdata[0]}<br>同时满足 %{customdata[1]}"
                    "<br>理论阈值 $%{customdata[2]}<br>方式 %{customdata[3]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    derivative_columns = (
        (f"sma{short_windows[0]}_derivative_pct", f"SMA{short_windows[0]}", "#2563eb"),
        (f"sma{short_windows[1]}_derivative_pct", f"SMA{short_windows[1]}", "#f59e0b"),
        (f"sma{short_windows[2]}_derivative_pct", f"SMA{short_windows[2]}", "#16a34a"),
        ("sma_short_avg_derivative_pct", "三线平均", "#7c3aed"),
    )
    for column, label, color in derivative_columns:
        figure.add_trace(
            go.Scatter(
                x=prices["date"],
                y=prices[column],
                mode="lines",
                name=f"{label} 日变化率",
                line={"color": color, "width": 2.0 if column.startswith("sma_short_avg") else 1.2},
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": f"{label} 日变化率",
                    "control_group": "derivative",
                    "control_group_label": "下图 4 条日变化率",
                },
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>{label} %{{y:.4f}}%<extra></extra>",
            ),
            row=2,
            col=1,
        )

    figure.update_layout(
        height=940,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision=f"{symbol.lower()}-intraday-sma-or-v1",
    )
    figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}])
    figure.update_xaxes(rangeslider={"visible": True, "thickness": 0.08}, row=2, col=1)
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="日变化（%）", fixedrange=False, zeroline=True, row=2, col=1)
    return figure


def build_performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    for row, values, panel, showlegend in (
        (1, daily["equity"], "equity", True),
        (2, drawdown(daily["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=daily["date"],
                y=values,
                mode="lines",
                name="固定参数 OR 策略" if showlegend else "策略回撤",
                showlegend=showlegend,
                line={"color": "#0f766e", "width": 2.3},
                meta={"series_key": "strategy", "panel": panel, "label": "固定参数 OR 策略"},
            ),
            row=row,
            col=1,
        )
    for row, values, panel, showlegend in (
        (1, benchmark["equity"], "equity", True),
        (2, drawdown(benchmark["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name="QQQ 持有 100 股" if showlegend else "QQQ 回撤",
                showlegend=showlegend,
                line={"color": "#334155", "width": 2.0, "dash": "dash"},
                meta={
                    "series_key": "buy_hold",
                    "panel": panel,
                    "label": "QQQ 持有 100 股",
                    "is_benchmark": panel == "equity",
                    "cost_bps": 0,
                },
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        height=760,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
    )
    figure.update_yaxes(title_text="USD", row=1, col=1, fixedrange=False)
    figure.update_yaxes(title_text="%", row=2, col=1, fixedrange=False)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def build_ablation_figure(ablations: pd.DataFrame) -> go.Figure:
    frame = ablations.copy()
    frame["label"] = frame["disabled_signal"].map(
        lambda signal: "全部规则" if pd.isna(signal) else f"去掉：{SIGNAL_LABELS.get(str(signal), str(signal))}"
    )
    figure = make_subplots(rows=1, cols=2, subplot_titles=("CAGR", "最大回撤"))
    figure.add_trace(
        go.Bar(x=frame["label"], y=frame["cagr_pct"], name="CAGR", marker_color="#0f766e"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(x=frame["label"], y=frame["max_drawdown_pct"], name="最大回撤", marker_color="#c2413b"),
        row=1,
        col=2,
    )
    figure.update_layout(height=560, showlegend=False, margin={"l": 60, "r": 25, "t": 60, "b": 175})
    figure.update_xaxes(tickangle=-35)
    figure.update_yaxes(title_text="%", row=1, col=1)
    figure.update_yaxes(title_text="%", row=1, col=2)
    return figure


def trade_rows(orders: pd.DataFrame) -> str:
    rows: list[str] = []
    for item in orders.itertuples(index=False):
        reason = SIGNAL_LABELS.get(str(item.primary_signal), str(item.primary_signal))
        matched = "；".join(SIGNAL_LABELS.get(signal, signal) for signal in str(item.matched_signals).split("|"))
        before_anchor = item.cost_basis_before if item.type == "sell" else item.last_sell_price_before
        rows.append(
            "<tr>"
            f"<td>{pd.Timestamp(item.date).date().isoformat()}</td>"
            f"<td>{str(item.type).upper()}</td>"
            f"<td>{html.escape(reason)}</td>"
            f"<td>{html.escape(matched)}</td>"
            f"<td>${float(item.theoretical_trigger):.4f}</td>"
            f"<td>${float(item.fill_price):.4f}</td>"
            f"<td>{html.escape(str(item.fill_source))}</td>"
            f"<td>{'—' if pd.isna(before_anchor) else f'${float(before_anchor):.4f}'}</td>"
            f"<td>{float(item.shares):,.4f}</td>"
            "</tr>"
        )
    return "".join(rows)


def summary_html(summary: dict[str, Any], orders: pd.DataFrame, ablations: pd.DataFrame) -> str:
    metrics = summary["strategy"]
    benchmark = summary["benchmark"]
    signal_rows = "".join(
        f"<tr><td>{html.escape(SIGNAL_LABELS.get(signal, signal))}</td><td>{count}</td></tr>"
        for signal, count in metrics["primary_signal_counts"].items()
    )
    ablation_rows = "".join(
        "<tr>"
        f"<td>{'全部规则' if pd.isna(item.disabled_signal) else '去掉：' + html.escape(SIGNAL_LABELS.get(str(item.disabled_signal), str(item.disabled_signal)))}</td>"
        f"<td>{item.cagr_pct:.3f}%</td><td>{item.sharpe:.3f}</td>"
        f"<td>{item.max_drawdown_pct:.3f}%</td><td>{int(item.order_count)}</td>"
        "</tr>"
        for item in ablations.itertuples(index=False)
    )
    user_orders = orders[~orders["is_initial_seed"].astype(bool)]
    return f"""
<h2>固定参数首轮结果</h2>
<p>从 2021-01-04 Open 的 100 股 QQQ（初始权益 {money(summary['initial_equity'])}）开始；截至 2026-08-04，策略期末 {money(metrics['final_equity'])}，总收益 <strong>{metrics['total_return_pct']:.2f}%</strong>、CAGR {metrics['cagr_pct']:.2f}%、Sharpe {metrics['sharpe']:.3f}、最大回撤 {metrics['max_drawdown_pct']:.2f}%。同期持有期末 {money(benchmark['final_equity'])}，总收益 <strong>{benchmark['total_return_pct']:.2f}%</strong>、CAGR {benchmark['cagr_pct']:.2f}%、Sharpe {benchmark['sharpe']:.3f}、最大回撤 {benchmark['max_drawdown_pct']:.2f}%。</p>
<p><strong>客观结论：</strong>这组固定参数把最大回撤改善了 {metrics['max_drawdown_pct'] - benchmark['max_drawdown_pct']:.2f} 个百分点，但总收益少了 {benchmark['total_return_pct'] - metrics['total_return_pct']:.2f} 个百分点，Sharpe 也低 {benchmark['sharpe'] - metrics['sharpe']:.3f}。共 {len(user_orders)} 次策略成交（{len(user_orders) // 2} 个完整卖出→买回循环），周转很高；因此首轮没有显示“已可用”，而是明确暴露了需要减少震荡换手的问题。</p>
<table><thead><tr><th>路径</th><th>期末净值</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>持仓率</th></tr></thead><tbody>
<tr><td>固定参数 OR 策略</td><td>{money(metrics['final_equity'])}</td><td>{metrics['total_return_pct']:.2f}%</td><td>{metrics['cagr_pct']:.2f}%</td><td>{metrics['sharpe']:.3f}</td><td>{metrics['max_drawdown_pct']:.2f}%</td><td>{metrics['exposure_pct']:.2f}%</td></tr>
<tr><td>QQQ 持有 100 股</td><td>{money(benchmark['final_equity'])}</td><td>{benchmark['total_return_pct']:.2f}%</td><td>{benchmark['cagr_pct']:.2f}%</td><td>{benchmark['sharpe']:.3f}</td><td>{benchmark['max_drawdown_pct']:.2f}%</td><td>100.00%</td></tr>
</tbody></table>
<h2>成交主因</h2><table><thead><tr><th>规则</th><th>作为首个触发原因的次数</th></tr></thead><tbody>{signal_rows}</tbody></table>
<p>盘中精确阈值成交 {metrics['fill_source_counts'].get('intraday_trigger', 0)} 次，隔夜越过后按 Open 成交 {metrics['fill_source_counts'].get('open_gap', 0)} 次。一个 Open 可能同时越过多条规则，逐笔表同时保留主因和全部已满足规则。</p>
<h2>逐条剔除诊断（不是参数优化）</h2>
<table><thead><tr><th>规则组合</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>策略成交数</th></tr></thead><tbody>{ablation_rows}</tbody></table>
<h2>全部策略成交</h2>
<table><thead><tr><th>日期</th><th>方向</th><th>主因</th><th>同时满足</th><th>理论阈值</th><th>实际成交</th><th>方式</th><th>当时锚点</th><th>股数</th></tr></thead><tbody>{trade_rows(user_orders)}</tbody></table>
"""


def markdown_report(summary: dict[str, Any], orders: pd.DataFrame, ablations: pd.DataFrame, run_id: str) -> str:
    metrics = summary["strategy"]
    benchmark = summary["benchmark"]
    user_orders = orders[~orders["is_initial_seed"].astype(bool)]
    lines = [
        "# QQQ 日内动态 SMA OR 策略首轮回测",
        "",
        f"- Run：`{run_id}`",
        f"- 区间：{summary['start']}～{summary['end']}，{summary['bars']} 个交易日",
        f"- 初始：{summary['start']} Open 以 $305.07175 持有 100 股，初始权益 {money(summary['initial_equity'])}",
        "- 参数：A=7、B=130、C=-0.15%、D=3、E=200、F=SMA25/30/35、G=1%、H=200、L=1.5%、R=1.5%",
        "- 成交：前一日收盘后冻结次日动态阈值；隔夜越过按 Open，否则 OHLC 触及后按理论阈值；零费用/零滑点；每日最多一笔",
        "",
        "## 结果",
        "",
        "| 路径 | 期末净值 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 固定参数 OR 策略 | {money(metrics['final_equity'])} | {metrics['total_return_pct']:.2f}% | {metrics['cagr_pct']:.2f}% | {metrics['sharpe']:.3f} | {metrics['max_drawdown_pct']:.2f}% | {metrics['exposure_pct']:.2f}% |",
        f"| QQQ 持有 100 股 | {money(benchmark['final_equity'])} | {benchmark['total_return_pct']:.2f}% | {benchmark['cagr_pct']:.2f}% | {benchmark['sharpe']:.3f} | {benchmark['max_drawdown_pct']:.2f}% | 100.00% |",
        "",
        f"这组参数把最大回撤改善 {metrics['max_drawdown_pct'] - benchmark['max_drawdown_pct']:.2f} 个百分点，但总收益少 {benchmark['total_return_pct'] - metrics['total_return_pct']:.2f} 个百分点，Sharpe 低 {benchmark['sharpe'] - metrics['sharpe']:.3f}。共 {len(user_orders)} 次策略成交，存在明显震荡换手；不能直接进入模拟盘。",
        "",
        "## 规则主因次数",
        "",
    ]
    for signal, count in metrics["primary_signal_counts"].items():
        lines.append(f"- {SIGNAL_LABELS.get(signal, signal)}：{count} 次")
    lines.extend(["", "## 逐条剔除诊断", "", "| 组合 | CAGR | Sharpe | 最大回撤 | 成交数 |", "|---|---:|---:|---:|---:|"])
    for item in ablations.itertuples(index=False):
        label = "全部规则" if pd.isna(item.disabled_signal) else f"去掉 {SIGNAL_LABELS.get(str(item.disabled_signal), str(item.disabled_signal))}"
        lines.append(f"| {label} | {item.cagr_pct:.3f}% | {item.sharpe:.3f} | {item.max_drawdown_pct:.3f}% | {int(item.order_count)} |")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 这是固定参数的全样本探索与逻辑检查，不是样本外验证，也没有在看到结果后换参数。",
            "- 调整后 OHLC 不提供真实夜盘路径或分钟级先后顺序；预挂单隔夜越过一律按常规时段 Open，盘中只允许一笔成交。",
            "- 当日动态均线变化率以当时价格代入预先确定的线性公式；最终 Close 的变化率可能不再满足阈值，这是预挂条件单的正常结果。",
            "- 数据是拆股及股息调整价格，适合总回报近似，但不是现金分红逐笔入账的券商账户重放。",
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
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    ablations = pd.read_csv(block / "ablation_results.csv")
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((block / "manifest.json").read_text(encoding="utf-8"))
    benchmark_metrics = manifest["benchmark_metrics"]
    parameters = context.config["parameters"]
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "purpose": "fixed-parameter in-sample engineering and behavior audit; no optimization",
        "start": parameters["analysis_start"],
        "end": parameters["analysis_end"],
        "bars": len(daily),
        "initial_equity": float(context.config["initial_cash"]),
        "strategy": metrics,
        "benchmark": benchmark_metrics,
        "ablation_results": json_safe(ablations.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    raw = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    prepared = prepare_intraday_sma_data(raw, IntradaySmaSpec.from_parameters(parameters))
    prices = prepared[
        (prepared["date"] >= pd.Timestamp(parameters["analysis_start"]))
        & (prepared["date"] <= pd.Timestamp(parameters["analysis_end"]))
    ].reset_index(drop=True)
    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            "价格、全部可选均线、变化率与逐笔原因",
            build_market_figure(prices, orders),
            "market",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            "策略与 QQQ 持有基准",
            build_performance_figure(daily, benchmark),
            "performance",
        ),
        ReportFigure(
            "ablation-qqq",
            "逐条剔除规则诊断",
            build_ablation_figure(ablations),
            "generic",
        ),
    ]
    (run_root / "report.md").write_text(
        markdown_report(summary, orders, ablations, args.run_id), encoding="utf-8"
    )
    report = render_interactive_report(
        title="QQQ 日内动态 SMA OR 策略首轮回测",
        heading="QQQ 日内动态 SMA OR 策略首轮回测",
        subtitle="2021-01-04 Open 持有 100 股；固定 A–H、L、R，不优化；动态阈值用前一日及更早 Close 预先计算。",
        summary_html=summary_html(summary, orders, ablations),
        notes=[
            "主图保留 SMA25/30/35、三线平均及 SMA70–450（间隔 10）；下图 4 条日变化率和所有成交标记均可独立勾选。",
            "隔夜越过阈值按当日 Open；常规时段触及按精确理论阈值。调整日线没有真实夜盘路径，也不能判定同一日多个极值的分钟级先后，因此固定每日最多一笔。",
            "成交悬浮和下方逐笔表同时显示主因、同一成交时刻已满足的规则、理论阈值、实际价格以及 open_gap / intraday_trigger。",
            "净值图可勾选、区间左端对齐和生成等额定投；这些只在浏览器中改变显示，不改写正式账本。",
            "本轮只检查固定策略逻辑和行为，不据结果调参；逐条剔除仅用于发现规则主导与震荡来源。",
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
        "backtest/quantkit/intraday_sma.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/smoke_report_ui.mjs",
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

This immutable run evaluates the frozen QQQ intraday SMA OR strategy in `experiment_snapshot.json`.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable metrics, rule counts, and ablations.
- `QQQ/cost_0bps/`: PyBroker output, independent ledger, trigger plans, metrics, and hashes.
- `provenance.json`: exact source, dependency, template, manifest, and price-data hashes.
- `validation.json`: mandatory test, audit, hash, and real-browser gate evidence.

Browser rebasing and DCA are display-only scenarios and never mutate the formal ledger.
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
    print(
        f"strategy={metrics['total_return_pct']:.6f}%, "
        f"buy_hold={benchmark_metrics['total_return_pct']:.6f}%, "
        f"orders={metrics['order_count']}"
    )


if __name__ == "__main__":
    main()
