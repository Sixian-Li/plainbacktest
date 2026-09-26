"""Reusable builders for self-contained interactive research reports."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import plotly.graph_objects as go
from plotly import io as pio


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_ID = "interactive_research_v5"
TEMPLATE_ROOT = BACKTEST_ROOT / "report_templates"


@dataclass(frozen=True)
class ReportFigure:
    """One Plotly figure and the controls attached to it."""

    div_id: str
    title: str
    figure: go.Figure
    kind: str


def figure_html(figure: go.Figure, *, include_plotly: bool, div_id: str) -> str:
    # Plotly 6 may encode NumPy arrays as ``{dtype, bdata}``. Plotly itself can
    # render those objects, but our browser controls intentionally read x/y and
    # OHLC arrays. Override those fields in an unvalidated figure dict so the
    # graph-object validators do not immediately convert the lists back to NumPy.
    figure_dict = figure.to_plotly_json()
    for trace, trace_dict in zip(figure.data, figure_dict["data"], strict=True):
        meta = trace.meta if isinstance(trace.meta, dict) else {}
        fields: tuple[str, ...] = ()
        if trace.type == "candlestick":
            fields = ("x", "open", "high", "low", "close")
        elif meta.get("series_key"):
            fields = ("x", "y")
        for field in fields:
            values = getattr(trace, field, None)
            if values is None:
                continue
            plain = []
            for value in values:
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                elif hasattr(value, "item"):
                    value = value.item()
                plain.append(value)
            trace_dict[field] = plain
    return pio.to_html(
        figure_dict,
        full_html=False,
        include_plotlyjs="inline" if include_plotly else False,
        config={"scrollZoom": True, "responsive": True, "displaylogo": False},
        div_id=div_id,
        validate=False,
    )


def _market_series(figure: go.Figure) -> list[tuple[str, str, bool, str, str]]:
    seen: set[str] = set()
    series: list[tuple[str, str, bool, str, str]] = []
    for trace in figure.data:
        meta = trace.meta if isinstance(trace.meta, dict) else {}
        key = str(meta.get("series_key", ""))
        if not key or meta.get("panel") != "market" or key in seen:
            continue
        seen.add(key)
        series.append(
            (
                key,
                str(meta.get("label") or trace.name or key),
                trace.visible != "legendonly",
                str(meta.get("control_group") or "default"),
                str(meta.get("control_group_label") or "显示叠加线与标记"),
            )
        )
    return series


def market_controls(div_id: str, figure: go.Figure) -> str:
    groups: dict[str, dict[str, object]] = {}
    for key, label, checked, group, group_label in _market_series(figure):
        checked_attribute = " checked" if checked else ""
        groups.setdefault(group, {"label": group_label, "checkboxes": []})
        groups[group]["checkboxes"].append(  # type: ignore[union-attr]
            f'<label><input type="checkbox" data-series="{html.escape(key)}"'
            f' data-market-group="{html.escape(group)}"{checked_attribute}> '
            f'{html.escape(label)}</label>'
        )
    group_controls: list[str] = []
    for group, payload in groups.items():
        checkboxes = "".join(payload["checkboxes"])  # type: ignore[arg-type]
        label = html.escape(str(payload["label"]))
        picker = (
            f'<div class="series-picker market-overlays" data-market-group-panel="{html.escape(group)}">'
            f'<strong>{label}</strong>{checkboxes}</div>'
        )
        if group in {"long_sma"}:
            picker = f'<details><summary>{label}（展开逐条选择）</summary>{picker}</details>'
        group_controls.append(picker)
    overlay_controls = "".join(group_controls)
    return f"""<div class="market-controls" data-target="{html.escape(div_id)}">
{overlay_controls}
<div class="market-axis-actions">
<button type="button" data-action="visible-y">可见区间自动 Y 轴</button>
<button type="button" data-action="manual-y">手动 Y 轴</button>
<label>下限 <input type="number" data-role="y-min" step="any"></label>
<label>上限 <input type="number" data-role="y-max" step="any"></label>
<button type="button" data-action="apply-y">应用上下限</button>
<button type="button" data-action="full-range">恢复全部区间</button>
<span class="control-status" data-role="status">自动跟随可见 K 线</span>
</div>
</div>"""


def _performance_series(figure: go.Figure) -> tuple[list[tuple[str, str, bool]], str, float]:
    seen: set[str] = set()
    series: list[tuple[str, str, bool]] = []
    benchmark_key = ""
    benchmark_cost_bps = 0.0
    for trace in figure.data:
        meta = trace.meta if isinstance(trace.meta, dict) else {}
        key = str(meta.get("series_key", ""))
        if not key or meta.get("panel") != "equity" or key in seen:
            continue
        seen.add(key)
        series.append((key, str(meta.get("label") or trace.name or key), trace.visible != "legendonly"))
        if meta.get("is_benchmark"):
            benchmark_key = key
            benchmark_cost_bps = float(meta.get("cost_bps", 0.0))
    if not benchmark_key:
        raise ValueError("Performance figure must mark one equity trace meta.is_benchmark=true.")
    return series, benchmark_key, benchmark_cost_bps


def performance_controls(div_id: str, figure: go.Figure) -> str:
    series, benchmark_key, benchmark_cost_bps = _performance_series(figure)
    checkboxes = []
    for key, label, checked in series:
        checked_attribute = " checked" if checked else ""
        checkboxes.append(
            f'<label><input type="checkbox" data-series="{html.escape(key)}"{checked_attribute}> '
            f"{html.escape(label)}</label>"
        )
    checkboxes.append(
        '<label><input type="checkbox" data-series="dca_visible_range" disabled> '
        "区间等额定投</label>"
    )
    cost_label = f"{benchmark_cost_bps:g} bps"
    return f"""<div class="performance-controls" data-target="{html.escape(div_id)}" data-benchmark-key="{html.escape(benchmark_key)}" data-cost-bps="{benchmark_cost_bps:g}">
