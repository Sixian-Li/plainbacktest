# Backtest

> 发行版请先从[根 README](../README.md)运行离线示例。下文保留正式研究流水线；原始大包和历史 run 未全部附带，范围见[发布说明](docs/release/scope.md)。

本目录是 Quant 工作区的标准化回测项目。新 agent 先阅读根目录 `../catalog.md`、`../log.md` 和 `experiments/index.md`，再根据任务进入 `docs/`。

研究组织的权威入口是 [研究架构](docs/architecture.md)、[总策略演化史](experiments/strategy_evolution.md)、分策略演化史（[DER](experiments/program_evolution/DER.md) / [ROT](experiments/program_evolution/ROT.md) / [TIM](experiments/program_evolution/TIM.md)）、[分流派登记册](experiments/index.md)、[分叉谱系图](experiments/research_map.html) 和 [统一指标台账](experiments/scorecard.csv)。正式实验按 `experiments/<DER|ROT|TIM>/<版本>__<YY-MM-DD>__<slug>/` 物理分类，机器权威和完整日期保存在 `experiments/lineage.json`。

整体平台开源的现状证据、抽象难点、分阶段迁移与验收条件见 [GitHub-ready 重构计划](docs/github_ready_refactor_plan.md)。该文档是后续工程路线图，不代表已实现或已获准公开的数据能力。

## 固定流水线

```text
data/source_registry.yaml
→ 标准化与质量门禁
→ experiment.json：策略规格、研究假设和晋级规则
→ runs/<run_id>/：冻结配置与执行上下文
→ PyBroker + 独立参考账本
→ 参数研究与稳健性分析
→ CSV / JSON / NPZ 机器结果
→ Markdown + 标题后先用人类语言解释冻结策略的版本化自包含 Plotly HTML
→ 强制验证门禁并锁定 run
→ 更新 lineage.json 与追加式 research_events.jsonl
→ 重建 experiments/index.md、strategy_evolution.md、program_evolution/{DER,ROT,TIM}.md、scorecard.csv、research_map.html
→ 更新 log.md 和 catalog.md
```

`experiment.json` 是长期研究定义；`runs/<run_id>/` 是一次冻结数据、代码、环境和结果的具体执行；网格中的单个参数组合叫 case。同一实验可以有多个 run，但已有 run 和已完成区块不覆盖，验证后的 run 禁止改写。改变策略语义时建立新实验。`runs/` 之所以再分 `run_id`，是为了保留同一策略在不同数据、代码或执行批次下的冻结证据，不代表报告是临时产物；实验根目录的 `report.html` 始终指向最新 validated run，日常从这里直接打开。

网络或 Agent 会话断开不属于策略失败。新会话先恢复 active run：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment EXPERIMENT --resume-active
.venv/bin/python -m scripts.manage_experiment_run --experiment EXPERIMENT status
```

确实无法续跑时才显式标记中断；成功重跑并验证后，清理命令默认只预览，只有额外传入 `--apply` 才删除中断目录并保留中央事件：

```bash
.venv/bin/python -m scripts.manage_experiment_run --experiment EXPERIMENT interrupt --run-id RUN_ID --reason "network/session interruption"
.venv/bin/python -m scripts.manage_experiment_run --experiment EXPERIMENT prune --run-id RUN_ID --superseded-by VALIDATED_RUN_ID
```

正式实验必须保存自然语言策略描述、精确买卖规则、研究阶段与晋级标准。正式 run 必须保存数据与代码指纹、信号和成交时间、成本、全部 case、订单、交易、每日账户状态、报告及验证证据。全样本参数扫描不得称为样本外验证，阶段规则见 `docs/research_protocol.md`。

日内动态 SMA200 双阈值策略的盘前解价、成交状态机、QQQ 两套保留候选及跨标的完整研发流程，见 [`docs/intraday_sma200_research_playbook.md`](docs/intraday_sma200_research_playbook.md)。

## 环境

项目环境固定在 `.venv/`。直接依赖见 `requirements.in`，完整环境见 `requirements.lock`。

```bash
cd backtest
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m scripts.build_research_catalog --check
.venv/bin/python -m scripts.audit_workspace --workspace ..
```

## 双均线 Streamlit 实验台

需要即时切换批准标的、日期和快慢 SMA，查看持仓/日历 CAGR 与 Sharpe，或生成自定义参数热力图时，启动探索性实验台：

```bash
cd backtest
.venv/bin/python -m streamlit run dual_sma_lab/app.py
```

完整口径和验证入口见 [`dual_sma_lab/README.md`](dual_sma_lab/README.md)。页面使用参考账本和分块向量网格，不会写入 experiment 或把临时结果声明为 validated 证据。

## Agent worktree 与发布

工作树只放在主仓库可见的 `../worktrees/`，用于并行隔离；正式实验、谱系和报告最终都回到本目录。创建实验工作树时只展开目标实验的历史 runs，大型数据和 `.venv` 只建立共享链接，不生成副本：

```bash
.venv/bin/python -m scripts.manage_worktree create TASK_NAME --experiment TIM/TIM-vX__YY-MM-DD__slug
```

Agent 在工作树中完成、验证并提交分支后，从干净主目录串行发布。发布会合并分支、补齐未被 Git 跟踪的大型 run 产物、重建研究目录并创建实验根目录 `report.html` 入口：

```bash
.venv/bin/python -m scripts.manage_worktree publish TASK_NAME --experiment TIM/TIM-vX__YY-MM-DD__slug
.venv/bin/python -m scripts.manage_worktree remove TASK_NAME
```

禁止把 `.worktrees/` 或 worktree 里的报告当成最终交付，也禁止在工作树保留 `data/` 的真实副本。

## 当前实验复现

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid
.venv/bin/python -m scripts.run_sma_threshold_grid --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.run_sma_threshold_grid --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.run_sma_threshold_grid --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID --symbol SPY --cost-bps 0
.venv/bin/python -m scripts.run_sma_threshold_grid --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID --symbol SPY --cost-bps 5
.venv/bin/python -m scripts.analyze_sma_threshold_grid --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.10__26-08-08__sma200_threshold_grid --run-id RUN_ID
```

