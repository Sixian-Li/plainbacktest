#!/usr/bin/env python3
"""Build the chart-first report for the QQQ three-window stability search."""

from __future__ import annotations

import argparse
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
from quantkit.intraday_sma import prepare_intraday_sma_data
from quantkit.intraday_sma_search import FORCED_REENTRY_COLUMN, PARAMETER_COLUMNS
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts import analyze_intraday_sma_backtest as base
from scripts.run_intraday_sma_global_search import spec_from_row


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / "experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability"


PARAMETER_LABELS = {
    "A_negative_days_slow": "A",
    "B_slow_sma_window": "B",
    "C_fast_derivative_pct": "C%",
    "D_negative_days_fast": "D",
    "E_fallback_sma_window": "E",
    "F_short_sma_center": "F中心",
    "F_short_sma_spacing": "F间距",
    "G_short_recovery_below_pct": "G%",
    "H_reentry_sma_window": "H",
    "L_cost_stop_pct": "L%",
    "R_forced_rebuy_pct": "R%",
    FORCED_REENTRY_COLUMN: "强制买回",
}


def representative_for_reason(formal: pd.DataFrame, reason: str) -> str:
    matches = formal[formal["selection_reason"].str.contains(reason, regex=False)]
    if matches.empty:
        raise ValueError(f"No representative contains selection reason {reason!r}.")
    return str(matches.iloc[0]["representative_id"])


def performance_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    formal: pd.DataFrame,
    *,
    window_id: str,
    objective: str = "cagr_pct",
) -> go.Figure:
    representative_id = representative_for_reason(formal, f"{window_id}:{objective}")
    row = formal[
        formal["window_id"].eq(window_id) & formal["representative_id"].eq(representative_id)
    ].iloc[0]
    case_id = str(row["case_id"])
    strategy = daily[daily["case_id"] == case_id].sort_values("date")
    buy_hold = benchmark[benchmark["case_id"] == case_id].sort_values("date")
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        row_heights=[0.7, 0.3], subplot_titles=(f"{window_id} CAGR 赢家净值", "从各自峰值回撤"),
    )
    for frame, label, key, color, dash, is_benchmark in (
        (strategy, f"{representative_id} 策略", case_id, "#0f766e", "solid", False),
        (buy_hold, "同首买点 Buy & Hold", f"buy_hold_{case_id}", "#334155", "dash", True),
    ):
        for panel, values, row_number, showlegend in (
            ("equity", frame["equity"], 1, True),
            ("drawdown", base.drawdown(frame["equity"]), 2, False),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=values, mode="lines", name=label, showlegend=showlegend,
                    line={"color": color, "width": 2.3, "dash": dash},
                    meta={
                        "series_key": key, "panel": panel, "label": label,
                        "is_benchmark": is_benchmark and panel == "equity", "cost_bps": 0,
                    },
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ),
                row=row_number, col=1,
            )
    figure.update_layout(
        height=760, margin={"l": 65, "r": 25, "t": 70, "b": 55},
        hovermode="x unified", showlegend=False, uirevision="qqq-window-stability-v1",
    )
    figure.update_yaxes(title_text="USD", fixedrange=False, row=1, col=1)
    figure.update_yaxes(title_text="%", fixedrange=False, row=2, col=1)
    figure.update_xaxes(rangeslider={"visible": True}, row=2, col=1)
    return figure


