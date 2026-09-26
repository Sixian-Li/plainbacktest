#!/usr/bin/env python3
"""Analyze and render the frozen QQQ-flat Bear24 DIRECT/SMA ablation."""

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
from plotly.subplots import make_subplots

from quantkit.experiment import load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
import scripts.analyze_qqq_flat_trio_substitution as shared
from scripts.run_qqq_flat_bear24_sma_ablation import expected_cases, ordered_assets


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation"
)
FORMAL_COST_BPS = 5.0
FAMILY_LABELS = {
    "core12": "核心12",
    "near_core8": "近核心8",
    "retail4": "零售4",
}
DIRECT_COLOR = "#64748b"
SMA_COLOR = "#2563eb"
QQQ_CASH_COLOR = "#a855f7"
QQQ_HOLD_COLOR = "#111827"


def case_labels(assets: list[str], windows: dict[str, int]) -> dict[str, str]:
    labels = {"QQQ_CASH": "QQQ择时＋现金"}
    for symbol in assets:
        labels[f"QQQ_{symbol}_DIRECT"] = f"{symbol} 不筛选"
        labels[f"QQQ_{symbol}_SMA{windows[symbol]}"] = f"{symbol} SMA{windows[symbol]}"
    return labels


def monthly(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.sort_values("date").copy()
    return data.groupby(data["date"].dt.to_period("M"), sort=True).tail(1)


def drawdown(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype(float)
    peak = numeric.cummax()
    return (numeric / peak - 1.0) * 100.0


def paired_summary(
    metrics: pd.DataFrame,
    spell_summary: pd.DataFrame,
    assets: list[str],
    windows: dict[str, int],
    family_by_symbol: dict[str, str],
) -> pd.DataFrame:
    indexed = metrics.set_index("case_id")
    spells = spell_summary[spell_summary["scope"] == "post_sell_excluding_dotcom"].set_index("case_id")
    rows: list[dict[str, Any]] = []
    for symbol in assets:
        direct_id = f"QQQ_{symbol}_DIRECT"
        sma_id = f"QQQ_{symbol}_SMA{windows[symbol]}"
        direct = indexed.loc[direct_id]
        sma = indexed.loc[sma_id]
        direct_event = spells.loc[direct_id]
        sma_event = spells.loc[sma_id]
        rows.append(
            {
                "family": family_by_symbol[symbol],
                "symbol": symbol,
                "sma_window": windows[symbol],
                "direct_case_id": direct_id,
                "sma_case_id": sma_id,
                "direct_total_return_pct": float(direct.total_return_pct),
                "sma_total_return_pct": float(sma.total_return_pct),
                "sma_minus_direct_total_return_pp": float(sma.total_return_pct - direct.total_return_pct),
                "direct_cagr_pct": float(direct.cagr_pct),
                "sma_cagr_pct": float(sma.cagr_pct),
                "sma_minus_direct_cagr_pp": float(sma.cagr_pct - direct.cagr_pct),
                "direct_sharpe": float(direct.sharpe),
                "sma_sharpe": float(sma.sharpe),
                "direct_max_drawdown_pct": float(direct.max_drawdown_pct),
                "sma_max_drawdown_pct": float(sma.max_drawdown_pct),
                "max_drawdown_improvement_pp": float(sma.max_drawdown_pct - direct.max_drawdown_pct),
                "direct_substitute_exposure_pct": float(direct.substitute_exposure_pct),
                "sma_substitute_exposure_pct": float(sma.substitute_exposure_pct),
                "ex2000_direct_compound_return_pct": float(direct_event.timed_compound_return_pct),
                "ex2000_sma_compound_return_pct": float(sma_event.timed_compound_return_pct),
                "ex2000_sma_minus_direct_pp": float(
                    sma_event.timed_compound_return_pct - direct_event.timed_compound_return_pct
                ),
                "ex2000_direct_positive_spells": int(direct_event.positive_timed_spell_count),
                "ex2000_sma_positive_spells": int(sma_event.positive_timed_spell_count),
            }
        )
    return pd.DataFrame(rows)


def group_summary(paired: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for family in ("core12", "near_core8", "retail4", "all24"):
        selected = paired if family == "all24" else paired[paired["family"] == family]
        rows.append(
            {
                "family": family,
                "asset_count": int(len(selected)),
                "sma_total_return_win_count": int((selected["sma_minus_direct_total_return_pp"] > 0).sum()),
                "sma_cagr_win_count": int((selected["sma_minus_direct_cagr_pp"] > 0).sum()),
                "sma_drawdown_improvement_count": int((selected["max_drawdown_improvement_pp"] > 0).sum()),
                "sma_ex2000_win_count": int((selected["ex2000_sma_minus_direct_pp"] > 0).sum()),
                "median_sma_minus_direct_total_return_pp": float(selected["sma_minus_direct_total_return_pp"].median()),
                "median_sma_minus_direct_cagr_pp": float(selected["sma_minus_direct_cagr_pp"].median()),
                "median_max_drawdown_improvement_pp": float(selected["max_drawdown_improvement_pp"].median()),
                "median_ex2000_sma_minus_direct_pp": float(selected["ex2000_sma_minus_direct_pp"].median()),
            }
        )
    return pd.DataFrame(rows)


def performance_figure(
    block: dict[str, Any],
    cases: tuple[str, ...],
    labels: dict[str, str],
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
    )
    daily = block["daily"]
    for case_id in cases:
        selected = monthly(daily[daily["case_id"] == case_id])
        mode = "CASH" if case_id == "QQQ_CASH" else ("DIRECT" if case_id.endswith("_DIRECT") else "SMA")
        color = QQQ_CASH_COLOR if mode == "CASH" else (DIRECT_COLOR if mode == "DIRECT" else SMA_COLOR)
        visible: bool | str = True if case_id == "QQQ_CASH" else "legendonly"
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["equity"],
                name=labels[case_id],
                visible=visible,
                line={"color": color, "width": 1.7, "dash": "dot" if mode == "DIRECT" else "solid"},
                meta={
                    "series_key": case_id.lower(),
                    "label": labels[case_id],
                    "panel": "equity",
                    "cost_bps": FORMAL_COST_BPS,
                },
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=drawdown(selected["equity"]),
                name=labels[case_id],
                showlegend=False,
                visible=visible,
                line={"color": color, "width": 1.3, "dash": "dot" if mode == "DIRECT" else "solid"},
                meta={
                    "series_key": case_id.lower(),
                    "label": labels[case_id],
                    "panel": "drawdown",
                    "cost_bps": FORMAL_COST_BPS,
                },
            ),
            row=2,
            col=1,
        )
    hold = monthly(block["qqq_hold_daily"])
    figure.add_trace(
        go.Scatter(
            x=hold["date"],
            y=hold["equity"],
            name="QQQ Buy & Hold",
            line={"color": QQQ_HOLD_COLOR, "width": 1.8, "dash": "dash"},
            meta={
                "series_key": "qqq_hold",
                "label": "QQQ Buy & Hold",
                "panel": "equity",
                "is_benchmark": True,
                "cost_bps": FORMAL_COST_BPS,
            },
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=hold["date"],
            y=drawdown(hold["equity"]),
            name="QQQ Buy & Hold",
            showlegend=False,
            line={"color": QQQ_HOLD_COLOR, "width": 1.4, "dash": "dash"},
            meta={
                "series_key": "qqq_hold",
                "label": "QQQ Buy & Hold",
                "panel": "drawdown",
                "is_benchmark": True,
                "cost_bps": FORMAL_COST_BPS,
            },
        ),
        row=2,
        col=1,
    )
    figure.update_yaxes(title_text="账户净值（美元，对数）", type="log", row=1, col=1)
    figure.update_yaxes(title_text="回撤", ticksuffix="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": False}, row=1, col=1)
    figure.update_xaxes(title_text="日期", rangeslider={"visible": False}, row=2, col=1)
    figure.update_layout(
        template="plotly_white",
        height=650,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.13},
        margin={"l": 75, "r": 25, "t": 65, "b": 60},
    )
    return figure


