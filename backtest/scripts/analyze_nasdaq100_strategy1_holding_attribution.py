#!/usr/bin/env python3
"""Build the v5 per-security Strategy1 >90% holding attribution report."""

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

from quantkit.experiment import assert_run_writable, load_experiment, load_run, sha256
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution"
)
SYMBOL = "NASDAQ100_STRATEGY1_90_ATTRIBUTION"
HORIZONS = (5, 10, 20, 60)


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


def load_blocks(run_root: Path) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in (0.0, 5.0):
        root = run_root / SYMBOL / f"cost_{cost:g}bps"
        blocks[cost] = {
            "root": root,
            "metrics": pd.read_csv(root / "metrics.csv"),
            "metrics_json": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
            "exits": pd.read_csv(root / "exit_events.csv.gz", parse_dates=["exit_date", "signal_date"]),
        }
    return blocks


def _fmt(value: object, digits: int = 2, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number:.{digits}f}{suffix}"


def pooled_exit_summary(exits: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        returns = exits[f"forward_close_return_{horizon}d_pct"].dropna().astype(float)
        rebounds = exits[f"max_high_rebound_{horizon}d_pct"].dropna().astype(float)
        rows.append(
            {
                "horizon": horizon,
                "event_count": len(returns),
                "mean_return_pct": float(returns.mean()),
                "median_return_pct": float(returns.median()),
                "positive_pct": float((returns > 0).mean() * 100.0),
                "mean_max_rebound_pct": float(rebounds.mean()),
                "median_max_rebound_pct": float(rebounds.median()),
            }
        )
    return pd.DataFrame(rows)


def load_security_daily(path: Path, security_id: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, parse_dates=["date"], chunksize=200_000):
        selected = chunk[chunk["security_id"].astype(str).eq(security_id)]
        if not selected.empty:
            frames.append(selected)
    if not frames:
        raise ValueError(f"daily ledger misses representative security: {security_id}")
    return pd.concat(frames, ignore_index=True).sort_values("date", kind="stable")


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def representative_equity_figure(
    daily_by_cost: dict[float, pd.DataFrame],
    *,
    ticker: str,
    source_file: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=(
            f"横截面中位数代表：{ticker} · {source_file}",
            "从各自历史峰值回撤",
        ),
    )
    for cost, color, dash in ((0.0, "#2563eb", "solid"), (5.0, "#f97316", "dot")):
        frame = daily_by_cost[cost]
        key = f"representative_gate_{cost:g}bps"
        for row, values, panel, showlegend in (
            (1, frame["equity"], "equity", True),
            (2, drawdown(frame["equity"]), "drawdown", False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    name=f"{ticker} Strategy1-90 · {cost:g} bps",
                    showlegend=showlegend,
                    line={"color": color, "width": 2.0, "dash": dash},
                    meta={
                        "series_key": key,
                        "panel": panel,
                        "label": f"{ticker} Strategy1-90 · {cost:g} bps",
                        "cost_bps": cost,
                    },
                ),
                row=row,
                col=1,
            )
    dates = daily_by_cost[0.0]["date"]
    cash = pd.Series(100_000.0, index=dates.index)
    for row, values, panel, showlegend in (
        (1, cash, "equity", True),
        (2, cash * 0.0, "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=values,
                name="10万美元现金",
                showlegend=showlegend,
                line={"color": "#111827", "width": 2.2, "dash": "dash"},
                meta={
                    "series_key": "cash_benchmark",
                    "panel": panel,
                    "label": "10万美元现金",
                    "is_benchmark": panel == "equity",
                    "cost_bps": 0.0,
                },
            ),
            row=row,
            col=1,
        )
    figure.update_layout(height=760, showlegend=False, hovermode="x unified")
    figure.update_yaxes(title_text="账户净值", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def merged_metrics(blocks: dict[float, dict[str, Any]]) -> pd.DataFrame:
    zero = blocks[0.0]["metrics"].copy()
    five = blocks[5.0]["metrics"].copy()
    keys = ["security_id", "display_ticker", "source_file"]
    five_columns = keys + [
        "holding_period_cagr_pct",
        "holding_return_sharpe",
        "max_drawdown_pct",
        "cagr_pct",
        "sharpe",
        "final_equity",
    ]
    renamed = five[five_columns].rename(
        columns={column: f"{column}_5bps" for column in five_columns if column not in keys}
    )
    return zero.merge(renamed, on=keys, how="inner", validate="one_to_one")


def summary_html(metrics: pd.DataFrame, rebound: pd.DataFrame) -> str:
    adequate = metrics[metrics["adequate_holding_sample"].astype(bool)].copy()
    positive_holding_cagr = float((adequate["holding_period_cagr_pct"] > 0).mean() * 100.0)
    positive_holding_sharpe = float((adequate["holding_return_sharpe"] > 0).mean() * 100.0)
    horizon20 = rebound.set_index("horizon").loc[20]
    rows = []
    for item in metrics.sort_values(["display_ticker", "source_file"]).itertuples(index=False):
        adequate_text = "足够" if bool(item.adequate_holding_sample) else "偏少"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.display_ticker))}</td>"
            f"<td>{html.escape(str(item.source_file))}</td>"
            f"<td>{html.escape(str(item.start))} → {html.escape(str(item.end))}</td>"
            f"<td>{int(item.holding_sessions):,}</td><td>{item.holding_time_pct:.1f}%</td>"
            f"<td>{int(item.entry_count)}</td><td>{adequate_text}</td>"
            f"<td>{_fmt(item.holding_period_cagr_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.holding_return_sharpe, 3)}</td>"
            f"<td>{_fmt(item.max_drawdown_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.cagr_pct, 2, '%')}</td>"
            f"<td>{int(item.post_exit_20d_count)}</td>"
            f"<td>{_fmt(item.post_exit_20d_mean_return_pct, 2, '%')}</td>"
            f"<td>{_fmt(item.post_exit_20d_positive_pct, 1, '%')}</td>"
            f"<td>{_fmt(item.holding_period_cagr_pct_5bps, 2, '%')}</td>"
            f"<td>{_fmt(item.holding_return_sharpe_5bps, 3)}</td>"
            "</tr>"
        )
    return (
        '<div class="summary-grid">'
        f'<div class="summary-card"><strong>曾超过90%</strong><span>{len(metrics):,} 个稳定证券身份</span></div>'
        f'<div class="summary-card"><strong>持仓样本≥252日</strong><span>{len(adequate):,} 只；以下横截面摘要以它们为主</span></div>'
        f'<div class="summary-card"><strong>持仓CAGR为正</strong><span>{positive_holding_cagr:.1f}% 的足量样本证券</span></div>'
        f'<div class="summary-card"><strong>持仓Sharpe为正</strong><span>{positive_holding_sharpe:.1f}% 的足量样本证券</span></div>'
        f'<div class="summary-card"><strong>卖出后20日上涨</strong><span>{horizon20["positive_pct"]:.1f}% · {int(horizon20["event_count"]):,} 次可观察退出</span></div>'
        f'<div class="summary-card"><strong>卖出后20日均值</strong><span>{horizon20["mean_return_pct"]:+.2f}%（中位数 {horizon20["median_return_pct"]:+.2f}%）</span></div>'
        '</div>'
        '<p class="strategy-note">持仓CAGR沿用此前定义：把日历CAGR按有仓位交易日比例做几何压缩。持仓Sharpe只使用实际暴露收益；最大回撤看持有/现金账户的连续净值。样本偏少的股票仍完整列出，但不用于主要横截面判断。</p>'
        '<details><summary>展开全部354个证券的逐股指标（0 bps主口径，末两列为5 bps敏感性）</summary>'
        '<div class="table-wrap" style="max-height:760px;overflow:auto"><table><thead><tr>'
        '<th>Ticker</th><th>来源文件</th><th>可用区间</th><th>持仓日</th><th>持仓时间</th><th>入场段</th><th>样本</th>'
        '<th>持仓CAGR</th><th>持仓Sharpe</th><th>最大回撤</th><th>日历CAGR</th>'
        '<th>20日退出数</th><th>退出后20日均值</th><th>20日上涨率</th><th>5bps持仓CAGR</th><th>5bps持仓Sharpe</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>'
    )


