#!/usr/bin/env python3
"""Build the formal report for annual dynamic single-StochRSI versus fixed baselines."""

from __future__ import annotations

import argparse
import html
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_intraday_sma_backtest import json_safe, normalize_frame


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
LABELS = {
    "DYNAMIC_CAGR": "年度CAGR冠军换参",
    "FIXED_014": "固定周期14",
    "FIXED_140": "固定周期140",
    "BUY_HOLD": "QQQ Buy & Hold",
}
COLORS = {
    "DYNAMIC_CAGR": "#2563eb",
    "FIXED_014": "#16a085",
    "FIXED_140": "#d97706",
    "BUY_HOLD": "#334155",
}


def market_figure(daily: pd.DataFrame, raw: pd.DataFrame) -> go.Figure:
    dynamic = daily[daily["case_id"].eq("DYNAMIC_CAGR")].copy()
    market = raw[raw["date"].between(dynamic["date"].min(), dynamic["date"].max())]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=market["date"], y=market["close"], mode="lines", name="QQQ Close",
        line={"color": "#111827", "width": 1.5},
        meta={"series_key": "qqq_close", "panel": "price", "label": "QQQ Close"},
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=dynamic["date"], y=dynamic["selected_period"], mode="lines", name="当年StochRSI周期",
        line={"color": "#dc2626", "width": 1.5, "shape": "hv"},
        meta={"series_key": "annual_period", "panel": "market", "label": "当年StochRSI周期"},
    ), secondary_y=True)
    fig.update_layout(template="plotly_white", height=580, hovermode="x unified", dragmode="pan")
    fig.update_yaxes(title_text="QQQ adjusted Close", secondary_y=False)
    fig.update_yaxes(title_text="StochRSI周期", secondary_y=True)
    return fig


def performance_figure(daily: pd.DataFrame, buy_hold: pd.DataFrame) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.7, .3], vertical_spacing=.07, subplot_titles=("账户净值", "回撤"))
    series = [("BUY_HOLD", buy_hold)] + [(case_id, group) for case_id, group in daily.groupby("case_id", sort=False)]
    for case_id, frame in series:
        values = frame["equity"].to_numpy(float)
        drawdown = values / np.maximum.accumulate(values) - 1.0
        meta = {
            "series_key": case_id.lower(), "panel": "equity", "label": LABELS[case_id],
            "is_benchmark": case_id == "BUY_HOLD", "cost_bps": 5,
        }
        fig.add_trace(go.Scatter(
            x=frame["date"], y=values, mode="lines", name=LABELS[case_id],
            line={"color": COLORS[case_id], "width": 2 if case_id == "DYNAMIC_CAGR" else 1.6}, meta=meta,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=frame["date"], y=drawdown * 100, mode="lines", showlegend=False,
            line={"color": COLORS[case_id], "width": 1.2},
            meta={"series_key": case_id.lower(), "panel": "drawdown", "label": LABELS[case_id]},
        ), row=2, col=1)
    fig.update_layout(template="plotly_white", height=760, hovermode="x unified", dragmode="pan")
    fig.update_yaxes(title_text="美元", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)
    return fig


