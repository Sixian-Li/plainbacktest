#!/usr/bin/env python3
"""Analyze and report the QQQ single-StochRSI five-year rolling drift grid."""

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


def best_by_window(results: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    rows = []
    benchmarks = benchmark.set_index("window_id")
    for window_id, group in results.groupby("window_id", sort=False):
        ordered_cagr = group.sort_values(["cagr_pct", "period"], ascending=[False, True])
        ordered_sharpe = group.sort_values(["sharpe", "period"], ascending=[False, True])
        cagr, sharpe = ordered_cagr.iloc[0], ordered_sharpe.iloc[0]
        bh = benchmarks.loc[window_id]
        rows.append({
            "window_id": window_id,
            "window_label": cagr["window_label"],
            "window_start": cagr["window_start"],
            "window_end": cagr["window_end"],
            "start_year": int(cagr["start_year"]),
            "best_cagr_period": int(cagr["period"]),
            "best_cagr_pct": float(cagr["cagr_pct"]),
            "best_cagr_total_return_pct": float(cagr["total_return_pct"]),
            "best_cagr_sharpe": float(cagr["sharpe"]),
            "best_sharpe_period": int(sharpe["period"]),
            "best_sharpe": float(sharpe["sharpe"]),
            "best_sharpe_cagr_pct": float(sharpe["cagr_pct"]),
            "top3_cagr_period_median": float(ordered_cagr.head(3)["period"].median()),
            "top3_sharpe_period_median": float(ordered_sharpe.head(3)["period"].median()),
            "buy_hold_cagr_pct": float(bh["cagr_pct"]),
            "buy_hold_sharpe": float(bh["sharpe"]),
        })
    return pd.DataFrame(rows).sort_values("start_year").reset_index(drop=True)


def drift_statistics(best: pd.DataFrame, column: str, label: str) -> dict[str, object]:
    x = best["start_year"].to_numpy(float)
    y = best[column].to_numpy(float)
    differences = np.diff(y)
    signs = np.sign(differences)
    nonzero = signs[signs != 0]
    direction_changes = int(np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) > 1 else 0
    return {
        "metric": label,
        "period_column": column,
        "spearman_start_year_vs_period": float(pd.Series(x).rank().corr(pd.Series(y).rank())),
        "linear_slope_periods_per_start_year": float(np.polyfit(x, y, 1)[0]),
        "median_absolute_one_year_jump": float(np.median(np.abs(differences))),
        "maximum_absolute_one_year_jump": float(np.max(np.abs(differences))),
        "up_steps": int(np.sum(differences > 0)),
        "down_steps": int(np.sum(differences < 0)),
        "flat_steps": int(np.sum(differences == 0)),
        "direction_changes": direction_changes,
        "monotonic_non_decreasing": bool(np.all(differences >= 0)),
        "monotonic_non_increasing": bool(np.all(differences <= 0)),
        "sequence": [int(value) for value in y],
    }


def response_figure(results: pd.DataFrame, metric: str, y_title: str) -> go.Figure:
    fig = go.Figure()
    first = results[results["window_id"].eq(results.iloc[0]["window_id"])].sort_values("period")
    fig.add_trace(go.Scatter(
        x=first["period"], y=first[metric], mode="lines", line={"color": "rgba(0,0,0,0)"},
        hoverinfo="skip", showlegend=False, meta={"panel": "price", "label": "纵轴自动范围锚点"},
    ))
    palette = ["#0f766e", "#2563eb", "#7c3aed", "#be123c", "#c2410c", "#a16207", "#4d7c0f", "#0e7490", "#475569", "#9333ea", "#dc2626"]
    for color, (window_id, group) in zip(palette, results.groupby("window_id", sort=False)):
        group = group.sort_values("period")
        best_index = group[metric].idxmax()
        label = str(group.iloc[0]["window_label"])
        custom = np.column_stack([group["cagr_pct"], group["total_return_pct"], group["sharpe"], group["exposure_pct"]])
        fig.add_trace(go.Scatter(
            x=group["period"], y=group[metric], mode="lines+markers", name=label,
            line={"color": color, "width": 1.7}, marker={"size": [9 if index == best_index else 4 for index in group.index]},
            customdata=custom,
            hovertemplate="窗口 " + label + "<br>周期 %{x}<br>CAGR %{customdata[0]:.2f}%<br>累计收益 %{customdata[1]:.2f}%<br>Sharpe %{customdata[2]:.3f}<br>在场率 %{customdata[3]:.1f}%<extra></extra>",
            meta={
                "series_key": window_id.lower(), "panel": "market", "label": label,
                "control_group": "rolling_windows", "control_group_label": "显示五年滚动窗口",
            },
        ))
    fig.update_layout(
        template="plotly_white", height=650, hovermode="closest", dragmode="pan",
        xaxis={"title": "单一 StochRSI 周期", "tickmode": "linear", "dtick": 14, "range": [12, 212]},
        yaxis={"title": y_title}, legend={"orientation": "h", "y": -0.18}, margin={"b": 130},
    )
    return fig


def drift_figure(best: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=best["window_label"], y=best["best_cagr_period"], mode="lines+markers", name="CAGR机械最佳",
        line={"color": "#2563eb", "width": 2}, marker={"size": 9},
    ))
    fig.add_trace(go.Scatter(
        x=best["window_label"], y=best["best_sharpe_period"], mode="lines+markers", name="Sharpe机械最佳",
        line={"color": "#dc2626", "width": 2}, marker={"size": 9},
    ))
    fig.add_trace(go.Scatter(
        x=best["window_label"], y=best["top3_cagr_period_median"], mode="lines", name="CAGR前三周期中位",
        visible="legendonly", line={"color": "#60a5fa", "width": 1.5, "dash": "dot"},
    ))
    fig.add_trace(go.Scatter(
        x=best["window_label"], y=best["top3_sharpe_period_median"], mode="lines", name="Sharpe前三周期中位",
        visible="legendonly", line={"color": "#f87171", "width": 1.5, "dash": "dot"},
    ))
    fig.update_layout(template="plotly_white", height=560, xaxis_title="五年窗口（右端年份不含）", yaxis_title="最佳 StochRSI 周期", hovermode="x unified")
    return fig


