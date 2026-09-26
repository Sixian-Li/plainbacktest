#!/usr/bin/env python3
"""Build a formal chart-first report for one frozen intraday-SMA strategy."""

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
from quantkit.intraday_sma import IntradaySmaSpec, prepare_intraday_sma_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history"


def signal_labels(parameters: dict[str, Any]) -> dict[str, str]:
    f = "/".join(str(value) for value in parameters["F_short_sma_windows"])
    return {
        "SELL_COST_STOP": f"成本价下方 {parameters['L_cost_stop_pct']:g}%",
        "SELL_SLOW_TREND": f"{f} 平均连续 {parameters['A_negative_days_slow']} 日转弱且跌破 SMA{parameters['B_slow_sma_window']}",
        "SELL_FAST_DROP": f"SMA{f} 各自变化率均 ≤ {parameters['C_fast_derivative_pct']:g}%",
        "SELL_SMA200_CROSS": f"下穿 SMA{parameters['E_fallback_sma_window']}",
        "BUY_FORCED_REENTRY": f"重新达到卖出价 +{parameters['R_forced_rebuy_pct']:g}%",
        "BUY_SMA200_CROSS": f"上穿 SMA{parameters['H_reentry_sma_window']}",
        "BUY_SHORT_RECOVERY": f"上穿 {f} 平均线下方 {parameters['G_short_recovery_below_pct']:g}%",
    }


def performance_figure(daily: pd.DataFrame, benchmark: pd.DataFrame, symbol: str) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("完整评价窗口账户净值", "从各自峰值回撤"),
    )
    for frame, key, label, color, dash, is_benchmark in (
        (daily, "strategy", "冻结参数策略", "#0f766e", "solid", False),
        (benchmark, "buy_hold", f"{symbol} 窗口首日买入持有", "#334155", "dash", True),
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
                    line={"color": color, "width": 2.4, "dash": dash},
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
        height=780,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        uirevision=f"{symbol.lower()}-fixed-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def annual_return_figure(yearly: pd.DataFrame, symbol: str) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=yearly["year"],
            y=yearly["strategy_return_pct"],
            name="冻结策略",
            marker_color="#0f766e",
            hovertemplate="%{x}<br>冻结策略 %{y:.2f}%<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=yearly["year"],
            y=yearly["benchmark_return_pct"],
            name=f"{symbol} Buy & Hold",
            marker_color="#64748b",
            hovertemplate=f"%{{x}}<br>{symbol} %{{y:.2f}}%<extra></extra>",
        )
    )
    figure.add_hline(y=0, line={"color": "#111827", "width": 1})
    figure.update_layout(
        barmode="group",
        height=590,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.09},
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
    )
    figure.update_yaxes(title_text="日历年度收益（%）", fixedrange=False)
    figure.update_xaxes(dtick=1)
    return figure


def signal_figure(orders: pd.DataFrame, labels: dict[str, str]) -> go.Figure:
    counts = orders["primary_signal"].value_counts().rename_axis("signal").reset_index(name="count")
    counts["label"] = counts["signal"].map(lambda value: labels.get(str(value), str(value)))
    counts["side"] = counts["signal"].str.startswith("BUY").map({True: "买入", False: "卖出"})
    colors = counts["side"].map({"买入": "#0f766e", "卖出": "#c2413b"})
    figure = go.Figure(
        go.Bar(
            x=counts["count"],
            y=counts["label"],
            orientation="h",
            marker_color=colors,
            customdata=counts[["side"]],
            hovertemplate="%{y}<br>%{customdata[0]} %{x} 次<extra></extra>",
        )
    )
    figure.update_layout(height=510, margin={"l": 280, "r": 35, "t": 45, "b": 55})
    figure.update_xaxes(title_text="作为主成交原因的次数", fixedrange=False)
    figure.update_yaxes(autorange="reversed")
    return figure


