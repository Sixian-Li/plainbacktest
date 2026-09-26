#!/usr/bin/env python3
"""Analyze and report the frozen scaled StochRSI virtual-pool strategy."""

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


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80__26-08-25__qqq_stochrsi_scaled_pools_2015_2026"
LABELS = {
    "STRATEGY": "StochRSI分档补仓与双池减仓",
    "MODIFIED_BUY_HOLD": "Modified Buy & Hold（同步注资）",
    "NAIVE_BUY_HOLD": "Naive Buy & Hold（不追加）",
}
COLORS = {"STRATEGY": "#2563eb", "MODIFIED_BUY_HOLD": "#334155"}


def display_rows(metrics: dict) -> pd.DataFrame:
    rows = []
    for key in ("strategy", "modified_buy_hold", "naive_buy_hold"):
        item = dict(metrics[key])
        case_id = {
            "strategy": "STRATEGY", "modified_buy_hold": "MODIFIED_BUY_HOLD",
            "naive_buy_hold": "NAIVE_BUY_HOLD",
        }[key]
        rows.append({
            "case_id": case_id, "label": LABELS[case_id],
            "cagr_pct": item["cagr_pct"], "sharpe": item["sharpe"],
            "max_drawdown_pct": item["max_drawdown_pct"],
            "xirr_pct": item.get("xirr_pct"),
            "external_contributions": item.get("external_contributions", 0.0),
            "total_contributed_capital": item.get("total_contributed_capital", item["initial_cash"]),
            "final_equity": item["final_equity"],
            "net_profit": item.get("net_profit", item["final_equity"] - item["initial_cash"]),
            "order_count": item["order_count"], "exposure_pct": item["exposure_pct"],
        })
    return pd.DataFrame(rows)


def result_table(rows: pd.DataFrame, metrics: dict) -> str:
    body = []
    for row in rows.itertuples():
        xirr = "—" if pd.isna(row.xirr_pct) else f"{row.xirr_pct:.3f}%"
        body.append(
            "<tr>"
            f"<td>{html.escape(row.label)}</td><td>{row.cagr_pct:.3f}%</td>"
            f"<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>"
            f"<td>{xirr}</td><td>${row.total_contributed_capital:,.2f}</td>"
            f"<td>${row.final_equity:,.2f}</td><td>${row.net_profit:,.2f}</td>"
            f"<td>{int(row.order_count)}</td></tr>"
        )
    equality = metrics["strict_boundary_equality_audit"]
    return (
        "<h2>零成本关键结果</h2><table><thead><tr><th>账户</th><th>CAGR</th>"
        "<th>Sharpe</th><th>最大回撤</th><th>XIRR</th><th>累计投入</th>"
        "<th>期末资产</th><th>净盈利</th><th>订单</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
        f"<p>策略实际外部注资 ${metrics['strategy']['external_contributions']:,.2f}，共"
        f" {metrics['fast_drop_event_count']} 次快速下跌0.30理论价成交。"
        f"StochRSI100精确等于0.60/0.30分别出现 {equality['stochrsi100_exact_0_60_count']} / "
        f"{equality['stochrsi100_exact_0_30_count']} 次。</p>"
    )


