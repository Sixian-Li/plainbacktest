#!/usr/bin/env python3
"""Build the 2000–2015 narrow dual-StochRSI robustness report."""
from __future__ import annotations
import argparse, html, json, platform
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import plotly, plotly.graph_objects as go
from plotly.subplots import make_subplots
from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, run_reference
from quantkit.experiment import load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_stochrsi_multihorizon_robustness import distribution_figure, surface_figure
from scripts.run_dual_stochrsi_timing import buy_hold

ROOT=Path(__file__).resolve().parents[1]; WORKSPACE=ROOT.parent

def market_figure(raw):
    d=raw[raw.date.between("2000-01-03","2015-12-31")].copy(); d["sma100"]=d.close.rolling(100).mean(); fig=go.Figure(go.Scatter(x=d.date,y=d.close,mode="lines",name="QQQ Close",line={"color":"#111827","width":1.4},meta={"series_key":"qqq_close","panel":"price","label":"QQQ Close"})); fig.add_trace(go.Scatter(x=d.date,y=d.sma100,mode="lines",name="SMA100参考",visible="legendonly",line={"color":"#94a3b8","width":1},meta={"series_key":"sma100_reference","panel":"market","label":"SMA100参考"})); fig.update_layout(template="plotly_white",height=560,hovermode="x unified",dragmode="pan",yaxis_title="QQQ adjusted Close"); return fig

