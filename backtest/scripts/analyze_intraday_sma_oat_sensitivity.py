#!/usr/bin/env python3
"""Build a chart-first QQQ OAT sensitivity report."""

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
import plotly.io as pio
from plotly.subplots import make_subplots

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.intraday_sma import prepare_intraday_sma_data
from quantkit.oat_sensitivity import baseline_plateau_diagnostics
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_intraday_sma_global_search import spec_from_row


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30b.1__26-08-13__qqq_intraday_sma_oat_sensitivity_two_periods"
WINDOW_LABELS = {
    "W1999_2009": "1999–2009",
    "W2010_2026": "2010–2026",
    "FULL_HISTORY": "1999–2026 全历史",
}
COLORS = {
    "W1999_2009": "#2563eb",
    "W2010_2026": "#dc2626",
    "FULL_HISTORY": "#0f766e",
}


def configured_windows(config: dict[str, Any]) -> None:
    """Expose configured labels and stable colors to reusable chart helpers."""

    palette = ("#2563eb", "#dc2626", "#0f766e", "#7c3aed")
    for index, window in enumerate(config["parameters"]["windows"]):
        window_id = str(window["window_id"])
        WINDOW_LABELS[window_id] = str(window.get("label", window_id))
        COLORS[window_id] = palette[index % len(palette)]


