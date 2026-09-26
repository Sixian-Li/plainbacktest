#!/usr/bin/env python3
"""Analyze and report the six-case QQQ StochRSI structural ablation."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import (
    assert_run_writable,
    cost_label,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_stochrsi_pool_ablation import CASE_LABELS, CASE_SPECS


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80a.1__26-08-25__qqq_stochrsi_pool_ablation"
COLORS = {
    "FULL": "#2563eb", "NO_POOL_B": "#7c3aed", "NO_A_DAILY_DECAY": "#dc2626",
    "NO_B1_FLOOR": "#16a34a", "NO_B2_FLOOR": "#e67e22",
    "DEFERRED_BUY_CROSS_040": "#0891b2",
}


def unitized(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    equity = frame["equity"].to_numpy(float)
    flows = frame.get("external_contribution", pd.Series(0.0, index=frame.index)).to_numpy(float)
    returns = np.zeros(len(frame))
    returns[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1.0
    index = np.cumprod(1.0 + returns)
    drawdown = index / np.maximum.accumulate(index) - 1.0
    return index, drawdown


def result_table(rows: pd.DataFrame) -> str:
    body = []
    for row in rows.itertuples():
        body.append(
            "<tr>"
            f"<td>{html.escape(row.label)}</td><td>{row.cagr_pct:.3f}%</td>"
            f"<td>{row.matched_bh_cagr_pct:.3f}%</td>"
            f"<td>{row.cagr_gap_vs_matched_bh_pct_points:+.3f}pp</td>"
            f"<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>"
            f"<td>{row.drawdown_improvement_vs_matched_bh_pct_points:+.2f}pp</td>"
            f"<td>${row.external_contributions:,.2f}</td><td>${row.final_equity:,.2f}</td>"
            f"<td>{int(row.order_count)}</td></tr>"
        )
    return (
        "<h2>六个固定案例</h2><table><thead><tr><th>案例</th><th>CAGR</th>"
        "<th>同步注资B&H CAGR</th><th>CAGR差</th><th>Sharpe</th><th>最大回撤</th>"
        "<th>回撤改善</th><th>外部注资</th><th>期末资产</th><th>订单</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def market_figure(block: Path) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    case = block / "cases" / "FULL"
    daily = pd.read_csv(case / "daily.csv", parse_dates=["date"])
    orders = pd.read_csv(case / "orders.csv", parse_dates=["date"])
    figure = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.42, 0.21, 0.18, 0.19],
        subplot_titles=("QQQ Close与完整策略成交", "StochRSI 42 / 100", "虚拟池市值", "QQQ持仓市值与现金"),
    )
    figure.add_trace(go.Scatter(
        x=indicator["date"], y=indicator["close"], mode="lines", name="QQQ Close",
        line={"color": "#111827", "width": 1.5},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ), row=1, col=1)
    for side, color, marker in (("buy", "#16a34a", "triangle-up"), ("sell", "#dc2626", "triangle-down")):
        current = orders[orders["type"].eq(side)]
        figure.add_trace(go.Scatter(
            x=current["date"], y=current["fill_price"], mode="markers", name=side,
            marker={"color": color, "symbol": marker, "size": 7}, text=current["primary_signal"],
            hovertemplate="%{x}<br>%{y:.2f}<br>%{text}<extra></extra>",
            meta={"series_key": f"orders_{side}", "panel": "market", "label": side},
        ), row=1, col=1)
    for period, color in ((42, "#2563eb"), (100, "#e67e22")):
        figure.add_trace(go.Scatter(
            x=indicator["date"], y=indicator[f"stochrsi_{period}"], mode="lines",
            name=f"StochRSI {period}", line={"color": color, "width": 1.2},
            meta={"series_key": f"stochrsi_{period}", "panel": "derivative", "label": f"StochRSI {period}"},
        ), row=2, col=1)
    for level in (0.2, 0.3, 0.4, 0.5, 0.7, 0.8):
        figure.add_hline(y=level, line={"color": "#94a3b8", "dash": "dot", "width": 0.7}, row=2, col=1)
    for column, label, color in (("pool_a_value", "Pool A", "#7c3aed"), ("pool_b_value", "Pool B", "#0891b2")):
        figure.add_trace(go.Scatter(
            x=daily["date"], y=daily[column], mode="lines", name=label,
            line={"color": color, "width": 1.2},
            meta={"series_key": column, "panel": "pool", "label": label},
        ), row=3, col=1)
    position_value = daily["shares"] * daily["close"]
    for values, key, label, color in (
        (position_value, "position_value", "QQQ持仓市值", "#2563eb"),
        (daily["cash"], "cash_value", "现金", "#d97706"),
    ):
        figure.add_trace(go.Scatter(
            x=daily["date"], y=values, mode="lines", name=label,
            line={"color": color, "width": 1.3},
            meta={"series_key": key, "panel": "allocation", "label": label},
        ), row=4, col=1)
    figure.update_layout(template="plotly_white", height=1120, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="0–1", range=[-0.03, 1.03], row=2, col=1)
    figure.update_yaxes(title_text="美元", row=3, col=1)
    figure.update_yaxes(title_text="美元", row=4, col=1)
    return figure


def performance_figure(block: Path) -> go.Figure:
    case = block / "cases" / "FULL"
    strategy = pd.read_csv(case / "daily.csv", parse_dates=["date"])
    modified = pd.read_csv(case / "modified_buy_hold_daily.csv", parse_dates=["date"])
    _, strategy_dd = unitized(strategy)
    _, modified_dd = unitized(modified)
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.70, 0.30], subplot_titles=("完整策略资金构成与同步注资基准", "现金流调整后的回撤"),
    )
    traces = (
        (strategy["equity"], "完整策略总资产", "#2563eb", "full_equity", False, "solid"),
        (modified["equity"], "Modified Buy & Hold", "#334155", "modified_bh", True, "solid"),
        (strategy["shares"] * strategy["close"], "策略QQQ持仓市值", "#16a34a", "full_position", False, "dash"),
        (strategy["cash"], "策略现金", "#d97706", "full_cash", False, "dot"),
    )
    for values, label, color, key, benchmark, dash in traces:
        figure.add_trace(go.Scatter(
            x=strategy["date"], y=values, mode="lines", name=label,
            line={"color": color, "width": 1.7, "dash": dash},
            meta={"series_key": key, "panel": "equity", "label": label, "is_benchmark": benchmark, "cost_bps": 0},
        ), row=1, col=1)
    for values, label, color, key in (
        (strategy_dd * 100, "完整策略回撤", "#2563eb", "full_drawdown"),
        (modified_dd * 100, "Modified B&H回撤", "#334155", "modified_drawdown"),
    ):
        figure.add_trace(go.Scatter(
            x=strategy["date"], y=values, mode="lines", name=label,
            line={"color": color, "width": 1.2},
            meta={"series_key": key, "panel": "drawdown", "label": label},
        ), row=2, col=1)
    figure.update_layout(template="plotly_white", height=760, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def ablation_figure(block: Path) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.68, 0.32], subplot_titles=("六案例现金流调整净值", "六案例回撤"),
    )
    for case_id in CASE_SPECS:
        daily = pd.read_csv(block / "cases" / case_id / "daily.csv", parse_dates=["date"])
        index, drawdown = unitized(daily)
        label = CASE_LABELS[case_id]
        meta = {"series_key": case_id.lower(), "label": label, "cost_bps": 0}
        figure.add_trace(go.Scatter(
            x=daily["date"], y=index, mode="lines", name=label,
            line={"color": COLORS[case_id], "width": 1.6}, meta={**meta, "panel": "equity"},
        ), row=1, col=1)
        figure.add_trace(go.Scatter(
            x=daily["date"], y=drawdown * 100, mode="lines", showlegend=False,
            line={"color": COLORS[case_id], "width": 1.1}, meta={**meta, "panel": "drawdown"},
        ), row=2, col=1)
    benchmark = pd.read_csv(
        block / "cases" / "FULL" / "modified_buy_hold_daily.csv", parse_dates=["date"]
    )
    benchmark_index, benchmark_drawdown = unitized(benchmark)
    benchmark_meta = {
        "series_key": "full_modified_buy_hold", "label": "完整策略同步注资Buy & Hold",
        "cost_bps": 0, "is_benchmark": True,
    }
    figure.add_trace(go.Scatter(
        x=benchmark["date"], y=benchmark_index, mode="lines",
        name="完整策略同步注资Buy & Hold",
        line={"color": "#334155", "width": 1.8, "dash": "dash"},
        meta={**benchmark_meta, "panel": "equity"},
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=benchmark["date"], y=benchmark_drawdown * 100, mode="lines",
        name="完整策略同步注资B&H回撤", showlegend=False,
        line={"color": "#334155", "width": 1.2, "dash": "dash"},
        meta={**benchmark_meta, "panel": "drawdown"},
    ), row=2, col=1)
    figure.update_layout(template="plotly_white", height=760, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="起点=1", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    run = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in run["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    block = run_root / "QQQ" / cost_label(0)
    metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    rows = pd.read_csv(block / "parameter_results.csv")
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    rows.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    ranked = rows.sort_values(["cagr_gap_vs_matched_bh_pct_points", "sharpe"], ascending=False)
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "predeclared six-case structural ablation over the full 2015-2026 exploratory window",
        "analysis_start": metrics["analysis_start"], "analysis_end": metrics["analysis_end"],
        "cases": metrics["cases"], "naive_buy_hold": metrics["naive_buy_hold"],
        "strict_boundary_equality_audit": metrics["strict_boundary_equality_audit"],
        "ranking_by_cagr_gap_then_sharpe": ranked["case_id"].tolist(),
        "max_cross_check_difference": metrics["max_cross_check_difference"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# QQQ StochRSI分档补仓与虚拟池结构消融", "",
        "- 窗口：2015-01-02～2026-08-04完整样本探索。", "- 成本：0 bps。",
        "- 六案均逐案对照自己的同步注资Buy & Hold。", "",
        "| 案例 | CAGR | 同步B&H CAGR | CAGR差 | Sharpe | 最大回撤 | 回撤改善 | 外部注资 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples():
        lines.append(
            f"| {row.label} | {row.cagr_pct:.3f}% | {row.matched_bh_cagr_pct:.3f}% | "
            f"{row.cagr_gap_vs_matched_bh_pct_points:+.3f}pp | {row.sharpe:.3f} | "
            f"{row.max_drawdown_pct:.2f}% | {row.drawdown_improvement_vs_matched_bh_pct_points:+.2f}pp | "
            f"${row.external_contributions:,.2f} |"
        )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = render_interactive_report(
        title="QQQ StochRSI分档补仓与虚拟池结构消融",
        heading="QQQ 2015–2026 六个固定结构案例",
        subtitle="StochRSI 42/100 · 零成本 · 同Close成交 · 每案独立同步注资基准",
        summary_html=result_table(rows),
        notes=[
            "所有结果都是完整窗口探索，不是样本外证据，也没有根据结果追加或替换案例。",
            "延迟买入案按虚拟剩余现金累计原本计划投入的金额，并在StochRSI100严格上穿0.40时统一成交；任何实际卖出会取消待买金额。",
            "完整策略资金图同时展示总资产、QQQ持仓市值和现金；市场图另加持仓与现金面板。",
            "普通成交为收盘确认并按同一收盘价成交；快速下跌特例仍不检查真实OHLC触达。",
            "浏览器定投和区间重定基准只改变显示，不改写正式账本。",
        ],
        figures=[
            ReportFigure("market-qqq", "完整策略：市场、指标、虚拟池与资金构成", market_figure(block), "market"),
            ReportFigure("ablation-qqq", "六个结构案例对比", ablation_figure(block), "performance"),
            ReportFigure("performance-qqq", "完整策略收益、持仓资金与现金", performance_figure(block), "performance"),
        ],
        experiment=context.config, run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/stochrsi_scaled_pools.py",
        "backtest/quantkit/metrics.py", "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_pool_ablation.py",
        "backtest/scripts/analyze_stochrsi_pool_ablation.py",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id, "created_at_utc": summary["created_at_utc"],
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
        f"# Run {args.run_id}\n\nFrozen six-case QQQ StochRSI structural ablation; see report.html, report.md, analysis/, and QQQ/cost_0bps/.\n",
        encoding="utf-8",
    )
    artifact_manifest = {"schema_version": 1, "created_at_utc": summary["created_at_utc"], "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