def group_figure(paired: pd.DataFrame, family: str) -> go.Figure:
    selected = paired[paired["family"] == family]
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=selected["symbol"],
            y=selected["direct_cagr_pct"],
            name="不筛选",
            marker_color=DIRECT_COLOR,
            meta={"series_key": f"{family}_direct_cagr", "label": "不筛选"},
        )
    )
    figure.add_trace(
        go.Bar(
            x=selected["symbol"],
            y=selected["sma_cagr_pct"],
            name="自身SMA±3%",
            marker_color=SMA_COLOR,
            customdata=np.column_stack(
                [selected["sma_window"], selected["sma_minus_direct_cagr_pp"]]
            ),
            hovertemplate="%{x} / SMA%{customdata[0]}<br>CAGR %{y:.2f}%<br>较不筛选 %{customdata[1]:+.2f}pp<extra></extra>",
            meta={"series_key": f"{family}_sma_cagr", "label": "自身SMA±3%"},
        )
    )
    figure.add_hline(y=0, line_color="#94a3b8", line_width=1)
    figure.update_layout(
        template="plotly_white",
        height=430,
        barmode="group",
        yaxis={"title": "全账户 CAGR", "ticksuffix": "%"},
        xaxis={"title": FAMILY_LABELS[family]},
        legend={"orientation": "h", "y": 1.1},
        margin={"l": 70, "r": 25, "t": 45, "b": 60},
    )
    return figure