def performance_figure(raw,ids):
    start,end=pd.Timestamp("2005-01-03"),pd.Timestamp("2009-12-31"); d=raw[raw.date.between(start,end)].reset_index(drop=True); bh,_,_=buy_hold(d,initial_cash=100000,cost_bps=5); series=[("buy_hold","Buy & Hold",bh.date,bh.equity,True)]
    for cid in ids:
        short,long=map(int,cid.removeprefix("P").split("_")); spec=TimingSpec("CROSS",cost_bps=5,periods=(short,long),buy_threshold=.2,sell_threshold=.8); prepared=prepare_dual_stochrsi_data(raw,spec); ref=run_reference(prepared,spec,analysis_start=start,analysis_end=end,initial_cash=100000); series.append((cid.lower(),f"{short}/{long}",ref.daily.date,ref.daily.equity,False))
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.7,.3],vertical_spacing=.07,subplot_titles=("代表性五年窗口净值","回撤")); colors=["#334155","#16a085","#2563eb","#d97706"]
    for color,(key,name,x,e,is_benchmark) in zip(colors,series):
        values=pd.Series(e).to_numpy(float); dd=values/pd.Series(values).cummax().to_numpy()-1; fig.add_trace(go.Scatter(x=x,y=values,mode="lines",name=name,line={"color":color,"width":1.8},meta={"series_key":key,"panel":"equity","label":name,"is_benchmark":is_benchmark,"cost_bps":5}),row=1,col=1); fig.add_trace(go.Scatter(x=x,y=dd*100,mode="lines",showlegend=False,line={"color":color,"width":1.1},meta={"series_key":key,"panel":"drawdown","label":name}),row=2,col=1)
    fig.update_layout(template="plotly_white",height=720,hovermode="x unified",dragmode="pan"); return fig

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--experiment",type=Path,required=True); ap.add_argument("--run-id",required=True); a=ap.parse_args(); c=load_experiment(a.experiment); record=load_run(c,a.run_id)
    if any(b["status"]!="completed" for b in record["expected_blocks"]): raise RuntimeError("Incomplete blocks")
    rr=c.run_root(a.run_id); ar=rr/"analysis"; ar.mkdir(parents=True,exist_ok=True); frames=[]
    for cost in (0.,5.,10.):
        f=pd.read_csv(rr/f"QQQ/cost_{cost:g}bps/formal_results.csv"); f.insert(0,"cost_bps",cost); frames.append(f)
    formal=pd.concat(frames,ignore_index=True); formal.to_csv(ar/"formal_results.csv",index=False,lineterminator="\n")
    aggregate=formal.groupby(["cost_bps","case_id"],as_index=False).agg(window_count=("window_id","count"),median_cagr_pct=("cagr_pct","median"),q25_cagr_pct=("cagr_pct",lambda x:x.quantile(.25)),median_sharpe=("sharpe","median"),q25_sharpe=("sharpe",lambda x:x.quantile(.25)),median_max_drawdown_pct=("max_drawdown_pct","median")); aggregate.to_csv(ar/"aggregate_results.csv",index=False,lineterminator="\n")
    five=rr/"QQQ/cost_5bps"; selection=json.loads((five/"selection.json").read_text()); surface=pd.read_csv(five/"surface.csv"); selected=selection["robust_champion"] or selection["strongest_near_miss"]; passed=selection["robust_champion"] is not None
    gate_specs=[("最差期限下四分位","worst_horizon_q25_joint_rank",.55),("最差时期块中位","worst_time_block_median_joint_rank",.55),("删除任一起始年份","leave_one_year_out_q25_joint_rank",.50),("3×3邻域最差","worst_local_3x3_score",.45),("CAGR胜持有比例","cagr_win_rate_vs_buy_hold",.40),("Sharpe胜持有比例","sharpe_win_rate_vs_buy_hold",.55),("回撤胜持有比例","drawdown_win_rate_vs_buy_hold",.60)]
    gates="".join(f"<tr><td>{n}</td><td>{selected[k]:.3f}</td><td>{t:.3f}</td><td>{'通过' if selected[k]>=t else '失败'}</td></tr>" for n,k,t in gate_specs)
    rows="".join(f"<tr><td>{html.escape(r.case_id)}</td><td>{r.cost_bps:g}</td><td>{r.median_cagr_pct:.3f}%</td><td>{r.q25_cagr_pct:.3f}%</td><td>{r.median_sharpe:.3f}</td><td>{r.q25_sharpe:.3f}</td><td>{r.median_max_drawdown_pct:.2f}%</td></tr>" for r in aggregate.itertuples())
    status=f"{selected['short_period']:.0f}/{selected['long_period']:.0f}通过全部门禁，是本轮稳健冠军" if passed else f"没有参数通过全部门禁；{selected['short_period']:.0f}/{selected['long_period']:.0f}只是通过门禁最多的近似候选"
    summary_html=f"<h2>稳健结论</h2><p><strong>{status}；通过数为{selection['passing_case_count']}/121。</strong></p><table><thead><tr><th>门禁</th><th>结果</th><th>要求</th><th>状态</th></tr></thead><tbody>{gates}</tbody></table><h2>132窗口候选表现</h2><table><thead><tr><th>参数</th><th>单边bps</th><th>中位CAGR</th><th>下四分位CAGR</th><th>中位Sharpe</th><th>下四分位Sharpe</th><th>中位回撤</th></tr></thead><tbody>{rows}</tbody></table>"
    raw=pd.read_csv(WORKSPACE/"data/processed/daily/QQQ.csv",parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date"); ids=list(dict.fromkeys(["P022_088","P028_088",selected["case_id"]]))
    report=render_interactive_report(title="QQQ双周期Stochastic RSI 2000–2015窄网格稳健选参",heading="20–30 × 85–95 · 121组 · 132个季度重启窗口",subtitle="只用2000–2015 · 买0.20 / 卖0.80 · 0/5/10 bps",summary_html=summary_html,notes=["机械最高点不参与冠军命名；只有全部七类门禁同时通过才称为稳健冠军。","选择和报告不读取2016年以后数据。","5 bps筛选使用独立账本；报告候选在0/5/10 bps逐窗口由PyBroker复核。"],figures=[ReportFigure("market-qqq","QQQ Close · 2000–2015",market_figure(raw),"market"),ReportFigure("performance-qqq","代表性五年窗口净值与回撤",performance_figure(raw,ids),"performance"),ReportFigure("robust-surface","121组maximin稳健分",surface_figure(surface),"analysis"),ReportFigure("window-distributions","候选在132个窗口的CAGR分布",distribution_figure(formal),"analysis")],experiment=c.config,run_id=a.run_id,template_id="interactive_research_v5")
    (rr/"report.html").write_text(report); (rr/"report.md").write_text(f"# QQQ StochRSI 2000–2015窄网格稳健选参\n\n- {status}。\n")
    summary={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"robust_champion":selection["robust_champion"],"strongest_near_miss":selection["strongest_near_miss"],"passing_case_count":selection["passing_case_count"],"aggregate_results":aggregate.to_dict("records")}; (ar/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    provenance={"schema_version":1,"experiment_id":c.config["experiment_id"],"run_id":a.run_id,"software":{"python":platform.python_version(),"plotly":plotly.__version__},"source_files":{}}
    for rel in ["backtest/scripts/run_stochrsi_multihorizon_robustness.py","backtest/scripts/analyze_stochrsi_narrow_robustness.py","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/reporting.py","data/processed/daily/QQQ.csv"]:
        p=WORKSPACE/rel; provenance["source_files"][rel]={"bytes":p.stat().st_size,"sha256":sha256(p)}
    (rr/"provenance.json").write_text(json.dumps(provenance,indent=2)+"\n"); (rr/"README.md").write_text(f"# {a.run_id}\n\n2000–2015 narrow-grid robustness run.\n"); artifacts={"schema_version":1,"artifacts":{}}
    for p in sorted(rr.rglob("*")):
        if p.is_file() and p.name not in {"artifact_manifest.json","run.json","validation.json","report.pdf"}: artifacts["artifacts"][str(p.relative_to(rr))]={"bytes":p.stat().st_size,"sha256":sha256(p)}
    (rr/"artifact_manifest.json").write_text(json.dumps(artifacts,indent=2)+"\n");
    if record.get("status")=="running": record_analysis_complete(c,a.run_id)
    print(rr/"report.html")

if __name__=="__main__": main()
