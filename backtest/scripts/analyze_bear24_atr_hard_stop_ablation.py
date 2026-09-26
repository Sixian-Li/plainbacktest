#!/usr/bin/env python3
"""Analyze and render the formal bear24 ATR hard-stop policy ablation."""

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

from quantkit.bear_atr_hard_stop import (
    POLICIES,
    POLICY_ATR_PRICE,
    POLICY_ATR_SMA,
    POLICY_HOLD,
    POLICY_SMA,
)
from quantkit.experiment import block_root, load_experiment, load_run, sha256
from quantkit.paths import BACKTEST_ROOT
from quantkit.reporting import ReportFigure, render_interactive_report
from scripts.run_bear24_atr_hard_stop_ablation import (
    EXPECTED_CORE12,
    EXPECTED_GROUPS,
    EXPECTED_NEAR8,
    EXPECTED_RETAIL4,
    EXPECTED_UNIVERSE,
    SYMBOL_BLOCK,
)


WORKSPACE_ROOT = BACKTEST_ROOT.parent
DEFAULT_EXPERIMENT = BACKTEST_ROOT / (
    "experiments/TIM/"
    "TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation"
)
TARGET_ORDER = (*EXPECTED_UNIVERSE, *EXPECTED_GROUPS)
POLICY_LABELS = {
    POLICY_HOLD: "A 不择时/不止损",
    POLICY_SMA: "B 完整SMA200",
    POLICY_ATR_PRICE: "C ATR止损+价格线回买",
    POLICY_ATR_SMA: "D ATR止损+SMA回买",
}
SCOPE_LABELS = {
    "minor": "总小熊",
    "major": "总大熊",
    "all": "总熊",
    "all_ex_2000_2002": "总熊（不包含2000–2002）",
}


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in (
        "date", "start", "end", "entry_execution_date", "exit_execution_date",
        "signal_date", "entry_date", "exit_date",
    ):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column])
    return frame


def load_blocks(context: Any, run_id: str) -> dict[float, dict[str, Any]]:
    blocks: dict[float, dict[str, Any]] = {}
    for cost in context.config["cost_scenarios_bps_per_side"]:
        cost_value = float(cost)
        root = block_root(context, run_id, SYMBOL_BLOCK, cost_value)
        if not (root / "manifest.json").is_file():
            raise FileNotFoundError(f"missing completed block: {root}")
        blocks[cost_value] = {
            "root": root,
            "metrics": read_csv(root / "metrics.csv"),
            "interval_returns": read_csv(root / "interval_returns.csv"),
            "scope_summary": read_csv(root / "scope_summary.csv"),
            "case_index": read_csv(root / "case_index.csv"),
            "date_index": read_csv(root / "date_index.csv"),
            "daily": np.load(root / "daily_state.npz"),
        }
    return blocks


def policy_effectiveness(
    five_metrics: pd.DataFrame,
    five_scopes: pd.DataFrame,
    zero_metrics: pd.DataFrame,
    target: str,
) -> list[dict[str, Any]]:
    """Apply the exact predeclared B-A, C-A, and D-C gates."""

    metrics5 = five_metrics[five_metrics["target"] == target].set_index("policy_id")
    metrics0 = zero_metrics[zero_metrics["target"] == target].set_index("policy_id")
    scopes = five_scopes[
        (five_scopes["target"] == target)
        & (five_scopes["scope"].isin(["all", "all_ex_2000_2002"]))
    ].pivot(index="policy_id", columns="scope", values="compound_return")
    rows: list[dict[str, Any]] = []
    for comparison, child, parent in (
        ("B-A", POLICY_SMA, POLICY_HOLD),
        ("C-A", POLICY_ATR_PRICE, POLICY_HOLD),
        ("D-C", POLICY_ATR_SMA, POLICY_ATR_PRICE),
    ):
        dd = float(metrics5.loc[child, "max_drawdown_pct"] - metrics5.loc[parent, "max_drawdown_pct"])
        zero_dd = float(metrics0.loc[child, "max_drawdown_pct"] - metrics0.loc[parent, "max_drawdown_pct"])
        worst = float(
            metrics5.loc[child, "all_bear_worst_interval_return_pct"]
            - metrics5.loc[parent, "all_bear_worst_interval_return_pct"]
        )
        ex_diff = float(
            (scopes.loc[child, "all_ex_2000_2002"] - scopes.loc[parent, "all_ex_2000_2002"])
            * 100.0
        )
        all_diff = float((scopes.loc[child, "all"] - scopes.loc[parent, "all"]) * 100.0)
        if comparison in {"B-A", "C-A"}:
            effective = dd >= 5.0 and worst >= 0.0 and ex_diff >= -5.0 and zero_dd >= 0.0
            gate = "回撤改善≥5pp、最差单段不恶化、剔除2000收益差≥-5pp、0bps方向不反转"
        else:
            effective = (
                (dd >= -2.0 and ex_diff >= 5.0)
                or (ex_diff >= -5.0 and dd >= 2.0)
            )
            gate = "D-C满足回撤不差2pp且收益多5pp，或收益不差5pp且回撤改善2pp"
        rows.append(
            {
                "target": target,
                "comparison": comparison,
                "child_policy": child,
                "parent_policy": parent,
                "max_drawdown_improvement_pp": dd,
                "zero_bps_max_drawdown_improvement_pp": zero_dd,
                "worst_interval_improvement_pp": worst,
                "all_bear_return_improvement_pp": all_diff,
                "ex_2000_return_improvement_pp": ex_diff,
                "effective": bool(effective),
                "gate": gate,
            }
        )
    return rows


