# qqq_intraday_sma200_forced_sell_sensitivity_v1

固定上一轮两组 QQQ SMA200 a/b，只拆分强制买回 c 与强制卖出 d，并在 `c=5%` 时扫描 `d=关闭、2.5%、5%、7.5%、10%、12.5%、15%`。

最新验证 run：`run_20260813T195219Z_e1c0afc8`。没有 d 通过触发数与三点宽平台门禁；d=2.5% 系统性恶化，d=5% 样本不足且两组 a/b 结论分歧，d≥7.5% 零触发。

## 保留候选

| 候选 | a | b | c | d | 定位 |
|---|---:|---:|---:|---:|---|
| `sma200_c5_doff` | 3.75% | -12.75% | 5% | 关闭 | 核心历史基线；后续固定迁移与前向观察优先使用 |
| `sma200_c5_d5` | 3.75% | -12.75% | 5% | 5% | 保险影子版；仅4次 d 事件，不视为已优化参数 |

`a=3.00%、b=-12.75%` 只保留为回撤诊断对照，不是第三套候选。两套候选的精确公式、成交时序、完整研发过程和跨标的复用协议见[日内动态 SMA200 双阈值策略研发复用手册](../../docs/intraday_sma200_research_playbook.md)。它们均未通过不可重调的前向检验，不是实盘推荐。

正式运行命令：

```bash
.venv/bin/python -m scripts.start_experiment_run --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity
.venv/bin/python -m scripts.run_intraday_sma200_forced_sell_sensitivity --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID --symbol QQQ --cost-bps 5
.venv/bin/python -m scripts.analyze_intraday_sma200_forced_sell_sensitivity --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID
.venv/bin/python -m scripts.validate_run --experiment experiments/TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity --run-id RUN_ID
```
