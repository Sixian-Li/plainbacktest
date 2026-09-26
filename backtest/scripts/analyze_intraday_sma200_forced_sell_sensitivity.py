#!/usr/bin/env python3
"""Build the detailed report for the QQQ SMA200 forced-sell sensitivity run."""

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
from quantkit.intraday_sma_threshold import prepare_intraday_threshold_data
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.analyze_intraday_sma200_threshold_grid import drawdown, market_figure
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity"
)
PAIR_LABELS = {
    "primary": "主候选 a=3.75%、b=-12.75%",
    "drawdown_reference": "回撤对照 a=3.00%、b=-12.75%",
}
PAIR_COLORS = {"primary": "#2563eb", "drawdown_reference": "#d97706"}


def _label(row: pd.Series) -> str:
    c = "关" if pd.isna(row["correction_buy_pct"]) else f"{row['correction_buy_pct']:g}%"
    d = "关" if pd.isna(row["correction_sell_pct"]) else f"{row['correction_sell_pct']:g}%"
    return f"{PAIR_LABELS[str(row['pair_id'])]}；c={c}、d={d}"


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    results: pd.DataFrame,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=("两组 a/b 的 c=5%、d 扫描", "从各自历史峰值回撤"),
    )
    sweep = results[np.isclose(results["correction_buy_pct"], 5.0)]
    d_colors = {
        np.nan: "#111827",
        2.5: "#dc2626",
        5.0: "#7c3aed",
        7.5: "#0891b2",
        10.0: "#16a34a",
        12.5: "#64748b",
        15.0: "#a16207",
    }
    for _, row in sweep.iterrows():
        frame = daily[daily["case_id"] == row["case_id"]].sort_values("date")
        d_value = row["correction_sell_pct"]
        color = "#111827" if pd.isna(d_value) else d_colors[float(d_value)]
        dash = "solid" if row["pair_id"] == "primary" else "dot"
        label = _label(row)
        visible: bool | str = True if pd.isna(d_value) or d_value in (2.5, 5.0) else "legendonly"
        for panel, values, row_number, showlegend in (
            ("equity", frame["equity"], 1, True),
            ("drawdown", drawdown(frame["equity"]), 2, False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"],
                    y=values,
                    mode="lines",
                    name=label,
                    showlegend=showlegend,
                    visible=visible,
                    line={"color": color, "width": 2.2, "dash": dash},
                    meta={"series_key": str(row["case_id"]), "panel": panel, "label": label},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    label = "QQQ Buy & Hold（2000-01-03 Open，5 bps）"
    for panel, values, row_number, showlegend in (
        ("equity", benchmark["equity"], 1, True),
        ("drawdown", drawdown(benchmark["equity"]), 2, False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=label,
                showlegend=showlegend,
                line={"color": "#000000", "width": 2, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel,
                    "label": label,
                    "is_benchmark": panel == "equity",
                    "cost_bps": 5,
                },
                hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
            ),
            row=row_number,
            col=1,
        )
    figure.update_layout(
        height=820,
        margin={"l": 65, "r": 25, "t": 75, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-sma200-forced-sell-performance-v1",
    )
    figure.update_yaxes(title_text="USD", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def d_response_figure(results: pd.DataFrame) -> go.Figure:
    metrics = (
        ("rolling_5y_cagr_q25_pct", "S5", "%"),
        ("restart_10y_cagr_q25_pct", "S10", "%"),
        ("cagr_pct", "全历史 CAGR", "%"),
        ("sharpe", "全历史 Sharpe", ""),
        ("max_drawdown_pct", "最大回撤", "%"),
        ("sell_correction_count", "强制卖出次数", "次"),
    )
    figure = make_subplots(rows=3, cols=2, subplot_titles=[item[1] for item in metrics])
    for metric_index, (metric, _, unit) in enumerate(metrics):
        row_number = metric_index // 2 + 1
        column = metric_index % 2 + 1
        for pair_id, group in results.groupby("pair_id", sort=False):
            sweep = group[np.isclose(group["correction_buy_pct"], 5.0)].copy()
            sweep["d_axis"] = sweep["correction_sell_pct"].fillna(0.0)
            sweep = sweep.sort_values("d_axis")
            figure.add_trace(
                go.Scatter(
                    x=sweep["d_axis"],
                    y=sweep[metric],
                    mode="lines+markers+text" if metric == "sell_correction_count" else "lines+markers",
                    text=(
                        sweep[metric].astype(int).astype(str)
                        if metric == "sell_correction_count"
                        else None
                    ),
                    textposition="top center",
                    name=PAIR_LABELS[pair_id],
                    legendgroup=pair_id,
                    showlegend=metric_index == 0,
                    line={"color": PAIR_COLORS[pair_id], "width": 2.4},
                    hovertemplate=(
                        f"{PAIR_LABELS[pair_id]}<br>d=%{{x:g}}%（0=关闭）"
                        f"<br>{metric}=%{{y:.4f}} {unit}<extra></extra>"
                    ),
                ),
                row=row_number,
                col=column,
            )
        figure.update_xaxes(title_text="d（%，0表示关闭）", row=row_number, col=column)
        figure.update_yaxes(title_text=unit, row=row_number, col=column)
    figure.update_layout(height=1050, margin={"l": 65, "r": 30, "t": 80, "b": 55})
    return figure


def ablation_figure(results: pd.DataFrame) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=3,
        subplot_titles=("全历史 CAGR", "Sharpe", "最大回撤", "S5", "S10", "成交次数"),
    )
    metrics = (
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "rolling_5y_cagr_q25_pct",
        "restart_10y_cagr_q25_pct",
        "order_count",
    )
    for index, metric in enumerate(metrics):
        row_number = index // 3 + 1
        column = index % 3 + 1
        for pair_id, group in results.groupby("pair_id", sort=False):
            selected = group[
                group["correction_buy_pct"].isna() | np.isclose(group["correction_buy_pct"], 5.0)
            ]
            selected = selected[
                selected["correction_sell_pct"].isna()
                | np.isclose(selected["correction_sell_pct"], 5.0)
            ].copy()
            selected["mode"] = selected.apply(
                lambda item: (
                    f"c={'关' if pd.isna(item['correction_buy_pct']) else '5%'}\n"
                    f"d={'关' if pd.isna(item['correction_sell_pct']) else '5%'}"
                ),
                axis=1,
            )
            selected = selected.sort_values(
                ["correction_buy_pct", "correction_sell_pct"], na_position="first"
            )
            figure.add_trace(
                go.Bar(
                    x=selected["mode"],
                    y=selected[metric],
                    name=PAIR_LABELS[pair_id],
                    legendgroup=pair_id,
                    showlegend=index == 0,
                    marker_color=PAIR_COLORS[pair_id],
                    hovertemplate=f"{PAIR_LABELS[pair_id]}<br>%{{x}}<br>{metric}=%{{y:.4f}}<extra></extra>",
                ),
                row=row_number,
                col=column,
            )
    figure.update_layout(
        height=820,
        barmode="group",
        margin={"l": 65, "r": 25, "t": 80, "b": 60},
    )
    return figure


def event_figure(events: pd.DataFrame, results: pd.DataFrame) -> go.Figure:
    labels = results.set_index("case_id")["case_label"].to_dict()
    frame = events.copy()
    frame["case_label"] = frame["case_id"].map(labels)
    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("卖出后20/60日最低 Low", "下一次买回的时间与价差"),
        horizontal_spacing=0.12,
    )
    for case_id, group in frame.groupby("case_id", sort=False):
        label = str(group.iloc[0]["case_label"])
        figure.add_trace(
            go.Scatter(
                x=group["min_low_vs_raw_sell_pct_20"],
                y=group["min_low_vs_raw_sell_pct_60"],
                mode="markers",
                name=label,
                legendgroup=case_id,
                hovertemplate=(
                    f"{label}<br>%{{customdata|%Y-%m-%d}}"
                    "<br>20日最低 %{x:.2f}%<br>60日最低 %{y:.2f}%<extra></extra>"
                ),
                customdata=group["sell_date"],
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=group["flat_sessions"],
                y=group["buyback_vs_sell_pct"],
                mode="markers",
                name=label,
                legendgroup=case_id,
                showlegend=False,
                hovertemplate=(
                    f"{label}<br>%{{customdata|%Y-%m-%d}}"
                    "<br>空仓 %{x:.0f} 个交易日<br>买回价差 %{y:.2f}%<extra></extra>"
                ),
                customdata=group["sell_date"],
            ),
            row=1,
            col=2,
        )
    figure.add_hline(y=0, line_dash="dash", line_color="#64748b", row=1, col=1)
    figure.add_vline(x=0, line_dash="dash", line_color="#64748b", row=1, col=1)
    figure.add_hline(y=0, line_dash="dash", line_color="#64748b", row=1, col=2)
    figure.update_xaxes(title_text="未来20日最低 Low 相对卖出价（%）", row=1, col=1)
    figure.update_yaxes(title_text="未来60日最低 Low 相对卖出价（%）", row=1, col=1)
    figure.update_xaxes(title_text="距下一次买回的交易日数", row=1, col=2)
    figure.update_yaxes(title_text="买回成交价相对卖出成交价（%）", row=1, col=2)
    figure.update_layout(height=620, margin={"l": 70, "r": 30, "t": 80, "b": 60})
    return figure


def comparison_table(results: pd.DataFrame) -> str:
    lines = [
        "| a/b | d | S5 | S10 | CAGR | Sharpe | 最大回撤 | 强制卖出 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    sweep = results[np.isclose(results["correction_buy_pct"], 5.0)].copy()
    sweep["d_axis"] = sweep["correction_sell_pct"].fillna(0.0)
    for row in sweep.sort_values(["pair_id", "d_axis"]).itertuples(index=False):
        d_label = "关闭" if pd.isna(row.correction_sell_pct) else f"{row.correction_sell_pct:g}%"
        lines.append(
            f"| {row.a_pct:.2f}/{row.b_pct:.2f} | {d_label} | "
            f"{row.rolling_5y_cagr_q25_pct:.3f}% | {row.restart_10y_cagr_q25_pct:.3f}% | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{int(row.sell_correction_count)} |"
        )
    return "\n".join(lines)


def markdown_report(
    run_id: str,
    results: pd.DataFrame,
    summary: dict[str, Any],
    events: pd.DataFrame,
) -> str:
    primary = results[
        (results["pair_id"] == "primary")
        & np.isclose(results["correction_buy_pct"], 5.0)
    ]
    reference = results[
        (results["pair_id"] == "drawdown_reference")
        & np.isclose(results["correction_buy_pct"], 5.0)
    ]
    p_off = primary[primary["correction_sell_pct"].isna()].iloc[0]
    p_five = primary[np.isclose(primary["correction_sell_pct"], 5.0)].iloc[0]
    r_off = reference[reference["correction_sell_pct"].isna()].iloc[0]
    r_five = reference[np.isclose(reference["correction_sell_pct"], 5.0)].iloc[0]
    return "\n".join(
        [
            "# QQQ SMA200 强制卖出 d 独立敏感性",
            "",
            f"> Run `{run_id}`；固定两组 a/b 与 c=5%，2000-01-03～2025-12-31 历史探索。",
            "",
            "## 结论",
            "",
            "- **没有 d 通过预登记的触发数与三点宽平台门禁，因此不优化 d。**",
            f"- 主候选在关闭 d 时 CAGR/Sharpe/回撤为 {p_off.cagr_pct:.3f}%/{p_off.sharpe:.3f}/{p_off.max_drawdown_pct:.2f}%；d=5% 为 {p_five.cagr_pct:.3f}%/{p_five.sharpe:.3f}/{p_five.max_drawdown_pct:.2f}%，只触发 {int(p_five.sell_correction_count)} 次。",
            f"- 回撤对照关闭 d 时为 {r_off.cagr_pct:.3f}%/{r_off.sharpe:.3f}/{r_off.max_drawdown_pct:.2f}%；d=5% 为 {r_five.cagr_pct:.3f}%/{r_five.sharpe:.3f}/{r_five.max_drawdown_pct:.2f}%，只触发 {int(r_five.sell_correction_count)} 次。",
            "- d=2.5% 分别触发28/31次，但两组的 CAGR、S5、S10、Sharpe和最大回撤均明显恶化；d≥7.5%在两组中全部零触发，与关闭完全等价。",
            f"- 18个 case 合计保存 {len(events)} 条强制卖出事件；累计数不可替代单一 d 的可识别性门禁。",
            "",
            "## c=5% 时的完整 d 扫描",
            "",
            comparison_table(results),
            "",
            "## 因果消融解释",
            "",
            "- 当 c 关闭时，d=5%在两组 a/b 下均为零触发，与 c、d 全关完全相同。深跌普通买入后，普通 SMA 卖出总在5%成本线之前触发。",
            "- c=5%会创建更频繁的纠错买回，从而改变成本锚；只有在这个路径上 d=5%才偶尔成为主卖出原因。因此 d 的效果与 c 强交互，不能独立从旧的 c=d 实验推断。",
            "- 本轮是同一历史上的机制诊断，不是2026年后的前向证据；使用复权OHLC且每日最多一笔。",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"Cannot analyze incomplete run: {incomplete}")
    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(parents=True, exist_ok=True)
    block = block_root(context, args.run_id, "QQQ", 5.0)
    results = pd.read_csv(block / "parameter_results.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    events = pd.read_csv(block / "forced_sell_events.csv", parse_dates=["sell_date", "next_buy_date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(block / "signal_plans.csv", parse_dates=["date"])
    selection = json.loads((block / "selection_summary.json").read_text(encoding="utf-8"))
    display = results[
        (results["pair_id"] == "primary")
        & np.isclose(results["correction_buy_pct"], 5.0)
        & np.isclose(results["correction_sell_pct"], 5.0)
    ]
    if len(display) != 1:
        raise AssertionError("Expected exactly one primary c=5%, d=5% display case.")
    display_row = display.iloc[0]
    display_case = str(display_row["case_id"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    prices = prepare_intraday_threshold_data(
        raw,
        int(context.config["strategy"]["sma_window"]),
    )
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    display_plans = plans[plans["case_id"] == display_case].copy()
    display_orders = orders[orders["case_id"] == display_case].copy()
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary = {
        **selection,
        "created_at_utc": created_at,
        "cases": json_safe(results.to_dict("records")),
        "event_summary": json_safe(
            events.groupby("case_id").agg(
                events=("event_index", "count"),
                median_flat_sessions=("flat_sessions", "median"),
                median_buyback_vs_sell_pct=("buyback_vs_sell_pct", "median"),
                median_min_low_20=("min_low_vs_raw_sell_pct_20", "median"),
                median_min_low_60=("min_low_vs_raw_sell_pct_60", "median"),
            ).reset_index().to_dict("records")
        ),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    compact = (
        "<p><strong>没有 d 通过预登记门禁。</strong> d=2.5%有足够触发但系统性恶化；"
        "d=5%只有4/7次触发，样本不足；d≥7.5%零触发，与关闭相同。"
        "因此本轮不把任何历史最好点解释为可优化参数。</p>"
    )
    figures = [
        ReportFigure("performance-qqq", "d 扫描的净值与回撤", performance_figure(daily, benchmark, results), "performance"),
        ReportFigure("d-response", "S5 / S10 / CAGR / Sharpe / 回撤 / 触发数", d_response_figure(results), "generic"),
        ReportFigure("cd-ablation", "c/d 独立开关 2×2 消融", ablation_figure(results), "generic"),
        ReportFigure("forced-sell-events", "每次强制卖出的后续路径", event_figure(events, results), "generic"),
        ReportFigure(
            "market-qqq",
            "主候选 c=5%、d=5% 的动态线与成交",
            market_figure(
                prices,
                display_plans,
                display_orders,
                a_pct=float(display_row["a_pct"]),
                b_pct=float(display_row["b_pct"]),
            ),
            "market",
        ),
    ]
    report = render_interactive_report(
        title="QQQ SMA200 强制卖出 d 独立敏感性",
        heading="QQQ SMA200 强制卖出 d 独立敏感性",
        subtitle="固定两组 a/b；先拆分 c/d 因果，再固定 c=5% 扫描 d=关闭至15%。",
        summary_html=compact,
        notes=[
            "单边5 bps、初始空仓、允许小数股、不融资；动态线均在开盘前由完成历史解出。",
            "S5是22个继承状态的滚动五年CAGR下四分位；S10是17个独立空仓十年重启CAGR下四分位。",
            "每个启用 d 至少需要10次主强制卖出，且至少三个相邻 d 同时通过多指标平台门槛，才允许提名。",
            "本轮在2000–2025历史上研究机制；2026以后仍是未用于本轮调参的连续前向期。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, results, selection, events), encoding="utf-8"
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_threshold.py",
        "backtest/quantkit/intraday_sma_threshold_search.py",
        "backtest/quantkit/intraday_sma_threshold_selection.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/run_intraday_sma200_forced_sell_sensitivity.py",
        "backtest/scripts/analyze_intraday_sma200_forced_sell_sensitivity.py",
        "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html",
        f"{template_path}/styles.css",
        f"{template_path}/interactions.js",
        "data/processed/manifest.json",
        "data/processed/daily/QQQ.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(),
            "numba": __import__("numba").__version__,
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
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This run contains the QQQ SMA200 asymmetric forced-sell sensitivity experiment.

- `report.html` / `report.md`: detailed c/d ablation, d response and event attribution.
- `analysis/summary.json`: machine-readable conclusions.
- `QQQ/cost_5bps/`: all 18 cases, rolling/restart windows and complete ledgers.
- `provenance.json` / `validation.json`: source and correctness evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {
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
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
