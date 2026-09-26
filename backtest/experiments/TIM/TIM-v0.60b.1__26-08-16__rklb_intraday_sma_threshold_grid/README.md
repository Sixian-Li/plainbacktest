# RKLB 日内动态 SMA 双阈值网格

正式定义见 `experiment.json`。本实验扫描 SMA 15–115（步长 2）以及独立的买入/卖出 1%、2%、3% 阈值，共 459 个 case；所有 case 使用共同评估起点。

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid
.venv/bin/python -m scripts.run_rklb_intraday_sma_grid --experiment experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid --run-id RUN_ID --symbol RKLB --cost-bps 0
.venv/bin/python -m scripts.run_rklb_intraday_sma_grid --experiment experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid --run-id RUN_ID --symbol RKLB --cost-bps 5
.venv/bin/python -m scripts.analyze_rklb_intraday_sma_grid --experiment experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid --run-id RUN_ID
```
