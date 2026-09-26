#!/usr/bin/env python3
"""Build the four-surface QQQ StochRSI supergrid report."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, run_reference
from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_stochrsi_supergrid_heatmaps import aligned_buy_hold

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def market_figure(raw: pd.DataFrame) -> go.Figure:
    data = raw[raw["date"].between("2000-01-03", "2015-12-31")].copy()
    data["sma_200"] = data["close"].rolling(200, min_periods=200).mean()
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=data["date"], y=data["close"], mode="lines", name="QQQ Close", line={"color":"#111827","width":1.4}, meta={"series_key":"qqq_close","panel":"price","label":"QQQ Close"}))
    figure.add_trace(go.Scatter(x=data["date"], y=data["sma_200"], mode="lines", name="SMA 200", line={"color":"#d97706","width":1.1}, meta={"series_key":"sma_200","panel":"market","label":"SMA 200"}))
    figure.update_layout(template="plotly_white", height=540, hovermode="x unified", dragmode="pan", yaxis_title="QQQ adjusted Close")
    return figure


def performance_figure(raw: pd.DataFrame, case_ids: list[str]) -> go.Figure:
    start, end = pd.Timestamp("2005-01-03"), pd.Timestamp("2009-12-31")
    series, selected_reference = [], None
    for current_id in case_ids:
        short, long = map(int, current_id.removeprefix("P").split("_"))
        spec = TimingSpec("CROSS", cost_bps=5, periods=(short,long), buy_threshold=.2, sell_threshold=.8)
        reference = run_reference(prepare_dual_stochrsi_data(raw, spec), spec, analysis_start=start, analysis_end=end, initial_cash=100000)
        series.append((current_id.lower(), f"{short}/{long}", reference.daily["date"], reference.daily["equity"], False))
        selected_reference = reference
    benchmark, _, _ = aligned_buy_hold(selected_reference.daily, selected_reference.orders, initial_cash=100000)
    series.insert(0, ("aligned_buy_hold", "候选首次入场对齐持有", benchmark["date"], benchmark["equity"], True))
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.7,.3], vertical_spacing=.07, subplot_titles=("代表性五年窗口净值", "回撤"))
    colors = ["#334155", "#16a085", "#2563eb", "#d97706", "#7c3aed"]
    for color, (key, name, dates, equity, is_benchmark) in zip(colors, series):
        values = pd.Series(equity).to_numpy(float); drawdown = values / pd.Series(values).cummax().to_numpy() - 1
        figure.add_trace(go.Scatter(x=dates,y=values,mode="lines",name=name,line={"color":color,"width":1.8},meta={"series_key":key,"panel":"equity","label":name,"is_benchmark":is_benchmark,"cost_bps":5}), row=1,col=1)
        figure.add_trace(go.Scatter(x=dates,y=drawdown*100,mode="lines",showlegend=False,line={"color":color,"width":1.1},meta={"series_key":key,"panel":"drawdown","label":name}), row=2,col=1)
    figure.update_layout(template="plotly_white",height=700,hovermode="x unified",dragmode="pan")
    return figure


def heatmap_figure(surface: pd.DataFrame, column: str, colorbar: str, colorscale: str, *, zmin=None, zmax=None) -> go.Figure:
    pivot = surface.pivot(index="short_period", columns="long_period", values=column)
    custom = surface.pivot(index="short_period", columns="long_period", values="case_id")
    figure = go.Figure(go.Heatmap(
        x=pivot.columns, y=pivot.index, z=pivot.values, customdata=custom.values,
        colorscale=colorscale, zmin=zmin, zmax=zmax, colorbar={"title":colorbar},
        hovertemplate="%{customdata}<br>短周期 %{y}<br>长周期 %{x}<br>数值 %{z:.4f}<extra></extra>",
        hoverongaps=False,
    ))
    figure.add_trace(go.Scatter(x=[50,70], y=[50,70], mode="lines", line={"color":"rgba(255,255,255,.65)","dash":"dot","width":1}, name="短周期=长周期边界", hoverinfo="skip"))
    figure.update_layout(template="plotly_white", height=650, xaxis_title="长周期", yaxis_title="短周期", dragmode="pan")
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--experiment", type=Path, required=True); parser.add_argument("--run-id", required=True); args = parser.parse_args()
    context = load_experiment(args.experiment); record = load_run(context, args.run_id)
    if any(block["status"] != "completed" for block in record["expected_blocks"]): raise RuntimeError("Incomplete blocks")
    run_root = context.run_root(args.run_id); analysis_root = run_root / "analysis"; analysis_root.mkdir(parents=True, exist_ok=True)
    block = run_root / "QQQ/cost_5bps"; formal = pd.read_csv(block / "formal_results.csv"); formal.insert(0, "cost_bps", 5.0); formal.to_csv(analysis_root / "formal_results.csv", index=False, lineterminator="\n")
    aggregate = formal.groupby("case_id", as_index=False).agg(window_count=("window_id","count"),median_cagr_pct=("cagr_pct","median"),q25_cagr_pct=("cagr_pct",lambda x:x.quantile(.25)),median_sharpe=("sharpe","median"),q25_sharpe=("sharpe",lambda x:x.quantile(.25)),median_max_drawdown_pct=("max_drawdown_pct","median")); aggregate.to_csv(analysis_root / "aggregate_results.csv", index=False, lineterminator="\n")
    selection = json.loads((block / "selection.json").read_text()); surface = pd.read_csv(block / "surface.csv"); selected = selection["robust_champion"] or selection["strongest_near_miss"]; passed = selection["robust_champion"] is not None
    status = f"{selected['short_period']:.0f}/{selected['long_period']:.0f}通过全部七道门禁" if passed else f"没有参数通过全部七道门禁；{selected['short_period']:.0f}/{selected['long_period']:.0f}是最强近似候选"
    rows = "".join(f"<tr><td>{html.escape(row.case_id)}</td><td>{row.median_cagr_pct:.3f}%</td><td>{row.q25_cagr_pct:.3f}%</td><td>{row.median_sharpe:.3f}</td><td>{row.q25_sharpe:.3f}</td><td>{row.median_max_drawdown_pct:.2f}%</td></tr>" for row in aggregate.itertuples())
    summary_html = f"<h2>超大网格结论</h2><p><strong>{status}；通过数为{selection['passing_case_count']}/6738。</strong></p><p>period=0没有定义、period=1会退化为零宽随机指标窗口，因此实际有效短周期为2–70；灰白空区表示短周期不小于长周期，未参与计算。</p><table><thead><tr><th>复核参数</th><th>中位CAGR</th><th>下四分位CAGR</th><th>中位Sharpe</th><th>下四分位Sharpe</th><th>中位回撤</th></tr></thead><tbody>{rows}</tbody></table>"
    raw = pd.read_csv(WORKSPACE / "data/processed/daily/QQQ.csv", parse_dates=["date"]); raw = raw[raw["symbol"].eq("QQQ")].sort_values("date")
    ids = list(dict.fromkeys(["P017_097","P030_143","P042_100",str(selected["case_id"])]))
    report = render_interactive_report(
        title="QQQ双周期Stochastic RSI 2000–2015超大参数网格",
        heading="短周期2–70 × 长周期50–150 · short<long · 6,738组",
        subtitle="132个3/5/7年季度重启窗口 · 5 bps · 四张完整参数面",
        summary_html=summary_html,
        notes=["用户请求的短周期0无定义，period=1会退化为零宽随机指标窗口；二者均排除，未用伪值替代。","所有候选均只读取2000–2015窗口；没有使用2016年以后数据。","稳健分为四类稳定性得分的最弱值；边界缺少完整3×3邻域时显示为空。"],
        figures=[
            ReportFigure("market-qqq","QQQ Close · 2000–2015",market_figure(raw),"market"),
            ReportFigure("performance-qqq","代表窗口净值与回撤",performance_figure(raw,ids),"performance"),
            ReportFigure("heatmap-robust","Heatmap 1 · maximin稳健得分",heatmap_figure(surface,"maximin_score","稳健分","Viridis",zmin=0,zmax=1),"analysis"),
            ReportFigure("heatmap-gates","Heatmap 2 · 通过门禁数量",heatmap_figure(surface,"gate_pass_count","门禁数","Cividis",zmin=0,zmax=7),"analysis"),
            ReportFigure("heatmap-cagr","Heatmap 3 · 132窗口中位CAGR",heatmap_figure(surface,"median_cagr_pct","CAGR %","RdYlGn"),"analysis"),
            ReportFigure("heatmap-sharpe","Heatmap 4 · 132窗口中位Sharpe",heatmap_figure(surface,"median_sharpe","Sharpe","RdYlGn"),"analysis"),
        ],
        experiment=context.config, run_id=args.run_id, template_id="interactive_research_v5",
    )
    (run_root / "report.html").write_text(report); (run_root / "report.md").write_text(f"# QQQ StochRSI超大网格\n\n- {status}。\n- 四张参数面：稳健分、门禁数、中位CAGR、中位Sharpe。\n")
    summary = {"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"robust_champion":selection["robust_champion"],"strongest_near_miss":selection["strongest_near_miss"],"passing_case_count":selection["passing_case_count"],"valid_grid_count":len(surface),"heatmap_metrics":context.config["parameters"]["heatmap_metrics"],"aggregate_results":aggregate.to_dict("records")}; (analysis_root / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    provenance = {"schema_version":1,"experiment_id":context.config["experiment_id"],"run_id":args.run_id,"software":{"python":platform.python_version(),"plotly":plotly.__version__},"source_files":{}}
    for relative in ["backtest/scripts/run_stochrsi_supergrid_heatmaps.py","backtest/scripts/analyze_stochrsi_supergrid_heatmaps.py","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/reporting.py","data/processed/daily/QQQ.csv"]:
        path=WORKSPACE/relative; provenance["source_files"][relative]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root / "provenance.json").write_text(json.dumps(provenance,indent=2)+"\n"); (run_root / "README.md").write_text(f"# {args.run_id}\n\n2000–2015 StochRSI supergrid heatmap run.\n")
    artifacts={"schema_version":1,"artifacts":{}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json","report.pdf"}: artifacts["artifacts"][str(path.relative_to(run_root))]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root / "artifact_manifest.json").write_text(json.dumps(artifacts,indent=2)+"\n")
    if record.get("status") == "running": record_analysis_complete(context,args.run_id)
    print(run_root / "report.html")


if __name__ == "__main__":
    main()