def return_matrix(
    interval_returns: pd.DataFrame,
    scope_summary: pd.DataFrame,
    target: str,
) -> tuple[list[str], np.ndarray]:
    own_intervals = interval_returns[interval_returns["target"] == target]
    own_scopes = scope_summary[scope_summary["target"] == target]
    baseline = own_intervals[own_intervals["policy_id"] == POLICY_HOLD].sort_values("ordinal")
    if len(baseline) != 12:
        raise ValueError(f"{target} must contain 12 bear intervals")
    interval_ids = baseline["interval_id"].astype(str).tolist()
    labels = [
        f"{row.label} · {pd.Timestamp(row.start).date()}～{pd.Timestamp(row.end).date()}"
        for row in baseline.itertuples(index=False)
    ]
    labels.extend(SCOPE_LABELS[scope] for scope in ("minor", "major", "all", "all_ex_2000_2002"))
    values = np.full((16, 4), np.nan, dtype=float)
    for column, policy in enumerate(POLICIES):
        intervals_by_id = own_intervals[own_intervals["policy_id"] == policy].set_index("interval_id")
        scopes_by_id = own_scopes[own_scopes["policy_id"] == policy].set_index("scope")
        for row, interval_id in enumerate(interval_ids):
            values[row, column] = float(intervals_by_id.loc[interval_id, "total_return"]) * 100.0
        for row, scope in enumerate(("minor", "major", "all", "all_ex_2000_2002"), start=12):
            values[row, column] = float(scopes_by_id.loc[scope, "compound_return"]) * 100.0
    return labels, values


def cell_style(value: float, scale: float) -> str:
    alpha = min(abs(value) / max(scale, 1e-12), 1.0) * 0.30 + 0.04
    color = f"rgba(22,163,74,{alpha:.3f})" if value >= 0 else f"rgba(220,38,38,{alpha:.3f})"
    return f' style="background:{color}"'


def matrix_table(
    interval_returns: pd.DataFrame,
    scope_summary: pd.DataFrame,
    target: str,
    *,
    open_by_default: bool,
) -> str:
    labels, values = return_matrix(interval_returns, scope_summary, target)
    scale = float(np.nanpercentile(np.abs(values), 90)) or 1.0
    rows: list[str] = []
    for index, label in enumerate(labels):
        cells = "".join(
            f"<td{cell_style(float(values[index, column]), scale)}>{values[index, column]:+.2f}%</td>"
            for column in range(4)
        )
        row_class = ' class="aggregate-row"' if index >= 12 else ""
        rows.append(f"<tr{row_class}><th>{html.escape(label)}</th>{cells}</tr>")
    open_attr = " open" if open_by_default else ""
    return (
        f'<details class="target-matrix"{open_attr}><summary>{html.escape(target)} · 16行×4政策</summary>'
        '<div class="table-wrap"><table class="return-matrix"><thead><tr><th>熊市 / 汇总</th>'
        + "".join(f"<th>{html.escape(POLICY_LABELS[policy])}</th>" for policy in POLICIES)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div></details>"
    )


