#!/usr/bin/env python3
"""Build the formal single-period StochRSI 2005–2020 robustness report."""
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
from scripts.run_dual_stochrsi_timing import buy_hold

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def market_figure(raw: pd.DataFrame) -> go.Figure:
    data = raw[raw.date.between("2005-01-03", "2020-12-31")].copy()
    data["sma100"] = data.close.rolling(100).mean()
    fig = go.Figure(go.Scatter(x=data.date, y=data.close, mode="lines", name="QQQ Close", line={"color":"#111827","width":1.5}, meta={"series_key":"qqq_close","panel":"price","label":"QQQ Close"}))
    fig.add_trace(go.Scatter(x=data.date, y=data.sma100, mode="lines", name="SMA100参考", visible="legendonly", line={"color":"#94a3b8","width":1}, meta={"series_key":"sma100_reference","panel":"market","label":"SMA100参考"}))
    fig.update_layout(template="plotly_white", height=560, hovermode="x unified", dragmode="pan", yaxis_title="QQQ adjusted Close")
    return fig


def performance_figure(raw: pd.DataFrame, case_ids: list[str]) -> go.Figure:
    start, end = pd.Timestamp("2015-01-02"), pd.Timestamp("2019-12-31")
    window = raw[raw.date.between(start, end)].reset_index(drop=True)
    bh, _, _ = buy_hold(window, initial_cash=100000, cost_bps=5)
    series = [("buy_hold", "Buy & Hold", bh.date, bh.equity, True)]
    for case in case_ids:
        period = int(case.removeprefix("S"))
        spec = TimingSpec("CROSS", cost_bps=5, periods=(period,), buy_threshold=.2, sell_threshold=.8)
        prepared = prepare_dual_stochrsi_data(raw, spec)
        ref = run_reference(prepared, spec, analysis_start=start, analysis_end=end, initial_cash=100000)
        series.append((case.lower(), f"单周期 {period}", ref.daily.date, ref.daily.equity, False))
    colors = ["#334155", "#16a085", "#2563eb", "#d97706"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.7,.3], vertical_spacing=.07, subplot_titles=("代表性五年窗口净值", "回撤"))
    for color, (key, name, dates, equity, benchmark) in zip(colors, series):
        values = pd.Series(equity).to_numpy(float); drawdown = values / pd.Series(values).cummax().to_numpy() - 1
        fig.add_trace(go.Scatter(x=dates, y=values, mode="lines", name=name, line={"color":color,"width":1.8}, meta={"series_key":key,"panel":"equity","label":name,"is_benchmark":benchmark,"cost_bps":5}), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=drawdown*100, mode="lines", showlegend=False, line={"color":color,"width":1.1}, meta={"series_key":key,"panel":"drawdown","label":name}), row=2, col=1)
    fig.update_layout(template="plotly_white", height=720, hovermode="x unified", dragmode="pan")
    fig.update_yaxes(title_text="美元", row=1, col=1); fig.update_yaxes(title_text="%", row=2, col=1)
    return fig


def robustness_figure(surface: pd.DataFrame, selected_case: str) -> go.Figure:
    ordered = surface.sort_values("period")
    fig = go.Figure(go.Scatter(x=ordered.period, y=ordered.maximin_score, mode="lines", name="maximin稳健分", line={"color":"#2563eb","width":1.7}))
    selected = ordered[ordered.case_id.eq(selected_case)].iloc[0]
    fig.add_trace(go.Scatter(x=[selected.period], y=[selected.maximin_score], mode="markers", name="按冻结规则选出", marker={"color":"#dc2626","size":12,"symbol":"diamond"}))
    fig.add_hline(y=.45, line_dash="dash", line_color="#94a3b8", annotation_text="局部门禁参考 0.45")
    fig.update_layout(template="plotly_white", height=560, xaxis_title="单一 StochRSI period", yaxis_title="跨维度最差排名", hovermode="x unified")
    return fig