def _npz_series(state: np.lib.npyio.NpzFile, case_id: str) -> tuple[np.ndarray, np.ndarray]:
    index = int(np.where(state["case_ids"] == case_id)[0][0])
    start, end = int(state["offsets"][index]), int(state["offsets"][index + 1])
    return state["dates"][start:end], state["actual_equity"][start:end]


def performance_figure(block: Path, best: pd.DataFrame, representative_window: str) -> go.Figure:
    selected = best[best["window_id"].eq(representative_window)].iloc[0]
    state = np.load(block / "daily_signal_state.npz")
    benchmark = pd.read_csv(block / "buy_hold_daily.csv")
    benchmark = benchmark[benchmark["window_id"].eq(representative_window)]
    series = [("buy_hold", "QQQ Buy & Hold", benchmark["date"].to_numpy(), benchmark["equity"].to_numpy(float), True)]
    for key, label, period in (
        ("best_cagr", f"CAGR最佳 · {int(selected['best_cagr_period'])}", int(selected["best_cagr_period"])),
        ("best_sharpe", f"Sharpe最佳 · {int(selected['best_sharpe_period'])}", int(selected["best_sharpe_period"])),
    ):
        case_id = f"S{period:03d}__{representative_window}"
        dates, equity = _npz_series(state, case_id)
        if any(item[0] == key for item in series) or (period == int(selected["best_cagr_period"]) and key == "best_sharpe"):
            continue
        series.append((key, label, dates, equity, False))
    colors = ["#334155", "#2563eb", "#dc2626"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.7, .3], vertical_spacing=.07, subplot_titles=("代表窗口净值", "回撤"))
    for color, (key, label, dates, equity, benchmark_flag) in zip(colors, series):
        values = np.asarray(equity, dtype=float)
        drawdown = values / np.maximum.accumulate(values) - 1.0
        meta = {"series_key": key, "panel": "equity", "label": label, "is_benchmark": benchmark_flag, "cost_bps": 5}
        fig.add_trace(go.Scatter(x=dates, y=values, mode="lines", name=label, line={"color": color, "width": 1.8}, meta=meta), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=drawdown * 100, mode="lines", showlegend=False, line={"color": color, "width": 1.1}, meta={"series_key": key, "panel": "drawdown", "label": label}), row=2, col=1)
    fig.update_layout(template="plotly_white", height=700, hovermode="x unified", dragmode="pan")
    fig.update_yaxes(title_text="美元", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)
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
    benchmark = pd.read_csv(block / "buy_hold_results.csv")
    best = best_by_window(results, benchmark)
    stats = pd.DataFrame([
        drift_statistics(best, "best_cagr_period", "CAGR机械最佳"),
        drift_statistics(best, "best_sharpe_period", "Sharpe机械最佳"),
        drift_statistics(best, "top3_cagr_period_median", "CAGR前三周期中位"),
        drift_statistics(best, "top3_sharpe_period_median", "Sharpe前三周期中位"),
    ])
    analysis = run_root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    normalize_frame(results).to_csv(analysis / "formal_results.csv", index=False, lineterminator="\n")
    normalize_frame(best).to_csv(analysis / "best_by_window.csv", index=False, lineterminator="\n")
    normalize_frame(stats).to_csv(analysis / "drift_statistics.csv", index=False, lineterminator="\n")

    rows = "".join(
        f"<tr><td>{html.escape(str(row.window_label))}</td><td>{html.escape(str(row.window_start))}—{html.escape(str(row.window_end))}</td>"
        f"<td>{row.best_cagr_period}</td><td>{row.best_cagr_pct:.2f}%</td><td>{row.best_sharpe_period}</td><td>{row.best_sharpe:.3f}</td>"
        f"<td>{row.buy_hold_cagr_pct:.2f}%</td><td>{row.buy_hold_sharpe:.3f}</td></tr>"
        for row in best.itertuples()
    )
    cagr_stats, sharpe_stats = stats.iloc[0], stats.iloc[1]
    summary_html = (
        "<h2>机械最佳周期如何移动</h2>"
        f"<p><strong>CAGR最佳周期序列：</strong>{html.escape(' → '.join(map(str, cagr_stats['sequence'])))}；"
        f"起始年份与周期Spearman相关为 {cagr_stats['spearman_start_year_vs_period']:.3f}，方向反转 {int(cagr_stats['direction_changes'])} 次。</p>"
        f"<p><strong>Sharpe最佳周期序列：</strong>{html.escape(' → '.join(map(str, sharpe_stats['sequence'])))}；"
        f"起始年份与周期Spearman相关为 {sharpe_stats['spearman_start_year_vs_period']:.3f}，方向反转 {int(sharpe_stats['direction_changes'])} 次。</p>"
        "<p>相邻窗口共享约80%的交易日，因此曲线平滑或最佳点连续只能作为描述性线索，不能单独证明参数存在可预测漂移。</p>"
        "<h2>逐窗口机械最高点</h2><table><thead><tr><th>窗口</th><th>实际日期</th><th>CAGR最佳周期</th><th>最佳CAGR</th>"
        "<th>Sharpe最佳周期</th><th>最佳Sharpe</th><th>持有CAGR</th><th>持有Sharpe</th></tr></thead><tbody>" + rows + "</tbody></table>"
    )
    figures = [
        ReportFigure("market-qqq", "累计收益对周期的响应 · 11窗口可勾选", response_figure(results, "total_return_pct", "五年累计收益 (%)"), "market"),
        ReportFigure("cagr-response", "CAGR对周期的响应 · 11窗口可勾选", response_figure(results, "cagr_pct", "CAGR (%)"), "market"),
        ReportFigure("sharpe-response", "Sharpe对周期的响应 · 11窗口可勾选", response_figure(results, "sharpe", "Sharpe"), "market"),
        ReportFigure("best-period-drift", "机械最佳周期的逐年漂移", drift_figure(best), "analysis"),
        ReportFigure("performance-qqq", "2010–2015代表窗口：最佳参数与持有", performance_figure(block, best, "W2010_2015"), "performance"),
    ]
    report = render_interactive_report(
        title="QQQ 单周期 Stochastic RSI 五年滚动漂移诊断",
        heading="11个左侧逐年平移窗口 · 29个周期 · 319条双账本路径",
        subtitle="窗口右端年份不含 · 周期14–210 / 步长7 · 买0.20 / 卖0.80 · 5 bps",
        summary_html=summary_html,
        notes=[
            "每条曲线都可在图上方单独勾选；大圆点是该窗口在当前纵轴指标下的机械最高点。",
            "2005–2010表示2005年第一个交易日至2010年第一个交易日前一日，其他窗口同理。",
            "本实验诊断参数漂移，不从11个窗口中挑选可部署冠军，也不把重叠窗口当作独立样本。",
        ],
        figures=figures, experiment=context.config, run_id=args.run_id, template_id="interactive_research_v5",
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report_print.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        "# QQQ单周期StochRSI五年滚动漂移诊断\n\n"
        f"- CAGR最佳周期：{' → '.join(map(str, cagr_stats['sequence']))}。\n"
        f"- Sharpe最佳周期：{' → '.join(map(str, sharpe_stats['sequence']))}。\n"
        f"- CAGR/Sharpe起始年Spearman相关：{cagr_stats['spearman_start_year_vs_period']:.3f} / {sharpe_stats['spearman_start_year_vs_period']:.3f}。\n"
        "- 相邻窗口共享约80%数据，结果仅用于诊断有序漂移。\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "case_count": int(len(results)), "window_count": int(best.shape[0]),
        "best_by_window": best.to_dict("records"),
        "drift_statistics": [json_safe(row) for row in stats.to_dict("records")],
    }
    (analysis / "summary.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"], "run_id": args.run_id,
        "software": {"python": platform.python_version(), "plotly": plotly.__version__}, "source_files": {},
    }
    for relative in (
        "backtest/scripts/run_single_stochrsi_rolling_drift.py",
        "backtest/scripts/analyze_single_stochrsi_rolling_drift.py",
        "backtest/quantkit/dual_stochrsi_timing.py",
        "backtest/quantkit/reporting.py",
        "data/processed/daily/QQQ.csv",
    ):
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    (run_root / "README.md").write_text("# Rolling StochRSI drift run\n\nSee report.html, report.pdf, and analysis outputs.\n", encoding="utf-8")
    subprocess.run([
        "node", "scripts/print_html_pdf.mjs", str(run_root / "report_print.html"), str(run_root / "report.pdf"),
        "什么时候买", "什么时候卖", "信号如何变成成交", "机械最佳周期",
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
