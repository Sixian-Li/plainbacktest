#!/usr/bin/env python3
"""Report the corrected all-window 2010–2026 QQQ StochRSI restart scan."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly

from quantkit.experiment import assert_run_writable, cost_label, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.analyze_stochrsi_multistart_robustness import aggregate, label, market_figure, performance_figure, surface_figure, window_figure


BACKTEST_ROOT=Path(__file__).resolve().parents[1]
WORKSPACE_ROOT=BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT=BACKTEST_ROOT/"experiments/TIM/TIM-v0.70a.4__26-08-25__qqq_stochrsi_multistart_full_2010_2026"


def summary_table(agg: pd.DataFrame, selection: dict) -> str:
    current=agg[agg["cost_bps"].eq(5)].copy()
    order=["BUY_HOLD","P042_100","P036_152","P032_152",selection["mechanical_max"]["case_id"]]
    if selection["robust_champion"] is not None: order.append(selection["robust_champion"]["case_id"])
    body=[]
    for case in dict.fromkeys(order):
        row=current[current["case_id"].eq(case)]
        if row.empty: continue
        r=row.iloc[0]; name="Buy & Hold" if case=="BUY_HOLD" else label(case,selection)
        if selection["robust_champion"] is not None and case==selection["robust_champion"]["case_id"]: name=f"稳健冠军 {case[1:4]}/{int(case[-3:])}"
        body.append(f"<tr><td>{html.escape(name)}</td><td>{r.median_cagr_pct:.3f}%</td><td>{r.q25_cagr_pct:.3f}%</td><td>{r.median_sharpe:.3f}</td><td>{r.q25_sharpe:.3f}</td><td>{r.median_max_drawdown_pct:.2f}%</td><td>{r.median_closed_trades:.1f}</td></tr>")
    champion=selection["robust_champion"]
    verdict=(f"通过九点内部平台门槛的描述性稳健冠军为{champion['case_id']}。" if champion else "95%高分集合没有形成九点内部平台，因此没有稳健冠军。")
    return "<h2>5 bps · 47个五年窗口</h2><table><thead><tr><th>路径</th><th>中位CAGR</th><th>下四分位CAGR</th><th>中位Sharpe</th><th>下四分位Sharpe</th><th>中位最大回撤</th><th>中位闭合交易</th></tr></thead><tbody>"+"".join(body)+"</tbody></table><p>"+verdict+"</p>"


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--experiment",type=Path,default=DEFAULT_EXPERIMENT); parser.add_argument("--run-id",required=True); args=parser.parse_args()
    context=load_experiment(args.experiment); record=load_run(context,args.run_id)
    if record.get("status") not in {"running","completed_unvalidated"}: raise RuntimeError("Run must be running or completed-unvalidated")
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
    five=run_root/"QQQ"/"cost_5bps"; selection=json.loads((five/"selection.json").read_text(encoding="utf-8")); surface=pd.read_csv(five/"surface.csv")
    ids=json.loads((five/"metrics.json").read_text(encoding="utf-8"))["selected_case_ids"]
    summary={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"purpose":"2010–2026 all-observable five-year restart exploration; no holdout",
             "window_count":47,"selection":selection,"selected_case_ids":ids,"aggregate_5bps":json_safe(agg[agg.cost_bps.eq(5)].to_dict("records")),
             "conclusion":"robust_champion_named_descriptively" if selection["robust_champion"] else "no_robust_champion"}
    (analysis_root/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    raw=pd.read_csv(WORKSPACE_ROOT/"data/processed/daily/QQQ.csv",parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True)
    report=render_interactive_report(title="QQQ双周期Stochastic RSI 2010–2026全样本多起点",heading="47个季度起点 · 每个窗口真正现金重启五年",subtitle="2010Q1–2021Q3起点 · 买0.20 / 卖0.80 · 0/5 bps",
        summary_html=summary_table(agg,selection),notes=["47个五年窗口全部参与稳健评分，覆盖2010至2026全部可观察区间。","每个窗口从10万美元现金和空仓重新开始；起点前数据只用于指标预热。","该实验没有留出期，任何冠军都只能称为全样本描述性候选。","874组筛选使用独立账本；所有报告候选均由PyBroker与独立账本在0/5 bps逐窗口复核。"],
        figures=[ReportFigure("market-qqq","QQQ Close · 2010–2026",market_figure(raw),"market"),ReportFigure("performance-qqq","2020起点代表窗口净值与回撤",performance_figure(raw,ids,selection,context.config["parameters"]),"performance"),ReportFigure("robust-surface","47窗口稳健分参数面",surface_figure(surface),"analysis"),ReportFigure("window-distributions","47个五年窗口指标分布",window_figure(formal,selection),"analysis")],
        experiment=context.config,run_id=args.run_id,template_id=context.config["reporting"]["template_id"])
    (run_root/"report.html").write_text(report,encoding="utf-8")
    champion=selection["robust_champion"]; (run_root/"report.md").write_text("# QQQ双周期Stochastic RSI 2010–2026全样本多起点\n\n- 47个季度起点五年窗口全部参与选择。\n- 这是全样本探索，没有样本外验证。\n- 稳健冠军："+(champion["case_id"] if champion else "无")+"。\n",encoding="utf-8")
    tracked=["backtest/requirements.lock","backtest/quantkit/dual_stochrsi_timing.py","backtest/quantkit/metrics.py","backtest/quantkit/reporting.py","backtest/scripts/run_stochrsi_multistart_full_2010_2026.py","backtest/scripts/analyze_stochrsi_multistart_full_2010_2026.py","backtest/report_templates/interactive_research_v5/page.html","backtest/report_templates/interactive_research_v5/styles.css","backtest/report_templates/interactive_research_v5/interactions.js","data/processed/manifest.json","data/processed/daily/QQQ.csv"]
    provenance={"schema_version":1,"experiment_id":context.config["experiment_id"],"run_id":args.run_id,"created_at_utc":summary["created_at_utc"],"software":{"python":platform.python_version(),"lib_pybroker":"1.2.12","plotly":plotly.__version__},"source_files":{}}
    for relative in tracked:
        path=WORKSPACE_ROOT/relative; provenance["source_files"][relative]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root/"provenance.json").write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (run_root/"README.md").write_text(f"# Run {args.run_id}\n\nQQQ all-window 2010–2026 StochRSI restart scan.\n",encoding="utf-8")
    artifacts={"schema_version":1,"created_at_utc":summary["created_at_utc"],"artifacts":{}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json","run.json","validation.json"}: artifacts["artifacts"][str(path.relative_to(run_root))]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    (run_root/"artifact_manifest.json").write_text(json.dumps(artifacts,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if not analysis_was_complete: record_analysis_complete(context,args.run_id)
    print(f"Wrote {run_root/'report.html'}")


if __name__=="__main__": main()
