#!/usr/bin/env python3
"""Build a self-contained Close plus normalized RSI/StochRSI market view."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly import colors, io as pio
from plotly.subplots import make_subplots


VIEW_ID = "close_rsi_stochrsi_periods_v1"
DEFAULT_PERIODS = (14, 28, 42, 56, 70, 100, 140)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_periods(value: str) -> tuple[int, ...]:
    try:
        periods = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("periods must be comma-separated integers") from exc
    if not periods or any(period < 2 for period in periods):
        raise ValueError("every period must be at least 2")
    if len(periods) != len(set(periods)):
        raise ValueError("periods must not contain duplicates")
    return periods


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Return Wilder RSI, with the first value after exactly period deltas."""

    values = pd.Series(close, dtype=float).reset_index(drop=True)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(result, index=close.index, dtype=float)

    delta = values.diff().to_numpy(dtype=float)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(np.mean(gains[1 : period + 1]))
    average_loss = float(np.mean(losses[1 : period + 1]))

    def value(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 50.0 if gain == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index] = value(average_gain, average_loss)
    return pd.Series(result, index=close.index, dtype=float)


def prepare_view(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start: str,
    end: str,
    periods: Sequence[int],
) -> pd.DataFrame:
    required = {"date", "symbol", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if not periods or any(period < 2 for period in periods):
        raise ValueError("At least one period >= 2 is required")

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="raise")
    data = data[data["symbol"] == symbol].sort_values("date").reset_index(drop=True)
    if data.empty:
        raise ValueError(f"No rows found for symbol {symbol}")
    if data["date"].duplicated().any():
        raise ValueError(f"Duplicate dates found for symbol {symbol}")
    data["close"] = pd.to_numeric(data["close"], errors="raise")
    if data["close"].isna().any() or (data["close"] <= 0).any():
        raise ValueError("Close must contain only positive, non-null values")

    for period in periods:
        rsi = wilder_rsi(data["close"], period)
        low = rsi.rolling(period, min_periods=period).min()
        high = rsi.rolling(period, min_periods=period).max()
        span = high - low
        stochrsi = (rsi - low) / span
        stochrsi.loc[span.eq(0.0) & low.notna()] = 0.5
        data[f"rsi{period}"] = rsi / 100.0
        data[f"stochrsi{period}"] = stochrsi

    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    view = data[(data["date"] >= start_date) & (data["date"] <= end_date)].copy()
    if view.empty:
        raise ValueError("Requested interval contains no trading sessions")
    unavailable = [
        column
        for period in periods
        for column in (f"rsi{period}", f"stochrsi{period}")
        if not view[column].notna().any()
    ]
    if unavailable:
        raise ValueError(f"Requested interval has no usable values for: {unavailable}")
    return view


