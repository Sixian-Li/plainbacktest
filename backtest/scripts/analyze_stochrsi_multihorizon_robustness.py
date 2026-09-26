#!/usr/bin/env python3
"""Build the formal multidimensional StochRSI robustness report."""
from __future__ import annotations
import argparse, html, json, platform
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import plotly, plotly.graph_objects as go
from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_stochrsi_multistart_robustness import market_figure, performance_figure

ROOT=Path(__file__).resolve().parents[1]; WORKSPACE=ROOT.parent

def surface_figure(surface):
    pivot=surface.pivot(index="short_period",columns="long_period",values="maximin_score")
    fig=go.Figure(go.Heatmap(x=pivot.columns,y=pivot.index,z=pivot.values,colorscale="Viridis",colorbar={"title":"maximin"},hovertemplate="短 %{y}<br>长 %{x}<br>稳健分 %{z:.3f}<extra></extra>"))
    champion=surface[surface.passes_all_gates.astype(bool)]
    if not champion.empty: fig.add_trace(go.Scatter(x=champion.long_period,y=champion.short_period,mode="markers",marker={"symbol":"x","size":14,"color":"white"},name="全部门禁通过"))
    fig.update_layout(xaxis_title="长周期",yaxis_title="短周期",height=620); return fig

def distribution_figure(formal):
    fig=go.Figure()
    for (cost,case),g in formal.groupby(["cost_bps","case_id"]):
        fig.add_trace(go.Box(y=g.cagr_pct,name=f"{case} · {cost:g}bps",boxpoints=False,visible=True if cost==5 else "legendonly"))
    fig.update_layout(yaxis_title="窗口 CAGR (%)",height=560); return fig

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--experiment",type=Path,required=True); ap.add_argument("--run-id",required=True); a=ap.parse_args()
    c=load_experiment(a.experiment); record=load_run(c,a.run_id)
    if any(b["status"]!="completed" for b in record["expected_blocks"]): raise RuntimeError("Incomplete blocks")
    rr=c.run_root(a.run_id); ar=rr/"analysis"; ar.mkdir(parents=True,exist_ok=True); frames=[]
    for cost in (0.,5.,10.):
        f=pd.read_csv(rr/f"QQQ/cost_{cost:g}bps/formal_results.csv"); f.insert(0,"cost_bps",cost); frames.append(f)
    formal=pd.concat(frames,ignore_index=True); formal.to_csv(ar/"formal_results.csv",index=False,lineterminator="\n")
    aggregate=formal.groupby(["cost_bps","case_id"],as_index=False).agg(window_count=("window_id","count"),median_cagr_pct=("cagr_pct","median"),q25_cagr_pct=("cagr_pct",lambda x:x.quantile(.25)),median_sharpe=("sharpe","median"),q25_sharpe=("sharpe",lambda x:x.quantile(.25)),median_max_drawdown_pct=("max_drawdown_pct","median"))
    aggregate.to_csv(ar/"aggregate_results.csv",index=False,lineterminator="\n")
    five=rr/"QQQ/cost_5bps"; selection=json.loads((five/"selection.json").read_text()); surface=pd.read_csv(five/"surface.csv"); champion=selection["robust_champion"]
    rows=[]
    for r in aggregate.itertuples(): rows.append(f"<tr><td>{html.escape(r.case_id)}</td><td>{r.cost_bps:g}</td><td>{r.median_cagr_pct:.3f}%</td><td>{r.q25_cagr_pct:.3f}%</td><td>{r.median_sharpe:.3f}</td><td>{r.q25_sharpe:.3f}</td><td>{r.median_max_drawdown_pct:.2f}%</td></tr>")
    gates=[("最差期限下四分位",champion["worst_horizon_q25_joint_rank"],.55),("最差时期块中位",champion["worst_time_block_median_joint_rank"],.55),("删除任一起始年份",champion["leave_one_year_out_q25_joint_rank"],.50),("3×3邻域最差",champion["worst_local_3x3_score"],.45),("CAGR胜持有比例",champion["cagr_win_rate_vs_buy_hold"],.40),("Sharpe胜持有比例",champion["sharpe_win_rate_vs_buy_hold"],.55),("回撤胜持有比例",champion["drawdown_win_rate_vs_buy_hold"],.60)]
    gate_rows="".join(f"<tr><td>{n}</td><td>{v:.3f}</td><td>{t:.3f}</td><td>{'通过' if v>=t else '失败'}</td></tr>" for n,v,t in gates)
    summary_html=f"<h2>稳健结论</h2><p><strong>22/88 是唯一同时通过全部冻结门禁的参数组；通过数为 {selection['passing_case_count']}/874。</strong></p><table><thead><tr><th>门禁</th><th>结果</th><th>要求</th><th>状态</th></tr></thead><tbody>{gate_rows}</tbody></table><h2>141窗口候选表现</h2><table><thead><tr><th>参数</th><th>单边bps</th><th>中位CAGR</th><th>下四分位CAGR</th><th>中位Sharpe</th><th>下四分位Sharpe</th><th>中位回撤</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    raw=pd.read_csv(WORKSPACE/"data/processed/daily/QQQ.csv",parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date")
    report=render_interactive_report(title="QQQ双周期Stochastic RSI 多维稳健筛选",heading="3/5/7年 · 141个季度重启窗口 · 七类门禁",subtitle="2010–2026全样本描述性研究 · 买0.20 / 卖0.80 · 0/5/10 bps",summary_html=summary_html,notes=["机械最高点不参与冠军命名；只有全部七类门禁同时通过才入选。","全部2010–2026数据都参与选择，因此22/88仍不是样本外结论。","874×141条筛选路径使用独立账本，报告候选在0/5/10 bps逐窗口由PyBroker复核。"],figures=[ReportFigure("market-qqq","QQQ Close · 2010–2026",market_figure(raw),"market"),ReportFigure("performance-qqq","2020起点代表窗口净值与回撤",performance_figure(raw,["P042_100","P022_088"],{"mechanical_max":{"case_id":"P022_088"}},c.config["parameters"]),"performance"),ReportFigure("robust-surface","跨期限与邻域 maximin 参数面",surface_figure(surface),"analysis"),ReportFigure("window-distributions","候选在141个窗口的CAGR分布",distribution_figure(formal),"analysis")],experiment=c.config,run_id=a.run_id,template_id="interactive_research_v5")
    (rr/"report.html").write_text(report); (rr/"report.md").write_text("# QQQ StochRSI 多维稳健筛选\n\n- 唯一通过全部七类门禁：22/88。\n- 全样本描述性候选，不是样本外冠军。\n")
    summary={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"robust_champion":champion,"passing_case_count":selection["passing_case_count"],"aggregate_results":aggregate.to_dict("records")}; (ar/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    provenance={"schema_version":1,"experiment_id":c.config["experiment_id"],"run_id":a.run_id,"software":{"python":platform.python_version(),"plotly":plotly.__version__},"source_files":{}}
    for rel in ["backtest/scripts/run_stochrsi_multihorizon_robustness.py","backtest/scripts/analyze_stochrsi_multihorizon_robustness.py","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/reporting.py","data/processed/daily/QQQ.csv"]:
        path=WORKSPACE/rel; provenance["source_files"][rel]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (rr/"provenance.json").write_text(json.dumps(provenance,indent=2)+"\n"); (rr/"README.md").write_text(f"# {a.run_id}\n\nMultidimensional StochRSI robustness run.\n")
    artifacts={"schema_version":1,"artifacts":{}}
    for path in sorted(rr.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json","report.pdf"}: artifacts["artifacts"][str(path.relative_to(rr))]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (rr/"artifact_manifest.json").write_text(json.dumps(artifacts,indent=2)+"\n")
    if record.get("status") == "running": record_analysis_complete(c,a.run_id)
    print(rr/"report.html")

if __name__=="__main__": main()
