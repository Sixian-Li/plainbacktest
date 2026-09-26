#!/usr/bin/env python3
"""Analyze and render the fixed MO/AZO/TLT SMA200 flat-spell study."""

from __future__ import annotations

import argparse
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go

from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_trio_sma200_flat_spells import EXPECTED_SYMBOLS


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution"
)
FORMAL_COST_BPS = 5.0
HORIZONS = (5, 10, 20, 60)


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in frame.columns:
        if column == "date" or column.endswith("_date"):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def load_blocks(context: Any, run_id: str) -> dict[float, dict[str, dict[str, Any]]]:
    blocks: dict[float, dict[str, dict[str, Any]]] = {}
    for cost in context.config["cost_scenarios_bps_per_side"]:
        cost_value = float(cost)
        blocks[cost_value] = {}
        for symbol in EXPECTED_SYMBOLS:
            root = block_root(context, run_id, symbol, cost_value)
            if not (root / "manifest.json").is_file():
                raise FileNotFoundError(f"missing completed block: {root}")
            blocks[cost_value][symbol] = {
                "root": root,
                "daily": read_csv(root / "daily_state.csv"),
                "orders": read_csv(root / "orders.csv"),
                "events": read_csv(root / "flat_spells.csv"),
                "hold": read_csv(root / "buy_hold_daily.csv"),
                "metrics": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
                "manifest": json.loads((root / "manifest.json").read_text(encoding="utf-8")),
            }
    return blocks