<div class="series-picker"><strong>显示曲线</strong>{''.join(checkboxes)}</div>
<div class="scenario-actions">
<button type="button" data-action="rebase-visible">区间左端对齐</button>
<button type="button" data-action="restore-equity">恢复原始净值</button>
<label>定投次数 <input type="number" data-role="dca-count" min="1" step="1" value="50"></label>
<button type="button" data-action="build-dca">生成 / 更新定投</button>
<span data-role="dca-budget">总预算：按区间左端 Buy &amp; Hold 净值</span>
</div>
<p class="control-help">对齐使用当前可见区间首个共同交易日，并以基准当日净值为共同起点；定投在该区间内按交易日等间隔投入，未投入资金保留现金且不计息，每笔采用 {html.escape(cost_label)} 买入成本。两项均为浏览器端情景分析，不改写正式回测结果。</p>
<span class="control-status" data-role="status">当前显示原始全历史净值</span>
</div>"""


def _strategy_text(value: object) -> str:
    if value in (None, "", [], {}):
        return "未配置"
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    if isinstance(value, Mapping):
        return "；".join(
            f"{_parameter_label(str(key))}：{_strategy_text(item)}"
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return "、".join(_strategy_text(item) for item in value)
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _strategy_value(value: object) -> str:
    return html.escape(_strategy_text(value))


_PARAMETER_LABELS = {
    "analysis_start": "回测开始日期",
    "analysis_end": "回测结束日期",
    "train_start": "训练期开始日期",
    "train_end": "训练期结束日期",
    "locked_test_start": "锁定测试开始日期",
    "locked_test_end": "锁定测试结束日期",
    "sma_window": "SMA 周期",
    "initial_cash": "初始资金",
    "initial_position": "初始仓位",
    "cost_bps": "单边交易成本（bps）",
    "cost_scenarios_bps_per_side": "单边交易成本情景（bps）",
    "combination_count": "参数组合数量",
    "combination_count_per_cost": "每个成本情景的参数组合数量",
    "random_seed": "随机种子",
    "universe": "标的池",
    "symbols": "标的",
    "windows": "研究窗口",
    "fixed_subperiods": "固定子区间",
    "search_space": "搜索范围",
    "search_ranges": "搜索范围",
    "selection": "选择规则",
    "robustness_subwindows": "稳健性子区间",
}

_PARAMETER_TOKENS = {
    "analysis": "回测",
    "start": "开始",
    "end": "结束",
    "train": "训练",
    "test": "测试",
    "locked": "锁定",
    "window": "周期",
    "windows": "周期",
    "period": "周期",
    "periods": "周期",
    "day": "天",
    "days": "天数",
    "buy": "买入",
    "sell": "卖出",
    "entry": "入场",
    "exit": "离场",
    "threshold": "阈值",
    "range": "范围",
    "ranges": "范围",
    "cost": "成本",
    "initial": "初始",
    "position": "仓位",
    "signal": "信号",
    "slope": "斜率",
    "momentum": "动量",
    "stop": "止损",
    "loss": "亏损",
    "reentry": "重新入场",
    "rebuy": "重新买入",
    "slow": "慢趋势",
    "fast": "快速",
    "negative": "下降",
    "positive": "上升",
    "count": "数量",
    "min": "最小",
    "max": "最大",
    "step": "步长",
    "pct": "%",
    "bps": "bps",
    "sma": "SMA",
    "rsi": "RSI",
    "stochrsi": "StochRSI",
}


def _parameter_label(key: str) -> str:
    direct = _PARAMETER_LABELS.get(key)
    if direct:
        return direct
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_")
    parts = [part for part in normalized.split("_") if part]
    return " ".join(_PARAMETER_TOKENS.get(part.lower(), part) for part in parts)


def _parameter_tree(value: object, *, path: str = "parameters") -> str:
    """Render exact parameters as a readable, collapsed HTML appendix."""

    if isinstance(value, Mapping):
        if not value:
            return '<p class="parameter-empty">未配置</p>'
        items = []
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}"
            label = html.escape(_parameter_label(key_text))
            field = html.escape(item_path, quote=True)
            if isinstance(item, Mapping) or (
                isinstance(item, (list, tuple))
                and any(isinstance(child, Mapping) for child in item)
            ):
                rendered = _parameter_tree(item, path=item_path)
                items.append(
                    f'<li class="parameter-group"><span class="parameter-name" '
                    f'data-parameter-key="{field}" title="配置字段：{field}">{label}</span>{rendered}</li>'
                )
            else:
                items.append(
                    f'<li><span class="parameter-name" data-parameter-key="{field}" '
                    f'title="配置字段：{field}">{label}</span>'
                    f'<span class="parameter-value">{_strategy_value(item)}</span></li>'
                )
        return f'<ul class="parameter-list">{"".join(items)}</ul>'
    if isinstance(value, (list, tuple)):
        if not value:
            return '<p class="parameter-empty">未配置</p>'
        items = [
            f'<li><span class="parameter-value">{_strategy_value(item)}</span></li>'
            if not isinstance(item, Mapping)
            else f'<li class="parameter-group">{_parameter_tree(item, path=f"{path}[{index}]")}</li>'
            for index, item in enumerate(value)
        ]
        return f'<ul class="parameter-list">{"".join(items)}</ul>'
    return f'<p class="parameter-value">{_strategy_value(value)}</p>'


def _financing_text(value: object) -> str:
    if str(value).strip().lower() in {"disabled", "none", "false", "no"}:
        return "不使用融资"
    return _strategy_value(value)


def strategy_definition_html(experiment: Mapping[str, Any], run_id: str) -> str:
    """Explain the exact strategy in reading order before any result."""

    strategy = experiment.get("strategy")
    if not isinstance(strategy, Mapping):
        raise ValueError("report experiment must contain a strategy object")
    required = (
        "name",
        "description",
        "buy_rule",
        "sell_rule",
        "signal_time",
        "execution_time",
    )
    missing = [key for key in required if not strategy.get(key)]
    if missing:
        raise ValueError(f"report strategy is missing required fields: {missing}")

    sizing = strategy.get("position_sizing", strategy.get("positioning"))
    plain_language = strategy.get("plain_language")
    if not isinstance(plain_language, Mapping):
        plain_language = {
            "summary": strategy.get("description"),
            "buy": strategy.get("buy_rule"),
            "sell": strategy.get("sell_rule"),
            "execution": (
                f"先在 {_strategy_text(strategy.get('signal_time'))} 判断是否出现信号；"
                f"触发后，在 {_strategy_text(strategy.get('execution_time'))} 成交。"
            ),
            "position": sizing,
        }
    identity = (
        f"实验 {_strategy_value(experiment.get('experiment_id'))} · "
        f"运行 {_strategy_value(run_id)} · "
        f"研究阶段 {_strategy_value(experiment.get('research', {}).get('stage'))} · "
        f"策略配置名 {_strategy_value(strategy.get('name'))}"
    )
    symbols = _strategy_value(experiment.get("symbols"))
    initial_position = _strategy_value(strategy.get("initial_position"))
    costs = _strategy_value(experiment.get("cost_scenarios_bps_per_side"))
    return f"""<section class="card strategy-definition" id="strategy-definition">