def all_windows_figure(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame,
    formal: pd.DataFrame,
    window_order: list[str],
) -> go.Figure:
    figure = make_subplots(
        rows=len(window_order), cols=1, vertical_spacing=0.08,
        subplot_titles=[f"{window_id}：本窗 CAGR 赢家与同首买点持有" for window_id in window_order],
    )
    for row_number, window_id in enumerate(window_order, start=1):
        representative_id = representative_for_reason(formal, f"{window_id}:cagr_pct")
        record = formal[
            formal["window_id"].eq(window_id) & formal["representative_id"].eq(representative_id)
        ].iloc[0]
        case_id = str(record["case_id"])
        strategy = daily[daily["case_id"] == case_id].sort_values("date")
        buy_hold = benchmark[benchmark["case_id"] == case_id].sort_values("date")
        for frame, label, color, dash in (
            (strategy, f"{window_id} CAGR 赢家", "#0f766e", "solid"),
            (buy_hold, f"{window_id} Buy & Hold", "#64748b", "dash"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=frame["date"], y=frame["equity"], mode="lines", name=label,
                    line={"color": color, "width": 2, "dash": dash},
                    hovertemplate=f"{label}<br>%{{x|%Y-%m-%d}}<br>%{{y:,.2f}}<extra></extra>",
                ), row=row_number, col=1,
            )
        figure.update_yaxes(title_text="USD", fixedrange=False, row=row_number, col=1)
    figure.update_layout(
        height=1050, hovermode="x unified", showlegend=True,
        margin={"l": 65, "r": 25, "t": 70, "b": 55},
    )
    return figure


def cross_window_rank_figure(formal: pd.DataFrame, window_order: list[str]) -> go.Figure:
    representatives = formal[["representative_id", "selection_reason"]].drop_duplicates()
    representatives = representatives.sort_values("representative_id")
    labels = [f"{row.representative_id} · {row.selection_reason}" for row in representatives.itertuples()]
    figure = make_subplots(rows=1, cols=2, subplot_titles=("CAGR 排名百分位", "Sharpe 排名百分位"))
    for column, field in enumerate(("cagr_pct_rank_percentile", "sharpe_rank_percentile"), start=1):
        values = []
        annotations = []
        for representative_id in representatives["representative_id"]:
            row_values = []
            row_text = []
            for window_id in window_order:
                row = formal[
                    formal["representative_id"].eq(representative_id) & formal["window_id"].eq(window_id)
                ].iloc[0]
                row_values.append(float(row[field]))
                row_text.append(f"{float(row[field]):.1f}")
            values.append(row_values)
            annotations.append(row_text)
        figure.add_trace(
            go.Heatmap(
                z=values, x=window_order, y=labels, text=annotations, texttemplate="%{text}",
                zmin=0, zmax=100, colorscale="RdYlGn", showscale=column == 2,
                colorbar={"title": "百分位"},
                hovertemplate="%{y}<br>%{x}<br>排名百分位 %{z:.2f}<extra></extra>",
            ), row=1, col=column,
        )
    figure.update_layout(height=max(560, 120 + 65 * len(representatives)), margin={"l": 230, "r": 80, "t": 70, "b": 60})
    return figure


def normalized_parameter(value: float, options: list[float]) -> float:
    low, high = float(min(options)), float(max(options))
    return 0.0 if high == low else (float(value) - low) / (high - low)


def parameter_stability_figure(
    winners: pd.DataFrame,
    top_summary: pd.DataFrame,
    search_space: dict[str, list[float]],
) -> go.Figure:
    parameters = [*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN]
    spaces = {**search_space, FORCED_REENTRY_COLUMN: [0.0, 1.0]}
    labels = [PARAMETER_LABELS[name] for name in parameters]
    winner_rows: list[list[float]] = []
    winner_labels: list[str] = []
    for row in winners.itertuples(index=False):
        winner_labels.append(f"{row.window_id} · {'CAGR' if row.objective == 'cagr_pct' else 'Sharpe'}")
        winner_rows.append([
            normalized_parameter(float(getattr(row, name)), spaces[name]) for name in parameters
        ])
    median_rows: list[list[float]] = []
    median_labels: list[str] = []
    for (window_id, objective), group in top_summary.groupby(["window_id", "objective"], sort=False):
        by_parameter = group.set_index("parameter")
        median_labels.append(f"{window_id} · top1% {'CAGR' if objective == 'cagr_pct' else 'Sharpe'}")
        median_rows.append([
            normalized_parameter(float(by_parameter.loc[name, "median"]), spaces[name])
            for name in parameters
        ])
    figure = make_subplots(rows=2, cols=1, vertical_spacing=0.18, subplot_titles=("精确赢家（按各参数搜索范围归一化）", "Top 1% 参数中位数"))
    figure.add_trace(
        go.Heatmap(z=winner_rows, x=labels, y=winner_labels, zmin=0, zmax=1, colorscale="Viridis", showscale=False, text=np.round(winner_rows, 2), texttemplate="%{text:.2f}"),
        row=1, col=1,
    )
    figure.add_trace(
        go.Heatmap(z=median_rows, x=labels, y=median_labels, zmin=0, zmax=1, colorscale="Viridis", showscale=True, colorbar={"title": "范围位置"}, text=np.round(median_rows, 2), texttemplate="%{text:.2f}"),
        row=2, col=1,
    )
    figure.update_layout(height=880, margin={"l": 210, "r": 80, "t": 80, "b": 60})
    return figure


