#!/usr/bin/env python3
"""Build the interactive report and print-first PDF source for the ablation."""

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


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods"
CASE_LABELS = {
    "base": "基础：条件 1+3",
    "measure_1": "措施 1：三条短 SMA 均上涨",
    "measure_2": "措施 2：3% 止损与锁定回买",
    "measures_1_2": "措施 1+2",
}
WINDOW_LABELS = {"2010_2015": "2010–2015", "2020_2026": "2020–2026"}
COLORS = {
    "base": "#2563eb", "measure_1": "#d97706", "measure_2": "#0f766e",
    "measures_1_2": "#7c3aed", "buy_hold": "#111827",
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
    equity = equity.astype(float)
    return (equity / equity.cummax() - 1.0) * 100.0


def build_performance_figure(
    daily: pd.DataFrame, benchmark: pd.DataFrame, window_id: str
) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=(f"{WINDOW_LABELS[window_id]} 账户净值", "从各自历史峰值回撤"),
    )
    for case_id, label in CASE_LABELS.items():
        frame = daily[(daily["window_id"] == window_id) & (daily["case_id"] == case_id)].sort_values("date")
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label,
                    showlegend=showlegend, line={"color": COLORS[case_id], "width": 2.2},
                    meta={"series_key": case_id, "panel": panel, "label": label},
                    hovertemplate=("%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>" if row == 1
                                   else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"),
                ), row=row, col=1,
            )
    hold = benchmark[benchmark["window_id"] == window_id].sort_values("date")
    for row, values, panel, showlegend in (
        (1, hold["equity"], "equity", True),
        (2, drawdown(hold["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=hold["date"], y=values, mode="lines", name="QQQ Buy & Hold",
                showlegend=showlegend, line={"color": COLORS["buy_hold"], "dash": "dash", "width": 2},
                meta={"series_key": "buy_hold", "panel": panel, "label": "QQQ Buy & Hold",
                      "is_benchmark": panel == "equity", "cost_bps": 0},
            ), row=row, col=1,
        )
    figure.update_layout(
        height=760, margin={"l": 65, "r": 25, "t": 60, "b": 55},
        hovermode="x unified", showlegend=False, uirevision=f"two-measure-{window_id}",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def build_market_figure(indicators: pd.DataFrame, orders: pd.DataFrame) -> go.Figure:
    market = indicators[indicators["date"] >= pd.Timestamp("2020-01-01")].copy()
    selected = orders[
        (orders["window_id"] == "2020_2026") & (orders["case_id"] == "measures_1_2")
    ].copy()
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.73, 0.27],
        subplot_titles=("2020–2026 QQQ、SMA25/30/35/200 与措施 1+2 成交", "SMA25/30/35 日变化率"),
    )
    figure.add_trace(go.Candlestick(
        x=market["date"], open=market["open"], high=market["high"], low=market["low"],
        close=market["close"], name="QQQ 复权 OHLC",
        increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
    ), row=1, col=1)
    sma_colors = {25: "#e11d48", 30: "#d97706", 35: "#7c3aed", 200: "#2563eb"}
    for window in (25, 30, 35, 200):
        figure.add_trace(go.Scatter(
            x=market["date"], y=market[f"sma{window}"], mode="lines", name=f"SMA{window}",
            line={"color": sma_colors[window], "width": 1.4},
            meta={"series_key": f"sma{window}", "panel": "market", "label": f"SMA{window}"},
        ), row=1, col=1)
    for side, label, color, symbol in (
        ("buy", "措施 1+2 买入", "#087f5b", "triangle-up"),
        ("sell", "措施 1+2 卖出", "#c92a2a", "triangle-down"),
    ):
        subset = selected[selected["type"] == side]
        figure.add_trace(go.Scatter(
            x=subset["date"], y=subset["raw_price"], mode="markers", name=label,
            customdata=subset[["reason", "fill_source"]].astype(str).to_numpy(),
            marker={"color": color, "symbol": symbol, "size": 8},
            meta={"series_key": f"order_{side}", "panel": "market", "label": label},
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:.4f}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
        ), row=1, col=1)
    for window in (25, 30, 35):
        figure.add_trace(go.Scatter(
            x=market["date"], y=market[f"sma{window}"].pct_change(fill_method=None) * 100,
            mode="lines", name=f"SMA{window} 日变化", line={"color": sma_colors[window], "width": 1.2},
            meta={"series_key": f"sma{window}_derivative", "panel": "market", "label": f"SMA{window} 日变化率"},
        ), row=2, col=1)
    figure.update_layout(
        height=820, margin={"l": 65, "r": 25, "t": 60, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="two-measure-market",
    )
    figure.update_yaxes(title_text="复权价格", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def comparison_rows(results: pd.DataFrame, benchmarks: pd.DataFrame, window_id: str) -> list[dict[str, Any]]:
    rows = []
    for case_id, label in CASE_LABELS.items():
        row = results[(results["window_id"] == window_id) & (results["case_id"] == case_id)].iloc[0].to_dict()
        rows.append({"name": label, **row})
    benchmark = benchmarks[benchmarks["window_id"] == window_id].iloc[0].to_dict()
    rows.append({"name": "QQQ Buy & Hold", "case_id": "buy_hold", **benchmark})
    return rows


def table_body(rows: list[dict[str, Any]], *, mechanics: bool = False) -> str:
    rendered = []
    for row in rows:
        extra = ""
        if mechanics:
            extra = (
                f"<td>{int(row.get('intraday_stop_count', 0))}</td>"
                f"<td>{int(row.get('sell_price_reentry_count', 0))}</td>"
                f"<td>{int(row.get('sma200_reset_reentry_count', 0))}</td>"
            )
        rendered.append(
            "<tr>" f"<td>{html.escape(str(row['name']))}</td>"
            f"<td>{float(row['total_return_pct']):.2f}%</td>"
            f"<td>{float(row['cagr_pct']):.3f}%</td>"
            f"<td>{float(row['sharpe']):.3f}</td>"
            f"<td>{float(row['max_drawdown_pct']):.2f}%</td>"
            f"<td>{float(row['exposure_pct']):.2f}%</td>"
            f"<td>{int(row['order_count'])}</td>{extra}</tr>"
        )
    return "".join(rendered)


def html_table(rows: list[dict[str, Any]], *, mechanics: bool = False) -> str:
    extra = "<th>3%止损</th><th>卖价回买</th><th>SMA重置回买</th>" if mechanics else ""
    return (
        "<table><thead><tr><th>版本</th><th>总收益</th><th>CAGR</th><th>Sharpe</th>"
        f"<th>最大回撤</th><th>持仓率</th><th>成交</th>{extra}</tr></thead>"
        f"<tbody>{table_body(rows, mechanics=mechanics)}</tbody></table>"
    )


def markdown_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 版本 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 成交 | 3%止损 | 卖价回买 | SMA重置回买 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {float(row['total_return_pct']):.2f}% | {float(row['cagr_pct']):.3f}% | "
            f"{float(row['sharpe']):.3f} | {float(row['max_drawdown_pct']):.2f}% | "
            f"{float(row['exposure_pct']):.2f}% | {int(row['order_count'])} | "
            f"{int(row.get('intraday_stop_count', 0))} | {int(row.get('sell_price_reentry_count', 0))} | "
            f"{int(row.get('sma200_reset_reentry_count', 0))} |"
        )
    return lines


def best_drawdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max((row for row in rows if row["case_id"] != "buy_hold"), key=lambda row: float(row["max_drawdown_pct"]))


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
    results = pd.read_csv(block / "parameter_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    benchmarks = pd.read_csv(block / "benchmark_results.csv")
    orders = pd.read_csv(block / "orders.csv", parse_dates=["signal_date", "date"])
    indicators = pd.read_csv(block / "indicators.csv", parse_dates=["date"])
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    rows_by_window = {
        window_id: comparison_rows(results, benchmarks, window_id)
        for window_id in WINDOW_LABELS
    }
    winners = {window: best_drawdown(rows) for window, rows in rows_by_window.items()}
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id, "created_at_utc": created_at,
        "tested_rules": context.config["strategy"],
        "actual_windows": metrics["actual_windows"],
        "comparison_rows": rows_by_window, "drawdown_winners": winners,
        "same_drawdown_winner_both_windows": winners["2010_2015"]["case_id"] == winners["2020_2026"]["case_id"],
        "max_cross_check_differences": metrics["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    summary_html = """
<h2>我们测的是什么</h2>
<p><strong>旧 8% 止损已完全删除。</strong>基础策略只有条件 1（Close&gt;SMA200）和条件 3（SMA200&gt;SMA250&gt;SMA300）。本轮对两个新措施做完整 2×2 消融：基础、只加措施 1、只加措施 2、两者都加，并在两个独立窗口分别从 100,000 美元现金开始。</p>
<ol>
<li><strong>措施 1：</strong>SMA25、SMA30、SMA35 必须各自严格高于前一交易日；任一持平或下跌，主条件不满足。</li>
<li><strong>措施 2：</strong>每个交易日前，以实际入场价和此前完成 Close 的最高值作为已知峰值；当日回撤达到 3% 时，跳空按 Open，否则 Low 触及时按精确阈值立即卖出。止损后只有两条回买路：Close 回到实际卖价且主条件全部满足；或依次完成 Close 下穿 SMA200、Close≤SMA200×95%、再上穿 SMA200，此后主条件全部满足。两类回买均在下一交易日 Open 执行。</li>
</ol>
"""
    for window_id, rows in rows_by_window.items():
        summary_html += f"<h2>{WINDOW_LABELS[window_id]}</h2>{html_table(rows, mechanics=True)}"
    report = render_interactive_report(
        title="QQQ 两项回撤措施：双时期 2×2 消融",
        heading="QQQ 两项回撤措施：双时期 2×2 消融",
        subtitle="旧 8% 止损已删除；2010–2015 与 2020–2026 各自独立测试四种版本。",
        summary_html=summary_html,
        notes=[
            "两个窗口均初始空仓、独立现金和独立状态，不继承前一窗口仓位或止损锁。",
            "措施 1 的三条短 SMA 是分别严格上涨，不使用平均值；持平也算不满足。",
            "3% 止损阈值不使用当日 High 更新，避免同日未来信息；次日 Open 买回后可以同日盘中再次止损。",
            "零成本结果会低估措施 1 和措施 2 增加成交后的摩擦。",
        ],
        figures=[
            ReportFigure("market-qqq", "2020–2026 市场、短均线与措施 1+2 成交", build_market_figure(indicators, orders), "market"),
            ReportFigure("performance-qqq", "2010–2015 净值与回撤", build_performance_figure(daily, benchmark, "2010_2015"), "performance"),
            ReportFigure("performance-qqq-2020", "2020–2026 净值与回撤", build_performance_figure(daily, benchmark, "2020_2026"), "other"),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")

    markdown = [
        "# QQQ 两项回撤措施：双时期 2×2 消融", "", "## 我们测的是什么", "",
        "旧 8% 止损已完全删除。基础策略只保留 Close>SMA200 与 SMA200>SMA250>SMA300。", "",
        "- 措施 1：SMA25、SMA30、SMA35 各自严格高于前一交易日；任一持平或下跌即不满足主条件。",
        "- 措施 2：按盘前已知持仓峰值设置 3% 当日止损；止损后只有回到实际卖价且主条件满足，或依次完成下穿 SMA200、到达 SMA200−5%、再上穿 SMA200且主条件满足，才能次日 Open 买回。",
        "- 四个版本：基础、措施 1、措施 2、措施 1+2。两个时期均独立从现金开始。", "",
    ]
    for window_id, rows in rows_by_window.items():
        markdown.extend([f"## {WINDOW_LABELS[window_id]}", "", *markdown_table(rows), ""])
    markdown.extend([
        "## 边界", "", "- 最大回撤是主指标，不从两个历史窗口中选择或推广参数。",
        "- 盘中止损用日线 OHLC 推断阈值触及，无法知道日内更细的真实路径；同日 Open 买回再止损按 Open 先于 Low 的可知顺序。",
        "- 零成本会低估高换手策略的摩擦；本轮仍是回溯性探索。", "",
    ])
    (run_root / "report.md").write_text("\n".join(markdown), encoding="utf-8")

    # Print-first source: page 1 is deliberately rules-only, followed by one
    # result page per window. Chrome converts this single HTML into one PDF.
    def print_table(rows: list[dict[str, Any]]) -> str:
        return html_table(rows, mechanics=True)
    print_html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>QQQ 两项回撤措施消融</title>
<style>@page{{size:A4 landscape;margin:12mm}}*{{box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;color:#172033;margin:0}}.page{{page-break-after:always;min-height:180mm;padding:5mm}}.page:last-child{{page-break-after:auto}}h1{{font-size:28px;margin:0 0 14px}}h2{{font-size:20px;margin:16px 0 8px}}p,li{{font-size:14px;line-height:1.65}}.callout{{background:#edf7f4;border-left:5px solid #0f766e;padding:10px 14px}}table{{border-collapse:collapse;width:100%;font-size:11px;margin-top:12px}}th,td{{border:1px solid #cbd5e1;padding:6px;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#e9eef5}}.small{{font-size:11px;color:#526071}}.winner{{font-weight:700;color:#0f766e}}</style></head><body>
<section class="page"><h1>QQQ 两项回撤措施：双时期 2×2 消融</h1><div class="callout"><strong>我们测的是什么</strong><br>旧 8% 止损已完全删除。基础策略只保留：Close&gt;SMA200，且 SMA200&gt;SMA250&gt;SMA300。</div>
<h2>措施 1：三条短 SMA 分别上涨</h2><p>SMA25(t)&gt;SMA25(t−1)、SMA30(t)&gt;SMA30(t−1)、SMA35(t)&gt;SMA35(t−1) 必须同时成立。任何一条持平或下跌，主条件都不满足。</p>
<h2>措施 2：3% 盘中止损与回买锁</h2><p>每个交易日前，用实际入场价和此前完成 Close 的最高值维护已知峰值。Open 已低于峰值×97% 时按 Open 卖；否则 Low 触及峰值×97% 时按阈值立即卖。止损后只有两种回买：</p><ol><li>完成 Close 回到或高于实际止损卖价，同时主条件全部满足；</li><li>价格未回到卖价时，止损后依次完成 Close 下穿 SMA200、Close≤SMA200×95%、随后从下方向上穿 SMA200，之后主条件全部满足。</li></ol><p>回买均在下一交易日 Open；Open 买回后，当日仍可能再次触发 3% 止损。</p>
<h2>四个版本与两个时期</h2><p>基础、只加措施 1、只加措施 2、措施 1+2；分别测试 2010-01-04～2015-12-31 与 2020-01-02～2026-08-04。每个 case-window 独立从 100,000 美元现金开始，零成本、允许小数股。</p><p class="small">主指标：最大回撤。收益、Sharpe、持仓率、成交和回买次数用于说明代价；不选参、不晋级。</p></section>
<section class="page"><h1>结果：2010–2015</h1>{print_table(rows_by_window['2010_2015'])}<p class="winner">策略内最大回撤最小：{html.escape(winners['2010_2015']['name'])}，{float(winners['2010_2015']['max_drawdown_pct']):.2f}%</p><p class="small">3%止损、卖价回买、SMA重置回买只对启用措施 2 的版本有意义。Buy & Hold 不参与措施优劣判断。</p></section>
<section class="page"><h1>结果：2020–2026</h1>{print_table(rows_by_window['2020_2026'])}<p class="winner">策略内最大回撤最小：{html.escape(winners['2020_2026']['name'])}，{float(winners['2020_2026']['max_drawdown_pct']):.2f}%</p><h2>研究边界</h2><p>这是两个回溯窗口，不是实时样本外。盘中止损由日线 OHLC 推断触及，无法重建更细的日内路径；零成本尤其会低估高换手版本的摩擦。只有在两个窗口都稳定改善回撤且后续加入成本后仍成立，才值得继续测试。</p></section></body></html>"""
    (run_root / "report_print.html").write_text(print_html, encoding="utf-8")

    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py", "backtest/quantkit/metrics.py",
        "backtest/quantkit/reference.py", "backtest/quantkit/reporting.py",
        "backtest/quantkit/sma_regime_reentry_ablation.py",
        "backtest/scripts/run_sma_regime_two_measure_ablation.py",
        "backtest/scripts/analyze_sma_regime_two_measure_ablation.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/rot/test_sma_regime_reentry_ablation.py",
        "backtest/report_templates/interactive_research_v3/page.html",
        "backtest/report_templates/interactive_research_v3/styles.css",
        "backtest/report_templates/interactive_research_v3/interactions.js",
        "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id, "created_at_utc": created_at,
        "software": {"python": platform.python_version(), "lib_pybroker": "1.2.12", "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run compares two new drawdown-control measures across two independently initialized QQQ windows.

- `report.pdf`: requested combined PDF; page one defines the tested rules.
- `report_print.html`: print source for the PDF.
- `report.html` / `report.md`: interactive and concise human reports.
- `analysis/summary.json`: machine-readable two-window comparison.
- `QQQ/cost_0bps/`: eight reference/PyBroker paths, benchmarks, indicators and hashes.
- `validation.json`: mandatory gates and PDF verification.
""", encoding="utf-8"
    )
    # The PDF is printed after this script. Artifact hashing and lifecycle
    # completion therefore happen in finalize_sma_regime_two_measure_ablation.
    print(f"Wrote {run_root / 'report_print.html'}")


if __name__ == "__main__":
    main()