<div class="strategy-kicker">先看懂策略，再看结果</div>
<h2>这项策略怎么运行</h2>
<p class="strategy-description"><strong>交易对象是 {symbols}。</strong>{_strategy_value(plain_language.get("summary"))}</p>
<ol class="strategy-flow">
<li class="strategy-step strategy-entry"><span class="strategy-step-number">1</span><div><h3>什么时候买</h3><p>{_strategy_value(plain_language.get("buy"))}</p></div></li>
<li class="strategy-step strategy-exit"><span class="strategy-step-number">2</span><div><h3>什么时候卖</h3><p>{_strategy_value(plain_language.get("sell"))}</p></div></li>
<li class="strategy-step strategy-execution"><span class="strategy-step-number">3</span><div><h3>信号如何变成成交</h3><p>{_strategy_value(plain_language.get("execution"))}</p></div></li>
</ol>
<div class="strategy-assumptions">
<p><strong>仓位与资金：</strong>{_strategy_value(plain_language.get("position"))} 初始持仓为 {initial_position}，初始资金为 {_strategy_value(experiment.get("initial_cash"))}，{_financing_text(experiment.get("financing"))}。</p>
<p><strong>成本与比较：</strong>每次单边交易分别按 {costs} bps 计算；结果与 {_strategy_value(experiment.get("benchmark"))} 比较。</p>
</div>
<p class="strategy-parameter-reference">打印版只保留策略逻辑和交易假设；完整冻结参数以本次 run 的 <span>experiment_snapshot.json</span> 为准。</p>
<details class="strategy-parameters strategy-technical-details"><summary>网页中查看精确规则和完整冻结参数（打印版不展开）</summary>
<h3>精确策略定义</h3>
<ul class="parameter-list strategy-exact-rules">
<li><span class="parameter-name">策略定义</span><span class="parameter-value">{_strategy_value(strategy.get("description"))}</span></li>
<li><span class="parameter-name">精确买入规则</span><span class="parameter-value">{_strategy_value(strategy.get("buy_rule"))}</span></li>
<li><span class="parameter-name">精确卖出规则</span><span class="parameter-value">{_strategy_value(strategy.get("sell_rule"))}</span></li>
<li><span class="parameter-name">信号确认时点</span><span class="parameter-value">{_strategy_value(strategy.get("signal_time"))}</span></li>
<li><span class="parameter-name">成交时点</span><span class="parameter-value">{_strategy_value(strategy.get("execution_time"))}</span></li>
<li><span class="parameter-name">精确仓位规则</span><span class="parameter-value">{_strategy_value(sizing)}</span></li>
</ul>
<h3>完整冻结参数</h3>{_parameter_tree(experiment.get("parameters"))}</details>
<p class="strategy-identity">{identity}</p>
</section>"""


def render_interactive_report(
    *,
    title: str,
    heading: str,
    subtitle: str,
    summary_html: str,
    notes: Sequence[str],
    figures: Sequence[ReportFigure],
    experiment: Mapping[str, Any],
    run_id: str,
    template_id: str = DEFAULT_TEMPLATE_ID,
) -> str:
    """Render a self-contained report from a versioned project template."""

    template_dir = TEMPLATE_ROOT / template_id
    page = (template_dir / "page.html").read_text(encoding="utf-8")
    css = (template_dir / "styles.css").read_text(encoding="utf-8")
    javascript = (template_dir / "interactions.js").read_text(encoding="utf-8")
    if "{{STRATEGY}}" not in page:
        subtitle_anchor = "<p>{{SUBTITLE}}</p>"
        if subtitle_anchor not in page:
            raise RuntimeError("Report template has no strategy slot or subtitle injection point.")
        page = page.replace(subtitle_anchor, f"{subtitle_anchor}\n{{{{STRATEGY}}}}", 1)

    sections: list[str] = []
    nav_items = [
        '<a href="#strategy-definition">具体策略</a>',
        '<a href="#summary">概要</a>',
    ]
    for index, item in enumerate(figures):
        if item.kind == "market":
            controls = market_controls(item.div_id, item.figure)
        elif item.kind == "performance":
            controls = performance_controls(item.div_id, item.figure)
        else:
            controls = ""
        chart = figure_html(item.figure, include_plotly=index == 0, div_id=item.div_id)
        sections.append(
            f'<section class="chart" id="section-{html.escape(item.div_id)}">'
            f"<h2>{html.escape(item.title)}</h2>{controls}{chart}</section>"
        )
        nav_items.append(
            f'<a href="#section-{html.escape(item.div_id)}">{html.escape(item.title)}</a>'
        )

    replacements = {
        "{{LANG}}": "zh-CN",
        "{{TITLE}}": html.escape(title),
        "{{HEADING}}": html.escape(heading),
        "{{SUBTITLE}}": html.escape(subtitle),
        "{{TEMPLATE_ID}}": html.escape(template_id),
        "{{STYLES}}": css,
        "{{STRATEGY}}": strategy_definition_html(experiment, run_id),
        "{{NAVIGATION}}": "".join(nav_items),
        "{{SUMMARY}}": summary_html,
        "{{NOTES}}": "".join(f"<li>{html.escape(note)}</li>" for note in notes),
        "{{SECTIONS}}": "\n".join(sections),
        "{{INTERACTIONS}}": javascript,
    }
    for token, value in replacements.items():
        page = page.replace(token, value)
    unresolved = [token for token in replacements if token in page]
    if unresolved:
        raise RuntimeError(f"Unresolved report template tokens: {unresolved}")
    return page
