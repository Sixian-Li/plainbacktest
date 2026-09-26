#!/usr/bin/env python3
"""Build a self-contained SPY/QQQ bear-market interval annotator."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly import io as pio
from plotly.subplots import make_subplots


VIEW_ID = "spy_qqq_bear_market_annotator_v1"
SYMBOLS = ("QQQ", "SPY")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_close(path: Path, symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["date", "symbol", "close"])
    if frame.empty:
        raise ValueError(f"{path} contains no rows")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame = frame.loc[frame["symbol"] == symbol, ["date", "close"]].copy()
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"{path} contains no rows for {symbol}")
    if frame["date"].duplicated().any():
        raise ValueError(f"{symbol} contains duplicate dates")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ValueError(f"{symbol} close must be positive and non-null")
    return frame.rename(columns={"close": symbol})


def prepare_prices(qqq_path: Path, spy_path: Path) -> pd.DataFrame:
    qqq = load_close(qqq_path, "QQQ")
    spy = load_close(spy_path, "SPY")
    prices = qqq.merge(spy, on="date", how="inner", validate="one_to_one")
    if len(prices) < 2:
        raise ValueError("QQQ and SPY need at least two common trading sessions")
    return prices.sort_values("date").reset_index(drop=True)


def _figure_html(prices: pd.DataFrame) -> str:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        subplot_titles=("QQQ 调整后 Close", "SPY 调整后 Close"),
    )
    colors = {"QQQ": "#2563eb", "SPY": "#d97706"}
    for row, symbol in enumerate(SYMBOLS, start=1):
        figure.add_trace(
            go.Scatter(
                x=prices["date"],
                y=prices[symbol],
                mode="lines",
                name=symbol,
                line={"color": colors[symbol], "width": 1.7},
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>{symbol} Close %{{y:.2f}}<extra></extra>"
                ),
                meta={"symbol": symbol},
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        template="plotly_white",
        height=820,
        dragmode="select",
        selectdirection="h",
        hovermode="x unified",
        showlegend=False,
        margin={"l": 70, "r": 26, "t": 54, "b": 55},
        uirevision=VIEW_ID,
        selectionrevision=VIEW_ID,
        newselection={"line": {"color": "#dc2626", "width": 1}},
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
    )
    figure.update_xaxes(
        title="交易日",
        rangeslider={"visible": True, "thickness": 0.07},
        row=2,
        col=1,
    )
    for row in (1, 2):
        figure.update_yaxes(title="调整后价格（对数）", type="log", row=row, col=1)
    figure_dict = figure.to_plotly_json()
    for trace, trace_dict in zip(figure.data, figure_dict["data"], strict=True):
        trace_dict["x"] = [
            value.date().isoformat()
            if hasattr(value, "date")
            else str(value).split("T", maxsplit=1)[0]
            for value in trace.x
        ]
        trace_dict["y"] = [float(value) for value in trace.y]
    return pio.to_html(
        figure_dict,
        full_html=False,
        include_plotlyjs="inline",
        config={
            "responsive": True,
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "toggleSpikelines"],
        },
        div_id="bear-market-chart",
        validate=False,
    )


def build_html(
    prices: pd.DataFrame,
    *,
    qqq_path: Path,
    spy_path: Path,
    qqq_sha256: str,
    spy_sha256: str,
) -> str:
    dates = [value.date().isoformat() for value in prices["date"]]
    data = {
        "dates": dates,
        "QQQ": [round(float(value), 8) for value in prices["QQQ"]],
        "SPY": [round(float(value), 8) for value in prices["SPY"]],
    }
    source = {
        "price_field": "split-and-dividend-adjusted Close",
        "common_start": dates[0],
        "common_end": dates[-1],
        "trading_sessions": len(dates),
        "QQQ": {"path": str(qqq_path), "sha256": qqq_sha256},
        "SPY": {"path": str(spy_path), "sha256": spy_sha256},
    }
    chart = _figure_html(prices)
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    source_json = json.dumps(
        source, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}">
<title>QQQ / SPY 熊市区间标注器</title>
<style>
:root{{--ink:#17212b;--muted:#64748b;--line:#dbe3eb;--panel:#fff;--soft:#f8fafc;--red:#b91c1c}}
*{{box-sizing:border-box}} body{{margin:0;background:#eef2f6;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1720px;margin:0 auto;padding:22px}} .card{{background:var(--panel);border-radius:14px;padding:18px;box-shadow:0 2px 12px rgba(15,23,42,.08)}}
h1{{margin:0 0 7px;font-size:26px}} p{{line-height:1.55}} .intro{{margin:0 0 14px;color:#475569}}
.toolbar{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:11px;background:var(--soft);border:1px solid var(--line);border-radius:10px}}
button,.button{{border:1px solid #b8c5d1;background:white;color:var(--ink);border-radius:7px;padding:7px 11px;cursor:pointer;font:inherit}}
button:hover,.button:hover{{background:#edf4fa}} button.active{{color:white;background:#334155;border-color:#334155}} button.danger{{color:var(--red)}}
.button input{{display:none}} .spacer{{flex:1}} .status{{font-size:13px;color:var(--muted)}}
.chart-wrap{{margin-top:10px}} .summary{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:18px 0 9px}}
.badge{{font-size:13px;padding:4px 8px;border-radius:999px;background:#fee2e2;color:#991b1b}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:9px}} table{{width:100%;border-collapse:collapse;min-width:1180px}}
th,td{{border-bottom:1px solid #e8edf2;padding:8px;text-align:left;vertical-align:middle;font-size:13px}} th{{background:#f8fafc;color:#475569;position:sticky;top:0}}
tbody tr:last-child td{{border-bottom:0}} input[type="text"],input[type="date"]{{width:100%;min-width:116px;border:1px solid #cbd5e1;border-radius:6px;padding:6px;font:inherit}}
input.note{{min-width:190px}} .metric{{font-variant-numeric:tabular-nums;white-space:nowrap}} .empty{{padding:22px;text-align:center;color:var(--muted)}}
.footer-note{{font-size:12px;color:var(--muted);margin:10px 2px 0}} .hidden{{display:none!important}}
@media(max-width:760px){{main{{padding:8px}}.card{{padding:10px}}h1{{font-size:21px}}.spacer{{display:none}}}}
</style></head><body><main><section class="card">
<h1>QQQ / SPY 熊市区间标注器</h1>
<p class="intro">在任意一张图上<strong>横向拖拽</strong>即可新增一个区间；上下图同步标红。可在下表调整日期、命名和备注，结果会自动保存在这个浏览器里。</p>
<div class="toolbar">
  <button type="button" data-mode="select" class="active">标注模式</button>
  <button type="button" data-mode="zoom">缩放模式</button>
  <button type="button" data-mode="pan">平移模式</button>
  <button type="button" data-action="reset-view">恢复全区间</button>
  <button type="button" data-action="toggle-scale">切换为线性坐标</button>
  <span class="spacer"></span>
  <button type="button" data-action="undo">撤销上次</button>
  <button type="button" data-action="export-json">导出 JSON</button>
  <button type="button" data-action="export-csv">导出 CSV</button>
  <label class="button">导入 JSON<input type="file" data-action="import-json" accept="application/json,.json"></label>
  <button type="button" data-action="clear" class="danger">清空标注</button>
  <span class="status" data-role="status">准备标注</span>
</div>
<div class="chart-wrap">{chart}</div>
<div class="summary"><h2 style="margin:0;font-size:20px">已标注区间</h2><span class="badge" data-role="count">0 段</span><span class="status">收益和最大回撤均按区间内首末交易日的调整后 Close 计算。</span></div>
<div class="table-wrap"><table>
<thead><tr><th>名称</th><th>开始日期</th><th>结束日期</th><th>交易日</th><th>QQQ 收益</th><th>QQQ 最大回撤</th><th>SPY 收益</th><th>SPY 最大回撤</th><th>备注</th><th>操作</th></tr></thead>
<tbody data-role="intervals"></tbody></table><div class="empty" data-role="empty">还没有标注。请在上方任意折线图中横向拖拽。</div></div>
<p class="footer-note">共同覆盖区间：{dates[0]}～{dates[-1]}，共 {len(dates):,} 个交易日。数据为标准库中拆股及股息调整后的 Close；此工具用于主观区间标记，不自动定义熊市。</p>
</section></main>
<script type="application/json" id="price-data">{data_json}</script>
<script type="application/json" id="source-data">{source_json}</script>
<script>
(function(){{'use strict';
const VIEW_ID={json.dumps(VIEW_ID)};
const STORAGE_KEY='quant:'+VIEW_ID+':intervals';
const graph=document.getElementById('bear-market-chart');
const tbody=document.querySelector('[data-role="intervals"]');
const empty=document.querySelector('[data-role="empty"]');
const count=document.querySelector('[data-role="count"]');
const status=document.querySelector('[data-role="status"]');
const priceData=JSON.parse(document.getElementById('price-data').textContent);
const sourceData=JSON.parse(document.getElementById('source-data').textContent);
const dates=priceData.dates;
const dateMs=dates.map(value=>Date.parse(value+'T00:00:00Z'));
const baseAnnotations=JSON.parse(JSON.stringify(graph.layout.annotations||[]));
let intervals=[]; let history=[]; let scale='log'; let selectionBusy=false;

function uid(){{return 'bear-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8)}}
function normalizeDate(value){{const match=String(value||'').match(/^\\d{{4}}-\\d{{2}}-\\d{{2}}/);return match?match[0]:null}}
function nearestIndex(value, side){{const target=Date.parse(normalizeDate(value)+'T00:00:00Z');if(!Number.isFinite(target))return null;let lo=0,hi=dateMs.length;while(lo<hi){{const mid=(lo+hi)>>1;if(dateMs[mid]<target)lo=mid+1;else hi=mid}}if(side==='start')return Math.min(lo,dateMs.length-1);if(lo<dateMs.length&&dateMs[lo]===target)return lo;return Math.max(0,lo-1)}}
function clean(item,index){{let start=normalizeDate(item.start),end=normalizeDate(item.end);if(!start||!end)return null;if(start>end)[start,end]=[end,start];const si=nearestIndex(start,'start'),ei=nearestIndex(end,'end');if(si===null||ei===null||si>ei)return null;return{{id:String(item.id||uid()),label:String(item.label||('熊市 '+(index+1))),start:dates[si],end:dates[ei],note:String(item.note||'')}}}}
function snapshot(){{history.push(JSON.stringify(intervals));if(history.length>50)history.shift()}}
function save(){{try{{localStorage.setItem(STORAGE_KEY,JSON.stringify(intervals))}}catch(error){{setStatus('浏览器未允许本地保存：'+error.message,true)}}}}
function setStatus(message,error=false){{status.textContent=message;status.style.color=error?'#b91c1c':''}}
function percent(value){{return Number.isFinite(value)?(value>=0?'+':'')+(value*100).toFixed(2)+'%':'—'}}
function metrics(item,symbol){{const start=nearestIndex(item.start,'start'),end=nearestIndex(item.end,'end');if(start===null||end===null||start>end)return null;const values=priceData[symbol].slice(start,end+1).map(Number);let peak=-Infinity,maxDrawdown=0;values.forEach(value=>{{peak=Math.max(peak,value);maxDrawdown=Math.min(maxDrawdown,value/peak-1)}});return{{sessions:values.length,totalReturn:values.at(-1)/values[0]-1,maxDrawdown}}}}
function shapes(){{return intervals.map(item=>({{type:'rect',xref:'x',yref:'paper',x0:item.start,x1:item.end,y0:0,y1:1,fillcolor:'rgba(220,38,38,.13)',line:{{color:'rgba(185,28,28,.65)',width:1}},layer:'below'}}))}}
function annotations(){{return intervals.map(item=>({{xref:'x',yref:'paper',x:(Date.parse(item.start)+Date.parse(item.end))/2,y:1.015,text:item.label,showarrow:false,font:{{size:11,color:'#991b1b'}},bgcolor:'rgba(254,226,226,.88)',borderpad:3}}))}}
async function updateChart(){{await Plotly.relayout(graph,{{shapes:shapes(),annotations:[...baseAnnotations,...annotations()],selections:[]}})}}
function field(item,key,type='text',className=''){{const value=String(item[key]||'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');return '<input type="'+type+'" class="'+className+'" data-id="'+item.id+'" data-field="'+key+'" value="'+value+'">'}}
function render(){{tbody.innerHTML='';intervals.forEach(item=>{{const q=metrics(item,'QQQ'),s=metrics(item,'SPY');const tr=document.createElement('tr');tr.dataset.id=item.id;tr.innerHTML='<td>'+field(item,'label')+'</td><td>'+field(item,'start','date')+'</td><td>'+field(item,'end','date')+'</td><td class="metric">'+(q?q.sessions:'—')+'</td><td class="metric">'+percent(q?.totalReturn)+'</td><td class="metric">'+percent(q?.maxDrawdown)+'</td><td class="metric">'+percent(s?.totalReturn)+'</td><td class="metric">'+percent(s?.maxDrawdown)+'</td><td>'+field(item,'note','text','note')+'</td><td><button type="button" class="danger" data-delete="'+item.id+'">删除</button></td>';tbody.appendChild(tr)}});empty.classList.toggle('hidden',intervals.length>0);count.textContent=intervals.length+' 段';save();updateChart()}}
function addInterval(start,end){{const item=clean({{start,end}},intervals.length);if(!item||item.start===item.end){{setStatus('请至少选择两个交易日',true);return}}snapshot();intervals.push(item);intervals.sort((a,b)=>a.start.localeCompare(b.start));render();setStatus('已新增 '+item.start+'～'+item.end)}}
function selectedRange(event){{const xs=(event?.points||[]).map(point=>normalizeDate(point.x)).filter(Boolean);if(xs.length>=2)return [xs.sort()[0],xs.sort().at(-1)];const range=event?.range?.x||event?.range?.x2;if(range?.length===2)return range.map(normalizeDate);return null}}
graph.on('plotly_selected',event=>{{if(selectionBusy)return;const range=selectedRange(event);if(!range)return;selectionBusy=true;addInterval(range[0],range[1]);Plotly.restyle(graph,{{selectedpoints:[null]}},[0,1]).finally(()=>{{selectionBusy=false}})}});
tbody.addEventListener('change',event=>{{const input=event.target.closest('input[data-id]');if(!input)return;const index=intervals.findIndex(item=>item.id===input.dataset.id);if(index<0)return;snapshot();const candidate={{...intervals[index],[input.dataset.field]:input.value}};const cleaned=clean(candidate,index);if(!cleaned){{setStatus('日期超出可用范围或区间无效',true);render();return}}intervals[index]=cleaned;intervals.sort((a,b)=>a.start.localeCompare(b.start));render();setStatus('区间已更新')}});
tbody.addEventListener('click',event=>{{const button=event.target.closest('[data-delete]');if(!button)return;snapshot();intervals=intervals.filter(item=>item.id!==button.dataset.delete);render();setStatus('区间已删除')}});
function setMode(mode){{document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===mode));Plotly.relayout(graph,{{dragmode:mode}});setStatus(mode==='select'?'标注模式：横向拖拽新增区间':mode==='zoom'?'缩放模式：框选放大':'平移模式：拖动画面')}}
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.mode)));
document.querySelector('[data-action="reset-view"]').addEventListener('click',()=>Plotly.relayout(graph,{{'xaxis.autorange':true,'xaxis2.autorange':true}}).then(()=>setStatus('已恢复全区间')));
document.querySelector('[data-action="toggle-scale"]').addEventListener('click',event=>{{scale=scale==='log'?'linear':'log';Plotly.relayout(graph,{{'yaxis.type':scale,'yaxis2.type':scale}});event.currentTarget.textContent=scale==='log'?'切换为线性坐标':'切换为对数坐标';setStatus('已切换为'+(scale==='log'?'对数':'线性')+'坐标')}});
document.querySelector('[data-action="undo"]').addEventListener('click',()=>{{if(!history.length){{setStatus('没有可撤销的操作');return}}intervals=JSON.parse(history.pop());render();setStatus('已撤销')}});
document.querySelector('[data-action="clear"]').addEventListener('click',()=>{{if(!intervals.length)return;if(!confirm('确定清空全部熊市标注吗？'))return;snapshot();intervals=[];render();setStatus('全部标注已清空')}});
function payload(){{return{{schema_version:1,view_id:VIEW_ID,exported_at:new Date().toISOString(),source:sourceData,intervals:intervals.map((item,index)=>{{const q=metrics(item,'QQQ'),s=metrics(item,'SPY');return{{ordinal:index+1,...item,trading_sessions:q?.sessions??null,QQQ:{{total_return:q?.totalReturn??null,max_drawdown:q?.maxDrawdown??null}},SPY:{{total_return:s?.totalReturn??null,max_drawdown:s?.maxDrawdown??null}}}}}})}}}}
function download(name,text,type){{const blob=new Blob([text],{{type}}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}}
document.querySelector('[data-action="export-json"]').addEventListener('click',()=>{{download('spy_qqq_bear_markets.json',JSON.stringify(payload(),null,2)+'\\n','application/json');setStatus('已导出 JSON')}});
document.querySelector('[data-action="export-csv"]').addEventListener('click',()=>{{const quote=value=>'"'+String(value??'').replaceAll('"','""')+'"';const rows=[['label','start','end','trading_sessions','qqq_total_return','qqq_max_drawdown','spy_total_return','spy_max_drawdown','note']];payload().intervals.forEach(item=>rows.push([item.label,item.start,item.end,item.trading_sessions,item.QQQ.total_return,item.QQQ.max_drawdown,item.SPY.total_return,item.SPY.max_drawdown,item.note]));download('spy_qqq_bear_markets.csv','\\ufeff'+rows.map(row=>row.map(quote).join(',')).join('\\n')+'\\n','text/csv;charset=utf-8');setStatus('已导出 CSV')}});
document.querySelector('[data-action="import-json"]').addEventListener('change',async event=>{{const file=event.target.files?.[0];if(!file)return;try{{const parsed=JSON.parse(await file.text()),incoming=Array.isArray(parsed)?parsed:parsed.intervals;if(!Array.isArray(incoming))throw new Error('找不到 intervals 数组');const cleaned=incoming.map(clean).filter(Boolean);snapshot();intervals=cleaned.sort((a,b)=>a.start.localeCompare(b.start));render();setStatus('已导入 '+intervals.length+' 段')}}catch(error){{setStatus('导入失败：'+error.message,true)}}finally{{event.target.value=''}}}});
try{{const stored=JSON.parse(localStorage.getItem(STORAGE_KEY)||'[]');if(Array.isArray(stored))intervals=stored.map(clean).filter(Boolean)}}catch(error){{setStatus('本地记录读取失败：'+error.message,true)}}
render();setMode('select');
window.__bearAnnotator={{getIntervals:()=>JSON.parse(JSON.stringify(intervals)),addInterval,payload,setMode}};
}}());
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qqq", type=Path, required=True)
    parser.add_argument("--spy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    prices = prepare_prices(args.qqq, args.spy)
    qqq_hash = sha256(args.qqq)
    spy_hash = sha256(args.spy)
    output = args.output.resolve()
    metadata_path = args.metadata.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_html(
            prices,
            qqq_path=args.qqq,
            spy_path=args.spy,
            qqq_sha256=qqq_hash,
            spy_sha256=spy_hash,
        ),
        encoding="utf-8",
    )
    metadata = {
        "schema_version": 1,
        "view_id": VIEW_ID,
        "symbols": list(SYMBOLS),
        "price_field": "split-and-dividend-adjusted Close",
        "actual_start": prices["date"].iloc[0].date().isoformat(),
        "actual_end": prices["date"].iloc[-1].date().isoformat(),
        "trading_sessions": len(prices),
        "interaction": "horizontal drag creates an editable inclusive interval",
        "persistence": "browser localStorage plus JSON/CSV export and JSON import",
        "sources": {
            "QQQ": {"path": str(args.qqq), "sha256": qqq_hash},
            "SPY": {"path": str(args.spy), "sha256": spy_hash},
        },
        "output": str(args.output),
        "output_sha256": sha256(output),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output}")
    print(f"Wrote {metadata_path}")
    print(
        f"{len(prices)} common sessions: "
        f"{metadata['actual_start']} through {metadata['actual_end']}"
    )


if __name__ == "__main__":
    main()
