# qqq_sma_recovery_probation_grid_2000_2015_v1

`TIM-v0.50b.1` 从已验证的 `TIM-v0.50` 派生，重新检验原来表现较强的买入 SMA310 / 卖出 SMA190 区域。它保留 `c=3%`、关闭 `d`，但修复熊市小反弹可能过早买入且买后没有“恢复失败”退出状态的问题。

普通恢复买入只认完成 Close 从长均线下方向上穿越，下一交易日 Open 才成交。所有新仓先是试探仓：普通试探仓若在完成 Close 站上短均线前重新收在长均线下方，下一 Open 清仓；一次性 `c=3%` 强制买回形成的试探仓，若在真正恢复前收盘跌回原确认多头卖出计成本价下方，同样在下一 Open 清仓。试探仓完成 Close 站上短均线后才升级为确认多头，随后恢复盘前冻结动态短均线、开盘跳空或盘中向下触线卖出的规则。失败退出不刷新 `c` 锚，每个确认卖出后的熊市阶段最多一次强制买回。

本轮冻结 QQQ 2000-12-18～2015-12-31，买入和卖出 SMA 均扫描80～450日、步长10，共1,444组；每边5 bps，初始空仓，全进全出。主指标仍是三个连续、继承完整策略状态的分段 CAGR 最小值；同时检查二维高分平台、边界、事件可识别性、PBO、DSR，以及每个周期对相对父实验 `c=3%、d关闭` 的配对变化。全历史 CAGR、Sharpe和最大回撤冠军只作补充。

这是在已经反复研究的2000～2015数据上进行策略语义修复和重新探索，不是新的样本外验证。此前查看过的2016～2026路径也不能再作为这套新语义的纯净锁定测试。

正式运行命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015
.venv/bin/python -m scripts.run_sma_recovery_probation_grid --experiment experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015 --run-id RUN_ID --symbol QQQ --cost-bps 5 --parent-results /ABSOLUTE/PATH/TO/TIM-v0.50/QQQ/cost_5bps/parameter_results.csv
.venv/bin/python -m scripts.analyze_sma_recovery_probation_grid --experiment experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015 --run-id RUN_ID
```

若运行中断，必须恢复同一个 active run；不得新建重复实验或把网络、Chrome、数据挂载等基础设施问题记成策略失败。
