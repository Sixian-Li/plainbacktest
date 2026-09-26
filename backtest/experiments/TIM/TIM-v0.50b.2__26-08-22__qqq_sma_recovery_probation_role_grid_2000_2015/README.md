# qqq_sma_recovery_probation_role_grid_2000_2015_v1

`TIM-v0.50b.2` 保留 `TIM-v0.50b.1` 的全部成交与试探持仓语义，只修正选参域。首轮无角色约束的曲面代表是买入 SMA270 / 卖出 SMA390，这相当于交换了“长线确认恢复”和“短线快速退出”的工作，不能回答原研究问题。因此本轮把 `buy_window > sell_window` 冻结为硬门禁。

买入和卖出 SMA 仍各扫描80～450日、步长10，全部1,444格都计算并留存，便于与旧语义逐格核对；但只有703个买入周期严格长于卖出周期的组合可以进入冠军、二维平台、PBO、DSR和晋级判断。若高分区触及买入450、卖出80或 `buy=sell+10` 的语义对角边界，也不得提名稳定区域。局部代表必须具有完整、角色合法且事件可识别的3×3邻域。

执行语义不变：普通恢复由完成 Close 上穿长均线确认并在下一 Open 买入；每个新仓先是试探仓，失败时下一 Open 退出；试探仓完成 Close 站上短均线后才成为确认多头；确认多头按盘前冻结的动态短均线盘中卖出；每个确认卖出后的熊市阶段最多一次 `c=3%` 强制买回，失败退出不刷新锚。`d` 关闭，单边成本5 bps。

本轮仍使用 QQQ 2000-12-18～2015-12-31，属于在既有探索数据上的研究设计修正，不是样本外验证。正式命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015
.venv/bin/python -m scripts.run_sma_recovery_probation_role_grid --experiment experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015 --run-id RUN_ID --symbol QQQ --cost-bps 5 --parent-results /ABSOLUTE/PATH/TO/TIM-v0.50/QQQ/cost_5bps/parameter_results.csv
.venv/bin/python -m scripts.analyze_sma_recovery_probation_role_grid --experiment experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015 --run-id RUN_ID
```