def market_figure(block: Path) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    figure = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        row_heights=[0.52, 0.25, 0.23],
        subplot_titles=("QQQ Close与成交", "StochRSI 42 / 100", "虚拟池市值"),
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
            marker={"color": color, "symbol": marker, "size": 7},
            text=current["primary_signal"], hovertemplate="%{x}<br>%{y:.2f}<br>%{text}<extra></extra>",
            meta={"series_key": f"orders_{side}", "panel": "market", "label": side},
        ), row=1, col=1)
    for period, color in ((42, "#2563eb"), (100, "#e67e22")):
        figure.add_trace(go.Scatter(
            x=indicator["date"], y=indicator[f"stochrsi_{period}"], mode="lines",
            name=f"StochRSI {period}", line={"color": color, "width": 1.2},
            meta={"series_key": f"stochrsi_{period}", "panel": "derivative", "label": f"StochRSI {period}"},
        ), row=2, col=1)
    for level in (0.2, 0.3, 0.5, 0.7, 0.8):
        figure.add_hline(y=level, line={"color": "#94a3b8", "dash": "dot", "width": 0.7}, row=2, col=1)
    for column, label, color in (("pool_a_value", "Pool A", "#7c3aed"), ("pool_b_value", "Pool B", "#0891b2")):
        figure.add_trace(go.Scatter(
            x=daily["date"], y=daily[column], mode="lines", name=label,
            line={"color": color, "width": 1.2},
            meta={"series_key": column, "panel": "pool", "label": label},
        ), row=3, col=1)
    figure.update_layout(template="plotly_white", height=920, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
    figure.update_yaxes(title_text="0–1", range=[-0.03, 1.03], row=2, col=1)
    figure.update_yaxes(title_text="美元", row=3, col=1)
    return figure


def unitized(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    equity = frame["equity"].to_numpy(float)
    flows = frame.get("external_contribution", pd.Series(0.0, index=frame.index)).to_numpy(float)
    returns = np.zeros(len(frame))
    returns[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1.0
    index = np.cumprod(1.0 + returns)
    drawdown = index / np.maximum.accumulate(index) - 1.0
    return index, drawdown


def performance_figure(block: Path) -> go.Figure:
    strategy = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    modified = pd.read_csv(block / "modified_buy_hold_daily.csv", parse_dates=["date"])
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.7, 0.3], subplot_titles=("真实账户金额", "现金流调整后的回撤"),
    )
    for case_id, frame in (("MODIFIED_BUY_HOLD", modified), ("STRATEGY", strategy)):
        _, drawdown = unitized(frame)
        meta = {
            "series_key": case_id.lower(), "panel": "equity", "label": LABELS[case_id],
            "is_benchmark": case_id == "MODIFIED_BUY_HOLD", "cost_bps": 0,
        }
        figure.add_trace(go.Scatter(
            x=frame["date"], y=frame["equity"], mode="lines", name=LABELS[case_id],
            line={"color": COLORS[case_id], "width": 1.8}, meta=meta,
        ), row=1, col=1)
        figure.add_trace(go.Scatter(
            x=frame["date"], y=drawdown * 100, mode="lines", showlegend=False,
            line={"color": COLORS[case_id], "width": 1.2},
            meta={"series_key": case_id.lower(), "panel": "drawdown", "label": LABELS[case_id]},
        ), row=2, col=1)
    figure.update_layout(template="plotly_white", height=720, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="美元", row=1, col=1)
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
    rows = display_rows(metrics)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    rows.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "frozen 2015-2026 full-sample exploratory path; no parameter search",
        "analysis_start": metrics["analysis_start"], "analysis_end": metrics["analysis_end"],
        "strategy": metrics["strategy"], "modified_buy_hold": metrics["modified_buy_hold"],
        "naive_buy_hold": metrics["naive_buy_hold"], "event_counts": metrics["event_counts"],
        "fast_drop_event_count": metrics["fast_drop_event_count"],
        "strict_boundary_equality_audit": metrics["strict_boundary_equality_audit"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# QQQ StochRSI分档补仓与双虚拟池减仓", "",
        "- 窗口：2015-01-02～2026-08-04完整样本探索。", "- 成本：0 bps。",
        "- 普通成交：Close确认、同Close成交；快速下跌特例不检查OHLC触达。", "",
        "| 账户 | CAGR | Sharpe | 最大回撤 | XIRR | 累计投入 | 期末资产 |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples():
        xirr = "—" if pd.isna(row.xirr_pct) else f"{row.xirr_pct:.3f}%"
        lines.append(
            f"| {row.label} | {row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{xirr} | ${row.total_contributed_capital:,.2f} | ${row.final_equity:,.2f} |"
        )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = render_interactive_report(
        title="QQQ StochRSI分档补仓与双虚拟池减仓",
        heading="QQQ 2015–2026 固定复杂仓位状态机",
        subtitle="StochRSI 42/100 · 零成本 · 同Close成交 · 外部注资与modified Buy & Hold同步",
        summary_html=result_table(rows, metrics),
        notes=[
            "CAGR、Sharpe和回撤对外部现金流做了时间加权调整；XIRR另按实际注资日期计算。",
            "真实金额图只比较策略与同步注资的modified Buy & Hold；naive Buy & Hold只进入结果表。",
            "快速下跌0.30理论价成交不检查真实OHLC触达，属于用户明确授权的理想化执行假设。",
            "浏览器定投只是显示情景，不改变正式策略或三条账户账本。",
            "本实验没有参数扰动，完整窗口结果不能视为样本外证据。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ、StochRSI与虚拟池状态", market_figure(block), "market"),
            ReportFigure("performance-qqq", "策略与Modified Buy & Hold", performance_figure(block), "performance"),
        ],
        experiment=context.config, run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/stochrsi_scaled_pools.py",
        "backtest/quantkit/metrics.py", "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_scaled_pools.py",
        "backtest/scripts/analyze_stochrsi_scaled_pools.py",
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
        f"# Run {args.run_id}\n\nFrozen QQQ scaled StochRSI virtual-pool experiment; see report.html, report.md, analysis/, and QQQ/cost_0bps/.\n",
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
