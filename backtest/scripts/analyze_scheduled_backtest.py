#!/usr/bin/env python3
"""Build the formal report for an externally supplied scheduled backtest."""

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
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1"


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def format_money(value: float) -> str:
    return f"${value:,.2f}"


def format_number(value: float, digits: int = 4) -> str:
    return f"{value:,.{digits}f}"


def build_market_figure(prices: pd.DataFrame, schedule: list[dict[str, str]]) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=prices["date"],
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=prices["date"],
            y=prices["close"],
            mode="lines",
            name="QQQ Close",
            line={"color": "#234f8b", "width": 2},
            meta={"series_key": "qqq_close", "panel": "market", "label": "QQQ Close"},
        )
    )
    indexed = prices.set_index("date")
    for side, color, symbol, label in (
        ("buy", "#087f5b", "triangle-up", "买入"),
        ("sell", "#c92a2a", "triangle-down", "卖出"),
    ):
        dates = [pd.Timestamp(item["date"]) for item in schedule if item["side"] == side]
        values = [float(indexed.loc[date, "close"]) for date in dates]
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=values,
                mode="markers",
                name=label,
                marker={"color": color, "symbol": symbol, "size": 11},
                meta={"series_key": f"scheduled_{side}", "panel": "market", "label": label},
                hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>Close $%{{y:.4f}}<extra></extra>",
            )
        )
    figure.update_layout(
        height=650,
        margin={"l": 60, "r": 25, "t": 45, "b": 55},
        hovermode="x unified",
        xaxis={"rangeslider": {"visible": True}, "type": "date"},
        yaxis={"title": "复权价格（USD）", "fixedrange": False},
        legend={"orientation": "h", "y": 1.08},
    )
    return figure


