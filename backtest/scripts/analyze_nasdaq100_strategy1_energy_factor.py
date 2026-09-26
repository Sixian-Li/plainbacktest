#!/usr/bin/env python3
"""Build the v5 Strategy1 energy-factor anatomy report."""

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
    "experiments/ROT/ROT-v0.50a.5__26-08-28__nasdaq100_strategy1_energy_factor_anatomy"
)
SYMBOL = "NASDAQ100_STRATEGY1_ENERGY_FACTOR"
BUCKET_ORDER = ["E00_20", "E20_50", "E50_80", "E80_90", "E90_95", "E95_99", "E99_100"]
BUCKET_LABELS = {
    "E00_20": "0–20%", "E20_50": "20–50%", "E50_80": "50–80%",
    "E80_90": "80–90%", "E90_95": "90–95%", "E95_99": "95–99%",
    "E99_100": "99–100%",
}
SPREAD_LABELS = {
    "HIGH_GT90_MINUS_NOT_HIGH": ">90% − ≤90%",
    "E99_100_MINUS_E90_95": "99–100% − 90–95%",
    "HIGH_RISING_MINUS_HIGH_FALLING": "高能上升 − 高能下降",
}
ERA_LABELS = {
    "ERA_2000_2004": "1999–2004", "ERA_2005_2009": "2005–2009",
    "ERA_2010_2014": "2010–2014", "ERA_2015_2019": "2015–2019",
    "ERA_2020_2026": "2020–2026",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _fmt(value: object, digits: int = 3, percent: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    if percent:
        return f"{number * 100:+.{digits}f}%"
    return f"{number:+.{digits}f}"


def load_blocks(run_root: Path) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in (0.0, 5.0):
        root = run_root / SYMBOL / f"cost_{cost:g}bps"
        blocks[cost] = {
            "root": root,
            "metrics": json.loads((root / "metrics.json").read_text(encoding="utf-8")),
            "bucket": pd.read_csv(root / "bucket_summary.csv"),
            "state": pd.read_csv(root / "state_summary.csv"),
            "ic": pd.read_csv(root / "daily_rank_ic.csv.gz", parse_dates=["signal_date"]),
            "era_ic": pd.read_csv(root / "era_rank_ic_summary.csv"),
            "bootstrap": pd.read_csv(root / "bootstrap_spreads.csv"),
            "era_spreads": pd.read_csv(root / "era_spreads.csv"),
            "era_bucket": pd.read_csv(root / "era_bucket_summary.csv"),
            "monotonicity": pd.read_csv(root / "bucket_monotonicity.csv"),
        }
    return blocks


def bucket_curve_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    horizons = [5, 10, 20, 60]
    figure = make_subplots(rows=2, cols=2, subplot_titles=[f"未来{h}个交易日" for h in horizons])
    for index, horizon in enumerate(horizons):
        row, col = divmod(index, 2)
        for cost, color, dash in ((0.0, "#2563eb", "solid"), (5.0, "#f97316", "dot")):
            data = blocks[cost]["bucket"]
            selected = data[data["horizon"].eq(horizon)].copy()
            selected["order"] = selected["energy_bucket"].map({v: i for i, v in enumerate(BUCKET_ORDER)})
            selected = selected.sort_values("order")
            figure.add_trace(
                go.Scatter(
                    x=[BUCKET_LABELS[value] for value in selected["energy_bucket"]],
                    y=selected["date_equal_mean_excess_return"] * 100,
                    mode="lines+markers",
                    name=f"{cost:g} bps",
                    showlegend=index == 0,
                    line={"color": color, "dash": dash, "width": 2.5},
                    customdata=np.column_stack([
                        selected["event_count"], selected["security_count"],
                        selected["security_equal_mean_excess_return"] * 100,
                    ]),
                    hovertemplate=("能量 %{x}<br>逐日等权超额 %{y:+.3f}%"
                                   "<br>事件 %{customdata[0]:,.0f} · 证券 %{customdata[1]:,.0f}"
                                   "<br>证券等权超额 %{customdata[2]:+.3f}%<extra></extra>"),
                ), row=row + 1, col=col + 1,
            )
        figure.add_hline(y=0, line_dash="dot", line_color="#94a3b8", row=row + 1, col=col + 1)
        figure.update_yaxes(title_text="相对QQQ超额 %", row=row + 1, col=col + 1)
    figure.update_layout(height=790, legend={"orientation": "h"}, hovermode="x unified")
    return figure


def rank_ic_figure(block: dict[str, Any]) -> go.Figure:
    ic = block["ic"]
    full = ic.groupby(["horizon", "factor_variant"], as_index=False).agg(
        mean_rank_ic=("rank_ic", "mean"), median_rank_ic=("rank_ic", "median"),
        positive_rate=("rank_ic", lambda values: float((values > 0).mean())),
        date_count=("rank_ic", "size"),
    )
    era = block["era_ic"]
    raw = era[era["factor_variant"].eq("RAW_SCORE")].copy()
    raw["era_label"] = raw["era_id"].map(ERA_LABELS)
    matrix = raw.pivot(index="era_label", columns="horizon", values="mean_rank_ic").reindex(ERA_LABELS.values())
    figure = make_subplots(rows=1, cols=2, subplot_titles=("全期逐日横截面Rank IC", "原始能量的分时期Rank IC"))
    for variant, label, color in (
        ("RAW_SCORE", "原始能量", "#2563eb"),
        ("OWN_HISTORY_PERCENTILE", "自身历史百分位", "#7c3aed"),
    ):
        selected = full[full["factor_variant"].eq(variant)].sort_values("horizon")
        figure.add_trace(go.Bar(
            x=[f"{int(value)}日" for value in selected["horizon"]],
            y=selected["mean_rank_ic"], name=label, marker_color=color,
            customdata=np.column_stack([selected["median_rank_ic"], selected["positive_rate"] * 100, selected["date_count"]]),
            hovertemplate=("平均IC %{y:+.4f}<br>中位IC %{customdata[0]:+.4f}"
                           "<br>IC为正日期 %{customdata[1]:.1f}% · 日期 %{customdata[2]:,.0f}<extra></extra>"),
        ), row=1, col=1)
    figure.add_trace(go.Heatmap(
        z=matrix.to_numpy(float), x=[f"{int(value)}日" for value in matrix.columns], y=matrix.index,
        colorscale="RdBu", zmid=0, colorbar={"title": "Rank IC"},
        text=np.vectorize(lambda value: f"{value:+.3f}")(matrix.to_numpy(float)),
        texttemplate="%{text}", hovertemplate="%{y} · %{x}<br>平均IC %{z:+.4f}<extra></extra>",
    ), row=1, col=2)
    figure.add_hline(y=0, line_dash="dot", line_color="#64748b", row=1, col=1)
    figure.update_layout(height=590, barmode="group", legend={"orientation": "h"})
    return figure


def spread_figure(blocks: dict[float, dict[str, Any]]) -> go.Figure:
    figure = make_subplots(rows=1, cols=3, subplot_titles=list(SPREAD_LABELS.values()))
    for column, spread_id in enumerate(SPREAD_LABELS, start=1):
        for cost, color, offset in ((0.0, "#2563eb", -0.12), (5.0, "#f97316", 0.12)):
            data = blocks[cost]["bootstrap"]
            selected = data[data["spread_id"].eq(spread_id)].sort_values("horizon")
            x = np.arange(len(selected), dtype=float) + offset
            y = selected["mean"].to_numpy(float) * 100
            lower = (selected["mean"] - selected["ci_lower"]).to_numpy(float) * 100
            upper = (selected["ci_upper"] - selected["mean"]).to_numpy(float) * 100
            figure.add_trace(go.Scatter(
                x=x, y=y, mode="markers", marker={"size": 10, "color": color},
                error_y={"type": "data", "array": upper, "arrayminus": lower, "visible": True},
                name=f"{cost:g} bps", showlegend=column == 1,
                customdata=np.column_stack([selected["horizon"], selected["count"], selected["month_count"]]),
                hovertemplate=("未来%{customdata[0]:.0f}日<br>逐日等权差 %{y:+.3f}%"
                               "<br>日期 %{customdata[1]:,.0f} · 月块 %{customdata[2]:,.0f}<extra></extra>"),
            ), row=1, col=column)
        figure.add_hline(y=0, line_dash="dot", line_color="#64748b", row=1, col=column)
        figure.update_xaxes(tickmode="array", tickvals=np.arange(4), ticktext=["5日", "10日", "20日", "60日"], row=1, col=column)
        figure.update_yaxes(title_text="相对QQQ超额差 %" if column == 1 else None, row=1, col=column)
    figure.update_layout(height=590, legend={"orientation": "h"})
    return figure


def era_bucket_figure(block: dict[str, Any], horizon: int = 20) -> go.Figure:
    data = block["era_bucket"]
    selected = data[data["horizon"].eq(horizon)].copy()
    selected["era_label"] = selected["era_id"].map(ERA_LABELS)
    matrix = selected.pivot(index="era_label", columns="energy_bucket", values="date_equal_mean_excess_return")
    matrix = matrix.reindex(index=ERA_LABELS.values(), columns=BUCKET_ORDER) * 100
    figure = go.Figure(go.Heatmap(
        z=matrix.to_numpy(float), x=[BUCKET_LABELS[value] for value in matrix.columns], y=matrix.index,
        colorscale="RdBu", zmid=0, colorbar={"title": "超额 %"},
        text=np.vectorize(lambda value: f"{value:+.2f}%")(matrix.to_numpy(float)), texttemplate="%{text}",
        hovertemplate="%{y} · 能量 %{x}<br>未来20日逐日等权超额 %{z:+.3f}%<extra></extra>",
    ))
    figure.update_layout(height=570)
    return figure


def path_and_sample_figure(block: dict[str, Any], horizon: int = 20) -> go.Figure:
    data = block["bucket"]
    selected = data[data["horizon"].eq(horizon)].copy()
    selected["order"] = selected["energy_bucket"].map({v: i for i, v in enumerate(BUCKET_ORDER)})
    selected = selected.sort_values("order")
    x = [BUCKET_LABELS[value] for value in selected["energy_bucket"]]
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(go.Bar(x=x, y=selected["mean_maximum_favorable_excursion"] * 100, name="平均最大有利涨幅", marker_color="#16a34a"), secondary_y=False)
    figure.add_trace(go.Bar(x=x, y=selected["mean_maximum_adverse_excursion"] * 100, name="平均最大不利回撤", marker_color="#dc2626"), secondary_y=False)
    figure.add_trace(go.Scatter(x=x, y=selected["event_count"], name="事件数", mode="lines+markers", line={"color": "#334155", "width": 2.5}), secondary_y=True)
    figure.update_yaxes(title_text="相对入场价 %", secondary_y=False)
    figure.update_yaxes(title_text="证券日事件数", secondary_y=True)
    figure.update_layout(height=560, barmode="group", legend={"orientation": "h"})
    return figure


def summary_html(blocks: dict[float, dict[str, Any]]) -> str:
    primary = blocks[0.0]
    decision = primary["metrics"]["factor_decision"]
    raw = primary["ic"][primary["ic"]["factor_variant"].eq("RAW_SCORE")].groupby("horizon")["rank_ic"].mean()
    bootstrap = primary["bootstrap"].copy()
    rows = []
    for spread_id in SPREAD_LABELS:
        for item in bootstrap[bootstrap["spread_id"].eq(spread_id)].sort_values("horizon").itertuples(index=False):
            rows.append(
                "<tr>" f"<td>{html.escape(SPREAD_LABELS[spread_id])}</td><td>{int(item.horizon)}日</td>"
                f"<td>{_fmt(item.mean, percent=True)}</td><td>{_fmt(item.ci_lower, percent=True)}</td>"
                f"<td>{_fmt(item.ci_upper, percent=True)}</td><td>{int(item.count):,}</td></tr>"
            )
    verdict = lambda value: "支持" if bool(value) else "不支持"
    return (
        '<div class="summary-grid">'
        f'<div class="summary-card"><strong>原始能量可排序</strong><span>{verdict(decision["raw_score_ranking_supported"])}</span></div>'
        f'<div class="summary-card"><strong>90%门禁有独立信息</strong><span>{verdict(decision["high_energy_gate_supported"])}</span></div>'
        f'<div class="summary-card"><strong>高能上升优于高能下降</strong><span>{verdict(decision["high_energy_direction_supported"])}</span></div>'
        f'<div class="summary-card"><strong>10 / 20 / 60日平均Rank IC</strong><span>{_fmt(raw.get(10))} / {_fmt(raw.get(20))} / {_fmt(raw.get(60))}</span></div>'
        f'<div class="summary-card"><strong>可执行事件</strong><span>{int(primary["metrics"]["event_count"]):,} 条 · {int(primary["metrics"]["event_security_count"]):,} 个证券身份</span></div>'
        f'<div class="summary-card"><strong>数据结论等级</strong><span>{html.escape(str(primary["metrics"]["data_status"]))} · 探索性</span></div>'
        '</div>'
        '<p class="strategy-note">这里的“支持”只按实验开始前冻结的判据机械判断；它不是挑选最好期限，也不是可直接交易的组合收益。每个证券日事件独立归一化，重叠事件不共享资本，因此本报告不计算CAGR。</p>'
        '<details><summary>展开三项诊断差的月度区块95%置信区间</summary><div class="table-wrap"><table><thead><tr>'
        '<th>诊断</th><th>期限</th><th>逐日等权差</th><th>区间下界</th><th>区间上界</th><th>日期数</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    assert_run_writable(context, args.run_id)
    record = load_run(context, args.run_id)
    incomplete = [item["block_id"] for item in record["expected_blocks"] if item["status"] != "completed"]
    if incomplete:
        raise RuntimeError(f"incomplete blocks: {incomplete}")
    run_root = context.run_root(args.run_id)
    blocks = load_blocks(run_root)
    primary = blocks[0.0]
    decision = primary["metrics"]["factor_decision"]
    analysis = run_root / "analysis"
    analysis.mkdir(exist_ok=True)
    ic_summary = primary["ic"].groupby(["horizon", "factor_variant"], as_index=False).agg(
        date_count=("rank_ic", "size"), mean_rank_ic=("rank_ic", "mean"),
        median_rank_ic=("rank_ic", "median"), positive_rate=("rank_ic", lambda values: float((values > 0).mean())),
    )
    ic_summary.to_csv(analysis / "rank_ic_summary.csv", index=False, lineterminator="\n")
    cost_comparison = pd.concat([
        block["bucket"].assign(cost_bps=cost) for cost, block in blocks.items()
    ], ignore_index=True)
    cost_comparison.to_csv(analysis / "bucket_cost_comparison.csv", index=False, lineterminator="\n")
    pd.concat([
        block["bootstrap"].assign(cost_bps=cost) for cost, block in blocks.items()
    ], ignore_index=True).to_csv(analysis / "spread_cost_comparison.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "experiment_id": context.config["experiment_id"], "run_id": args.run_id,
        "data_status": primary["metrics"]["data_status"], "primary_cost_bps": 0.0,
        "factor_decision": decision, "rank_ic_summary": ic_summary.to_dict("records"),
        "bucket_monotonicity": primary["monotonicity"].to_dict("records"),
        "bootstrap_spreads": primary["bootstrap"].to_dict("records"),
        "interpretation_boundary": "overlapping normalized events; no capital-feasible portfolio CAGR",
    }
    (analysis / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    report = render_interactive_report(
        title="Nasdaq-100 Strategy1 能量因子解剖",
        heading="高能量究竟预示未来上涨，还是只记录已经发生的路径",
        subtitle=(f"冻结母策略 · {primary['metrics']['event_security_count']:,}个证券身份 · "
                  f"{primary['metrics']['event_count']:,}条可执行期限事件 · 0/5 bps"),
        summary_html=summary_html(blocks),
        notes=[
            "主口径先在每个日期、每个能量组内平均股票，再让每个日期等权；同时保留 pooled 与证券等权结果，避免成分数量和长历史股票支配结论。",
            "信号在收盘后确认，只从下一交易日真实Open开始；若下一日没有真实Open，该证券日直接排除，不向后顺延。",
            "Rank IC每天横截面计算，至少需要10只可比较证券；置信区间按日度诊断差的自然月区块重采样，保留时间相关性。",
            "终止交易样本按最后真实Close截短，证券与QQQ使用完全相同的实际区间；终止截短、合成目标日和排除数均写入机器工件。",
            "成分区间与研究窗相交、但供应商文件在研究窗内没有任何价格的身份只进入显式排除清单，不生成事件；本次该类身份数由共享manifest逐项记录。",
            "历史成分与个股行情仍为pending_review候选包。即使某项冻结判据显示支持，也只能进入下一次预先冻结、考虑换手与共享资金的组合验证。",
        ],
        figures=[
            ReportFigure("bucket-energy-factor", "能量分档与未来相对QQQ收益", bucket_curve_figure(blocks), "generic"),
            ReportFigure("rank-ic-energy-factor", "能量的逐日横截面排序能力", rank_ic_figure(primary), "generic"),
            ReportFigure("spreads-energy-factor", "门禁、顶端分档与方向差的置信区间", spread_figure(blocks), "generic"),
            ReportFigure("era-energy-factor", "未来20日能量分档的时期稳定性", era_bucket_figure(primary), "generic"),
            ReportFigure("path-energy-factor", "未来20日路径波动与样本量", path_and_sample_figure(primary), "generic"),
        ],
        experiment=context.config, run_id=args.run_id, template_id=context.config["reporting"]["template_id"],
    )
    (run_root / "report.html").write_text(report, encoding="utf-8")
    raw = ic_summary[ic_summary["factor_variant"].eq("RAW_SCORE")].set_index("horizon")
    (run_root / "report.md").write_text(
        "# Nasdaq-100 Strategy1 能量因子解剖\n\n"
        "## 冻结判定\n\n"
        f"- 原始能量横截面排序：{'支持' if decision['raw_score_ranking_supported'] else '不支持'}。\n"
        f"- 严格90%门禁：{'支持' if decision['high_energy_gate_supported'] else '不支持'}。\n"
        f"- 高能上升相对高能下降：{'支持' if decision['high_energy_direction_supported'] else '不支持'}。\n"
        f"- 原始能量10/20/60日平均逐日Rank IC：{raw.loc[10, 'mean_rank_ic']:+.5f} / {raw.loc[20, 'mean_rank_ic']:+.5f} / {raw.loc[60, 'mean_rank_ic']:+.5f}。\n\n"
        "## 边界\n\n每条观察是独立归一化事件，不是共享资本组合，故不报告CAGR。历史成分与个股价格为pending_review候选数据。\n",
        encoding="utf-8",
    )
    tracked = [
        "backtest/quantkit/nasdaq100_strategy1_energy_factor.py", "backtest/quantkit/reporting.py",
        "backtest/quantkit/experiment.py", "backtest/scripts/run_nasdaq100_strategy1_energy_factor.py",
        "backtest/scripts/analyze_nasdaq100_strategy1_energy_factor.py",
        "backtest/scripts/finalize_nasdaq100_strategy1_energy_factor.py",
        "backtest/scripts/smoke_nasdaq100_strategy1_energy_factor_anatomy_report.mjs",
        "backtest/scripts/validate_run.py", "backtest/tests/strategies/rot/test_nasdaq100_strategy1_energy_factor.py",
        "backtest/requirements.lock", "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
    ]
    provenance = {
        "schema_version": 1, "experiment_id": context.config["experiment_id"], "run_id": args.run_id,
        "created_at_utc": summary["created_at_utc"],
        "software": {"python": platform.python_version(), "plotly": plotly.__version__}, "source_files": {},
    }
    for relative in tracked:
        path = WORKSPACE_ROOT / relative
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    shared_manifest = json.loads((run_root / "shared/manifest.json").read_text(encoding="utf-8"))
    for relative, expected_hash in shared_manifest["source_files"].items():
        path = WORKSPACE_ROOT / relative
        actual = sha256(path)
        if actual != expected_hash:
            raise AssertionError(f"source changed during run: {relative}")
        provenance["source_files"][relative] = {"bytes": path.stat().st_size, "sha256": actual}
    (run_root / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (run_root / "README.md").write_text(
        f"# Run {args.run_id}\n\nFrozen full-history Strategy1 energy-factor event study.\n\n"
        "- `report.html` / `report.pdf`: v5 interactive and printable report.\n"
        "- `analysis/`: cross-cost factor summaries.\n"
        f"- `{SYMBOL}/`: immutable 0/5 bps event and diagnostic blocks.\n"
        "- `shared/`: full frozen score surface, dense price panel, source hashes and diagnostics.\n",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'}; decision={decision}")


if __name__ == "__main__":
    main()
