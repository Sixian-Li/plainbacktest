#!/usr/bin/env python3
"""Run the frozen 3/5/7-year QQQ StochRSI robustness suite."""

from __future__ import annotations

import argparse, concurrent.futures, json, multiprocessing, os, platform, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

from quantkit.experiment import load_experiment, record_block_complete, reserve_block, sha256
from scripts.analyze_stochrsi_cross_threshold_grid import json_safe
from scripts.run_dual_stochrsi_timing import buy_hold
from scripts.run_intraday_sma_backtest import normalize_frame
from scripts.run_stochrsi_cross_period_grid import case_id, period_values
from scripts.run_stochrsi_multistart_robustness import configure_screen, formal_cases, screen_worker

BACKTEST_ROOT=Path(__file__).resolve().parents[1]; WORKSPACE_ROOT=BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT=BACKTEST_ROOT/"experiments/TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness"


def build_windows(raw: pd.DataFrame, parameters: dict) -> list[dict]:
    sessions=pd.DatetimeIndex(raw["date"]); first=pd.Timestamp(parameters["research_start"]); out=[]
    for years in map(int,parameters["horizons_years"]):
        last=pd.Timestamp(parameters["horizon_last_start"][str(years)])
        for quarter in pd.period_range(first,last,freq="Q"):
            candidates=sessions[(sessions>=quarter.start_time)&(sessions<=quarter.end_time)]
            if candidates.empty: raise RuntimeError(f"No session in {quarter}")
            start=candidates[0]; eligible=sessions[sessions < start+pd.DateOffset(years=years)]
            if eligible.empty: raise RuntimeError(f"No end for {years}Y {quarter}")
            out.append({"cohort":f"H{years}","horizon_years":years,"window_id":f"H{years}_{quarter.year}Q{quarter.quarter}","start":start,"end":eligible[-1]})
        actual=sum(w["horizon_years"]==years for w in out)
        if actual != int(parameters["expected_windows"][str(years)]): raise RuntimeError(f"Expected {parameters['expected_windows'][str(years)]}, got {actual}")
    return out


def build_holdout_window(raw: pd.DataFrame, parameters: dict) -> list[dict]:
    if not parameters.get("holdout_start"):
        return []
    sessions=pd.DatetimeIndex(raw["date"])
    start_candidates=sessions[sessions>=pd.Timestamp(parameters["holdout_start"])]
    end_candidates=sessions[sessions<=pd.Timestamp(parameters["holdout_end"])]
    if start_candidates.empty or end_candidates.empty: raise RuntimeError("Holdout boundary is outside approved data")
    start,end=start_candidates[0],end_candidates[-1]
    if start>end: raise RuntimeError("Holdout start follows holdout end")
    return [{"cohort":"HOLDOUT","horizon_years":0,"window_id":"HOLDOUT_2021_2026","start":start,"end":end}]


