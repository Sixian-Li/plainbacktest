#!/usr/bin/env python3
"""Analyze and report the QQQ intraday SMA200 threshold experiment."""

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
from scripts.run_intraday_sma_backtest import json_safe


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = (
    BACKTEST_ROOT / "experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025"
)
MODE_LABELS = {
    "disabled": "不启用纠错线",
    "cd_5pct": "c=d=5%",
    "cd_10pct": "c=d=10%",
}
SIGNAL_LABELS = {
    "BUY_SMA200_THRESHOLD": "SMA200 普通上穿买入",
    "SELL_SMA200_THRESHOLD": "SMA200 普通下穿卖出",
    "BUY_CORRECTION": "卖出价上方纠错买回",
    "SELL_CORRECTION": "买入价下方纠错卖出",
}


def drawdown(values: pd.Series) -> pd.Series:
    series = values.astype(float)
    return (series / series.cummax() - 1.0) * 100.0


def stable_rows(formal: pd.DataFrame) -> pd.DataFrame:
    rows = formal[formal["selection_reason"].str.contains("STABLE_PLATEAU")].copy()
    if set(rows["correction_mode"]) != set(MODE_LABELS):
        raise AssertionError("Formal candidates do not contain one stable row per correction mode.")
    return rows.sort_values("correction_mode").reset_index(drop=True)


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    stable: pd.DataFrame,
    *,
    benchmark_entry_date: str,
) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("5 bps 稳定代表净值", "从各自历史峰值回撤"),
    )
    colors = {"disabled": "#2563eb", "cd_5pct": "#0f766e", "cd_10pct": "#7c3aed"}
    for row in stable.itertuples(index=False):
        case_id = str(row.case_id)
        frame = daily[daily["case_id"] == case_id].sort_values("date")
        label = (
            f"{MODE_LABELS[str(row.correction_mode)]} "
            f"(a={row.a_pct:.2f}%, b={row.b_pct:.2f}%)"
        )
        color = colors[str(row.correction_mode)]
        for panel, values, panel_name, showlegend in (
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
                    line={"color": color, "width": 2.2},
                    meta={"series_key": case_id, "panel": panel_name, "label": label},
                ),
                row=panel,
                col=1,
            )
    benchmark = benchmark.sort_values("date")
    benchmark_label = f"QQQ Buy & Hold（{benchmark_entry_date} Open）"
    for panel, values, panel_name, showlegend in (
        (1, benchmark["equity"], "equity", True),
        (2, drawdown(benchmark["equity"]), "drawdown", False),
    ):
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=values,
                mode="lines",
                name=benchmark_label,
                showlegend=showlegend,
                line={"color": "#334155", "width": 2.0, "dash": "dash"},
                meta={
                    "series_key": "buy_hold_5bps",
                    "panel": panel_name,
                    "label": benchmark_label,
                    "is_benchmark": panel_name == "equity",
                    "cost_bps": 5,
                },
            ),
            row=panel,
            col=1,
        )
    figure.update_layout(
        height=780,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-intraday-sma200-threshold-performance-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def heatmap_figure(
    screening: pd.DataFrame,
    formal: pd.DataFrame,
) -> go.Figure:
    ordered_modes = tuple(MODE_LABELS)
    minimum = float(screening["cagr_pct"].min())
    maximum = float(screening["cagr_pct"].max())
    figure = make_subplots(
        rows=1,
        cols=3,
        subplot_titles=[MODE_LABELS[mode] for mode in ordered_modes],
        horizontal_spacing=0.06,
    )
    for column, mode in enumerate(ordered_modes, start=1):
        frame = screening[screening["correction_mode"] == mode]
        pivot = frame.pivot(index="a_pct", columns="b_pct", values="cagr_pct").sort_index().sort_index(axis=1)
        figure.add_trace(
            go.Heatmap(
                x=pivot.columns,
                y=pivot.index,
                z=pivot.to_numpy(),
                coloraxis="coloraxis",
                hovertemplate="a=%{y:.2f}%<br>b=%{x:.2f}%<br>CAGR=%{z:.3f}%<extra></extra>",
            ),
            row=1,
            col=column,
        )
        selected = formal[formal["correction_mode"] == mode]
        for symbol, reason in (("x", "GLOBAL_CAGR"), ("circle-open", "STABLE_PLATEAU")):
            row = selected[selected["selection_reason"].str.contains(reason)].iloc[0]
            figure.add_trace(
                go.Scatter(
                    x=[row["b_pct"]],
                    y=[row["a_pct"]],
                    mode="markers",
                    marker={"symbol": symbol, "size": 12, "color": "white", "line": {"width": 2}},
                    showlegend=False,
                    hovertemplate=f"{reason}<br>a=%{{y:.2f}}%<br>b=%{{x:.2f}}%<extra></extra>",
                ),
                row=1,
                col=column,
            )
        figure.update_xaxes(title_text="b（%）", row=1, col=column)
        if column == 1:
            figure.update_yaxes(title_text="a（%）", row=1, col=column)
    figure.update_layout(
        height=660,
        margin={"l": 60, "r": 65, "t": 80, "b": 55},
        coloraxis={
            "colorscale": "Turbo",
            "cmin": minimum,
            "cmax": maximum,
            "colorbar": {"title": "CAGR %"},
        },
    )
    return figure


def market_figure(
    prices: pd.DataFrame,
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    *,
    a_pct: float,
    b_pct: float,
) -> go.Figure:
    frame = prices.merge(
        plans[["date", "side", "sma_trigger", "correction_trigger"]],
        on="date",
        how="left",
    )
    frame["buy_sma_trigger"] = frame["sma_trigger"].where(frame["side"] == "buy")
    frame["sell_sma_trigger"] = frame["sma_trigger"].where(frame["side"] == "sell")
    frame["buy_correction_trigger"] = frame["correction_trigger"].where(frame["side"] == "buy")
    frame["sell_correction_trigger"] = frame["correction_trigger"].where(frame["side"] == "sell")
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=frame["date"], open=frame["open"], high=frame["high"],
            low=frame["low"], close=frame["close"], name="QQQ 复权 OHLC",
            increasing_line_color="#1b7f5a", decreasing_line_color="#c2413b",
        )
    )
    overlays = (
        ("close", "QQQ Close", "#111827", "solid"),
        ("sma", "SMA200（收盘显示）", "#f59e0b", "solid"),
        ("buy_sma_trigger", f"普通买入动态线 b={b_pct:.2f}%", "#16a34a", "dash"),
        ("sell_sma_trigger", f"普通卖出动态线 a={a_pct:.2f}%", "#dc2626", "dash"),
        ("buy_correction_trigger", "纠错买回线", "#0891b2", "dot"),
        ("sell_correction_trigger", "纠错卖出线", "#9333ea", "dot"),
    )
    for column, label, color, dash in overlays:
        figure.add_trace(
            go.Scatter(
                x=frame["date"], y=frame[column], mode="lines", name=label,
                line={"color": color, "width": 1.8, "dash": dash},
                connectgaps=False,
                meta={
                    "series_key": column,
                    "panel": "market",
                    "label": label,
                    "control_group": "thresholds",
                    "control_group_label": "价格、SMA200 与当日预挂线",
                },
            )
        )
    for side, color, marker, label in (
        ("buy", "#087f5b", "triangle-up", "买入成交"),
        ("sell", "#c92a2a", "triangle-down", "卖出成交"),
    ):
        selected = orders[orders["type"] == side].copy()
        custom = (
            np.column_stack(
                [
                    selected["primary_signal"].map(SIGNAL_LABELS),
                    selected["theoretical_trigger"],
                    selected["raw_fill_price"],
                    selected["fill_source"],
                ]
            )
            if len(selected)
            else np.empty((0, 4))
        )
        figure.add_trace(
            go.Scatter(
                x=selected["date"], y=selected["fill_price"], customdata=custom,
                mode="markers", name=label,
                marker={"color": color, "symbol": marker, "size": 10},
                meta={
                    "series_key": f"trade_{side}", "panel": "market", "label": label,
                    "control_group": "thresholds", "control_group_label": "价格、SMA200 与当日预挂线",
                },
                hovertemplate=(
                    f"{label}<br>%{{x|%Y-%m-%d}}<br>账户成交 $%{{y:.4f}}"
                    "<br>原因 %{customdata[0]}<br>理论线 $%{customdata[1]:.4f}"
                    "<br>原始成交 $%{customdata[2]:.4f}<br>方式 %{customdata[3]}<extra></extra>"
                ),
            )
        )
    figure.update_layout(
        height=760,
        margin={"l": 65, "r": 25, "t": 55, "b": 55},
        hovermode="x unified",
        showlegend=False,
        uirevision="qqq-intraday-sma200-threshold-market-v1",
    )
    figure.update_xaxes(
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        rangeslider={"visible": True, "thickness": 0.08},
    )
    figure.update_yaxes(title_text="复权价格（USD）", fixedrange=False)
    return figure


