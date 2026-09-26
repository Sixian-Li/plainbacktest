# qqq_intraday_sma_period_cross_locked_2016_2026_v1

`TIM-v0.50a.1` 是 `TIM-v0.50` 的锁定样本外子实验。唯一主测试参数在查看2016年以后结果前固定为 `c=3%`、`d=关闭`、买入SMA310、卖出SMA190；策略从2016-01-04以100,000美元现金独立空仓开始，运行到批准QQQ数据末日2026-08-04。

同时预登记买入SMA270～350、卖出SMA150～230、步长10的9×9局部扰动。81组全部保存并核对，但只用于观察310/190附近是否平滑；禁止根据本轮结果把锚点替换成收益最高的扰动组合。单边成本固定5 bps，c固定3%，d始终关闭，其余信号、盘前解价、跳空成交和每日最多一笔的语义与父实验完全相同。

正式运行命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026
.venv/bin/python -m scripts.run_intraday_sma_period_locked_test --experiment experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026 --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma_period_locked_test --experiment experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026 --run-id RUN_ID
```

本实验不使用PBO或DSR在保留期内二次选参。即使310/190和局部邻域表现良好，也只能记录为样本外支持证据；父实验PBO/DSR失败仍然阻止直接晋级。

最新验证 run：`run_20260815T161550Z_27a24f47`。锁定310/190在2016-01-04～2026-08-04取得 CAGR 20.003%、Sharpe 0.977、最大回撤 -31.46%，同期QQQ持有为20.406%、0.946、-35.12%。它保留了持有98.0%的CAGR并略微提高Sharpe，但回撤只改善3.66个百分点，未达到预登记5个百分点，因此锚点效果总门禁未通过。

局部3×3稳定性门禁通过：9组CAGR均为正，范围18.421%～22.335%，中位数20.003%；最差Sharpe相对锚点只低0.068，最差回撤只恶化0.47个百分点。全81组两套编译账本所有指标差为0，81组PyBroker/Python正式复核最大净值差约8.15e-10美元；230项backtest、19项research、依赖、哈希、审计和真实Chrome交互门禁通过。结果说明310/190附近并非孤立尖峰，但本轮不换参、也不晋级。
