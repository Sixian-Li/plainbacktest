#!/usr/bin/env python3
"""Build a self-contained Close/SMA market view with a derivative panel."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly import colors, io as pio
from plotly.subplots import make_subplots


VIEW_ID = "close_sma_cluster_derivative_v4"
SMA_AVERAGE_COLUMN = "sma_average"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sma_windows(start: int, end: int, step: int) -> tuple[int, ...]:
    if start < 2 or end < start or step < 1:
        raise ValueError("SMA range must satisfy start >= 2, end >= start, and step >= 1")
    windows = tuple(range(start, end + 1, step))
    if not windows or windows[-1] != end:
        raise ValueError("SMA end must be reachable exactly from start using step")
    return windows


def prepare_view(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start: str,
    end: str,
    windows: Sequence[int],
    overlay_windows: Sequence[int] = (),
) -> pd.DataFrame:
    required = {"date", "symbol", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if not windows:
        raise ValueError("At least one SMA window is required")
    calculation_windows = tuple(dict.fromkeys([*windows, *overlay_windows]))
    if any(window < 2 for window in calculation_windows):
        raise ValueError("Every SMA window must be at least 2")

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise")
    data = data[data["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError(f"No rows found for symbol {symbol}")
    if data["date"].duplicated().any():
        raise ValueError(f"Duplicate dates found for symbol {symbol}")
    if not pd.api.types.is_numeric_dtype(data["close"]):
        data["close"] = pd.to_numeric(data["close"], errors="raise")
    if data["close"].isna().any() or (data["close"] <= 0).any():
        raise ValueError("Close must contain only positive, non-null values")

    for window in calculation_windows:
        column = f"sma{window}"
        data[column] = data["close"].rolling(window, min_periods=window).mean()
    sma_columns = [f"sma{window}" for window in windows]
    data[SMA_AVERAGE_COLUMN] = data[sma_columns].mean(axis=1, skipna=False)

    indicator_columns = [*sma_columns, SMA_AVERAGE_COLUMN]
    for column in indicator_columns:
        derivative = data[column].pct_change(fill_method=None) * 100
        data[f"{column}_change_pct"] = derivative.replace(
            [float("inf"), float("-inf")], pd.NA
        )

    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    view = data[(data["date"] >= start_date) & (data["date"] <= end_date)].copy()
    if view.empty:
        raise ValueError("Requested interval contains no trading sessions")

    unavailable_sma = [
        f"sma{window}"
        for window in calculation_windows
        if not view[f"sma{window}"].notna().any()
    ]
    unavailable_derivative = [
        column
        for column in indicator_columns
        if not view[f"{column}_change_pct"].notna().any()
    ]
    if unavailable_sma or unavailable_derivative:
        raise ValueError(
            "Requested interval has no usable values for these indicators: "
            f"{[*unavailable_sma, *unavailable_derivative]}. "
            "Include more history or use shorter windows."
        )
    return view


def _figure_html(figure: go.Figure, div_id: str) -> str:
    figure_dict = figure.to_plotly_json()
    for trace, trace_dict in zip(figure.data, figure_dict["data"], strict=True):
        trace_dict["x"] = [
            value.isoformat() if hasattr(value, "isoformat") else str(value)
            for value in trace.x
        ]
        trace_dict["y"] = [
            None if pd.isna(value) else float(value)
            for value in trace.y
        ]
    return pio.to_html(
        figure_dict,
        full_html=False,
        include_plotlyjs="inline",
        config={"scrollZoom": True, "responsive": True, "displaylogo": False},
        div_id=div_id,
        validate=False,
    )


def build_html(
    view: pd.DataFrame,
    *,
    symbol: str,
    requested_start: str,
    requested_end: str,
    windows: Sequence[int],
    source_label: str,
    overlay_windows: Sequence[int] = (),
) -> str:
    sma_columns = [f"sma{window}" for window in windows]
    overlay_columns = [
        f"sma{window}" for window in overlay_windows if window not in windows
    ]
    core_series = [*sma_columns, SMA_AVERAGE_COLUMN]
    derivative_series = [f"{column}_change_pct" for column in core_series]
    labels = {
        **{f"sma{window}": f"SMA{window}" for window in windows},
        **{f"sma{window}": f"SMA{window}" for window in overlay_windows},
        SMA_AVERAGE_COLUMN: (
            "SMA25/30/35 平均"
            if tuple(windows) == (25, 30, 35)
            else "SMA 平均"
        ),
    }
    palette = (
        ["#2563eb", "#f59e0b", "#16a34a"]
        if len(sma_columns) == 3
        else colors.sample_colorscale(
            "Turbo",
            [
                index / max(len(sma_columns) - 1, 1)
                for index in range(len(sma_columns))
            ],
        )
    )
    overlay_palette = colors.sample_colorscale(
        "Turbo",
        [
            0.05 + 0.9 * index / max(len(overlay_columns) - 1, 1)
            for index in range(len(overlay_columns))
        ],
    )
    series_colors = {
        **dict(zip(sma_columns, palette, strict=True)),
        **dict(zip(overlay_columns, overlay_palette, strict=True)),
        SMA_AVERAGE_COLUMN: "#7c3aed",
    }

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.075,
        subplot_titles=(
            f"{symbol} Close 与 SMA",
            "SMA 相对前一交易日变化（%）",
        ),
    )
    figure.add_trace(
        go.Scatter(
            x=view["date"],
            y=view["close"],
            mode="lines",
            name=f"{symbol} Close",
            line={"color": "#111827", "width": 2.2},
            meta={"series_key": "close", "panel": "main", "group": "price"},
            hovertemplate="%{x|%Y-%m-%d}<br>Close %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    for column in core_series:
        is_average = column == SMA_AVERAGE_COLUMN
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[column],
                mode="lines",
                name=labels[column],
                line={
                    "color": series_colors[column],
                    "width": 2.4 if is_average else 1.45,
                    "dash": "dash" if is_average else "solid",
                },
                opacity=0.95 if is_average else 0.82,
                meta={"series_key": column, "panel": "main", "group": "core"},
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>{labels[column]} %{{y:.2f}}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    for column in overlay_columns:
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[column],
                mode="lines",
                name=labels[column],
                line={"color": series_colors[column], "width": 1.0},
                opacity=0.68,
                visible="legendonly",
                meta={"series_key": column, "panel": "main", "group": "overlay"},
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>{labels[column]} %{{y:.2f}}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
    for column, derivative_column in zip(core_series, derivative_series, strict=True):
        is_average = column == SMA_AVERAGE_COLUMN
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[derivative_column],
                mode="lines",
                name=f"{labels[column]} 日变化率",
                line={
                    "color": series_colors[column],
                    "width": 2.2 if is_average else 1.25,
                    "dash": "dash" if is_average else "solid",
                },
                opacity=0.95 if is_average else 0.8,
                meta={
                    "series_key": derivative_column,
                    "panel": "derivative",
                    "group": "derivative",
                },
                hovertemplate=(
                    f"%{{x|%Y-%m-%d}}<br>{labels[column]} 日变化 %{{y:.4f}}%"
                    "<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )

    figure.update_layout(
        template="plotly_white",
        height=940,
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        margin={"l": 70, "r": 28, "t": 65, "b": 65},
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
        rangeslider={"visible": True, "thickness": 0.09},
        row=2,
        col=1,
    )
    figure.update_yaxes(
        title="拆股及股息调整价格",
        fixedrange=False,
        row=1,
        col=1,
    )
    figure.update_yaxes(
        title="日变化（%）",
        fixedrange=False,
        zeroline=True,
        zerolinecolor="#64748b",
        zerolinewidth=1,
        row=2,
        col=1,
    )

    core_checkboxes = "".join(
        f'<label><input type="checkbox" data-series="{column}" data-panel="main" data-group="core" checked> {html.escape(labels[column])}</label>'
        for column in core_series
    )
    overlay_checkboxes = "".join(
        f'<label><input type="checkbox" data-series="{column}" data-panel="main" data-group="overlay"> {html.escape(labels[column])}</label>'
        for column in overlay_columns
    )
    derivative_checkboxes = "".join(
        f'<label><input type="checkbox" data-series="{derivative}" data-panel="derivative" data-group="derivative" checked> {html.escape(labels[column])}</label>'
        for column, derivative in zip(core_series, derivative_series, strict=True)
    )
    graph_id = "sma-market-view"
    chart = _figure_html(figure, graph_id)
    average_label = "、".join(f"SMA{window}" for window in windows)
    overlay_label = (
        f"SMA{overlay_windows[0]}–{overlay_windows[-1]}（间隔 "
        f"{overlay_windows[1] - overlay_windows[0] if len(overlay_windows) > 1 else 0}）"
        if overlay_windows
        else "无额外长期均线"
    )
    first_date = pd.Timestamp(view["date"].iloc[0]).date().isoformat()
    last_date = pd.Timestamp(view["date"].iloc[-1]).date().isoformat()
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}">
<title>{html.escape(symbol)} Close、SMA 与日变化率</title>
<style>
body{{margin:0;background:#eef2f6;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1620px;margin:0 auto;padding:24px}} .card{{background:white;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(15,23,42,.08)}}
.controls{{margin:16px 0;padding:12px;background:#f8fafc;border:1px solid #dbe3eb;border-radius:9px}}
.actions,.series{{display:flex;gap:7px;align-items:center;flex-wrap:wrap}} .series{{margin-top:10px}}
.series label{{display:inline-flex;gap:4px;align-items:center;padding:5px 8px;background:white;border:1px solid #dbe3eb;border-radius:999px}}
.group-title{{min-width:108px}} details{{margin-top:10px}} summary{{cursor:pointer;font-weight:600;color:#334155}} .overlay-series{{max-height:190px;overflow:auto;padding:4px 0 8px}}
button{{border:1px solid #b8c5d1;background:white;border-radius:6px;padding:7px 10px;cursor:pointer}} button:hover{{background:#eaf2f8}}
.note,.status{{color:#52606d;font-size:13px;line-height:1.55}} h1{{margin-bottom:6px}}
@media(max-width:700px){{main{{padding:10px}}.card{{padding:10px}}.group-title{{min-width:auto}}}}
</style></head><body><main><div class="card">
<h1>{html.escape(symbol)} 全历史 Close、{html.escape(average_label)} 与变化率</h1>
<p>{html.escape(requested_start)} 至 {html.escape(requested_end)}；实际包含 {len(view)} 个交易日。价格只使用每日 Close。</p>
<p class="note">每条 SMA 都按完整历史滚动计算；紫色虚线是 {html.escape(average_label)} 的算术平均。主图还提供 {html.escape(overlay_label)} 共 {len(overlay_columns)} 条长期 SMA，默认隐藏、可逐条勾选。下方面板仍只有短均线三条及其平均线共 4 条“导数”，采用相对前一交易日变化率：(今日值 / 前一交易日值 − 1) × 100%。上市初期在完成均线预热前会留空，变化率再晚一个交易日出现。数据源：{html.escape(source_label)}。</p>
<div class="controls" data-target="{graph_id}"><div class="actions"><strong>快速选择</strong>
<button type="button" data-action="core-all">显示短均线</button><button type="button" data-action="core-none">隐藏短均线</button>
<button type="button" data-action="overlay-all">显示全部长期均线</button><button type="button" data-action="overlay-none">隐藏全部长期均线</button>
<button type="button" data-action="derivative-all">显示全部变化率</button><button type="button" data-action="derivative-none">隐藏全部变化率</button>
<button type="button" data-action="reset">恢复全区间</button>
<span class="status">Close 始终显示；短均线 4 条、长期均线 {len(overlay_columns)} 条、下方变化率 4 条均可独立勾选。</span></div>
<div class="series"><strong class="group-title">主图短均线</strong>{core_checkboxes}</div>
<details><summary>主图长期均线：{html.escape(overlay_label)}（展开逐条选择）</summary><div class="series overlay-series">{overlay_checkboxes}</div></details>
<div class="series"><strong class="group-title">下图变化率</strong>{derivative_checkboxes}</div></div>
{chart}
</div><script>
(function(){{'use strict';
const graph=document.getElementById('{graph_id}'); const controls=document.querySelector('.controls[data-target="{graph_id}"]'); let updating=false;
function indices(key){{const found=[];graph.data.forEach((trace,index)=>{{if(trace.meta&&trace.meta.series_key===key)found.push(index)}});return found}}
function currentRange(){{for(const name of ['xaxis2','xaxis']){{const axis=graph._fullLayout&&graph._fullLayout[name];if(axis&&Array.isArray(axis.range))return axis.range}}return null}}
function visibleIndices(dates,current){{if(!current)return dates.map((_,i)=>i);const start=new Date(current[0]).getTime(),end=new Date(current[1]).getTime();return dates.reduce((out,date,i)=>{{const value=new Date(date).getTime();if(value>=start&&value<=end)out.push(i);return out}},[])}}
function panelRange(panel,current){{let low=Infinity,high=-Infinity;graph.data.forEach(trace=>{{if((trace.meta&&trace.meta.panel)!==panel||trace.visible==='legendonly')return;visibleIndices(Array.from(trace.x),current).forEach(i=>{{const value=Number(trace.y[i]);if(Number.isFinite(value)){{low=Math.min(low,value);high=Math.max(high,value)}}}})}});if(!Number.isFinite(low)||!Number.isFinite(high))return null;const pad=Math.max((high-low)*.08,Math.abs(high)*.004,.0001);return [low-pad,high+pad]}}
function rescalePanels(){{if(updating)return;const current=currentRange(),main=panelRange('main',current),derivative=panelRange('derivative',current),update={{}};if(main){{update['yaxis.autorange']=false;update['yaxis.range']=main}}if(derivative){{update['yaxis2.autorange']=false;update['yaxis2.range']=derivative}}if(!Object.keys(update).length)return;updating=true;Plotly.relayout(graph,update).then(()=>{{updating=false}})}}
function setGroup(group,visible){{controls.querySelectorAll('input[data-group="'+group+'"]').forEach(box=>{{box.checked=visible;Plotly.restyle(graph,{{visible:visible?true:'legendonly'}},indices(box.dataset.series))}});window.setTimeout(rescalePanels,120)}}
controls.querySelectorAll('input[data-series]').forEach(box=>box.addEventListener('change',()=>{{Plotly.restyle(graph,{{visible:box.checked?true:'legendonly'}},indices(box.dataset.series));window.setTimeout(rescalePanels,80)}}));
controls.querySelector('[data-action="core-all"]').addEventListener('click',()=>setGroup('core',true));
controls.querySelector('[data-action="core-none"]').addEventListener('click',()=>setGroup('core',false));
controls.querySelector('[data-action="overlay-all"]').addEventListener('click',()=>setGroup('overlay',true));
controls.querySelector('[data-action="overlay-none"]').addEventListener('click',()=>setGroup('overlay',false));
controls.querySelector('[data-action="derivative-all"]').addEventListener('click',()=>setGroup('derivative',true));
controls.querySelector('[data-action="derivative-none"]').addEventListener('click',()=>setGroup('derivative',false));
controls.querySelector('[data-action="reset"]').addEventListener('click',()=>Plotly.relayout(graph,{{'xaxis2.range':['{first_date}','{last_date}']}}).then(rescalePanels));
graph.on('plotly_relayout',event=>{{if(updating)return;const changed=Object.keys(event).some(key=>key.indexOf('xaxis.range')===0||key.indexOf('xaxis2.range')===0||key==='xaxis.autorange'||key==='xaxis2.autorange');if(changed)window.requestAnimationFrame(rescalePanels)}});
window.requestAnimationFrame(rescalePanels);
}}());
</script></main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--sma-start", type=int, default=25)
    parser.add_argument("--sma-end", type=int, default=35)
    parser.add_argument("--sma-step", type=int, default=5)
    parser.add_argument("--overlay-start", type=int, default=70)
    parser.add_argument("--overlay-end", type=int, default=450)
    parser.add_argument("--overlay-step", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    windows = sma_windows(args.sma_start, args.sma_end, args.sma_step)
    overlay_windows = sma_windows(
        args.overlay_start, args.overlay_end, args.overlay_step
    )
    frame = pd.read_csv(args.input)
    view = prepare_view(
        frame,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        windows=windows,
        overlay_windows=overlay_windows,
    )
    output = args.output.resolve()
    metadata_path = args.metadata.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = build_html(
        view,
        symbol=args.symbol,
        requested_start=args.start,
        requested_end=args.end,
        windows=windows,
        overlay_windows=overlay_windows,
        source_label=str(args.input),
    )
    output.write_text(rendered, encoding="utf-8")
    derivative_series = [
        *(f"sma{window}_change_pct" for window in windows),
        "sma_average_change_pct",
    ]
    payload = {
        "schema_version": 1,
        "view_id": VIEW_ID,
        "symbol": args.symbol,
        "requested_start": args.start,
        "requested_end": args.end,
        "actual_start": view["date"].iloc[0].date().isoformat(),
        "actual_end": view["date"].iloc[-1].date().isoformat(),
        "trading_sessions": len(view),
        "price_field": "close",
        "price_adjustment": "split_and_dividend_adjusted canonical Close",
        "sma_windows": list(windows),
        "sma_average_windows": list(windows),
        "overlay_sma_windows": list(overlay_windows),
        "overlay_default_visible": False,
        "sma_calculation": "daily rolling mean of Close over full canonical history before interval filtering",
        "sma_average": "arithmetic mean of sma_average_windows only; valid only when every average component is available",
        "derivative_formula": "(current / previous trading session - 1) * 100",
        "derivative_series": derivative_series,
        "initial_warmup_gaps_preserved": True,
        "source": str(args.input),
        "source_sha256": sha256(args.input),
        "output": str(args.output),
        "output_sha256": sha256(output),
    }
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    print(f"Wrote {metadata_path}")
    print(
        f"{len(view)} sessions; {len(windows)} core SMA series; "
        f"{len(overlay_windows)} optional overlay SMA series; "
        f"1 SMA average; {len(derivative_series)} derivative series"
    )


if __name__ == "__main__":
    main()