def stability_statistics(
    winners: pd.DataFrame,
    formal: pd.DataFrame,
    search_space: dict[str, list[float]],
) -> dict[str, Any]:
    parameters = [*PARAMETER_COLUMNS, FORCED_REENTRY_COLUMN]
    spaces = {**search_space, FORCED_REENTRY_COLUMN: [0.0, 1.0]}
    objective_stats: dict[str, Any] = {}
    for objective in ("cagr_pct", "sharpe"):
        objective_winners = winners[winners["objective"] == objective].copy()
        vectors = np.asarray([
            [normalized_parameter(float(row[name]), spaces[name]) for name in parameters]
            for row in objective_winners.to_dict("records")
        ])
        distances = []
        for left in range(len(vectors)):
            for right in range(left + 1, len(vectors)):
                distances.append(float(np.abs(vectors[left] - vectors[right]).mean()))
        off_window_percentiles = []
        for winner in objective_winners.to_dict("records"):
            selection_reason = f"{winner['window_id']}:{objective}"
            representative_id = representative_for_reason(formal, selection_reason)
            field = f"{objective}_rank_percentile"
            off_window_percentiles.extend(
                formal[
                    formal["representative_id"].eq(representative_id)
                    & ~formal["window_id"].eq(winner["window_id"])
                ][field].astype(float).tolist()
            )
        objective_stats[objective] = {
            "mean_pairwise_winner_distance_normalized": float(np.mean(distances)),
            "max_pairwise_winner_distance_normalized": float(np.max(distances)),
            "median_off_window_rank_percentile": float(np.median(off_window_percentiles)),
            "minimum_off_window_rank_percentile": float(np.min(off_window_percentiles)),
        }
    return objective_stats


