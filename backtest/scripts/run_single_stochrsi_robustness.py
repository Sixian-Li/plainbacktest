#!/usr/bin/env python3
"""Run single-period StochRSI robustness selection on 2005–2020 only."""
from __future__ import annotations
import argparse, concurrent.futures, json, multiprocessing, os, platform, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from quantkit.dual_stochrsi_timing import TimingSpec, prepare_dual_stochrsi_data, run_reference
from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from quantkit.metrics import calculate_metrics
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import normalize_frame
from scripts.run_stochrsi_cross_threshold_grid import run_case
from scripts.run_stochrsi_multihorizon_robustness import build_windows

ROOT=Path(__file__).resolve().parents[1]; WORKSPACE=ROOT.parent; _STATE={}
DEFAULT=ROOT/"experiments/TIM/TIM-v0.70b.1__26-08-25__qqq_single_stochrsi_robust_2005_2020"

def period_values(start:int,end:int,step:int)->list[int]: return list(range(int(start),int(end)+1,int(step)))
def case_id(period:int)->str: return f"S{period:03d}"
def complete_local_min(period:int,lookup:dict[int,float])->float:
    neighbors=[lookup.get(period-1,np.nan),lookup.get(period,np.nan),lookup.get(period+1,np.nan)]
    return np.nan if any(pd.isna(value) for value in neighbors) else min(neighbors)
def rank_near_misses(interior:pd.DataFrame)->pd.DataFrame:
    return interior.sort_values(["gate_pass_count","maximin_score","sharpe_win_rate_vs_buy_hold","cagr_win_rate_vs_buy_hold","period"],ascending=[False,False,False,False,True])

def configure(raw,windows,p,cash):
    global _STATE; _STATE={"raw":raw,"windows":windows,"p":p,"cash":cash}

def worker(period:int)->list[dict]:
    s=_STATE; p=s["p"]; spec=TimingSpec("CROSS",cost_bps=float(p["screen_cost_bps"]),periods=(period,),buy_threshold=float(p["buy_threshold"]),sell_threshold=float(p["sell_threshold"])); prepared=prepare_dual_stochrsi_data(s["raw"],spec); out=[]
    for w in s["windows"]:
        ref=run_reference(prepared,spec,analysis_start=w["start"],analysis_end=w["end"],initial_cash=s["cash"]); m=calculate_metrics(ref.daily,ref.orders,ref.trades,initial_cash=s["cash"]); out.append({"case_id":case_id(period),"period":period,"cohort":w["cohort"],"window_id":w["window_id"],"window_start":w["start"].date().isoformat(),"window_end":w["end"].date().isoformat(),**m})
    return out