def display_parameter_value(value: Any, parameter: str) -> str:
    if parameter == "forced_reentry_enabled":
        return "开启" if str(value).strip().lower() in {"true", "1", "1.0"} else "关闭"
    return f"{float(value):g}"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    formal: pd.DataFrame,
    *,
    window_id: str,
) -> go.Figure:
    baseline = formal[
        formal["window_id"].eq(window_id)
        & formal["selection_reason"].str.contains("baseline", regex=False)
    ].iloc[0]
    formal_case_id = str(baseline["formal_case_id"])
    strategy = daily[daily["formal_case_id"] == formal_case_id].sort_values("date")
    buy_hold = benchmark[benchmark["formal_case_id"] == formal_case_id].sort_values("date")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("基线参数账户净值", "从各自峰值回撤"),
    )
    for frame, key, label, color, dash, is_benchmark in (
        (strategy, "strategy", "OAT 基线策略", "#0f766e", "solid", False),
        (buy_hold, "buy_hold", "QQQ 窗口首日买入持有", "#334155", "dash", True),
    ):
        for row, values, panel in (
            (1, frame["equity"], "equity"),
            (2, base.drawdown(frame["equity"]), "drawdown"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    line={"color": color, "width": 2.3, "dash": dash},
                    meta={
                        "series_key": key,
                        "panel": panel,
                        "label": label,
                        "is_benchmark": is_benchmark and panel == "equity",
                        "cost_bps": 0,
                    },
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row,
                col=1,
            )
    figure.update_layout(
        height=760,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision="qqq-oat-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def sensitivity_figure(group: pd.DataFrame, plateau: pd.DataFrame | None = None) -> go.Figure:
    parameter = str(group.iloc[0]["parameter"])
    categorical = parameter == "forced_reentry_enabled"
    figure = make_subplots(rows=1, cols=2, subplot_titles=("CAGR", "Sharpe"))
    for window_id in dict.fromkeys(group["window_id"]):
        window = group[group["window_id"] == window_id].sort_values("point_order")
        x = (
            window["value"].map(
                lambda value: "开启" if str(value).strip().lower() in {"true", "1", "1.0"} else "关闭"
            )
            if categorical
            else window["value"].astype(float)
        )
        for column, metric in enumerate(("cagr_pct", "sharpe"), start=1):
            custom = list(zip(window["is_baseline"], window["order_count"], strict=True))
            figure.add_trace(
                go.Scatter(
                    x=x,
                    y=window[metric],
                    mode="lines+markers",
                    name=WINDOW_LABELS.get(window_id, window_id),
                    legendgroup=window_id,
                    showlegend=column == 1,
                    line={"color": COLORS.get(window_id, "#0f766e"), "width": 2.2},
                    marker={
                        "color": COLORS.get(window_id, "#0f766e"),
                        "size": [11 if flag else 6 for flag in window["is_baseline"]],
                        "symbol": ["diamond" if flag else "circle" for flag in window["is_baseline"]],
                    },
                    customdata=custom,
                    hovertemplate=(
                        f"{WINDOW_LABELS.get(window_id, window_id)}<br>参数 %{{x}}"
                        f"<br>{'CAGR' if metric == 'cagr_pct' else 'Sharpe'} %{{y:.4f}}"
                        "<br>基线点 %{customdata[0]}<br>成交 %{customdata[1]:.0f}<extra></extra>"
                    ),
                ),
                row=1,
                col=column,
            )
            winner = window.loc[window[metric].astype(float).idxmax()]
            figure.add_trace(
                go.Scatter(
                    x=["开启" if bool(winner["value"]) else "关闭"] if categorical else [float(winner["value"])],
                    y=[float(winner[metric])],
                    mode="markers",
                    name=f"{WINDOW_LABELS.get(window_id, window_id)} {'CAGR' if metric == 'cagr_pct' else 'Sharpe'} 最高",
                    showlegend=False,
                    marker={"color": COLORS.get(window_id, "#0f766e"), "size": 14, "symbol": "star", "line": {"color": "white", "width": 1}},
                    hovertemplate="曲线最高点<br>参数 %{x}<br>指标 %{y:.4f}<extra></extra>",
                ),
                row=1,
                col=column,
            )
            if plateau is not None and not categorical:
                matched = plateau[
                    plateau["window_id"].eq(window_id)
                    & plateau["sweep_id"].eq(group.iloc[0]["sweep_id"])
                ]
                if len(matched) == 1 and bool(matched.iloc[0]["baseline_qualifies"]):
                    figure.add_vrect(
                        x0=float(matched.iloc[0]["plateau_min_value"]),
                        x1=float(matched.iloc[0]["plateau_max_value"]),
                        fillcolor=COLORS.get(window_id, "#0f766e"),
                        opacity=0.08,
                        line_width=0,
                        row=1,
                        col=column,
                    )
    figure.update_yaxes(title_text="%", row=1, col=1, fixedrange=False)
    figure.update_yaxes(title_text="Sharpe", row=1, col=2, fixedrange=False)
    figure.update_xaxes(title_text=str(group.iloc[0]["parameter_label"]), fixedrange=False)
    figure.update_layout(
        height=530,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 65, "r": 25, "t": 75, "b": 65},
    )
    return figure


def summary_table(summary: pd.DataFrame) -> go.Figure:
    cagr = summary[summary["metric"] == "cagr_pct"].copy()
    cagr["window"] = cagr["window_id"].map(WINDOW_LABELS)
    cagr["baseline"] = cagr["baseline_metric"].map(lambda value: f"{value:.3f}%")
    cagr["best"] = cagr.apply(
        lambda row: (
            f"{display_parameter_value(row.best_value, row.parameter)}"
            f" → {row.best_metric:.3f}%"
        ),
        axis=1,
    )
    cagr["range"] = cagr["metric_range"].map(lambda value: f"{value:.3f} pp")
    cagr["boundary"] = cagr["best_at_boundary"].map({True: "是", False: "否"})
    cagr["shape"] = cagr.apply(
        lambda row: "完全平坦" if row.curve_is_flat else (
            f"{int(row.best_tie_count)} 个并列最高" if int(row.best_tie_count) > 1 else "唯一最高"
        ),
        axis=1,
    )
    figure = go.Figure(
        data=[
            go.Table(
                header={
                    "values": ["参数", "时期", "基线 CAGR", "代表最好值 → CAGR", "曲线范围", "形状", "代表值在边界"],
                    "fill_color": "#0f766e",
                    "font": {"color": "white"},
                    "align": "left",
                },
                cells={
                    "values": [cagr[column] for column in ("parameter_label", "window", "baseline", "best", "range", "shape", "boundary")],
                    "fill_color": "#f8fafc",
                    "align": "left",
                    "height": 27,
                },
            )
        ]
    )
    figure.update_layout(height=820, margin={"l": 20, "r": 20, "t": 25, "b": 20})
    return figure


def plateau_table(plateau: pd.DataFrame) -> go.Figure:
    view = plateau.copy()
    labels = {
        "two_sided_plateau": "双侧高原",
        "one_sided_plateau": "单侧高原",
        "narrow_or_spike": "窄区间 / 尖峰",
        "baseline_outside_joint_band": "基线不在联合近最高带",
    }
    view["classification_label"] = view["classification"].map(labels)
    view["baseline"] = view.apply(
        lambda row: display_parameter_value(row.baseline_value, row.parameter), axis=1
    )
    view["plateau"] = view.apply(
        lambda row: "无" if pd.isna(row.plateau_min_value) else (
            f"{display_parameter_value(row.plateau_min_value, row.parameter)} – "
            f"{display_parameter_value(row.plateau_max_value, row.parameter)}"
        ), axis=1,
    )
    figure = go.Figure(
        data=[
            go.Table(
                header={
                    "values": ["参数", "基线", "相连高原", "点数", "判定"],
                    "fill_color": "#0f766e",
                    "font": {"color": "white"},
                    "align": "left",
                },
                cells={
                    "values": [
                        view["parameter_label"],
                        view["baseline"],
                        view["plateau"],
                        view["plateau_point_count"],
                        view["classification_label"],
                    ],
                    "fill_color": "#f8fafc",
                    "align": "left",
                    "height": 29,
                },
            )
        ]
    )
    figure.update_layout(height=285, margin={"l": 20, "r": 20, "t": 25, "b": 20})
    return figure


PARAMETER_SHORT_LABELS = {
    "A_negative_days_slow": "A 慢趋势连续负变化日数",
    "B_slow_sma_window": "B 慢趋势卖出 SMA",
    "E_fallback_sma_window": "E 兜底卖出 SMA",
    "F_short_sma_center": "F 三短 SMA 中心",
    "F_short_sma_spacing": "F 三短 SMA 间隔",
    "G_short_recovery_below_pct": "G 短均线恢复买入偏移 (%)",
    "H_reentry_sma_window": "H 趋势恢复买入 SMA",
    "L_cost_stop_pct": "L 成本止损 (%)",
    "R_forced_rebuy_pct": "R 卖价上方强制买回 (%)",
}


def strategy_card_html(config: dict[str, Any]) -> str:
    """Return the complete frozen strategy before any result metric."""

    strategy = config["strategy"]
    parameters = config["parameters"]
    baseline = parameters["baseline"]
    short = (
        float(baseline["F_short_sma_center"]) - float(baseline["F_short_sma_spacing"]),
        float(baseline["F_short_sma_center"]),
        float(baseline["F_short_sma_center"]) + float(baseline["F_short_sma_spacing"]),
    )
    values = "；".join(
        f"{html.escape(PARAMETER_SHORT_LABELS[name])}={float(baseline[name]):g}"
        for name in PARAMETER_SHORT_LABELS
    )
    windows = "；".join(
        f"{html.escape(str(window.get('label', window['window_id'])))}：{window['analysis_start']}～{window['analysis_end']}"
        for window in parameters["windows"]
    )
    return f"""
<section class="strategy-freeze" style="border:1px solid #94a3b8;border-left:6px solid #0f766e;border-radius:8px;padding:14px 16px;margin:8px 0 16px;background:#f8fafc">
  <h2 style="margin-top:0">冻结策略（结果之前完整复述）</h2>
  <p><strong>策略：</strong>{html.escape(str(strategy['description']))}</p>
  <p><strong>买入：</strong>{html.escape(str(strategy['buy_rule']))}</p>
  <p><strong>卖出：</strong>{html.escape(str(strategy['sell_rule']))}</p>
  <p><strong>时点与成交：</strong>{html.escape(str(strategy['signal_time']))} {html.escape(str(strategy['execution_time']))}</p>
  <p><strong>仓位与成本：</strong>{html.escape(str(strategy['position_sizing']))}；{html.escape(str(config['financing']))}；单边成本 0 bps。</p>
  <p><strong>冻结基线：</strong>{values}；三短均线={short[0]:g}/{short[1]:g}/{short[2]:g}；强制买回=开启；<strong>C/D 快速导数卖出=关闭</strong>。</p>
  <p><strong>评价窗口：</strong>{windows}。每窗独立从 100,000 美元现金与空仓开始；Buy &amp; Hold 在评价首日 Open 买入。</p>
  <p><strong>研究性质：</strong>每次只扰动一个参数；不重新选参、不采用本轮极值。两个窗口有重叠，2005–2015 不是独立样本外。</p>
</section>"""


def baseline_metrics_rows(formal: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline = formal[formal["selection_reason"].str.contains("baseline", regex=False)]
    for row in baseline.itertuples(index=False):
        rows.append(
            {
                "window_id": row.window_id,
                "strategy_cagr": float(row.cagr_pct),
                "strategy_sharpe": float(row.sharpe),
                "strategy_drawdown": float(row.max_drawdown_pct),
                "strategy_exposure": float(row.exposure_pct),
                "orders": int(row.order_count),
                "benchmark_cagr": float(row.benchmark_cagr_pct),
                "benchmark_sharpe": float(row.benchmark_sharpe),
                "benchmark_drawdown": float(row.benchmark_max_drawdown_pct),
            }
        )
    return rows


def html_metrics_table(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(WINDOW_LABELS.get(row['window_id'], row['window_id']))}</td>"
            f"<td>{row['strategy_cagr']:.3f}%</td><td>{row['strategy_sharpe']:.3f}</td>"
            f"<td>{row['strategy_drawdown']:.2f}%</td><td>{row['strategy_exposure']:.1f}%</td>"
            f"<td>{row['orders']}</td><td>{row['benchmark_cagr']:.3f}%</td>"
            f"<td>{row['benchmark_sharpe']:.3f}</td><td>{row['benchmark_drawdown']:.2f}%</td></tr>"
        )
    return (
        "<table><thead><tr><th>窗口</th><th>策略 CAGR</th><th>策略 Sharpe</th>"
        "<th>策略 MDD</th><th>持仓率</th><th>订单</th><th>B&amp;H CAGR</th>"
        "<th>B&amp;H Sharpe</th><th>B&amp;H MDD</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def write_print_report(
    path: Path,
    *,
    config: dict[str, Any],
    sensitivity: pd.DataFrame,
    plateau: pd.DataFrame,
    intersections: pd.DataFrame,
    formal: pd.DataFrame,
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> None:
    """Write the single rules-first HTML source converted to the combined PDF."""

    common_style = """
@page{size:A4 landscape;margin:9mm}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#172033;margin:0}.page{page-break-after:always;min-height:185mm;padding:3mm}.page:last-child{page-break-after:auto}h1{font-size:24px;margin:0 0 9px}h2{font-size:16px;margin:9px 0 5px}p,li{font-size:10.5px;line-height:1.45;margin:5px 0}.callout{background:#edf7f4;border-left:5px solid #0f766e;padding:7px 11px}table{border-collapse:collapse;width:100%;font-size:9px;margin-top:8px}th,td{border:1px solid #cbd5e1;padding:4px;text-align:right}th:first-child,td:first-child{text-align:left}th{background:#e9eef5}.small{font-size:9px;color:#526071}.plot{height:150mm;width:100%}.strategy-freeze{font-size:9px}.strategy-freeze h2{font-size:15px}.strategy-freeze p{font-size:9.6px;line-height:1.35}
"""
    sections = [
        f"<section class='page'><h1>{html.escape(str(config['reporting']['report_title']))}</h1>"
        "<div class='callout'><strong>单参数扰动诊断</strong>：菱形为冻结基线，星形为各时期各指标最高点，色带为与基线相连的联合近最高区间。</div>"
        f"{strategy_card_html(config)}</section>"
    ]
    baseline_rows = baseline_metrics_rows(formal)
    for window in config["parameters"]["windows"]:
        window_id = str(window["window_id"])
        perf = performance_figure(daily, benchmark, formal, window_id=window_id)
        plot = pio.to_html(
            perf,
            full_html=False,
            include_plotlyjs="inline" if len(sections) == 1 else False,
            config={"displayModeBar": False, "responsive": True},
            div_id=f"print-performance-{window_id}",
        )
        metric_row = [row for row in baseline_rows if row["window_id"] == window_id]
        sections.append(
            f"<section class='page'><h1>{html.escape(WINDOW_LABELS.get(window_id, window_id))}：冻结基线与 Buy &amp; Hold</h1>"
            f"{html_metrics_table(metric_row)}<div class='plot'>{plot}</div></section>"
        )
    for sweep_id, group in sensitivity.groupby("sweep_id", sort=False):
        figure = sensitivity_figure(group, plateau)
        plot = pio.to_html(
            figure,
            full_html=False,
            include_plotlyjs=False,
            config={"displayModeBar": False, "responsive": True},
            div_id=f"print-sensitivity-{str(sweep_id).lower()}",
        )
        diag = plateau[plateau["sweep_id"].eq(sweep_id)]
        diagnostic_rows = []
        for row in diag.itertuples(index=False):
            interval = "无基线相连高原" if not row.baseline_qualifies else f"{float(row.plateau_min_value):g}–{float(row.plateau_max_value):g}"
            diagnostic_rows.append(
                f"<tr><td>{html.escape(WINDOW_LABELS.get(row.window_id, row.window_id))}</td>"
                f"<td>{interval}</td><td>{html.escape(str(row.classification))}</td></tr>"
            )
        sections.append(
            f"<section class='page'><h1>{html.escape(str(group.iloc[0]['parameter_label']))}</h1>"
            "<p class='small'>联合近最高带：CAGR ≥ 本曲线最高 CAGR−0.75 个百分点，且 Sharpe ≥ 本曲线最高 Sharpe−0.05；仅取包含冻结基线的连续区间。</p>"
            f"<table><thead><tr><th>窗口</th><th>基线相连区间</th><th>形状判定</th></tr></thead><tbody>{''.join(diagnostic_rows)}</tbody></table>"
            f"<div class='plot'>{plot}</div></section>"
        )
    intersection_rows = []
    for row in intersections.itertuples(index=False):
        interval = (
            f"{float(row.intersection_min_value):g}–{float(row.intersection_max_value):g}"
            if row.has_intersection else "无"
        )
        intersection_rows.append(
            f"<tr><td>{html.escape(str(row.parameter_label))}</td><td>{float(row.baseline_value):g}</td>"
            f"<td>{interval}</td><td>{'是' if row.has_intersection else '否'}</td></tr>"
        )
    sections.append(
        "<section class='page'><h1>跨窗口高原交集与解释边界</h1>"
        f"<table><thead><tr><th>参数</th><th>冻结基线</th><th>两窗交集</th><th>存在交集</th></tr></thead><tbody>{''.join(intersection_rows)}</tbody></table>"
        "<h2>如何阅读</h2><ul><li>交集只表示两个重叠历史窗口的一维局部稳定区间，不是独立样本外验证。</li>"
        "<li>每次只改变一个参数，未覆盖参数联动；曲线最高点仅作诊断，本轮不改参数。</li>"
        "<li>使用调整后日线 OHLC 推断盘中条件单触及，零交易成本；不存在逐笔成交表。</li></ul></section>"
    )
    path.write_text(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{html.escape(str(config['reporting']['report_title']))}</title><style>{common_style}</style>"
        "</head><body>" + "".join(sections) + "</body></html>",
        encoding="utf-8",
    )


def markdown_report(
    run_id: str,
    run_summary: dict[str, Any],
    sensitivity_summary: pd.DataFrame,
    plateau: pd.DataFrame,
    correlations: pd.DataFrame,
    config: dict[str, Any],
) -> str:
    boundary = sensitivity_summary[
        sensitivity_summary["best_at_boundary"].astype(bool)
    ]
    title = str(run_summary.get("report_title", "QQQ OAT 参数敏感性"))
    lines = [
        f"# {title}",
        "",
        "## 冻结策略",
        "",
        f"- {config['strategy']['description']}",
        f"- 买入：{config['strategy']['buy_rule']}",
        f"- 卖出：{config['strategy']['sell_rule']}",
        f"- 时点：{config['strategy']['signal_time']} {config['strategy']['execution_time']}",
        f"- 仓位：{config['strategy']['position_sizing']}；C/D 快速导数卖出关闭。",
        "",
        "## 结果摘要",
        "",
        f"- Run：`{run_id}`；{run_summary['unique_candidate_count']} 个唯一参数、{run_summary['case_window_count']} 个窗口回测、{run_summary['formal_case_count']} 个正式复核 case。",
        "- 绩效从窗口首日 100,000 美元现金开始；Buy & Hold 在窗口首日 Open 买入，所有参数使用完全相同的评价日期。",
        "- 每次只改变一个参数，所以这些曲线只说明基线附近的一维局部敏感性，不说明参数联动。",
        "",
        "## CAGR 局部最优",
        "",
        "| 参数 | 时期 | 基线 CAGR | 代表最好值 | 最好 CAGR | 范围 | 形状 | 代表值在边界 |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in sensitivity_summary[sensitivity_summary["metric"] == "cagr_pct"].itertuples(index=False):
        lines.append(
            f"| {row.parameter_label} | {WINDOW_LABELS.get(row.window_id, row.window_id)} | {row.baseline_metric:.3f}% | {display_parameter_value(row.best_value, row.parameter)} | {row.best_metric:.3f}% | {row.metric_range:.3f} pp | {'完全平坦' if row.curve_is_flat else (str(int(row.best_tie_count)) + ' 个并列最高' if int(row.best_tie_count) > 1 else '唯一最高')} | {'是' if row.best_at_boundary else '否'} |"
        )
    if not correlations.empty:
        lines.extend(["", "## 跨时期方向一致性", ""])
        for row in correlations.itertuples(index=False):
            metric = "CAGR" if row.metric == "cagr_pct" else "Sharpe"
            spearman = "NA" if pd.isna(row.spearman_rank_correlation) else f"{row.spearman_rank_correlation:.3f}"
            lines.append(f"- {row.parameter_label} · {metric}：Spearman {spearman}。")
    lines.extend(
        [
            "",
            "## 冻结基线的相连高原",
            "",
            "高原门槛：相对各曲线最高 CAGR 最多下降 0.75 个百分点且相对各曲线最高 Sharpe 最多下降 0.05；只计入与冻结基线连续相连的点。",
            "",
            "| 参数 | 基线 | 相连区间 | 点数 | 判定 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    labels = {
        "two_sided_plateau": "双侧高原",
        "one_sided_plateau": "单侧高原",
        "narrow_or_spike": "窄区间 / 尖峰",
        "baseline_outside_joint_band": "基线不在联合近最高带",
    }
    for row in plateau.itertuples(index=False):
        interval = "无" if pd.isna(row.plateau_min_value) else (
            f"{display_parameter_value(row.plateau_min_value, row.parameter)}–"
            f"{display_parameter_value(row.plateau_max_value, row.parameter)}"
        )
        lines.append(
            f"| {row.parameter_label} | {display_parameter_value(row.baseline_value, row.parameter)} | "
            f"{interval} | "
            f"{int(row.plateau_point_count)} | {labels[row.classification]} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            f"- 排除完全平坦曲线后，{len(boundary[~boundary['curve_is_flat'].astype(bool)])} / {len(sensitivity_summary[~sensitivity_summary['curve_is_flat'].astype(bool)])} 个“参数 × 时期 × 目标”的代表最好点落在测试边界。并列最高时固定优先展示基线，避免把平台误读成边界最优。",
            "- 这是已知基线周围的 OAT 诊断，不是重新做全局优化，也不是样本外证明。",
            "- 报告不包含逐笔买卖表；正式候选的订单、交易、信号计划和每日账户状态保存在机器账本。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    configured_windows(context.config)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 0.0)
    sensitivity = pd.read_csv(block / "sensitivity_results.csv", parse_dates=["first_entry_date"])
    sweep_summary = pd.read_csv(block / "sensitivity_summary.csv")
    plateau_path = block / "baseline_plateau_diagnostics.csv"
    plateau = (
        pd.read_csv(plateau_path)
        if plateau_path.is_file()
        else baseline_plateau_diagnostics(sensitivity)
    )
    correlations = pd.read_csv(block / "cross_period_correlations.csv")
    intersections_path = block / "cross_window_plateau_intersections.csv"
    intersections = (
        pd.read_csv(intersections_path)
        if intersections_path.is_file()
        else pd.DataFrame()
    )
    formal = pd.read_csv(block / "formal_candidate_results.csv", parse_dates=["first_entry_date"])
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    run_summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        **run_summary,
        "sensitivity_summary": json_safe(sweep_summary.to_dict("records")),
        "baseline_plateau_diagnostics": json_safe(plateau.to_dict("records")),
        "cross_window_plateau_intersections": json_safe(intersections.to_dict("records")),
        "cross_period_correlations": json_safe(correlations.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    baseline = context.config["parameters"]["baseline"]
    windows = context.config["parameters"]["windows"]
    late_id = str(context.config["parameters"].get("report_primary_window_id", windows[-1]["window_id"]))
    baseline_formal = formal[
        formal["window_id"].eq(late_id)
        & formal["selection_reason"].str.contains("baseline", regex=False)
    ].iloc[0]
    baseline_case = str(baseline_formal["formal_case_id"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    spec = spec_from_row(pd.Series(baseline))
    prices = prepare_intraday_sma_data(raw, spec)
    report_window = next(window for window in windows if window["window_id"] == late_id)
    prices = prices[
        (prices["date"] >= pd.Timestamp(report_window["analysis_start"]))
        & (prices["date"] <= pd.Timestamp(report_window["analysis_end"]))
    ].reset_index(drop=True)
    late_orders = orders[orders["formal_case_id"] == baseline_case]

    nonflat = ~sweep_summary["curve_is_flat"].astype(bool)
    boundary_count = int((sweep_summary["best_at_boundary"].astype(bool) & nonflat).sum())
    compact = strategy_card_html(context.config) + (
        f"<p><strong>{run_summary['unique_candidate_count']}</strong> 个唯一 OAT 组合在 "
        f"<strong>{len(windows)}</strong> 个评价窗口形成 "
        f"<strong>{run_summary['case_window_count']}</strong> 个窗口回测；正式复核 "
        f"<strong>{run_summary['formal_case_count']}</strong> 个去重候选。"
        f"在 {int(nonflat.sum())} 条非平坦“参数 × 时期 × 指标”曲线中，"
        f"<strong>{boundary_count}</strong> 个代表最好点落在范围边界。"
        "每张参数图的菱形点是冻结基线。</p>"
    )
    figures = []
    for sweep_id, group in sensitivity.groupby("sweep_id", sort=False):
        figures.append(
            ReportFigure(
                f"sensitivity-{str(sweep_id).lower().replace('_', '-')}",
                str(group.iloc[0]["parameter_label"]),
                sensitivity_figure(group, plateau),
                "generic",
            )
        )
    figures.extend(
        [
            ReportFigure(
                "baseline-plateau-summary",
                "冻结基线的相连高原",
                plateau_table(plateau),
                "generic",
            ),
            ReportFigure("cagr-summary", "CAGR 局部最优与曲线范围", summary_table(sweep_summary), "generic"),
        ]
    )
    for index, window in enumerate(windows):
        window_id = str(window["window_id"])
        figures.append(
            ReportFigure(
                "performance-qqq" if index == 0 else f"performance-qqq-{index + 1}",
                f"{WINDOW_LABELS.get(window_id, window_id)} 基线策略与统一起点 Buy & Hold",
                performance_figure(daily, benchmark, formal, window_id=window_id),
                "performance" if index == 0 else "other",
            )
        )
    if bool(context.config["reporting"].get("include_market_figure", True)):
        figures.append(
            ReportFigure(
                "market-qqq",
                f"{WINDOW_LABELS.get(late_id, late_id)} 基线参数的价格、均线与成交原因",
                base.build_market_figure(prices, late_orders, short_windows=spec.f_short_sma_windows),
                "market",
            )
        )
    report_title = str(context.config["reporting"].get("report_title", "QQQ OAT 参数敏感性"))
    report_subtitle = str(
        context.config["reporting"].get(
            "report_subtitle",
            "每次只改变一个参数，观察 CAGR / Sharpe 的局部曲线。",
        )
    )
    run_summary["report_title"] = report_title
    report = render_interactive_report(
        title=report_title,
        heading=report_title,
        subtitle=report_subtitle,
        summary_html=compact,
        notes=[
            "窗口可使用此前 Close 预热均线，但评价窗口前禁止交易。",
            "策略和基准均从窗口首日统一计绩；空仓等待首买是策略表现的一部分。",
            "OAT 曲线不包含参数交互效应；它用于局部形状诊断，不是样本外证明。",
            "高原诊断采用预先声明的双门槛：相对各曲线最高 CAGR 不低超过 0.75 个百分点、相对各曲线最高 Sharpe 不低超过 0.05，并只计算与冻结基线相连的区间。",
            "逐笔成交不生成表格；成交原因仅在 K 线 hover 和机器账本中保留。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, run_summary, sweep_summary, plateau, correlations, context.config), encoding="utf-8"
    )
    combined_pdf = bool(context.config["reporting"].get("combined_pdf", False))
    if combined_pdf:
        write_print_report(
            run_root / "report_print.html",
            config=context.config,
            sensitivity=sensitivity,
            plateau=plateau,
            intersections=intersections,
            formal=formal,
            daily=daily,
            benchmark=benchmark,
        )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py",
        "backtest/quantkit/intraday_sma_search.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/oat_sensitivity.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma_global_search.py",
        "backtest/scripts/run_intraday_sma_oat_sensitivity.py",
        "backtest/scripts/analyze_intraday_sma_oat_sensitivity.py",
        "backtest/scripts/finalize_intraday_sma_oat_sensitivity.py",
        "backtest/scripts/print_html_pdf.mjs",
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
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run contains the configured QQQ one-at-a-time sensitivity analysis.

- `report.pdf`: combined rules-first two-window PDF.
- `report.html` / `report.md`: chart-first reports without a per-order table.
- `report_print.html`: deterministic print source for the combined PDF.
- `analysis/summary.json`: machine-readable curve summaries and plateau diagnostics.
- `QQQ/cost_0bps/`: all case-window scores, plotted sweep points and selected formal ledgers.
- `provenance.json` / `validation.json`: source and correctness evidence.
""",
        encoding="utf-8",
    )
    if combined_pdf:
        print(f"Wrote {run_root / 'report_print.html'}; PDF printing and finalization remain pending")
        return

    artifact_manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
