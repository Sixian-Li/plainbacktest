#!/usr/bin/env python3
"""Build the formal report for the QQQ three-condition SMA regime ablation."""

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
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history"
CASE_LABELS = {
    "all_conditions": "完整策略：条件 1+2+3",
    "without_condition_1": "去掉条件 1：不要求 Close>SMA200",
    "without_condition_2": "去掉条件 2：不要求四条 SMA 连升 3 日",
    "without_condition_3": "去掉条件 3：不要求 SMA200>SMA250>SMA300",
}
COLORS = {
    "all_conditions": "#0f766e",
    "without_condition_1": "#d97706",
    "without_condition_2": "#7c3aed",
    "without_condition_3": "#2563eb",
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


def build_market_figure(indicators: pd.DataFrame, orders: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.73, 0.27],
        subplot_titles=(
            "QQQ 复权 OHLC、SMA30/200/250/300 与完整策略成交",
            "四条 SMA 相对前一交易日变化（%）",
        ),
    )
    figure.add_trace(
        go.Candlestick(
            x=indicators["date"],
            open=indicators["open"],
            high=indicators["high"],
            low=indicators["low"],
            close=indicators["close"],
            name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        ),
        row=1,
        col=1,
    )
    sma_colors = {30: "#e11d48", 200: "#2563eb", 250: "#7c3aed", 300: "#d97706"}
    for window in (30, 200, 250, 300):
        figure.add_trace(
            go.Scatter(
                x=indicators["date"],
                y=indicators[f"sma{window}"],
                mode="lines",
                name=f"SMA{window}",
                line={"color": sma_colors[window], "width": 1.5},
                meta={
                    "series_key": f"sma{window}",
                    "panel": "market",
                    "label": f"SMA{window}",
                },
            ),
            row=1,
            col=1,
        )
    full_orders = orders[orders["case_id"] == "all_conditions"].copy()
    for side, label, color, symbol in (
        ("buy", "完整策略买入", "#087f5b", "triangle-up"),
        ("sell", "完整策略卖出", "#c92a2a", "triangle-down"),
    ):
        subset = full_orders[full_orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=subset["date"],
                y=subset["raw_price"],
                customdata=subset[["signal_date", "reason"]].astype(str).to_numpy(),
                mode="markers",
                name=label,
                marker={"color": color, "symbol": symbol, "size": 9},
                meta={
                    "series_key": f"full_{side}",
                    "panel": "market",
                    "label": label,
                },
                hovertemplate=(
                    f"{label}<br>成交 %{{x|%Y-%m-%d}} Open $%{{y:.4f}}"
                    "<br>信号 %{customdata[0]} Close<br>%{customdata[1]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    for window in (30, 200, 250, 300):
        derivative = indicators[f"sma{window}"].pct_change(fill_method=None) * 100.0
        figure.add_trace(
            go.Scatter(
                x=indicators["date"],
                y=derivative,
                mode="lines",
                name=f"SMA{window} 日变化",
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
        height=830,
        margin={"l": 65, "r": 25, "t": 55, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-regime-ablation-v1",
    )
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def build_performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("账户净值（同一初始资金与观察区间）", "从各自历史峰值回撤"),
    )
    for case_id, label in CASE_LABELS.items():
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    line={"color": COLORS[case_id], "width": 2.2},
                    meta={
                        "series_key": case_id,
                        "panel": panel,
                        "label": label,
                    },
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                        if row == 1
                        else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                    ),
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
                name="QQQ Buy & Hold",
                showlegend=showlegend,
                line={"color": COLORS["buy_hold"], "width": 2.1, "dash": "dash"},
                meta={
                    "series_key": "buy_hold",
                    "panel": panel,
                    "label": "QQQ Buy & Hold",
                    "is_benchmark": panel == "equity",
                    "cost_bps": 0,
                },
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                    if row == 1
                    else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        height=820,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma-regime-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def ordered_rows(results: pd.DataFrame, benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    by_case = results.set_index("case_id")
    rows = [
        {
            "name": CASE_LABELS[case_id],
            "case_id": case_id,
            **by_case.loc[case_id].to_dict(),
        }
        for case_id in CASE_LABELS
    ]
    rows.append({"name": "QQQ Buy & Hold", "case_id": "buy_hold", **benchmark})
    return rows


def table_body(rows: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for item in rows:
        rendered.append(
            "<tr>"
            f"<td>{html.escape(str(item['name']))}</td>"
            f"<td>${float(item['final_equity']):,.2f}</td>"
            f"<td>{float(item['total_return_pct']):.2f}%</td>"
            f"<td>{float(item['cagr_pct']):.3f}%</td>"
            f"<td>{float(item['sharpe']):.3f}</td>"
            f"<td>{float(item['max_drawdown_pct']):.2f}%</td>"
            f"<td>{float(item['exposure_pct']):.2f}%</td>"
            f"<td>{int(item['order_count'])}</td>"
            "</tr>"
        )
    return "".join(rendered)


def summary_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    winner = summary["drawdown_winner"]
    full = next(item for item in rows if item["case_id"] == "all_conditions")
    return f"""
<h2>结论先看回撤</h2>
<p>按唯一目标“最大回撤越接近 0 越好”，四个策略中表现最好的是 <strong>{html.escape(CASE_LABELS[winner['case_id']])}</strong>，最大回撤 <strong>{winner['max_drawdown_pct']:.2f}%</strong>。完整三条件策略的最大回撤为 <strong>{float(full['max_drawdown_pct']):.2f}%</strong>，总收益 {float(full['total_return_pct']):.2f}%。</p>
<table><thead><tr><th>版本</th><th>期末净值</th><th>总收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>持仓率</th><th>成交</th></tr></thead><tbody>{table_body(rows)}</tbody></table>
<h2>严格口径</h2>
<p>条件 2 指 SMA30、SMA200、SMA250、SMA300 四条线都满足 SMA(t)&gt;SMA(t-1)&gt;SMA(t-2)&gt;SMA(t-3)。所有信号在完成 Close 后计算，下一交易日 Open 才执行；预热结束前保持现金。</p>
"""


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]], run_id: str) -> str:
    winner = summary["drawdown_winner"]
    lines = [
        "# QQQ 三项 SMA 持仓条件与逐项消融",
        "",
        f"- Run：`{run_id}`",
        f"- 共同观察区间：{summary['analysis_start']}～{summary['analysis_end']}，{summary['bars']} 个交易日",
        "- 信号/成交：完成 Close 判定，下一交易日 Open 全仓切换；初始空仓；允许小数股；零成本；现金不计息",
        "- 条件 2：SMA30/200/250/300 四条线均连续三个交易日严格增加，即三个正差分",
        "",
        "| 版本 | 总收益 | CAGR | Sharpe | 最大回撤 | 持仓率 | 成交 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in rows:
        lines.append(
            f"| {item['name']} | {float(item['total_return_pct']):.2f}% | "
            f"{float(item['cagr_pct']):.3f}% | {float(item['sharpe']):.3f} | "
            f"{float(item['max_drawdown_pct']):.2f}% | {float(item['exposure_pct']):.2f}% | "
            f"{int(item['order_count'])} |"
        )
    lines.extend(
        [
            "",
            "## 回撤结论",
            "",
            f"四个策略中最大回撤最小的是 **{CASE_LABELS[winner['case_id']]}**：{winner['max_drawdown_pct']:.2f}%。完整策略最大回撤为 {summary['full_case']['max_drawdown_pct']:.2f}%。",
            "",
            "## 边界",
            "",
            "- 这是 QQQ 全历史样本内消融，不能据此直接进入模拟盘或断言未来回撤。",
            "- 正式策略按用户的唯一目标用最大回撤排序；收益、CAGR 与 Sharpe 只用于说明代价，不参与选择。",
            "- 零成本结果会低估频繁全仓切换的摩擦；若用于后续验证，应加入费用、滑点与样本外窗口。",
            "- 数据是拆股及股息调整 OHLC，适合一致趋势研究，但不是原始成交价加现金分红的交易所级回放。",
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
    incomplete = [
        item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"
    ]
    if incomplete:
        raise RuntimeError(f"Cannot analyze an incomplete run: {incomplete}")

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    symbol = context.config["symbols"][0]
    block = block_root(context, args.run_id, symbol, 0.0)
    results = pd.read_csv(block / "parameter_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["signal_date", "date"])
    indicators = pd.read_csv(block / "indicators.csv", parse_dates=["date"])
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    rows = ordered_rows(results, metrics["benchmark"])
    strategy_winner = results.sort_values(
        ["max_drawdown_pct", "total_return_pct"], ascending=[False, False]
    ).iloc[0].to_dict()
    full_case = results[results["case_id"] == "all_conditions"].iloc[0].to_dict()
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "purpose": "drawdown-first full-history four-case SMA condition ablation",
        "analysis_start": metrics["analysis_start"],
        "analysis_end": metrics["analysis_end"],
        "bars": metrics["bars"],
        "drawdown_winner": strategy_winner,
        "full_case": full_case,
        "comparison_rows": rows,
        "benchmark": metrics["benchmark"],
        "max_cross_check_differences": metrics["max_cross_check_differences"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            "QQQ、四条均线与完整策略成交点",
            build_market_figure(indicators, orders),
            "market",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            "四组收益曲线与对应回撤",
            build_performance_figure(daily, benchmark),
            "performance",
        ),
    ]
    (run_root / "report.md").write_text(
        markdown_report(summary, rows, args.run_id), encoding="utf-8"
    )
    report = render_interactive_report(
        title="QQQ 三项 SMA 持仓条件与逐项消融",
        heading="QQQ 三项 SMA 持仓条件与逐项消融",
        subtitle="只为控制回撤：完整策略，以及分别去掉条件 1、2、3 的三条收益与回撤路径。",
        summary_html=summary_html(summary, rows),
        notes=[
            "只有启用条件全部成立才目标持有；任一启用条件不成立就目标空仓，之后重新全部成立才买回。",
            "收盘后确认信号，下一交易日 Open 全仓切换；最后一个数据日不虚构下一日成交。",
            "四个策略共享同一 SMA300+三次变化预热、初始 100,000 美元、零成本和完整 QQQ 数据截止日。",
            "报告默认展示四条正式曲线和 Buy & Hold；区间左端对齐及定投是浏览器展示情景，不改写正式结果。",
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
        "backtest/scripts/run_sma_regime_ablation.py",
        "backtest/scripts/analyze_sma_regime_ablation.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/tests/strategies/rot/test_sma_regime.py",
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

This immutable run evaluates the full QQQ three-condition regime and each single-condition ablation.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable drawdown-first comparison.
- `QQQ/cost_0bps/`: four PyBroker/reference ledgers, benchmark, indicators and hashes.
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
        if path.is_file() and path.name not in {
            "artifact_manifest.json", "run.json", "validation.json"
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
