# qqq_intraday_sma_period_cross_grid_1999_2015_v1

`TIM-v0.50` 从 `TIM-v0.30a.1` 派生：把固定 SMA200 的百分比普通买卖线改为可独立选择的买入/卖出 SMA 周期，同时保留实际计成本成交价锚定的强制买回 `c` 与止损 `d` 状态机。

本轮只使用 QQQ 2000-12-18～2015-12-31 做探索性研究。买入和卖出 SMA 均扫描80～450日、步长10；`c=3/5/8/10%`，`d=关闭/3/5/8/10%`，合计28,880组。主指标是三个连续继承状态分段 CAGR 的最小值；每个 c/d 模式独立检查38×38二维高分平台、边界、普通交叉次数、止损主触发次数、PBO和DSR。全历史 CAGR、Sharpe和最大回撤冠军只作补充诊断。

普通信号和成交口径已冻结：开盘前用截至前一收盘的完成历史解出当日价格等于 provisional SMA 的精确线；普通买入要求从买入SMA下方向上穿越，普通卖出要求从卖出SMA上方向下穿越；跳空越线按 Open，否则按日内精确线，随后施加单边5 bps。每日最多一笔，初始空仓，全进全出，允许小数股，不融资。

最新验证 run：`run_20260814T190632Z_a8335cff`。20个纠错模式中有8个二维平台通过结构门禁，但全网格 PBO=75.54%，20个代表的有效试验数 DSR 全低于95%，因此不提名唯一周期组合。历史诊断较强的结构合格代表是 `c=3%、d关闭、买入SMA310/卖出SMA190`：三段最差 CAGR 6.337%，全历史 CAGR 10.022%、Sharpe 0.726、最大回撤 -23.04%；它只用于理解参数面，不能视为未来固定参数。

止损方面，`d=3%` 虽在大部分买卖周期对中有至少10次主触发，但对四个 c 的三段最差 CAGR 配对中位影响全部为负；`d≥5%` 又普遍触发不足。这个结果支持保留 d 关闭作为清晰基线，而不是从本轮重新优化 d。

正式运行命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015
.venv/bin/python -m scripts.run_intraday_sma_period_cross_grid --experiment experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015 --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma_period_cross_grid --experiment experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015 --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015 --run-id RUN_ID
```

任何历史门禁通过都只能提名一个未来锁定验证区域，不能从本轮直接声称未来最优参数。
