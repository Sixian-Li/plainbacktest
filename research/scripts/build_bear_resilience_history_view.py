#!/usr/bin/env python3
"""Build a self-contained 24-asset Close/SMA view with bear highlights."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from plotly.offline.offline import get_plotlyjs


VIEW_ID = "bear_resilience_24_full_history_sma_bear_intervals_v1"
SCHEMA_VERSION = 1
SMA_WINDOWS = (30, 150, 200, 250, 300)
DEFAULT_START = "1999-01-01"
DEFAULT_END = "2026-08-04"

GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group_id": "core12",
        "label": "核心层 12",
        "description": "此前事件筛选得到的 12 个核心候选（已排除 Q）。",
        "symbols": (
            "AZO",
            "TLT",
            "COR",
            "EXE",
            "DVA",
            "SJM",
            "SO",
            "ED",
            "GLD",
            "CHD",
            "HRL",
            "GILD",
        ),
    },
    {
        "group_id": "near8",
        "label": "近核心层 8",
        "description": "业务属性或历史表现接近核心层、但未通过原筛选的 8 个候选。",
        "symbols": ("HSY", "ORLY", "MO", "WRB", "EQT", "LMT", "GIS", "WEC"),
    },
    {
        "group_id": "retail4",
        "label": "零售补充 4",
        "description": "单独补充观察的四个零售标的。",
        "symbols": ("DLTR", "DG", "WMT", "TSCO"),
    },
)

COMPANY_NAMES = {
    "AZO": "AutoZone",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "COR": "Cencora",
    "EXE": "Expand Energy",
    "DVA": "DaVita",
    "SJM": "J.M. Smucker",
    "SO": "Southern Company",
    "ED": "Consolidated Edison",
    "GLD": "SPDR Gold Shares",
    "CHD": "Church & Dwight",
    "HRL": "Hormel Foods",
    "GILD": "Gilead Sciences",
    "HSY": "Hershey",
    "ORLY": "O'Reilly Automotive",
    "MO": "Altria",
    "WRB": "W. R. Berkley",
    "EQT": "EQT",
    "LMT": "Lockheed Martin",
    "GIS": "General Mills",
    "WEC": "WEC Energy",
    "DLTR": "Dollar Tree",
    "DG": "Dollar General",
    "WMT": "Walmart",
    "TSCO": "Tractor Supply",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def all_symbols() -> tuple[str, ...]:
    return tuple(symbol for group in GROUPS for symbol in group["symbols"])


def _close_series(
    frame: pd.DataFrame,
    *,
    symbol: str,
    date_column: str,
    close_column: str,
) -> pd.Series:
    required = {date_column, close_column}
    if missing := required.difference(frame.columns):
        raise ValueError(f"{symbol} price data misses {sorted(missing)}")
    data = frame[[date_column, close_column]].copy()
    data[date_column] = pd.to_datetime(data[date_column], errors="raise")
    data[close_column] = pd.to_numeric(data[close_column], errors="raise")
    data = data.sort_values(date_column)
    if data.empty:
        raise ValueError(f"{symbol} price data is empty")
    if data[date_column].duplicated().any():
        raise ValueError(f"{symbol} price data has duplicate dates")
    if data[close_column].isna().any() or (data[close_column] <= 0).any():
        raise ValueError(f"{symbol} Close must be positive and non-null")
    return data.set_index(date_column)[close_column].astype(float).rename(symbol)


def load_canonical_price(path: Path, symbol: str) -> pd.Series:
    frame = pd.read_csv(path, usecols=["date", "close"])
    return _close_series(
        frame,
        symbol=symbol,
        date_column="date",
        close_column="close",
    )


def load_vendor_price(path: Path, symbol: str) -> pd.Series:
    frame = pd.read_csv(path, usecols=["Date", "Close"])
    return _close_series(
        frame,
        symbol=symbol,
        date_column="Date",
        close_column="Close",
    )


def load_intervals(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_intervals = payload.get("intervals")
    if not isinstance(raw_intervals, list) or not raw_intervals:
        raise ValueError("bear interval file must contain a non-empty intervals list")
    intervals: list[dict[str, Any]] = []
    previous_end: pd.Timestamp | None = None
    seen_ids: set[str] = set()
    for raw in raw_intervals:
        interval_id = str(raw["interval_id"])
        if interval_id in seen_ids:
            raise ValueError(f"duplicate bear interval id: {interval_id}")
        severity = str(raw["severity"])
        if severity not in {"major", "minor"}:
            raise ValueError(f"unsupported bear severity: {severity}")
        start = pd.Timestamp(raw["start"])
        end = pd.Timestamp(raw["end"])
        if start > end:
            raise ValueError(f"bear interval {interval_id} starts after it ends")
        if previous_end is not None and start <= previous_end:
            raise ValueError("bear intervals must be sorted and non-overlapping")
        seen_ids.add(interval_id)
        previous_end = end
        ordinal = int(raw["ordinal"])
        intervals.append(
            {
                "interval_id": interval_id,
                "ordinal": ordinal,
                "label": str(raw["label"]),
                "severity": severity,
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "short_label": f"{'大' if severity == 'major' else '小'}{ordinal:02d}",
            }
        )
    return intervals, payload


def prepare_asset_view(
    series: pd.Series,
    *,
    start: str,
    end: str,
    windows: Iterable[int] = SMA_WINDOWS,
) -> pd.DataFrame:
    windows = tuple(windows)
    if not windows or any(window < 2 for window in windows):
        raise ValueError("every SMA window must be at least 2")
    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    if series.empty or series.index.duplicated().any():
        raise ValueError("price series must be non-empty with unique dates")
    data = series.sort_index().loc[:end_date].to_frame("close")
    for window in windows:
        data[f"sma{window}"] = data["close"].rolling(window, min_periods=window).mean()
    view = data.loc[start_date:end_date].copy()
    if view.empty:
        raise ValueError("requested interval contains no price data")
    return view


def _json_values(series: pd.Series) -> list[float | None]:
    return [None if pd.isna(value) else round(float(value), 6) for value in series]


def build_payload(
    *,
    prices: dict[str, pd.Series],
    source_labels: dict[str, str],
    intervals: list[dict[str, Any]],
    start: str,
    end: str,
) -> dict[str, Any]:
    expected = set(all_symbols())
    if set(prices) != expected:
        missing = sorted(expected.difference(prices))
        extra = sorted(set(prices).difference(expected))
        raise ValueError(f"price universe mismatch; missing={missing}, extra={extra}")
    assets: list[dict[str, Any]] = []
    for group in GROUPS:
        for symbol in group["symbols"]:
            view = prepare_asset_view(prices[symbol], start=start, end=end)
            available_smas = [
                window for window in SMA_WINDOWS if view[f"sma{window}"].notna().any()
            ]
            assets.append(
                {
                    "symbol": symbol,
                    "company_name": COMPANY_NAMES[symbol],
                    "group_id": group["group_id"],
                    "group_label": group["label"],
                    "source_label": source_labels[symbol],
                    "actual_start": view.index[0].strftime("%Y-%m-%d"),
                    "actual_end": view.index[-1].strftime("%Y-%m-%d"),
                    "sessions": int(len(view)),
                    "available_smas": available_smas,
                    "dates": [value.strftime("%Y-%m-%d") for value in view.index],
                    "close": _json_values(view["close"]),
                    "smas": {
                        str(window): _json_values(view[f"sma{window}"])
                        for window in SMA_WINDOWS
                    },
                }
            )
    return {
        "view_id": VIEW_ID,
        "requested_start": start,
        "requested_end": end,
        "sma_windows": list(SMA_WINDOWS),
        "default_sma": 200,
        "default_bear_mode": "all",
        "groups": [
            {
                "group_id": group["group_id"],
                "label": group["label"],
                "description": group["description"],
                "symbols": list(group["symbols"]),
            }
            for group in GROUPS
        ],
        "intervals": intervals,
        "assets": assets,
    }


def _asset_cards(payload: dict[str, Any]) -> str:
    asset_by_symbol = {asset["symbol"]: asset for asset in payload["assets"]}
    windows = payload["sma_windows"]
    pieces: list[str] = []
    for group in payload["groups"]:
        nav = "".join(
            f'<a href="#asset-{symbol}">{symbol}</a>' for symbol in group["symbols"]
        )
        pieces.append(
            f'<section class="group-block" id="group-{group["group_id"]}">'
            f'<div class="group-heading"><div><span class="eyebrow">{html.escape(group["label"])}</span>'
            f'<h2>{html.escape(group["label"])}</h2><p>{html.escape(group["description"])}</p></div>'
            f'<nav class="ticker-nav" aria-label="{html.escape(group["label"])} 标的导航">{nav}</nav></div>'
        )
        for symbol in group["symbols"]:
            asset = asset_by_symbol[symbol]
            sma_controls = "".join(
                (
                    '<label class="check-pill">'
                    f'<input type="checkbox" data-control="sma" data-window="{window}"'
                    f'{" checked" if window == payload["default_sma"] else ""}'
                    f'{"" if window in asset["available_smas"] else " disabled"}>'
                    f'<span>SMA{window}</span></label>'
                )
                for window in windows
            )
            pieces.append(
                f'<article class="asset-card" id="asset-{symbol}" data-symbol="{symbol}">'
                '<header class="asset-header"><div>'
                f'<span class="asset-group">{html.escape(group["label"])}</span>'
                f'<h3>{symbol} <small>{html.escape(asset["company_name"])}</small></h3>'
                f'<p>{asset["actual_start"]}～{asset["actual_end"]} · {asset["sessions"]:,} 个交易日 · 复权 Close</p>'
                '</div><a class="back-top" href="#top">回到顶部 ↑</a></header>'
                '<div class="chart-controls" role="group" aria-label="图表选项">'
                f'<div class="control-cluster"><strong>均线</strong>{sma_controls}</div>'
                '<label class="select-control"><strong>熊市高亮</strong>'
                '<select data-control="bear-mode">'
                '<option value="off">不显示</option><option value="minor">仅小熊</option>'
                '<option value="major">仅大熊</option><option value="all" selected>全部熊市</option>'
                '</select></label>'
                '<label class="check-pill axis-toggle"><input type="checkbox" data-control="log" checked>'
                '<span>对数 Y 轴</span></label>'
                '<button type="button" data-action="reset">重置缩放</button>'
                '</div>'
                '<div class="plot-shell"><div class="plot-placeholder">图表载入中…</div>'
                f'<div class="plot" id="plot-{symbol}" aria-label="{symbol} 完整历史价格图"></div></div>'
                f'<details class="source"><summary>数据来源</summary><code>{html.escape(asset["source_label"])}</code></details>'
                '</article>'
            )
        pieces.append("</section>")
    return "".join(pieces)


def build_html(payload: dict[str, Any]) -> str:
    data_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("</", "<\\/")
    cards = _asset_cards(payload)
    plotly_js = get_plotlyjs()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}">
<title>24 个熊市候选 · 完整历史 Close / SMA</title>
<style>
:root{{--ink:#172033;--muted:#667085;--line:#dce3ec;--paper:#f4f7fb;--card:#fff;--blue:#2563eb;--major:#dc2626;--minor:#d97706}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
a{{color:var(--blue);text-decoration:none}} a:hover{{text-decoration:underline}} .page{{max-width:1760px;margin:0 auto;padding:28px 34px 70px}}
.hero{{background:linear-gradient(135deg,#0f172a,#1e3a8a);color:#fff;border-radius:22px;padding:30px 34px;box-shadow:0 18px 55px rgba(15,23,42,.18)}}
.hero h1{{font-size:32px;line-height:1.18;margin:5px 0 10px}} .hero p{{max-width:1100px;margin:0;color:#dbeafe;font-size:15px}}
.eyebrow{{font-size:12px;letter-spacing:.13em;text-transform:uppercase;font-weight:800;color:#93c5fd}} .hero-meta{{display:flex;flex-wrap:wrap;gap:9px;margin-top:18px}}
.hero-meta span{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);padding:6px 10px;border-radius:999px}}
.global-bar{{position:sticky;top:0;z-index:30;margin:16px 0 25px;padding:12px 14px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.95);backdrop-filter:blur(12px);box-shadow:0 7px 22px rgba(15,23,42,.08);display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.global-bar strong{{font-size:13px}} button,select{{font:inherit;border:1px solid #c8d2df;border-radius:9px;background:#fff;color:var(--ink);padding:7px 10px}} button{{cursor:pointer}} button:hover{{border-color:#7aa2e8;background:#eff6ff}}
.legend{{margin-left:auto;display:flex;gap:12px;color:var(--muted)}} .swatch{{display:inline-block;width:13px;height:13px;border-radius:3px;margin-right:5px;vertical-align:-2px}} .major{{background:rgba(220,38,38,.22)}} .minor{{background:rgba(245,158,11,.25)}}
.group-block{{scroll-margin-top:80px}} .group-heading{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:35px 4px 14px}} .group-heading h2{{font-size:25px;margin:2px 0}} .group-heading p{{margin:0;color:var(--muted)}}
.group-heading .eyebrow{{color:#5271a8}} .ticker-nav{{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px}} .ticker-nav a{{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 9px;font-weight:700}}
.asset-card{{scroll-margin-top:82px;background:var(--card);border:1px solid var(--line);border-radius:17px;margin:0 0 18px;padding:18px 18px 13px;box-shadow:0 7px 22px rgba(15,23,42,.055)}}
.asset-header{{display:flex;justify-content:space-between;gap:15px;align-items:start}} .asset-group{{color:#5271a8;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}
.asset-header h3{{font-size:22px;margin:2px 0 1px}} .asset-header h3 small{{font-size:14px;font-weight:500;color:var(--muted)}} .asset-header p{{margin:0;color:var(--muted)}} .back-top{{font-size:12px;white-space:nowrap}}
.chart-controls{{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin:14px 0 5px;padding:10px 11px;background:#f8fafc;border:1px solid #e7ecf2;border-radius:11px}}
.control-cluster,.select-control{{display:flex;align-items:center;gap:7px}} .control-cluster strong,.select-control strong{{font-size:12px;color:var(--muted)}}
.check-pill{{position:relative;display:inline-flex;align-items:center}} .check-pill input{{position:absolute;opacity:0;pointer-events:none}} .check-pill span{{border:1px solid #cbd5e1;border-radius:999px;background:#fff;padding:5px 9px;cursor:pointer;font-weight:650}}
.check-pill input:checked+span{{background:#eaf2ff;border-color:#7aa2e8;color:#1d4ed8}} .check-pill input:disabled+span{{opacity:.38;cursor:not-allowed}} .axis-toggle{{margin-left:auto}}
.plot-shell{{position:relative;min-height:520px}} .plot{{height:520px;width:100%}} .plot-placeholder{{position:absolute;inset:0;display:grid;place-items:center;color:#8a98aa;background:linear-gradient(90deg,#f8fafc,#fff,#f8fafc);border-radius:10px}} .plot-shell.ready .plot-placeholder{{display:none}}
.source{{color:var(--muted);font-size:12px;margin:3px 5px 0}} .source code{{display:block;white-space:normal;word-break:break-all;margin-top:4px}}
.footnote{{margin-top:28px;padding:17px;border:1px solid var(--line);border-radius:13px;background:#fff;color:var(--muted)}}
@media(max-width:760px){{.page{{padding:16px 10px 45px}}.hero{{padding:22px 18px}}.hero h1{{font-size:25px}}.global-bar{{position:static}}.legend{{margin-left:0}}.group-heading{{display:block}}.ticker-nav{{justify-content:flex-start;margin-top:10px}}.axis-toggle{{margin-left:0}}.plot,.plot-shell{{height:430px;min-height:430px}}}}
</style>
</head>
<body><main class="page" id="top">
<section class="hero"><span class="eyebrow">ROT + TIM · retrospective market view</span>
<h1>12 + 8 + 4 个熊市候选：完整历史 Close / SMA</h1>
<p>逐只查看 1999-01-01～2026-08-04 的拆股及股息调整 Close。默认显示 SMA200 与全部 12 段主观峰值→谷底熊市；每张图都可独立切换五条均线、大小熊市高亮、对数/线性纵轴和缩放。</p>
<div class="hero-meta"><span>24 个标的</span><span>5 条可选 SMA</span><span>6 段大熊 + 6 段小熊</span><span>上市较晚标的保留真实起点</span></div></section>
<section class="global-bar" aria-label="全局图表控制"><strong>全部图表</strong>
<button type="button" data-global-sma="200">仅 SMA200</button><button type="button" data-global-sma="all">打开全部 SMA</button><button type="button" data-global-sma="off">关闭全部 SMA</button>
<label>熊市 <select data-global-bear><option value="off">不显示</option><option value="minor">仅小熊</option><option value="major">仅大熊</option><option value="all" selected>全部熊市</option></select></label>
<button type="button" data-global-axis="log">全部对数轴</button><button type="button" data-global-axis="linear">全部线性轴</button>
<div class="legend"><span><i class="swatch major"></i>大熊</span><span><i class="swatch minor"></i>小熊</span></div></section>
{cards}
<section class="footnote"><strong>口径边界：</strong>这里只是复权 Close 与简单移动平均的市场观察，不产生交易信号、持仓或账户收益。熊市区间来自用户手工标注后在候选窗口内收紧的 QQQ/SPY 峰值至谷底，属于事后已知区间。复权价格近似股息再投资，不等同于精确现金公司行动账本。</section>
</main>
<script>{plotly_js}</script>
<script type="application/json" id="view-data">{data_json}</script>
<script>
(() => {{
  const data = JSON.parse(document.getElementById('view-data').textContent);
  const assetBySymbol = new Map(data.assets.map((asset) => [asset.symbol, asset]));
  const colors = {{close:'#111827','30':'#2563eb','150':'#16a34a','200':'#dc2626','250':'#7c3aed','300':'#d97706'}};
  const cards = [...document.querySelectorAll('.asset-card')];
  const rendered = new Set();
  const dateNumber = (value) => Date.parse(value);

  function selectedIntervals(mode) {{
    if (mode === 'off') return [];
    return data.intervals.filter((item) => mode === 'all' || item.severity === mode);
  }}
  function bearShapes(mode) {{
    return selectedIntervals(mode).map((item) => ({{
      type:'rect', xref:'x', yref:'paper', x0:item.start, x1:item.end, y0:0, y1:1,
      fillcolor:item.severity === 'major' ? 'rgba(220,38,38,.14)' : 'rgba(245,158,11,.16)',
      line:{{width:0}}, layer:'below', name:item.label
    }}));
  }}
  function traces(asset, card) {{
    const output = [{{
      x:asset.dates, y:asset.close, type:'scatter', mode:'lines', name:`${{asset.symbol}} Close`,
      line:{{color:colors.close,width:1.7}}, hovertemplate:'%{{x}}<br>Close %{{y:.2f}}<extra></extra>'
    }}];
    for (const window of data.sma_windows) {{
      const checked = card.querySelector(`[data-control="sma"][data-window="${{window}}"]`).checked;
      output.push({{
        x:asset.dates, y:asset.smas[String(window)], type:'scatter', mode:'lines', name:`SMA${{window}}`,
        visible:checked, line:{{color:colors[String(window)],width:1.25}}, opacity:.88,
        hovertemplate:`%{{x}}<br>SMA${{window}} %{{y:.2f}}<extra></extra>`
      }});
    }}
    return output;
  }}
  function layout(asset, card) {{
    return {{
      template:'plotly_white', height:520, margin:{{l:65,r:24,t:30,b:54}},
      hovermode:'x unified', dragmode:'pan', showlegend:true, uirevision:`${{asset.symbol}}-${{data.view_id}}`,
      legend:{{orientation:'h',x:0,y:1.04,font:{{size:11}}}},
      xaxis:{{range:[data.requested_start,data.requested_end],rangeslider:{{visible:false}},showgrid:true,gridcolor:'#edf1f5',title:'日期'}},
      yaxis:{{type:card.querySelector('[data-control="log"]').checked?'log':'linear',showgrid:true,gridcolor:'#edf1f5',title:'复权价格'}},
      shapes:bearShapes(card.querySelector('[data-control="bear-mode"]').value),
      paper_bgcolor:'#fff',plot_bgcolor:'#fff'
    }};
  }}
  function currentRange(div) {{
    const range = div.layout?.xaxis?.range;
    return Array.isArray(range) && range.length === 2 ? range : [data.requested_start,data.requested_end];
  }}
  function adjustVisibleY(card) {{
    if (!rendered.has(card.dataset.symbol) || card.__adjustingY) return;
    const asset = assetBySymbol.get(card.dataset.symbol);
    const div = card.querySelector('.plot');
    const [left,right] = currentRange(div).map(dateNumber);
    const active = [asset.close];
    for (const window of data.sma_windows) {{
      if (card.querySelector(`[data-control="sma"][data-window="${{window}}"]`).checked) active.push(asset.smas[String(window)]);
    }}
    let low=Infinity, high=-Infinity;
    for (let index=0; index<asset.dates.length; index+=1) {{
      const stamp=dateNumber(asset.dates[index]); if (stamp<left || stamp>right) continue;
      for (const values of active) {{ const value=values[index]; if (value!==null && Number.isFinite(value) && value>0) {{low=Math.min(low,value);high=Math.max(high,value)}} }}
    }}
    if (!Number.isFinite(low) || !Number.isFinite(high)) return;
    if (low===high) {{low*=.98;high*=1.02}}
    const isLog=card.querySelector('[data-control="log"]').checked;
    const range=isLog?[Math.log10(low)-.045,Math.log10(high)+.045]:[low-(high-low)*.07,high+(high-low)*.07];
    card.__adjustingY=true;
    Plotly.relayout(div,{{'yaxis.range':range}}).finally(()=>{{card.__adjustingY=false}});
  }}
  async function renderAsset(card) {{
    const symbol=card.dataset.symbol; if (rendered.has(symbol)) return;
    const asset=assetBySymbol.get(symbol); const div=card.querySelector('.plot');
    await Plotly.newPlot(div,traces(asset,card),layout(asset,card),{{responsive:true,scrollZoom:true,displaylogo:false}});
    rendered.add(symbol); card.querySelector('.plot-shell').classList.add('ready');
    div.on('plotly_relayout',(event)=>{{ if (Object.keys(event).some((key)=>key.startsWith('xaxis.range')||key==='xaxis.autorange')) requestAnimationFrame(()=>adjustVisibleY(card)); }});
    adjustVisibleY(card);
  }}
  function updateSma(card,window,checked) {{
    if (!rendered.has(card.dataset.symbol)) return;
    Plotly.restyle(card.querySelector('.plot'),{{visible:checked}},[data.sma_windows.indexOf(Number(window))+1]).then(()=>adjustVisibleY(card));
  }}
  function updateBear(card,mode) {{
    if (rendered.has(card.dataset.symbol)) Plotly.relayout(card.querySelector('.plot'),{{shapes:bearShapes(mode)}});
  }}
  function updateAxis(card,isLog) {{
    if (!rendered.has(card.dataset.symbol)) return;
    Plotly.relayout(card.querySelector('.plot'),{{'yaxis.type':isLog?'log':'linear'}}).then(()=>adjustVisibleY(card));
  }}
  function reset(card) {{
    if (!rendered.has(card.dataset.symbol)) return;
    Plotly.relayout(card.querySelector('.plot'),{{'xaxis.range':[data.requested_start,data.requested_end]}}).then(()=>adjustVisibleY(card));
  }}
  for (const card of cards) {{
    card.querySelectorAll('[data-control="sma"]').forEach((input)=>input.addEventListener('change',()=>updateSma(card,input.dataset.window,input.checked)));
    card.querySelector('[data-control="bear-mode"]').addEventListener('change',(event)=>updateBear(card,event.target.value));
    card.querySelector('[data-control="log"]').addEventListener('change',(event)=>updateAxis(card,event.target.checked));
    card.querySelector('[data-action="reset"]').addEventListener('click',()=>reset(card));
  }}
  document.querySelectorAll('[data-global-sma]').forEach((button)=>button.addEventListener('click',()=>{{
    const mode=button.dataset.globalSma;
    for (const card of cards) for (const input of card.querySelectorAll('[data-control="sma"]')) {{
      const checked=mode==='all'||(mode==='200'&&input.dataset.window==='200');
      if (!input.disabled && input.checked!==checked) {{input.checked=checked;updateSma(card,input.dataset.window,checked)}}
    }}
  }}));
  document.querySelector('[data-global-bear]').addEventListener('change',(event)=>{{
    for (const card of cards) {{const select=card.querySelector('[data-control="bear-mode"]');select.value=event.target.value;updateBear(card,select.value)}}
  }});
  document.querySelectorAll('[data-global-axis]').forEach((button)=>button.addEventListener('click',()=>{{
    const isLog=button.dataset.globalAxis==='log';
    for (const card of cards) {{const input=card.querySelector('[data-control="log"]');input.checked=isLog;updateAxis(card,isLog)}}
  }}));
  const observer=new IntersectionObserver((entries)=>{{for(const entry of entries)if(entry.isIntersecting)renderAsset(entry.target)}},{{rootMargin:'900px 0px'}});
  cards.forEach((card)=>observer.observe(card));
  renderAsset(cards[0]);
  window.__bearHistory={{data,assetBySymbol,cards,rendered,renderAsset,updateBear,adjustVisibleY,bearShapes,ready:true}};
}})();
</script>
</body></html>"""


