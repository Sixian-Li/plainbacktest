# Quant Research：Agent 入口

本项目是自然语言策略回测框架。先读 `README.md`、`catalog.md`、`log.md`、`backtest/docs/architecture.md` 和 `backtest/experiments/lineage.json`。

## 项目内 Skill

- 回测、实验、对账和报告：`.agents/skills/quant-backtest/SKILL.md`。
- 数据状态、影子更新和候选审查：`.agents/skills/data-update/SKILL.md`。
- 目录、移动、谱系和结构维护：`.agents/skills/quant-tidy/SKILL.md`。

这些文件随仓库分发；从当前目录定位项目根，不使用作者本机路径或全局 Skill 副本。Skill 的 Python 实现留在项目代码中。

## 发行版起点

1. 默认读取随包标准日线；先运行 `backtest/.venv/bin/python backtest/scripts/release_data.py check`。离线快速入口为 `backtest/scripts/quickstart.py`，输出到 `outputs/`。
2. 完整原始档案及历史 run 未附带。`backtest/docs/release/scope.md` 说明范围；不要隐式重建标准数据、恢复旧 run 指针，或把旧结论称为本次验证。
3. 版权许可见 `data/distribution.json` 和 `data/LICENSE`。VOO 的质量失败、历史成员候选的待审状态保持不变。数据使用权不替代质量门禁。
4. 快速示例用于安装和双账本验证；正式 run 仍必须符合完整生命周期、Git 溯源和验证门禁。项目尚无 Git 仓库时，可做准备性开发和示例执行，不声称完成正式 run 验证。

## 研究约定

- 三条策略线是 DER、ROT、TIM，机器权威是 `backtest/experiments/lineage.json`。
- 动手前读工作区状态，保留用户修改。检查 `active_run_id`，优先恢复同一定义的 `running` 或 `completed_unvalidated` run。
- 存在 Git 仓库且开展正式研究时，使用 `backtest/scripts/manage_worktree.py` 在主目录 `worktrees/` 下隔离；数据和环境链接到该仓库主目录。不要把运行环境链接到发行前的作者目录。
- 只读使用数据，显式指定 Open/Close 与信号时序；对每个 case 核对 PyBroker 与独立账本的现金、持仓、净值和订单。
- 正式报告通过 `quantkit.reporting.render_interactive_report` 使用 v5；标题后、指标前讲清对象、买卖、成交顺序、仓位、成本和基准。精确机器配置放折叠附录。
- 不改 validated run。网络、进程或会话中断记为 `interrupted`，不当作策略失败。
- 研究变更在谱系边记录原因；由 `scripts.build_research_catalog` 同步登记册、演化史、台账与图，不手改生成文件。
- 正式成果完成后验证并发布回本仓库主目录；工作树不是唯一交付位置。

## 常用检查

从 `backtest/` 执行：

```bash
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m scripts.build_research_catalog --check
.venv/bin/python -m scripts.audit_workspace --workspace ..
```

完整原始包测试在缺少相应源档案时明确 skip；不能把 skip 报告成已通过。正式数据重建和在线更新需按数据 Skill 的具体流程操作。