def metrics_table(summary: dict[str, Any]) -> str:
    strategy = summary["strategy"]
    benchmark = summary["benchmark"]
    rows = []
    fields = (
        ("期末净值", "final_equity", "$", 2),
        ("总收益", "total_return_pct", "%", 2),
        ("CAGR", "cagr_pct", "%", 3),
        ("Sharpe", "sharpe", "", 3),
        ("Sortino", "sortino", "", 3),
        ("年化波动", "annual_volatility_pct", "%", 2),
        ("最大回撤", "max_drawdown_pct", "%", 2),
        ("最大回撤持续天数", "max_drawdown_duration_days", " 天", 0),
        ("持仓率", "exposure_pct", "%", 2),
    )
    for label, field, suffix, digits in fields:
        prefix = "$" if suffix == "$" else ""
        actual_suffix = "" if suffix == "$" else suffix
        rows.append(
            f"<tr><td>{label}</td><td>{prefix}{float(strategy[field]):,.{digits}f}{actual_suffix}</td>"
            f"<td>{prefix}{float(benchmark[field]):,.{digits}f}{actual_suffix}</td></tr>"
        )
    return "".join(rows)


def parameter_summary(parameters: dict[str, Any]) -> str:
    f = "/".join(str(value) for value in parameters["F_short_sma_windows"])
    return (
        f"A={parameters['A_negative_days_slow']}、B={parameters['B_slow_sma_window']}、"
        f"C={parameters['C_fast_derivative_pct']:g}%、D={parameters['D_negative_days_fast']}、"
        f"E={parameters['E_fallback_sma_window']}、F=SMA{f}、"
        f"G={parameters['G_short_recovery_below_pct']:g}%、H={parameters['H_reentry_sma_window']}、"
        f"L={parameters['L_cost_stop_pct']:g}%、R={parameters['R_forced_rebuy_pct']:g}%"
    )


def summary_html(summary: dict[str, Any], yearly: pd.DataFrame) -> str:
    strategy = summary["strategy"]
    symbol = summary["symbol"]
    labels = signal_labels(summary["parameters"])
    first_label = labels.get(summary["first_entry_signal"], summary["first_entry_signal"])
    return f"""
<h2>冻结规格</h2>
<p>{parameter_summary(summary['parameters'])}，强制买回开启。本 run 只有这一个 case，没有继续优化。</p>
<p>策略从 {summary['analysis_start']} 以 100,000 美元现金开始；第一笔普通买入是 <strong>{summary['first_entry_date']}</strong> 的“{html.escape(first_label)}”，成交价 ${summary['first_entry_fill']:.4f}。完整绩效包含此前现金等待期；{symbol} 基准在窗口首日 Open 买入。</p>
<h2>最终历史表现</h2>
<table><thead><tr><th>指标</th><th>最终策略</th><th>{symbol} Buy &amp; Hold</th></tr></thead><tbody>{metrics_table(summary)}</tbody></table>
<p>相对 {symbol}：CAGR {summary['delta_cagr_pct_points']:+.3f} 个百分点，Sharpe {summary['delta_sharpe']:+.3f}，最大回撤差 {summary['delta_max_drawdown_pct_points']:+.2f} 个百分点（越接近 0 表示回撤越小）。策略共 {int(strategy['order_count'])} 笔成交，持仓率 {strategy['exposure_pct']:.2f}%；在 {len(yearly)} 个日历年度/部分年度中有 {summary['years_strategy_outperformed']} 个跑赢 {symbol}。</p>
"""


