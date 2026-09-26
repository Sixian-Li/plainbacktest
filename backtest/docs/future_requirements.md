# 回测工程延期需求

> 文档用途：记录已经认可、但当前明确不实施的工程能力，防止后续 agent 遗忘或误当成当前承诺。策略研究创新仍记录在 `ideas.md`；本文件只管理工程与项目治理需求。

## 当前决策

以下编号沿用 2026-08-08 的项目管理建议，并按 2026-08-14 的 28 个实验现状重新评估。已完成能力不再继续伪装成 deferred；触发但尚未实施的项目明确标为 next priority。

### 3. 区分实验规格与解析后的最终配置

- 状态：`partially_completed`
- 目标：分别保留用户/agent 最初定义的实验意图，以及补齐默认值、版本和哈希后的机器执行配置。
- 已有能力：`experiment.json` 保存权威研究定义，run 内的 `experiment_snapshot.json`、定义哈希、provenance 和 manifest 保存实际执行证据。
- 剩余缺口：尚未单独保存“用户最初输入”与“补齐默认值后的 resolved config”；只有当自动默认值继续增加时再拆分，不为现有 28 个实验倒写聊天记录。

### 5. 为配置和 manifest 建立正式 JSON Schema

- 状态：`next_priority`
- 目标：用版本化 Schema 验证 experiment、数据登记和 artifact manifest 的字段、类型、范围与兼容性。
- 当前判断：三类策略与多个 agent 的触发条件已经满足；现有 Python 审计仍通过，但下一轮新增配置字段前应先建立 schema，而不是继续堆定制校验。

### 6. 建立完整统一命令入口

- 状态：`partially_completed`
- 目标：最终提供类似 `quant new/run/report/validate/finalize` 的稳定用户入口。
- 已有能力：数据统一使用 `scripts/data_update.py`；run 创建/恢复使用 `scripts.start_experiment_run.py`；中断与清理使用 `scripts.manage_experiment_run.py`；验证使用 `scripts.validate_run.py`。
- 剩余缺口：不同策略仍保留各自 run/analyze 命令。只有其参数接口进一步趋同时才封装顶层 `quant`，避免一个巨型入口隐藏策略差异。

### 10. 自动生成实验索引并减少 Markdown/JSON 双写

- 状态：`completed`
- 目标：从实验配置和 manifest 自动生成状态、参数、指标与产物链接，人工只维护研究判断和决策理由。
- 已完成：`scripts.build_research_catalog` 已从 28 个 experiment、lineage 和代表 run 自动生成 `strategy_evolution.md`、`scorecard.csv` 与 `research_map.html`，并重排 `index.md` 的流派分组；测试和 `--check` 阻止漂移。

## 数据框架后续边界

- `ready`：S&P 500 历史时点基线、当前成员影子更新、供应商原始响应重放、购买数据隔离接收。
- `not_implemented`：QQQ/SPY 影子更新、Nasdaq-100 历史时点成员、批准库提升写入和自动调度。
- `not_defined`：熊市空仓期的对冲资产池及其数据契约；须先由策略研究确定候选类别，再扩展数据范围。

这些状态由 `data/data_update_registry.json.capabilities` 机器登记，`data_update.py status` 必须原样暴露，不能把 SPY 当前成分更新器描述成覆盖全部策略数据。

## 复核规则

- 每次新增一种实验范式或完成一轮结构升级时，检查上述状态；`next_priority` 仍需单独设计和验收，不因顺手修改而扩张。
- 若决定启动某项，先把状态、范围、迁移方式和验收条件写入实施计划，再修改代码或目录。
- 本文件记录延期工程需求，不记录已完成状态、具体回测结果或策略参数想法。