def build_performance_figure(frames: dict[float, pd.DataFrame], benchmark: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("账户净值", "从各自历史峰值回撤"),
    )
    palette = {0.0: "#0f766e", 5.0: "#d97706"}
    for cost in sorted(frames):
        key = f"strategy_{int(cost)}bps"
        label = f"手工日程（{cost:g} bps）"
        frame = frames[cost]
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["equity"],
                mode="lines",
                name=label,
                line={"color": palette.get(cost, "#555"), "width": 2.4},
                meta={"series_key": key, "panel": "equity", "label": label},
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=drawdown(frame["equity"]),
                mode="lines",
                name=f"{label}回撤",
                showlegend=False,
                line={"color": palette.get(cost, "#555"), "width": 1.7},
                meta={"series_key": key, "panel": "drawdown", "label": label},
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>",
            ),
            row=2,
            col=1,
        )

    benchmark_key = "buy_hold"
    benchmark_label = "QQQ 持有 100 股"
    for row, values, panel in (
        (1, benchmark["equity"], "equity"),
        (2, drawdown(benchmark["equity"]), "drawdown"),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=benchmark_label if row == 1 else f"{benchmark_label}回撤",
                showlegend=row == 1,
                line={"color": "#334155", "width": 2, "dash": "dash"},
                meta={
                    "series_key": benchmark_key,
                    "panel": panel,
                    "label": benchmark_label,
                    "is_benchmark": row == 1,
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
        height=760,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
    )
    figure.update_yaxes(title_text="USD", row=1, col=1, fixedrange=False)
    figure.update_yaxes(title_text="%", row=2, col=1, fixedrange=False)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def execution_rows(
    frames: dict[float, pd.DataFrame],
    orders: dict[float, pd.DataFrame],
) -> list[dict[str, Any]]:
    zero = orders[0.0][~orders[0.0]["is_initial_seed"].astype(bool)].reset_index(drop=True)
    five = orders[5.0][~orders[5.0]["is_initial_seed"].astype(bool)].reset_index(drop=True)
    if zero[["date", "type"]].to_dict("records") != five[["date", "type"]].to_dict("records"):
        raise AssertionError("Cost scenarios do not contain the same scheduled instructions.")
    states = {cost: frame.set_index("date") for cost, frame in frames.items()}
    result: list[dict[str, Any]] = []
    for index in range(len(zero)):
        date = pd.Timestamp(zero.loc[index, "date"])
        result.append(
            {
                "date": date.date().isoformat(),
                "side": str(zero.loc[index, "type"]),
                "close": float(zero.loc[index, "raw_price"]),
                "fill_0bps": float(zero.loc[index, "fill_price"]),
                "shares_0bps": float(zero.loc[index, "shares"]),
                "equity_0bps": float(states[0.0].loc[date, "equity"]),
                "fill_5bps": float(five.loc[index, "fill_price"]),
                "shares_5bps": float(five.loc[index, "shares"]),
                "equity_5bps": float(states[5.0].loc[date, "equity"]),
            }
        )
    return result


def summary_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = summary["cost_scenarios"]
    benchmark = summary["benchmark"]
    metric_rows = []
    for label, values in (
        ("手工日程（0 bps）", metrics["0bps"]),
        ("手工日程（5 bps）", metrics["5bps"]),
        ("QQQ 持有 100 股", benchmark),
    ):
        metric_rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{format_money(values['final_equity'])}</td>"
            f"<td>{values['total_return_pct']:.4f}%</td>"
            f"<td>{values['max_drawdown_pct']:.4f}%</td>"
            f"<td>{values['sharpe']:.4f}</td>"
            "</tr>"
        )
    trade_rows = []
    for item in rows:
        trade_rows.append(
            "<tr>"
            f"<td>{item['date']}</td><td>{item['side'].upper()}</td>"
            f"<td>${item['close']:.4f}</td>"
            f"<td>{format_number(item['shares_0bps'])}</td>"
            f"<td>{format_money(item['equity_0bps'])}</td>"
            f"<td>${item['fill_5bps']:.4f}</td>"
            f"<td>{format_number(item['shares_5bps'])}</td>"
            f"<td>{format_money(item['equity_5bps'])}</td>"
            "</tr>"
        )
    return f"""
<h2>严格回测结果</h2>
<p>起点为 2026-01-16 收盘后已持有 100 股 QQQ，初始市值 {format_money(summary['initial_equity'])}。清单共 19 个执行动作，最终均在 2026-04-13 收盘卖出并回到现金。</p>
<table><thead><tr><th>路径</th><th>期末净值</th><th>区间收益</th><th>最大回撤</th><th>日频 Sharpe</th></tr></thead><tbody>{''.join(metric_rows)}</tbody></table>
<p><strong>相对 QQQ：</strong>零成本日程高 {metrics['0bps']['excess_total_return_pct_points']:.4f} 个百分点；单边 5 bps 日程高 {metrics['5bps']['excess_total_return_pct_points']:.4f} 个百分点。样本只有 {summary['bars']} 个交易日，Sharpe 和年化值很不稳定，判断以区间总收益为主。</p>
<h2>逐笔执行明细</h2>
<table><thead><tr><th>日期</th><th>方向</th><th>复权 Close / 0bp 成交</th><th>0bp 成交股数</th><th>0bp 成交后净值</th><th>5bp 成交价</th><th>5bp 成交股数</th><th>5bp 成交后净值</th></tr></thead><tbody>{''.join(trade_rows)}</tbody></table>
"""


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]], run_id: str) -> str:
    zero = summary["cost_scenarios"]["0bps"]
    five = summary["cost_scenarios"]["5bps"]
    benchmark = summary["benchmark"]
    lines = [
        "# QQQ 手工交易日程严格回测",
        "",
        f"- Run：`{run_id}`",
        f"- 区间：{summary['start']}～{summary['end']}，共 {summary['bars']} 个交易日",
        f"- 初始状态：{summary['start']} 收盘后已持有 100 股，初始市值 {format_money(summary['initial_equity'])}",
        "- 成交：清单日期的常规时段复权 Close；买卖均全仓；允许小数股；现金不计息",
        "- 成本：同时报告 0 bps 与单边 5 bps；初始既有持仓不补收买入成本",
        "- 基准：同期持有最初 100 股并按期末 Close 盯市，不虚构期末卖出成本",
        "",
        "## 结果",
        "",
        "| 路径 | 期末净值 | 区间收益 | 相对基准 | 最大回撤 | 日频 Sharpe |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 手工日程（0 bps） | {format_money(zero['final_equity'])} | {zero['total_return_pct']:.4f}% | {zero['excess_total_return_pct_points']:+.4f} pp | {zero['max_drawdown_pct']:.4f}% | {zero['sharpe']:.4f} |",
        f"| 手工日程（5 bps） | {format_money(five['final_equity'])} | {five['total_return_pct']:.4f}% | {five['excess_total_return_pct_points']:+.4f} pp | {five['max_drawdown_pct']:.4f}% | {five['sharpe']:.4f} |",
        f"| QQQ 持有 100 股 | {format_money(benchmark['final_equity'])} | {benchmark['total_return_pct']:.4f}% | — | {benchmark['max_drawdown_pct']:.4f}% | {benchmark['sharpe']:.4f} |",
        "",
        "这段日程在零成本下盈利，但加入单边 5 bps 后转为小幅亏损；仍略好于同期 QQQ。区间很短且日期由观察历史后给出，因此这是路径核算，不是预测能力验证。",
        "",
        "## 逐笔明细",
        "",
        "| 日期 | 方向 | Close | 0bp 成交股数 | 0bp 净值 | 5bp 成交价 | 5bp 成交股数 | 5bp 净值 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in rows:
        lines.append(
            f"| {item['date']} | {item['side'].upper()} | ${item['close']:.4f} | "
            f"{format_number(item['shares_0bps'])} | {format_money(item['equity_0bps'])} | "
            f"${item['fill_5bps']:.4f} | {format_number(item['shares_5bps'])} | "
            f"{format_money(item['equity_5bps'])} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 数据是拆股及股息调整 OHLC，因此适合一致的总回报近似，但不是原始价格加现金分红的交易所级账户重放。",
            "- 当日 Close 成交只有在指令于收盘前已确定时才可实现；本实验把日期视为外部预先给定的 MOC 日程，不由当日 Close 生成信号。",
            "- 日期来自已观察区间，存在完全的后见偏差，不可据此推断未来收益或直接晋级模拟盘。",
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
    costs = tuple(float(value) for value in context.config["cost_scenarios_bps_per_side"])
    if set(costs) != {0.0, 5.0}:
        raise ValueError("This report requires cost scenarios 0 and 5 bps.")
    frames = {
        cost: pd.read_csv(block_root(context, args.run_id, symbol, cost) / "daily.csv", parse_dates=["date"])
        for cost in costs
    }
    orders = {
        cost: pd.read_csv(block_root(context, args.run_id, symbol, cost) / "orders.csv", parse_dates=["date"])
        for cost in costs
    }
    metrics = {
        cost: json.loads(
            (block_root(context, args.run_id, symbol, cost) / "metrics.json").read_text(
                encoding="utf-8"
            )
        )
        for cost in costs
    }
    benchmark = pd.read_csv(
        block_root(context, args.run_id, symbol, 0.0) / "buy_hold_daily.csv",
        parse_dates=["date"],
    )
    benchmark_metrics = json.loads(
        (block_root(context, args.run_id, symbol, 0.0) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )["benchmark_metrics"]
    parameters = context.config["parameters"]
    initial_equity = float(context.config["initial_cash"])
    rows = execution_rows(frames, orders)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "retrospective accounting of a user-supplied path; not predictive validation",
        "start": str(parameters["initial_date"]),
        "end": str(parameters["end_date"]),
        "bars": len(frames[0.0]),
        "initial_shares": float(parameters["initial_shares"]),
        "initial_equity": initial_equity,
        "cost_scenarios": {"0bps": metrics[0.0], "5bps": metrics[5.0]},
        "benchmark": benchmark_metrics,
        "execution_rows": rows,
    }
    summary_path = analysis_root / "summary.json"
    summary_path.write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    prices = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    prices = prices[
        (prices["date"] >= pd.Timestamp(parameters["initial_date"]))
        & (prices["date"] <= pd.Timestamp(parameters["end_date"]))
    ].reset_index(drop=True)
    figures = [
        ReportFigure(
            f"market-{symbol.lower()}",
            f"{symbol} 价格与手工买卖点",
            build_market_figure(prices, parameters["trade_schedule"]),
            "market",
        ),
        ReportFigure(
            f"performance-{symbol.lower()}",
            f"手工日程与 {symbol} 持有基准",
            build_performance_figure(frames, benchmark),
            "performance",
        ),
    ]
    report_md = markdown_report(summary, rows, args.run_id)
    (run_root / "report.md").write_text(report_md, encoding="utf-8")
    report_html = render_interactive_report(
        title="QQQ 手工交易日程严格回测",
        heading="QQQ 手工交易日程严格回测",
        subtitle="2026-01-16 已持有 100 股；按清单日期的复权 Close 全仓切换，并与同期 QQQ 持有基准比较。",
        summary_html=summary_html(summary, rows),
        notes=[
            "清单日期是成交日期；引擎在前一交易日提交外部预定指令，并于所列日期 Close 成交。",
            "初始 100 股是既有持仓，不补收买入成本；后续买卖分别按 0 bps 与单边 5 bps 计算。",
            "全仓买回允许小数股，空仓现金不计利息；4 月 13 日策略卖清，QQQ 基准按收盘市值盯市。",
            "日期来自已观察历史，结果只能说明这条路径过去如何，不能证明择时规则具有未来有效性。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    created_at = summary["created_at_utc"]
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
    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/scheduled.py",
        "backtest/scripts/run_scheduled_backtest.py",
        "backtest/scripts/analyze_scheduled_backtest.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        f"data/processed/daily/{symbol}.csv",
    ]
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
    readme = f"""# Run {args.run_id}

This immutable run strictly accounts for the externally supplied QQQ close schedule frozen in `experiment_snapshot.json`.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable comparison and every scheduled execution.
- `QQQ/cost_{{n}}bps/`: PyBroker output, independent ledger, metrics, and hashes.
- `provenance.json`: exact code, dependency, template, manifest, and price-data hashes.
- `validation.json`: mandatory gate evidence, written by the validator.

The browser-side rebasing and DCA controls are visualization scenarios and never mutate the formal ledger.
"""
    (run_root / "README.md").write_text(readme, encoding="utf-8")

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
        f"0bps={metrics[0.0]['total_return_pct']:.6f}%, "
        f"5bps={metrics[5.0]['total_return_pct']:.6f}%, "
        f"buy_hold={benchmark_metrics['total_return_pct']:.6f}%"
    )


if __name__ == "__main__":
    main()
