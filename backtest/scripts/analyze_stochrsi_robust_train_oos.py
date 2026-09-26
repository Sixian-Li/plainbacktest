#!/usr/bin/env python3
"""Report the 2005–2020 robust selection and untouched 2021–2026 test."""
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
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.analyze_stochrsi_multihorizon_robustness import surface_figure

ROOT=Path(__file__).resolve().parents[1]; WORKSPACE=ROOT.parent

def market_figure(raw):
    d=raw[raw.date.between("2005-01-03","2026-08-04")].copy(); d["sma100"]=d.close.rolling(100).mean()
    fig=go.Figure(go.Scatter(x=d.date,y=d.close,mode="lines",name="QQQ Close",line={"color":"#111827","width":1.4},meta={"series_key":"qqq_close","panel":"price","label":"QQQ Close"}))
    fig.add_trace(go.Scatter(x=d.date,y=d.sma100,mode="lines",name="SMA100参考",visible="legendonly",line={"color":"#94a3b8","width":1.0},meta={"series_key":"sma100_reference","panel":"market","label":"SMA100参考"}))
    fig.add_vrect(x0="2005-01-03",x1="2020-12-31",fillcolor="#2563eb",opacity=.08,line_width=0,annotation_text="只用于选参")
    fig.add_vrect(x0="2021-01-04",x1="2026-08-04",fillcolor="#16a085",opacity=.08,line_width=0,annotation_text="锁定验证")
    fig.update_layout(template="plotly_white",height=560,hovermode="x unified",dragmode="pan",yaxis_title="QQQ adjusted Close"); return fig