def strategy_summary(blocks: dict[float, dict[str, dict[str, Any]]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for cost, symbol_blocks in blocks.items():
        for symbol, block in symbol_blocks.items():
            metrics = block["metrics"]
            strategy = metrics["strategy_metrics"]
            benchmark = metrics["benchmark_metrics"]
            flat = metrics["flat_spell_summary"]
            rows.append(
                {
                    "symbol": symbol,
                    "cost_bps": cost,
                    "analysis_start": flat["analysis_start"],
                    "analysis_end": flat["analysis_end"],
                    "strategy_total_return_pct": strategy["total_return_pct"],
                    "hold_total_return_pct": benchmark["total_return_pct"],
                    "strategy_cagr_pct": strategy["cagr_pct"],
                    "hold_cagr_pct": benchmark["cagr_pct"],
                    "strategy_sharpe": strategy["sharpe"],
                    "hold_sharpe": benchmark["sharpe"],
                    "strategy_max_drawdown_pct": strategy["max_drawdown_pct"],
                    "hold_max_drawdown_pct": benchmark["max_drawdown_pct"],
                    "strategy_exposure_pct": strategy["exposure_pct"],
                    "order_count": strategy["order_count"],
                    "flat_spell_count": flat["spell_count"],
                    "closed_spell_count": flat["closed_spell_count"],
                    "open_spell_count": flat["open_spell_count"],
                    "closed_negative_share_pct": flat["closed_negative_share_pct"],
                    "closed_compound_asset_return_pct": flat[
                        "closed_compound_asset_return_pct"
                    ],
                    "median_closed_asset_return_pct": flat[
                        "median_closed_asset_return_pct"
                    ],
                    "worst_closed_asset_return_pct": flat[
                        "worst_closed_asset_return_pct"
                    ],
                    "best_closed_asset_return_pct": flat[
                        "best_closed_asset_return_pct"
                    ],
                }
            )
    return pd.DataFrame(rows).sort_values(["cost_bps", "symbol"]).reset_index(drop=True)


def horizon_summary(symbol_blocks: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, block in symbol_blocks.items():
        events = block["events"]
        for horizon in HORIZONS:
            complete = events[events[f"complete_horizon_{horizon}"].astype(bool)]
            returns = complete[f"end_close_return_pct_{horizon}"].astype(float)
            rows.append(
                {
                    "symbol": symbol,
                    "horizon_sessions": horizon,
                    "complete_event_count": len(complete),
                    "negative_share_pct": float((returns < 0).mean() * 100.0),
                    "median_end_return_pct": float(returns.median()),
                    "median_min_low_pct": float(
                        complete[f"min_low_return_pct_{horizon}"].astype(float).median()
                    ),
                    "median_max_high_pct": float(
                        complete[f"max_high_return_pct_{horizon}"].astype(float).median()
                    ),
                }
            )
    return pd.DataFrame(rows)


def performance_figure(block: dict[str, Any], symbol: str) -> go.Figure:
    daily = block["daily"]
    hold = block["hold"]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=daily["date"],
            y=daily["equity"],
            name=f"{symbol} SMA200±3%",
            line={"color": "#2563eb", "width": 2},
            meta={
                "series_key": f"{symbol.lower()}_sma200_pm3",
                "label": f"{symbol} SMA200±3%",
                "panel": "equity",
                "cost_bps": FORMAL_COST_BPS,
            },
        )
    )
    figure.add_trace(
        go.Scatter(
            x=hold["date"],
            y=hold["equity"],
            name=f"{symbol} Buy & Hold",
            line={"color": "#64748b", "width": 1.7, "dash": "dash"},
            meta={
                "series_key": f"{symbol.lower()}_hold",
                "label": f"{symbol} Buy & Hold",
                "panel": "equity",
                "is_benchmark": True,
                "cost_bps": FORMAL_COST_BPS,
            },
        )
    )
    figure.update_layout(
        template="plotly_white",
        height=470,
        hovermode="x unified",
        yaxis={"title": "账户净值（美元，对数）", "type": "log"},
        xaxis={"title": "日期", "rangeslider": {"visible": False}},
        legend={"orientation": "h", "y": 1.08},
        margin={"l": 70, "r": 25, "t": 35, "b": 55},
    )
    return figure


def market_figure(block: dict[str, Any], symbol: str) -> go.Figure:
    daily = block["daily"]
    orders = block["orders"]
    events = block["events"]
    figure = go.Figure()
    market_meta = {
        "panel": "market",
        "control_group": "timing",
        "control_group_label": "显示均线、轨道和成交",
    }
    figure.add_trace(
        go.Candlestick(
            x=daily["date"],
            open=daily["open"],
            high=daily["high"],
            low=daily["low"],
            close=daily["close"],
            name="复权OHLC",
            increasing={"line": {"color": "#16a34a"}},
            decreasing={"line": {"color": "#dc2626"}},
            meta={**market_meta, "series_key": f"{symbol}_ohlc", "label": "复权OHLC"},
        )
    )
    traces = [
        ("sma200", "SMA200", "#f59e0b", 1.5, True),
        ("buy_threshold", "上轨 +3%", "#16a34a", 1.0, True),
        ("sell_threshold", "下轨 -3%", "#dc2626", 1.0, True),
    ]
    for column, label, color, width, visible in traces:
        figure.add_trace(
            go.Scatter(
                x=daily["date"],
                y=daily[column],
                name=label,
                line={"color": color, "width": width},
                visible=visible,
                meta={**market_meta, "series_key": f"{symbol}_{column}", "label": label},
            )
        )
    for side, label, color, symbol_shape in (
        ("buy", "买入成交", "#16a34a", "triangle-up"),
        ("sell", "卖出成交", "#dc2626", "triangle-down"),
    ):
        selected = orders[orders["type"] == side]
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["raw_price"],
                mode="markers",
                name=label,
                marker={"color": color, "size": 8, "symbol": symbol_shape},
                meta={**market_meta, "series_key": f"{symbol}_{side}", "label": label},
            )
        )
    for event in events.itertuples(index=False):
        figure.add_vrect(
            x0=event.sell_fill_date,
            x1=event.mark_date,
            fillcolor="rgba(148,163,184,0.14)",
            line_width=0,
            layer="below",
        )
    figure.update_layout(
        template="plotly_white",
        height=560,
        hovermode="x unified",
        yaxis={"title": "复权价格"},
        xaxis={
            "title": "日期",
            "rangeslider": {"visible": False},
            "rangeselector": {
                "buttons": [
                    {"count": 1, "label": "1年", "step": "year", "stepmode": "backward"},
                    {"count": 5, "label": "5年", "step": "year", "stepmode": "backward"},
                    {"count": 10, "label": "10年", "step": "year", "stepmode": "backward"},
                    {"step": "all", "label": "全部"},
                ]
            },
        },
        legend={"orientation": "h", "y": 1.12},
        margin={"l": 70, "r": 25, "t": 55, "b": 55},
    )
    return figure


def flat_path_points(block: dict[str, Any]) -> pd.DataFrame:
    daily = block["daily"]
    events = block["events"]
    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.sell_fill_date)
        end = pd.Timestamp(event.mark_date)
        if bool(event.closed_spell):
            path = daily[(daily["date"] >= start) & (daily["date"] < end)]
        else:
            path = daily[(daily["date"] >= start) & (daily["date"] <= end)]
        for session, item in enumerate(path.itertuples(index=False), start=1):
            rows.append(
                {
                    "spell_index": int(event.spell_index),
                    "session": session,
                    "date": item.date,
                    "normalized_close": float(item.close) / float(event.sell_raw_open) * 100.0,
                    "outcome": event.outcome,
                    "closed_spell": bool(event.closed_spell),
                }
            )
    return pd.DataFrame(rows)