def markdown_report(
    run_id: str,
    summary: dict[str, Any],
    winners: pd.DataFrame,
    formal: pd.DataFrame,
    stability: dict[str, Any],
) -> str:
    lines = [
        "# QQQ 日内 SMA 三窗口参数稳定性",
        "",
        f"- Run：`{run_id}`；{summary['unique_candidate_count']:,} 个唯一参数在三窗共同评分，共 {summary['screened_case_window_count']:,} 个 case-window。",
        f"- {summary['representative_count']} 组唯一代表参数在三窗形成 {summary['formal_cross_case_count']} 个正式交叉 case，均不在报告中展示逐笔表。",
        "- 三个窗口相互重叠，因此只能检验时期敏感性，不能作为独立样本外证明。",
        "",
        "## 每窗样本内赢家",
        "",
        "| 窗口 | 目标 | 指标值 | A | B | C | D | E | F中心/间距 | G | H | L | R | 强制买回 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in winners.itertuples(index=False):
        lines.append(
            f"| {row.window_id} | {'CAGR' if row.objective == 'cagr_pct' else 'Sharpe'} | {row.value:.4f} | {row.A_negative_days_slow:g} | {row.B_slow_sma_window:g} | {row.C_fast_derivative_pct:g} | {row.D_negative_days_fast:g} | {row.E_fallback_sma_window:g} | {row.F_short_sma_center:g}/{row.F_short_sma_spacing:g} | {row.G_short_recovery_below_pct:g} | {row.H_reentry_sma_window:g} | {row.L_cost_stop_pct:g} | {row.R_forced_rebuy_pct:g} | {'开' if row.forced_reentry_enabled else '关'} |"
        )
    lines.extend(["", "## 稳定性摘要", ""])
    for objective, values in stability.items():
        label = "CAGR" if objective == "cagr_pct" else "Sharpe"
        lines.append(
            f"- {label} 赢家平均归一化参数距离 {values['mean_pairwise_winner_distance_normalized']:.3f}，最大 {values['max_pairwise_winner_distance_normalized']:.3f}；赢家移植到另外两窗的排名百分位中位数 {values['median_off_window_rank_percentile']:.2f}，最低 {values['minimum_off_window_rank_percentile']:.2f}。"
        )
    lines.extend([
        "",
        "## 边界",
        "",
        "- 每个 case 与 Buy & Hold 从该 case 在目标窗口的第一笔普通买入同时开始。",
        "- 2005、2010 窗口用之前历史预热指标但不允许提前交易；1999 窗口没有上市前数据。",
        "- 本轮窗口重叠、参数仍在样本内优化；任何赢家都不能直接晋级模拟盘。",
        "- 逐笔订单只保存在机器账本及 K 线 hover，HTML/Markdown 不生成逐笔买卖表。",
    ])
    return "\n".join(lines) + "\n"


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
    block = block_root(context, args.run_id, "QQQ", 0.0)
    screening = pd.read_csv(block / "parameter_results.csv", parse_dates=["first_entry_date"])
    formal = pd.read_csv(block / "formal_candidate_results.csv", parse_dates=["first_entry_date"])
    winners = pd.read_csv(block / "window_winners.csv")
    top_summary = pd.read_csv(block / "top_percent_parameter_summary.csv")
    daily = pd.read_csv(block / "daily.csv", parse_dates=["date"])
    benchmark = pd.read_csv(block / "buy_hold_daily.csv", parse_dates=["date"])
    orders = pd.read_csv(block / "orders.csv", parse_dates=["date", "signal_date"])
    summary = json.loads((block / "metrics.json").read_text(encoding="utf-8"))
    parameters = context.config["parameters"]
    window_order = [item["window_id"] for item in parameters["windows"]]
    latest_window = window_order[-1]
    stability = stability_statistics(winners, formal, parameters["search_space"])
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analysis_summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        **summary,
        "stability_statistics": stability,
        "formal_cross_results": base.json_safe(formal.to_dict("records")),
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(analysis_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    latest_rep = representative_for_reason(formal, f"{latest_window}:cagr_pct")
    latest_formal = formal[
        formal["window_id"].eq(latest_window) & formal["representative_id"].eq(latest_rep)
    ].iloc[0]
    latest_case = str(latest_formal["case_id"])
    raw = pd.read_csv(WORKSPACE_ROOT / "data/processed/daily/QQQ.csv", parse_dates=["date"])
    spec = spec_from_row(latest_formal)
    prices = prepare_intraday_sma_data(raw, spec)
    latest_config = parameters["windows"][-1]
    prices = prices[
        (prices["date"] >= pd.Timestamp(latest_config["analysis_start"]))
        & (prices["date"] <= pd.Timestamp(latest_config["analysis_end"]))
    ].reset_index(drop=True)
    latest_orders = orders[orders["case_id"] == latest_case].copy()

    cagr_stats = stability["cagr_pct"]
    sharpe_stats = stability["sharpe"]
    compact = (
        f"<p><strong>{summary['unique_candidate_count']:,}</strong> 个唯一参数在三个窗口共同评分；"
        f"正式交叉核验 <strong>{summary['formal_cross_case_count']}</strong> 个 case。"
        f"CAGR / Sharpe 赢家的平均归一化参数距离分别为 "
        f"<strong>{cagr_stats['mean_pairwise_winner_distance_normalized']:.3f}</strong> / "
        f"<strong>{sharpe_stats['mean_pairwise_winner_distance_normalized']:.3f}</strong>。"
        "下方先看图，报告不包含逐笔买卖表。</p>"
    )
    figures = [
        ReportFigure("performance-qqq", f"{latest_window} CAGR 赢家与同首买点持有", performance_figure(daily, benchmark, formal, window_id=latest_window), "performance"),
        ReportFigure("window-performance", "三个窗口各自 CAGR 赢家净值", all_windows_figure(daily, benchmark, formal, window_order), "generic"),
        ReportFigure("cross-window-ranks", "代表参数移植到其他窗口后的排名", cross_window_rank_figure(formal, window_order), "generic"),
        ReportFigure("parameter-stability", "赢家与 Top 1% 的参数位置", parameter_stability_figure(winners, top_summary, parameters["search_space"]), "generic"),
        ReportFigure("market-qqq", f"{latest_window} CAGR 赢家的价格、均线与成交原因", base.build_market_figure(prices, latest_orders, short_windows=spec.f_short_sma_windows), "market"),
    ]
    report = render_interactive_report(
        title="QQQ 日内 SMA 三窗口参数稳定性",
        heading="QQQ 日内 SMA 三窗口参数稳定性",
        subtitle="同一局部候选全集在三个重叠历史窗口中的 CAGR / Sharpe 优化与交叉排名。",
        summary_html=compact,
        notes=[
            "三个窗口相互重叠，结果衡量时期敏感性，不是独立样本外验证。",
            "2005 与 2010 窗口用窗口前历史预热均线，但账户从窗口首日现金开始且预热期没有交易；1999 窗口没有 QQQ 上市前数据。",
            "每个参数和 Buy & Hold 都从该参数在目标窗口的第一笔普通买入成交开始。",
            "成交原因仅保留在 K 线标记 hover 和机器账本，报告不生成逐笔买卖表。",
        ],
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=str(context.config["reporting"]["template_id"]),
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    (run_root / "report.md").write_text(
        markdown_report(args.run_id, summary, winners, formal, stability), encoding="utf-8"
    )

    template_path = str(context.config["reporting"]["template_path"])
    tracked = [
        "backtest/requirements.lock", "backtest/quantkit/execution.py", "backtest/quantkit/experiment.py",
        "backtest/quantkit/intraday_sma.py", "backtest/quantkit/intraday_sma_search.py",
        "backtest/quantkit/window_stability.py", "backtest/quantkit/metrics.py", "backtest/quantkit/reporting.py",
        "backtest/scripts/run_intraday_sma_backtest.py", "backtest/scripts/analyze_intraday_sma_backtest.py",
        "backtest/scripts/run_intraday_sma_global_search.py", "backtest/scripts/run_intraday_sma_window_stability.py",
        "backtest/scripts/analyze_intraday_sma_window_stability.py", "backtest/scripts/smoke_report_ui.mjs",
        f"{template_path}/page.html", f"{template_path}/styles.css", f"{template_path}/interactions.js",
        "data/processed/manifest.json", "data/processed/daily/QQQ.csv",
    ]
    provenance: dict[str, Any] = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id, "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(), "numba": __import__("numba").__version__,
            "lib_pybroker": "1.2.12", "plotly": plotly.__version__,
        },
        "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (run_root / "README.md").write_text(
        f"""# Run {args.run_id}

This immutable run contains the shared-candidate three-window search and formal cross-window checks.

- `report.html` / `report.md`: chart-first reports without a per-order table.
- `analysis/summary.json`: machine-readable stability statistics and formal cross results.
- `QQQ/cost_0bps/`: every case/window score, top-1% summaries, representatives and full ledgers.
- `provenance.json` / `validation.json`: source and correctness evidence.
""",
        encoding="utf-8",
    )
    artifact_manifest: dict[str, Any] = {"schema_version": 1, "created_at_utc": created_at, "artifacts": {}}
    for path in sorted(run_root.rglob("*")):
        if path.is_file() and path.name not in {"artifact_manifest.json", "run.json", "validation.json"}:
            relative = str(path.relative_to(run_root))
            artifact_manifest["artifacts"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    (run_root / "artifact_manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    record_analysis_complete(context, args.run_id)
    print(f"Wrote {run_root / 'report.html'}")


if __name__ == "__main__":
    main()