def performance_figure(raw, selected):
    start,end=pd.Timestamp("2021-01-04"),pd.Timestamp("2026-08-04"); d=raw[raw.date.between(start,end)].reset_index(drop=True); bh,_,_=buy_hold(d,initial_cash=100000,cost_bps=5)
    series=[("buy_hold","Buy & Hold",bh.date,bh.equity,True)]
    for case,name in [(selected,"训练冻结28/88"),("P042_100","参考42/100")]:
        short,long=map(int,case.removeprefix("P").split("_")); spec=TimingSpec("CROSS",cost_bps=5,periods=(short,long),buy_threshold=.2,sell_threshold=.8); prepared=prepare_dual_stochrsi_data(raw,spec); ref=run_reference(prepared,spec,analysis_start=start,analysis_end=end,initial_cash=100000); series.append((case.lower(),name,ref.daily.date,ref.daily.equity,False))
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.7,.3],vertical_spacing=.07,subplot_titles=("锁定账户净值","回撤"))
    colors={"buy_hold":"#334155",selected.lower():"#16a085","p042_100":"#2563eb"}
    for key,name,x,e,is_benchmark in series:
        values=pd.Series(e).to_numpy(float); dd=values/pd.Series(values).cummax().to_numpy()-1
        fig.add_trace(go.Scatter(x=x,y=values,mode="lines",name=name,line={"color":colors[key],"width":1.8},meta={"series_key":key,"panel":"equity","label":name,"is_benchmark":is_benchmark,"cost_bps":5}),row=1,col=1)
        fig.add_trace(go.Scatter(x=x,y=dd*100,mode="lines",showlegend=False,line={"color":colors[key],"width":1.1},meta={"series_key":key,"panel":"drawdown","label":name}),row=2,col=1)
    fig.update_layout(template="plotly_white",height=720,hovermode="x unified",dragmode="pan"); fig.update_yaxes(title_text="美元",row=1,col=1); fig.update_yaxes(title_text="%",row=2,col=1); return fig

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--experiment",type=Path,required=True); ap.add_argument("--run-id",required=True); a=ap.parse_args(); c=load_experiment(a.experiment); record=load_run(c,a.run_id)
    if any(b["status"]!="completed" for b in record["expected_blocks"]): raise RuntimeError("Incomplete blocks")
    rr=c.run_root(a.run_id); ar=rr/"analysis"; ar.mkdir(parents=True,exist_ok=True); formal=[]; benchmarks=[]
    for cost in (0.,5.,10.):
        f=pd.read_csv(rr/f"QQQ/cost_{cost:g}bps/formal_results.csv"); f.insert(0,"cost_bps",cost); formal.append(f)
        b=pd.read_csv(rr/f"QQQ/cost_{cost:g}bps/buy_hold_results.csv"); b.insert(0,"cost_bps",cost); benchmarks.append(b)
    formal=pd.concat(formal,ignore_index=True); benchmark=pd.concat(benchmarks,ignore_index=True); formal.to_csv(ar/"formal_results.csv",index=False,lineterminator="\n")
    selection=json.loads((rr/"QQQ/cost_5bps/selection.json").read_text()); selected=(selection["robust_champion"] or selection["strongest_near_miss"])["case_id"]; passed=selection["robust_champion"] is not None
    hold=formal[formal.cohort.eq("HOLDOUT")].copy(); bh=benchmark[benchmark.cohort.eq("HOLDOUT")].copy(); bh["case_id"]="BUY_HOLD"; combined=pd.concat([hold,bh],ignore_index=True); combined.to_csv(ar/"holdout_results.csv",index=False,lineterminator="\n")
    train=formal[formal.cohort.ne("HOLDOUT")].groupby(["cost_bps","case_id"],as_index=False).agg(window_count=("window_id","count"),median_cagr_pct=("cagr_pct","median"),q25_cagr_pct=("cagr_pct",lambda x:x.quantile(.25)),median_sharpe=("sharpe","median"),q25_sharpe=("sharpe",lambda x:x.quantile(.25))); train.to_csv(ar/"training_aggregate.csv",index=False,lineterminator="\n")
    five=combined[combined.cost_bps.eq(5)]; rows="".join(f"<tr><td>{html.escape('Buy & Hold' if r.case_id=='BUY_HOLD' else r.case_id)}</td><td>{r.cagr_pct:.3f}%</td><td>{r.sharpe:.3f}</td><td>{r.max_drawdown_pct:.2f}%</td><td>{r.total_return_pct:.2f}%</td></tr>" for r in five.itertuples())
    near=selection["strongest_near_miss"]; status="通过全部训练门禁" if passed else "未通过全部训练门禁"
    summary_html=f"<h2>先冻结参数</h2><p><strong>2005–2020没有参数通过全部七类门禁；按预案冻结最强近似候选28/88（{status}），之后才打开2021–2026。</strong></p><table><thead><tr><th>训练诊断</th><th>28/88</th><th>要求</th></tr></thead><tbody><tr><td>最差期限下四分位排名</td><td>{near['worst_horizon_q25_joint_rank']:.3f}</td><td>≥0.550</td></tr><tr><td>最差时期块中位排名</td><td>{near['worst_time_block_median_joint_rank']:.3f}</td><td>≥0.550</td></tr><tr><td>删除任一起始年份</td><td>{near['leave_one_year_out_q25_joint_rank']:.3f}</td><td>≥0.500</td></tr><tr><td>3×3邻域最差</td><td>{near['worst_local_3x3_score']:.3f}</td><td>≥0.450</td></tr></tbody></table><h2>2021–2026锁定验证 · 5 bps</h2><table><thead><tr><th>路径</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>总收益</th></tr></thead><tbody>{rows}</tbody></table>"
    raw=pd.read_csv(WORKSPACE/"data/processed/daily/QQQ.csv",parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True); surface=pd.read_csv(rr/"QQQ/cost_5bps/surface.csv")
    report=render_interactive_report(title="QQQ Stochastic RSI 2005–2020训练 / 2021–2026锁定验证",heading="训练期不读取2021以后 · 参数先冻结再验证",subtitle="132个训练窗口 · 874组周期 · 2021–2026单一连续验证账户",summary_html=summary_html,notes=["训练期没有任何参数通过全部七类门禁，因此28/88只能称为预先规则选出的最强近似候选。","2021–2026数据没有参与参数排名、门禁或替代候选选择。","锁定期42/100只作为事先存在的参考锚点，不能取代训练冻结的28/88。"],figures=[ReportFigure("market-qqq","QQQ Close与训练/验证边界",market_figure(raw),"market"),ReportFigure("performance-qqq","2021–2026锁定净值与回撤",performance_figure(raw,selected),"performance"),ReportFigure("robust-surface","2005–2020训练 maximin 参数面",surface_figure(surface),"analysis")],experiment=c.config,run_id=a.run_id,template_id="interactive_research_v5")
    (rr/"report.html").write_text(report); hold5=five[five.case_id.eq(selected)].iloc[0]; summary={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"training_robust_champion":selection["robust_champion"],"frozen_parameter":selected,"frozen_parameter_status":"robust_champion" if passed else "strongest_near_miss","holdout_5bps":hold5.to_dict(),"holdout_results":combined.to_dict("records")}; (ar/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n"); (rr/"report.md").write_text(f"# QQQ StochRSI 训练与锁定验证\n\n- 训练稳健冠军：无。\n- 冻结最强近似参数：28/88。\n- 2021–2026 5 bps CAGR：{hold5.cagr_pct:.3f}%。\n")
    provenance={"schema_version":1,"experiment_id":c.config["experiment_id"],"run_id":a.run_id,"software":{"python":platform.python_version(),"plotly":plotly.__version__},"source_files":{}}
    for rel in ["backtest/scripts/run_stochrsi_multihorizon_robustness.py","backtest/scripts/analyze_stochrsi_robust_train_oos.py","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/reporting.py","data/processed/daily/QQQ.csv"]:
        path=WORKSPACE/rel; provenance["source_files"][rel]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (rr/"provenance.json").write_text(json.dumps(provenance,indent=2)+"\n"); (rr/"README.md").write_text(f"# {a.run_id}\n\n2005–2020 training and untouched 2021–2026 validation.\n"); artifacts={"schema_version":1,"artifacts":{}}
    for path in sorted(rr.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json","report.pdf"}: artifacts["artifacts"][str(path.relative_to(rr))]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (rr/"artifact_manifest.json").write_text(json.dumps(artifacts,indent=2)+"\n")
    if record.get("status")=="running": record_analysis_complete(c,a.run_id)
    print(rr/"report.html")

if __name__=="__main__": main()
