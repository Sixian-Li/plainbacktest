#!/usr/bin/env python3
"""Build a current-constituent bear-market resilience event study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


VIEW_ID = "bear_market_resilience_current_constituents_v1"
SCHEMA_VERSION = 1
AVAILABLE = "available"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "true", "yes"}


def _close_series(frame: pd.DataFrame, *, date_column: str, close_column: str) -> pd.Series:
    data = frame[[date_column, close_column]].copy()
    data[date_column] = pd.to_datetime(data[date_column], errors="raise")
    data[close_column] = pd.to_numeric(data[close_column], errors="raise")
    data = data.sort_values(date_column)
    if data.empty or data[date_column].duplicated().any():
        raise ValueError("price series is empty or has duplicate dates")
    if data[close_column].isna().any() or (data[close_column] <= 0).any():
        raise ValueError("close must be positive and non-null")
    return data.set_index(date_column)[close_column].astype(float)


def load_canonical_price(path: Path) -> pd.Series:
    return _close_series(pd.read_csv(path, usecols=["date", "close"]), date_column="date", close_column="close")


def load_vendor_price(path: Path) -> pd.Series:
    return _close_series(pd.read_csv(path, usecols=["Date", "Close"]), date_column="Date", close_column="Close")


def load_sp500_current(
    security_master_path: Path,
    current_constituents_path: Path,
    price_directory: Path,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, pd.Series]]:
    official = pd.read_csv(current_constituents_path, dtype=str).fillna("")
    if official.empty or official["as_of_date"].nunique() != 1:
        raise ValueError("S&P 500 current snapshot must contain exactly one as-of date")
    snapshot = str(official["as_of_date"].iloc[0])
    master = pd.read_csv(security_master_path, dtype=str).fillna("")
    master_by_id = {row["instrument_id"]: row for row in master.to_dict("records")}
    if official["symbol"].duplicated().any():
        raise ValueError("S&P 500 official snapshot contains duplicate symbols")
    members: dict[str, dict[str, Any]] = {}
    prices: dict[str, pd.Series] = {}
    for row in official.to_dict("records"):
        asset_id = row["vendor_instrument_id"] or row["symbol"]
        source = price_directory / f"{asset_id}.csv"
        price_available = _truthy(row["vendor_price_available"])
        if price_available and not source.exists():
            raise ValueError(f"Missing S&P 500 price file for {asset_id}: {source}")
        if price_available and asset_id not in master_by_id:
            raise ValueError(f"S&P 500 official link {asset_id} is absent from security master")
        members[asset_id] = {
            "asset_id": asset_id,
            "symbol": row["symbol"],
            "company_name": row["company_name"],
            "role": "equity",
            "sp500_current": True,
            "nasdaq100_current": False,
            "price_source": str(source) if price_available else "unavailable",
        }
        prices[asset_id] = load_canonical_price(source) if price_available else pd.Series(dtype=float)
    return snapshot, members, prices


def load_nasdaq100_current(
    archive_path: Path,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, pd.Series]]:
    records: dict[str, tuple[dict[str, str], pd.DataFrame]] = {}
    snapshot = ""
    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not names:
            raise ValueError("Nasdaq-100 archive contains no CSV members")
        for name in names:
            with archive.open(name) as raw:
                frame = pd.read_csv(io.TextIOWrapper(raw, encoding="utf-8-sig"), dtype=str).fillna("")
            required = {"Date", "CompanyName", "Symbol", "Close", "InIndex"}
            if missing := required.difference(frame.columns):
                raise ValueError(f"Nasdaq-100 archive member {name} misses {sorted(missing)}")
            member_latest = str(frame["Date"].max())
            snapshot = max(snapshot, member_latest)
            records[Path(name).stem] = (frame.iloc[-1].to_dict(), frame)

    members: dict[str, dict[str, Any]] = {}
    prices: dict[str, pd.Series] = {}
    for asset_id, (_, frame) in records.items():
        snapshot_rows = frame.loc[frame["Date"] == snapshot]
        if snapshot_rows.empty or not snapshot_rows["InIndex"].map(_truthy).any():
            continue
        if len(snapshot_rows) != 1:
            raise ValueError(f"Nasdaq-100 {asset_id} has duplicate rows on {snapshot}")
        row = snapshot_rows.iloc[0]
        members[asset_id] = {
            "asset_id": asset_id,
            "symbol": str(row["Symbol"]),
            "company_name": str(row["CompanyName"]),
            "role": "equity",
            "sp500_current": False,
            "nasdaq100_current": True,
            "price_source": f"{archive_path}!{asset_id}.csv",
        }
        prices[asset_id] = _close_series(frame, date_column="Date", close_column="Close")
    if not members:
        raise ValueError(f"No Nasdaq-100 members are active on latest common snapshot {snapshot}")
    return snapshot, members, prices


def merge_current_universes(
    sp500_members: dict[str, dict[str, Any]],
    sp500_prices: dict[str, pd.Series],
    nasdaq_members: dict[str, dict[str, Any]],
    nasdaq_prices: dict[str, pd.Series],
) -> tuple[dict[str, dict[str, Any]], dict[str, pd.Series]]:
    members = {asset_id: dict(row) for asset_id, row in sp500_members.items()}
    prices = dict(sp500_prices)
    for asset_id, row in nasdaq_members.items():
        if asset_id in members:
            members[asset_id]["nasdaq100_current"] = True
            members[asset_id]["price_source"] = (
                f"{members[asset_id]['price_source']} | Nasdaq mirror: {row['price_source']}"
            )
        else:
            members[asset_id] = dict(row)
            prices[asset_id] = nasdaq_prices[asset_id]
    for row in members.values():
        row["universe"] = (
            "Both"
            if row["sp500_current"] and row["nasdaq100_current"]
            else "S&P 500"
            if row["sp500_current"]
            else "Nasdaq-100"
        )
    return members, prices


def calculate_interval_result(series: pd.Series, interval: dict[str, Any]) -> dict[str, Any]:
    start = pd.Timestamp(interval["start"])
    end = pd.Timestamp(interval["end"])
    if start > end:
        raise ValueError("bear interval starts after it ends")
    if series.empty:
        return {"status": "no_price_data", "total_return": None, "max_drawdown": None}
    if start < series.index[0]:
        return {"status": "not_yet_listed", "total_return": None, "max_drawdown": None}
    if end > series.index[-1]:
        return {"status": "ended_before_interval_end", "total_return": None, "max_drawdown": None}
    if start not in series.index or end not in series.index:
        return {"status": "missing_endpoint", "total_return": None, "max_drawdown": None}
    values = series.loc[start:end]
    if values.empty:
        return {"status": "missing_interval", "total_return": None, "max_drawdown": None}
    drawdowns = values / values.cummax() - 1
    return {
        "status": AVAILABLE,
        "start_close": float(values.iloc[0]),
        "end_close": float(values.iloc[-1]),
        "total_return": float(values.iloc[-1] / values.iloc[0] - 1),
        "max_drawdown": float(drawdowns.min()),
        "trading_sessions": int(len(values)),
    }


def _compound(values: Iterable[float | None]) -> float | None:
    available = [float(value) for value in values if value is not None]
    if not available:
        return None
    return math.prod(1 + value for value in available) - 1


def summarize_asset(
    *,
    asset: dict[str, Any],
    interval_results: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
) -> dict[str, Any]:
    interval_by_id = {item["interval_id"]: item for item in intervals}
    available = [row for row in interval_results if row["status"] == AVAILABLE]
    positives = [row for row in available if row["total_return"] > 0]
    major = [row for row in available if interval_by_id[row["interval_id"]]["severity"] == "major"]
    minor = [row for row in available if interval_by_id[row["interval_id"]]["severity"] == "minor"]
    all_compound = _compound(row["total_return"] for row in available)
    worst = min(available, key=lambda row: row["total_return"]) if available else None
    output = dict(asset)
    output.update(
        {
            "available_interval_count": len(available),
            "positive_interval_count": len(positives),
            "positive_interval_rate": len(positives) / len(available) if available else None,
            "major_available_count": len(major),
            "minor_available_count": len(minor),
            "major_compound_return": _compound(row["total_return"] for row in major),
            "minor_compound_return": _compound(row["total_return"] for row in minor),
            "all_compound_return": all_compound,
            "passes_screen": bool(positives and all_compound is not None and all_compound > 0),
            "worst_single_bear_return": worst["total_return"] if worst else None,
            "worst_interval_label": worst["label"] if worst else None,
        }
    )
    return output


def run_study(
    *,
    intervals: list[dict[str, Any]],
    members: dict[str, dict[str, Any]],
    prices: dict[str, pd.Series],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for asset_id in sorted(members):
        asset = members[asset_id]
        asset_details = []
        for interval in intervals:
            metrics = calculate_interval_result(prices[asset_id], interval)
            row = {
                "asset_id": asset_id,
                "symbol": asset["symbol"],
                "company_name": asset.get("company_name", ""),
                "role": asset["role"],
                "universe": asset.get("universe", "Reference"),
                "sp500_current": asset.get("sp500_current", False),
                "nasdaq100_current": asset.get("nasdaq100_current", False),
                "interval_id": interval["interval_id"],
                "ordinal": interval["ordinal"],
                "label": interval["label"],
                "severity": interval["severity"],
                "start": interval["start"],
                "end": interval["end"],
                "spy_return": interval["SPY"]["total_return"],
                "qqq_return": interval["QQQ"]["total_return"],
                **metrics,
            }
            if metrics["status"] == AVAILABLE:
                row["excess_vs_spy"] = metrics["total_return"] - row["spy_return"]
                row["excess_vs_qqq"] = metrics["total_return"] - row["qqq_return"]
            else:
                row["excess_vs_spy"] = None
                row["excess_vs_qqq"] = None
            asset_details.append(row)
            details.append(row)
        summaries.append(summarize_asset(asset=asset, interval_results=asset_details, intervals=intervals))
    summaries.sort(
        key=lambda row: (
            not row["passes_screen"],
            -(row["all_compound_return"] if row["all_compound_return"] is not None else -math.inf),
            row["symbol"],
        )
    )
    return summaries, details


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def build_html(
    *,
    summaries: list[dict[str, Any]],
    details: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    metadata: dict[str, Any],
    summary_csv_name: str,
    detail_csv_name: str,
) -> str:
    payload = json.dumps(
        _json_safe({"summaries": summaries, "details": details, "intervals": intervals, "metadata": metadata}),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    counts = metadata.get("counts", {})
    analyzed = counts.get("analyzed_assets", len(summaries))
    screened = counts.get("screened_assets", sum(bool(row.get("passes_screen")) for row in summaries))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}"><title>当前成分股熊市逆势研究</title>
<style>
:root{{--ink:#17212b;--muted:#64748b;--line:#dbe3eb;--bg:#eef2f6;--green:#067647;--red:#b42318;--amber:#b54708}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1680px;margin:auto;padding:24px}}h1{{margin:0 0 8px}}h2{{margin:0 0 12px}}p{{line-height:1.55}}.muted{{color:var(--muted)}}.warning{{background:#fff5d6;border-left:5px solid #d69e2e;padding:14px 18px;margin:18px 0}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px}}.card,.panel{{background:#fff;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(15,23,42,.08)}}.metric strong{{display:block;font-size:28px;margin-top:4px}}.controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:18px 0}}select,input,button{{font:inherit;padding:8px 10px;border:1px solid #b8c5d1;border-radius:7px;background:white}}button{{cursor:pointer}}.check{{display:flex;align-items:center;gap:6px;background:white;padding:8px 10px;border:1px solid #b8c5d1;border-radius:7px}}.table-wrap{{overflow:auto;max-height:660px;border:1px solid var(--line);border-radius:9px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{position:sticky;top:0;background:#f8fafc;z-index:2;cursor:pointer}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){{text-align:left}}tbody tr{{cursor:pointer}}tbody tr:hover,tbody tr.selected{{background:#edf6ff}}.pass{{color:var(--green);font-weight:700}}.risk{{color:var(--red);font-weight:650}}.na{{color:#94a3b8}}.bars{{display:grid;gap:8px;margin-top:15px}}.bar-row{{display:grid;grid-template-columns:116px 1fr 92px 92px;align-items:center;gap:8px}}.bar-track{{position:relative;height:20px;background:#f1f5f9;border-radius:5px;overflow:hidden}}.bar-zero{{position:absolute;left:50%;height:100%;border-left:1px solid #64748b}}.bar{{position:absolute;top:3px;height:14px;border-radius:4px}}.bar.pos{{left:50%;background:#12b76a}}.bar.neg{{right:50%;background:#f04438}}.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted)}}.pill{{border-radius:999px;padding:3px 8px;background:#eef2f6}}.foot{{font-size:13px;color:var(--muted)}}@media(max-width:900px){{main{{padding:12px}}.cards{{grid-template-columns:repeat(2,1fr)}}.bar-row{{grid-template-columns:92px 1fr 72px}}.bar-row .bench{{display:none}}}}
</style></head><body><main>
<h1>当前成分股熊市逆势研究</h1>
<p class="muted">S&amp;P 500 + Nasdaq-100 当前成分股去重，并加入 TLT、GLD、SPY、QQQ 参考标的；按 12 段主观峰值→谷底熊市计算复权收盘收益。</p>
<div class="warning"><strong>回顾性事件研究，不是交易回测。</strong> 当前成分股存在幸存者偏差。筛选只要求“至少一段熊市上涨”且“所有有数据熊市的复合总收益为正”；最差单次熊市收益仅作风险提示，不参与筛选。</div>
<section class="cards">
 <div class="card metric"><span>分析标的</span><strong>{analyzed}</strong></div>
 <div class="card metric"><span>通过筛选</span><strong>{screened}</strong></div>
 <div class="card metric"><span>熊市区间</span><strong>{len(intervals)}</strong></div>
 <div class="card metric"><span>大小熊市</span><strong>{sum(x['severity']=='major' for x in intervals)} / {sum(x['severity']=='minor' for x in intervals)}</strong></div>
</section>
<div class="controls">
 <label class="check"><input type="checkbox" data-control="screened-only" checked>只看纳入考虑范围</label>
 <label>股票池 <select data-control="universe"><option value="all">全部</option><option value="S&P 500">S&amp;P 500</option><option value="Nasdaq-100">Nasdaq-100</option><option value="Both">两者重合</option><option value="Reference">参考 ETF</option></select></label>
 <label>最低覆盖 <select data-control="coverage"><option value="0">不限</option><option value="12">12/12</option><option value="10">至少 10</option><option value="6">至少 6</option></select></label>
 <label>搜索 <input data-control="search" placeholder="代码或公司名"></label>
 <button data-action="reset">重置</button>
 <a href="{html.escape(summary_csv_name)}" download>下载汇总 CSV</a>
 <a href="{html.escape(detail_csv_name)}" download>下载明细 CSV</a>
 <span class="muted" data-role="visible-count"></span>
</div>
<section class="panel"><h2>标的汇总</h2><div class="table-wrap"><table><thead><tr>
<th data-sort="symbol">代码</th><th data-sort="company_name">名称</th><th data-sort="universe">股票池</th><th data-sort="passes_screen">筛选</th><th data-sort="available_interval_count">覆盖</th><th data-sort="positive_interval_count">上涨次数</th><th data-sort="major_compound_return">大总</th><th data-sort="minor_compound_return">小总</th><th data-sort="all_compound_return">总收益</th><th data-sort="worst_single_bear_return">最差单次熊市收益</th><th data-sort="worst_interval_label">最差区间</th>
</tr></thead><tbody data-role="summary-body"></tbody></table></div></section>
<section class="panel"><h2 data-role="detail-title">分段收益</h2><div class="legend"><span class="pill">绿色：上涨</span><span class="pill">红色：下跌</span><span>右侧同时显示 SPY / QQQ</span></div><div id="detail-bars" class="bars"></div></section>
<section class="panel foot"><h2>口径与限制</h2><ul>
<li>每段收益为复权 Close：结束价 ÷ 开始价 − 1；大总、小总、总收益只把相应熊市区间复合，不包含熊市之间的持有期。</li>
<li>尚未上市或缺少任一端点的区间记为 N/A，不当作 0；总收益按该标的实际可用区间计算，所以比较时必须同时看覆盖数。</li>
<li>S&amp;P 500 使用 2026-08-11 官方 SPY 持仓；Nasdaq-100 使用 2026-08-04 最新供应商快照。FERG 因暂无批准行情保留为全 N/A。指数可能含多类别股份，因此数量可超过 500/100。</li>
<li>复权价格是含股息总回报的近似，不是独立现金分红账本；未来历史成分股版本将替换股票池模块以降低幸存者偏差。</li>
</ul></section>
<script type="application/json" id="study-data">{payload}</script>
<script>
(function(){{'use strict';
const data=JSON.parse(document.getElementById('study-data').textContent);let sortKey='all_compound_return',sortAsc=false,selected=null;
const body=document.querySelector('[data-role="summary-body"]'),bars=document.getElementById('detail-bars');
const pct=v=>v==null?'N/A':(v*100).toFixed(2)+'%';const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function universeMatch(row,value){{if(value==='all')return true;if(value==='Reference')return row.role!=='equity';if(value==='Both')return row.universe==='Both';if(value==='S&P 500')return Boolean(row.sp500_current);if(value==='Nasdaq-100')return Boolean(row.nasdaq100_current);return true}}
function filtered(){{const only=document.querySelector('[data-control="screened-only"]').checked,u=document.querySelector('[data-control="universe"]').value,min=Number(document.querySelector('[data-control="coverage"]').value),q=document.querySelector('[data-control="search"]').value.trim().toLowerCase();return data.summaries.filter(r=>(!only||r.passes_screen)&&universeMatch(r,u)&&r.available_interval_count>=min&&(!q||(r.symbol+' '+r.company_name).toLowerCase().includes(q))).sort((a,b)=>{{let av=a[sortKey],bv=b[sortKey];if(av==null&&bv==null)return a.symbol.localeCompare(b.symbol);if(av==null)return 1;if(bv==null)return -1;let c=typeof av==='string'?av.localeCompare(bv):Number(av)-Number(bv);return sortAsc?c:-c}})}}
function renderTable(){{const rows=filtered();document.querySelector('[data-role="visible-count"]').textContent='显示 '+rows.length+' / '+data.summaries.length;body.innerHTML=rows.map(r=>`<tr data-id="${{esc(r.asset_id)}}" class="${{r.asset_id===selected?'selected':''}}"><td><strong>${{esc(r.symbol)}}</strong></td><td>${{esc(r.company_name)}}</td><td>${{esc(r.universe)}}</td><td class="${{r.passes_screen?'pass':''}}">${{r.passes_screen?'纳入':'—'}}</td><td>${{r.available_interval_count}}/${{data.intervals.length}}</td><td>${{r.positive_interval_count}}</td><td>${{pct(r.major_compound_return)}}</td><td>${{pct(r.minor_compound_return)}}</td><td class="${{r.all_compound_return>0?'pass':''}}">${{pct(r.all_compound_return)}}</td><td class="risk">${{pct(r.worst_single_bear_return)}}</td><td>${{esc(r.worst_interval_label||'N/A')}}</td></tr>`).join('');body.querySelectorAll('tr').forEach(tr=>tr.onclick=()=>select(tr.dataset.id));if(rows.length&&!rows.some(r=>r.asset_id===selected))select(rows[0].asset_id,false);if(!rows.length){{bars.innerHTML='<p class="na">当前筛选没有标的。</p>';document.querySelector('[data-role="detail-title"]').textContent='分段收益'}}}}
function select(id,rerender=true){{selected=id;const s=data.summaries.find(r=>r.asset_id===id);if(!s)return;document.querySelector('[data-role="detail-title"]').textContent=s.symbol+' · '+s.company_name+' · 分段收益';const rows=data.details.filter(r=>r.asset_id===id).sort((a,b)=>a.ordinal-b.ordinal);const vals=rows.flatMap(r=>[r.total_return,r.spy_return,r.qqq_return]).filter(Number.isFinite);const scale=Math.max(.01,...vals.map(Math.abs));bars.innerHTML=rows.map(r=>{{if(r.total_return==null)return `<div class="bar-row"><span>${{esc(r.label)}}</span><div class="na">${{esc(r.status)}}</div><strong class="na">N/A</strong><span class="bench">SPY ${{pct(r.spy_return)}} / QQQ ${{pct(r.qqq_return)}}</span></div>`;const width=Math.min(50,Math.abs(r.total_return)/scale*50);return `<div class="bar-row"><span>${{esc(r.label)}}</span><div class="bar-track"><span class="bar-zero"></span><span class="bar ${{r.total_return>=0?'pos':'neg'}}" style="width:${{width}}%"></span></div><strong class="${{r.total_return>=0?'pass':'risk'}}">${{pct(r.total_return)}}</strong><span class="bench">SPY ${{pct(r.spy_return)}} / QQQ ${{pct(r.qqq_return)}}</span></div>`}}).join('');if(rerender)renderTable()}}
document.querySelectorAll('[data-control]').forEach(el=>el.addEventListener(el.tagName==='INPUT'?'input':'change',renderTable));document.querySelector('[data-action="reset"]').onclick=()=>{{document.querySelector('[data-control="screened-only"]').checked=true;document.querySelector('[data-control="universe"]').value='all';document.querySelector('[data-control="coverage"]').value='0';document.querySelector('[data-control="search"]').value='';sortKey='all_compound_return';sortAsc=false;renderTable()}};document.querySelectorAll('th[data-sort]').forEach(th=>th.onclick=()=>{{if(sortKey===th.dataset.sort)sortAsc=!sortAsc;else{{sortKey=th.dataset.sort;sortAsc=false}}renderTable()}});
window.__bearResilience={{data,filtered,select,get selected(){{return selected}},get sortKey(){{return sortKey}}}};renderTable();
}}());</script></main></body></html>'''


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervals", type=Path, required=True)
    parser.add_argument("--sp500-security-master", type=Path, required=True)
    parser.add_argument("--sp500-current", type=Path, required=True)
    parser.add_argument("--sp500-manifest", type=Path, required=True)
    parser.add_argument("--sp500-prices", type=Path, required=True)
    parser.add_argument("--nasdaq100-archive", type=Path, required=True)
    parser.add_argument("--tlt", type=Path, required=True)
    parser.add_argument("--gld", type=Path, required=True)
    parser.add_argument("--spy", type=Path, required=True)
    parser.add_argument("--qqq", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--output-detail", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    parser.add_argument("--output-html", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    interval_payload = json.loads(args.intervals.read_text(encoding="utf-8"))
    intervals = interval_payload["intervals"]
    if not intervals or len({row["interval_id"] for row in intervals}) != len(intervals):
        raise ValueError("interval dataset is empty or contains duplicate IDs")

    sp_snapshot, sp_members, sp_prices = load_sp500_current(
        args.sp500_security_master, args.sp500_current, args.sp500_prices
    )
    ndx_snapshot, ndx_members, ndx_prices = load_nasdaq100_current(args.nasdaq100_archive)
    members, prices = merge_current_universes(sp_members, sp_prices, ndx_members, ndx_prices)

    references = {
        "TLT": ("iShares 20+ Year Treasury Bond ETF", args.tlt, load_vendor_price),
        "GLD": ("SPDR Gold Shares ETF", args.gld, load_vendor_price),
        "SPY": ("SPDR S&P 500 ETF Trust", args.spy, load_canonical_price),
        "QQQ": ("Invesco QQQ Trust", args.qqq, load_canonical_price),
    }
    for symbol, (name, path, loader) in references.items():
        members[symbol] = {
            "asset_id": symbol,
            "symbol": symbol,
            "company_name": name,
            "role": "benchmark" if symbol in {"SPY", "QQQ"} else "reference_asset",
            "sp500_current": False,
            "nasdaq100_current": False,
            "universe": "Reference",
            "price_source": str(path),
        }
        prices[symbol] = loader(path)

    summaries, details = run_study(intervals=intervals, members=members, prices=prices)
    screened = [row for row in summaries if row["passes_screen"]]
    counts = {
        "sp500_current": len(sp_members),
        "sp500_price_available": sum(not series.empty for asset_id, series in sp_prices.items()),
        "nasdaq100_current": len(ndx_members),
        "equity_overlap": len(set(sp_members) & set(ndx_members)),
        "equity_union": len(set(sp_members) | set(ndx_members)),
        "reference_assets": 4,
        "analyzed_assets": len(summaries),
        "assets_with_any_available_interval": sum(row["available_interval_count"] > 0 for row in summaries),
        "screened_assets": len(screened),
        "intervals": len(intervals),
    }
    sources = {
        "intervals": args.intervals,
        "sp500_security_master": args.sp500_security_master,
        "sp500_current": args.sp500_current,
        "sp500_manifest": args.sp500_manifest,
        "nasdaq100_archive": args.nasdaq100_archive,
        "tlt": args.tlt,
        "gld": args.gld,
        "spy": args.spy,
        "qqq": args.qqq,
    }
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "view_id": VIEW_ID,
        "builder": {"path": str(Path(__file__)), "sha256": sha256(Path(__file__))},
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "study_type": "retrospective_event_study",
        "universe_policy": "current_constituents_mvp",
        "snapshots": {"sp500_official": sp_snapshot, "nasdaq100_vendor": ndx_snapshot},
        "return_policy": "split-and-dividend-adjusted close-to-close total return approximation",
        "aggregation_policy": "compound available bear-interval returns only; gaps between intervals excluded",
        "selection_rule": "at least one available bear interval return > 0 and all available bear intervals compound return > 0",
        "risk_policy": "worst single bear return is reported but is not a screen",
        "counts": counts,
        "sources": {
            key: {"path": str(path), "sha256": sha256(path)} for key, path in sources.items()
        },
        "limitations": [
            "Current-constituent universe has survivorship bias.",
            "Assets with different available interval counts are not directly comparable without coverage context.",
            "Adjusted OHLC approximates dividend reinvestment and is not an exact cash corporate-action ledger.",
            "S&P 500 and Nasdaq-100 may contain multiple share classes, so constituent counts can exceed 500 and 100.",
        ],
        "outputs": {
            "summary_csv": str(args.output_summary),
            "detail_csv": str(args.output_detail),
            "metadata_json": str(args.output_metadata),
            "interactive_html": str(args.output_html),
        },
    }

    write_csv(summaries, args.output_summary)
    write_csv(details, args.output_detail)
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(
        build_html(
            summaries=summaries,
            details=details,
            intervals=intervals,
            metadata=metadata,
            summary_csv_name=args.output_summary.name,
            detail_csv_name=args.output_detail.name,
        ),
        encoding="utf-8",
    )
    print(json.dumps(counts, ensure_ascii=False))
    print(f"Wrote {args.output_summary}")
    print(f"Wrote {args.output_detail}")
    print(f"Wrote {args.output_metadata}")
    print(f"Wrote {args.output_html}")


if __name__ == "__main__":
    main()