def build_metadata(
    *,
    builder_path: Path,
    interval_path: Path,
    interval_source: dict[str, Any],
    source_paths: dict[str, Path],
    payload: dict[str, Any],
    output_html: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "view_id": VIEW_ID,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "view_type": "non_trading_market_view",
        "builder": {"path": str(builder_path), "sha256": sha256(builder_path)},
        "requested_window": {
            "start": payload["requested_start"],
            "end": payload["requested_end"],
        },
        "price_policy": "split-and-dividend-adjusted Close; pre-window history is used only to warm SMAs",
        "sma_windows": payload["sma_windows"],
        "groups": payload["groups"],
        "counts": {
            "assets": len(payload["assets"]),
            "core": len(GROUPS[0]["symbols"]),
            "near_core": len(GROUPS[1]["symbols"]),
            "retail": len(GROUPS[2]["symbols"]),
            "bear_intervals": len(payload["intervals"]),
            "major_bear_intervals": sum(item["severity"] == "major" for item in payload["intervals"]),
            "minor_bear_intervals": sum(item["severity"] == "minor" for item in payload["intervals"]),
        },
        "coverage": [
            {
                "symbol": asset["symbol"],
                "group": asset["group_id"],
                "actual_start": asset["actual_start"],
                "actual_end": asset["actual_end"],
                "sessions": asset["sessions"],
                "available_smas": asset["available_smas"],
            }
            for asset in payload["assets"]
        ],
        "sources": {
            "bear_intervals": {
                "path": str(interval_path),
                "sha256": sha256(interval_path),
                "dataset_id": interval_source.get("dataset_id"),
                "boundary_policy": interval_source.get("boundary_policy"),
            },
            "prices": {
                symbol: {"path": str(path), "sha256": sha256(path)}
                for symbol, path in source_paths.items()
            },
        },
        "limitations": [
            "The 24 assets were selected after reviewing the same historical bear intervals, so the view is retrospective and subject to selection and survivorship bias.",
            "Bear intervals are ex-post user annotations refined to QQQ/SPY peak-to-trough windows and were not knowable at their starts.",
            "Adjusted Close approximates dividend reinvestment and is not an exact cash corporate-action ledger.",
            "Assets listed after 1999 retain their true first available date; no prices are fabricated or backfilled.",
        ],
        "outputs": {
            "interactive_html": str(output_html),
            "interactive_html_sha256": sha256(output_html),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity-price-dir", type=Path, required=True)
    parser.add_argument("--tlt", type=Path, required=True)
    parser.add_argument("--gld", type=Path, required=True)
    parser.add_argument("--intervals", type=Path, required=True)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_paths = {
        symbol: args.equity_price_dir / f"{symbol}.csv"
        for symbol in all_symbols()
        if symbol not in {"TLT", "GLD"}
    }
    source_paths["TLT"] = args.tlt
    source_paths["GLD"] = args.gld
    missing = [str(path) for path in source_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing price files: {missing}")
    prices: dict[str, pd.Series] = {}
    for symbol in all_symbols():
        path = source_paths[symbol]
        prices[symbol] = (
            load_vendor_price(path, symbol)
            if symbol in {"TLT", "GLD"}
            else load_canonical_price(path, symbol)
        )
    intervals, interval_source = load_intervals(args.intervals)
    payload = build_payload(
        prices=prices,
        source_labels={symbol: str(path) for symbol, path in source_paths.items()},
        intervals=intervals,
        start=args.start,
        end=args.end,
    )
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(build_html(payload), encoding="utf-8")
    metadata = build_metadata(
        builder_path=Path(__file__).resolve(),
        interval_path=args.intervals,
        interval_source=interval_source,
        source_paths=source_paths,
        payload=payload,
        output_html=args.output_html,
    )
    args.output_metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output_html}")
    print(f"Wrote {args.output_metadata}")


if __name__ == "__main__":
    main()