def holding_quality_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("全部证券", "持仓至少252日"),
        horizontal_spacing=0.10,
    )
    for cost, color, visible in ((0.0, "#2563eb", True), (5.0, "#f97316", "legendonly")):
        data = blocks[cost]["metrics"].copy()
        for column, filtered in ((1, data), (2, data[data["adequate_holding_sample"].astype(bool)])):
            size = np.clip(np.sqrt(filtered["holding_sessions"].astype(float)) * 1.15, 6, 26)
            figure.add_trace(
                go.Scatter(
                    x=filtered["holding_return_sharpe"],
                    y=filtered["holding_period_cagr_pct"],
                    mode="markers",
                    name=f"{cost:g} bps" if column == 1 else f"{cost:g} bps · 足量样本",
                    visible=visible,
                    marker={
                        "size": size,
                        "color": filtered["max_drawdown_pct"],
                        "colorscale": "RdYlGn",
                        "cmin": -100,
                        "cmax": 0,
                        "showscale": column == 2 and cost == 0,
                        "colorbar": {"title": "最大回撤 %"},
                        "opacity": 0.74,
                        "line": {"color": color, "width": 0.5},
                    },
                    customdata=np.column_stack(
                        [
                            filtered["display_ticker"],
                            filtered["source_file"],
                            filtered["holding_sessions"],
                            filtered["holding_time_pct"],
                            filtered["max_drawdown_pct"],
                        ]
                    ),
                    hovertemplate=(
                        "%{customdata[0]} · %{customdata[1]}<br>持仓CAGR %{y:.2f}%"
                        "<br>持仓Sharpe %{x:.3f}<br>最大回撤 %{customdata[4]:.2f}%"
                        "<br>持仓日 %{customdata[2]:,.0f} · 持仓时间 %{customdata[3]:.1f}%<extra></extra>"
                    ),
                    meta={
                        "series_key": f"holding_quality_{column}_{cost:g}bps",
                        "panel": "holding_quality",
                        "label": f"{cost:g} bps",
                        "cost_bps": cost,
                    },
                ),
                row=1,
                col=column,
            )
    for column in (1, 2):
        figure.add_hline(y=0, line_dash="dot", line_color="#64748b", row=1, col=column)
        figure.add_vline(x=0, line_dash="dot", line_color="#64748b", row=1, col=column)
        figure.update_xaxes(title_text="持仓收益Sharpe", row=1, col=column)
        figure.update_yaxes(title_text="持仓期间CAGR %", row=1, col=column)
    figure.update_layout(height=700, showlegend=False, uirevision="strategy1-holding-quality-v1")
    return figure