def score_surface(screen: pd.DataFrame, benchmark: pd.DataFrame, p: dict) -> tuple[pd.DataFrame,dict]:
    d=screen.copy(); d["start_year"]=pd.to_datetime(d.window_start).dt.year
    d["horizon_years"]=d["cohort"].str.removeprefix("H").astype(int)
    d["cagr_rank"]=d.groupby("window_id").cagr_pct.rank(pct=True,method="average")
    d["sharpe_rank"]=d.groupby("window_id").sharpe.rank(pct=True,method="average")
    d["joint_rank"]=d[["cagr_rank","sharpe_rank"]].min(axis=1)
    blocks=p["chronological_blocks"]
    d["time_block"]=[next(f"{a}_{b}" for a,b in blocks if a<=y<=b) for y in d.start_year]
    bh=benchmark[["window_id","cagr_pct","sharpe","max_drawdown_pct"]].rename(columns={"cagr_pct":"bh_cagr","sharpe":"bh_sharpe","max_drawdown_pct":"bh_dd"})
    d=d.merge(bh,on="window_id",validate="many_to_one")
    base=[]
    for keys,g in d.groupby(["case_id","short_period","long_period"],sort=False):
        horizon_q25=g.groupby("horizon_years").joint_rank.quantile(.25)
        block_median=g.groupby(["horizon_years","time_block"]).joint_rank.median()
        loo=[]
        for year in sorted(g.start_year.unique()): loo.append(g.loc[g.start_year.ne(year),"joint_rank"].quantile(.25))
        base.append({"case_id":keys[0],"short_period":keys[1],"long_period":keys[2],
          "worst_horizon_q25_joint_rank":horizon_q25.min(),"worst_time_block_median_joint_rank":block_median.min(),
          "leave_one_year_out_q25_joint_rank":min(loo),"cagr_win_rate_vs_buy_hold":(g.cagr_pct>=g.bh_cagr).mean(),
          "sharpe_win_rate_vs_buy_hold":(g.sharpe>=g.bh_sharpe).mean(),"drawdown_win_rate_vs_buy_hold":(g.max_drawdown_pct>=g.bh_dd).mean(),
          "median_cagr_pct":g.cagr_pct.median(),"q25_cagr_pct":g.cagr_pct.quantile(.25),"median_sharpe":g.sharpe.median(),"q25_sharpe":g.sharpe.quantile(.25)})
    s=pd.DataFrame(base); lookup={(r.short_period,r.long_period):min(r.worst_horizon_q25_joint_rank,r.worst_time_block_median_joint_rank,r.leave_one_year_out_q25_joint_rank) for r in s.itertuples()}
    step=int(p["period_step"]); local=[]
    for r in s.itertuples():
        values=[lookup.get((r.short_period+di*step,r.long_period+dj*step),np.nan) for di in (-1,0,1) for dj in (-1,0,1)]
        local.append(float(np.nanmin(values)) if not any(np.isnan(values)) else np.nan)
    s["worst_local_3x3_score"]=local
    s["maximin_score"]=s[["worst_horizon_q25_joint_rank","worst_time_block_median_joint_rank","leave_one_year_out_q25_joint_rank","worst_local_3x3_score"]].min(axis=1)
    gates=p["gates"]; passed=s.worst_local_3x3_score.notna(); gate_checks=[]
    for column,threshold in [("worst_horizon_q25_joint_rank",gates["minimum_worst_horizon_q25_joint_rank"]),("worst_time_block_median_joint_rank",gates["minimum_worst_time_block_median_joint_rank"]),("leave_one_year_out_q25_joint_rank",gates["minimum_leave_one_start_year_out_q25_joint_rank"]),("worst_local_3x3_score",gates["minimum_worst_local_3x3_score"]),("cagr_win_rate_vs_buy_hold",gates["minimum_cagr_win_rate_vs_buy_hold"]),("sharpe_win_rate_vs_buy_hold",gates["minimum_sharpe_win_rate_vs_buy_hold"]),("drawdown_win_rate_vs_buy_hold",gates["minimum_drawdown_win_rate_vs_buy_hold"])]: passed &= s[column].ge(float(threshold))
    for column,threshold in [("worst_horizon_q25_joint_rank",gates["minimum_worst_horizon_q25_joint_rank"]),("worst_time_block_median_joint_rank",gates["minimum_worst_time_block_median_joint_rank"]),("leave_one_year_out_q25_joint_rank",gates["minimum_leave_one_start_year_out_q25_joint_rank"]),("worst_local_3x3_score",gates["minimum_worst_local_3x3_score"]),("cagr_win_rate_vs_buy_hold",gates["minimum_cagr_win_rate_vs_buy_hold"]),("sharpe_win_rate_vs_buy_hold",gates["minimum_sharpe_win_rate_vs_buy_hold"]),("drawdown_win_rate_vs_buy_hold",gates["minimum_drawdown_win_rate_vs_buy_hold"])]: gate_checks.append(s[column].ge(float(threshold)))
    checks=pd.concat(gate_checks,axis=1); passed &= checks.all(axis=1); s["gate_pass_count"]=checks.sum(axis=1); s["passes_all_gates"]=passed
    ranked=s.sort_values(["passes_all_gates","maximin_score","sharpe_win_rate_vs_buy_hold","cagr_win_rate_vs_buy_hold","case_id"],ascending=[False,False,False,False,True])
    champion=ranked[ranked.passes_all_gates].iloc[0].to_dict() if passed.any() else None
    interior=s[s.worst_local_3x3_score.notna()]; near=interior.sort_values(["gate_pass_count","maximin_score","sharpe_win_rate_vs_buy_hold","cagr_win_rate_vs_buy_hold","case_id"],ascending=[False,False,False,False,True]).iloc[0].to_dict()
    return s,{"robust_champion":json_safe(champion),"strongest_near_miss":json_safe(near),"passing_case_count":int(passed.sum())}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--experiment",type=Path,default=DEFAULT_EXPERIMENT); ap.add_argument("--run-id",required=True); ap.add_argument("--cost-bps",type=float,required=True); ap.add_argument("--workers",type=int,default=min(8,os.cpu_count() or 1)); a=ap.parse_args()
    c=load_experiment(a.experiment); cost=float(a.cost_bps)
    if cost not in {0.,5.,10.}: raise ValueError("Frozen costs are 0/5/10 bps")
    out=reserve_block(c,a.run_id,"QQQ",cost); p=c.config["parameters"]; cash=float(c.config["initial_cash"])
    source=WORKSPACE_ROOT/"data/processed/daily/QQQ.csv"; canonical_manifest=json.loads((WORKSPACE_ROOT/"data/processed/manifest.json").read_text()); dataset=next(item for item in canonical_manifest["datasets"] if item["symbol"]=="QQQ")
    if dataset["effective_status"]!="approved": raise RuntimeError("QQQ data is not approved")
    raw=pd.read_csv(source,parse_dates=["date"]); raw=raw[raw.symbol.eq("QQQ")].sort_values("date").reset_index(drop=True); windows=build_windows(raw,p); holdout=build_holdout_window(raw,p); formal_windows=windows+holdout; start=time.perf_counter()
    selection_path=c.run_root(a.run_id)/"QQQ/cost_5bps/selection.json"
    if cost==5.:
        pairs=[(x,y) for x in period_values(p["short_period_start"],p["short_period_end"],p["period_step"]) for y in period_values(p["long_period_start"],p["long_period_end"],p["period_step"])]
        configure_screen(raw,windows,p,cash); ex=concurrent.futures.ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context("fork")) if a.workers>1 else None
        try: screen=pd.DataFrame([row for batch in (ex.map(screen_worker,pairs,chunksize=1) if ex else map(screen_worker,pairs)) for row in batch])
        finally:
            if ex: ex.shutdown(wait=True,cancel_futures=True)
        benchmark=[]
        for w in windows:
            _,_,m=buy_hold(raw[raw.date.between(w["start"],w["end"])].reset_index(drop=True),initial_cash=cash,cost_bps=cost); benchmark.append({"window_id":w["window_id"],"horizon_years":w["horizon_years"],**m})
        benchmark=pd.DataFrame(benchmark); surface,selection=score_surface(screen,benchmark,p)
        normalize_frame(screen).to_csv(out/"screen.csv",index=False,lineterminator="\n"); normalize_frame(surface).to_csv(out/"surface.csv",index=False,lineterminator="\n"); selection_path.write_text(json.dumps(selection,ensure_ascii=False,indent=2)+"\n")
    else:
        selection=json.loads(selection_path.read_text())
    ids=[case_id(int(pair[0]),int(pair[1])) for pair in p.get("reference_pairs",[[42,100]])]; ids.append(str((selection["robust_champion"] or selection["strongest_near_miss"])["case_id"]))
    ids=list(dict.fromkeys(ids)); formal,orders,trades,diffs=formal_cases(raw,formal_windows,ids,p,cash,cost)
    normalize_frame(formal).to_csv(out/"formal_results.csv",index=False,lineterminator="\n"); normalize_frame(pd.concat(orders,ignore_index=True) if orders else pd.DataFrame()).to_csv(out/"formal_orders.csv",index=False,lineterminator="\n"); normalize_frame(pd.concat(trades,ignore_index=True) if trades else pd.DataFrame()).to_csv(out/"formal_trades.csv",index=False,lineterminator="\n")
    benchmark_rows=[]
    for w in formal_windows:
        _,_,m=buy_hold(raw[raw.date.between(w["start"],w["end"])].reset_index(drop=True),initial_cash=cash,cost_bps=cost); benchmark_rows.append({"cohort":w["cohort"],"window_id":w["window_id"],"horizon_years":w["horizon_years"],"window_start":w["start"].date().isoformat(),"window_end":w["end"].date().isoformat(),**m})
    normalize_frame(pd.DataFrame(benchmark_rows)).to_csv(out/"buy_hold_results.csv",index=False,lineterminator="\n")
    summary={"training_window_count":len(windows),"holdout_window_count":len(holdout),"screen_case_windows":len(windows)*int(p["combination_count"]) if cost==5 else 0,"selected_case_ids":ids,"selection":selection,"max_cross_check_differences":diffs,"elapsed_seconds":time.perf_counter()-start}
    (out/"metrics.json").write_text(json.dumps(json_safe(summary),ensure_ascii=False,indent=2)+"\n")
    manifest={"schema_version":1,"experiment_id":c.config["experiment_id"],"experiment_run_id":a.run_id,"completed_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"symbol":"QQQ","cost_bps":cost,"python":platform.python_version(),"source_file_sha256":sha256(source),"source_manifest_build_id":canonical_manifest["build_id"],"source_manifest_entry":dataset,"summary":summary,"artifacts":{}}
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name!="manifest.json": manifest["artifacts"][path.name]={"bytes":path.stat().st_size,"sha256":sha256(path)}
    mp=out/"manifest.json"; mp.write_text(json.dumps(json_safe(manifest),ensure_ascii=False,indent=2)+"\n"); record_block_complete(c,a.run_id,"QQQ",cost,mp); print(json.dumps({"cost":cost,"seconds":summary["elapsed_seconds"],"selection":selection},ensure_ascii=False))

if __name__=="__main__": main()