def distribution_figure(formal: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for (cost, case), group in formal.groupby(["cost_bps", "case_id"]):
        fig.add_trace(go.Box(y=group.cagr_pct, name=f"{case} · {cost:g}bps", boxpoints=False, visible=True if cost == 5 else "legendonly"))
    fig.update_layout(template="plotly_white", height=560, yaxis_title="132个窗口 CAGR (%)")
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True); parser.add_argument("--run-id", required=True)
    args = parser.parse_args(); context = load_experiment(args.experiment); record = load_run(context, args.run_id)
    if any(block["status"] != "completed" for block in record["expected_blocks"]): raise RuntimeError("Incomplete blocks")
    run_root = context.run_root(args.run_id); analysis = run_root / "analysis"; analysis.mkdir(parents=True, exist_ok=True)
    formal_frames = []
    for cost in (0., 5., 10.):
        frame = pd.read_csv(run_root / f"QQQ/cost_{cost:g}bps/formal_results.csv"); frame.insert(0, "cost_bps", cost); formal_frames.append(frame)
    formal = pd.concat(formal_frames, ignore_index=True); formal.to_csv(analysis / "formal_results.csv", index=False, lineterminator="\n")
    aggregate = formal.groupby(["cost_bps","case_id"], as_index=False).agg(window_count=("window_id","count"), median_cagr_pct=("cagr_pct","median"), q25_cagr_pct=("cagr_pct",lambda x:x.quantile(.25)), median_sharpe=("sharpe","median"), q25_sharpe=("sharpe",lambda x:x.quantile(.25)), median_max_drawdown_pct=("max_drawdown_pct","median"))
    aggregate.to_csv(analysis / "aggregate_results.csv", index=False, lineterminator="\n")
    five = run_root / "QQQ/cost_5bps"; selection = json.loads((five / "selection.json").read_text(encoding="utf-8")); surface = pd.read_csv(five / "surface.csv")
    champion = selection["robust_champion"]; selected = champion or selection["strongest_near_miss"]; selected_case = selected["case_id"]; passed = champion is not None
    gate_specs = [("最差期限下四分位", "worst_horizon_q25_joint_rank", .55), ("最差时期块中位", "worst_time_block_median_joint_rank", .55), ("删除任一起始年份", "leave_one_year_out_q25_joint_rank", .50), ("相邻三个周期最差", "worst_local_3point_score", .45), ("CAGR胜持有比例", "cagr_win_rate_vs_buy_hold", .40), ("Sharpe胜持有比例", "sharpe_win_rate_vs_buy_hold", .55), ("回撤胜持有比例", "drawdown_win_rate_vs_buy_hold", .60)]
    gate_rows = "".join(f"<tr><td>{name}</td><td>{selected[key]:.3f}</td><td>{threshold:.3f}</td><td>{'通过' if selected[key]>=threshold else '失败'}</td></tr>" for name,key,threshold in gate_specs)
    metric_rows = "".join(f"<tr><td>{html.escape(row.case_id)}</td><td>{row.cost_bps:g}</td><td>{row.median_cagr_pct:.3f}%</td><td>{row.q25_cagr_pct:.3f}%</td><td>{row.median_sharpe:.3f}</td><td>{row.q25_sharpe:.3f}</td><td>{row.median_max_drawdown_pct:.2f}%</td></tr>" for row in aggregate.itertuples())
    conclusion = f"单周期 {int(selected['period'])} 是稳健冠军" if passed else f"没有单周期通过全部门禁；{int(selected['period'])} 只是最强近似候选"
    summary_html = f"<h2>稳健结论</h2><p><strong>{conclusion}。通过数为 {selection['passing_case_count']}/187。</strong></p><table><thead><tr><th>门禁</th><th>结果</th><th>要求</th><th>状态</th></tr></thead><tbody>{gate_rows}</tbody></table><h2>132窗口候选表现</h2><table><thead><tr><th>参数</th><th>单边bps</th><th>中位CAGR</th><th>下四分位CAGR</th><th>中位Sharpe</th><th>下四分位Sharpe</th><th>中位回撤</th></tr></thead><tbody>{metric_rows}</tbody></table>"
    raw = pd.read_csv(WORKSPACE / "data/processed/daily/QQQ.csv", parse_dates=["date"]); raw = raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True)
    case_ids = list(dict.fromkeys([selected_case, "S042", "S100"]))
    notes = ["机械最高点不参与冠军命名；只有全部七类冻结门禁同时通过才称为稳健冠军。", "选参和报告只使用2005–2020，未查看2021–2026表现。", "187×132条筛选路径使用独立账本；报告候选在0/5/10 bps逐窗口由PyBroker复核。"]
    report = render_interactive_report(title="QQQ 单周期 Stochastic RSI 2005–2020稳健选参", heading="3/5/7年 · 132个季度重启窗口 · 187个单周期", subtitle="只看一个Stochastic RSI · 买0.20 / 卖0.80 · 0/5/10 bps", summary_html=summary_html, notes=notes, figures=[ReportFigure("market-qqq","QQQ Close · 2005–2020",market_figure(raw),"market"), ReportFigure("performance-qqq","代表性五年窗口净值与回撤",performance_figure(raw,case_ids),"performance"), ReportFigure("robust-curve","单周期稳健分曲线",robustness_figure(surface,selected_case),"analysis"), ReportFigure("window-distributions","候选在132个窗口的CAGR分布",distribution_figure(formal),"analysis")], experiment=context.config, run_id=args.run_id, template_id="interactive_research_v5")
    (run_root / "report.html").write_text(report, encoding="utf-8")
    status = "稳健冠军" if passed else "最强近似候选（非冠军）"
    (run_root / "report.md").write_text(f"# QQQ 单周期 StochRSI 稳健选参\n\n- 结论：{status}为 period {int(selected['period'])}。\n- 通过全部七类门禁：{selection['passing_case_count']}/187。\n- 选参数据严格截止于 2020-12-31。\n", encoding="utf-8")
    summary = {"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"robust_champion":champion,"strongest_near_miss":selection["strongest_near_miss"],"passing_case_count":selection["passing_case_count"],"aggregate_results":aggregate.to_dict("records")}
    (analysis / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    provenance = {"schema_version":1,"experiment_id":context.config["experiment_id"],"run_id":args.run_id,"software":{"python":platform.python_version(),"plotly":plotly.__version__},"source_files":{}}
    for relative in ["backtest/scripts/run_single_stochrsi_robustness.py","backtest/scripts/analyze_single_stochrsi_robustness.py","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/reporting.py","data/processed/daily/QQQ.csv"]:
        path = WORKSPACE / relative; provenance["source_files"][relative] = {"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root / "provenance.json").write_text(json.dumps(provenance, indent=2)+"\n", encoding="utf-8")
    (run_root / "README.md").write_text(f"# {args.run_id}\n\nSingle-period StochRSI robustness selection using 2005–2020 only.\n", encoding="utf-8")
    artifacts = {"schema_version":1,"artifacts":{}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json","report.pdf"}: artifacts["artifacts"][str(path.relative_to(run_root))] = {"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root / "artifact_manifest.json").write_text(json.dumps(artifacts, indent=2)+"\n", encoding="utf-8")
    if record.get("status") == "running": record_analysis_complete(context, args.run_id)
    print(run_root / "report.html")


if __name__ == "__main__":
    main()