def rebound_figure(rebound: pd.DataFrame) -> go.Figure:
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    x = [f"{int(value)}日" for value in rebound["horizon"]]
    for column, name, color in (
        ("mean_return_pct", "平均收盘收益", "#2563eb"),
        ("median_return_pct", "中位收盘收益", "#60a5fa"),
        ("mean_max_rebound_pct", "期间平均最大反弹", "#f97316"),
    ):
        figure.add_trace(go.Bar(x=x, y=rebound[column], name=name, marker_color=color), secondary_y=False)
    figure.add_trace(
        go.Scatter(
            x=x,
            y=rebound["positive_pct"],
            name="期末上涨比例",
            mode="lines+markers+text",
            text=[f"{value:.1f}%" for value in rebound["positive_pct"]],
            textposition="top center",
            line={"color": "#15803d", "width": 2.5},
        ),
        secondary_y=True,
    )
    figure.update_yaxes(title_text="相对卖出Open %", secondary_y=False)
    figure.update_yaxes(title_text="期末上涨事件占比 %", range=[0, 100], secondary_y=True)
    figure.update_layout(height=560, barmode="group", legend={"orientation": "h"})
    return figure


def distribution_figure(metrics: pd.DataFrame) -> go.Figure:
    adequate = metrics[metrics["adequate_holding_sample"].astype(bool)]
    figure = make_subplots(rows=1, cols=3, subplot_titles=("持仓期间CAGR", "持仓收益Sharpe", "最大回撤"))
    for column, field, color in (
        (1, "holding_period_cagr_pct", "#2563eb"),
        (2, "holding_return_sharpe", "#7c3aed"),
        (3, "max_drawdown_pct", "#dc2626"),
    ):
        figure.add_trace(
            go.Box(
                y=adequate[field],
                name="持仓≥252日",
                boxpoints="outliers",
                marker_color=color,
                customdata=adequate[["display_ticker", "source_file"]],
                hovertemplate="%{customdata[0]} · %{customdata[1]}<br>%{y:.3f}<extra></extra>",
            ),
            row=1,
            col=column,
        )
    figure.update_layout(height=590, showlegend=False)
    return figure


