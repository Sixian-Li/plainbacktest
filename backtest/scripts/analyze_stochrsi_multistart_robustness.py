#!/usr/bin/env python3
"""Analyze and report the QQQ StochRSI true-restart robustness experiment."""

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
from quantkit.experiment import assert_run_writable, cost_label, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.run_dual_stochrsi_timing import buy_hold


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/TIM/TIM-v0.70a.3__26-08-25__qqq_stochrsi_multistart_robustness"
COLORS = {"BUY_HOLD":"#334155","P042_100":"#2563eb","P036_152":"#dc2626","P032_152":"#e67e22","P044_070":"#16a085"}


def label(case: str, selection: dict) -> str:
    names = {"P042_100":"锚点42/100","P036_152":"全历史CAGR 36/152","P032_152":"全历史Sharpe 32/152"}
    if case == selection["mechanical_max"]["case_id"]: return f"训练机械最高 {case[1:4]}/{int(case[-3:])}"
    return names.get(case, case)


def aggregate(formal: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (cost, cohort, case), current in formal.groupby(["cost_bps","cohort","case_id"]):
        rows.append({"cost_bps":cost,"cohort":cohort,"case_id":case,"window_count":len(current),
                     "median_cagr_pct":current["cagr_pct"].median(),"q25_cagr_pct":current["cagr_pct"].quantile(.25),
                     "median_sharpe":current["sharpe"].median(),"q25_sharpe":current["sharpe"].quantile(.25),
                     "median_max_drawdown_pct":current["max_drawdown_pct"].median(),
                     "median_closed_trades":current["closed_trade_count"].median()})
    for (cost, cohort), current in benchmark.groupby(["cost_bps","cohort"]):
        rows.append({"cost_bps":cost,"cohort":cohort,"case_id":"BUY_HOLD","window_count":len(current),
                     "median_cagr_pct":current["cagr_pct"].median(),"q25_cagr_pct":current["cagr_pct"].quantile(.25),
                     "median_sharpe":current["sharpe"].median(),"q25_sharpe":current["sharpe"].quantile(.25),
                     "median_max_drawdown_pct":current["max_drawdown_pct"].median(),
                     "median_closed_trades":current["closed_trade_count"].median()})
    return pd.DataFrame(rows)


def market_figure(raw: pd.DataFrame) -> go.Figure:
    current = raw[raw["date"].between("2010-01-04","2026-08-04")].copy()
    current["sma100"] = current["close"].rolling(100).mean()
    figure = go.Figure(go.Scatter(x=current["date"],y=current["close"],mode="lines",name="QQQ Close",
                                  line={"color":"#111827","width":1.4},meta={"series_key":"qqq_close","panel":"price","label":"QQQ Close"}))
    figure.add_trace(go.Scatter(x=current["date"],y=current["sma100"],mode="lines",name="SMA100参考",
                                line={"color":"#94a3b8","width":1.0},visible="legendonly",
                                meta={"series_key":"sma100_reference","panel":"market","label":"SMA100参考"}))
    figure.add_vrect(x0="2010-01-04",x1="2019-09-30",fillcolor="#2563eb",opacity=.07,line_width=0,annotation_text="训练可见日期")
    figure.add_vrect(x0="2020-01-02",x1="2026-06-30",fillcolor="#16a085",opacity=.07,line_width=0,annotation_text="锁定验证日期")
    figure.update_layout(template="plotly_white",height=560,hovermode="x unified",dragmode="pan",xaxis_rangeslider_visible=False,yaxis_title="QQQ adjusted Close")
    return figure


def performance_figure(raw: pd.DataFrame, ids: list[str], selection: dict, parameters: dict) -> go.Figure:
    start, end, initial_cash = pd.Timestamp("2020-01-02"), pd.Timestamp("2024-12-31"), 100000.0
    slice_ = raw[raw["date"].between(start,end)].reset_index(drop=True)
    benchmark, _, _ = buy_hold(slice_,initial_cash=initial_cash,cost_bps=5)
    series=[("BUY_HOLD","Buy & Hold",benchmark["date"],benchmark["equity"])]
    for case in ids:
        short,long=map(int,case.removeprefix("P").split("_"))
        spec=TimingSpec("CROSS",cost_bps=5,periods=(short,long),buy_threshold=.2,sell_threshold=.8)
        prepared=prepare_dual_stochrsi_data(raw,spec)
        reference=run_reference(prepared,spec,analysis_start=start,analysis_end=end,initial_cash=initial_cash)
        series.append((case,label(case,selection),reference.daily["date"],reference.daily["equity"]))
    figure=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.07,row_heights=[.7,.3],subplot_titles=("账户净值","回撤"))
    for key,name,x,equity in series:
        values=pd.Series(equity).to_numpy(float); drawdown=values/pd.Series(values).cummax().to_numpy()-1
        figure.add_trace(go.Scatter(x=x,y=values,mode="lines",name=name,line={"color":COLORS.get(key,"#7c3aed"),"width":1.8},
                                    meta={"series_key":key.lower(),"panel":"equity","label":name,"is_benchmark":key=="BUY_HOLD","cost_bps":5}),row=1,col=1)
        figure.add_trace(go.Scatter(x=x,y=drawdown*100,mode="lines",showlegend=False,line={"color":COLORS.get(key,"#7c3aed"),"width":1.1},
                                    meta={"series_key":key.lower(),"panel":"drawdown","label":name}),row=2,col=1)
    figure.update_layout(template="plotly_white",height=720,hovermode="x unified",dragmode="pan")
    figure.update_yaxes(title_text="美元",row=1,col=1); figure.update_yaxes(title_text="%",row=2,col=1)
    return figure