def select_robust(screen:pd.DataFrame,benchmark:pd.DataFrame,p:dict)->tuple[pd.DataFrame,dict]:
    d=screen.copy(); d["start_year"]=pd.to_datetime(d.window_start).dt.year; d["horizon_years"]=d.cohort.str.removeprefix("H").astype(int); d["cagr_rank"]=d.groupby("window_id").cagr_pct.rank(pct=True,method="average"); d["sharpe_rank"]=d.groupby("window_id").sharpe.rank(pct=True,method="average"); d["joint_rank"]=d[["cagr_rank","sharpe_rank"]].min(axis=1); blocks=p["chronological_blocks"]; d["time_block"]=[next(f"{a}_{b}" for a,b in blocks if a<=y<=b) for y in d.start_year]
    bh=benchmark[["window_id","cagr_pct","sharpe","max_drawdown_pct"]].rename(columns={"cagr_pct":"bh_cagr","sharpe":"bh_sharpe","max_drawdown_pct":"bh_dd"}); d=d.merge(bh,on="window_id",validate="many_to_one"); rows=[]
    for (cid,period),g in d.groupby(["case_id","period"],sort=False):
        hq=g.groupby("horizon_years").joint_rank.quantile(.25); bm=g.groupby(["horizon_years","time_block"]).joint_rank.median(); loo=[g.loc[g.start_year.ne(y),"joint_rank"].quantile(.25) for y in sorted(g.start_year.unique())]
        rows.append({"case_id":cid,"period":period,"worst_horizon_q25_joint_rank":hq.min(),"worst_time_block_median_joint_rank":bm.min(),"leave_one_year_out_q25_joint_rank":min(loo),"cagr_win_rate_vs_buy_hold":(g.cagr_pct>=g.bh_cagr).mean(),"sharpe_win_rate_vs_buy_hold":(g.sharpe>=g.bh_sharpe).mean(),"drawdown_win_rate_vs_buy_hold":(g.max_drawdown_pct>=g.bh_dd).mean(),"median_cagr_pct":g.cagr_pct.median(),"q25_cagr_pct":g.cagr_pct.quantile(.25),"median_sharpe":g.sharpe.median(),"q25_sharpe":g.sharpe.quantile(.25)})
    s=pd.DataFrame(rows); s["base_score"]=s[["worst_horizon_q25_joint_rank","worst_time_block_median_joint_rank","leave_one_year_out_q25_joint_rank"]].min(axis=1); lookup=dict(zip(s.period,s.base_score))
    local_scores=[complete_local_min(r.period,lookup) for r in s.itertuples()]
    s["worst_local_3point_score"]=local_scores
    s["maximin_score"]=[np.nan if pd.isna(local) else min(base,local) for base,local in zip(s.base_score,s.worst_local_3point_score)]
    g=p["gates"]; passed=s.worst_local_3point_score.notna(); gate_checks=[]
    for col,threshold in [("worst_horizon_q25_joint_rank",g["minimum_worst_horizon_q25_joint_rank"]),("worst_time_block_median_joint_rank",g["minimum_worst_time_block_median_joint_rank"]),("leave_one_year_out_q25_joint_rank",g["minimum_leave_one_start_year_out_q25_joint_rank"]),("worst_local_3point_score",g["minimum_worst_local_3point_score"]),("cagr_win_rate_vs_buy_hold",g["minimum_cagr_win_rate_vs_buy_hold"]),("sharpe_win_rate_vs_buy_hold",g["minimum_sharpe_win_rate_vs_buy_hold"]),("drawdown_win_rate_vs_buy_hold",g["minimum_drawdown_win_rate_vs_buy_hold"])]:
        check=s[col].ge(float(threshold)); gate_checks.append(check); passed &= check
    s["gate_pass_count"]=pd.concat(gate_checks,axis=1).sum(axis=1); s["passes_all_gates"]=passed; interior=s[s.worst_local_3point_score.notna()]
    ranked=interior.sort_values(["passes_all_gates","maximin_score","sharpe_win_rate_vs_buy_hold","cagr_win_rate_vs_buy_hold","period"],ascending=[False,False,False,False,True]); champion=ranked[ranked.passes_all_gates].iloc[0].to_dict() if passed.any() else None
    near=rank_near_misses(interior).iloc[0].to_dict()
    return s,{"robust_champion":json_safe(champion),"strongest_near_miss":json_safe(near),"passing_case_count":int(passed.sum())}