手工日期日程实验复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1
.venv/bin/python -m scripts.run_scheduled_backtest --experiment experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1 --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.run_scheduled_backtest --experiment experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1 --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_scheduled_backtest --experiment experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1 --run-id RUN_ID
```

QQQ 日内动态 SMA OR 规则实验复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021
.venv/bin/python -m scripts.run_intraday_sma_backtest --experiment experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021 --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_intraday_sma_backtest --experiment experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021 --run-id RUN_ID
```

QQQ 日内动态 SMA200 双阈值与纠错线网格复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025
.venv/bin/python -m scripts.run_intraday_sma200_threshold_grid --experiment experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025 --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.run_intraday_sma200_threshold_grid --experiment experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025 --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma200_threshold_grid --experiment experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025 --run-id RUN_ID
```

QQQ SMA200 a/b 全历史稳健选参复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection
.venv/bin/python -m scripts.run_intraday_sma200_robust_selection --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma200_robust_selection --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID
```

QQQ SMA200 强制卖出 d 的独立敏感性复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity
.venv/bin/python -m scripts.run_intraday_sma200_forced_sell_sensitivity --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma200_forced_sell_sensitivity --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID
```

八个熊市候选的 SMA200 峰值回撤 3%～20% 网格复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid
.venv/bin/python -m scripts.run_bear_event_selected8_stop_grid --experiment experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid --run-id RUN_ID --symbol BEAR_SELECTED8_STOP_GRID --cost-bps 0
.venv/bin/python -m scripts.run_bear_event_selected8_stop_grid --experiment experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid --run-id RUN_ID --symbol BEAR_SELECTED8_STOP_GRID --cost-bps 5
.venv/bin/python -m scripts.analyze_bear_event_selected8_stop_grid --experiment experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid --run-id RUN_ID
```

15 标的熊市星标双坑与峰值强卖消融复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation
.venv/bin/python -m scripts.run_bear_event_weighted_trailing_stop --experiment experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation --run-id RUN_ID --symbol BEAR_EVENT_WEIGHTED_STOPS --cost-bps 0
.venv/bin/python -m scripts.run_bear_event_weighted_trailing_stop --experiment experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation --run-id RUN_ID --symbol BEAR_EVENT_WEIGHTED_STOPS --cost-bps 5
.venv/bin/python -m scripts.analyze_bear_event_weighted_trailing_stop --experiment experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation --run-id RUN_ID
```

QQQ 初始空仓与强制买回 R 网格实验复现：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021
.venv/bin/python -m scripts.run_intraday_sma_reentry_grid --experiment experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021 --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_intraday_sma_reentry_grid --experiment experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021 --run-id RUN_ID
```