def asset_detail_figure(
    block: dict[str, Any], symbol: str, window: int
) -> go.Figure:
    direct_id = f"QQQ_{symbol}_DIRECT"
    sma_id = f"QQQ_{symbol}_SMA{window}"
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.16,
        row_heights=[0.62, 0.38],
        subplot_titles=("完整账户净值（月末取样）", "每次QQQ卖出后的替代期回报"),
    )
    for case_id, label, color, dash in (
        (direct_id, "不筛选", DIRECT_COLOR, "dot"),
        (sma_id, f"SMA{window}±3%", SMA_COLOR, "solid"),
    ):
        selected = monthly(block["daily"][block["daily"]["case_id"] == case_id])
        figure.add_trace(
            go.Scatter(
                x=selected["date"],
                y=selected["equity"],
                name=label,
                line={"color": color, "width": 2, "dash": dash},
                meta={"series_key": f"{symbol.lower()}_{label}_equity", "label": label},
            ),
            row=1,
            col=1,
        )
    events = block["substitute_flat_event_returns"]
    direct_events = events[
        (events["case_id"] == direct_id) & (events["spell_type"] == "post_sell")
    ].copy()
    sma_events = events[
        (events["case_id"] == sma_id) & (events["spell_type"] == "post_sell")
    ].copy()
    if list(direct_events["spell_id"]) != list(sma_events["spell_id"]):
        raise AssertionError(f"{symbol} DIRECT/SMA spell alignment changed")
    labels = direct_events["start_fill_date"].dt.strftime("%Y-%m-%d")
    figure.add_trace(
        go.Bar(
            x=labels,
            y=direct_events["timed_return_pct"],
            name="不筛选：逐段",
            marker_color=DIRECT_COLOR,
            customdata=np.column_stack(
                [
                    direct_events["mark_date"].dt.strftime("%Y-%m-%d"),
                    direct_events["overlaps_dotcom_bear"],
                ]
            ),
            hovertemplate="开始 %{x}<br>结束 %{customdata[0]}<br>回报 %{y:.2f}%<br>重叠2000熊市 %{customdata[1]}<extra></extra>",
            meta={"series_key": f"{symbol.lower()}_direct_events", "label": "不筛选：逐段"},
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=sma_events["timed_return_pct"],
            name=f"SMA{window}：逐段",
            marker_color=SMA_COLOR,
            meta={"series_key": f"{symbol.lower()}_sma_events", "label": f"SMA{window}：逐段"},
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_color="#94a3b8", line_width=1, row=2, col=1)
    figure.update_yaxes(title_text="净值（美元，对数）", type="log", row=1, col=1)
    figure.update_yaxes(title_text="该段回报", ticksuffix="%", row=2, col=1)
    figure.update_xaxes(title_text="日期", row=1, col=1)
    figure.update_xaxes(title_text="QQQ空仓开始日", tickangle=-45, row=2, col=1)
    figure.update_layout(
        template="plotly_white",
        height=700,
        hovermode="x unified",
        barmode="group",
        legend={"orientation": "h", "y": 1.08},
        margin={"l": 75, "r": 25, "t": 75, "b": 100},
    )
    return figure


def fmt_pct(value: float) -> str:
    return f"{float(value):+,.1f}%"


def summary_html(
    block: dict[str, Any], paired: pd.DataFrame, groups: pd.DataFrame
) -> str:
    hold = block["metrics_json"]["qqq_hold_metrics"]
    cash = block["parameter_results"].set_index("case_id").loc["QQQ_CASH"]
    cards: list[str] = []
    for family in ("core12", "near_core8", "retail4", "all24"):
        row = groups.set_index("family").loc[family]
        label = "全部24" if family == "all24" else FAMILY_LABELS[family]
        cards.append(
            '<article class="result-card">'
            f'<strong>{label}</strong>'
            f'<span>SMA提高全账户收益：{int(row.sma_total_return_win_count)}/{int(row.asset_count)}</span>'
            f'<span>SMA改善最大回撤：{int(row.sma_drawdown_improvement_count)}/{int(row.asset_count)}</span>'
            f'<span>剔除2000后SMA胜出：{int(row.sma_ex2000_win_count)}/{int(row.asset_count)}</span>'
            f'<span>CAGR差中位数：{row.median_sma_minus_direct_cagr_pp:+.2f}pp</span>'
            '</article>'
        )
    tables: list[str] = []
    for family in ("core12", "near_core8", "retail4"):
        rows: list[str] = []
        for item in paired[paired["family"] == family].itertuples(index=False):
            rows.append(
                '<tr>'
                f'<td>{html.escape(item.symbol)}</td><td>SMA{int(item.sma_window)}</td>'
                f'<td>{fmt_pct(item.direct_total_return_pct)}</td><td>{fmt_pct(item.sma_total_return_pct)}</td>'
                f'<td>{item.sma_minus_direct_total_return_pp:+,.1f}pp</td>'
                f'<td>{item.direct_max_drawdown_pct:.1f}%</td><td>{item.sma_max_drawdown_pct:.1f}%</td>'
                f'<td>{fmt_pct(item.ex2000_direct_compound_return_pct)}</td>'
                f'<td>{fmt_pct(item.ex2000_sma_compound_return_pct)}</td>'
                f'<td>{item.ex2000_sma_minus_direct_pp:+,.1f}pp</td>'
                '</tr>'
            )
        tables.append(
            f'<h3>{FAMILY_LABELS[family]}</h3>'
            '<div class="event-scroll"><table class="event-table"><thead><tr>'
            '<th>标的</th><th>冻结窗口</th><th>不筛选总收益</th><th>SMA总收益</th><th>SMA差</th>'
            '<th>不筛选回撤</th><th>SMA回撤</th><th>剔除2000不筛选</th><th>剔除2000 SMA</th><th>SMA差</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        )
    return (
        '<section class="result-intro"><h2>先看成对结果</h2>'
        '<p>每只标的都有两个独立账户：灰色“不筛选”在QQQ空仓时直接持有，蓝色“SMA”还要求该标的通过自己的冻结窗口和上下3%门槛。正式口径为单边5bps。</p>'
        f'<div class="result-cards">{"".join(cards)}</div>'
        f'<p>共同的QQQ＋现金基线：总收益 {fmt_pct(cash.total_return_pct)}，CAGR {cash.cagr_pct:.2f}%，最大回撤 {cash.max_drawdown_pct:.2f}%；'
        f'同起点QQQ Buy & Hold：总收益 {fmt_pct(hold["total_return_pct"])}，CAGR {hold["cagr_pct"]:.2f}%，最大回撤 {hold["max_drawdown_pct"]:.2f}%。</p>'
        f'{"".join(tables)}'
        '<p><strong>解释边界：</strong>24只资产和窗口都来自同一完整历史样本的事后复核。这里能回答在这段历史上SMA相对直接持有做了什么，不能把胜出窗口直接视为样本外有效。</p>'
        '</section>'
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
    parameters = context.config["parameters"]
    assets, family_by_symbol = ordered_assets(parameters)
    windows = {str(key): int(value) for key, value in parameters["substitute_windows"].items()}
    cases = expected_cases(assets, windows)
    if tuple(parameters["formal_cases"]) != cases:
        raise AssertionError("Bear24 formal case order changed")
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("analysis requires a running, writable run")
    if len(record.get("expected_blocks", [])) != 2 or any(
        item["status"] != "completed" for item in record["expected_blocks"]
    ):
        raise RuntimeError("both cost blocks must be complete before analysis")
    blocks = {
        float(cost): shared.load_block(context, args.run_id, float(cost))
        for cost in context.config["cost_scenarios_bps_per_side"]
    }
    block = blocks[FORMAL_COST_BPS]
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    all_metrics = pd.concat(
        [item["parameter_results"].assign(cost_bps=cost) for cost, item in blocks.items()],
        ignore_index=True,
    )
    all_spells = pd.concat(
        [item["flat_spell_summary"].assign(cost_bps=cost) for cost, item in blocks.items()],
        ignore_index=True,
    )
    paired = paired_summary(
        block["parameter_results"], block["flat_spell_summary"], assets, windows, family_by_symbol
    )
    groups = group_summary(paired)
    all_metrics.to_csv(analysis_root / "case_summary_all_costs.csv", index=False, lineterminator="\n")
    block["parameter_results"].to_csv(analysis_root / "case_summary_5bps.csv", index=False, lineterminator="\n")
    paired.to_csv(analysis_root / "paired_summary_5bps.csv", index=False, lineterminator="\n")
    groups.to_csv(analysis_root / "group_summary_5bps.csv", index=False, lineterminator="\n")
    all_spells.to_csv(analysis_root / "flat_spell_summary_all_costs.csv", index=False, lineterminator="\n")
    block["substitute_flat_event_returns"].to_csv(
        analysis_root / "flat_spell_events_5bps.csv", index=False, lineterminator="\n"
    )
    result_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": FORMAL_COST_BPS,
        "paired_results": json.loads(paired.to_json(orient="records")),
        "group_results": json.loads(groups.to_json(orient="records")),
        "qqq_hold_metrics": block["metrics_json"]["qqq_hold_metrics"],
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "assets and windows were selected after full-history bear-market review",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(json_safe(result_summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    labels = case_labels(assets, windows)
    figures = [
        ReportFigure(
            "performance-qqq_flat_substitution",
            "49条QQQ择时账户与QQQ持有（5bps；候选默认收起）",
            performance_figure(block, cases, labels),
            "performance",
        ),
        ReportFigure(
            "market-qqq",
            "QQQ SMA200±3%总开关与空仓高亮",
            shared.market_figure(block),
            "market",
        ),
    ]
    for family in ("core12", "near_core8", "retail4"):
        figures.append(
            ReportFigure(
                f"group-{family}",
                f"{FAMILY_LABELS[family]}：不筛选与自身SMA的全账户CAGR",
                group_figure(paired, family),
                "generic",
            )
        )
    for symbol in assets:
        figures.append(
            ReportFigure(
                f"asset-{symbol.lower()}",
                f"{symbol}：不筛选 vs SMA{windows[symbol]}±3%",
                asset_detail_figure(block, symbol, windows[symbol]),
                "generic",
            )
        )
    notes = [
        "QQQ是唯一账户总开关：QQQ处于多头状态时只持有QQQ；QQQ空仓期间才比较指定替代品直接持有和自身SMA筛选。",
        "DIRECT没有替代品技术信号；SMA路径的自身状态始终连续计算，跌破下轨后留现金，重新真实上穿上轨才再次进入。",
        "所有信号使用完成Close，下一全体资产共同拥有价格的交易日调整Open先卖后买；三条GILD上市后缺失日从全部路径共同剔除，未填充价格。",
        "正式结果使用单边5bps，0bps只检查成本方向；允许小数股、无融资、现金不计息，样本末不强制清仓。",
        "逐空仓段的剔除2000口径会删除任何与第一段2000–2002主观熊市重叠的QQQ空仓段，但熊市标签从未参与信号。",
        "资产与窗口均来自同一全历史样本的事后筛选，本报告是机械规则消融，不是未来收益的样本外证明。",
    ]
    report = render_interactive_report(
        title="QQQ空仓期：24只替代标的SMA筛选消融",
        heading="直接持有，还是再加一道自身SMA门槛？",
        subtitle="核心12、近核心8、零售4逐只成对比较；QQQ SMA200±3%总开关保持不变。",
        summary_html=summary_html(block, paired, groups),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.result-cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-card{display:flex;flex-direction:column;gap:7px;border:1px solid #dbe3ec;border-radius:12px;padding:15px;background:#fff}.result-card strong{font-size:1.02rem}.result-card span{font-variant-numeric:tabular-nums;color:#334155}.event-scroll{overflow-x:auto}.event-table{width:100%;border-collapse:collapse;margin:12px 0;font-variant-numeric:tabular-nums}.event-table th,.event-table td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:right;white-space:nowrap}.event-table th:first-child,.event-table td:first-child{text-align:left}@media(max-width:1000px){.result-cards{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){.result-cards{grid-template-columns:1fr}}@media print{.result-card{break-inside:avoid}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="analysis/paired_summary_5bps.csv">24只成对汇总</a> · '
        '<a download href="analysis/group_summary_5bps.csv">三组统计</a> · '
        '<a download href="analysis/case_summary_all_costs.csv">49条路径与0/5bps</a> · '
        '<a download href="analysis/flat_spell_events_5bps.csv">逐QQQ空仓段结果</a> · '
        '<a download href="QQQ_FLAT_SUBSTITUTION/cost_5bps/orders.csv">完整成交</a>'
        '</p></section>'
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")

    all24 = groups.set_index("family").loc["all24"]
    lines = [
        "# QQQ空仓期：24只替代标的SMA筛选消融",
        "",
        "## 5bps摘要",
        "",
        f"- 24只中，SMA提高全账户总收益 {int(all24.sma_total_return_win_count)}/24，只改善最大回撤 {int(all24.sma_drawdown_improvement_count)}/24，剔除2000后空仓段胜出 {int(all24.sma_ex2000_win_count)}/24。",
        f"- SMA相对不筛选的CAGR差中位数为 {all24.median_sma_minus_direct_cagr_pp:+.2f}pp。",
        "",
        "## 逐标的",
        "",
    ]
    for item in paired.itertuples(index=False):
        lines.append(
            f"- {item.symbol} / SMA{int(item.sma_window)}：不筛选总收益 {item.direct_total_return_pct:+.1f}%，SMA {item.sma_total_return_pct:+.1f}%（差 {item.sma_minus_direct_total_return_pp:+.1f}pp）；剔除2000空仓段差 {item.ex2000_sma_minus_direct_pp:+.1f}pp。"
        )
    lines.extend(
        [
            "",
            "## 研究边界",
            "",
            "- QQQ始终是总开关，替代品只在QQQ空仓期出现。",
            "- 资产与窗口来自同一全历史样本的事后筛选，不能视为样本外验证。",
            "- 三条缺失交易日从全部49条路径共同剔除，没有填充或使用未来价格。",
        ]
    )
    (run_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tracked = [
        "backtest/quantkit/qqq_flat_substitution.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_qqq_flat_bear24_sma_ablation.py",
        "backtest/scripts/analyze_qqq_flat_bear24_sma_ablation.py",
        "backtest/scripts/finalize_qqq_flat_bear24_sma_ablation.py",
        "backtest/scripts/smoke_qqq_flat_bear24_sma_ablation_report.mjs",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_qqq_flat_bear24_sma_ablation.py",
        "backtest/experiments/TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json",
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
    for relative in block["manifest"]["source_files"]:
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

This immutable run compares DIRECT and frozen-SMA substitutes for all Bear24 assets while QQQ SMA200 timing is flat.

- `report.html` / `report.md`: v5 strategy-first report and concise Chinese summary.
- `report.pdf`: Chrome-printed report after the v5 print gate.
- `analysis/`: 49-path, paired-asset, group, and per-QQQ-flat-spell summaries.
- `QQQ_FLAT_SUBSTITUTION/`: immutable 0/5 bps ledgers, decisions, positions, orders, shared-calendar exclusions, master spells, and event returns.
- `provenance.json`: exact source-code, market-data, and bear-label hashes.
- `validation.json`: tests, audit, browser, PDF, reconciliation, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'} with {len(figures)} interactive figures")


if __name__ == "__main__":
    main()
