#!/usr/bin/env python3
"""Build the formal report for the three bear-event SMA200 portfolios."""

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

from quantkit.experiment import (
    block_root,
    load_experiment,
    load_run,
    record_analysis_complete,
    sha256,
)
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT
    / "experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios"
)
SYMBOL_BLOCK = "BEAR_EVENT_PORTFOLIOS"
UNIVERSE_LABELS = {
    "core12_near8": "12+8",
    "core12_retail4": "12+4",
    "core12_near8_retail4": "12+8+4",
}
MODE_LABELS = {
    "no_filter": "不筛选",
    "sma200_hysteresis": "SMA200 筛选",
}
SCOPE_LABELS = {"major": "大熊", "minor": "小熊", "all": "总熊"}
COLORS = {
    "core12_near8__no_filter": "#94a3b8",
    "core12_near8__sma200_hysteresis": "#2563eb",
    "core12_retail4__no_filter": "#a8a29e",
    "core12_retail4__sma200_hysteresis": "#d97706",
    "core12_near8_retail4__no_filter": "#64748b",
    "core12_near8_retail4__sma200_hysteresis": "#0f766e",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def case_label(case_id: str) -> str:
    universe_id, mode = case_id.split("__", 1)
    return f"{UNIVERSE_LABELS[universe_id]} · {MODE_LABELS[mode]}"


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def market_figure(spy: pd.DataFrame, schedule: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=spy["date"],
            open=spy["open"],
            high=spy["high"],
            low=spy["low"],
            close=spy["close"],
            name="SPY 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=spy["date"],
            y=spy["close"],
            mode="lines",
            name="SPY Close",
            line={"color": "#0f766e", "width": 1.2},
            meta={
                "series_key": "spy_close",
                "panel": "market",
                "label": "SPY Close",
                "control_group": "reference",
                "control_group_label": "参考价格",
            },
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
        )
    )
    unique_schedule = schedule.drop_duplicates("interval_id").sort_values("ordinal")
    for interval in unique_schedule.itertuples(index=False):
        figure.add_vrect(
            x0=pd.Timestamp(interval.start),
            x1=pd.Timestamp(interval.end),
            fillcolor="#dc2626" if interval.severity == "major" else "#f59e0b",
            opacity=0.09,
            line_width=0,
            layer="below",
        )
    figure.update_layout(
        height=650,
        yaxis_title="USD",
        xaxis={"rangeslider": {"visible": True}},
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 25, "b": 55},
        uirevision="bear-event-market-v1",
    )
    figure.update_yaxes(fixedrange=False)
    return figure


def load_blocks(context, run_id: str) -> dict[float, dict[str, pd.DataFrame]]:
    blocks: dict[float, dict[str, pd.DataFrame]] = {}
    parse_dates = {
        "daily": ["date"],
        "orders": ["date"],
        "transitions": ["signal_date", "execution_date"],
        "interval_returns": ["start", "end", "entry_execution_date", "exit_execution_date"],
        "interval_schedule": ["start", "end", "entry_execution_date", "exit_execution_date"],
        "calendar_exclusions": ["date"],
    }
    for configured_cost in context.config["cost_scenarios_bps_per_side"]:
        cost = float(configured_cost)
        root = block_root(context, run_id, SYMBOL_BLOCK, cost)
        blocks[cost] = {
            name: pd.read_csv(root / f"{name}.csv", parse_dates=dates)
            for name, dates in parse_dates.items()
        }
        for name in ("metrics", "interval_summary", "filter_comparison"):
            blocks[cost][name] = pd.read_csv(root / f"{name}.csv")
    return blocks