def top_bottom_figure(metrics: pd.DataFrame) -> go.Figure:
    adequate = metrics[metrics["adequate_holding_sample"].astype(bool)].copy()
    top = adequate.nlargest(15, "holding_period_cagr_pct")
    bottom = adequate.nsmallest(15, "holding_period_cagr_pct")
    display = pd.concat([top.assign(group="最高15"), bottom.assign(group="最低15")])
    labels = display["display_ticker"] + " · " + display["source_file"]
    values = [
        display["group"],
        labels,
        display["holding_sessions"].map(lambda value: f"{int(value):,}"),
        display["holding_time_pct"].map(lambda value: f"{value:.1f}%"),
        display["holding_period_cagr_pct"].map(lambda value: f"{value:.2f}%"),
        display["holding_return_sharpe"].map(lambda value: f"{value:.3f}"),
        display["max_drawdown_pct"].map(lambda value: f"{value:.2f}%"),
        display["post_exit_20d_mean_return_pct"].map(lambda value: _fmt(value, 2, "%")),
    ]
    figure = go.Figure(
        go.Table(
            header={
                "values": ["分组", "证券身份", "持仓日", "持仓时间", "持仓CAGR", "持仓Sharpe", "最大回撤", "退出后20日均值"],
                "fill_color": "#0f766e",
                "font": {"color": "white"},
                "align": "left",
            },
            cells={"values": values, "fill_color": "#f8fafc", "align": "left", "height": 25},
        )
    )
    figure.update_layout(height=930, margin={"l": 20, "r": 20, "t": 25, "b": 20})
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [block["block_id"] for block in record["expected_blocks"] if block["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    blocks = load_blocks(run_root)
    combined = merged_metrics(blocks)
    rebound = pooled_exit_summary(blocks[0.0]["exits"])
    adequate = combined[combined["adequate_holding_sample"].astype(bool)]
    median_cagr = float(adequate["holding_period_cagr_pct"].median())
    representative = (
        adequate.assign(
            distance_to_median=(adequate["holding_period_cagr_pct"] - median_cagr).abs()
        )
        .sort_values(["distance_to_median", "security_id"], kind="stable")
        .iloc[0]
    )
    representative_id = str(representative["security_id"])
    representative_daily = {
        cost: load_security_daily(
            block["root"] / "daily.csv.gz",
            representative_id,
        )
        for cost, block in blocks.items()
    }
    primary = {
        "security_count": int(len(combined)),
        "adequate_holding_sample_count": int(len(adequate)),
        "median_holding_period_cagr_pct": float(adequate["holding_period_cagr_pct"].median()),
        "median_holding_return_sharpe": float(adequate["holding_return_sharpe"].median()),
        "median_max_drawdown_pct": float(adequate["max_drawdown_pct"].median()),
        "positive_holding_cagr_pct": float((adequate["holding_period_cagr_pct"] > 0).mean() * 100.0),
        "positive_holding_sharpe_pct": float((adequate["holding_return_sharpe"] > 0).mean() * 100.0),
    }
    analysis = run_root / "analysis"
    analysis.mkdir(exist_ok=True)
    combined.to_csv(analysis / "per_security_metrics.csv", index=False, lineterminator="\n")
    rebound.to_csv(analysis / "post_exit_pooled_summary.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "data_status": "candidate_pending_review",
        "primary_cost_bps": 0.0,
        "cross_section": {"adequate_sample": primary},
        "post_exit_pooled": rebound.to_dict("records"),
        "interpretation_boundary": "descriptive attribution only; no threshold or exit rule was selected",
        "parent_run_reused": context.config["parameters"]["parent_run_id"],
        "deterministic_median_representative": {
            "security_id": representative_id,
            "display_ticker": str(representative["display_ticker"]),
            "source_file": str(representative["source_file"]),
            "holding_period_cagr_pct": float(representative["holding_period_cagr_pct"]),
        },
    }
    (analysis / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    horizon20 = rebound.set_index("horizon").loc[20]
    report = render_interactive_report(
        title="Nasdaq-100逐股 Strategy1-90 持仓与退出归因",
        heading="90%门槛抓住了怎样的上涨段，又是否经常在反弹前卖出",
        subtitle=(
            f"父run信号与价格缓存原样复用 · {len(combined)}个证券身份 · "
            f"足量样本持仓CAGR中位数 {primary['median_holding_period_cagr_pct']:.2f}%"
        ),
        summary_html=summary_html(combined, rebound),
        notes=[
            "逐股表不是把354只股票同时持有：每一行都是独立10万美元账户，只在该证券和现金之间切换。",
            "持仓期间CAGR按有仓位交易日比例压缩日历CAGR；持仓Sharpe只使用入场、连续持有与退出时真正赚到的收益，最大回撤来自连续的持有/现金净值。",
            f"0 bps下共有{int(horizon20['event_count']):,}次具有真实20日后价格的退出，其中{horizon20['positive_pct']:.1f}%在20日后高于卖出Open；这比只看单股CAGR更直接检验是否提前离场。",
            "退出后的上涨不自动说明卖点错误：重新入场、期间先跌后涨、成交成本和轮动资金的机会成本仍需在另一个预先冻结的退出规则实验中检验。",
            f"时间序列图不是额外选优：它按足量样本持仓CAGR距横截面中位数最近、再以稳定证券ID打破并列，确定为{representative['display_ticker']}（{representative['source_file']}）；现金线只用于展示门控净值。",
            "历史成分与个股行情仍是pending_review候选包，本报告只能作探索性归因，不能当作样本外或正式轮动证据。",
        ],
        figures=[
            ReportFigure(
                "performance-nasdaq100_strategy1_90_attribution",
                "横截面中位数代表证券的门控净值",
                representative_equity_figure(
                    representative_daily,
                    ticker=str(representative["display_ticker"]),
                    source_file=str(representative["source_file"]),
                ),
                "performance",
            ),
            ReportFigure(
                "holding-quality-nasdaq100_strategy1_90_attribution",
                "逐股持仓CAGR、持仓Sharpe与最大回撤",
                holding_quality_figure(blocks),
                "generic",
            ),
            ReportFigure(
                "distribution-nasdaq100_strategy1_90_attribution",
                "持仓至少252日证券的横截面分布",
                distribution_figure(combined),
                "generic",
            ),
            ReportFigure(
                "rebound-nasdaq100_strategy1_90_attribution",
                "所有实际卖出后的5/10/20/60日表现",
                rebound_figure(rebound),
                "generic",
            ),
            ReportFigure(
                "extremes-nasdaq100_strategy1_90_attribution",
                "足量样本中持仓CAGR最高与最低各15个证券",
                top_bottom_figure(combined),
                "generic",
            ),
        ],
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        f"""# Nasdaq-100逐股 Strategy1-90 持仓与退出归因

## 结论摘要

- 0 bps主口径共有 `{len(combined)}` 个曾严格超过90%的稳定证券身份，其中 `{len(adequate)}` 个至少持仓252个交易日。
- 足量样本的持仓期间CAGR中位数为 `{primary['median_holding_period_cagr_pct']:.3f}%`，持仓收益Sharpe中位数为 `{primary['median_holding_return_sharpe']:.3f}`，最大回撤中位数为 `{primary['median_max_drawdown_pct']:.2f}%`。
- `{primary['positive_holding_cagr_pct']:.1f}%` 的足量样本证券持仓CAGR为正；`{primary['positive_holding_sharpe_pct']:.1f}%` 的持仓Sharpe为正。
- 具有真实20日后价格的 `{int(horizon20['event_count'])}` 次退出中，`{horizon20['positive_pct']:.1f}%` 在20日后上涨；平均收益 `{horizon20['mean_return_pct']:+.3f}%`，中位数 `{horizon20['median_return_pct']:+.3f}%`。

## 研究边界

本实验只复用父run冻结的90%资格与价格缓存做逐股归因，没有重算Strategy1、调整阈值或重跑16条组合路径。数据仍为 `pending_review`，退出后上涨也不能单独证明卖点错误。
""",
        encoding="utf-8",
    )

    tracked = [
        "backtest/quantkit/nasdaq100_strategy1_holding_attribution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/stochrsi_position_gates.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_nasdaq100_strategy1_holding_attribution.py",
        "backtest/scripts/analyze_nasdaq100_strategy1_holding_attribution.py",
        "backtest/scripts/finalize_nasdaq100_stochrsi_rotation.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/rot/test_nasdaq100_strategy1_holding_attribution.py",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": summary["created_at_utc"],
        "software": {"python": platform.python_version(), "plotly": plotly.__version__},
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    shared_manifest = json.loads((run_root / "shared/manifest.json").read_text(encoding="utf-8"))
    for relative, expected_hash in shared_manifest["source_files"].items():
        path = WORKSPACE_ROOT / relative
        actual = sha256(path)
        if actual != expected_hash:
            raise AssertionError(f"parent source changed during attribution: {relative}")
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": actual}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

Derived per-security attribution for the frozen parent Strategy1 >90% signal.

- `report.html` / `report.pdf`: interactive and printable v5 report.
- `analysis/per_security_metrics.csv`: all 354 identities, with 0bps primary and 5bps sensitivity columns.
- `analysis/post_exit_pooled_summary.csv`: pooled 5/10/20/60-session post-exit evidence.
- `{SYMBOL}/`: per-cost daily ledgers, orders, trades, exposed returns and exit events.
- `shared/`: byte-for-byte copied parent validated score/price cache and hashes.
""",
        encoding="utf-8",
    )
    print(
        f"Wrote {run_root / 'report.html'}; adequate={len(adequate)}/{len(combined)}; "
        f"20d positive={horizon20['positive_pct']:.1f}%"
    )


if __name__ == "__main__":
    main()
