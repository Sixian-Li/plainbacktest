#!/usr/bin/env python3
"""Analyze and report the QQQ StochRSI sparse-entry 2x2x2 factorial."""

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

from quantkit.experiment import assert_run_writable, cost_label, load_experiment, load_run, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_intraday_sma_backtest import json_safe
from scripts.run_stochrsi_sparse_entry_factorial import CASE_FLAGS, CASE_LABELS, CASE_SPECS


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.80a.2__26-08-25__qqq_stochrsi_sparse_entry_factorial"
ANCHOR = "D0_F0_S0"
COLORS = {
    "D0_F0_S0": "#2563eb", "D0_F0_S1": "#7c3aed",
    "D0_F1_S0": "#16a34a", "D0_F1_S1": "#0891b2",
    "D1_F0_S0": "#dc2626", "D1_F0_S1": "#e67e22",
    "D1_F1_S0": "#be185d", "D1_F1_S1": "#4f46e5",
}


def unitized(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    equity = frame["equity"].to_numpy(float)
    flows = frame.get("external_contribution", pd.Series(0.0, index=frame.index)).to_numpy(float)
    returns = np.zeros(len(frame))
    returns[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1.0
    index = np.cumprod(1.0 + returns)
    drawdown = index / np.maximum.accumulate(index) - 1.0
    return index, drawdown


def factorial_effects(rows: pd.DataFrame) -> pd.DataFrame:
    signs = {factor: rows[factor].map({False: -1.0, True: 1.0}).to_numpy(float) for factor in "DFS"}
    metrics = (
        "cagr_pct", "sharpe", "max_drawdown_pct", "external_contributions",
        "average_qqq_weight_pct", "order_count",
    )
    records = []
    for term in ("D", "F", "S", "DF", "DS", "FS", "DFS"):
        contrast = np.prod([signs[factor] for factor in term], axis=0)
        record = {"term": term}
        for metric in metrics:
            record[metric] = float(2.0 * np.mean(rows[metric].to_numpy(float) * contrast))
        records.append(record)
    return pd.DataFrame(records)


def result_table(rows: pd.DataFrame) -> str:
    body = []
    for row in rows.itertuples():
        body.append(
            "<tr>"
            f"<td>{row.case_id}</td><td>{'开' if row.D else '关'}</td>"
            f"<td>{'开' if row.F else '关'}</td><td>{'开' if row.S else '关'}</td>"
            f"<td>{row.cagr_pct:.3f}%</td><td>{row.cagr_gap_vs_matched_bh_pct_points:+.3f}pp</td>"
            f"<td>{row.sharpe:.3f}</td><td>{row.max_drawdown_pct:.2f}%</td>"
            f"<td>{row.drawdown_improvement_vs_matched_bh_pct_points:+.2f}pp</td>"
            f"<td>${row.external_contributions:,.2f}</td><td>{row.average_qqq_weight_pct:.1f}%</td>"
            f"<td>{int(row.order_count)}</td></tr>"
        )
    return (
        "<h2>八个固定组合</h2><table><thead><tr><th>案例</th><th>累计0.2释放</th>"
        "<th>短周期40%</th><th>长周期50%</th><th>CAGR</th><th>对同步B&H差</th>"
        "<th>Sharpe</th><th>最大回撤</th><th>回撤改善</th><th>外部注资</th>"
        "<th>平均QQQ仓位</th><th>订单</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def effect_table(effects: pd.DataFrame) -> str:
    body = []
    for row in effects.itertuples():
        body.append(
            "<tr>"
            f"<td>{html.escape(row.term)}</td><td>{row.cagr_pct:+.3f}pp</td>"
            f"<td>{row.sharpe:+.3f}</td><td>{row.max_drawdown_pct:+.2f}pp</td>"
            f"<td>${row.external_contributions:+,.2f}</td>"
            f"<td>{row.average_qqq_weight_pct:+.2f}pp</td><td>{row.order_count:+.1f}</td></tr>"
        )
    return (
        "<h2>三因子与交互对比</h2><p>D为累计到0.2释放，F为短周期40%买入，S为长周期50%买入；"
        "正值表示开关从关到开时相应指标上升，双字母和三字母为交互对比。</p>"
        "<table><thead><tr><th>因子</th><th>CAGR影响</th><th>Sharpe影响</th>"
        "<th>回撤数值影响</th><th>外部注资影响</th><th>平均仓位影响</th><th>订单影响</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def market_figure(block: Path) -> go.Figure:
    indicator = pd.read_csv(block / "indicator_daily.csv", parse_dates=["date"])
    case = block / "cases" / ANCHOR
    daily = pd.read_csv(case / "daily.csv", parse_dates=["date"])
    orders = pd.read_csv(case / "orders.csv", parse_dates=["date"])
    figure = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.42, 0.21, 0.18, 0.19],
        subplot_titles=("QQQ Close与锚点成交", "StochRSI 42 / 100", "虚拟池市值", "QQQ持仓市值与现金"),
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
    for level in (0.2, 0.3, 0.5, 0.7, 0.8):
        figure.add_hline(y=level, line={"color": "#94a3b8", "dash": "dot", "width": 0.7}, row=2, col=1)
    for column, label, color in (("pool_a_value", "Pool A", "#7c3aed"), ("pool_b_value", "Pool B", "#0891b2")):
        figure.add_trace(go.Scatter(
            x=daily["date"], y=daily[column], mode="lines", name=label,
            line={"color": color, "width": 1.2},
            meta={"series_key": column, "panel": "pool", "label": label},
        ), row=3, col=1)
    for values, key, label, color in (
        (daily["shares"] * daily["close"], "position_value", "QQQ持仓市值", "#2563eb"),
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


def comparison_figure(block: Path) -> go.Figure:
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.68, 0.32], subplot_titles=("八案例现金流调整净值", "八案例回撤"),
    )
    for case_id in CASE_SPECS:
        daily = pd.read_csv(block / "cases" / case_id / "daily.csv", parse_dates=["date"])
        index, drawdown = unitized(daily)
        meta = {"series_key": case_id.lower(), "label": case_id, "cost_bps": 0}
        figure.add_trace(go.Scatter(
            x=daily["date"], y=index, mode="lines", name=case_id,
            line={"color": COLORS[case_id], "width": 1.5}, meta={**meta, "panel": "equity"},
        ), row=1, col=1)
        figure.add_trace(go.Scatter(
            x=daily["date"], y=drawdown * 100, mode="lines", showlegend=False,
            line={"color": COLORS[case_id], "width": 1.0}, meta={**meta, "panel": "drawdown"},
        ), row=2, col=1)
    benchmark = pd.read_csv(
        block / "cases" / ANCHOR / "modified_buy_hold_daily.csv", parse_dates=["date"]
    )
    benchmark_index, benchmark_drawdown = unitized(benchmark)
    meta = {"series_key": "anchor_modified_bh", "label": "锚点同步注资Buy & Hold", "cost_bps": 0, "is_benchmark": True}
    figure.add_trace(go.Scatter(
        x=benchmark["date"], y=benchmark_index, mode="lines", name=meta["label"],
        line={"color": "#334155", "width": 1.8, "dash": "dash"}, meta={**meta, "panel": "equity"},
    ), row=1, col=1)
    figure.add_trace(go.Scatter(
        x=benchmark["date"], y=benchmark_drawdown * 100, mode="lines", showlegend=False,
        line={"color": "#334155", "width": 1.2, "dash": "dash"}, meta={**meta, "panel": "drawdown"},
    ), row=2, col=1)
    figure.update_layout(template="plotly_white", height=780, hovermode="x unified", dragmode="pan")
    figure.update_yaxes(title_text="起点=1", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return figure


def performance_figure(block: Path) -> go.Figure:
    case = block / "cases" / ANCHOR
    strategy = pd.read_csv(case / "daily.csv", parse_dates=["date"])
    modified = pd.read_csv(case / "modified_buy_hold_daily.csv", parse_dates=["date"])
    _, strategy_dd = unitized(strategy)
    _, modified_dd = unitized(modified)
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
        row_heights=[0.70, 0.30], subplot_titles=("锚点资金构成与同步注资基准", "现金流调整后的回撤"),
    )
    for values, label, color, key, benchmark, dash in (
        (strategy["equity"], "锚点总资产", "#2563eb", "anchor_equity", False, "solid"),
        (modified["equity"], "Modified Buy & Hold", "#334155", "modified_bh", True, "solid"),
        (strategy["shares"] * strategy["close"], "锚点QQQ持仓市值", "#16a34a", "anchor_position", False, "dash"),
        (strategy["cash"], "锚点现金", "#d97706", "anchor_cash", False, "dot"),
    ):
        figure.add_trace(go.Scatter(
            x=strategy["date"], y=values, mode="lines", name=label,
            line={"color": color, "width": 1.7, "dash": dash},
            meta={"series_key": key, "panel": "equity", "label": label, "is_benchmark": benchmark, "cost_bps": 0},
        ), row=1, col=1)
    for values, label, color, key in (
        (strategy_dd * 100, "锚点回撤", "#2563eb", "anchor_drawdown"),
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
    for factor in "DFS":
        rows[factor] = rows[factor].astype(bool)
    effects = factorial_effects(rows)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    rows.to_csv(analysis_root / "results_table.csv", index=False, lineterminator="\n")
    effects.to_csv(analysis_root / "factorial_effects.csv", index=False, lineterminator="\n")
    ranked = rows.sort_values(["cagr_gap_vs_matched_bh_pct_points", "sharpe"], ascending=False)
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "purpose": "predeclared sparse-entry 2x2x2 factorial over the full 2015-2026 exploratory window",
        "analysis_start": metrics["analysis_start"], "analysis_end": metrics["analysis_end"],
        "cases": metrics["cases"], "naive_buy_hold": metrics["naive_buy_hold"],
        "factorial_effects": effects.to_dict("records"),
        "ranking_by_cagr_gap_then_sharpe": ranked["case_id"].tolist(),
        "strict_boundary_equality_audit": metrics["strict_boundary_equality_audit"],
        "max_cross_check_difference": metrics["max_cross_check_difference"],
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# QQQ StochRSI低仓位恢复买入三因子消融", "",
        "- 窗口：2015-01-02～2026-08-04完整样本探索。", "- 成本：0 bps。",
        "- D/F/S三个开关形成八案，每案对照自己的同步注资Buy & Hold。", "",
        "| 案例 | D | F | S | CAGR | 同步B&H差 | Sharpe | 最大回撤 | 外部注资 | 平均QQQ仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows.itertuples():
        lines.append(
            f"| {row.case_id} | {int(row.D)} | {int(row.F)} | {int(row.S)} | {row.cagr_pct:.3f}% | "
            f"{row.cagr_gap_vs_matched_bh_pct_points:+.3f}pp | {row.sharpe:.3f} | "
            f"{row.max_drawdown_pct:.2f}% | ${row.external_contributions:,.2f} | {row.average_qqq_weight_pct:.2f}% |"
        )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = render_interactive_report(
        title="QQQ StochRSI低仓位恢复买入三因子消融",
        heading="QQQ 2015–2026 八个固定买入组合",
        subtitle="StochRSI 42/100 · 零成本 · 同Close成交 · 每案独立同步注资基准",
        summary_html=result_table(rows) + effect_table(effects),
        notes=[
            "D表示把原有B1/B2金额累计到StochRSI100严格上穿0.20再释放；关闭D则沿用即时低位买入。",
            "F只在仓位低于15%、长周期高于0.20且短周期先下穿再上穿0.20时买40%现金；S在低仓位长周期上穿0.20时买50%现金。",
            "同日先执行累计金额，S压过F；新增两条恢复买入只使用现金，不追加本金。",
            "完整样本结果仅用于研究交互，不是样本外证据；快速下跌理论价仍不检查OHLC触达。",
            "市场与资金构成图固定展示D0_F0_S0锚点，八案例正式净值在对比图中全部保留。",
        ],
        figures=[
            ReportFigure("market-qqq", "锚点：市场、指标、虚拟池与资金构成", market_figure(block), "market"),
            ReportFigure("factorial-qqq", "八个买入组合对比", comparison_figure(block), "performance"),
            ReportFigure("performance-qqq", "锚点收益、持仓资金与现金", performance_figure(block), "performance"),
        ],
        experiment=context.config, run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/experiment.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/stochrsi_scaled_pools.py",
        "backtest/quantkit/metrics.py", "backtest/quantkit/reporting.py",
        "backtest/scripts/run_stochrsi_sparse_entry_factorial.py",
        "backtest/scripts/analyze_stochrsi_sparse_entry_factorial.py",
        "backtest/scripts/finalize_stochrsi_sparse_entry_factorial.py",
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
        f"# Run {args.run_id}\n\nFrozen QQQ sparse-entry factorial; print report.pdf, then run the matching finalizer.\n",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'}; PDF printing and finalization remain pending")


if __name__ == "__main__":
    main()
