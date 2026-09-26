#!/usr/bin/env python3
"""Build a self-contained full-history Close/SMA/StochRSI graph."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly import colors, io as pio
from plotly.subplots import make_subplots

from build_rsi_stochrsi_market_view import wilder_rsi


VIEW_ID = "full_history_sma_stochrsi_graph_v1"
DEFAULT_VISIBLE_SMA = (50, 100, 200, 350)
DEFAULT_VISIBLE_STOCHRSI = (14, 42, 98, 210)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer_range(start: int, end: int, step: int, *, label: str) -> tuple[int, ...]:
    if start < 2 or end < start or step < 1:
        raise ValueError(
            f"{label} range must satisfy start >= 2, end >= start, and step >= 1"
        )
    values = tuple(range(start, end + 1, step))
    if not values or values[-1] != end:
        raise ValueError(f"{label} end must be reachable exactly from start using step")
    return values


def prepare_view(
    frame: pd.DataFrame,
    *,
    symbol: str,
    sma_windows: Sequence[int],
    stochrsi_periods: Sequence[int],
) -> pd.DataFrame:
    required = {"date", "symbol", "close"}
    if missing := required.difference(frame.columns):
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if not sma_windows or any(window < 2 for window in sma_windows):
        raise ValueError("At least one SMA window >= 2 is required")
    if not stochrsi_periods or any(period < 2 for period in stochrsi_periods):
        raise ValueError("At least one StochRSI period >= 2 is required")
    if len(sma_windows) != len(set(sma_windows)):
        raise ValueError("SMA windows must not contain duplicates")
    if len(stochrsi_periods) != len(set(stochrsi_periods)):
        raise ValueError("StochRSI periods must not contain duplicates")

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
    data = data[data["symbol"] == symbol].sort_values("date", kind="stable")
    data = data.reset_index(drop=True)
    if data.empty:
        raise ValueError(f"No rows found for symbol {symbol}")
    if data["date"].duplicated().any():
        raise ValueError(f"Duplicate dates found for symbol {symbol}")
    data["close"] = pd.to_numeric(data["close"], errors="raise")
    if data["close"].isna().any() or (data["close"] <= 0).any():
        raise ValueError("Close must contain only positive, non-null values")

    close = data["close"].astype(float)
    for window in sma_windows:
        data[f"sma{window}"] = close.rolling(window, min_periods=window).mean()
    for period in stochrsi_periods:
        rsi = wilder_rsi(close, period)
        low = rsi.rolling(period, min_periods=period).min()
        high = rsi.rolling(period, min_periods=period).max()
        span = high - low
        stochrsi = (rsi - low) / span
        stochrsi.loc[span.eq(0.0) & low.notna()] = 0.5
        data[f"stochrsi{period}"] = stochrsi

    unavailable = [
        column
        for column in [
            *[f"sma{window}" for window in sma_windows],
            *[f"stochrsi{period}" for period in stochrsi_periods],
        ]
        if not data[column].notna().any()
    ]
    if unavailable:
        raise ValueError(f"Full history has no usable values for: {unavailable}")
    return data


def build_figure(
    view: pd.DataFrame,
    *,
    symbol: str,
    sma_windows: Sequence[int],
    stochrsi_periods: Sequence[int],
) -> go.Figure:
    sma_palette = colors.sample_colorscale(
        "Turbo",
        [index / max(len(sma_windows) - 1, 1) for index in range(len(sma_windows))],
    )
    stoch_palette = colors.sample_colorscale(
        "Viridis",
        [index / max(len(stochrsi_periods) - 1, 1) for index in range(len(stochrsi_periods))],
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.66, 0.34],
        vertical_spacing=0.075,
        subplot_titles=(f"{symbol} Close 与 SMA", "Raw StochRSI（0–1）"),
    )
    figure.add_trace(
        go.Scatter(
            x=view["date"],
            y=view["close"],
            mode="lines",
            name=f"{symbol} Close",
            line={"color": "#111827", "width": 2.2},
            meta={"series_key": "close", "panel": "price", "group": "price"},
            hovertemplate="%{x|%Y-%m-%d}<br>Close %{y:.4f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    for color, window in zip(sma_palette, sma_windows, strict=True):
        column = f"sma{window}"
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[column],
                mode="lines",
                name=f"SMA {window}",
                line={"color": color, "width": 1.15},
                opacity=0.86,
                visible=True if window in DEFAULT_VISIBLE_SMA else "legendonly",
                meta={"series_key": column, "panel": "price", "group": "sma"},
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>SMA {window} %{{y:.4f}}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    for color, period in zip(stoch_palette, stochrsi_periods, strict=True):
        column = f"stochrsi{period}"
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[column],
                mode="lines",
                name=f"StochRSI {period}",
                line={"color": color, "width": 1.35},
                opacity=0.9,
                visible=(
                    True if period in DEFAULT_VISIBLE_STOCHRSI else "legendonly"
                ),
                meta={
                    "series_key": column,
                    "panel": "stochrsi",
                    "group": "stochrsi",
                },
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>StochRSI {period} %{{y:.4f}}"
                    "<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )

    figure.add_hline(
        y=0.2,
        line_dash="dash",
        line_color="#94a3b8",
        line_width=1,
        row=2,
        col=1,
    )
    figure.add_hline(
        y=0.8,
        line_dash="dash",
        line_color="#94a3b8",
        line_width=1,
        row=2,
        col=1,
    )
    figure.update_layout(
        template="plotly_white",
        height=980,
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        margin={"l": 72, "r": 30, "t": 70, "b": 68},
        uirevision=f"{symbol}-{VIEW_ID}",
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
    )
    figure.update_xaxes(
        title="交易日",
        rangeslider={"visible": True, "thickness": 0.08},
        row=2,
        col=1,
    )
    figure.update_yaxes(
        title="拆股及股息调整 Close / SMA",
        type="log",
        fixedrange=False,
        row=1,
        col=1,
    )
    figure.update_yaxes(
        title="StochRSI",
        range=[-0.03, 1.03],
        fixedrange=False,
        row=2,
        col=1,
    )
    return figure


def _figure_html(figure: go.Figure, div_id: str) -> str:
    figure_dict = figure.to_plotly_json()
    for trace, trace_dict in zip(figure.data, figure_dict["data"], strict=True):
        trace_dict["x"] = [
            value.isoformat() if hasattr(value, "isoformat") else str(value)
            for value in trace.x
        ]
        trace_dict["y"] = [
            None if pd.isna(value) else float(value) for value in trace.y
        ]
    return pio.to_html(
        figure_dict,
        full_html=False,
        include_plotlyjs="inline",
        config={"scrollZoom": True, "responsive": True, "displaylogo": False},
        div_id=div_id,
        validate=False,
    )


def _checkbox(
    *, series_key: str, label: str, group: str, checked: bool, color: str
) -> str:
    checked_attribute = " checked" if checked else ""
    default = "true" if checked else "false"
    return (
        f'<label><input type="checkbox" data-series="{html.escape(series_key)}" '
        f'data-group="{html.escape(group)}" data-default="{default}"'
        f'{checked_attribute}><i style="--series-color:{html.escape(color)}"></i>'
        f'{html.escape(label)}</label>'
    )


def build_html(
    view: pd.DataFrame,
    *,
    symbol: str,
    sma_windows: Sequence[int],
    stochrsi_periods: Sequence[int],
    source_label: str,
) -> str:
    figure = build_figure(
        view,
        symbol=symbol,
        sma_windows=sma_windows,
        stochrsi_periods=stochrsi_periods,
    )
    sma_palette = colors.sample_colorscale(
        "Turbo",
        [index / max(len(sma_windows) - 1, 1) for index in range(len(sma_windows))],
    )
    stoch_palette = colors.sample_colorscale(
        "Viridis",
        [index / max(len(stochrsi_periods) - 1, 1) for index in range(len(stochrsi_periods))],
    )
    close_control = _checkbox(
        series_key="close",
        label=f"{symbol} Close",
        group="price",
        checked=True,
        color="#111827",
    )
    sma_controls = "".join(
        _checkbox(
            series_key=f"sma{window}",
            label=f"SMA {window}",
            group="sma",
            checked=window in DEFAULT_VISIBLE_SMA,
            color=color,
        )
        for color, window in zip(sma_palette, sma_windows, strict=True)
    )
    stoch_controls = "".join(
        _checkbox(
            series_key=f"stochrsi{period}",
            label=f"StochRSI {period}",
            group="stochrsi",
            checked=period in DEFAULT_VISIBLE_STOCHRSI,
            color=color,
        )
        for color, period in zip(stoch_palette, stochrsi_periods, strict=True)
    )
    first_date = view["date"].iloc[0].date().isoformat()
    last_date = view["date"].iloc[-1].date().isoformat()
    graph_id = "full-history-indicator-graph"
    chart = _figure_html(figure, graph_id)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}">
<title>{html.escape(symbol)} 全历史 SMA / StochRSI</title>
<style>
:root{{--bg:#eef2f6;--card:#fff;--ink:#17212b;--muted:#5b6875;--line:#d7e0e8;--accent:#175cd3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1880px;margin:0 auto;padding:20px}}.card{{background:var(--card);border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(15,23,42,.08)}}
h1{{margin:0 0 6px;font-size:25px}}.summary,.note{{color:var(--muted);line-height:1.55}}.note{{font-size:13px}}
.toolbar{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:14px 0}}button{{border:1px solid #b8c5d1;background:white;border-radius:6px;padding:7px 10px;cursor:pointer}}button:hover{{background:#eef5ff}}
.axis-toggle{{display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#f8fafc;font-size:13px}}
.control-panel{{display:grid;grid-template-columns:1fr;gap:10px;margin:12px 0 16px}}.control-group{{border:1px solid var(--line);border-radius:9px;background:#f8fafc;padding:10px}}
.group-head{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}}.group-head strong{{margin-right:4px}}.checks{{display:flex;gap:6px;flex-wrap:wrap;max-height:148px;overflow:auto}}
.checks label{{display:inline-flex;align-items:center;gap:5px;background:white;border:1px solid var(--line);border-radius:999px;padding:5px 8px;font-size:12px;cursor:pointer}}.checks i{{width:13px;border-top:3px solid var(--series-color);display:inline-block}}
.chart-wrap{{min-height:980px}}@media(max-width:700px){{main{{padding:8px}}.card{{padding:10px}}h1{{font-size:21px}}.checks{{max-height:190px}}}}
</style></head><body><main><section class="card">
<h1>{html.escape(symbol)} 全历史 Close / SMA / StochRSI</h1>
<p class="summary">{first_date} 至 {last_date}，共 {len(view):,} 个交易日。上方价格图可选 SMA 10–350（步长 10），下方可选 Raw StochRSI 14–210（步长 14）。</p>
<p class="note">SMA 仅按拆股及股息调整 Close 计算。StochRSI 使用 Wilder RSI，并以同一周期计算 RSI 滚动高低区间；零区间记为 0.5，不计算 K/D。上市初期预热空值保留。数据源：{html.escape(source_label)}。</p>
<div class="toolbar"><button type="button" data-action="defaults">恢复默认勾选</button><button type="button" data-action="reset-range">恢复全历史</button><label class="axis-toggle"><input type="checkbox" data-action="log" checked>价格对数轴</label></div>
<div class="control-panel" data-target="{graph_id}">
  <div class="control-group"><div class="group-head"><strong>价格</strong></div><div class="checks">{close_control}</div></div>
  <div class="control-group"><div class="group-head"><strong>SMA</strong><button type="button" data-group-action="sma-all">全选</button><button type="button" data-group-action="sma-none">全关</button><button type="button" data-group-action="sma-default">默认</button></div><div class="checks">{sma_controls}</div></div>
  <div class="control-group"><div class="group-head"><strong>StochRSI</strong><button type="button" data-group-action="stochrsi-all">全选</button><button type="button" data-group-action="stochrsi-none">全关</button><button type="button" data-group-action="stochrsi-default">默认</button></div><div class="checks">{stoch_controls}</div></div>
</div>
<div class="chart-wrap">{chart}</div>
</section></main><script>
(function(){{'use strict';
const graph=document.getElementById('{graph_id}'),controls=document.querySelector('.control-panel[data-target="{graph_id}"]');let updating=false;
function indices(series){{const found=[];graph.data.forEach((trace,index)=>{{if(trace.meta&&trace.meta.series_key===series)found.push(index)}});return found}}
function boxes(group){{return Array.from(controls.querySelectorAll(`input[data-group="${{group}}"]`))}}
function currentRange(){{for(const name of ['xaxis2','xaxis']){{const axis=graph._fullLayout&&graph._fullLayout[name];if(axis&&Array.isArray(axis.range))return axis.range}}return null}}
function priceRange(){{const current=currentRange(),start=current?new Date(current[0]).getTime():-Infinity,end=current?new Date(current[1]).getTime():Infinity;let low=Infinity,high=-Infinity;graph.data.forEach((trace)=>{{if(!trace.meta||trace.meta.panel!=='price'||trace.visible==='legendonly'||trace.visible===false)return;Array.from(trace.x).forEach((date,index)=>{{const when=new Date(date).getTime(),value=Number(trace.y[index]);if(when>=start&&when<=end&&Number.isFinite(value)&&value>0){{low=Math.min(low,value);high=Math.max(high,value)}}}})}});if(!Number.isFinite(low)||!Number.isFinite(high))return null;if(document.querySelector('[data-action="log"]').checked){{const logLow=Math.log10(low),logHigh=Math.log10(high),pad=Math.max((logHigh-logLow)*.07,.02);return [logLow-pad,logHigh+pad]}}const pad=Math.max((high-low)*.08,Math.abs(high)*.004,.01);return [low-pad,high+pad]}}
function rescalePrice(){{if(updating)return Promise.resolve();const range=priceRange();if(!range)return Promise.resolve();updating=true;return Plotly.relayout(graph,{{'yaxis.autorange':false,'yaxis.range':range,'yaxis2.autorange':false,'yaxis2.range':[-.03,1.03]}}).finally(()=>{{updating=false}})}}
function setBox(box){{return Plotly.restyle(graph,{{visible:box.checked?true:'legendonly'}},indices(box.dataset.series))}}
function setGroup(group,mode){{const selected=boxes(group);selected.forEach((box)=>{{box.checked=mode==='all'||(mode==='default'&&box.dataset.default==='true')}});const shown=selected.filter((box)=>box.checked).flatMap((box)=>indices(box.dataset.series)),hidden=selected.filter((box)=>!box.checked).flatMap((box)=>indices(box.dataset.series));return Promise.all([shown.length?Plotly.restyle(graph,{{visible:true}},shown):Promise.resolve(),hidden.length?Plotly.restyle(graph,{{visible:'legendonly'}},hidden):Promise.resolve()]).then(rescalePrice)}}
controls.querySelectorAll('input[data-series]').forEach((box)=>box.addEventListener('change',()=>setBox(box).then(rescalePrice)));
controls.querySelectorAll('[data-group-action]').forEach((button)=>button.addEventListener('click',()=>{{const [group,mode]=button.dataset.groupAction.split('-');setGroup(group,mode)}}));
document.querySelector('[data-action="defaults"]').addEventListener('click',()=>{{controls.querySelectorAll('input[data-series]').forEach((box)=>{{box.checked=box.dataset.default==='true'}});Promise.all(Array.from(controls.querySelectorAll('input[data-series]')).map(setBox)).then(rescalePrice)}});
document.querySelector('[data-action="reset-range"]').addEventListener('click',()=>Plotly.relayout(graph,{{'xaxis.range':['{first_date}','{last_date}'],'xaxis2.range':['{first_date}','{last_date}']}}).then(rescalePrice));
document.querySelector('[data-action="log"]').addEventListener('change',(event)=>Plotly.relayout(graph,{{'yaxis.type':event.target.checked?'log':'linear'}}).then(rescalePrice));
graph.on('plotly_relayout',(event)=>{{if(updating)return;const changed=Object.keys(event).some((key)=>key.indexOf('xaxis.range')===0||key.indexOf('xaxis2.range')===0||key==='xaxis.autorange'||key==='xaxis2.autorange');if(changed)window.requestAnimationFrame(rescalePrice)}});
window.__indicatorGraph={{graph,controls,indices,boxes,setGroup,rescalePrice,ready:true}};window.requestAnimationFrame(rescalePrice);
}}());
</script></body></html>'''


def metadata_payload(
    view: pd.DataFrame,
    *,
    symbol: str,
    sma_windows: Sequence[int],
    stochrsi_periods: Sequence[int],
    source: Path,
    output: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "view_id": VIEW_ID,
        "symbol": symbol,
        "actual_start": view["date"].iloc[0].date().isoformat(),
        "actual_end": view["date"].iloc[-1].date().isoformat(),
        "trading_sessions": len(view),
        "price_field": "close",
        "price_adjustment": "split_and_dividend_adjusted canonical Close",
        "sma_windows": list(sma_windows),
        "sma_formula": "rolling mean of adjusted Close with min_periods equal to the SMA window",
        "stochrsi_periods": list(stochrsi_periods),
        "rsi_method": "Wilder RSI",
        "stochrsi_formula": "(RSI - rolling_min(RSI, period)) / (rolling_max(RSI, period) - rolling_min(RSI, period)); zero range maps to 0.5",
        "same_period_for_rsi_and_stochastic_range": True,
        "k_smoothing": None,
        "d_smoothing": None,
        "warmup_policy": "preserve all Close rows and retain incomplete indicator values as null",
        "default_visible_sma": list(DEFAULT_VISIBLE_SMA),
        "default_visible_stochrsi": list(DEFAULT_VISIBLE_STOCHRSI),
        "default_price_axis": "log",
        "source": str(source),
        "source_sha256": sha256(source),
        "output": str(output),
        "output_sha256": sha256(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sma-start", type=int, default=10)
    parser.add_argument("--sma-end", type=int, default=350)
    parser.add_argument("--sma-step", type=int, default=10)
    parser.add_argument("--stochrsi-start", type=int, default=14)
    parser.add_argument("--stochrsi-end", type=int, default=210)
    parser.add_argument("--stochrsi-step", type=int, default=14)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    sma_windows = integer_range(args.sma_start, args.sma_end, args.sma_step, label="SMA")
    stochrsi_periods = integer_range(
        args.stochrsi_start, args.stochrsi_end, args.stochrsi_step, label="StochRSI"
    )
    view = prepare_view(
        pd.read_csv(args.input),
        symbol=args.symbol,
        sma_windows=sma_windows,
        stochrsi_periods=stochrsi_periods,
    )
    output = args.output.resolve()
    metadata = args.metadata.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_html(
            view,
            symbol=args.symbol,
            sma_windows=sma_windows,
            stochrsi_periods=stochrsi_periods,
            source_label=str(args.input),
        ),
        encoding="utf-8",
    )
    metadata.write_text(
        json.dumps(
            metadata_payload(
                view,
                symbol=args.symbol,
                sma_windows=sma_windows,
                stochrsi_periods=stochrsi_periods,
                source=args.input,
                output=args.output,
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    print(f"Wrote {metadata}")
    print(
        f"{len(view)} sessions; {len(sma_windows)} SMA traces; "
        f"{len(stochrsi_periods)} StochRSI traces"
    )


if __name__ == "__main__":
    main()