def group_overview(metrics: pd.DataFrame, scopes: pd.DataFrame) -> str:
    rows: list[str] = []
    for target in EXPECTED_GROUPS:
        target_metrics = metrics[metrics["target"] == target].set_index("policy_id")
        target_scopes = scopes[scopes["target"] == target].pivot(
            index="policy_id", columns="scope", values="compound_return"
        )
        for policy in POLICIES:
            rows.append(
                "<tr>"
                f"<td>{html.escape(target)}</td><td>{html.escape(POLICY_LABELS[policy])}</td>"
                f"<td>{target_scopes.loc[policy, 'major'] * 100:+.2f}%</td>"
                f"<td>{target_scopes.loc[policy, 'minor'] * 100:+.2f}%</td>"
                f"<td>{target_scopes.loc[policy, 'all'] * 100:+.2f}%</td>"
                f"<td>{target_scopes.loc[policy, 'all_ex_2000_2002'] * 100:+.2f}%</td>"
                f"<td>{target_metrics.loc[policy, 'max_drawdown_pct']:+.2f}%</td>"
                f"<td>{int(target_metrics.loc[policy, 'hard_stop_count'])}</td>"
                "</tr>"
            )
    return (
        '<section class="matrix-section"><h2>三个分组总体表现（5 bps）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>分组</th><th>政策</th><th>总大熊</th>'
        '<th>总小熊</th><th>总熊</th><th>不包含2000熊市</th><th>最大回撤</th><th>硬止损次数</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></section>"
    )


def effectiveness_table(rows: pd.DataFrame) -> str:
    group = rows[rows["target"].isin(EXPECTED_GROUPS)]
    body: list[str] = []
    for row in group.itertuples(index=False):
        body.append(
            "<tr>"
            f"<td>{html.escape(row.target)}</td><td>{html.escape(row.comparison)}</td>"
            f"<td>{row.max_drawdown_improvement_pp:+.2f}pp</td>"
            f"<td>{row.worst_interval_improvement_pp:+.2f}pp</td>"
            f"<td>{row.ex_2000_return_improvement_pp:+.2f}pp</td>"
            f"<td>{'通过' if row.effective else '未通过'}</td>"
            "</tr>"
        )
    return (
        '<section class="matrix-section"><h2>冻结有效性门槛：分组结果</h2>'
        '<div class="table-wrap"><table><thead><tr><th>分组</th><th>比较</th><th>回撤改善</th>'
        '<th>最差单段改善</th><th>不含2000收益改善</th><th>判定</th></tr></thead><tbody>'
        + "".join(body)
        + "</tbody></table></div></section>"
    )


def performance_figure(block: dict[str, Any]) -> go.Figure:
    cases = block["case_index"]
    dates = pd.to_datetime(block["date_index"]["date"])
    equity = block["daily"]["equity"]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.06,
    )
    colors = {
        POLICY_HOLD: "#64748b", POLICY_SMA: "#2563eb",
        POLICY_ATR_PRICE: "#059669", POLICY_ATR_SMA: "#7c3aed",
    }
    for target in EXPECTED_GROUPS:
        own = cases[cases["target"] == target]
        for row in own.itertuples(index=False):
            values = equity[int(row.case_index)]
            normalized = values / float(values[0]) * 100.0
            drawdown = (normalized / np.maximum.accumulate(normalized) - 1.0) * 100.0
            is_benchmark = target == "GROUP_CORE12" and str(row.policy_id) == POLICY_HOLD
            figure.add_trace(
                go.Scatter(
                    x=dates,
                    y=normalized,
                    mode="lines",
                    name=f"{target} · {POLICY_LABELS[str(row.policy_id)]}",
                    legendgroup=target,
                    line={"color": colors[str(row.policy_id)], "width": 2.2},
                    meta={
                        "series_key": str(row.case_id), "panel": "equity",
                        "is_benchmark": is_benchmark,
                    },
                    hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}<extra>%{fullData.name}</extra>",
                ),
                row=1,
                col=1,
            )
            figure.add_trace(
                go.Scatter(
                    x=dates,
                    y=drawdown,
                    mode="lines",
                    name=f"{target} · {POLICY_LABELS[str(row.policy_id)]}",
                    legendgroup=target,
                    showlegend=False,
                    line={"color": colors[str(row.policy_id)], "width": 1.7},
                    meta={
                        "series_key": str(row.case_id),
                        "panel": "drawdown",
                        "is_benchmark": is_benchmark,
                    },
                    hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}%<extra>%{fullData.name}</extra>",
                ),
                row=2,
                col=1,
            )
    figure.update_layout(
        template="plotly_white", height=720, hovermode="x unified",
        legend={"orientation": "h", "y": 1.12}, uirevision="bear24-group-policy-v2",
    )
    figure.update_yaxes(title_text="净值（起点100）", row=1, col=1)
    figure.update_yaxes(title_text="回撤", ticksuffix="%", row=2, col=1)
    figure.update_xaxes(title_text="日期", row=2, col=1)
    return figure