def performance_figure(blocks: dict[float, dict[str, pd.DataFrame]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("只在标注熊市持仓的连续账户净值", "从事件账户历史峰值回撤"),
    )
    benchmark_case = "core12_near8_retail4__no_filter"
    for cost in sorted(blocks):
        daily = blocks[cost]["daily"]
        for case_id in COLORS:
            frame = daily[daily["case_id"] == case_id].sort_values("date")
            series_key = f"{case_id}_{cost:g}bps"
            visible: bool | str = True if cost == 5 else "legendonly"
            dash = "solid" if cost == 5 else "dot"
            label = f"{case_label(case_id)} · {cost:g} bps"
            for row, values, panel, showlegend in (
                (1, frame["equity"], "equity", True),
                (2, drawdown(frame["equity"]), "drawdown", False),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=frame["date"],
                        y=values,
                        mode="lines",
                        name=label,
                        showlegend=showlegend,
                        visible=visible,
                        line={"color": COLORS[case_id], "width": 2.0, "dash": dash},
                        meta={
                            "series_key": series_key,
                            "panel": panel,
                            "label": label,
                            "is_benchmark": bool(
                                panel == "equity"
                                and case_id == benchmark_case
                                and cost == 5
                            ),
                            "cost_bps": cost,
                        },
                        hovertemplate=(
                            "%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>"
                            if row == 1
                            else "%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>"
                        ),
                    ),
                    row=row,
                    col=1,
                )
    figure.update_layout(
        height=850,
        hovermode="x unified",
        showlegend=False,
        margin={"l": 65, "r": 25, "t": 65, "b": 55},
        uirevision="bear-event-performance-v1",
    )
    figure.update_yaxes(title_text="USD", type="log", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def scope_figure(summary: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=3,
        shared_yaxes=True,
        subplot_titles=tuple(SCOPE_LABELS.values()),
    )
    universes = list(UNIVERSE_LABELS)
    for column, scope in enumerate(SCOPE_LABELS, start=1):
        scoped = summary[summary["scope"] == scope]
        for mode, color in (("no_filter", "#94a3b8"), ("sma200_hysteresis", "#0f766e")):
            values = (
                scoped[scoped["filter_mode"] == mode]
                .set_index("universe_id")
                .reindex(universes)["compound_return"]
                * 100.0
            )
            figure.add_trace(
                go.Bar(
                    x=[UNIVERSE_LABELS[item] for item in universes],
                    y=values,
                    name=MODE_LABELS[mode],
                    marker_color=color,
                    showlegend=column == 1,
                    text=[f"{value:+.1f}%" for value in values],
                    textposition="outside",
                    cliponaxis=False,
                    hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
                ),
                row=1,
                col=column,
            )
        figure.add_hline(y=0, line={"color": "#475569", "width": 1}, row=1, col=column)
    figure.update_layout(
        barmode="group",
        height=500,
        yaxis_title="逐段收益复合值（%）",
        legend={"orientation": "h", "y": 1.18},
        margin={"l": 65, "r": 25, "t": 65, "b": 45},
    )
    return figure


def interval_figure(interval_returns: pd.DataFrame) -> go.Figure:
    ordered = interval_returns.sort_values("ordinal")
    labels = list(dict.fromkeys(ordered["label"].tolist()))
    figure = go.Figure()
    for case_id in COLORS:
        frame = ordered[ordered["case_id"] == case_id].set_index("label").reindex(labels)
        figure.add_trace(
            go.Bar(
                x=labels,
                y=frame["total_return"] * 100.0,
                name=case_label(case_id),
                marker_color=COLORS[case_id],
                hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
            )
        )
    figure.add_hline(y=0, line={"color": "#475569", "width": 1})
    figure.update_layout(
        barmode="group",
        height=610,
        yaxis_title="该段净收益（%）",
        xaxis_title="12 段手工标注并收紧到峰值—谷底的熊市",
        legend={"orientation": "h", "y": 1.20},
        margin={"l": 65, "r": 25, "t": 75, "b": 125},
    )
    return figure


def exposure_figure(daily: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for case_id in COLORS:
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["gross_exposure"] * 100.0,
                mode="lines",
                name=case_label(case_id),
                line={"color": COLORS[case_id], "width": 1.6},
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f}%<extra></extra>",
            )
        )
    figure.update_layout(
        height=500,
        yaxis_title="总持仓 / 净值（%）",
        xaxis={"rangeslider": {"visible": True}},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.18},
        margin={"l": 65, "r": 25, "t": 65, "b": 55},
    )
    return figure