def annual_figure(annual: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for case_id in ("BUY_HOLD", "DYNAMIC_CAGR", "FIXED_014", "FIXED_140"):
        group = annual[annual["case_id"].eq(case_id)]
        fig.add_trace(go.Bar(
            x=group["year"], y=group["return_pct"], name=LABELS[case_id],
            marker_color=COLORS[case_id],
            hovertemplate=f"{LABELS[case_id]}<br>%{{x}}年 %{{y:.2f}}%<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="#64748b", line_width=1)
    fig.update_layout(template="plotly_white", height=600, barmode="group", xaxis_title="应用年份", yaxis_title="账户年度收益 (%)", hovermode="x unified")
    return fig


def schedule_figure(schedule: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=schedule["application_year"], y=schedule["period"], mode="lines+markers+text",
        text=schedule["period"], textposition="top center", line={"color": "#2563eb", "width": 2, "shape": "hv"},
        marker={"size": 9}, name="冻结周期",
        customdata=np.column_stack([schedule["source_label"], schedule["source_end"]]),
        hovertemplate="应用%{x}年：周期%{y}<br>来源窗口 %{customdata[0]}<br>训练截止 %{customdata[1]}<extra></extra>",
    ))
    fig.update_layout(template="plotly_white", height=500, xaxis={"title": "应用年份", "dtick": 1}, yaxis_title="StochRSI周期")
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if any(block["status"] != "completed" for block in record["expected_blocks"]):
        raise RuntimeError("Run blocks are incomplete")
    run_root = context.run_root(args.run_id)
    block = run_root / "QQQ/cost_5bps"
    results = pd.read_csv(block / "parameter_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    buy_hold = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    annual = pd.read_csv(block / "calendar_year_returns.csv")
    schedule = pd.read_csv(block / "period_schedule.csv")
    block_metrics = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    benchmark = block_metrics["benchmark"]
    analysis = run_root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    metrics = {row.case_id: row._asdict() for row in results.itertuples(index=False)}
    metrics["BUY_HOLD"] = {"case_id": "BUY_HOLD", "label": LABELS["BUY_HOLD"], **benchmark}
    dynamic, fixed14, fixed140 = metrics["DYNAMIC_CAGR"], metrics["FIXED_014"], metrics["FIXED_140"]
    comparisons = {
        "dynamic_cagr_gap_vs_buy_hold_pct_points": float(dynamic["cagr_pct"] - benchmark["cagr_pct"]),
        "dynamic_cagr_gap_vs_fixed14_pct_points": float(dynamic["cagr_pct"] - fixed14["cagr_pct"]),
        "dynamic_cagr_gap_vs_fixed140_pct_points": float(dynamic["cagr_pct"] - fixed140["cagr_pct"]),
        "dynamic_sharpe_gap_vs_buy_hold": float(dynamic["sharpe"] - benchmark["sharpe"]),
        "dynamic_drawdown_improvement_vs_buy_hold_pct_points": float(dynamic["max_drawdown_pct"] - benchmark["max_drawdown_pct"]),
    }
    rejection = {
        "beats_buy_hold_cagr": comparisons["dynamic_cagr_gap_vs_buy_hold_pct_points"] > 0,
        "beats_fixed14_cagr": comparisons["dynamic_cagr_gap_vs_fixed14_pct_points"] > 0,
        "beats_fixed140_cagr": comparisons["dynamic_cagr_gap_vs_fixed140_pct_points"] > 0,
        "does_not_worsen_sharpe_vs_buy_hold": comparisons["dynamic_sharpe_gap_vs_buy_hold"] >= 0,
        "does_not_worsen_drawdown_vs_buy_hold": comparisons["dynamic_drawdown_improvement_vs_buy_hold_pct_points"] >= 0,
    }
    rejection["passes_predeclared_promotion_screen"] = all(rejection.values())
    annual_pivot = annual.pivot(index="year", columns="case_id", values="return_pct")
    annual_comparison = pd.DataFrame({
        "dynamic_return_pct": annual_pivot["DYNAMIC_CAGR"],
        "fixed14_return_pct": annual_pivot["FIXED_014"],
        "fixed140_return_pct": annual_pivot["FIXED_140"],
        "buy_hold_return_pct": annual_pivot["BUY_HOLD"],
    }, index=annual_pivot.index).rename_axis("year").reset_index()
    annual_comparison["dynamic_minus_buy_hold_pct_points"] = annual_comparison["dynamic_return_pct"] - annual_comparison["buy_hold_return_pct"]
    annual_comparison["dynamic_minus_fixed14_pct_points"] = annual_comparison["dynamic_return_pct"] - annual_comparison["fixed14_return_pct"]
    largest_fixed14_drag = annual_comparison.loc[annual_comparison["dynamic_minus_fixed14_pct_points"].idxmin()]
    material_fixed14_wins = int((annual_comparison["dynamic_minus_fixed14_pct_points"] > 1e-9).sum())
    normalize_frame(annual_comparison).to_csv(analysis / "annual_comparison.csv", index=False, lineterminator="\n")

    metric_rows = "".join(
        f"<tr><td>{html.escape(LABELS[case_id])}</td><td>{float(row['total_return_pct']):.2f}%</td><td>{float(row['cagr_pct']):.2f}%</td>"
        f"<td>{float(row['sharpe']):.3f}</td><td>{float(row['max_drawdown_pct']):.2f}%</td><td>{float(row.get('exposure_pct', 100.0)):.1f}%</td><td>${float(row['final_equity']):,.0f}</td></tr>"
        for case_id, row in (("DYNAMIC_CAGR", dynamic), ("FIXED_014", fixed14), ("FIXED_140", fixed140), ("BUY_HOLD", benchmark))
    )
    annual_lookup = annual[annual["case_id"].eq("DYNAMIC_CAGR")].set_index("year")["return_pct"]
    schedule_rows = "".join(
        f"<tr><td>{int(row.application_year)}</td><td>{html.escape(str(row.source_label))}</td><td>{html.escape(str(row.source_end))}</td>"
        f"<td>{int(row.period)}</td><td>{float(annual_lookup.loc[int(row.application_year)]):.2f}%</td></tr>"
        for row in schedule.itertuples()
    )
    verdict = "通过预声明比较屏" if rejection["passes_predeclared_promotion_screen"] else "未通过预声明比较屏"
    summary_html = (
        f"<h2>2011–2021结果</h2><p><strong>动态年度换参{verdict}。</strong>"
        f"相对Buy & Hold的CAGR差为 {comparisons['dynamic_cagr_gap_vs_buy_hold_pct_points']:+.2f} 个百分点，"
        f"相对固定14为 {comparisons['dynamic_cagr_gap_vs_fixed14_pct_points']:+.2f} 个百分点，"
        f"相对固定140为 {comparisons['dynamic_cagr_gap_vs_fixed140_pct_points']:+.2f} 个百分点。</p>"
        f"<p>相对固定14，年度换参只有 {material_fixed14_wins}/11 个年份产生实质正贡献；"
        f"最大拖累来自 {int(largest_fixed14_drag['year'])} 年，少赚 {abs(float(largest_fixed14_drag['dynamic_minus_fixed14_pct_points'])):.2f} 个百分点。</p>"
        "<table><thead><tr><th>路径</th><th>累计收益</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>在场率</th><th>期末资金</th></tr></thead><tbody>"
        + metric_rows + "</tbody></table>"
        "<h2>冻结年度周期</h2><table><thead><tr><th>应用年</th><th>来源窗口</th><th>训练截止</th><th>周期</th><th>动态路径当年收益</th></tr></thead><tbody>"
        + schedule_rows + "</tbody></table>"
    )
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    raw = raw[raw["symbol"].eq("QQQ")].sort_values("date")
    report = render_interactive_report(
        title="QQQ Stochastic RSI 年度CAGR冠军换参 · 2011–2021",
        heading="11年连续账户 · 年度冻结周期 · 固定14/140与持有基线",
        subtitle="父窗口至少留出一个完整日历年 · 买0.20 / 卖0.80 · 5 bps",
        summary_html=summary_html,
        notes=[
            "周期表在本次run开始前由父实验validated证据冻结，2011–2021收益不参与年度周期选择。",
            "年度边界延续原仓位；新周期以自己的前一日指标状态判断穿越，不把参数变化本身当成交易信号。",
            "这是提出年度换参想法后的历史walk-forward诊断，不是尚未被观察过的真正前瞻样本。",
        ],
        figures=[
            ReportFigure("market-qqq", "QQQ Close与年度周期", market_figure(daily, raw), "market"),
            ReportFigure("performance-qqq", "动态、固定周期与Buy & Hold净值", performance_figure(daily, buy_hold), "performance"),
            ReportFigure("annual-returns", "逐年账户收益", annual_figure(annual), "analysis"),
            ReportFigure("period-schedule", "冻结周期时间表", schedule_figure(schedule), "analysis"),
        ],
        experiment=context.config, run_id=args.run_id, template_id="interactive_research_v5",
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        "# QQQ StochRSI年度CAGR冠军换参\n\n"
        f"- 动态CAGR：{dynamic['cagr_pct']:.3f}%；Buy & Hold：{benchmark['cagr_pct']:.3f}%。\n"
        f"- 固定14/140 CAGR：{fixed14['cagr_pct']:.3f}% / {fixed140['cagr_pct']:.3f}%。\n"
        f"- 动态Sharpe/最大回撤：{dynamic['sharpe']:.3f} / {dynamic['max_drawdown_pct']:.2f}%。\n"
        f"- 结论：{verdict}。\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "primary_metrics": {case_id: json_safe(row) for case_id, row in metrics.items()},
        "comparisons": comparisons, "promotion_screen": rejection,
        "annual_win_counts": {
            "dynamic_vs_buy_hold": int((annual_comparison["dynamic_minus_buy_hold_pct_points"] > 1e-9).sum()),
            "dynamic_vs_fixed14": material_fixed14_wins,
        },
        "largest_dynamic_shortfall_vs_fixed14": {
            "year": int(largest_fixed14_drag["year"]),
            "percentage_points": float(largest_fixed14_drag["dynamic_minus_fixed14_pct_points"]),
        },
    }
    (analysis / "summary.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"], "run_id": args.run_id,
        "software": {"python": platform.python_version(), "plotly": plotly.__version__},
        "selection_source": {
            "parent_experiment_id": context.config["parameters"]["parent_experiment_id"],
            "parent_run_id": context.config["parameters"]["parent_run_id"],
            "artifact": context.config["parameters"]["parent_selection_artifact"],
            "sha256": context.config["parameters"]["parent_selection_artifact_sha256"],
        },
        "source_files": {},
    }
    for relative in (
        "backtest/scripts/run_single_stochrsi_annual_dynamic_oos.py",
        "backtest/scripts/analyze_single_stochrsi_annual_dynamic_oos.py",
        "backtest/quantkit/dual_stochrsi_timing.py", "backtest/quantkit/reporting.py",
        "data/processed/daily/QQQ.csv",
    ):
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    (run_root / "README.md").write_text("# Annual dynamic StochRSI run\n\nSee report.html, report.pdf, and analysis outputs.\n", encoding="utf-8")
    subprocess.run([
        "node", "scripts/print_html_pdf.mjs", str(run_root / "report_print.html"), str(run_root / "report.pdf"),
        "什么时候买", "什么时候卖", "信号如何变成成交", "2011–2021结果",
    ], cwd=BACKTEST_ROOT, check=True)
    artifacts = {"schema_version": 1, "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            artifacts["artifacts"][str(path.relative_to(run_root))] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(json.dumps(artifacts, indent=2) + "\n", encoding="utf-8")
    if record.get("status") == "running":
        record_analysis_complete(context, args.run_id)
    print(run_root / "report.html")


if __name__ == "__main__":
    main()
