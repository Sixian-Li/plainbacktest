#!/usr/bin/env python3
"""Build reports for the two-window QQQ four-exit-rule ablation."""

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

from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from quantkit.sma_entry_exit_ablation import CASE_FLAGS


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.20__26-08-14__qqq_sma_entry_four_exit_ablation_two_periods"
WINDOW_LABELS = {"2010_2015": "2010–2015", "2020_2026": "2020–2026"}
RULE_LABELS = {
    "r1": "R1 SMA200连续3日下降",
    "r2": "R2 短均线平均单日跌幅>0.15%",
    "r3": "R3 Close<SMA200",
    "r4": "R4 Close<SMA30",
}
PALETTE = [
    "#475569", "#2563eb", "#d97706", "#0f766e", "#7c3aed", "#db2777",
    "#0891b2", "#65a30d", "#9333ea", "#ea580c", "#0284c7", "#16a34a",
    "#be123c", "#4f46e5", "#a16207", "#111827",
]


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


def mask_number(case_id: str) -> int:
    return int(case_id.split("_")[1])


def short_label(case_id: str) -> str:
    enabled = [f"R{index + 1}" for index, flag in enumerate(CASE_FLAGS[case_id]) if flag]
    return "+".join(enabled) if enabled else "无卖出规则"


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def rank_results(results: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    ranked = results.copy()
    ranked["drawdown_rank"] = ranked.groupby("window_id")["max_drawdown_pct"].rank(
        method="min", ascending=False
    )
    ranked["return_rank"] = ranked.groupby("window_id")["total_return_pct"].rank(
        method="min", ascending=False
    )
    average = ranked.groupby("case_id", as_index=False).agg(
        cross_window_mean_drawdown_rank=("drawdown_rank", "mean"),
        cross_window_mean_return_rank=("return_rank", "mean"),
    )
    ranked = ranked.merge(average, on="case_id", how="left")
    representative = average.sort_values(
        ["cross_window_mean_drawdown_rank", "cross_window_mean_return_rank", "case_id"]
    ).iloc[0]["case_id"]
    return ranked, str(representative)


def factorial_effects(results: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window_id in WINDOW_LABELS:
        frame = results[results["window_id"].eq(window_id)]
        for index in range(1, 5):
            column = f"r{index}_enabled"
            enabled = frame[frame[column].astype(bool)]
            disabled = frame[~frame[column].astype(bool)]
            rows.append(
                {
                    "window_id": window_id,
                    "rule": f"r{index}",
                    "max_drawdown_effect_pct_points": float(
                        enabled["max_drawdown_pct"].mean()
                        - disabled["max_drawdown_pct"].mean()
                    ),
                    "total_return_effect_pct_points": float(
                        enabled["total_return_pct"].mean()
                        - disabled["total_return_pct"].mean()
                    ),
                    "sharpe_effect": float(enabled["sharpe"].mean() - disabled["sharpe"].mean()),
                    "exposure_effect_pct_points": float(
                        enabled["exposure_pct"].mean() - disabled["exposure_pct"].mean()
                    ),
                    "order_effect": float(enabled["order_count"].mean() - disabled["order_count"].mean()),
                }
            )
    return rows


def performance_figure(
    daily: pd.DataFrame, benchmark: pd.DataFrame, window_id: str
) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=(f"{WINDOW_LABELS[window_id]} 账户净值", "从各自历史峰值回撤"),
    )
    for case_id in CASE_FLAGS:
        frame = daily[
            daily["window_id"].eq(window_id) & daily["case_id"].eq(case_id)
        ].sort_values("date")
        label = short_label(case_id)
        color = PALETTE[mask_number(case_id)]
        width = 2.8 if mask_number(case_id) in (0, 15) else 1.25
        for row, values, panel, legend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=legend, line={"color": color, "width": width},
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                        if row == 1 else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                    ),
                ), row=row, col=1,
            )
    hold = benchmark[benchmark["window_id"].eq(window_id)].sort_values("date")
    for row, values, panel, legend in (
        (1, hold["equity"], "equity", True),
        (2, drawdown(hold["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=hold["date"], y=values, mode="lines", name="QQQ Buy & Hold",
                showlegend=legend, line={"color": "#000000", "dash": "dash", "width": 2},
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
        height=800, margin={"l": 65, "r": 25, "t": 60, "b": 55},
        hovermode="x unified", showlegend=False, uirevision=f"four-exit-{window_id}",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def ablation_matrix(results: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=2,
        horizontal_spacing=0.12, vertical_spacing=0.18,
        subplot_titles=(
            "2010–2015 最大回撤", "2010–2015 总收益",
            "2020–2026 最大回撤", "2020–2026 总收益",
        ),
    )
    row_labels = ["R1关 / R2关", "R1开 / R2关", "R1关 / R2开", "R1开 / R2开"]
    col_labels = ["R3关 / R4关", "R3开 / R4关", "R3关 / R4开", "R3开 / R4开"]
    for row_number, window_id in enumerate(WINDOW_LABELS, start=1):
        frame = results[results["window_id"].eq(window_id)].set_index("case_id")
        matrices: dict[str, list[list[float]]] = {"max_drawdown_pct": [], "total_return_pct": []}
        texts: dict[str, list[list[str]]] = {"max_drawdown_pct": [], "total_return_pct": []}
        for r1, r2 in ((False, False), (True, False), (False, True), (True, True)):
            metric_rows = {key: [] for key in matrices}
            text_rows = {key: [] for key in texts}
            for r3, r4 in ((False, False), (True, False), (False, True), (True, True)):
                case_id = next(
                    case for case, flags in CASE_FLAGS.items() if flags == (r1, r2, r3, r4)
                )
                record = frame.loc[case_id]
                for metric in matrices:
                    value = float(record[metric])
                    metric_rows[metric].append(value)
                    text_rows[metric].append(f"{short_label(case_id)}<br>{value:.2f}%")
            for metric in matrices:
                matrices[metric].append(metric_rows[metric])
                texts[metric].append(text_rows[metric])
        for col_number, metric in enumerate(("max_drawdown_pct", "total_return_pct"), start=1):
            figure.add_trace(
                go.Heatmap(
                    z=matrices[metric], x=col_labels, y=row_labels,
                    text=texts[metric], texttemplate="%{text}",
                    colorscale="RdYlGn", showscale=False,
                    hovertemplate="%{y}<br>%{x}<br>%{text}<extra></extra>",
                ), row=row_number, col=col_number,
            )
    figure.update_layout(
        height=760, margin={"l": 95, "r": 25, "t": 65, "b": 75},
        uirevision="four-exit-matrix",
    )
    return figure


def market_figure(indicators: pd.DataFrame, orders: pd.DataFrame, case_id: str) -> go.Figure:
    market = indicators[indicators["date"].ge(pd.Timestamp("2020-01-01"))].copy()
    selected = orders[
        orders["window_id"].eq("2020_2026") & orders["case_id"].eq(case_id)
    ].copy()
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.73, 0.27],
        subplot_titles=(
            f"2020–2026 QQQ、均线与跨窗口回撤排名代表 {short_label(case_id)} 成交",
            "短均线平均日变化率",
        ),
    )
    figure.add_trace(
        go.Candlestick(
            x=market["date"], open=market["open"], high=market["high"],
            low=market["low"], close=market["close"], name="QQQ复权OHLC",
            increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
        ), row=1, col=1,
    )
    colors = {25: "#e11d48", 30: "#d97706", 35: "#7c3aed", 200: "#2563eb"}
    for window in (25, 30, 35, 200):
        figure.add_trace(
            go.Scatter(
                x=market["date"], y=market[f"sma{window}"], mode="lines",
                name=f"SMA{window}", line={"color": colors[window], "width": 1.3},
                meta={"series_key": f"sma{window}", "panel": "market", "label": f"SMA{window}"},
            ), row=1, col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=market["date"], y=market["sma_short_avg"], mode="lines",
            name="SMA25/30/35平均", line={"color": "#64748b", "dash": "dot", "width": 1.3},
            meta={"series_key": "short_avg", "panel": "market", "label": "短均线平均"},
        ), row=1, col=1,
    )
    for side, label, color, symbol in (
        ("buy", "买入", "#087f5b", "triangle-up"),
        ("sell", "卖出", "#c92a2a", "triangle-down"),
    ):
        subset = selected[selected["type"].eq(side)]
        figure.add_trace(
            go.Scatter(
                x=subset["date"], y=subset["raw_price"], mode="markers", name=label,
                customdata=subset[["reason", "matched_signals"]].astype(str).to_numpy(),
                marker={"color": color, "symbol": symbol, "size": 8},
                meta={"series_key": f"order_{side}", "panel": "market", "label": label},
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.4f}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
            ), row=1, col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=market["date"], y=market["sma_short_avg_change_pct"], mode="lines",
            name="短均线平均日变化", line={"color": "#0f766e", "width": 1.2},
            meta={"series_key": "short_avg_change", "panel": "market", "label": "短均线平均日变化率"},
        ), row=2, col=1,
    )
    figure.add_hline(y=-0.15, line_dash="dash", line_color="#c92a2a", row=2, col=1)
    figure.update_layout(
        height=820, margin={"l": 65, "r": 25, "t": 60, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="four-exit-market",
    )
    figure.update_yaxes(title_text="复权价格", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def result_table(frame: pd.DataFrame, *, html_mode: bool) -> str | list[str]:
    frame = frame.sort_values("case_id", key=lambda values: values.map(mask_number))
    if html_mode:
        rows = []
        for record in frame.to_dict("records"):
            rows.append(
                "<tr>"
                f"<td>{mask_number(record['case_id']):02d}</td>"
                f"<td>{html.escape(short_label(record['case_id']))}</td>"
                f"<td>{record['total_return_pct']:.2f}%</td><td>{record['cagr_pct']:.2f}%</td>"
                f"<td>{record['sharpe']:.3f}</td><td>{record['max_drawdown_pct']:.2f}%</td>"
                f"<td>{record['exposure_pct']:.1f}%</td><td>{int(record['order_count'])}</td>"
                f"<td>{record['drawdown_rank']:.0f}</td></tr>"
            )
        return (
            "<table><thead><tr><th>Mask</th><th>启用卖出规则</th><th>总收益</th><th>CAGR</th>"
            "<th>Sharpe</th><th>最大回撤</th><th>持仓率</th><th>订单</th><th>回撤排名</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    lines = [
        "| Mask | 启用卖出规则 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 订单 | 回撤排名 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in frame.to_dict("records"):
        lines.append(
            f"| {mask_number(record['case_id']):02d} | {short_label(record['case_id'])} | "
            f"{record['total_return_pct']:.2f}% | {record['cagr_pct']:.2f}% | "
            f"{record['sharpe']:.3f} | {record['max_drawdown_pct']:.2f}% | "
            f"{record['exposure_pct']:.1f}% | {int(record['order_count'])} | "
            f"{record['drawdown_rank']:.0f} |"
        )
    return lines


def effect_table(effects: pd.DataFrame, *, html_mode: bool) -> str | list[str]:
    if html_mode:
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(WINDOW_LABELS[row.window_id])}</td>"
            f"<td>{html.escape(RULE_LABELS[row.rule])}</td>"
            f"<td>{row.max_drawdown_effect_pct_points:+.2f}pp</td>"
            f"<td>{row.total_return_effect_pct_points:+.2f}pp</td>"
            f"<td>{row.sharpe_effect:+.3f}</td><td>{row.order_effect:+.1f}</td></tr>"
            for row in effects.itertuples(index=False)
        )
        return (
            "<table><thead><tr><th>窗口</th><th>规则</th><th>平均回撤影响</th>"
            "<th>平均收益影响</th><th>平均Sharpe影响</th><th>平均订单影响</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    lines = [
        "| 窗口 | 规则 | 平均回撤影响 | 平均收益影响 | 平均Sharpe影响 | 平均订单影响 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in effects.itertuples(index=False):
        lines.append(
            f"| {WINDOW_LABELS[row.window_id]} | {RULE_LABELS[row.rule]} | "
            f"{row.max_drawdown_effect_pct_points:+.2f}pp | "
            f"{row.total_return_effect_pct_points:+.2f}pp | {row.sharpe_effect:+.3f} | "
            f"{row.order_effect:+.1f} |"
        )
    return lines


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
    block = block_root(context, args.run_id, "QQQ", 0)
    raw_results = pd.read_csv(block / "parameter_results.csv")
    results, representative = rank_results(raw_results)
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    benchmarks = pd.read_csv(block / "benchmark_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["signal_date", "date"])
    indicators = pd.read_csv(block / "indicators.csv", parse_dates=["date"])
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    effects = pd.DataFrame(factorial_effects(results))
    best_by_window = {
        window_id: results[results["window_id"].eq(window_id)].sort_values(
            ["max_drawdown_pct", "total_return_pct"], ascending=[False, False]
        ).iloc[0].to_dict()
        for window_id in WINDOW_LABELS
    }
    baseline = {
        window_id: results[
            results["window_id"].eq(window_id) & results["case_id"].str.startswith("mask_00")
        ].iloc[0].to_dict()
        for window_id in WINDOW_LABELS
    }
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "tested_rules": context.config["strategy"],
        "actual_windows": metrics["actual_windows"],
        "representative_by_cross_window_drawdown_rank": representative,
        "best_by_window": best_by_window,
        "no_exit_rule_baseline": baseline,
        "factorial_main_effects": effects.to_dict("records"),
        "results": results.to_dict("records"),
        "max_cross_check_differences": metrics["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    rules_html = """
<h2>我们测的是什么</h2>
<p>每次卖出后重新累计买入资格。路径 A：Close 同日上穿 SMA25、SMA30、SMA35 和三者平均线，随后在卖出信号出现前 Close&gt;SMA200。路径 B：Close 上穿 SMA200，随后在卖出信号出现前 Close 同时高于四条短线。两路径 OR。</p>
<p>资格形成后，从下一交易日起，price 同时高于动态 0.98×SMA30 和动态 0.98×SMA200 时买入。四个卖出开关执行完整 2⁴=16 组合：</p>
<ol><li>R1：SMA200 连续三个交易日严格下降；</li><li>R2：SMA25/30/35 平均单日严格下跌超过 0.15%；</li><li>R3：Close&lt;SMA200；</li><li>R4：Close&lt;SMA30。</li></ol>
"""
    summary_html = rules_html
    for window_id in WINDOW_LABELS:
        subset = results[results["window_id"].eq(window_id)]
        summary_html += f"<h2>{WINDOW_LABELS[window_id]}</h2>{result_table(subset, html_mode=True)}"
    summary_html += "<h2>平衡全因子平均主效应</h2><p>回撤影响为正表示最大回撤平均变浅；这是历史组合均值差，不是未来因果保证。</p>"
    summary_html += str(effect_table(effects, html_mode=True))
    report = render_interactive_report(
        title="QQQ 新买入状态机与四卖出规则：双时期2⁴消融",
        heading="QQQ 新买入状态机与四卖出规则：双时期2⁴消融",
        subtitle="16个卖出组合 × 2个独立窗口；主指标为最大回撤。",
        summary_html=summary_html,
        notes=[
            "同日四条短线共同上穿是事件，不是只要价格位于其上方；另一组条件使用完成Close的水平状态。",
            "买入动态阈值只用此前完成Close预先求解；卖出由完成Close确认、下一交易日Open成交。",
            "零成本会低估高换手组合的摩擦；两个历史窗口只用于探索，不选出可直接交易的最终规则。",
        ],
        figures=[
            ReportFigure("market-qqq", "市场、指标与代表组合成交", market_figure(indicators, orders, representative), "market"),
            ReportFigure("ablation-matrix", "四规则完整消融矩阵", ablation_matrix(results), "other"),
            ReportFigure("performance-qqq", "2010–2015净值与回撤", performance_figure(daily, benchmark, "2010_2015"), "performance"),
            ReportFigure("performance-qqq-2020", "2020–2026净值与回撤", performance_figure(daily, benchmark, "2020_2026"), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")

    markdown = [
        "# QQQ 新买入状态机与四卖出规则：双时期2⁴消融", "",
        "## 我们测的是什么", "",
        "路径A：Close同日上穿SMA25、SMA30、SMA35及三者平均，随后且在卖出信号前Close>SMA200。", "",
        "路径B：Close上穿SMA200，随后且在卖出信号前Close同时高于四条短线。两路径OR；资格形成后从下一交易日起，price同时高于动态0.98×SMA30和0.98×SMA200时买入。", "",
        "卖出开关：R1 SMA200连续3日下降；R2短均线平均单日跌幅>0.15%；R3 Close<SMA200；R4 Close<SMA30。", "",
    ]
    for window_id in WINDOW_LABELS:
        markdown.extend([
            f"## {WINDOW_LABELS[window_id]}", "",
            *result_table(results[results["window_id"].eq(window_id)], html_mode=False), "",
        ])
    markdown.extend([
        "## 平衡全因子平均主效应", "",
        "回撤影响为正表示最大回撤平均变浅。", "",
        *effect_table(effects, html_mode=False), "",
        "## 边界", "",
        "- 两个历史窗口均为回溯探索，不据此推广未来最优组合。",
        "- 使用拆股和股息调整日线；动态日内阈值由OHLC判断触及，不含更细日内路径。",
        "- 本轮为零成本，换手较高版本的真实结果会更差。", "",
    ])
    (run_root / "report.md").write_text("\n".join(markdown), encoding="utf-8")

    def print_table(window_id: str) -> str:
        return str(result_table(results[results["window_id"].eq(window_id)], html_mode=True))

    effects_html = str(effect_table(effects, html_mode=True))
    print_html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>QQQ 2^4消融</title>
<style>@page{{size:A4 landscape;margin:9mm}}*{{box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#172033;margin:0}}.page{{page-break-after:always;min-height:185mm;padding:3mm}}.page:last-child{{page-break-after:auto}}h1{{font-size:25px;margin:0 0 10px}}h2{{font-size:17px;margin:10px 0 5px}}p,li{{font-size:12px;line-height:1.5}}.callout{{background:#edf7f4;border-left:5px solid #0f766e;padding:8px 12px}}table{{border-collapse:collapse;width:100%;font-size:7.5px;margin-top:7px}}th,td{{border:1px solid #cbd5e1;padding:3px;text-align:right}}th:nth-child(2),td:nth-child(2){{text-align:left}}th{{background:#e9eef5}}.small{{font-size:9px;color:#526071}}</style></head><body>
<section class="page"><h1>QQQ 新买入状态机与四卖出规则：双时期2⁴消融</h1><div class="callout"><strong>我们测的是什么</strong><br>16个卖出组合×两个独立窗口；每个case从100,000美元现金和空仓开始。</div>
<h2>买入路径A</h2><p>完成Close在同一日上穿SMA25、SMA30、SMA35及三者平均线；随后且在任一启用卖出信号出现前，完成Close高于SMA200。</p>
<h2>买入路径B</h2><p>完成Close上穿SMA200；随后且在任一启用卖出信号出现前，完成Close同时高于SMA25、SMA30、SMA35及三者平均线。A/B为OR；卖出信号清空资格。</p>
<h2>资格后的价格门槛</h2><p>从资格形成后的下一交易日起，price同时高于动态SMA30×0.98和动态SMA200×0.98时买入。Open已在两线之上按Open，否则High触及较高动态边界时按边界买。</p>
<h2>四个卖出开关</h2><ol><li>R1：SMA200连续三个交易日严格下降；</li><li>R2：SMA25/30/35平均单日严格下跌超过0.15%；</li><li>R3：Close&lt;SMA200；</li><li>R4：Close&lt;SMA30。</li></ol><p>任一启用规则成立，下一交易日Open全卖；完整测试2⁴=16种启用组合。主指标为最大回撤。</p></section>
<section class="page"><h1>结果：2010–2015</h1>{print_table('2010_2015')}<p class="small">回撤排名1代表该窗口16个策略中最大回撤最浅；Buy & Hold另作基准，不参与排名。</p></section>
<section class="page"><h1>结果：2020–2026</h1>{print_table('2020_2026')}<p class="small">所有窗口独立初始化，不继承仓位、现金或买入资格。</p></section>
<section class="page"><h1>四条规则的平衡全因子平均主效应</h1><p>每条规则“开启的8个组合平均值−关闭的8个组合平均值”。回撤影响为正表示最大回撤变浅；收益影响为正表示收益增加。</p>{effects_html}<h2>研究边界</h2><p>这些是历史组合均值差，不是未来因果保证。日内买入只由日线OHLC推断阈值触及；零成本低估高换手摩擦。两个回溯窗口不能直接产生实盘候选。</p></section></body></html>"""
    (run_root / "report_print.html").write_text(print_html, encoding="utf-8")

    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/execution.py", "backtest/quantkit/metrics.py",
        "backtest/quantkit/reference.py", "backtest/quantkit/reporting.py",
        "backtest/quantkit/sma_entry_exit_ablation.py",
        "backtest/scripts/run_sma_entry_four_exit_ablation.py",
        "backtest/scripts/analyze_sma_entry_four_exit_ablation.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/rot/test_sma_entry_exit_ablation.py",
        "backtest/report_templates/interactive_research_v3/page.html",
        "backtest/report_templates/interactive_research_v3/styles.css",
        "backtest/report_templates/interactive_research_v3/interactions.js",
        "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(), "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size, "sha256": sha256(path),
        }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run evaluates all 16 exit-rule masks in two independent QQQ windows.

- `report.pdf`: combined rules-first PDF.
- `report.html` / `report.md`: interactive and concise reports.
- `analysis/summary.json`: rankings and balanced factorial main effects.
- `QQQ/cost_0bps/`: all 32 case-window ledgers and metrics.
- `validation.json`: mandatory gates.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report_print.html'}")


if __name__ == "__main__":
    main()