def flat_paths_figure(block: dict[str, Any], symbol: str) -> go.Figure:
    points = flat_path_points(block)
    events = block["events"].set_index("spell_index")
    figure = go.Figure()
    for spell_index, group in points.groupby("spell_index"):
        event = events.loc[spell_index]
        avoided = str(event["outcome"]) == "avoided_loss"
        figure.add_trace(
            go.Scatter(
                x=group["session"],
                y=group["normalized_close"],
                mode="lines",
                name=f"#{spell_index} {event['sell_fill_date'].date()}",
                line={
                    "color": "rgba(220,38,38,0.72)" if avoided else "rgba(37,99,235,0.35)",
                    "width": 2.2 if avoided else 1.0,
                },
                hovertemplate=(
                    f"空仓#{spell_index}<br>卖出 {event['sell_fill_date'].date()}"
                    "<br>第%{x}个交易日<br>相对卖出Open %{y:.2f}<extra></extra>"
                ),
            )
        )
    figure.add_hline(y=100.0, line={"color": "#0f172a", "dash": "dash", "width": 1})
    figure.update_layout(
        template="plotly_white",
        height=500,
        xaxis_title="卖出成交后处于现金的第 N 个交易日",
        yaxis_title="标的收盘价（卖出Open=100）",
        showlegend=False,
        margin={"l": 70, "r": 25, "t": 30, "b": 60},
    )
    return figure