def surface_figure(surface: pd.DataFrame) -> go.Figure:
    matrix=surface.pivot(index="long_period",columns="short_period",values="robust_score").sort_index()
    figure=go.Figure(go.Heatmap(x=matrix.columns,y=matrix.index,z=matrix.to_numpy(),colorscale="Viridis",colorbar={"title":"Q25 joint rank"},
                                hovertemplate="短周期 %{x}<br>长周期 %{y}<br>稳健分 %{z:.3f}<extra></extra>"))
    figure.update_layout(template="plotly_white",height=700,xaxis_title="短周期",yaxis_title="长周期")
    return figure


def window_figure(formal: pd.DataFrame, selection: dict) -> go.Figure:
    five=formal[formal["cost_bps"].eq(5)].copy(); figure=make_subplots(rows=1,cols=2,subplot_titles=("五年CAGR","五年Sharpe"))
    for case,current in five.groupby("case_id"):
        for col,metric in ((1,"cagr_pct"),(2,"sharpe")):
            figure.add_trace(go.Box(x=current["cohort"],y=current[metric],name=label(case,selection),legendgroup=case,showlegend=col==1,
                                    marker_color=COLORS.get(case,"#7c3aed"),boxpoints="all",jitter=.25),row=1,col=col)
    figure.update_layout(template="plotly_white",height=620,boxmode="group"); figure.update_yaxes(title_text="%",row=1,col=1)
    return figure