def markdown_report(summary: dict[str, Any], yearly: pd.DataFrame, run_id: str) -> str:
    strategy = summary["strategy"]
    benchmark = summary["benchmark"]
    symbol = summary["symbol"]
    labels = signal_labels(summary["parameters"])
    parameters = summary["parameters"]
    if parameters.get("training_experiment_id"):
        research_boundary = (
            f"- 参数只用 SPY 1993–2002 训练并在本轮前冻结；{summary['analysis_start']}～"
            f"{summary['analysis_end']} 是紧随其后的锁定测试，未在本窗口再次调参。"
        )
        remaining_boundary = (
            f"- {parameters.get('remaining_unopened_start', '测试窗口以后')} 起的数据没有进入本轮计算或结论；"
            "若以后打开，应继续原样冻结参数，不能用本轮结果回改。"
        )
    else:
        research_boundary = (
            f"- 参数先在 QQQ 历史上经过全局搜索、分窗比较和敏感性分析；本轮 {symbol} "
            "固定窗口是跨标的历史检验，没有再次调参，但两个指数 ETF 的市场风险高度相关，因此不是统计独立的样本外证明。"
        )
        remaining_boundary = None
    lines = [
        f"# {symbol} 冻结参数固定窗口回测",
        "",
        f"- Run：`{run_id}`",
        f"- 参数：{parameter_summary(summary['parameters'])}，强制买回开启",
        f"- 评价窗口：{summary['analysis_start']}～{summary['analysis_end']}；从窗口起点计绩，包含空仓等待；第一笔普通买入为 {summary['first_entry_date']}，成交价 ${summary['first_entry_fill']:.4f}",
        "- 零费用、零滑点、允许小数股、不融资、现金不计息、每天最多一笔",
        "",
        f"| 指标 | 最终策略 | {symbol} Buy & Hold |",
        "|---|---:|---:|",
        f"| 期末净值 | ${strategy['final_equity']:,.2f} | ${benchmark['final_equity']:,.2f} |",
        f"| 总收益 | {strategy['total_return_pct']:.2f}% | {benchmark['total_return_pct']:.2f}% |",
        f"| CAGR | {strategy['cagr_pct']:.3f}% | {benchmark['cagr_pct']:.3f}% |",
        f"| Sharpe | {strategy['sharpe']:.3f} | {benchmark['sharpe']:.3f} |",
        f"| Sortino | {strategy['sortino']:.3f} | {benchmark['sortino']:.3f} |",
        f"| 年化波动 | {strategy['annual_volatility_pct']:.2f}% | {benchmark['annual_volatility_pct']:.2f}% |",
        f"| 最大回撤 | {strategy['max_drawdown_pct']:.2f}% | {benchmark['max_drawdown_pct']:.2f}% |",
        f"| 最大回撤持续 | {int(strategy['max_drawdown_duration_days'])} 天 | {int(benchmark['max_drawdown_duration_days'])} 天 |",
        f"| 持仓率 | {strategy['exposure_pct']:.2f}% | {benchmark['exposure_pct']:.2f}% |",
        f"| 成交笔数 | {int(strategy['order_count'])} | 0 |",
        "",
        "## 主成交原因",
        "",
    ]
    for signal, count in sorted(summary["primary_signal_counts"].items(), key=lambda item: -item[1]):
        lines.append(f"- {labels.get(signal, signal)}：{count} 次。")
    lines.extend(
        [
            "",
            "## 年度比较摘要",
            "",
            f"- 共 {len(yearly)} 个日历年度或部分年度，策略 {summary['years_strategy_outperformed']} 个年度跑赢 {symbol}，{summary['years_benchmark_outperformed_or_tied']} 个年度未跑赢。",
            f"- 最好相对年度：{int(yearly.loc[yearly['delta_return_pct_points'].idxmax(), 'year'])}，领先 {yearly['delta_return_pct_points'].max():.2f} 个百分点。",
            f"- 最差相对年度：{int(yearly.loc[yearly['delta_return_pct_points'].idxmin(), 'year'])}，落后 {abs(yearly['delta_return_pct_points'].min()):.2f} 个百分点。",
            "",
            "## 解释边界",
            "",
            research_boundary,
            *([remaining_boundary] if remaining_boundary else []),
            "- 当前使用供应商的拆股及股息调整 OHLC，近似含分红再投资，但没有独立现金股息账本；常规时段日线也不能还原真实夜盘路径。",
            "- 零成本结果会高估可交易表现。若进入模拟盘，下一步应冻结本规格并加入佣金、点差/滑点与实时数据语义，不能再用同一历史回改参数。",
            "- 报告不生成逐笔买卖表；完整订单、成交、信号计划和每日账户状态保存在机器账本，K 线 hover 可查看成交原因。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    if len(context.config["symbols"]) != 1:
        raise ValueError("Fixed report expects exactly one configured symbol.")
    symbol = str(context.config["symbols"][0])
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, symbol, 0.0)
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    yearly = pd.read_csv(block / "annual_returns.csv", parse_dates=["start_date", "end_date"])
    summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    parameters = context.config["parameters"]
    labels = signal_labels(parameters)
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        **summary,
        "annual_returns": base.json_safe(yearly.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(base.json_safe(analysis_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    raw = pd.read_csv(WORKSPACE_ROOT / f"data/processed/daily/{symbol}.csv", parse_dates=["date"])
    spec = IntradaySmaSpec.from_parameters(parameters)
    prices = prepare_intraday_sma_data(raw, spec)
    prices = prices[
        (prices["date"] >= pd.Timestamp(parameters["analysis_start"]))
        & (prices["date"] <= pd.Timestamp(parameters["analysis_end"]))
    ].reset_index(drop=True)
    base.SIGNAL_LABELS = labels
    figures = [
        ReportFigure(f"performance-{symbol.lower()}", f"最终策略与 {symbol} 完整窗口净值", performance_figure(daily, benchmark, symbol), "performance"),
        ReportFigure("annual-returns", f"逐年收益：最终策略与 {symbol}", annual_return_figure(yearly, symbol), "generic"),
        ReportFigure("signal-counts", "各规则作为主成交原因的次数", signal_figure(orders, labels), "generic"),
        ReportFigure(
            f"market-{symbol.lower()}",
            f"{symbol} 价格、可选均线与成交原因",
            base.build_market_figure(
                prices,
                orders,
                short_windows=spec.f_short_sma_windows,
                symbol=symbol,
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title=f"{symbol} 冻结参数 {parameters['analysis_start'][:4]}–{parameters['analysis_end'][:4]} 回测",
        heading=f"{symbol} 冻结参数 {parameters['analysis_start'][:4]}–{parameters['analysis_end'][:4]} 回测",
        subtitle="参数冻结后只运行一个 case；完整窗口统一计绩，并与窗口首日买入持有比较。",
        summary_html=summary_html(summary, yearly),
        notes=[
            "本轮参数由用户预先冻结，没有在本 run 内继续优化或挑选赢家。",
            f"绩效从 {parameters['analysis_start']} 共同窗口起点计算；窗口前历史只供指标预热、禁止交易，策略等待首买期间保留现金，{symbol} 基准在窗口首日 Open 买入。",
            "主图的配置短均线、三线平均、SMA70–450 及成交标记可勾选；变化率副图和价格轴均支持可见区间自动缩放。",
            "净值区间对齐和定投是浏览器端情景显示，不会改变正式回测账本。",
            "完整订单只在机器账本与 K 线 hover 保存，不生成逐笔长表。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(markdown_report(summary, yearly, args.run_id), encoding="utf-8")

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
        "backtest/scripts/run_intraday_sma_final_fixed.py",
        "backtest/scripts/analyze_intraday_sma_final_fixed.py",
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
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run evaluates the user-frozen intraday-SMA parameter vector on {symbol}.

- `report.html` / `report.md`: chart-first reports without a per-order table.
- `analysis/summary.json`: machine-readable metrics, annual returns and signal counts.
- `{symbol}/cost_0bps/`: PyBroker/reference ledgers, annual returns, orders, trades, plans and hashes.
- `provenance.json` / `validation.json`: exact source and correctness evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
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