同时关闭强制买回、成本止损和慢性转弱卖出的完整历史组合消融：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history
.venv/bin/python -m scripts.run_intraday_sma_combination_ablation --experiment experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_intraday_sma_combination_ablation --experiment experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history --run-id RUN_ID
```

QQQ 全历史 A–H/L/R 双目标全局搜索：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history
.venv/bin/python -m scripts.run_intraday_sma_global_search --experiment experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_intraday_sma_global_search --experiment experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history --run-id RUN_ID
```

三窗口局部参数稳定性实验：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability
.venv/bin/python -m scripts.run_intraday_sma_window_stability --experiment experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_intraday_sma_window_stability --experiment experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability --run-id RUN_ID
```

QQQ 条件 1+3 的持仓峰值回撤阈值训练与样本外检验：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos
.venv/bin/python -m scripts.run_sma_regime_drawdown_oos --experiment experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_sma_regime_drawdown_oos --experiment experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos --run-id RUN_ID
```

QQQ 两项回撤措施的双时期 2×2 消融：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods
.venv/bin/python -m scripts.run_sma_regime_two_measure_ablation --experiment experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods --run-id RUN_ID --symbol QQQ --cost-bps 0
.venv/bin/python -m scripts.analyze_sma_regime_two_measure_ablation --experiment experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods --run-id RUN_ID
node scripts/print_html_pdf.mjs experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods/runs/RUN_ID/report_print.html experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods/runs/RUN_ID/report.pdf
.venv/bin/python -m scripts.finalize_sma_regime_two_measure_ablation --experiment experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods --run-id RUN_ID
.venv/bin/python -m scripts.validate_two_measure_run --experiment experiments/ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods --run-id RUN_ID
```

每个 symbol/cost 区块是独立检查点。确定性的代码、数据、账本、报告或质量门禁错误才标记 `failed` 并保留证据；网络/会话中断优先恢复 active run。原始购买数据只读，VOO 在解除质量门禁前不得进入正式实验。

## 报告模板

已验收的交互报告放在 `report_templates/<template_id>/`，Python 组装入口在 `quantkit/reporting.py`。实验生成器只提供标题、说明和带 `series_key`、`panel`、`is_benchmark` 元数据的 Plotly 图；不要把整段 HTML 复制到新实验。

`interactive_research_v1` 是首个验收版本；`v2` 增加 K 线叠加指标勾选及统一色阶参数热力图；`v3` 让市场图的价格与副图分别按可见时间段及已勾选曲线自动缩放；`v4` 在标题后、所有结果前原生展示本次冻结的完整策略卡；`v5` 把策略卡改成“交易对象 → 买入 → 卖出 → 信号与成交 → 资金与比较”的自然语言流程，取消首页字段表和参数代码块。完整参数只在网页折叠附录中展示，打印/PDF不展开，并指向 `experiment_snapshot.json`。新报告默认使用 v5；旧 validated HTML/PDF 保持不变。各版本都保留净值勾选、区间左端对齐和等额定投，对齐和定投只读取已保存净值，不改写正式账本。修改模板后运行：

使用 v5 的 experiment 必须在 `strategy.plain_language` 填写 `summary`、`buy`、`sell`、`execution` 和 `position` 五个短句。这里面只写给人看的中文解释；精确公式、穿越方向和时间条件仍分别保存在 `description`、`buy_rule`、`sell_rule`、`signal_time`、`execution_time` 与 `parameters`，由网页折叠附录和机器快照完整保留。

```bash
.venv/bin/python -m unittest tests.reporting.test_reporting -v
.venv/bin/python -m scripts.analyze_sma_threshold_grid --experiment EXPERIMENT --run-id RUN_ID
node scripts/smoke_report_ui.mjs EXPERIMENT/runs/RUN_ID/report.html performance-qqq
```

## 数据更新操作

数据层统一通过 `scripts/data_update.py` 操作，不直接编辑共享的标准价格文件：

```bash
.venv/bin/python scripts/data_update.py status
.venv/bin/python scripts/data_update.py check
.venv/bin/python scripts/data_update.py shadow-update
```

当前更新器只处理带日期的 SPY 现有成分，并且是影子模式；`status` 同时显示 QQQ/SPY/VOO 的截止日与尚未实现的能力。QQQ/SPY 影子更新、Nasdaq-100 历史时点成员、对冲资产池、正式提升和自动调度目前都不会被误报为已完成。完整契约见 `../data/README.md` 与 `../data/data_update_registry.json`；深度检查使用 `.venv/bin/python scripts/data_update.py check --deep`。