def summary_table(agg: pd.DataFrame, selection: dict) -> str:
    current=agg[(agg["cost_bps"].eq(5))].copy(); order=["BUY_HOLD","P042_100","P036_152","P032_152",selection["mechanical_max"]["case_id"]]
    body=[]
    for case in dict.fromkeys(order):
        for cohort in ("training","validation"):
            row=current[current["case_id"].eq(case)&current["cohort"].eq(cohort)]
            if row.empty: continue
            r=row.iloc[0]; body.append(f"<tr><td>{html.escape('Buy & Hold' if case=='BUY_HOLD' else label(case,selection))}</td><td>{'训练' if cohort=='training' else '锁定验证'}</td><td>{r.median_cagr_pct:.3f}%</td><td>{r.median_sharpe:.3f}</td><td>{r.median_max_drawdown_pct:.2f}%</td><td>{r.median_closed_trades:.1f}</td></tr>")
    stable=selection["robust_champion"]
    verdict=(f"训练稳健冠军为{stable['case_id']}。" if stable else "训练高分集合未形成九点内部平台，因此没有稳健冠军。")
    return "<h2>5 bps多起点结果</h2><table><thead><tr><th>路径</th><th>窗口</th><th>中位CAGR</th><th>中位Sharpe</th><th>中位最大回撤</th><th>中位闭合交易</th></tr></thead><tbody>"+"".join(body)+"</tbody></table><p>"+verdict+"</p>"


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--experiment",type=Path,default=DEFAULT_EXPERIMENT); parser.add_argument("--run-id",required=True); args=parser.parse_args()
    context=load_experiment(args.experiment); record=load_run(context,args.run_id)
    if record.get("status") not in {"running","completed_unvalidated"}: raise RuntimeError("Run must be running or completed-unvalidated before analysis")
    analysis_was_complete=record.get("status")=="completed_unvalidated"
    if not analysis_was_complete: assert_run_writable(context,args.run_id)
    incomplete=[b["block_id"] for b in record["expected_blocks"] if b["status"]!="completed"]
    if incomplete: raise RuntimeError(f"Incomplete blocks: {incomplete}")
    run_root=context.run_root(args.run_id); analysis_root=run_root/"analysis"; analysis_root.mkdir(parents=True,exist_ok=True)
    frames=[]; benchmarks=[]
    for cost in (0.0,5.0):
        block=run_root/"QQQ"/cost_label(cost); f=pd.read_csv(block/"formal_results.csv"); f.insert(0,"cost_bps",cost); frames.append(f)
        b=pd.read_csv(block/"buy_hold_results.csv"); b.insert(0,"cost_bps",cost); benchmarks.append(b)
    formal=pd.concat(frames,ignore_index=True); benchmark=pd.concat(benchmarks,ignore_index=True); agg=aggregate(formal,benchmark)
    formal.to_csv(analysis_root/"formal_results.csv",index=False,lineterminator="\n"); agg.to_csv(analysis_root/"aggregate_results.csv",index=False,lineterminator="\n")
    five_block=run_root/"QQQ"/"cost_5bps"; selection=json.loads((five_block/"training_selection.json").read_text(encoding="utf-8")); surface=pd.read_csv(five_block/"training_surface.csv")
    ids=json.loads((five_block/"metrics.json").read_text(encoding="utf-8"))["selected_case_ids"]
    anchor=agg[(agg.cost_bps.eq(5))&agg.cohort.eq("validation")&agg.case_id.eq("P042_100")].iloc[0]
    mechanical_id=selection["mechanical_max"]["case_id"]; mechanical=agg[(agg.cost_bps.eq(5))&agg.cohort.eq("validation")&agg.case_id.eq(mechanical_id)].iloc[0]
    summary={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"purpose":"2010–2026 true-restart training then locked validation", "selection":selection,
             "selected_case_ids":ids,"five_bps_locked_anchor":json_safe(anchor.to_dict()),"five_bps_locked_training_mechanical":json_safe(mechanical.to_dict()),
             "conclusion":"no_robust_champion" if selection["robust_champion"] is None else "locked_candidate_evaluated"}
    (analysis_root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    raw=pd.read_csv(WORKSPACE_ROOT/"data/processed/daily/QQQ.csv",parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True)
    report=render_interactive_report(title="QQQ双周期Stochastic RSI多起点稳健选参",heading="QQQ 2010–2026 · 真正现金重启的五年窗口",subtitle="20个训练窗口 + 7个锁定窗口 · 买0.20 / 卖0.80 · 0/5 bps",
        summary_html=summary_table(agg,selection),notes=["每个窗口均从10万美元现金和空仓重新开始；起点前数据只用于指标预热。","训练窗口最晚结束于2019年，锁定窗口最早开始于2020年，交易日期不重叠。","874组训练筛选使用独立账本；所有报告候选在每个窗口均由PyBroker与独立账本复核。","训练机械最高点若没有九点内部平台，不会被称为稳健冠军。"],
        figures=[ReportFigure("market-qqq","QQQ Close与训练/锁定日期",market_figure(raw),"market"),ReportFigure("performance-qqq","首个锁定窗口的账户净值与回撤",performance_figure(raw,ids,selection,context.config["parameters"]),"performance"),ReportFigure("training-surface","训练稳健分参数面",surface_figure(surface),"analysis"),ReportFigure("window-distributions","多起点五年指标分布",window_figure(formal,selection),"analysis")],
        experiment=context.config,run_id=args.run_id,template_id=context.config["reporting"]["template_id"])
    (run_root/"report.html").write_text(report,encoding="utf-8")
    lines=["# QQQ双周期Stochastic RSI多起点稳健选参","","- 训练：2010–2019内20个季度起点五年窗口。","- 锁定：2020–2026内7个季度起点五年窗口。",f"- 训练机械最高：{mechanical_id}；稳健冠军：{selection['robust_champion'] or '无'}。",f"- 锁定中位数：42/100 CAGR {anchor.median_cagr_pct:.3f}% / Sharpe {anchor.median_sharpe:.3f}；{mechanical_id} CAGR {mechanical.median_cagr_pct:.3f}% / Sharpe {mechanical.median_sharpe:.3f}。"]
    (run_root/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    tracked=["backtest/requirements.lock","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/metrics.py","backtest/quantkit/reporting.py","backtest/scripts/run_stochrsi_multistart_robustness.py","backtest/scripts/analyze_stochrsi_multistart_robustness.py","backtest/report_templates/interactive_research_v5/page.html","backtest/report_templates/interactive_research_v5/styles.css","backtest/report_templates/interactive_research_v5/interactions.js","data/processed/manifest.json","data/processed/daily/QQQ.csv"]
    provenance={"schema_version":1,"experiment_id":context.config["experiment_id"],"run_id":args.run_id,"created_at_utc":summary["created_at_utc"],"software":{"python":platform.python_version(),"lib_pybroker":"1.2.12","plotly":plotly.__version__},"source_files":{}}
    for relative in tracked:
        path=WORKSPACE_ROOT/relative; provenance["source_files"][relative]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root/"provenance.json").write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (run_root/"README.md").write_text(f"# Run {args.run_id}\n\nQQQ StochRSI true-restart robustness; see report and cost blocks.\n",encoding="utf-8")
    artifacts={"schema_version":1,"created_at_utc":summary["created_at_utc"],"artifacts":{}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json"}: artifacts["artifacts"][str(path.relative_to(run_root))]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root/"artifact_manifest.json").write_text(json.dumps(artifacts,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if not analysis_was_complete: record_analysis_complete(context,args.run_id)
    print(f"Wrote {run_root/'report.html'}")


if __name__=="__main__": main()
