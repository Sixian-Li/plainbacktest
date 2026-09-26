# qqq_intraday_sma200_robust_selection_v1

QQQ 盘前可解 SMA200 双阈值的全历史稳健选参实验。固定 `c=d=5%` 与单边 5 bps，完整扫描 `a=-2%～10%`、`b=-25%～10%`（步长均为 0.25%）。

主指标是连续状态下 22 个滚动五年 CAGR 的第 25 百分位；17 个空仓十年重启、局部连通平台、PBO 和 DSR 是预先冻结的门禁。全历史 CAGR 与 Sharpe 单独完整展示并核验，但不改变主选参规则。

正式运行命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection
.venv/bin/python -m scripts.run_intraday_sma200_robust_selection --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma200_robust_selection --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection --run-id RUN_ID
```