def metric_table(blocks: dict[float, dict[str, pd.DataFrame]]) -> str:
    rows: list[str] = []
    for cost in sorted(blocks):
        metrics = blocks[cost]["metrics"].set_index("case_id")
        for case_id in COLORS:
            item = metrics.loc[case_id]
            rows.append(
                "<tr>"
                f"<td>{html.escape(case_label(case_id))}</td>"
                f"<td>{cost:g}</td>"
                f"<td>{item['major_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['minor_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['all_bear_compound_return_pct']:+.2f}%</td>"
                f"<td>{item['average_bear_gross_exposure_pct']:.1f}%</td>"
                f"<td>{int(item['order_count']):,}</td>"
                f"<td>{item['turnover_multiple']:.1f}×</td>"
                "</tr>"
            )
    return (
        '<div class="summary-grid"><div class="summary-card"><strong>固定设计</strong>'
        '<span>3 股票池 × 2 路径 × 0/5 bps；熊市之外全现金</span></div>'
        '<div class="summary-card"><strong>关键边界</strong>'
        '<span>峰谷日期事后已知，只验证熊市内机制，不是实时择时回测</span></div></div>'
        '<div class="table-wrap"><table><thead><tr><th>组合</th><th>bps/边</th>'
        '<th>大熊复合</th><th>小熊复合</th><th>总熊复合</th><th>熊市平均持仓</th>'
        f"<th>订单</th><th>换手</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def markdown_comparison(comparison: pd.DataFrame) -> str:
    rows = ["| 股票池 | 大熊 | 小熊 | 总熊 |", "|---|---:|---:|---:|"]
    indexed = comparison.set_index(["universe_id", "scope"])
    for universe_id, label in UNIVERSE_LABELS.items():
        cells = [label]
        for scope in SCOPE_LABELS:
            row = indexed.loc[(universe_id, scope)]
            cells.append(
                f"{row['no_filter'] * 100:+.2f}% → {row['sma200_hysteresis'] * 100:+.2f}% "
                f"({row['improvement'] * 100:+.2f}pp)"
            )
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def evaluate_promotion_gates(
    zero_comparison: pd.DataFrame,
    five_comparison: pd.DataFrame,
) -> dict[str, Any]:
    """Evaluate the frozen all-bear and six major/minor paired gates."""

    five_all = five_comparison[five_comparison["scope"] == "all"]
    zero_all = zero_comparison[zero_comparison["scope"] == "all"]
    five_pairs = five_comparison[
        five_comparison["scope"].isin(["major", "minor"])
    ]
    if len(five_all) != 3 or len(zero_all) != 3 or len(five_pairs) != 6:
        raise ValueError("promotion gate requires three all rows and six major/minor rows")
    all_universes_improve = bool((five_all["improvement"] > 0).all())
    paired_improvement_count = int((five_pairs["improvement"] > 0).sum())
    zero_all_improve = bool((zero_all["improvement"] > 0).all())
    promotion_passed = bool(
        all_universes_improve and paired_improvement_count >= 4 and zero_all_improve
    )
    at_least_two_all_worse = int((five_all["improvement"] < 0).sum()) >= 2
    cost_reversal = zero_all[["universe_id", "improvement"]].merge(
        five_all[["universe_id", "improvement"]],
        on="universe_id",
        suffixes=("_0bps", "_5bps"),
    )
    cost_reversal_count = int(
        (
            (cost_reversal["improvement_0bps"] > 0)
            & (cost_reversal["improvement_5bps"] <= 0)
        ).sum()
    )
    return {
        "five_all": five_all,
        "all_universes_improve": all_universes_improve,
        "paired_improvement_count": paired_improvement_count,
        "zero_all_improve": zero_all_improve,
        "promotion_passed": promotion_passed,
        "cost_reversal_count": cost_reversal_count,
        "rejection_triggered": bool(at_least_two_all_worse or cost_reversal_count > 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running":
        raise RuntimeError("Run must still be writable before analysis")
    blocks = load_blocks(context, args.run_id)
    if 5.0 not in blocks or 0.0 not in blocks:
        raise ValueError("formal report requires both 0 and 5 bps blocks")
    five = blocks[5.0]
    zero = blocks[0.0]
    five_comparison = five["filter_comparison"].copy()
    zero_comparison = zero["filter_comparison"].copy()
    gates = evaluate_promotion_gates(zero_comparison, five_comparison)
    five_all = gates["five_all"]
    all_universes_improve = gates["all_universes_improve"]
    paired_improvement_count = gates["paired_improvement_count"]
    zero_all_improve = gates["zero_all_improve"]
    promotion_passed = gates["promotion_passed"]
    cost_reversal_count = gates["cost_reversal_count"]
    rejection_triggered = gates["rejection_triggered"]

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": 5.0,
        "promotion_criteria_passed": promotion_passed,
        "rejection_condition_triggered": rejection_triggered,
        "five_bps_all_universes_improve": all_universes_improve,
        "five_bps_major_minor_pair_improvement_count": paired_improvement_count,
        "zero_bps_all_universes_improve": zero_all_improve,
        "cost_reversal_count": cost_reversal_count,
        "five_bps_filter_comparison": json_safe(
            five_comparison.to_dict("records")
        ),
        "five_bps_case_metrics": json_safe(five["metrics"].to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    improvement_text = "；".join(
        f"{UNIVERSE_LABELS[row.universe_id]} {row.improvement * 100:+.2f}pp"
        for row in five_all.itertuples(index=False)
    )
    notes = [
        "这是一项事件条件化的事后研究：每段熊市的起止峰谷由全样本人工标注并收紧，实盘当时并不知道这些边界。",
        "所有信号在完成 Close 后形成，并在下一共同交易日的复权 Open 成交；同日先卖后买，允许小数股，现金不计息。",
        "SMA 路径的起点门槛为 Close>SMA200；途中使用上下 2% 滞回，并以自身最近一次状态切换成交价的上下 10% 区间锁住状态。",
        "股票池采用当前成分与事后筛选候选，因此存在生存偏差和候选选择偏差；结果只回答这些固定候选在这些固定窗口中的表现。",
        "GILD 上市后缺少 2001-10-02、2001-10-03、2002-08-06 三个 OHLC 日；两份购买源一致缺失，三日从所有 case 的共同日历统一删除，未填造价格。",
        "0 bps 用于成本敏感性；单边 5 bps 是正式主结果。所有 6 个 case 都被保留，没有按结果挑选赢家。",
    ]
    spy = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/SPY.csv", parse_dates=["date"])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    spy = spy[(spy["date"] >= start) & (spy["date"] <= end)].copy()
    figures = [
        ReportFigure(
            div_id="market-bear_event_portfolios",
            title="SPY 与 12 段事后峰谷熊市窗口",
            figure=market_figure(spy, five["interval_schedule"]),
            kind="market",
        ),
        ReportFigure(
            div_id="performance-bear_event_portfolios",
            title="连续事件账户净值与回撤",
            figure=performance_figure(blocks),
            kind="performance",
        ),
        ReportFigure(
            div_id="scope-bear_event_portfolios",
            title="5 bps：大熊、小熊与总熊复合收益",
            figure=scope_figure(five["interval_summary"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="intervals-bear_event_portfolios",
            title="5 bps：12 段熊市逐段收益",
            figure=interval_figure(five["interval_returns"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="exposure-bear_event_portfolios",
            title="5 bps：熊市窗口内外的组合总持仓",
            figure=exposure_figure(five["daily"]),
            kind="generic",
        ),
    ]
    report_html = render_interactive_report(
        title="熊市事件组合：不筛选 vs SMA200 滞回",
        heading="12+8、12+4、12+8+4：固定熊市窗口内的组合检验",
        subtitle=f"单边 5 bps 下，SMA200 相对同池不筛选的总熊改善：{improvement_text}",
        summary_html=metric_table(blocks),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    downloads = (
        '<section class="chart" id="downloads-bear-event"><h2>结果下载</h2><p>'
        '<a download href="BEAR_EVENT_PORTFOLIOS/cost_5bps/interval_returns.csv">5 bps 逐段收益</a> · '
        '<a download href="BEAR_EVENT_PORTFOLIOS/cost_5bps/interval_summary.csv">5 bps 大/小/总熊汇总</a> · '
        '<a download href="BEAR_EVENT_PORTFOLIOS/cost_5bps/orders.csv">5 bps 订单</a> · '
        '<a download href="BEAR_EVENT_PORTFOLIOS/cost_5bps/transitions.csv">5 bps 状态切换</a> · '
        '<a download href="BEAR_EVENT_PORTFOLIOS/cost_0bps/interval_summary.csv">0 bps 汇总</a>'
        '</p></section>'
    )
    report_html = report_html.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    decision = (
        "达到预声明的机制晋级门槛，但由于边界为事后 oracle，仍不能直接用于实盘；下一步只能研究实时可知的熊市触发器。"
        if promotion_passed
        else "未达到预声明的机制晋级门槛；不应把这套 SMA200 规则包装成已经验证的熊市配置方案。"
    )
    (run_root / "report.md").write_text(
        f"""# 熊市事件组合：不筛选 vs SMA200 滞回

## 结论

- 单边 5 bps 的总熊改善：{improvement_text}。
- 6 个“大熊/小熊 × 股票池”配对中，SMA 路径改善 `{paired_improvement_count}` 个。
- 判定：{decision}

## 单边 5 bps 结果

以下每格为“不筛选 → SMA200（改善百分点）”。

{markdown_comparison(five_comparison)}

## 实现边界

- 三个股票池固定为 12+8、12+4、12+8+4；Q 完全排除。
- 不筛选路径只在每段起点等权建立，途中不再平衡，结束后下一共同 Open 清仓。
- SMA 路径使用起点 Close>SMA200、途中 SMA200±2%、自身状态成交锚±10%锁定、次日 Open 成交和按现有市值比例再分配。
- 熊市起止是事后人工标签，候选也是当前成分/事后筛选；这是机制研究，不是可交易的实时择时成绩。

## 交互报告

打开 `report.html` 可查看 0/5 bps 连续事件净值、逐段收益、大/小/总熊复合收益、组合敞口，并下载底层 CSV。
""",
        encoding="utf-8",
    )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tracked = [
        "backtest/quantkit/bear_event_sma_portfolio.py",
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_bear_event_sma_portfolios.py",
        "backtest/scripts/analyze_bear_event_sma_portfolios.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_bear_event_sma_portfolio.py",
        "backtest/tests/strategies/rot/test_trend_score_portfolio.py",
        "backtest/experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v3/page.html",
        "backtest/report_templates/interactive_research_v3/styles.css",
        "backtest/report_templates/interactive_research_v3/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
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
    for cost in context.config["cost_scenarios_bps_per_side"]:
        manifest_path = block_root(context, args.run_id, SYMBOL_BLOCK, float(cost)) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative in manifest["source_files"]:
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

This immutable run evaluates three fixed bear-event universes, each with no-filter and SMA200-hysteresis paths.

- `report.html` / `report.md`: interactive and compact reports.
- `analysis/summary.json`: machine-readable promotion decision and 5 bps comparison.
- `BEAR_EVENT_PORTFOLIOS/cost_0bps/` and `cost_5bps/`: both ledgers, orders, signals, transitions, interval returns, summaries, exclusions and hashes.
- `provenance.json`: exact code, configuration, research-input and market-data hashes.
- `validation.json`: tests, audit, hash verification and real-browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {
            "artifact_manifest.json",
            "run.json",
            "validation.json",
        }:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(
        f"Wrote {run_root / 'report.html'}; five-bps paired improvements="
        f"{paired_improvement_count}/6; promotion={promotion_passed}; rejection={rejection_triggered}"
    )


if __name__ == "__main__":
    main()