def heatmap_figure(scopes: pd.DataFrame, scope: str, title: str) -> go.Figure:
    pivot = scopes[scopes["scope"] == scope].pivot(
        index="target", columns="policy_id", values="compound_return"
    ).reindex(index=list(TARGET_ORDER), columns=list(POLICIES)) * 100.0
    figure = go.Figure(
        go.Heatmap(
            z=pivot.to_numpy(dtype=float), x=[POLICY_LABELS[value] for value in POLICIES],
            y=pivot.index, colorscale="RdYlGn", zmid=0,
            text=np.vectorize(lambda value: f"{value:+.1f}%")(pivot.to_numpy(dtype=float)),
            texttemplate="%{text}", hovertemplate="%{y}<br>%{x}<br>%{z:+.2f}%<extra></extra>",
            colorbar={"title": "收益%"},
        )
    )
    figure.update_layout(template="plotly_white", height=900, title=title, uirevision=f"bear24-{scope}")
    return figure


def markdown_group_table(metrics: pd.DataFrame, scopes: pd.DataFrame) -> str:
    lines = [
        "| 分组 | 政策 | 总大熊 | 总小熊 | 总熊 | 不包含2000熊市 | 最大回撤 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for target in EXPECTED_GROUPS:
        own_metrics = metrics[metrics["target"] == target].set_index("policy_id")
        own_scopes = scopes[scopes["target"] == target].pivot(
            index="policy_id", columns="scope", values="compound_return"
        )
        for policy in POLICIES:
            lines.append(
                f"| {target} | {POLICY_LABELS[policy]} | {own_scopes.loc[policy, 'major'] * 100:+.2f}% | "
                f"{own_scopes.loc[policy, 'minor'] * 100:+.2f}% | {own_scopes.loc[policy, 'all'] * 100:+.2f}% | "
                f"{own_scopes.loc[policy, 'all_ex_2000_2002'] * 100:+.2f}% | "
                f"{own_metrics.loc[policy, 'max_drawdown_pct']:+.2f}% |"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    context = load_experiment(args.experiment)
    record = load_run(context, args.run_id)
    if record.get("status") != "running" or record.get("analysis", {}).get("status") != "pending":
        raise RuntimeError("analysis requires a running run with pending analysis")
    blocks = load_blocks(context, args.run_id)
    if set(blocks) != {0.0, 5.0}:
        raise ValueError("formal report requires exactly 0 and 5 bps")
    five = blocks[5.0]
    zero = blocks[0.0]
    effect_rows = pd.DataFrame(
        [
            row
            for target in TARGET_ORDER
            for row in policy_effectiveness(
                five["metrics"], five["scope_summary"], zero["metrics"], target
            )
        ]
    )
    group_counts = (
        effect_rows[effect_rows["target"].isin(EXPECTED_GROUPS)]
        .groupby("comparison")["effective"].sum().astype(int).to_dict()
    )

    run_root = context.run_root(args.run_id)
    analysis_root = run_root / "analysis"
    analysis_root.mkdir(exist_ok=True)
    effect_rows.to_csv(analysis_root / "policy_effectiveness.csv", index=False, lineterminator="\n")
    matrices: dict[str, Any] = {}
    for target in TARGET_ORDER:
        labels, values = return_matrix(five["interval_returns"], five["scope_summary"], target)
        matrices[target] = {
            "rows": labels,
            "columns": [POLICY_LABELS[policy] for policy in POLICIES],
            "values_pct": values.tolist(),
        }
    summary = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "formal_cost_bps": 5.0,
        "group_5bps": {
            "policy_effectiveness": json.loads(
                effect_rows[effect_rows["target"].isin(EXPECTED_GROUPS)].to_json(
                    orient="records"
                )
            ),
            "effective_group_count_by_comparison": group_counts,
        },
        "return_matrices_5bps": matrices,
        "direct_promotion_allowed": False,
        "direct_promotion_blocker": "24个候选和12段熊市边界均含事后选择",
    }
    (analysis_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    group_html = group_overview(five["metrics"], five["scope_summary"])
    effect_html = effectiveness_table(effect_rows)
    sections = [
        '<section class="matrix-section"><h2>核心12：每个标的独立表</h2>',
        *(matrix_table(five["interval_returns"], five["scope_summary"], target, open_by_default=False) for target in EXPECTED_CORE12),
        "</section>",
        '<section class="matrix-section"><h2>近核心8：每个标的独立表</h2>',
        *(matrix_table(five["interval_returns"], five["scope_summary"], target, open_by_default=False) for target in EXPECTED_NEAR8),
        "</section>",
        '<section class="matrix-section"><h2>零售4：每个标的独立表</h2>',
        *(matrix_table(five["interval_returns"], five["scope_summary"], target, open_by_default=False) for target in EXPECTED_RETAIL4),
        "</section>",
        '<section class="matrix-section"><h2>三个分组：独立固定袖套表</h2>',
        *(matrix_table(five["interval_returns"], five["scope_summary"], target, open_by_default=True) for target in EXPECTED_GROUPS),
        "</section>",
    ]
    summary_html = group_html + effect_html + "".join(sections)
    subtitle = (
        f"冻结门槛通过的分组数：B-A {group_counts.get('B-A', 0)}/3，"
        f"C-A {group_counts.get('C-A', 0)}/3，D-C {group_counts.get('D-C', 0)}/3；"
        "每张表都含不包含2000–2002熊市的复合收益。"
    )
    notes = [
        "12段熊市峰谷边界和24个候选均由全样本事后确定，本实验只比较固定机制，不能直接晋级模拟盘。",
        "24个单标的分别用100%独立账户；三个分组在每段起点对已完成SMA200和ATR20预热的成员重新等权。",
        "分组内每只股票拥有固定资金袖套；退出后的现金不会分配给幸存股票，因此止损不会隐含加仓其他标的。",
        "ATR硬止损按入场信号日已完成的Wilder ATR20和下一Open真实成交价冻结，距离限制为12%至20%，不是峰值移动止损。",
        "硬止损在入场日立即生效；跳空跌破按Open，盘中Low触线按固定线，之后再计单边成本。",
        "C在止损后的更晚收盘重新高于原止损线时回买；D要先回到SMA200+3%之下武装，再严格上穿。",
        "单边5bps是正式结果，0bps仅检查成本方向；完整参数保存在折叠附录和experiment_snapshot.json。",
        "复权OHLC只提供内部一致的总回报近似，不是原始价格、现金分红、拆股事件和真实止损滑点的精确回放。",
    ]
    figures = [
        ReportFigure(
            div_id="performance-bear24_atr_ablation",
            title="核心12、近核心8、零售4：四政策连续事件净值（5 bps）",
            figure=performance_figure(five),
            kind="performance",
        ),
        ReportFigure(
            div_id="heatmap-bear24-all",
            title="27个目标：全部熊市四政策收益",
            figure=heatmap_figure(five["scope_summary"], "all", "全部12段熊市复合收益"),
            kind="generic",
        ),
        ReportFigure(
            div_id="heatmap-bear24-ex2000",
            title="27个目标：不包含2000熊市的四政策收益",
            figure=heatmap_figure(
                five["scope_summary"], "all_ex_2000_2002", "剔除2000–2002后的11段复合收益"
            ),
            kind="generic",
        ),
    ]
    report = render_interactive_report(
        title="24标的 ATR 固定硬止损四政策消融",
        heading="24个单标的 + 核心12 / 近核心8 / 零售4三个分组",
        subtitle=subtitle,
        summary_html=summary_html,
        notes=notes,
        figures=figures,
        experiment=context.config,
        run_id=args.run_id,
        template_id=context.config["reporting"]["template_id"],
    )
    css = """
<style>
.matrix-section{margin:28px 0}.target-matrix{margin:12px 0;border:1px solid #dbe3ec;border-radius:12px;padding:10px 14px;background:#fff}
.target-matrix summary{cursor:pointer;font-weight:700}.return-matrix{min-width:920px}.return-matrix th:first-child{min-width:270px;text-align:left}
.return-matrix td{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}.aggregate-row th,.aggregate-row td{font-weight:700;border-top:2px solid #94a3b8}
@media print{.target-matrix{break-inside:avoid}.target-matrix:not([open]){display:none}}
</style>
"""
    report = report.replace("</head>", css + "</head>")
    downloads = (
        '<section class="chart"><h2>结果下载</h2><p>'
        '<a download href="BEAR24_ATR_ABLATION/cost_5bps/interval_returns.csv">5 bps逐段收益</a> · '
        '<a download href="BEAR24_ATR_ABLATION/cost_5bps/scope_summary.csv">5 bps大小熊与不含2000汇总</a> · '
        '<a download href="BEAR24_ATR_ABLATION/cost_5bps/metrics.csv">5 bps全部指标</a> · '
        '<a download href="BEAR24_ATR_ABLATION/cost_5bps/orders.csv">5 bps订单</a> · '
        '<a download href="analysis/policy_effectiveness.csv">冻结有效性判定</a> · '
        '<a download href="BEAR24_ATR_ABLATION/cost_0bps/scope_summary.csv">0 bps敏感性</a>'
        "</p></section>"
    )
    report = report.replace("</main>", downloads + "</main>")
    (run_root / "report.html").write_text(report, encoding="utf-8")

    (run_root / "report.md").write_text(
        f"""# 24标的 ATR 固定硬止损四政策消融

## 结论门禁

- B-A（完整SMA相对不择时）通过冻结门槛的分组：{group_counts.get('B-A', 0)}/3。
- C-A（ATR固定硬止损相对不止损）通过冻结门槛的分组：{group_counts.get('C-A', 0)}/3。
- D-C（SMA止损后回买相对价格线回买）通过冻结门槛的分组：{group_counts.get('D-C', 0)}/3。
- 每个单标的和三个分组都保存12段逐段、总小熊、总大熊、总熊与不包含2000–2002熊市的结果。
- 名单与熊市边界均含事后信息，任何历史改善都不能直接晋级模拟盘或实盘。

## 三个分组（5 bps）

{markdown_group_table(five['metrics'], five['scope_summary'])}

## 规则边界

- A不择时也不止损；B使用SMA200上下3%完整买卖；C/D只使用入场ATR固定硬止损，D仅把SMA200+3%用于止损后的重新买入。
- 固定止损距离为3倍Wilder ATR20除以实际入场价，并限制在12%至20%；不是峰值移动止损。
- 分组使用独立固定袖套，退出资金留现金，不加仓其他成员；每段熊市开始重新等权。
- Close信号下一共同Open成交；硬止损跳空按Open、日内触线按固定线；单边5bps为主，0bps为敏感性。
""",
        encoding="utf-8",
    )

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    tracked = [
        "backtest/quantkit/bear_atr_hard_stop.py",
        "backtest/quantkit/execution.py",
        "backtest/quantkit/metrics.py",
        "backtest/quantkit/experiment.py",
        "backtest/quantkit/reporting.py",
        "backtest/scripts/run_bear24_atr_hard_stop_ablation.py",
        "backtest/scripts/analyze_bear24_atr_hard_stop_ablation.py",
        "backtest/scripts/finalize_bear24_atr_hard_stop_ablation.py",
        "backtest/scripts/smoke_report_ui.mjs",
        "backtest/scripts/print_html_pdf.mjs",
        "backtest/scripts/validate_run.py",
        "backtest/tests/strategies/tim/test_bear_atr_hard_stop.py",
        "backtest/experiments/TIM/TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json",
        "backtest/requirements.lock",
        "backtest/report_templates/interactive_research_v5/page.html",
        "backtest/report_templates/interactive_research_v5/styles.css",
        "backtest/report_templates/interactive_research_v5/interactions.js",
        "research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json",
    ]
    provenance = {
        "schema_version": 1,
        "experiment_id": context.config["experiment_id"],
        "run_id": args.run_id,
        "created_at_utc": created_at,
        "software": {
            "python": platform.python_version(), "lib_pybroker": "1.2.12",
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

This run compares four frozen policies for 24 individual assets and three fixed-sleeve groups over 12 hindsight bear intervals.

- `report.html` / `report.md`: v5 strategy-first report and all 27 return tables.
- `report.pdf`: Chrome-printed strategy-first report with the technical appendix hidden.
- `analysis/summary.json`: machine-readable group effectiveness and all 27 matrices.
- `analysis/policy_effectiveness.csv`: predeclared B-A, C-A, and D-C gate calculations.
- `BEAR24_ATR_ABLATION/cost_0bps/` and `cost_5bps/`: all cases, ledgers, daily state, positions, hashes, and interval summaries.
- `provenance.json`: exact source and market-data hashes.
- `validation.json`: full test, audit, browser, PDF, and lifecycle evidence.
""",
        encoding="utf-8",
    )
    print(f"Wrote {run_root / 'report.html'}; group gate counts={group_counts}")


if __name__ == "__main__":
    main()