def formal_cases(raw,windows,ids,p,cash,cost):
    rows=[]; orders=[]; trades=[]; diffs={}
    for cid in ids:
        period=int(cid[1:]); spec=TimingSpec("CROSS",cost_bps=cost,periods=(period,),buy_threshold=float(p["buy_threshold"]),sell_threshold=float(p["sell_threshold"])); prepared=prepare_dual_stochrsi_data(raw,spec)
        for w in windows:
            m,_,_,o,t,checked=run_case(prepared,spec,start=w["start"],end=w["end"],initial_cash=cash); checked.pop("signal_plans"); rows.append({"case_id":cid,"period":period,"cohort":w["cohort"],"window_id":w["window_id"],"window_start":w["start"].date().isoformat(),"window_end":w["end"].date().isoformat(),**m,**{k:float(v) for k,v in checked.items()}})
            for frame,target in ((o,orders),(t,trades)):
                if not frame.empty:
                    x=frame.copy(); x.insert(0,"case_id",cid); x.insert(1,"window_id",w["window_id"]); target.append(x)
            for k,v in checked.items(): diffs[k]=max(diffs.get(k,0.),float(v))
    return pd.DataFrame(rows),orders,trades,diffs

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--experiment",type=Path,default=DEFAULT); ap.add_argument("--run-id",required=True); ap.add_argument("--cost-bps",type=float,required=True); ap.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1)); a=ap.parse_args(); c=load_experiment(a.experiment); cost=float(a.cost_bps)
    if cost not in {0.,5.,10.}: raise ValueError("Frozen costs are 0/5/10 bps")
    out=reserve_block(c,a.run_id,"QQQ",cost); p=c.config["parameters"]; cash=float(c.config["initial_cash"]); source=WORKSPACE/"data/processed/daily/QQQ.csv"
    canonical_manifest=json.loads((WORKSPACE/"data/processed/manifest.json").read_text(encoding="utf-8")); dataset=next(item for item in canonical_manifest["datasets"] if item["symbol"]=="QQQ")
    if dataset["effective_status"]!="approved": raise RuntimeError("QQQ data is not approved")
    raw=pd.read_csv(source,parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True); windows=build_windows(raw,p); started=time.perf_counter(); selection_path=c.run_root(a.run_id)/"QQQ/cost_5bps/selection.json"
    benchmark_rows=[]
    for w in windows:
        _,_,m=buy_hold(raw[raw.date.between(w["start"],w["end"])].reset_index(drop=True),initial_cash=cash,cost_bps=cost); benchmark_rows.append({"cohort":w["cohort"],"window_id":w["window_id"],"horizon_years":w["horizon_years"],"window_start":w["start"].date().isoformat(),"window_end":w["end"].date().isoformat(),**m})
    benchmark=pd.DataFrame(benchmark_rows); normalize_frame(benchmark).to_csv(out/"buy_hold_results.csv",index=False,lineterminator="\n")
    if cost==5.:
        periods=period_values(p["period_start"],p["period_end"],p["period_step"]); configure(raw,windows,p,cash); ex=concurrent.futures.ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context("fork")) if a.workers>1 else None
        try: screen=pd.DataFrame([row for batch in (ex.map(worker,periods,chunksize=1) if ex else map(worker,periods)) for row in batch])
        finally:
            if ex: ex.shutdown(wait=True,cancel_futures=True)
        surface,selection=select_robust(screen,benchmark,p); normalize_frame(screen).to_csv(out/"screen.csv",index=False,lineterminator="\n"); normalize_frame(surface).to_csv(out/"surface.csv",index=False,lineterminator="\n"); selection_path.write_text(json.dumps(selection,ensure_ascii=False,indent=2)+"\n")
    else: selection=json.loads(selection_path.read_text())
    chosen=(selection["robust_champion"] or selection["strongest_near_miss"])["case_id"]; ids=list(dict.fromkeys([chosen,*[case_id(x) for x in p["reference_periods"]]])); formal,orders,trades,diffs=formal_cases(raw,windows,ids,p,cash,cost); normalize_frame(formal).to_csv(out/"formal_results.csv",index=False,lineterminator="\n"); normalize_frame(pd.concat(orders,ignore_index=True) if orders else pd.DataFrame()).to_csv(out/"formal_orders.csv",index=False,lineterminator="\n"); normalize_frame(pd.concat(trades,ignore_index=True) if trades else pd.DataFrame()).to_csv(out/"formal_trades.csv",index=False,lineterminator="\n")
    summary={"training_window_count":len(windows),"screen_case_windows":len(windows)*int(p["combination_count"]) if cost==5 else 0,"selected_case_ids":ids,"selection":selection,"max_cross_check_differences":diffs,"elapsed_seconds":time.perf_counter()-started}; (out/"metrics.json").write_text(json.dumps(json_safe(summary),ensure_ascii=False,indent=2)+"\n"); manifest={"schema_version":1,"experiment_id":c.config["experiment_id"],"experiment_run_id":a.run_id,"completed_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"symbol":"QQQ","cost_bps":cost,"python":platform.python_version(),"source_file_sha256":sha256(source),"source_manifest_build_id":canonical_manifest["build_id"],"source_manifest_entry":dataset,"summary":summary,"artifacts":{}}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!="manifest.json": manifest["artifacts"][path.name]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    mp=out/"manifest.json"; mp.write_text(json.dumps(json_safe(manifest),ensure_ascii=False,indent=2)+"\n"); record_block_complete(c,a.run_id,"QQQ",cost,mp); print(json.dumps({"cost":cost,"seconds":summary["elapsed_seconds"],"selection":selection},ensure_ascii=False))

if __name__=="__main__": main()
