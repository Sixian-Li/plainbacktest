#!/usr/bin/env python3
"""Build the grouped registry, scorecard, lineage map, and strategy evolution histories."""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_ROOT = BACKTEST_ROOT / "experiments"
LINEAGE_PATH = EXPERIMENTS_ROOT / "lineage.json"
INDEX_PATH = EXPERIMENTS_ROOT / "index.md"
SCORECARD_PATH = EXPERIMENTS_ROOT / "scorecard.csv"
MAP_PATH = EXPERIMENTS_ROOT / "research_map.html"
EVOLUTION_PATH = EXPERIMENTS_ROOT / "strategy_evolution.md"
PROGRAM_EVOLUTION_ROOT = EXPERIMENTS_ROOT / "program_evolution"
EVENTS_PATH = EXPERIMENTS_ROOT / "research_events.jsonl"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_experiments(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    found: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(root.rglob("experiment.json")):
        if "runs" in path.relative_to(root).parts:
            continue
        config = read_json(path)
        experiment_id = str(config.get("experiment_id", "")).strip()
        if not experiment_id:
            raise ValueError(f"Missing experiment_id: {path}")
        if experiment_id in found:
            raise ValueError(f"Duplicate experiment_id: {experiment_id}")
        found[experiment_id] = (path, config)
    return found


def validate_lineage(
    lineage: dict[str, Any], experiments: dict[str, tuple[Path, dict[str, Any]]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    programs = {item["program_id"]: item for item in lineage.get("programs", [])}
    nodes: dict[str, dict[str, Any]] = {}
    codes: set[str] = set()
    for node in lineage.get("nodes", []):
        experiment_id = node["experiment_id"]
        if experiment_id in nodes:
            raise ValueError(f"Duplicate lineage node: {experiment_id}")
        if node.get("program_id") not in programs:
            raise ValueError(f"Unknown program for {experiment_id}: {node.get('program_id')}")
        display_code = str(node.get("display_code", "")).strip()
        if not display_code or display_code in codes:
            raise ValueError(f"Missing or duplicate display_code: {display_code!r}")
        created_on = str(node.get("created_on", "")).strip()
        try:
            date.fromisoformat(created_on)
        except ValueError as exc:
            raise ValueError(f"Invalid created_on for {experiment_id}: {created_on!r}") from exc
        config_path, config = experiments[experiment_id]
        program_code = str(programs[node["program_id"]]["code"])
        relative = config_path.relative_to(EXPERIMENTS_ROOT)
        expected_prefix = f"{display_code}__{created_on[2:]}__"
        if (
            len(relative.parts) != 3
            or relative.parts[0] != program_code
            or not config_path.parent.name.startswith(expected_prefix)
            or str(config.get("created_at_utc", ""))[:10] != created_on
        ):
            raise ValueError(
                f"Non-canonical experiment location for {experiment_id}: {relative}; "
                f"expected {program_code}/{expected_prefix}<slug>/experiment.json"
            )
        nodes[experiment_id] = node
        codes.add(display_code)
    missing_nodes = sorted(set(experiments) - set(nodes))
    stale_nodes = sorted(set(nodes) - set(experiments))
    if missing_nodes or stale_nodes:
        raise ValueError(f"Lineage mismatch; missing={missing_nodes}, stale={stale_nodes}")
    edge_keys: set[tuple[str, str]] = set()
    for edge in lineage.get("edges", []):
        if edge.get("from") not in nodes or edge.get("to") not in nodes:
            raise ValueError(f"Unknown lineage edge endpoint: {edge}")
        if edge.get("from") == edge.get("to"):
            raise ValueError(f"Self-referential lineage edge: {edge}")
        edge_key = (edge["from"], edge["to"])
        if edge_key in edge_keys:
            raise ValueError(f"Duplicate lineage edge: {edge_key}")
        edge_keys.add(edge_key)
        for field in ("relation", "rationale", "change_summary"):
            if not str(edge.get(field, "")).strip():
                raise ValueError(f"Lineage edge {edge_key} lacks {field}")
    return programs, nodes


def parse_registry_sections(text: str, experiment_ids: Iterable[str]) -> dict[str, str]:
    ids = list(experiment_ids)
    marked: dict[str, str] = {}
    for experiment_id in ids:
        pattern = re.compile(
            rf"<!-- EXPERIMENT:{re.escape(experiment_id)} -->\n(.*?)\n<!-- END_EXPERIMENT -->",
            re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            body = match.group(1)
            body = re.sub(r"^### .*\n", "", body, count=1)
            body = re.sub(r"^- Experiment ID：.*\n?", "", body, count=1)
            marked[experiment_id] = body.strip()
    if len(marked) == len(ids):
        return marked

    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not line.startswith(("## ", "### ")):
            continue
        matches = [experiment_id for experiment_id in ids if experiment_id in line]
        if len(matches) == 1:
            starts.append((index, matches[0]))
    sections: dict[str, str] = {}
    for position, (start, experiment_id) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        sections[experiment_id] = "\n".join(lines[start + 1 : end]).strip()
    return sections


def render_index(
    lineage: dict[str, Any],
    programs: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    sections: dict[str, str],
) -> str:
    missing = sorted(set(nodes) - set(sections))
    if missing:
        raise ValueError(f"Registry sections missing for: {missing}")
    program_order = sorted(programs.values(), key=lambda item: item.get("order", 999))
    program_links = " / ".join(
        f"[{program['code']}](program_evolution/{program['code']}.md)" for program in program_order
    )
    lines = [
        "# 回测实验登记册",
        "",
        "本文件按研究流派与演化版本集中登记正式实验；策略定义和不可覆盖的运行证据仍保存在各实验目录。",
        "",
        f"导航：[总策略演化史](strategy_evolution.md) · 分策略演化史：{program_links} · [研究谱系图](research_map.html) · [统一指标台账](scorecard.csv) · [机器谱系](lineage.json) · [事件日志](research_events.jsonl)",
        "",
    ]
    node_order = {item["experiment_id"]: index for index, item in enumerate(lineage["nodes"])}
    for program in program_order:
        lines.extend(
            [
                f"## {program['code']} · {program['name']}",
                "",
                program["description"],
                "",
            ]
        )
        program_nodes = sorted(
            (node for node in nodes.values() if node["program_id"] == program["program_id"]),
            key=lambda node: node_order[node["experiment_id"]],
        )
        for node in program_nodes:
            experiment_id = node["experiment_id"]
            lines.extend(
                [
                    f"<!-- EXPERIMENT:{experiment_id} -->",
                    f"### {node['display_code']} · {node['display_name']} · `{experiment_id}`",
                    f"- Experiment ID：`{experiment_id}`",
                    sections[experiment_id],
                    f"<!-- END_EXPERIMENT -->",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def walk_objects(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk_objects(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_objects(child, path + (str(index),))


def first_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def metric_row(path: tuple[str, ...], payload: dict[str, Any]) -> dict[str, Any] | None:
    cagr = first_number(payload, ("cagr_pct", "baseline_cagr_pct", "best_cagr_pct"))
    sharpe = first_number(payload, ("sharpe", "baseline_sharpe", "best_sharpe"))
    drawdown = first_number(payload, ("max_drawdown_pct", "baseline_max_drawdown_pct"))
    objective = str(payload.get("objective") or payload.get("metric") or "").lower()
    value = payload.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if objective in {"cagr", "cagr_pct"} and cagr is None:
            cagr = value
        elif objective in {"sharpe", "sharpe_ratio"} and sharpe is None:
            sharpe = value
    baseline_metric = payload.get("baseline_metric")
    if isinstance(baseline_metric, (int, float)) and not isinstance(baseline_metric, bool):
        if objective in {"cagr", "cagr_pct"} and cagr is None:
            cagr = baseline_metric
        elif objective in {"sharpe", "sharpe_ratio"} and sharpe is None:
            sharpe = baseline_metric
    if cagr is None and sharpe is None and drawdown is None:
        return None
    path_text = "/".join(path) or "root"
    case_id = payload.get("case_id") or payload.get("screen_case_id") or ""
    label = (
        payload.get("name")
        or payload.get("parameter_label")
        or payload.get("case_label")
        or payload.get("selection_reason")
        or case_id
        or path_text
    )
    role = "benchmark" if "benchmark" in path_text.lower() or str(case_id).lower() == "buy_hold" else "strategy"
    return {
        "metric_path": path_text,
        "role": role,
        "label": label,
        "case_id": case_id,
        "window_id": payload.get("window_id", ""),
        "symbol": payload.get("symbol", ""),
        "start": payload.get("start", ""),
        "end": payload.get("end", ""),
        "cost_bps": payload.get("cost_bps", ""),
        "total_return_pct": first_number(payload, ("total_return_pct", "baseline_total_return_pct")),
        "cagr_pct": cagr,
        "sharpe": sharpe,
        "max_drawdown_pct": drawdown,
        "exposure_pct": first_number(payload, ("exposure_pct", "average_gross_exposure_pct")),
        "order_count": first_number(payload, ("order_count",)),
    }


def summary_files(run_root: Path) -> list[Path]:
    preferred = run_root / "analysis/summary.json"
    if preferred.is_file():
        return [preferred]
    candidates = sorted(run_root.glob("*/cost_*bps/summary.json"))
    if candidates:
        return candidates
    return sorted(run_root.glob("*/cost_*bps/metrics.json"))


def declared_run_artifact(run_root: Path, relative_path: str) -> bool:
    """Return whether a tracked manifest declares an ignored run artifact.

    Large HTML/CSV outputs are intentionally absent from a fresh linked worktree.
    Catalog generation must therefore use the immutable artifact manifest rather
    than local file presence when deciding whether a validated report exists.
    """

    manifest_path = run_root / "artifact_manifest.json"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        if relative_path in manifest.get("artifacts", {}):
            return True

    # A linked worktree deliberately omits every other experiment's large run
    # directory.  The tracked scorecard is the compact integration proof for
    # those exact experiment/run/report triples; never infer a different run or
    # an arbitrary missing file from it.
    try:
        declared_report = str((run_root / relative_path).relative_to(EXPERIMENTS_ROOT))
    except ValueError:
        return False
    if relative_path != "report.html" or not SCORECARD_PATH.is_file():
        return False
    with SCORECARD_PATH.open(encoding="utf-8", newline="") as file:
        return any(
            row.get("run_id") == run_root.name
            and row.get("report") == declared_report
            for row in csv.DictReader(file)
        )


def parse_csv_number(value: str | None) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_cached_csv_number(value: str | None) -> float | int | None:
    """Parse a tracked scorecard value without changing its CSV spelling class.

    A sparse worktree often has no ignored summary artifact and must rebuild a
    row from the tracked scorecard itself.  Keeping ``100.0`` as a float and
    ``100`` as an integer makes that fallback byte-stable with the full primary
    workspace instead of reporting a false stale catalog.
    """

    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if any(marker in text.lower() for marker in (".", "e")):
        return number
    return int(number) if number.is_integer() else number


def csv_scorecard_rows(run_root: Path) -> list[dict[str, Any]]:
    """Normalize the older framework summary without rewriting its validated run."""
    path = run_root / "analysis/parameter_summary.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as file:
        for item in csv.DictReader(file):
            symbol = item.get("symbol", "")
            selection = item.get("selection", "")
            rows.append(
                {
                    "metric_path": f"analysis/parameter_summary.csv/{symbol}/{selection}",
                    "role": "strategy",
                    "label": selection,
                    "case_id": item.get("case_id", ""),
                    "window_id": "",
                    "symbol": symbol,
                    "start": "",
                    "end": "",
                    "cost_bps": parse_csv_number(item.get("cost_bps")),
                    "total_return_pct": parse_csv_number(item.get("total_return_pct")),
                    "cagr_pct": parse_csv_number(item.get("cagr_pct")),
                    "sharpe": parse_csv_number(item.get("sharpe")),
                    "max_drawdown_pct": parse_csv_number(item.get("max_drawdown_pct")),
                    "exposure_pct": parse_csv_number(item.get("exposure_pct")),
                    "order_count": parse_csv_number(item.get("order_count")),
                }
            )
    return rows


def cached_scorecard_rows(
    path: Path,
    *,
    experiment_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Reuse tracked normalized metrics when a legacy ignored CSV is absent.

    This fallback is intentionally scoped to the exact experiment and run. New
    or changed runs must still provide their own summary artifacts.
    """

    if not path.is_file():
        return []
    text_fields = (
        "metric_path",
        "role",
        "label",
        "case_id",
        "window_id",
        "symbol",
        "start",
        "end",
        "run_status",
        "report",
    )
    number_fields = (
        "cost_bps",
        "total_return_pct",
        "cagr_pct",
        "sharpe",
        "max_drawdown_pct",
        "exposure_pct",
        "order_count",
    )
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as file:
        for item in csv.DictReader(file):
            if item.get("experiment_id") != experiment_id or item.get("run_id") != run_id:
                continue
            row = {field: item.get(field, "") for field in text_fields}
            row.update(
                {
                    field: parse_cached_csv_number(item.get(field))
                    for field in number_fields
                }
            )
            rows.append(row)
    return rows


def build_scorecard_rows(
    lineage: dict[str, Any],
    programs: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    experiments: dict[str, tuple[Path, dict[str, Any]]],
) -> list[dict[str, Any]]:
    program_order = {item["program_id"]: item.get("order", 999) for item in programs.values()}
    node_order = {item["experiment_id"]: index for index, item in enumerate(lineage["nodes"])}
    rows: list[dict[str, Any]] = []
    for experiment_id, node in nodes.items():
        config_path, config = experiments[experiment_id]
        run_id = config.get("latest_validated_run_id") or config.get("active_run_id") or ""
        run_root = config_path.parent / "runs" / run_id if run_id else None
        run_record: dict[str, Any] = {}
        if run_root and (run_root / "run.json").is_file():
            run_record = read_json(run_root / "run.json")
        experiment_rows: list[dict[str, Any]] = []
        if run_root:
            files = summary_files(run_root)
            for source in files:
                source_prefix = "" if source == run_root / "analysis/summary.json" else str(source.relative_to(run_root))
                payload = read_json(source)
                for path, item in walk_objects(payload):
                    extracted = metric_row(path, item)
                    if not extracted:
                        continue
                    if source_prefix:
                        extracted["metric_path"] = f"{source_prefix}/{extracted['metric_path']}"
                    experiment_rows.append(extracted)
            if not experiment_rows:
                experiment_rows.extend(csv_scorecard_rows(run_root))
            if not experiment_rows:
                experiment_rows.extend(
                    cached_scorecard_rows(
                        SCORECARD_PATH,
                        experiment_id=experiment_id,
                        run_id=str(run_id),
                    )
                )
        if not experiment_rows:
            experiment_rows = [{
                "metric_path": "",
                "role": "",
                "label": "No normalized summary metric",
                "case_id": "",
                "window_id": "",
                "symbol": "",
                "start": "",
                "end": "",
                "cost_bps": "",
                "total_return_pct": None,
                "cagr_pct": None,
                "sharpe": None,
                "max_drawdown_pct": None,
                "exposure_pct": None,
                "order_count": None,
            }]
        report = ""
        if run_root and (
            (run_root / "report.html").is_file()
            or declared_run_artifact(run_root, "report.html")
        ):
            report = str((run_root / "report.html").relative_to(EXPERIMENTS_ROOT))
        for extracted in experiment_rows:
            if not extracted.get("symbol") and len(config.get("symbols", [])) == 1:
                extracted["symbol"] = config["symbols"][0]
            rows.append(
                {
                    "program_order": program_order[node["program_id"]],
                    "node_order": node_order[experiment_id],
                    "program": programs[node["program_id"]]["code"],
                    "display_code": node["display_code"],
                    "display_name": node["display_name"],
                    "experiment_id": experiment_id,
                    "experiment_status": config.get("status", ""),
                    "research_stage": config.get("research", {}).get("stage", ""),
                    "run_id": run_id,
                    "run_status": run_record.get("status", ""),
                    "report": report,
                    **extracted,
                }
            )
    return sorted(rows, key=lambda row: (row["program_order"], row["node_order"], row["metric_path"]))


SCORECARD_FIELDS = [
    "program",
    "display_code",
    "display_name",
    "experiment_id",
    "experiment_status",
    "research_stage",
    "run_id",
    "run_status",
    "symbol",
    "metric_path",
    "role",
    "label",
    "case_id",
    "window_id",
    "start",
    "end",
    "cost_bps",
    "total_return_pct",
    "cagr_pct",
    "sharpe",
    "max_drawdown_pct",
    "exposure_pct",
    "order_count",
    "report",
]


def render_scorecard(rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SCORECARD_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def choose_headline(node: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    chosen: dict[str, Any] | None = None
    selector = str(node.get("headline_metric_path", "")).strip()
    if selector:
        for row in rows:
            if row["metric_path"] == selector or row["metric_path"].endswith("/" + selector):
                chosen = row
                break
    if chosen is None:
        for row in rows:
            if row["role"] != "benchmark" and row["cagr_pct"] is not None and row["sharpe"] is not None:
                chosen = row
                break
    if chosen is None and rows:
        chosen = rows[0]
    if chosen is None:
        return None
    combined = dict(chosen)
    if combined["cagr_pct"] is None or combined["sharpe"] is None:
        for sibling in rows:
            if (
                sibling["label"] == combined["label"]
                and sibling["window_id"] == combined["window_id"]
                and sibling["symbol"] == combined["symbol"]
            ):
                if combined["cagr_pct"] is None and sibling["cagr_pct"] is not None:
                    combined["cagr_pct"] = sibling["cagr_pct"]
                if combined["sharpe"] is None and sibling["sharpe"] is not None:
                    combined["sharpe"] = sibling["sharpe"]
    return combined


def fmt_metric(value: Any, suffix: str = "") -> str:
    return "—" if value in (None, "") else f"{float(value):.3f}{suffix}"


def compact_json(value: Any, *, limit: int = 180) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return rendered.replace("`", "\\`")


def flatten_definition(value: Any, prefix: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key in sorted(value):
            flattened.update(flatten_definition(value[key], prefix + (str(key),)))
        return flattened
    return {".".join(prefix): value}


def definition_differences(parent: dict[str, Any], child: dict[str, Any]) -> list[str]:
    fields = ("symbols", "cost_scenarios_bps_per_side", "strategy", "parameters")
    parent_flat = flatten_definition({field: parent.get(field) for field in fields})
    child_flat = flatten_definition({field: child.get(field) for field in fields})
    changes: list[str] = []
    for key in sorted(set(parent_flat) | set(child_flat)):
        before = parent_flat.get(key, "<未设置>")
        after = child_flat.get(key, "<未设置>")
        if before != after:
            changes.append(f"`{key}`：`{compact_json(before)}` → `{compact_json(after)}`")
    return changes


def render_result(row: dict[str, Any] | None, *, link_prefix: str = "") -> str:
    if not row:
        return "尚无可归一化的代表结果。"
    context = [str(row.get("symbol") or "")]
    if row.get("window_id"):
        context.append(str(row["window_id"]))
    if row.get("cost_bps") not in (None, ""):
        context.append(f"{fmt_metric(row['cost_bps'])} bps/side")
    if row.get("case_id"):
        context.append(f"case `{row['case_id']}`")
    context_text = " · ".join(item for item in context if item)
    metrics = (
        f"CAGR {fmt_metric(row.get('cagr_pct'), '%')}；Sharpe {fmt_metric(row.get('sharpe'))}；"
        f"Max DD {fmt_metric(row.get('max_drawdown_pct'), '%')}"
    )
    run_text = f"run `{row.get('run_id') or '—'}`（{row.get('run_status') or 'unknown'}）"
    report = f"；[报告]({link_prefix}{row['report']})" if row.get("report") else ""
    return f"{context_text + '；' if context_text else ''}{metrics}；{run_text}{report}。"


def render_evolution(
    lineage: dict[str, Any],
    programs: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    experiments: dict[str, tuple[Path, dict[str, Any]]],
    rows: list[dict[str, Any]],
    *,
    selected_program_id: str | None = None,
) -> str:
    rows_by_experiment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_experiment.setdefault(row["experiment_id"], []).append(row)
    headlines = {
        experiment_id: choose_headline(node, rows_by_experiment.get(experiment_id, []))
        for experiment_id, node in nodes.items()
    }
    node_order = {item["experiment_id"]: index for index, item in enumerate(lineage["nodes"])}
    program_order = sorted(programs.values(), key=lambda item: item.get("order", 999))
    if selected_program_id is not None:
        if selected_program_id not in programs:
            raise ValueError(f"Unknown selected program: {selected_program_id}")
        program_order = [programs[selected_program_id]]
        selected_program = programs[selected_program_id]
        title = f"# {selected_program['code']} · {selected_program['name']} · 策略演化史"
        navigation = "导航：[总策略演化史](../strategy_evolution.md) · [实验登记册](../index.md) · [研究谱系图](../research_map.html)"
        link_prefix = "../"
    else:
        title = "# 策略演化史"
        program_links = " / ".join(
            f"[{program['code']}](program_evolution/{program['code']}.md)"
            for program in program_order
        )
        navigation = f"导航：分策略演化史 {program_links} · [实验登记册](index.md) · [研究谱系图](research_map.html)"
        link_prefix = ""
    lines = [
        title,
        "",
        "> 本文件由 `scripts.build_research_catalog` 自动生成，请勿手工修改。修改原因与策略变化只写入 `lineage.json`；参数差异来自父子 `experiment.json`，指标来自当前代表 run。",
        "",
        navigation,
        "",
        "指标只用于还原研究轨迹，不把样本内结果升级为样本外证据。`completed_unvalidated` 会原样显示；确定性门禁失败和已清理的会话中断不成为研究节点，其精简历史由 `research_events.jsonl` 记录。",
        "",
    ]
    for program in program_order:
        program_id = program["program_id"]
        program_edges = sorted(
            (
                edge
                for edge in lineage.get("edges", [])
                if nodes[edge["from"]]["program_id"] == program_id
                and nodes[edge["to"]]["program_id"] == program_id
            ),
            key=lambda edge: (node_order[edge["to"]], node_order[edge["from"]]),
        )
        incoming = {edge["to"] for edge in program_edges}
        lines.extend([f"## {program['code']} · {program['name']}", "", program["description"], ""])
        roots = [
            node
            for node in lineage["nodes"]
            if node["program_id"] == program_id and node["experiment_id"] not in incoming
        ]
        for node in roots:
            experiment_id = node["experiment_id"]
            config_path, config = experiments[experiment_id]
            lines.extend(
                [
                    f"### 起点 · {node['display_code']} · {node['display_name']}",
                    "",
                    f"- 策略：{config['strategy']['description']}",
                    f"- 代表结果：{render_result(headlines[experiment_id], link_prefix=link_prefix)}",
                    f"- 定义：[experiment.json]({link_prefix}{config_path.relative_to(EXPERIMENTS_ROOT)})",
                    "",
                ]
            )
        for edge in program_edges:
            source, target = edge["from"], edge["to"]
            source_node, target_node = nodes[source], nodes[target]
            source_path, source_config = experiments[source]
            target_path, target_config = experiments[target]
            changes = definition_differences(source_config, target_config)
            visible_changes = changes[:24]
            lines.extend(
                [
                    f"### {source_node['display_code']} → {target_node['display_code']} · {target_node['display_name']}",
                    "",
                    f"- 关系：`{edge['relation']}`",
                    f"- 为什么改：{edge['rationale']}",
                    f"- 策略修改：{edge['change_summary']}",
                    f"- 修改前：{render_result(headlines[source], link_prefix=link_prefix)}",
                    f"- 修改后：{render_result(headlines[target], link_prefix=link_prefix)}",
                    f"- 定义：[父实验]({link_prefix}{source_path.relative_to(EXPERIMENTS_ROOT)}) · [子实验]({link_prefix}{target_path.relative_to(EXPERIMENTS_ROOT)})",
                ]
            )
            if visible_changes:
                lines.extend(["- 自动配置差异：", ""])
                lines.extend(f"  - {item}" for item in visible_changes)
                if len(changes) > len(visible_changes):
                    lines.append(f"  - ……另有 {len(changes) - len(visible_changes)} 项，完整定义见父子 `experiment.json`。")
            else:
                lines.append("- 自动配置差异：父子策略/参数字段完全相同；变化仅来自研究窗口、数据或执行上下文之外的登记信息。")
            lines.append("")
    if selected_program_id is None:
        cross_program_edges = sorted(
            (
                edge
                for edge in lineage.get("edges", [])
                if nodes[edge["from"]]["program_id"]
                != nodes[edge["to"]]["program_id"]
            ),
            key=lambda edge: (node_order[edge["to"]], node_order[edge["from"]]),
        )
        if cross_program_edges:
            lines.extend(
                [
                    "## 跨策略输入",
                    "",
                    "这里记录一个策略流派向另一个流派提供候选池、数据边界或机制假设的关系；节点仍只归属一个主策略。",
                    "",
                ]
            )
        for edge in cross_program_edges:
            source, target = edge["from"], edge["to"]
            source_node, target_node = nodes[source], nodes[target]
            source_path, source_config = experiments[source]
            target_path, target_config = experiments[target]
            changes = definition_differences(source_config, target_config)
            visible_changes = changes[:24]
            lines.extend(
                [
                    f"### {source_node['display_code']} → {target_node['display_code']} · {target_node['display_name']}",
                    "",
                    f"- 关系：`{edge['relation']}`",
                    f"- 为什么改：{edge['rationale']}",
                    f"- 策略修改：{edge['change_summary']}",
                    f"- 修改前：{render_result(headlines[source])}",
                    f"- 修改后：{render_result(headlines[target])}",
                    f"- 定义：[来源实验]({source_path.relative_to(EXPERIMENTS_ROOT)}) · [目标实验]({target_path.relative_to(EXPERIMENTS_ROOT)})",
                ]
            )
            if visible_changes:
                lines.extend(["- 自动配置差异：", ""])
                lines.extend(f"  - {item}" for item in visible_changes)
                if len(changes) > len(visible_changes):
                    lines.append(
                        f"  - ……另有 {len(changes) - len(visible_changes)} 项，完整定义见来源与目标 `experiment.json`。"
                    )
            else:
                lines.append("- 自动配置差异：来源与目标策略/参数字段完全相同。")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_map(
    lineage: dict[str, Any],
    programs: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    experiments: dict[str, tuple[Path, dict[str, Any]]],
    rows: list[dict[str, Any]],
) -> str:
    rows_by_experiment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_experiment.setdefault(row["experiment_id"], []).append(row)
    primary_parent: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    relations: dict[tuple[str, str], str] = {}
    for edge in lineage.get("edges", []):
        source, target = edge["from"], edge["to"]
        relations[(source, target)] = edge["relation"]
        if nodes[source]["program_id"] == nodes[target]["program_id"] and target not in primary_parent:
            primary_parent[target] = source
            children.setdefault(source, []).append(target)
    node_order = {item["experiment_id"]: index for index, item in enumerate(lineage["nodes"])}
    for values in children.values():
        values.sort(key=lambda experiment_id: node_order[experiment_id])

    def card(experiment_id: str) -> str:
        node = nodes[experiment_id]
        config_path, config = experiments[experiment_id]
        headline = choose_headline(node, rows_by_experiment.get(experiment_id, []))
        metrics = ""
        if headline:
            metrics = (
                f"<div class='metrics'><span>CAGR {fmt_metric(headline['cagr_pct'], '%')}</span>"
                f"<span>Sharpe {fmt_metric(headline['sharpe'])}</span>"
                f"<span>Max DD {fmt_metric(headline['max_drawdown_pct'], '%')}</span></div>"
            )
        config_href = html.escape(str(config_path.relative_to(EXPERIMENTS_ROOT)))
        report_href = ""
        if headline and headline.get("report"):
            report_href = f" · <a href='{html.escape(headline['report'])}'>report</a>"
        run_status = headline.get("run_status", "") if headline else ""
        return (
            "<div class='node'>"
            f"<div class='code'>{html.escape(node['display_code'])}</div>"
            f"<div class='name'>{html.escape(node['display_name'])}</div>"
            f"<div class='status'>{html.escape(str(config.get('research', {}).get('stage', '')))} · {html.escape(str(run_status))}</div>"
            f"{metrics}<div class='links'><a href='{config_href}'>experiment.json</a>{report_href}</div>"
            "</div>"
        )

    def tree(experiment_id: str, seen: set[str]) -> str:
        if experiment_id in seen:
            return ""
        seen.add(experiment_id)
        descendants = children.get(experiment_id, [])
        branch = card(experiment_id)
        if descendants:
            items = "".join(
                f"<li><div class='edge'>{html.escape(relations[(experiment_id, child)])}</div>{tree(child, seen)}</li>"
                for child in descendants
            )
            branch += f"<ul>{items}</ul>"
        return branch

    program_sections: list[str] = []
    for program in sorted(programs.values(), key=lambda item: item.get("order", 999)):
        program_node_ids = [
            node["experiment_id"]
            for node in lineage["nodes"]
            if node["program_id"] == program["program_id"]
        ]
        roots = [experiment_id for experiment_id in program_node_ids if experiment_id not in primary_parent]
        seen: set[str] = set()
        forest = "".join(f"<li>{tree(root, seen)}</li>" for root in roots)
        for experiment_id in program_node_ids:
            if experiment_id not in seen:
                forest += f"<li>{tree(experiment_id, seen)}</li>"
        program_sections.append(
            f"<section><h2>{html.escape(program['code'])} · {html.escape(program['name'])}</h2>"
            f"<p>{html.escape(program['description'])}</p><div class='tree'><ul>{forest}</ul></div></section>"
        )

    edge_rows = "".join(
        f"<tr><td>{html.escape(nodes[edge['from']]['display_code'])}</td>"
        f"<td>{html.escape(str(edge['relation']))}</td>"
        f"<td>{html.escape(nodes[edge['to']]['display_code'])}</td>"
        f"<td>{html.escape(str(edge['change_summary']))}</td></tr>"
        for edge in lineage.get("edges", [])
    )
    view_rows = ""
    for view in lineage.get("views", []):
        program_codes = [programs[item]["code"] for item in view.get("program_ids", [])]
        links = " · ".join(
            f"<a href='../../{html.escape(path)}'>{html.escape(path)}</a>"
            for path in view.get("paths", [])
        )
        supported = "、".join(nodes[item]["display_code"] for item in view.get("supports", []))
        view_rows += (
            f"<tr><td>{html.escape(str(view.get('view_id', '')))}</td>"
            f"<td>{html.escape(' + '.join(program_codes))}</td><td>{links}</td>"
            f"<td>{html.escape(supported)}</td></tr>"
        )

    events: list[dict[str, Any]] = []
    if EVENTS_PATH.is_file():
        for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    event_rows = "".join(
        f"<tr><td>{html.escape(str(event.get('timestamp', '')))}</td>"
        f"<td>{html.escape(str(event.get('event_type', '')))}</td>"
        f"<td>{html.escape(str(event.get('summary', '')))}</td></tr>"
        for event in reversed(events)
    )
    program_history_links = " / ".join(
        f"<a href='program_evolution/{html.escape(program['code'])}.md'>{html.escape(program['code'])}</a>"
        for program in sorted(programs.values(), key=lambda item: item.get("order", 999))
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant 研究谱系</title><style>
:root{{--bg:#f5f6f8;--card:#fff;--ink:#17202a;--muted:#667085;--line:#aeb7c2;--accent:#175cd3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1500px;margin:auto;padding:28px}}h1{{margin:0 0 8px}}h2{{margin-top:42px}}a{{color:var(--accent)}}.top{{color:var(--muted)}}
.tree ul{{position:relative;list-style:none;margin:0;padding-left:30px}}.tree>ul{{padding-left:0;display:flex;gap:28px;align-items:flex-start;overflow:auto;padding-bottom:12px}}
.tree li{{position:relative;padding:8px 0 8px 24px;min-width:270px}}.tree li:before{{content:"";position:absolute;left:4px;top:0;height:50%;width:16px;border-left:2px solid var(--line);border-bottom:2px solid var(--line)}}
.tree>ul>li{{padding-left:0}}.tree>ul>li:before{{display:none}}.node{{background:var(--card);border:1px solid #d9dee7;border-radius:10px;padding:13px 15px;box-shadow:0 2px 8px #10182812;min-width:255px}}
.code{{font-weight:750;color:var(--accent)}}.name{{font-size:15px;font-weight:650;margin:3px 0}}.status,.links,.edge{{color:var(--muted);font-size:12px}}.metrics{{display:flex;gap:10px;flex-wrap:wrap;margin:9px 0;font-variant-numeric:tabular-nums}}.metrics span{{background:#eef4ff;padding:2px 6px;border-radius:5px}}
.edge{{margin-bottom:3px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:left}}
</style></head><body><main><h1>Quant 研究谱系</h1>
<p class="top">Experiment 是研究节点，Run 是执行历史，Case 是参数组合。节点指标来自当前 latest validated run；详细的“为什么改、改了什么、结果如何”见 <a href="strategy_evolution.md">总策略演化史</a>，分策略演化史见 {program_history_links}，多窗口和多 case 数据见 <a href="scorecard.csv">scorecard.csv</a>。</p>
{''.join(program_sections)}
<section><h2>完整关系边</h2><p>树图只选择一个主父节点排版；所有并行输入和交叉检验关系以下表为准。</p>
<table><thead><tr><th>From</th><th>Relation</th><th>To</th><th>Change</th></tr></thead><tbody>{edge_rows}</tbody></table></section>
<section><h2>View 与策略流派关联</h2><p>View 不含策略账本，但保留它支持了哪个研究节点的来源关系。</p>
<table><thead><tr><th>View</th><th>Program</th><th>Artifacts</th><th>Supports</th></tr></thead><tbody>{view_rows}</tbody></table></section>
<section><h2>研究事件日志</h2><table><thead><tr><th>时间</th><th>事件</th><th>摘要</th></tr></thead><tbody>{event_rows}</tbody></table></section>
</main></body></html>
"""


def compare_or_write(path: Path, content: str, *, check: bool) -> bool:
    if check:
        return path.is_file() and path.read_text(encoding="utf-8") == content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated outputs are stale.")
    args = parser.parse_args()
    lineage = read_json(LINEAGE_PATH)
    experiments = discover_experiments(EXPERIMENTS_ROOT)
    programs, nodes = validate_lineage(lineage, experiments)
    sections = parse_registry_sections(INDEX_PATH.read_text(encoding="utf-8"), nodes)
    index_text = render_index(lineage, programs, nodes, sections)
    rows = build_scorecard_rows(lineage, programs, nodes, experiments)
    scorecard_text = render_scorecard(rows)
    evolution_text = render_evolution(lineage, programs, nodes, experiments, rows)
    map_text = render_map(lineage, programs, nodes, experiments, rows)
    outputs = {
        INDEX_PATH: index_text,
        SCORECARD_PATH: scorecard_text,
        EVOLUTION_PATH: evolution_text,
        MAP_PATH: map_text,
    }
    for program in programs.values():
        outputs[PROGRAM_EVOLUTION_ROOT / f"{program['code']}.md"] = render_evolution(
            lineage,
            programs,
            nodes,
            experiments,
            rows,
            selected_program_id=program["program_id"],
        )
    stale = [path for path, content in outputs.items() if not compare_or_write(path, content, check=args.check)]
    if stale:
        print("Research catalog: STALE")
        for path in stale:
            print(f"- {path.relative_to(BACKTEST_ROOT)}")
        return 1
    print("Research catalog: PASS" if args.check else "Research catalog: BUILT")
    print(f"- programs: {len(programs)}")
    print(f"- experiments: {len(experiments)}")
    print(f"- scorecard rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