def report_table(
    zero_screening: pd.DataFrame,
    cost_formal: pd.DataFrame,
    benchmark: dict[str, Any],
) -> str:
    lines = [
        "| 纠错模式 | 5 bps 稳定 a | 5 bps 稳定 b | CAGR | Sharpe | 最大回撤 | 成交 | 0 bps 同参数 CAGR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in stable_rows(cost_formal).itertuples(index=False):
        zero_match = zero_screening[
            (zero_screening["correction_mode"] == row.correction_mode)
            & np.isclose(zero_screening["a_pct"], row.a_pct)
            & np.isclose(zero_screening["b_pct"], row.b_pct)
        ]
        zero_cagr = float(zero_match.iloc[0]["cagr_pct"]) if len(zero_match) == 1 else float("nan")
        lines.append(
            f"| {MODE_LABELS[str(row.correction_mode)]} | {row.a_pct:.2f}% | {row.b_pct:.2f}% | "
            f"{row.cagr_pct:.3f}% | {row.sharpe:.3f} | {row.max_drawdown_pct:.2f}% | "
            f"{int(row.order_count)} | {zero_cagr:.3f}% |"
        )
    lines.extend(
        [
            "",
            f"统一 QQQ Buy & Hold（5 bps 入场）：CAGR {benchmark['cagr_pct']:.3f}%，"
            f"Sharpe {benchmark['sharpe']:.3f}，最大回撤 {benchmark['max_drawdown_pct']:.2f}%。",
        ]
    )
    return "\n".join(lines)


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
    blocks = {cost: block_root(context, args.run_id, "QQQ", cost) for cost in (0.0, 5.0)}
    screening = {cost: pd.read_csv(root / "parameter_results.csv") for cost, root in blocks.items()}
    formal = {cost: pd.read_csv(root / "formal_candidate_results.csv") for cost, root in blocks.items()}
    daily = pd.read_csv(blocks[5.0] / "daily.csv", parse_dates=["date"])
    benchmark_daily = pd.read_csv(blocks[5.0] / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(blocks[5.0] / "orders.csv", parse_dates=["date"])
    plans = pd.read_csv(blocks[5.0] / "signal_plans.csv", parse_dates=["date"])
    metrics = {
        cost: json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        for cost, root in blocks.items()
    }
    stable = stable_rows(formal[5.0])
    best = stable.loc[stable["cagr_pct"].idxmax()]
    best_case = str(best["case_id"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    prices = prepare_intraday_threshold_data(
        raw,
        int(context.config["strategy"]["sma_window"]),
    )
    start = pd.Timestamp(context.config["parameters"]["analysis_start"])
    end = pd.Timestamp(context.config["parameters"]["analysis_end"])
    start_label = start.date().isoformat()
    end_label = end.date().isoformat()
    period_label = f"{start_label}～{end_label}"
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    best_plans = plans[plans["case_id"] == best_case].copy()
    best_orders = orders[orders["case_id"] == best_case].copy()

    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "cost_blocks": metrics,
        "stable_5bps": json_safe(stable.to_dict("records")),
        "best_stable_case_id": best_case,
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    table = report_table(screening[0.0], formal[5.0], metrics[5.0]["benchmark_metrics"])
    report_md = "\n".join(
        [
            "# QQQ 日内动态 SMA200 双阈值探索",
            "",
            f"> {period_label} 样本内优化；不是样本外验证或实盘推荐。",
            "",
            "- 每个交易日前用此前 199 个完成收盘解出当日动态 SMA200 触发价。",
            "- 盘中触及按理论线成交；隔夜越过按常规时段 Open；每日最多一笔。",
            "- a、b 各为 -20%～20%、步长 0.25%；纠错关闭、c=d=5%、c=d=10%。",
            "- 每个成本块 77,763 个 case；0/5 bps 共 155,526 个。",
            f"- 初始 100,000 美元现金；所有绩效从 {start_label} 开始，统一 Buy & Hold 也在该日 Open 入场。",
            "",
            "## 5 bps 稳定平台代表",
            "",
            table,
            "",
            "## 解释边界",
            "",
            "- 稳定代表从最大 top-decile 四邻域连通区中，按 3×3 邻域中位数减半个标准差选择；不是机械最高点。",
            f"- 本轮在同一 {period_label} 区间搜索并评价，参数表现含样本内选择偏差。",
            "- 日线只有 OHLC，无法还原同一天成交后的第二次反向触发，因此明确限制每天最多一笔。",
            "- 使用拆股及股息调整 OHLC，是内部一致的总回报价格近似，不是原始成交价加现金分红的账户级回放。",
        ]
    ) + "\n"
    (run_root / "report.md").write_text(report_md, encoding="utf-8")

    summary_html = (
        f"<p><strong>155,526</strong> 个参数/成本 case 完整穷举；"
        f"5 bps 下最佳稳定代表来自 <strong>{html.escape(MODE_LABELS[str(best['correction_mode'])])}</strong>，"
        f"a=<strong>{best['a_pct']:.2f}%</strong>、b=<strong>{best['b_pct']:.2f}%</strong>，"
        f"CAGR <strong>{best['cagr_pct']:.3f}%</strong>、Sharpe <strong>{best['sharpe']:.3f}</strong>、"
        f"最大回撤 <strong>{best['max_drawdown_pct']:.2f}%</strong>。这仍是样本内结果。</p>"
    )
    figures = [
        ReportFigure(
            "performance-qqq",
            "5 bps 稳定代表与 Buy & Hold",
            performance_figure(
                daily,
                benchmark_daily,
                stable,
                benchmark_entry_date=start_label,
            ),
            "performance",
        ),
        ReportFigure(
            "cagr-surfaces",
            "5 bps 三种纠错模式的同尺度 CAGR 参数面",
            heatmap_figure(screening[5.0], formal[5.0]),
            "generic",
        ),
        ReportFigure(
            "market-qqq",
            "最佳稳定代表的动态阈值、纠错线与成交",
            market_figure(
                prices,
                best_plans,
                best_orders,
                a_pct=float(best["a_pct"]),
                b_pct=float(best["b_pct"]),
            ),
            "market",
        ),
    ]
    report_html = render_interactive_report(
        title=f"QQQ 日内动态 SMA200 双阈值探索（{period_label}）",
        heading=f"QQQ 日内动态 SMA200 双阈值探索（{period_label}）",
        subtitle=f"{period_label} 样本内：盘前解出动态阈值，盘中触价或跳空开盘成交。",
        summary_html=summary_html,
        notes=[
            "普通买入保留向上穿越资格：前一完成收盘必须位于对应买入边界下方或相等。",
            "纠错线以计入成本后的实际账户成交价为锚；关闭、5%、10% 三种模式分别搜索 a/b。",
            "每个成本块完整保存 77,763 个 case；正式图中的代表点另经 PyBroker 与独立账本逐日核对。",
            f"本轮直接在 {period_label} 搜索并评价，只能用于历史时期敏感性比较。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report_html, encoding="utf-8")

    created_at = summary["created_at_utc"]
    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma_threshold.py",
        "backtest/quantkit/intraday_sma_threshold_search.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/reporting.py",
        "backtest/quantkit/surface.py",
        "backtest/scripts/run_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma200_threshold_grid.py",
        "backtest/scripts/analyze_intraday_sma200_threshold_grid.py",
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

This run contains the exhaustive QQQ intraday SMA200 threshold search for {period_label}.

- `report.html` / `report.md`: interactive and concise research reports.
- `analysis/summary.json`: machine-readable selected representatives.
- `QQQ/cost_0bps/` and `QQQ/cost_5bps/`: complete surfaces and formal ledgers.
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