def _figure_html(figure: go.Figure, div_id: str) -> str:
    figure_dict = figure.to_plotly_json()
    for trace, trace_dict in zip(figure.data, figure_dict["data"], strict=True):
        trace_dict["x"] = [
            value.isoformat() if hasattr(value, "isoformat") else str(value)
            for value in trace.x
        ]
        trace_dict["y"] = [None if pd.isna(value) else float(value) for value in trace.y]
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
    periods: Sequence[int],
    source_label: str,
) -> str:
    palette = colors.sample_colorscale(
        "Turbo", [index / max(len(periods) - 1, 1) for index in range(len(periods))]
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.58, 0.42],
        vertical_spacing=0.075,
        subplot_titles=(f"{symbol} Close", "RSI 与 Raw StochRSI（0–1）"),
    )
    figure.add_trace(
        go.Scatter(
            x=view["date"],
            y=view["close"],
            mode="lines",
            name=f"{symbol} Close",
            line={"color": "#111827", "width": 2.2},
            meta={"series_key": "close", "panel": "price"},
            hovertemplate="%{x|%Y-%m-%d}<br>Close %{y:.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    for color, period in zip(palette, periods, strict=True):
        visible = True if period == periods[0] else "legendonly"
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[f"rsi{period}"],
                mode="lines",
                name=f"RSI({period}) / 100",
                line={"color": color, "width": 1.8},
                visible=visible,
                meta={"series_key": f"rsi{period}", "period": period, "kind": "rsi", "panel": "indicator"},
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>RSI({period}) %{{y:.4f}}<extra></extra>",
            ),
            row=2,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=view["date"],
                y=view[f"stochrsi{period}"],
                mode="lines",
                name=f"Raw StochRSI({period})",
                line={"color": color, "width": 1.35, "dash": "dot"},
                opacity=0.88,
                visible=visible,
                meta={"series_key": f"stochrsi{period}", "period": period, "kind": "stochrsi", "panel": "indicator"},
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>StochRSI({period}) %{{y:.4f}}<extra></extra>",
            ),
            row=2,
            col=1,
        )

    figure.add_hline(y=0.2, line_dash="dash", line_color="#94a3b8", line_width=1, row=2, col=1)
    figure.add_hline(y=0.8, line_dash="dash", line_color="#94a3b8", line_width=1, row=2, col=1)
    figure.update_layout(
        template="plotly_white",
        height=980,
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        margin={"l": 70, "r": 28, "t": 68, "b": 65},
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
    figure.update_yaxes(title="拆股及股息调整 Close", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title="指标值", range=[-0.03, 1.03], fixedrange=False, row=2, col=1)

    period_controls = "".join(
        f'<label><input type="checkbox" data-period="{period}"{ " checked" if period == periods[0] else ""}> Period {period}</label>'
        for period in periods
    )
    graph_id = "rsi-stochrsi-market-view"
    chart = _figure_html(figure, graph_id)
    first_date = pd.Timestamp(view["date"].iloc[0]).date().isoformat()
    last_date = pd.Timestamp(view["date"].iloc[-1]).date().isoformat()
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="quant-view" content="{VIEW_ID}">
<title>{html.escape(symbol)} Close、RSI 与 StochRSI</title>
<style>
body{{margin:0;background:#eef2f6;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1620px;margin:0 auto;padding:24px}}.card{{background:white;border-radius:12px;padding:18px;box-shadow:0 2px 10px rgba(15,23,42,.08)}}
.controls{{margin:16px 0;padding:12px;background:#f8fafc;border:1px solid #dbe3eb;border-radius:9px}}
.actions,.periods{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.periods{{margin-top:10px}}
.periods label{{display:inline-flex;gap:5px;align-items:center;padding:6px 9px;background:white;border:1px solid #dbe3eb;border-radius:999px}}
button{{border:1px solid #b8c5d1;background:white;border-radius:6px;padding:7px 10px;cursor:pointer}}button:hover{{background:#eaf2f8}}
.note,.status{{color:#52606d;font-size:13px;line-height:1.55}}h1{{margin-bottom:6px}}.key{{display:inline-block;width:24px;border-top:3px solid #475569;vertical-align:middle;margin:0 4px}}.key.dot{{border-top-style:dotted}}
@media(max-width:700px){{main{{padding:10px}}.card{{padding:10px}}}}
</style></head><body><main><div class="card">
<h1>{html.escape(symbol)} Close、RSI 与 Stochastic RSI</h1>
<p>{html.escape(requested_start)} 至 {html.escape(requested_end)}；实际包含 {len(view)} 个交易日。</p>
<p class="note">上图只显示每日 Close。下图将 Wilder RSI 除以 100 后与未平滑 Raw StochRSI 放在同一个 0–1 坐标中；<span class="key"></span>实线是 RSI，<span class="key dot"></span>虚线是 StochRSI。同一 period 同时用于 RSI 计算和 StochRSI 的 RSI 高低区间，不计算 K/D。所有指标先使用图表开始日前的历史预热，再截取显示区间。灰色虚线是 0.2 与 0.8。数据源：{html.escape(source_label)}。</p>
<div class="controls" data-target="{graph_id}"><div class="actions"><strong>周期选择</strong>
<button type="button" data-action="all">全部显示</button><button type="button" data-action="none">全部隐藏</button><button type="button" data-action="reset">恢复全区间</button>
<span class="status">勾选一个 period 会同时显示该周期的 RSI 与 StochRSI；默认只显示 {periods[0]}。</span></div>
<div class="periods">{period_controls}</div></div>
{chart}
</div><script>
(function(){{'use strict';
const graph=document.getElementById('{graph_id}');const controls=document.querySelector('.controls[data-target="{graph_id}"]');let updating=false;
function periodIndices(period){{const found=[];graph.data.forEach((trace,index)=>{{if(trace.meta&&Number(trace.meta.period)===Number(period))found.push(index)}});return found}}
function currentRange(){{for(const name of ['xaxis2','xaxis']){{const axis=graph._fullLayout&&graph._fullLayout[name];if(axis&&Array.isArray(axis.range))return axis.range}}return null}}
function priceRange(current){{let low=Infinity,high=-Infinity;const trace=graph.data.find(item=>item.meta&&item.meta.panel==='price');if(!trace)return null;const start=current?new Date(current[0]).getTime():-Infinity,end=current?new Date(current[1]).getTime():Infinity;Array.from(trace.x).forEach((date,index)=>{{const when=new Date(date).getTime(),value=Number(trace.y[index]);if(when>=start&&when<=end&&Number.isFinite(value)){{low=Math.min(low,value);high=Math.max(high,value)}}}});if(!Number.isFinite(low)||!Number.isFinite(high))return null;const pad=Math.max((high-low)*.08,Math.abs(high)*.004,.01);return [low-pad,high+pad]}}
function rescalePrice(){{if(updating)return;const range=priceRange(currentRange());if(!range)return;updating=true;Plotly.relayout(graph,{{'yaxis.autorange':false,'yaxis.range':range,'yaxis2.autorange':false,'yaxis2.range':[-.03,1.03]}}).then(()=>{{updating=false}})}}
function setPeriod(box){{Plotly.restyle(graph,{{visible:box.checked?true:'legendonly'}},periodIndices(box.dataset.period));window.setTimeout(rescalePrice,80)}}
controls.querySelectorAll('input[data-period]').forEach(box=>box.addEventListener('change',()=>setPeriod(box)));
function setAll(visible){{controls.querySelectorAll('input[data-period]').forEach(box=>{{box.checked=visible;Plotly.restyle(graph,{{visible:visible?true:'legendonly'}},periodIndices(box.dataset.period))}});window.setTimeout(rescalePrice,100)}}
controls.querySelector('[data-action="all"]').addEventListener('click',()=>setAll(true));
controls.querySelector('[data-action="none"]').addEventListener('click',()=>setAll(false));
controls.querySelector('[data-action="reset"]').addEventListener('click',()=>Plotly.relayout(graph,{{'xaxis2.range':['{first_date}','{last_date}']}}).then(rescalePrice));
graph.on('plotly_relayout',event=>{{if(updating)return;const changed=Object.keys(event).some(key=>key.indexOf('xaxis.range')===0||key.indexOf('xaxis2.range')===0||key==='xaxis.autorange'||key==='xaxis2.autorange');if(changed)window.requestAnimationFrame(rescalePrice)}});
window.__rsiStochView={{graph,controls,periodIndices,rescalePrice}};window.requestAnimationFrame(rescalePrice);
}}());
</script></main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--periods", default=",".join(map(str, DEFAULT_PERIODS)))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    periods = parse_periods(args.periods)
    frame = pd.read_csv(args.input)
    view = prepare_view(
        frame,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        periods=periods,
    )
    output = args.output.resolve()
    metadata_path = args.metadata.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_html(
            view,
            symbol=args.symbol,
            requested_start=args.start,
            requested_end=args.end,
            periods=periods,
            source_label=str(args.input),
        ),
        encoding="utf-8",
    )
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
        "periods": list(periods),
        "rsi_method": "Wilder RSI divided by 100 for display",
        "stochrsi_formula": "(RSI - rolling_min(RSI, period)) / (rolling_max(RSI, period) - rolling_min(RSI, period)); zero range maps to 0.5",
        "same_period_for_rsi_and_stochastic_range": True,
        "k_smoothing": None,
        "d_smoothing": None,
        "prewarm_before_requested_start": True,
        "default_visible_periods": [periods[0]],
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
    print(f"{len(view)} sessions; {len(periods)} periods; {len(periods) * 2} indicator series")


if __name__ == "__main__":
    main()