def event_table(events: pd.DataFrame) -> str:
    rows = []
    for event in events.itertuples(index=False):
        reentry = "尚未买回" if pd.isna(event.next_buy_fill_date) else event.next_buy_fill_date.date()
        label = "避开下跌" if event.asset_return_while_flat_pct < 0 else "错过上涨/持平"
        rows.append(
            "<tr>"
            f"<td>{int(event.spell_index)}</td><td>{event.sell_fill_date.date()}</td>"
            f"<td>{reentry}</td><td>{int(event.flat_sessions)}</td>"
            f"<td>{event.asset_return_while_flat_pct:+.2f}%</td>"
            f"<td>{event.min_low_vs_sell_open_pct:+.2f}%</td>"
            f"<td>{event.max_high_vs_sell_open_pct:+.2f}%</td>"
            f"<td>{html.escape(label)}</td>"
            "</tr>"
        )
    return (
        '<div class="event-scroll"><table class="event-table"><thead><tr>'
        "<th>#</th><th>卖出Open</th><th>下次买入Open</th><th>空仓日</th>"
        "<th>截至买回的标的收益</th><th>期间最低Low</th><th>期间最高High</th><th>解释</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def summary_html(
    formal: pd.DataFrame,
    horizons: pd.DataFrame,
    symbol_blocks: dict[str, dict[str, Any]],
) -> str:
    cards: list[str] = []
    details: list[str] = []
    for symbol in EXPECTED_SYMBOLS:
        row = formal[formal["symbol"] == symbol].iloc[0]
        lost = 100.0 - float(row["closed_negative_share_pct"])
        cards.append(
            '<article class="result-card">'
            f"<h3>{symbol}</h3>"
            f"<strong>{row['closed_negative_share_pct']:.1f}% 空仓段真正避跌</strong>"
            f"<span>{lost:.1f}% 空仓段反而错过上涨</span>"
            f"<span>择时 / Hold总收益：{row['strategy_total_return_pct']:+,.1f}% / {row['hold_total_return_pct']:+,.1f}%</span>"
            f"<span>最大回撤：{row['strategy_max_drawdown_pct']:.1f}% / {row['hold_max_drawdown_pct']:.1f}%</span>"
            f"<span>持仓率：{row['strategy_exposure_pct']:.1f}%</span>"
            "</article>"
        )
        own_horizons = horizons[horizons["symbol"] == symbol]
        horizon_rows = "".join(
            "<tr>"
            f"<td>{int(item.horizon_sessions)}</td><td>{int(item.complete_event_count)}</td>"
            f"<td>{item.negative_share_pct:.1f}%</td>"
            f"<td>{item.median_end_return_pct:+.2f}%</td>"
            f"<td>{item.median_min_low_pct:+.2f}%</td>"
            f"<td>{item.median_max_high_pct:+.2f}%</td></tr>"
            for item in own_horizons.itertuples(index=False)
        )
        details.append(
            f'<details class="event-details"><summary>{symbol}：全部空仓事件与固定前瞻</summary>'
            '<h3>卖出后的固定交易日前瞻</h3><table class="event-table"><thead><tr>'
            '<th>交易日</th><th>完整事件数</th><th>下跌占比</th><th>末日中位数</th>'
            '<th>最低Low中位数</th><th>最高High中位数</th></tr></thead><tbody>'
            f"{horizon_rows}</tbody></table><h3>逐次空仓记录</h3>"
            f"{event_table(symbol_blocks[symbol]['events'])}</details>"
        )
    return (
        '<section class="result-intro"><h2>先看结论</h2>'
        '<p>这里的“避跌”要求标的从卖出Open到下一次买入Open真的为负；正数表示现金仓错过了上涨。'
        '卡片与逐段表均完整保留失败事件，正式成本为单边5 bps。</p>'
        f'<div class="result-cards">{"".join(cards)}</div></section>'
        '<section class="event-section"><h2>每次卖出之后发生了什么</h2>'
        f'{"".join(details)}</section>'
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    run_record = load_run(context, args.run_id)
    if run_record.get("status") != "running":
        raise RuntimeError("analysis requires a running, writable run")
    if any(item["status"] != "completed" for item in run_record["expected_blocks"]):
        raise RuntimeError("all six symbol/cost blocks must be complete before analysis")

    blocks = load_blocks(context, args.run_id)
    formal_blocks = blocks[FORMAL_COST_BPS]
    summary = strategy_summary(blocks)
    formal = summary[summary["cost_bps"] == FORMAL_COST_BPS].copy()
    horizons = horizon_summary(formal_blocks)
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    summary.to_csv(analysis_root / "symbol_summary_all_costs.csv", index=False, lineterminator="\n")
    formal.to_csv(analysis_root / "symbol_summary_5bps.csv", index=False, lineterminator="\n")
    horizons.to_csv(analysis_root / "flat_spell_horizon_summary_5bps.csv", index=False, lineterminator="\n")
    for symbol in EXPECTED_SYMBOLS:
        flat_path_points(formal_blocks[symbol]).to_csv(
            analysis_root / f"{symbol.lower()}_flat_path_points_5bps.csv",
            index=False,
            lineterminator="\n",
        )

    result_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": FORMAL_COST_BPS,
        "symbols": json.loads(formal.to_json(orient="records")),
        "fixed_horizons": json.loads(horizons.to_json(orient="records")),
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "MO/AZO/TLT are retained after full-history post-selection",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(result_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    figures: list[ReportFigure] = []
    for symbol in EXPECTED_SYMBOLS:
        block = formal_blocks[symbol]
        figures.extend(
            [
                ReportFigure(
                    div_id=f"performance-{symbol.lower()}",
                    title=f"{symbol}：完整择时净值 vs Buy & Hold（5 bps）",
                    figure=performance_figure(block, symbol),
                    kind="performance",
                ),
                ReportFigure(
                    div_id=f"market-{symbol.lower()}",
                    title=f"{symbol}：SMA200±3%成交与空仓高亮",
                    figure=market_figure(block, symbol),
                    kind="market",
                ),
                ReportFigure(
                    div_id=f"flat-paths-{symbol.lower()}",
                    title=f"{symbol}：每段空仓后的实际价格路径",
                    figure=flat_paths_figure(block, symbol),
                    kind="generic",
                ),
            ]
        )
    notes = [
        "三只标的各自独立计时和持有，不构成三资产组合；SMA200首次形成时仍为空仓，必须等待第一次真实上穿+3%上轨。",
        "信号在完成Close后确认，下一标的交易日复权Open成交；正式结果使用单边5bps，0bps只做成本方向检查。",
        "灰色高亮从卖出成交Open开始，到下一次买入成交Open为止；重新买入当日Open之后不属于空仓路径。",
        "空仓回报使用原始复权价格，不用交易成本美化避跌效果；末尾尚未买回的空仓段按2026-08-04复权Close计价并单独标记。",
        "主观大小熊区间只用于逐事件重叠标签，绝不改变SMA信号或成交。",
        "MO、AZO、TLT是此前全历史复核后留下的事后候选，存在选择偏差；本报告不能直接证明未来有效。",
    ]
    report = render_interactive_report(
        title="MO / AZO / TLT：SMA200空仓期归因",
        heading="卖出之后，究竟避开下跌还是错过上涨？",
        subtitle="固定SMA200上下3%滞回；允许再次买入；完整保留每一次空仓事件。",
        summary_html=summary_html(formal, horizons, formal_blocks),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.result-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.result-card{display:flex;flex-direction:column;gap:7px;border:1px solid #dbe3ec;border-radius:12px;padding:15px;background:#fff}
.result-card strong{font-size:1.05rem}.result-card span{font-variant-numeric:tabular-nums;color:#334155}
.event-section{margin:28px 0}.event-details{border:1px solid #dbe3ec;border-radius:10px;padding:12px;margin:12px 0;background:#fff}
.event-details summary{cursor:pointer;font-weight:700}.event-scroll{overflow-x:auto}.event-table{width:100%;border-collapse:collapse;margin:12px 0;font-variant-numeric:tabular-nums}
.event-table th,.event-table td{padding:7px 9px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}.event-table th:first-child,.event-table td:first-child{text-align:left}
@media(max-width:900px){.result-cards{grid-template-columns:1fr}}@media print{.event-details{display:none}.result-card{break-inside:avoid}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="analysis/symbol_summary_5bps.csv">三标的5 bps汇总</a> · '
        '<a download href="analysis/flat_spell_horizon_summary_5bps.csv">固定前瞻汇总</a> · '
        '<a download href="MO/cost_5bps/flat_spells.csv">MO逐次空仓</a> · '
        '<a download href="AZO/cost_5bps/flat_spells.csv">AZO逐次空仓</a> · '
        '<a download href="TLT/cost_5bps/flat_spells.csv">TLT逐次空仓</a> · '
        '<a download href="analysis/symbol_summary_all_costs.csv">0/5 bps对照</a>'
        "</p></section>"
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")
    lines = ["# MO / AZO / TLT：SMA200空仓期归因", "", "## 5 bps摘要", ""]
    for row in formal.itertuples(index=False):
        lines.append(
            f"- {row.symbol}：闭合空仓段 {int(row.closed_spell_count)} 次，"
            f"其中 {row.closed_negative_share_pct:.1f}% 真正避开下跌；"
            f"择时/持有总收益 {row.strategy_total_return_pct:+.1f}% / {row.hold_total_return_pct:+.1f}%，"
            f"最大回撤 {row.strategy_max_drawdown_pct:.1f}% / {row.hold_max_drawdown_pct:.1f}%。"
        )
    lines.extend(
        [
            "",
            "## 口径",
            "",
            "- 真实上穿SMA200+3%买入，持仓Close跌破SMA200-3%卖出，信号下一Open成交。",
            "- 卖出后允许用完全相同的规则重新买入；没有固定止损、ATR止损或熊市oracle。",
            "- 正的空仓期标的回报表示策略错过上涨，负数才表示真正避跌。",
            "- 三只标的是事后保留候选，结果属于诊断，不是样本外验证。",
        ]
    )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tracked = [
        "backtest/quantkit/sma_flat_spells.py",
        "backtest/quantkit/sma_threshold.py",
        "backtest/quantkit/reference.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_trio_sma200_flat_spells.py",
        "backtest/scripts/analyze_trio_sma200_flat_spells.py",
        "backtest/scripts/finalize_trio_sma200_flat_spells.py",
        "backtest/scripts/smoke_trio_sma200_flat_spell_report.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_trio_sma200_flat_spell_attribution.py",
        "backtest/experiments/TIM/TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "software": {
            "python": platform.python_version(),
            "lib_pybroker": "1.2.12",
            "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    for symbol in EXPECTED_SYMBOLS:
        manifest = formal_blocks[symbol]["manifest"]
        for key in ("source_file", "bear_interval_source"):
            relative = manifest[key]
            path = WORKSPACE_ROOT / relative
            provenance["source_files"][relative] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run attributes every flat spell of the fixed MO/AZO/TLT SMA200 +/-3% timing policy.

- `report.html` / `report.md`: v5 strategy-first report and concise result summary.
- `report.pdf`: Chrome-printed report with the v5 print gate.
- `analysis/`: 0/5 bps symbol summaries, fixed-horizon summaries, and normalized flat-path points.
- `MO/`, `AZO/`, `TLT/`: immutable PyBroker/reference ledgers, prices, orders, trades, and every flat spell.
- `provenance.json`: exact source-code, market-data, and bear-label hashes.
- `validation.json`: tests, audit, browser, PDF, reconciliation, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with nine interactive figures")


if __name__ == "__main__":
    main()
