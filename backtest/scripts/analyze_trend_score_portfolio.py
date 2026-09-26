#!/usr/bin/env python3
"""Build the formal interactive report for the unified trend-score portfolio."""

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

from quantkit.experiment import block_root, load_experiment, load_run, record_analysis_complete, sha256
from quantkit.reporting import ReportFigure, render_interactive_report


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio"
CASE_LABELS = {
    "unified_score": "完整统一评分",
    "own_score_only": "仅个股评分",
    "sma200_atr_only": "仅 SMA200+ATR",
    "risk_base_no_timing": "不择时风险预算",
}
COLORS = {
    "unified_score": "#0f766e",
    "own_score_only": "#2563eb",
    "sma200_atr_only": "#d97706",
    "risk_base_no_timing": "#111827",
    "spy": "#7c3aed",
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


def drawdown(equity: pd.Series) -> pd.Series:
    values = equity.astype(float)
    return (values / values.cummax() - 1.0) * 100.0


def load_blocks(context, run_id: str) -> dict[float, dict[str, pd.DataFrame]]:
    blocks: dict[float, dict[str, pd.DataFrame]] = {}
    for cost in context.config["cost_scenarios_bps_per_side"]:
        root = block_root(context, run_id, "RESILIENCE_21", float(cost))
        blocks[float(cost)] = {
            "metrics": pd.read_csv(root / "metrics.csv"),
            "daily": pd.read_csv(root / "daily.csv", parse_dates=["date"]),
            "orders": pd.read_csv(root / "orders.csv", parse_dates=["date"]),
            "targets": pd.read_csv(root / "weekly_targets.csv", parse_dates=["date"]),
            "signals": pd.read_csv(root / "signals.csv", parse_dates=["date"]),
            "bear": pd.read_csv(root / "bear_interval_returns.csv", parse_dates=["start", "end"]),
            "benchmark": pd.read_csv(root / "spy_buy_hold_daily.csv", parse_dates=["date"]),
        }
    return blocks


def market_figure(spy: pd.DataFrame, qqq: pd.DataFrame, signals: pd.DataFrame) -> go.Figure:
    market = signals.groupby("date", as_index=False)[
        ["spy_score", "qqq_score", "breadth_score", "market_score"]
    ].first()
    spy_view = spy.merge(market, on="date", how="left")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.68, 0.32],
        subplot_titles=("SPY 复权 OHLC", "市场环境分：SPY、QQQ、广度与合成"),
    )
    figure.add_trace(
        go.Candlestick(
            x=spy_view["date"],
            open=spy_view["open"],
            high=spy_view["high"],
            low=spy_view["low"],
            close=spy_view["close"],
            name="SPY 复权 OHLC",
            increasing_line_color="#1b7f5a",
            decreasing_line_color="#c2413b",
        ),
        row=1,
        col=1,
    )
    qqq_aligned = qqq.set_index("date")["close"].reindex(spy_view["date"]).to_numpy()
    first_valid_qqq = float(pd.Series(qqq_aligned).dropna().iloc[0])
    qqq_normalized = qqq_aligned / first_valid_qqq * float(spy_view.iloc[0]["close"])
    figure.add_trace(
        go.Scatter(
            x=spy_view["date"],
            y=qqq_normalized,
            mode="lines",
            name="QQQ（首日对齐 SPY）",
            line={"color": "#7c3aed", "width": 1.2},
            visible="legendonly",
            meta={
                "series_key": "qqq_normalized",
                "panel": "market",
                "label": "QQQ（首日对齐）",
                "control_group": "reference",
                "control_group_label": "参考价格",
            },
        ),
        row=1,
        col=1,
    )
    for column, label, color, width in (
        ("market_score", "市场合成分", "#0f766e", 2.4),
        ("spy_score", "SPY 分", "#2563eb", 1.2),
        ("qqq_score", "QQQ 分", "#7c3aed", 1.2),
        ("breadth_score", "SMA200 广度", "#d97706", 1.3),
    ):
        figure.add_trace(
            go.Scatter(
                x=spy_view["date"],
                y=spy_view[column],
                mode="lines",
                name=label,
                line={"color": color, "width": width},
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": label,
                    "control_group": "score",
                    "control_group_label": "市场评分",
                },
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f}<extra></extra>",
            ),
            row=2,
            col=1,
        )
    figure.update_layout(
        height=820,
        margin={"l": 65, "r": 25, "t": 65, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="resilience-market-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="0–100", range=[-5, 105], fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def performance_figure(blocks: dict[float, dict[str, pd.DataFrame]]) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.68, 0.32],
        subplot_titles=("组合净值", "从各自历史峰值回撤"),
    )
    for cost in sorted(blocks):
        daily = blocks[cost]["daily"]
        for case_id, label in CASE_LABELS.items():
            frame = daily[daily["case_id"] == case_id].sort_values("date")
            key = f"{case_id}_{cost:g}bps"
            visible = True if cost == 5 else "legendonly"
            dash = "solid" if cost == 5 else "dot"
            for row, values, panel, showlegend in (
                (1, frame["equity"], "equity", True),
                (2, drawdown(frame["equity"]), "drawdown", False),
            ):
                figure.add_trace(
                    go.Scatter(
                        x=frame["date"],
                        y=values,
                        mode="lines",
                        name=f"{label} · {cost:g} bps",
                        showlegend=showlegend,
                        visible=visible,
                        line={"color": COLORS[case_id], "width": 2.2, "dash": dash},
                        meta={
                            "series_key": key,
                            "panel": panel,
                            "label": f"{label} · {cost:g} bps",
                            "is_benchmark": panel == "equity" and case_id == "risk_base_no_timing" and cost == 5,
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
    benchmark = blocks[max(blocks)]["benchmark"].sort_values("date")
    for row, values, panel, showlegend in (
        (1, benchmark["equity"], "equity", True),
        (2, drawdown(benchmark["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name="SPY Buy & Hold",
                showlegend=showlegend,
                visible="legendonly",
                line={"color": COLORS["spy"], "width": 1.8, "dash": "dash"},
                meta={"series_key": "spy_buy_hold", "panel": panel, "label": "SPY Buy & Hold"},
            ),
            row=row,
            col=1,
        )
    figure.update_layout(
        height=840,
        margin={"l": 65, "r": 25, "t": 65, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="resilience-performance-v1",
    )
    figure.update_yaxes(title_text="USD", type="log", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def exposure_figure(block: dict[str, pd.DataFrame]) -> go.Figure:
    daily = block["daily"]
    figure = go.Figure()
    for case_id, label in CASE_LABELS.items():
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        figure.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame["gross_exposure"] * 100.0,
                mode="lines",
                name=label,
                line={"color": COLORS[case_id], "width": 1.7},
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f}%<extra></extra>",
            )
        )
    figure.update_layout(
        height=470,
        yaxis_title="总持仓占净值（%）",
        xaxis={"rangeslider": {"visible": True}},
        hovermode="x unified",
        margin={"l": 65, "r": 25, "t": 25, "b": 55},
        legend={"orientation": "h", "y": 1.08},
    )
    return figure


def bear_figure(bear: pd.DataFrame) -> go.Figure:
    available = bear.dropna(subset=["total_return"]).copy()
    labels = list(dict.fromkeys(available.sort_values("start")["label"].tolist()))
    figure = go.Figure()
    for case_id, label in CASE_LABELS.items():
        frame = available[available["case_id"] == case_id].set_index("label").reindex(labels)
        figure.add_trace(
            go.Bar(
                x=labels,
                y=frame["total_return"] * 100.0,
                name=label,
                marker_color=COLORS[case_id],
                hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>",
            )
        )
    figure.add_hline(y=0, line={"color": "#475569", "width": 1})
    figure.update_layout(
        barmode="group",
        height=540,
        yaxis_title="该段组合收益（%）",
        xaxis_title="仅显示 2007 年后有组合净值覆盖的手工峰谷熊市",
        margin={"l": 65, "r": 25, "t": 25, "b": 90},
        legend={"orientation": "h", "y": 1.12},
    )
    return figure


def current_score_figure(current: pd.DataFrame, core: set[str]) -> go.Figure:
    frame = current.copy().sort_values(["target_weight", "combined_score"], ascending=False)
    frame["layer"] = np.where(frame["symbol"].isin(core), "核心", "近核心")
    display = pd.DataFrame(
        {
            "标的": frame["symbol"],
            "层级": frame["layer"],
            "个股分": frame["own_score"].round(1),
            "市场分": frame["market_score"].round(1),
            "总分": frame["combined_score"].round(1),
            "倍率": frame["multiplier"].map(lambda value: f"{value:.0%}"),
            "底层预算": frame["base_weight"].map(lambda value: f"{value:.2%}"),
            "目标权重": frame["target_weight"].map(lambda value: f"{value:.2%}"),
        }
    )
    figure = go.Figure(
        data=[
            go.Table(
                header={
                    "values": list(display.columns),
                    "fill_color": "#0f766e",
                    "font": {"color": "white", "size": 12},
                    "align": "left",
                },
                cells={
                    "values": [display[column] for column in display.columns],
                    "fill_color": "#f8fafc",
                    "align": "left",
                    "height": 26,
                },
            )
        ]
    )
    figure.update_layout(height=760, margin={"l": 20, "r": 20, "t": 20, "b": 20})
    return figure


def metric_table(blocks: dict[float, dict[str, pd.DataFrame]]) -> str:
    rows: list[str] = []
    for cost in sorted(blocks):
        metrics = blocks[cost]["metrics"].set_index("case_id")
        for case_id in CASE_LABELS:
            item = metrics.loc[case_id]
            rows.append(
                "<tr>"
                f"<td>{html.escape(CASE_LABELS[case_id])}</td>"
                f"<td>{cost:g}</td>"
                f"<td>{item['cagr_pct']:.2f}%</td>"
                f"<td>{item['sharpe']:.3f}</td>"
                f"<td>{item['max_drawdown_pct']:.2f}%</td>"
                f"<td>{item['average_gross_exposure_pct']:.1f}%</td>"
                f"<td>{int(item['order_count']):,}</td>"
                f"<td>{item['turnover_multiple']:.1f}×</td>"
                "</tr>"
            )
    return (
        '<div class="summary-grid"><div class="summary-card"><strong>固定设计</strong><span>21 候选 · 周频 · 次日 Open · 统一参数</span></div>'
        '<div class="summary-card"><strong>研究边界</strong><span>当前成分股 + 全历史筛选，属于事后探索</span></div></div>'
        '<div class="table-wrap"><table><thead><tr><th>组合</th><th>bps/边</th><th>CAGR</th><th>Sharpe</th><th>最大回撤</th><th>平均持仓</th><th>订单</th><th>换手</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


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
    five = blocks[5.0]
    metrics = five["metrics"].set_index("case_id")
    primary = metrics.loc["unified_score"]
    base = metrics.loc["risk_base_no_timing"]
    drawdown_improvement = float(primary["max_drawdown_pct"] - base["max_drawdown_pct"])
    cagr_retention = float(primary["cagr_pct"] / base["cagr_pct"]) if base["cagr_pct"] > 0 else np.nan
    sharpe_difference = float(primary["sharpe"] - base["sharpe"])
    promotion_passed = bool(
        drawdown_improvement >= 10.0 and cagr_retention >= 0.70 and sharpe_difference >= 0
    )
    rejection_triggered = bool(
        drawdown_improvement < 5.0 or cagr_retention < 0.60 or sharpe_difference < 0
    )

    spy = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/SPY.csv", parse_dates=["date"])
    qqq = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    spy = spy[(spy["date"] >= start) & (spy["date"] <= end)]
    qqq = qqq[(qqq["date"] >= start) & (qqq["date"] <= end)]
    signals = five["signals"]
    latest_target_date = five["targets"]["date"].max()
    current = five["targets"][
        (five["targets"]["date"] == latest_target_date)
        & (five["targets"]["case_id"] == "unified_score")
    ].copy()

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "primary_case": "unified_score",
        "formal_cost_bps": 5.0,
        "drawdown_improvement_pct_points_vs_no_timing": drawdown_improvement,
        "cagr_retention_ratio_vs_no_timing": cagr_retention,
        "sharpe_difference_vs_no_timing": sharpe_difference,
        "promotion_criteria_passed": promotion_passed,
        "rejection_condition_triggered": rejection_triggered,
        "latest_completed_week_signal": latest_target_date.date().isoformat(),
        "primary_metrics": json_safe(primary.to_dict()),
        "base_metrics": json_safe(base.to_dict()),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    notes = [
        "主策略参数在实验前冻结，四个 case 之间不改选赢家。",
        "评分只使用信号日及此前数据，信号在每周最后一个完整交易日 Close 确认，下一共同交易日 Open 成交。",
        "21 个候选来自全历史熊市事件研究；市场广度也固定为当前成分股，因此全部结果都有事后选择与生存偏差。",
        "Q 截至结束日只有 193 根日线，未达到 252 根预热要求，表中保留但目标权重为零。",
        "报告中的最新正式目标使用 2026-07-31 完整周信号，并于 2026-08-03 Open 执行；8 月 4 日不是完整周末，不产生额外信号。",
    ]
    figures = [
        ReportFigure(
            div_id="market-resilience_21",
            title="市场环境与广度",
            figure=market_figure(spy, qqq, signals),
            kind="market",
        ),
        ReportFigure(
            div_id="performance-resilience_21",
            title="净值与回撤：主策略、消融与机制基准",
            figure=performance_figure(blocks),
            kind="performance",
        ),
        ReportFigure(
            div_id="exposure-resilience_21",
            title="5 bps 下的组合总持仓",
            figure=exposure_figure(five),
            kind="generic",
        ),
        ReportFigure(
            div_id="bear-resilience_21",
            title="手工峰谷熊市逐段组合收益",
            figure=bear_figure(five["bear"]),
            kind="generic",
        ),
        ReportFigure(
            div_id="scores-resilience_21",
            title=f"最新完整周评分与目标权重（{latest_target_date.date()} Close）",
            figure=current_score_figure(current, set(context.config["parameters"]["core_symbols"])),
            kind="generic",
        ),
    ]
    report_html = render_interactive_report(
        title="核心/近核心候选统一牛熊评分组合",
        heading="统一牛熊评分：先验证机制，再谈逐标的定制",
        subtitle=(
            f"2007-01-03 至 2026-08-04 · 5 bps 主结果：完整评分 CAGR {primary['cagr_pct']:.2f}%、"
            f"Sharpe {primary['sharpe']:.3f}、最大回撤 {primary['max_drawdown_pct']:.2f}%"
        ),
        summary_html=metric_table(blocks),
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    score_payload = json.dumps(
        json_safe(
            current[
                [
                    "symbol",
                    "ready",
                    "own_score",
                    "market_score",
                    "combined_score",
                    "base_weight",
                    "multiplier",
                    "target_weight",
                ]
            ].to_dict("records")
        ),
        ensure_ascii=False,
        allow_nan=False,
    ).replace("</", "<\\/")
    score_download = (
        '<section class="chart" id="current-score-download"><h2>当前评分机器可读导出</h2>'
        '<p><button type="button" id="download-current-scores">下载当前评分 JSON</button> '
        f'<a download="current_unified_scores_{latest_target_date.date()}.csv" href="RESILIENCE_21/cost_5bps/current_scores.csv">下载完整 CSV</a></p>'
        f'<script type="application/json" id="current-score-payload">{score_payload}</script>'
        "<script>(function(){const b=document.getElementById('download-current-scores');"
        "b.addEventListener('click',function(){const p=document.getElementById('current-score-payload').textContent;"
        "const u=URL.createObjectURL(new Blob([p],{type:'application/json'}));const a=document.createElement('a');"
        f"a.href=u;a.download='current_unified_scores_{latest_target_date.date()}.json';a.click();URL.revokeObjectURL(u);"
        "});})();</script></section>"
    )
    report_html = report_html.replace("</main>", score_download + "</main>")
    (run_root / "report.html").write_text(report_html, encoding="utf-8")
    decision = (
        "达到预声明数值门槛，但因当前成分股与全历史候选偏差，仍只能进入历史时点成分股复测。"
        if promotion_passed
        else "未达到预声明晋级门槛，当前统一评分不能作为可直接采用的配置规则。"
    )
    (run_root / "report.md").write_text(
        f"""# 核心/近核心候选统一牛熊评分组合

## 结论

- 单边 5 bps：完整统一评分 CAGR `{primary['cagr_pct']:.3f}%`、Sharpe `{primary['sharpe']:.3f}`、最大回撤 `{primary['max_drawdown_pct']:.2f}%`、平均总持仓 `{primary['average_gross_exposure_pct']:.1f}%`。
- 同一逆波动预算但不择时：CAGR `{base['cagr_pct']:.3f}%`、Sharpe `{base['sharpe']:.3f}`、最大回撤 `{base['max_drawdown_pct']:.2f}%`。
- 回撤改善 `{drawdown_improvement:.2f}` 个百分点，CAGR 保留率 `{cagr_retention:.1%}`，Sharpe 差 `{sharpe_difference:+.3f}`。
- 判定：{decision}

## 研究边界

21 个候选来自完整历史的手工熊市筛选，市场广度也使用 2026 当前成分股快照，因此这不是无偏的样本外检验。正式历史权重没有使用全样本抗跌收益，避免额外把未来熊市结果倒灌进每周仓位。Q 因 193 根日线不足 252 根预热，保留在表中但未进入组合。

## 交互报告

打开 `report.html` 可缩放查看市场环境、0/5 bps 净值与回撤、组合敞口、逐段熊市收益，以及截至 {latest_target_date.date()} Close 的 21 标的评分和目标权重。
""",
        encoding="utf-8",
    )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tracked = [
        "backtest/quantkit/trend_score_portfolio.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_trend_score_portfolio.py",
        "backtest/scripts/analyze_trend_score_portfolio.py",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/rot/test_trend_score_portfolio.py",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v3/page.html",
        "backtest/report_templates/interactive_research_v3/styles.css",
        "backtest/report_templates/interactive_research_v3/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
        "research/market_views/bear_market_resilience_current_constituents_summary.csv",
        "data/processed/universes/sp500/current_constituents.csv",
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
    # Block manifests contain the exact 21 asset, SPY/QQQ, current-universe
    # and Nasdaq archive inputs. Promote those hashes into run-level
    # provenance so the standard validator rechecks every market-data source.
    for cost in context.config["cost_scenarios_bps_per_side"]:
        manifest_path = block_root(
            context, args.run_id, "RESILIENCE_21", float(cost)
        ) / "manifest.json"
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

This immutable run evaluates four frozen weekly score cases on the 21 current resilience candidates.

- `report.html` / `report.md`: interactive and compact human reports.
- `analysis/summary.json`: machine-readable promotion-gate comparison.
- `RESILIENCE_21/cost_0bps/` and `cost_5bps/`: PyBroker/reference ledgers, scores, targets, bear intervals and hashes.
- `provenance.json`: exact source, dependency, template and research-input hashes.
- `validation.json`: mandatory tests, audit, hash checks and real-browser evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest = {
        "schema_version": 1,
        "created_at_utc": created_at,
        "artifacts": {},
    }
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
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
        f"Wrote {run_root / 'report.html'}; DD improvement={drawdown_improvement:.3f}pp; "
        f"CAGR retention={cagr_retention:.3%}; promotion={promotion_passed}"
    )


if __name__ == "__main__":
    main()
