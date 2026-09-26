# Quant 研究架构

## 四层职责

```text
data/ 标准数据与质量证据
  ↓ 只读
research/market_views/ 市场观察、区间标注与无交易信号的 View
  ↓ 形成假设
backtest/quantkit/ + scripts/ + report_templates/ 可复用的回测能力
  ↓ 冻结策略定义
backtest/experiments/<PROGRAM>/<VERSION>__<YY-MM-DD>__<slug>/ 研究节点与不可覆盖证据
```

- **View** 不含策略账本和账户收益；它可以支持一个或多个研究流派，但不是实验。
- **Backtest framework** 是策略无关或可参数化复用的执行、成交、账本、指标、报告和验证能力。
- **Experiment** 是带假设、规则、数据范围、选择标准与晋级条件的研究节点。策略语义或研究问题改变时新建 experiment；只重复执行同一冻结定义时新建 run；网格里的参数组合是 case。
- **Run** 冻结当时的 experiment、代码、数据和环境。`validated` 永不覆盖；代码/账本/门禁错误记为 `failed`；网络、进程或 Agent 会话断开记为 `interrupted`，它不是策略证据。

新会话必须先读取 `active_run_id`：同一定义的 `running` 直接续跑，`completed_unvalidated` 继续验证，不能因断网再创建一个目录。只有明确放弃时才标记 `interrupted`；存在 validated 后继且用户授权时，可以删除中断目录，但在 `research_events.jsonl` 留一条精简记录。

## 研究流派与版本

机器权威是 `experiments/lineage.json`，当前三条主线为：

- `DER`：短均线导数卖点、再入场、全局搜索、稳定性与跨标的检验。
- `ROT`：上涨状态、快速退出、候选评分与多标的组合轮动。
- `TIM`：简单趋势择时、熊市识别与未来空仓期对冲。

展示版本写作 `<PROGRAM>-v<major>.<minor>[branch.sequence]`。例如 `DER-v0.20a.1` 与 `DER-v0.20b.1` 是同一父节点的并行研究分支；run 重试不提升研究版本。正式目录固定为 `experiments/<PROGRAM>/<DISPLAY_CODE>__<YY-MM-DD>__<slug>/`，例如 `experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021/`。短日期只用于浏览排序，谱系中的 `created_on` 与 experiment 的 `created_at_utc` 保留完整年份；`experiment_id` 始终稳定。

新实验若确实不属于三条主线，可以在谱系中增加 `OTH`，但必须写明为何不能归入既有流派；不能用“其他”代替尚未完成的判断。

## 连续记录

- `experiments/index.md`：面向人的分流派实验登记册，保留完整结论与证据入口。
- `experiments/strategy_evolution.md`：自动生成的逐边演化史；把“为什么改、改了什么、修改前后指标、配置差异”连成可读链条，不手工维护。
- `experiments/program_evolution/{DER,ROT,TIM}.md`：同一权威自动生成的三份独立演化史；每份只含本流派节点和内部演化边，便于 Agent 专注接续一条策略线。
- `experiments/research_map.html`：可视化分叉谱系，显示每个节点当前代表性 CAGR、Sharpe 与最大回撤。
- `experiments/scorecard.csv`：所有 latest validated run 的统一长表；若尚无 validated run，明确显示 active run 状态。
- `experiments/research_events.jsonl`：追加式事件日志；run 启动、分析完成、失败和验证由生命周期代码自动记录。
- 根目录 `log.md`：只保留便于接班的人类工作摘要，不复制逐 case 指标。

`scripts/build_research_catalog.py` 从实验、谱系和结果重建登记册、总演化史、三份分策略演化史、成绩表与谱系图。每条新演化边只需在 `lineage.json` 填一次 `rationale` 和 `change_summary`；父子配置差异及代表 run 指标由生成器自动同步到总文档与对应流派文档。新增或完成实验后运行：

```bash
cd backtest
.venv/bin/python -m scripts.build_research_catalog
.venv/bin/python -m scripts.build_research_catalog --check
.venv/bin/python -m scripts.audit_workspace --workspace ..
```

审计要求每个 canonical `experiment.json` 恰好有一个流派、一个不重复展示版本和一个完整创建日期，并同时存在于对应物理目录、登记册、成绩表和谱系图。移动实验时必须整体移动其 `runs/`，不得改写 validated run 内部文件。

所有正式 `report.html` 都必须在标题后、任何指标前用人类语言复述本次冻结策略：先用一句话说明交易对象和意图，再依次解释何时买、何时卖、信号何时确认、何时成交、仓位与资金、成本和基准。不得把 `parameters` JSON、Python 字典、代码条件或机器字段表直接放在报告/PDF首页。`interactive_research_v5` 将完整机器参数保留在网页的默认折叠附录，并在打印时隐藏；打印版只保留可读策略逻辑，同时指向同一 run 的 `experiment_snapshot.json` 作为完整参数权威。历史 validated HTML/PDF 不回写。HTML 没有“临时报告”这一层：权威文件仍是不可覆盖的 `runs/<run_id>/report.html`，实验根目录的 `report.html` 是指向 `latest_validated_run_id` 的可见入口，供 Finder 直接打开。

测试按 `tests/core`、`data`、`lifecycle`、`reporting` 和 `strategies/{der,rot,tim}` 物理分类。测试是可复用工程契约，不使用实验日期或研究版本命名。

所有 linked worktree 统一放在主仓库可见的 `worktrees/`，并通过 `scripts.manage_worktree` 创建、发布和注销。工作树使用 sparse checkout，默认不展开其他实验的历史 `runs/`；`backtest/.venv` 与大型共享数据必须链接到主目录，发现真实副本就中止，不能静默保留。worktree 只提供 Agent 隔离，不是实验或报告的最终存放位置；validated 实验必须发布回主目录，主目录是唯一 canonical 交付面。禁止隐藏 `.worktrees/`、临时目录和 Quant 工作区外的 worktree。

## Agent Skill 边界

- `quant-backtest`：创建、修改、续跑、验证和发布 experiment/run，并自动同步策略演化。
- `data-update`：检查数据新鲜度、运行隔离影子更新、复核质量和接收新购买数据；不执行策略回测。
- `quant-tidy`：移动、去重、清理和审计工作区结构；普通回测完成不再重复触发它。

三个技能分别对应“研究执行、数据运营、结构治理”，没有重复技能。工作区脚本是唯一实现来源；Skill 只保留流程和安全边界，不复制验证器或数据抓取代码。
