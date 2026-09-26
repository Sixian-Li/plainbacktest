# 熊市事件组合：SMA200 滞回

本实验只在 12 段事后标注并收紧为峰值至谷底的熊市窗口内交易。它固定比较 `12+8`、`12+4`、`12+8+4` 三个股票池各自的“不筛选”等权持有和统一 SMA200 筛选路径；`Q` 完全排除。

正式语义、股票池、成本、2% 滞回、10% 状态成交锚锁定、次日共同 Open 成交、比例再分配和晋级/拒绝门槛都以 `experiment.json` 为准。熊市边界是事后可知的 oracle 输入，因此结果只用于机制研究，不能直接解释为实时择时业绩。

从 `backtest/` 运行：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios
.venv/bin/python -m scripts.run_bear_event_sma_portfolios --experiment experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios --run-id <RUN_ID> --symbol BEAR_EVENT_PORTFOLIOS --cost-bps 0
.venv/bin/python -m scripts.run_bear_event_sma_portfolios --experiment experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios --run-id <RUN_ID> --symbol BEAR_EVENT_PORTFOLIOS --cost-bps 5
.venv/bin/python -m scripts.analyze_bear_event_sma_portfolios --experiment experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios --run-id <RUN_ID>
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios --run-id <RUN_ID>
```
