# TIM · 简单择时与熊市对冲 · 策略演化史

> 本文件由 `scripts.build_research_catalog` 自动生成，请勿手工修改。修改原因与策略变化只写入 `lineage.json`；参数差异来自父子 `experiment.json`，指标来自当前代表 run。

导航：[总策略演化史](../strategy_evolution.md) · [实验登记册](../index.md) · [研究谱系图](../research_map.html)

指标只用于还原研究轨迹，不把样本内结果升级为样本外证据。`completed_unvalidated` 会原样显示；确定性门禁失败和已清理的会话中断不成为研究节点，其精简历史由 `research_events.jsonl` 记录。

## TIM · 简单择时与熊市对冲

研究 QQQ/SPY 的简单趋势择时、熊市识别和未来空仓期对冲配置。

### 起点 · TIM-v0.05 · QQQ 人工择时路径核算

- 策略：从 2026-01-16 收盘时已经持有的 100 股 QQQ 出发，严格按照用户给出的日期，在每个所列交易日的复权收盘价全仓卖出或把全部现金买回；日期序列本身不由行情生成。
- 代表结果：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1/experiment.json)

### 起点 · TIM-v0.10 · QQQ/SPY 收盘确认 SMA200 双阈值

- 策略：After a 200-session SMA warmup, enter only when the close crosses from at-or-below the upper SMA buffer to above it; exit whenever the close is below the lower SMA buffer. Signals are confirmed after the regular-session close and filled at the next regular-session open.
- 代表结果：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json)

### TIM-v0.10 → TIM-v0.20a.1 · QQQ 日内动态 SMA200 网格（2021–2025）

- 关系：`evolves_to`
- 为什么改：收盘确认、次日开盘执行可能错过阈值触发，需要测试盘前可解的动态日内成交语义。
- 策略修改：从 QQQ/SPY 收盘穿越改为 QQQ 动态日内 SMA200 阈值；a/b 扩到 -20%～20%，加入 c/d=5% 或 10% 的纠错情景和 0/5 bps 成本。
- 修改前：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json) · [子实验](../TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json)
- 自动配置差异：

  - `parameters.a_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.a_pct_range.start`：`"<未设置>"` → `-20.0`
  - `parameters.a_pct_range.step`：`"<未设置>"` → `0.25`
  - `parameters.a_pct_range.stop`：`"<未设置>"` → `20.0`
  - `parameters.analysis_end`：`"<未设置>"` → `"2025-12-31"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2021-01-04"`
  - `parameters.b_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.b_pct_range.start`：`"<未设置>"` → `-20.0`
  - `parameters.b_pct_range.step`：`"<未设置>"` → `0.25`
  - `parameters.b_pct_range.stop`：`"<未设置>"` → `20.0`
  - `parameters.combination_count_per_cost`：`"<未设置>"` → `77763`
  - `parameters.combination_count_per_mode`：`"<未设置>"` → `25921`
  - `parameters.combination_count_per_surface`：`1089` → `"<未设置>"`
  - `parameters.correction_constraint`：`"<未设置>"` → `"c=d within each enabled scenario"`
  - `parameters.correction_scenarios_pct`：`"<未设置>"` → `[null,5.0,10.0]`
  - `parameters.max_trades_per_session`：`"<未设置>"` → `1`
  - `parameters.requested_interval`：`"<未设置>"` → `"2021-01-01 inclusive to 2026-01-01 exclusive; normalized to available QQQ sessions"`
  - `parameters.total_case_count_all_costs`：`"<未设置>"` → `155526`
  - `strategy.buy_rule`：`"flat and previous_close <= previous_sma200 * (1 + b_pct / 100) and close > sma200 * (1 + b_pct / 100)"` → `"Flat ordinary entry: prior_close <= prior_sma200*(1+b/100), then buy if current regular-session price reaches P where P=(1+b/100)*(sum of prior 199 closes + P)/200. If correction…`
  - `strategy.description`：`"After a 200-session SMA warmup, enter only when the close crosses from at-or-below the upper SMA buffer to above it; exit whenever the close is below the lower SMA buffer. Signal…` → `"SMA200 uses completed history through the prior close to solve the exact next-session price at which the provisional current SMA200 reaches each a/b buffer. Start flat. When flat…`
  - `strategy.execution_time`：`"next regular-session open"` → `"If the regular-session Open has already crossed a predeclared trigger, fill at Open; otherwise fill at the exact theoretical trigger when the adjusted regular-session OHLC touche…`
  - `strategy.first_valid_sma_bar_can_enter`：`false` → `"<未设置>"`
  - `strategy.name`：`"sma200_asymmetric_threshold"` → `"intraday_dynamic_sma200_asymmetric_threshold_with_optional_corrections"`
  - `strategy.positioning`：`"long-only, all cash or all invested, fractional shares"` → `"long-only; start with 100,000 USD cash; every buy invests all cash and every sell liquidates all shares; fractional shares; no financing; cash earns zero"`
  - ……另有 3 项，完整定义见父子 `experiment.json`。

### TIM-v0.20a.1 → TIM-v0.20a.2 · QQQ 日内动态 SMA200 历史窗口（2000–2004）

- 关系：`tests_on_window`
- 为什么改：检查 2021–2025 选出的参数面形状能否在早期高波动市场重现。
- 策略修改：策略、完整网格、纠错模式和成本全部不变，只把分析窗口改为 2000-01-03～2004-12-31。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json) · [子实验](../TIM/TIM-v0.20a.2__26-08-13__qqq_intraday_sma200_threshold_grid_2000_2004/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2025-12-31"` → `"2004-12-31"`
  - `parameters.analysis_start`：`"2021-01-04"` → `"2000-01-03"`
  - `parameters.requested_interval`：`"2021-01-01 inclusive to 2026-01-01 exclusive; normalized to available QQQ sessions"` → `"2000-01-01 inclusive to 2005-01-01 exclusive; normalized to available QQQ sessions"`

### TIM-v0.20a.1 → TIM-v0.20a.3 · QQQ 日内动态 SMA200 历史窗口（2010–2014）

- 关系：`tests_on_window`
- 为什么改：增加一个中期历史窗口，辨别参数表现是普遍结构还是特定时期现象。
- 策略修改：策略、完整网格、纠错模式和成本全部不变，只把分析窗口改为 2010-01-04～2014-12-31。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json) · [子实验](../TIM/TIM-v0.20a.3__26-08-13__qqq_intraday_sma200_threshold_grid_2010_2014/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2025-12-31"` → `"2014-12-31"`
  - `parameters.analysis_start`：`"2021-01-04"` → `"2010-01-04"`
  - `parameters.requested_interval`：`"2021-01-01 inclusive to 2026-01-01 exclusive; normalized to available QQQ sessions"` → `"2010-01-01 inclusive to 2015-01-01 exclusive; normalized to available QQQ sessions"`

### TIM-v0.20a.1 → TIM-v0.30 · QQQ SMA200 多窗口稳健选参

- 关系：`informs`
- 为什么改：近期窗口提供候选参数面，但不能单独决定稳健参数，需要与长期滚动检验合并。
- 策略修改：把近期网格作为输入之一，转入连续历史、滚动窗、重启窗、平台、PBO 和 DSR 的联合稳健选择。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json) · [子实验](../TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.a_pct_range.start`：`-20.0` → `-2.0`
  - `parameters.a_pct_range.stop`：`20.0` → `10.0`
  - `parameters.analysis_start`：`"2021-01-04"` → `"2000-01-03"`
  - `parameters.b_pct_range.start`：`-20.0` → `-25.0`
  - `parameters.b_pct_range.stop`：`20.0` → `10.0`
  - `parameters.combination_count`：`"<未设置>"` → `6909`
  - `parameters.combination_count_per_cost`：`77763` → `"<未设置>"`
  - `parameters.combination_count_per_mode`：`25921` → `"<未设置>"`
  - `parameters.correction_constraint`：`"c=d within each enabled scenario"` → `"c=d=5% fixed; not optimized"`
  - `parameters.correction_pct`：`"<未设置>"` → `5.0`
  - `parameters.correction_scenarios_pct`：`[null,5.0,10.0]` → `"<未设置>"`
  - `parameters.dsr.candidate`：`"<未设置>"` → `"the single stable representative chosen before DSR is evaluated"`
  - `parameters.dsr.conservative_historical_trial_upper_bound`：`"<未设置>"` → `473487`
  - `parameters.dsr.historical_count_basis`：`"<未设置>"` → `"three prior 155,526-case a/b/cost scans plus the current 6,909-case grid; deliberately conservative and not de-duplicated"`
  - `parameters.dsr.minimum_probability`：`"<未设置>"` → `0.95`
  - `parameters.dsr.primary_trial_count`：`"<未设置>"` → `"correlation-adjusted effective number of identifiable strategies using 12-block log-return vectors"`
  - `parameters.dsr.reported_sensitivities`：`"<未设置>"` → `["effective correlated trials","all 6,909 current-grid trials","473,487 conservative historical trial upper bound"]`
  - `parameters.final_decision`：`"<未设置>"` → `"The unique representative passes only if the plateau span/boundary test, PBO<=20%, and effective-trials DSR>=95% all pass. If any fails, conclude that no credible fixed a/b has y…`
  - `parameters.identifiability_gate.minimum_buy_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.minimum_order_count`：`"<未设置>"` → `20`
  - `parameters.identifiability_gate.minimum_sell_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.purpose`：`"<未设置>"` → `"Exclude one-entry hold artifacts and cases in which the ordinary a/b rules were not meaningfully exercised. Correction counts are reported but not gated."`
  - `parameters.pbo.block_count`：`"<未设置>"` → `12`
  - ……另有 44 项，完整定义见父子 `experiment.json`。

### TIM-v0.20a.2 → TIM-v0.30 · QQQ SMA200 多窗口稳健选参

- 关系：`informs`
- 为什么改：早期窗口揭示不同参数面和回撤环境，必须纳入联合选择以惩罚时期依赖。
- 策略修改：把 2000–2004 的同规格参数面加入多窗口稳健评分，不从该窗口单独选取最高点。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.2__26-08-13__qqq_intraday_sma200_threshold_grid_2000_2004/experiment.json) · [子实验](../TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.a_pct_range.start`：`-20.0` → `-2.0`
  - `parameters.a_pct_range.stop`：`20.0` → `10.0`
  - `parameters.analysis_end`：`"2004-12-31"` → `"2025-12-31"`
  - `parameters.b_pct_range.start`：`-20.0` → `-25.0`
  - `parameters.b_pct_range.stop`：`20.0` → `10.0`
  - `parameters.combination_count`：`"<未设置>"` → `6909`
  - `parameters.combination_count_per_cost`：`77763` → `"<未设置>"`
  - `parameters.combination_count_per_mode`：`25921` → `"<未设置>"`
  - `parameters.correction_constraint`：`"c=d within each enabled scenario"` → `"c=d=5% fixed; not optimized"`
  - `parameters.correction_pct`：`"<未设置>"` → `5.0`
  - `parameters.correction_scenarios_pct`：`[null,5.0,10.0]` → `"<未设置>"`
  - `parameters.dsr.candidate`：`"<未设置>"` → `"the single stable representative chosen before DSR is evaluated"`
  - `parameters.dsr.conservative_historical_trial_upper_bound`：`"<未设置>"` → `473487`
  - `parameters.dsr.historical_count_basis`：`"<未设置>"` → `"three prior 155,526-case a/b/cost scans plus the current 6,909-case grid; deliberately conservative and not de-duplicated"`
  - `parameters.dsr.minimum_probability`：`"<未设置>"` → `0.95`
  - `parameters.dsr.primary_trial_count`：`"<未设置>"` → `"correlation-adjusted effective number of identifiable strategies using 12-block log-return vectors"`
  - `parameters.dsr.reported_sensitivities`：`"<未设置>"` → `["effective correlated trials","all 6,909 current-grid trials","473,487 conservative historical trial upper bound"]`
  - `parameters.final_decision`：`"<未设置>"` → `"The unique representative passes only if the plateau span/boundary test, PBO<=20%, and effective-trials DSR>=95% all pass. If any fails, conclude that no credible fixed a/b has y…`
  - `parameters.identifiability_gate.minimum_buy_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.minimum_order_count`：`"<未设置>"` → `20`
  - `parameters.identifiability_gate.minimum_sell_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.purpose`：`"<未设置>"` → `"Exclude one-entry hold artifacts and cases in which the ordinary a/b rules were not meaningfully exercised. Correction counts are reported but not gated."`
  - `parameters.pbo.block_count`：`"<未设置>"` → `12`
  - ……另有 44 项，完整定义见父子 `experiment.json`。

### TIM-v0.20a.3 → TIM-v0.30 · QQQ SMA200 多窗口稳健选参

- 关系：`informs`
- 为什么改：中期窗口几乎退化为买入持有，作为参数失效证据约束联合代表选择。
- 策略修改：把 2010–2014 的同规格参数面加入多窗口稳健评分，并保留其低成交、参数退化特征。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.3__26-08-13__qqq_intraday_sma200_threshold_grid_2010_2014/experiment.json) · [子实验](../TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.a_pct_range.start`：`-20.0` → `-2.0`
  - `parameters.a_pct_range.stop`：`20.0` → `10.0`
  - `parameters.analysis_end`：`"2014-12-31"` → `"2025-12-31"`
  - `parameters.analysis_start`：`"2010-01-04"` → `"2000-01-03"`
  - `parameters.b_pct_range.start`：`-20.0` → `-25.0`
  - `parameters.b_pct_range.stop`：`20.0` → `10.0`
  - `parameters.combination_count`：`"<未设置>"` → `6909`
  - `parameters.combination_count_per_cost`：`77763` → `"<未设置>"`
  - `parameters.combination_count_per_mode`：`25921` → `"<未设置>"`
  - `parameters.correction_constraint`：`"c=d within each enabled scenario"` → `"c=d=5% fixed; not optimized"`
  - `parameters.correction_pct`：`"<未设置>"` → `5.0`
  - `parameters.correction_scenarios_pct`：`[null,5.0,10.0]` → `"<未设置>"`
  - `parameters.dsr.candidate`：`"<未设置>"` → `"the single stable representative chosen before DSR is evaluated"`
  - `parameters.dsr.conservative_historical_trial_upper_bound`：`"<未设置>"` → `473487`
  - `parameters.dsr.historical_count_basis`：`"<未设置>"` → `"three prior 155,526-case a/b/cost scans plus the current 6,909-case grid; deliberately conservative and not de-duplicated"`
  - `parameters.dsr.minimum_probability`：`"<未设置>"` → `0.95`
  - `parameters.dsr.primary_trial_count`：`"<未设置>"` → `"correlation-adjusted effective number of identifiable strategies using 12-block log-return vectors"`
  - `parameters.dsr.reported_sensitivities`：`"<未设置>"` → `["effective correlated trials","all 6,909 current-grid trials","473,487 conservative historical trial upper bound"]`
  - `parameters.final_decision`：`"<未设置>"` → `"The unique representative passes only if the plateau span/boundary test, PBO<=20%, and effective-trials DSR>=95% all pass. If any fails, conclude that no credible fixed a/b has y…`
  - `parameters.identifiability_gate.minimum_buy_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.minimum_order_count`：`"<未设置>"` → `20`
  - `parameters.identifiability_gate.minimum_sell_sma_count`：`"<未设置>"` → `5`
  - `parameters.identifiability_gate.purpose`：`"<未设置>"` → `"Exclude one-entry hold artifacts and cases in which the ordinary a/b rules were not meaningfully exercised. Correction counts are reported but not gated."`
  - ……另有 45 项，完整定义见父子 `experiment.json`。

### TIM-v0.30 → TIM-v0.30a.1 · QQQ SMA200 强制卖出敏感性

- 关系：`robustness_check_of`
- 为什么改：联合选择仍包含纠错机制，需要隔离强制卖出 d 与强制买回 c 的贡献和事件样本量。
- 策略修改：冻结两组代表 a/b，独立扫描 c/d 组合形成 18 个 case，并增加触发归因、S5/S10 和宽平台门禁。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json) · [子实验](../TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity/experiment.json)
- 自动配置差异：

  - `parameters.a_pct_range.start`：`-2.0` → `"<未设置>"`
  - `parameters.a_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.a_pct_range.stop`：`10.0` → `"<未设置>"`
  - `parameters.b_pct_range.start`：`-25.0` → `"<未设置>"`
  - `parameters.b_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.b_pct_range.stop`：`10.0` → `"<未设置>"`
  - `parameters.causal_ablation`：`"<未设置>"` → `[{"c_pct":null,"d_pct":null},{"c_pct":5.0,"d_pct":null},{"c_pct":null,"d_pct":5.0},{"c_pct":5.0,"d_pct":5.0}]`
  - `parameters.combination_count`：`6909` → `18`
  - `parameters.correction_constraint`：`"c=d=5% fixed; not optimized"` → `"<未设置>"`
  - `parameters.correction_pct`：`5.0` → `"<未设置>"`
  - `parameters.d_sweep_pct`：`"<未设置>"` → `[null,2.5,5.0,7.5,10.0,12.5,15.0]`
  - `parameters.d_sweep_with_c_fixed_pct`：`"<未设置>"` → `5.0`
  - `parameters.dsr.candidate`：`"the single stable representative chosen before DSR is evaluated"` → `"<未设置>"`
  - `parameters.dsr.conservative_historical_trial_upper_bound`：`473487` → `"<未设置>"`
  - `parameters.dsr.historical_count_basis`：`"three prior 155,526-case a/b/cost scans plus the current 6,909-case grid; deliberately conservative and not de-duplicated"` → `"<未设置>"`
  - `parameters.dsr.minimum_probability`：`0.95` → `"<未设置>"`
  - `parameters.dsr.primary_trial_count`：`"correlation-adjusted effective number of identifiable strategies using 12-block log-return vectors"` → `"<未设置>"`
  - `parameters.dsr.reported_sensitivities`：`["effective correlated trials","all 6,909 current-grid trials","473,487 conservative historical trial upper bound"]` → `"<未设置>"`
  - `parameters.event_attribution.forward_sessions`：`"<未设置>"` → `[20,60]`
  - `parameters.event_attribution.paired_counterfactual`：`"<未设置>"` → `"For each a/b and d-sweep case, compare the saved daily equity with its c=5,d=disabled control; report terminal, CAGR, Sharpe and maximum-drawdown deltas. This is a whole-path pai…`
  - `parameters.event_attribution.path_metrics`：`"<未设置>"` → `"For every primary SELL_CORRECTION, report the minimum QQQ Low and final Close relative to the raw sell fill over the next 20 and 60 available sessions beginning on the next tradi…`
  - `parameters.final_decision`：`"The unique representative passes only if the plateau span/boundary test, PBO<=20%, and effective-trials DSR>=95% all pass. If any fails, conclude that no credible fixed a/b has y…` → `"<未设置>"`
  - `parameters.fixed_ab_pairs`：`"<未设置>"` → `[{"a_pct":3.75,"b_pct":-12.75,"pair_id":"primary","purpose":"上一轮 S5 冠军及冻结观察候选"},{"a_pct":3.0,"b_pct":-12.75,"pair_id":"drawdown_reference","purpose":"上一轮最大回撤较低的预登记对照"}]`
  - `parameters.identifiability_gate.minimum_buy_sma_count`：`5` → `"<未设置>"`
  - ……另有 45 项，完整定义见父子 `experiment.json`。

### TIM-v0.30 → TIM-v0.40 · 熊市事件 SMA200 滞回组合

- 关系：`extends_to_bear_portfolios`
- 为什么改：单指数 SMA200 研究只能回答 QQQ 自身的进出场，仍需检查同类趋势过滤能否在已标注熊市内改善候选对冲组合。
- 策略修改：从单标的动态阈值扩展到三个固定候选池；只在 12 段事后熊市内交易，比较不筛选持有与 SMA200±2% 滞回、成交锚±10% 解锁路径，并加入 0/5 bps 多标的双账本。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR_EVENT_PORTFOLIOS；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json) · [子实验](../TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[5]` → `[0,5]`
  - `parameters.a_pct_range.start`：`-2.0` → `"<未设置>"`
  - `parameters.a_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.a_pct_range.stop`：`10.0` → `"<未设置>"`
  - `parameters.analysis_end`：`"2025-12-31"` → `"2026-03-31"`
  - `parameters.analysis_start`：`"2000-01-03"` → `"2000-03-24"`
  - `parameters.b_pct_range.start`：`-25.0` → `"<未设置>"`
  - `parameters.b_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.b_pct_range.stop`：`10.0` → `"<未设置>"`
  - `parameters.bear_interval_source`：`"<未设置>"` → `"research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json"`
  - `parameters.combination_count`：`6909` → `"<未设置>"`
  - `parameters.common_calendar_policy`：`"<未设置>"` → `"以SPY日历为基础；标的上市前缺席不影响日历，任何池内标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"`
  - `parameters.core12`：`"<未设置>"` → `["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]`
  - `parameters.correction_constraint`：`"c=d=5% fixed; not optimized"` → `"<未设置>"`
  - `parameters.correction_pct`：`5.0` → `"<未设置>"`
  - `parameters.dsr.candidate`：`"the single stable representative chosen before DSR is evaluated"` → `"<未设置>"`
  - `parameters.dsr.conservative_historical_trial_upper_bound`：`473487` → `"<未设置>"`
  - `parameters.dsr.historical_count_basis`：`"three prior 155,526-case a/b/cost scans plus the current 6,909-case grid; deliberately conservative and not de-duplicated"` → `"<未设置>"`
  - `parameters.dsr.minimum_probability`：`0.95` → `"<未设置>"`
  - `parameters.dsr.primary_trial_count`：`"correlation-adjusted effective number of identifiable strategies using 12-block log-return vectors"` → `"<未设置>"`
  - `parameters.dsr.reported_sensitivities`：`["effective correlated trials","all 6,909 current-grid trials","473,487 conservative historical trial upper bound"]` → `"<未设置>"`
  - `parameters.filter_modes`：`"<未设置>"` → `["no_filter","sma200_hysteresis"]`
  - `parameters.final_decision`：`"The unique representative passes only if the plateau span/boundary test, PBO<=20%, and effective-trials DSR>=95% all pass. If any fails, conclude that no credible fixed a/b has y…` → `"<未设置>"`
  - `parameters.formal_cases`：`"<未设置>"` → `["core12_near8__no_filter","core12_near8__sma200_hysteresis","core12_retail4__no_filter","core12_retail4__sma200_hysteresis","core12_near8_retail4__no_filter","core12_near8_retail…`
  - ……另有 70 项，完整定义见父子 `experiment.json`。

### TIM-v0.10 → TIM-v0.60a.1 · RKLB Stochastic RSI 阈值与周期网格

- 关系：`branches_from`
- 为什么改：检验不依赖价格长期均线的归一化动量振荡指标，是否能在已批准的 RKLB 历史中形成可解释的全仓择时响应。
- 策略修改：标的改为 RKLB；指标改为未平滑 Stochastic RSI；扫描 period 10～130（步长3）以及卖出 1.0/0.8、买入 0.0/0.2 的四种阈值组合，保持收盘信号、次日开盘全仓成交和单边5bps成本。
- 修改前：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：RKLB；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json) · [子实验](../TIM/TIM-v0.60a.1__26-08-16__rklb_stochrsi_threshold_grid/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.a_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.b_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.buy_threshold`：`"<未设置>"` → `[0.0,0.2]`
  - `parameters.combination_count`：`"<未设置>"` → `164`
  - `parameters.combination_count_per_surface`：`1089` → `"<未设置>"`
  - `parameters.period`：`"<未设置>"` → `[10,13,16,19,22,25,28,31,34,37,40,43,46,49,52,55,58,61,64,67,70,73,76,79,82,85,88,91,94,97,100,103,106,109,112,115,118,121,124,127,130]`
  - `parameters.rsi_method`：`"<未设置>"` → `"Wilder"`
  - `parameters.sell_threshold`：`"<未设置>"` → `[0.8,1.0]`
  - `parameters.stochrsi_smoothing`：`"<未设置>"` → `"none"`
  - `strategy.buy_rule`：`"flat and previous_close <= previous_sma200 * (1 + b_pct / 100) and close > sma200 * (1 + b_pct / 100)"` → `"flat and raw StochRSI <= buy_threshold (inclusive level condition)"`
  - `strategy.comparison_window`：`"<未设置>"` → `"all cases start on the first date where period 130 has a complete StochRSI warmup"`
  - `strategy.description`：`"After a 200-session SMA warmup, enter only when the close crosses from at-or-below the upper SMA buffer to above it; exit whenever the close is below the lower SMA buffer. Signal…` → `"Compute unsmoothed Stochastic RSI from adjusted RKLB daily closes. The same period is used for Wilder RSI and for the rolling RSI minimum/maximum range. Starting flat, buy when t…`
  - `strategy.first_valid_sma_bar_can_enter`：`false` → `"<未设置>"`
  - `strategy.indicator_definition`：`"<未设置>"` → `"Wilder RSI(period), then (RSI - rolling_min(RSI, period)) / (rolling_max(RSI, period) - rolling_min(RSI, period)); zero range maps to 0.5; no K/D smoothing"`
  - `strategy.initial_position`：`"<未设置>"` → `"flat"`
  - `strategy.name`：`"sma200_asymmetric_threshold"` → `"rklb_raw_stochrsi_threshold_timing"`
  - `strategy.positioning`：`"long-only, all cash or all invested, fractional shares"` → `"long-only, all cash or all invested, fractional shares, no pyramiding"`
  - `strategy.sell_rule`：`"long and close < sma200 * (1 - a_pct / 100)"` → `"long and raw StochRSI >= sell_threshold (inclusive level condition)"`
  - `strategy.signal_time`：`"regular-session close after the bar is complete"` → `"regular-session close after the daily bar is complete"`
  - `strategy.sma_window`：`200` → `"<未设置>"`
  - `symbols`：`["QQQ","SPY"]` → `["RKLB"]`

### TIM-v0.60a.1 → TIM-v0.70 · QQQ 双周期 Stochastic RSI 三种择时语义

- 关系：`fixes_periods_and_compares_signal_semantics_of`
- 为什么改：RKLB 网格只比较单周期收盘状态阈值，无法回答短长两个振荡周期共同确认时，持续状态、精确极值和反向穿越三种语义的差别。
- 策略修改：标的改为 QQQ 并固定 StochRSI 42/100；新增三组配对买卖语义、盘前精确解价和仅 Open-to-Close 触线成交，同时加入 Buy & Hold、SMA200 正负3%和0/5 bps对照。
- 修改前：RKLB；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.60a.1__26-08-16__rklb_stochrsi_threshold_grid/experiment.json) · [子实验](../TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[5]` → `[0,5]`
  - `parameters.analysis_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2020-01-02"`
  - `parameters.buy_threshold`：`[0.0,0.2]` → `0.2`
  - `parameters.combination_count`：`164` → `"<未设置>"`
  - `parameters.combination_count_per_cost`：`"<未设置>"` → `3`
  - `parameters.gap_policy`：`"<未设置>"` → `"fill_at_open_when_open_is_already_beyond_trigger"`
  - `parameters.intraday_path`：`"<未设置>"` → `"directed_open_to_close_only"`
  - `parameters.period`：`[10,13,16,19,22,25,28,31,34,37,40,43,46,49,52,55,58,61,64,67,70,73,76,79,82,85,88,91,94,97,100,103,106,109,112,115,118,121,124,127,130]` → `"<未设置>"`
  - `parameters.rsi_method`：`"Wilder"` → `"<未设置>"`
  - `parameters.sell_threshold`：`[0.8,1.0]` → `0.8`
  - `parameters.sma_baseline_buy_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.sma_baseline_sell_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.sma_baseline_window`：`"<未设置>"` → `200`
  - `parameters.stochrsi_periods`：`"<未设置>"` → `[42,100]`
  - `parameters.stochrsi_smoothing`：`"none"` → `"<未设置>"`
  - `parameters.strategy_modes`：`"<未设置>"` → `["LEVEL","EXTREME","CROSS"]`
  - `strategy.buy_rule`：`"flat and raw StochRSI <= buy_threshold (inclusive level condition)"` → `"LEVEL: flat and both provisional StochRSI(42) and StochRSI(100) become < 0.2 on a downward Open-to-Close path. EXTREME: flat and both become 0 on a downward path. CROSS: flat, bo…`
  - `strategy.comparison_window`：`"all cases start on the first date where period 130 has a complete StochRSI warmup"` → `"<未设置>"`
  - `strategy.description`：`"Compute unsmoothed Stochastic RSI from adjusted RKLB daily closes. The same period is used for Wilder RSI and for the rolling RSI minimum/maximum range. Starting flat, buy when t…` → `"Use raw unsmoothed Stochastic RSI with periods 42 and 100 as a joint all-in/all-out QQQ timing signal. Compare three paired semantics: joint oversold/overbought level states, joi…`
  - `strategy.execution_time`：`"next regular-session open"` → `"Same regular session: Open when it has already gapped through the predeclared boundary; otherwise the exact boundary price only when the directed Open-to-Close segment reaches it…`
  - `strategy.indicator_definition`：`"Wilder RSI(period), then (RSI - rolling_min(RSI, period)) / (rolling_max(RSI, period) - rolling_min(RSI, period)); zero range maps to 0.5; no K/D smoothing"` → `"For each period p, Wilder RSI(p) is normalized by the rolling p-value minimum and maximum of that same RSI series; a zero range maps to 0.5; no K or D smoothing."`
  - `strategy.initial_position`：`"flat"` → `"flat for all timing strategies and the SMA200 baseline; Buy & Hold enters at the first analysis-session Open"`
  - `strategy.name`：`"rklb_raw_stochrsi_threshold_timing"` → `"qqq_dual_raw_stochrsi_intraday_timing"`
  - ……另有 9 项，完整定义见父子 `experiment.json`。

### TIM-v0.70 → TIM-v0.70a.1 · QQQ 双周期 Stochastic RSI CROSS 全历史阈值网格

- 关系：`expands_window_and_scans_thresholds_of`
- 为什么改：父实验显示固定0.20/0.80的双周期CROSS在2020年以后表现最好，但短窗口无法判断结果来自宽阈值区域还是偶然单点。
- 策略修改：只保留双周期CROSS语义，将QQQ观察窗扩展为2000-01-03至2026-08-04，并对买线0.00至0.40、卖线0.60至1.00按0.01做1,681组笛卡尔网格；成交、仓位和0/5 bps口径不变。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing/experiment.json) · [子实验](../TIM/TIM-v0.70a.1__26-08-24__qqq_stochrsi_cross_grid_full_history/experiment.json)
- 自动配置差异：

  - `parameters.analysis_start`：`"2020-01-02"` → `"2000-01-03"`
  - `parameters.buy_threshold`：`0.2` → `"<未设置>"`
  - `parameters.buy_threshold_end`：`"<未设置>"` → `0.4`
  - `parameters.buy_threshold_start`：`"<未设置>"` → `0.0`
  - `parameters.combination_count_per_cost`：`3` → `1681`
  - `parameters.grid_semantics`：`"<未设置>"` → `"full_cartesian_product"`
  - `parameters.parent_anchor_buy_threshold`：`"<未设置>"` → `0.2`
  - `parameters.parent_anchor_sell_threshold`：`"<未设置>"` → `0.8`
  - `parameters.sell_threshold`：`0.8` → `"<未设置>"`
  - `parameters.sell_threshold_end`：`"<未设置>"` → `1.0`
  - `parameters.sell_threshold_start`：`"<未设置>"` → `0.6`
  - `parameters.strategy_modes`：`["LEVEL","EXTREME","CROSS"]` → `"<未设置>"`
  - `parameters.threshold_step`：`"<未设置>"` → `0.01`
  - `strategy.buy_rule`：`"LEVEL: flat and both provisional StochRSI(42) and StochRSI(100) become < 0.2 on a downward Open-to-Close path. EXTREME: flat and both become 0 on a downward path. CROSS: flat, bo…` → `"For each buy threshold b in {0.00, 0.01, ..., 0.40}, buy while flat only when completed prior StochRSI(42) < b and completed prior StochRSI(100) < b, then both provisional same-s…`
  - `strategy.description`：`"Use raw unsmoothed Stochastic RSI with periods 42 and 100 as a joint all-in/all-out QQQ timing signal. Compare three paired semantics: joint oversold/overbought level states, joi…` → `"Extend the parent CROSS strategy to QQQ history from 2000 through the approved 2026-08-04 endpoint. Raw unsmoothed Stochastic RSI periods 42 and 100 jointly confirm each event. W…`
  - `strategy.execution_time`：`"Same regular session: Open when it has already gapped through the predeclared boundary; otherwise the exact boundary price only when the directed Open-to-Close segment reaches it…` → `"On the same regular session, fill at Open if it has already gapped through the frozen joint boundary; otherwise fill at the exact boundary only when the directed Open-to-Close se…`
  - `strategy.initial_position`：`"flat for all timing strategies and the SMA200 baseline; Buy & Hold enters at the first analysis-session Open"` → `"flat for every grid case; Buy & Hold enters at the first analysis-session Open"`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_intraday_timing"` → `"qqq_dual_raw_stochrsi_cross_threshold_grid"`
  - `strategy.plain_language.buy`：`"两个周期都进入低位时买入；三种版本分别在进入低位、同时创滚动低点或从低位向上恢复时行动。"` → `"空仓时，两个周期前一天都在候选低位线下方，并在当天一起向上越过这条线才买入。"`
  - `strategy.plain_language.execution`：`"每天开盘前用前一日及更早数据算好触发价；跳空越过就按开盘价成交，否则只有开盘到收盘的价格路径碰到触发价才按该价成交。"` → `"每天开盘前用前一日及更早数据算好触发价；跳空越过就按开盘价成交，否则只有开盘到收盘的价格路径碰到触发价才成交。"`
  - `strategy.plain_language.position`：`"策略只在满仓 QQQ 与持有现金之间切换，并与买入持有、SMA200 正负3%择时和报告中的等额定投情景比较。"` → `"每组阈值都独立在满仓 QQQ 和现金之间切换，并与同起点买入持有比较；闲置现金不计利息。"`
  - `strategy.plain_language.sell`：`"两个周期都进入高位时卖出；三种版本分别在进入高位、同时创滚动高点或从高位向下回落时行动。"` → `"持仓时，两个周期前一天都在候选高位线上方，并在当天一起向下跌破这条线才卖出。"`
  - `strategy.plain_language.summary`：`"比较两个不同速度的 StochRSI 同时确认超买超卖时，三种进出场解释能否改善 QQQ 的持有体验。"` → `"观察双周期 StochRSI 从低位共同回升、从高位共同回落的择时规则在 QQQ 长历史上是否存在稳定阈值区域。"`
  - `strategy.sell_rule`：`"LEVEL: long and both provisional StochRSI(42) and StochRSI(100) become > 0.8 on an upward Open-to-Close path. EXTREME: long and both become 1 on an upward path. CROSS: long, both…` → `"For each sell threshold s in {0.60, 0.61, ..., 1.00}, sell while long only when completed prior StochRSI(42) > s and completed prior StochRSI(100) > s, then both provisional same…`
  - ……另有 1 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.1 → TIM-v0.70a.2 · QQQ 双周期 Stochastic RSI CROSS 周期网格

- 关系：`fixes_thresholds_and_scans_periods_of`
- 为什么改：阈值网格显示0.20/0.80附近的最高点较窄，还需独立判断父实验42/100周期是否处于宽周期平台。
- 策略修改：固定CROSS买线0.20与卖线0.80，短周期扫描14至50、长周期扫描70至160且步长均为2，共874组；QQQ窗口、成交、仓位、基准和0/5 bps口径不变。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.1__26-08-24__qqq_stochrsi_cross_grid_full_history/experiment.json) · [子实验](../TIM/TIM-v0.70a.2__26-08-25__qqq_stochrsi_period_grid_full_history/experiment.json)
- 自动配置差异：

  - `parameters.buy_threshold`：`"<未设置>"` → `0.2`
  - `parameters.buy_threshold_end`：`0.4` → `"<未设置>"`
  - `parameters.buy_threshold_start`：`0.0` → `"<未设置>"`
  - `parameters.combination_count_per_cost`：`1681` → `874`
  - `parameters.long_period_end`：`"<未设置>"` → `160`
  - `parameters.long_period_start`：`"<未设置>"` → `70`
  - `parameters.parent_anchor_buy_threshold`：`0.2` → `"<未设置>"`
  - `parameters.parent_anchor_long_period`：`"<未设置>"` → `100`
  - `parameters.parent_anchor_sell_threshold`：`0.8` → `"<未设置>"`
  - `parameters.parent_anchor_short_period`：`"<未设置>"` → `42`
  - `parameters.period_step`：`"<未设置>"` → `2`
  - `parameters.sell_threshold`：`"<未设置>"` → `0.8`
  - `parameters.sell_threshold_end`：`1.0` → `"<未设置>"`
  - `parameters.sell_threshold_start`：`0.6` → `"<未设置>"`
  - `parameters.short_period_end`：`"<未设置>"` → `50`
  - `parameters.short_period_start`：`"<未设置>"` → `14`
  - `parameters.stochrsi_periods`：`[42,100]` → `"<未设置>"`
  - `parameters.threshold_step`：`0.01` → `"<未设置>"`
  - `strategy.buy_rule`：`"For each buy threshold b in {0.00, 0.01, ..., 0.40}, buy while flat only when completed prior StochRSI(42) < b and completed prior StochRSI(100) < b, then both provisional same-s…` → `"For every short period in {14,16,...,50} and long period in {70,72,...,160}, buy while flat only when both completed prior StochRSI values are < 0.20 and both provisional same-se…`
  - `strategy.description`：`"Extend the parent CROSS strategy to QQQ history from 2000 through the approved 2026-08-04 endpoint. Raw unsmoothed Stochastic RSI periods 42 and 100 jointly confirm each event. W…` → `"Keep the parent dual-StochRSI CROSS thresholds fixed at buy 0.20 and sell 0.80 while independently scanning one short and one long raw unsmoothed Stochastic RSI period. While fla…`
  - `strategy.indicator_definition`：`"For each period p, Wilder RSI(p) is normalized by the rolling p-value minimum and maximum of that same RSI series; a zero range maps to 0.5; no K or D smoothing."` → `"For each candidate period p, Wilder RSI(p) is normalized by the rolling p-value minimum and maximum of that same RSI series; a zero range maps to 0.5; no K or D smoothing."`
  - `strategy.initial_position`：`"flat for every grid case; Buy & Hold enters at the first analysis-session Open"` → `"flat for every period pair; Buy & Hold enters at the first analysis-session Open"`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_threshold_grid"` → `"qqq_dual_raw_stochrsi_cross_period_grid"`
  - `strategy.plain_language.buy`：`"空仓时，两个周期前一天都在候选低位线下方，并在当天一起向上越过这条线才买入。"` → `"空仓时，两个候选周期前一天都低于0.20，并在当天一起向上越过0.20才买入。"`
  - ……另有 4 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.2 → TIM-v0.70a.3 · QQQ 双周期 Stochastic RSI 多起点稳健选参

- 关系：`tests_true_restart_robustness_of`
- 为什么改：全历史32/152和36/152赢家受到2000年起点路径显著影响，而净值左侧对齐显示42/100在多数后续起点更强，因此改用真正账户重启检验周期稳健性。
- 策略修改：保留874组周期网格和0.20/0.80 CROSS规则，将单一2000至2026账户改为2010至2026季度起点的独立五年现金重启；2010至2019用于选参，2020至2026只作锁定验证。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.2__26-08-25__qqq_stochrsi_period_grid_full_history/experiment.json) · [子实验](../TIM/TIM-v0.70a.3__26-08-25__qqq_stochrsi_multistart_robustness/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"2000-01-03"` → `"<未设置>"`
  - `parameters.anchor_long_period`：`"<未设置>"` → `100`
  - `parameters.anchor_short_period`：`"<未设置>"` → `42`
  - `parameters.combination_count`：`"<未设置>"` → `874`
  - `parameters.combination_count_per_cost`：`874` → `"<未设置>"`
  - `parameters.expected_training_windows`：`"<未设置>"` → `20`
  - `parameters.expected_validation_windows`：`"<未设置>"` → `7`
  - `parameters.formal_costs_bps`：`"<未设置>"` → `[0,5]`
  - `parameters.full_sample_max_cagr_long_period`：`"<未设置>"` → `152`
  - `parameters.full_sample_max_cagr_short_period`：`"<未设置>"` → `36`
  - `parameters.full_sample_max_sharpe_long_period`：`"<未设置>"` → `152`
  - `parameters.full_sample_max_sharpe_short_period`：`"<未设置>"` → `32`
  - `parameters.gap_policy`：`"fill_at_open_when_open_is_already_beyond_trigger"` → `"<未设置>"`
  - `parameters.grid_semantics`：`"full_cartesian_product"` → `"<未设置>"`
  - `parameters.intraday_path`：`"directed_open_to_close_only"` → `"<未设置>"`
  - `parameters.parent_anchor_long_period`：`100` → `"<未设置>"`
  - `parameters.parent_anchor_short_period`：`42` → `"<未设置>"`
  - `parameters.research_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.research_start`：`"<未设置>"` → `"2010-01-04"`
  - `parameters.screen_cost_bps`：`"<未设置>"` → `5`
  - `parameters.sma_baseline_buy_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.sma_baseline_sell_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.sma_baseline_window`：`200` → `"<未设置>"`
  - ……另有 20 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.3 → TIM-v0.70a.4 · QQQ 双周期 Stochastic RSI 2010–2026全样本多起点

- 关系：`corrects_window_scope_of`
- 为什么改：父实验把用户要求的2010至2026数据误解为训练与锁定切分，使参与选择的起点仅覆盖2010至2014；本实验按用户原意让全部可用日期参与多起点稳健评分。
- 策略修改：五年现金重启、874组网格与评分门槛不变；季度起点扩展为2010Q1至2021Q3共47个，全部纳入2010至2026全样本描述性选择，不再声称样本外验证。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.3__26-08-25__qqq_stochrsi_multistart_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70a.4__26-08-25__qqq_stochrsi_multistart_full_2010_2026/experiment.json)
- 自动配置差异：

  - `parameters.all_start_first`：`"<未设置>"` → `"2010-01-01"`
  - `parameters.all_start_last`：`"<未设置>"` → `"2021-09-30"`
  - `parameters.expected_all_windows`：`"<未设置>"` → `47`
  - `parameters.expected_training_windows`：`20` → `"<未设置>"`
  - `parameters.expected_validation_windows`：`7` → `"<未设置>"`
  - `parameters.training_start_first`：`"2010-01-01"` → `"<未设置>"`
  - `parameters.training_start_last`：`"2014-12-31"` → `"<未设置>"`
  - `parameters.validation_start_first`：`"2020-01-01"` → `"<未设置>"`
  - `parameters.validation_start_last`：`"2021-09-30"` → `"<未设置>"`
  - `strategy.buy_rule`：`"While flat, buy only when both completed prior StochRSI values are below 0.20 and both provisional same-session values cross to or above 0.20; the joint trigger is the larger sol…` → `"While flat, both completed prior StochRSI values must be below 0.20 and both provisional same-session values must cross to or above 0.20; buy at the larger joint solved trigger."`
  - `strategy.description`：`"Fix raw unsmoothed dual-StochRSI CROSS thresholds at buy 0.20 and sell 0.80, scan short periods 14–50 and long periods 70–160 by two, and restart every candidate from cash at det…` → `"Fix dual raw StochRSI CROSS thresholds at buy 0.20 and sell 0.80 and scan short periods 14–50 against long periods 70–160 by two. Evaluate every pair on all quarterly-start five-…`
  - `strategy.execution_time`：`"Each window starts flat with 100000 dollars. On an eligible session, a gap beyond the frozen trigger fills at Open; otherwise only the directed Open-to-Close path may fill at the…` → `"Every window starts with 100000 dollars cash and no shares. Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. Hi…`
  - `strategy.indicator_definition`：`"For each period p, Wilder RSI(p) is normalized by its rolling p-value RSI minimum and maximum; zero range maps to 0.5; no K or D smoothing."` → `"Wilder RSI(p) normalized by its rolling p-value RSI minimum and maximum; zero range maps to 0.5; no K or D smoothing."`
  - `strategy.initial_position`：`"flat with 100000 dollars at every five-year window start"` → `"flat with 100000 dollars at every window start"`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_multistart_robustness"` → `"qqq_dual_raw_stochrsi_cross_multistart_full_2010_2026"`
  - `strategy.plain_language.execution`：`"前一日收盘后冻结当天触发价；跳空越线按开盘价成交，否则只有开盘到收盘碰到触发价才成交。"` → `"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则只有开盘到收盘碰到触发价才成交。"`
  - `strategy.plain_language.summary`：`"把同一组周期放到许多不同的五年起点重新投资，寻找不依赖单一起点的QQQ择时参数。"` → `"把每组周期放到2010至2026所有可用的五年起点重新投资，寻找不依赖单一起点的参数区域。"`
  - `strategy.positioning`：`"Each case-window is an independent long-only 0%/100% QQQ account with fractional shares, no leverage, no cash interest, and no inherited position or state."` → `"Each case-window is an independent long-only 0%/100% QQQ account with fractional shares, no leverage, no cash interest, and no inherited position."`
  - `strategy.sell_rule`：`"While long, sell only when both completed prior StochRSI values are above 0.80 and both provisional same-session values cross to or below 0.80; the joint trigger is the smaller s…` → `"While long, both completed prior StochRSI values must be above 0.80 and both provisional same-session values must cross to or below 0.80; sell at the smaller joint solved trigger…`
  - `strategy.signal_time`：`"Eligibility and exact trigger equations use only data completed through the prior close, including pre-window history solely for indicator warmup."` → `"Eligibility and exact triggers are frozen before each session using completed prior data; pre-window data warms indicators but never creates inherited account state."`

### TIM-v0.70a.4 → TIM-v0.70a.5 · QQQ 双周期 Stochastic RSI 多维稳健筛选

- 关系：`strengthens_robustness_tests_of`
- 为什么改：父实验主要依赖五年窗口排名与高分连通区，仍不足以回答用户要求的稳健型选择；本实验把单点最优降为参考并同时检验期限、时期、删年、参数邻域和基准胜率。
- 策略修改：交易规则和874组周期网格不变；窗口扩展为3/5/7年共141个季度重启账户，并加入三个时期块、逐起始年份删除、完整3×3邻域、相对持有胜率及0/5/10 bps候选复核。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.4__26-08-25__qqq_stochrsi_multistart_full_2010_2026/experiment.json) · [子实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[0,5,10]`
  - `parameters.all_start_first`：`"2010-01-01"` → `"<未设置>"`
  - `parameters.all_start_last`：`"2021-09-30"` → `"<未设置>"`
  - `parameters.anchor_long_period`：`100` → `"<未设置>"`
  - `parameters.anchor_short_period`：`42` → `"<未设置>"`
  - `parameters.chronological_blocks`：`"<未设置>"` → `[[2010,2013],[2014,2017],[2018,2023]]`
  - `parameters.expected_all_windows`：`47` → `"<未设置>"`
  - `parameters.expected_windows.3`：`"<未设置>"` → `55`
  - `parameters.expected_windows.5`：`"<未设置>"` → `47`
  - `parameters.expected_windows.7`：`"<未设置>"` → `39`
  - `parameters.formal_costs_bps`：`[0,5]` → `[0,5,10]`
  - `parameters.full_sample_max_cagr_long_period`：`152` → `"<未设置>"`
  - `parameters.full_sample_max_cagr_short_period`：`36` → `"<未设置>"`
  - `parameters.full_sample_max_sharpe_long_period`：`152` → `"<未设置>"`
  - `parameters.full_sample_max_sharpe_short_period`：`32` → `"<未设置>"`
  - `parameters.gates.minimum_cagr_win_rate_vs_buy_hold`：`"<未设置>"` → `0.4`
  - `parameters.gates.minimum_drawdown_win_rate_vs_buy_hold`：`"<未设置>"` → `0.6`
  - `parameters.gates.minimum_leave_one_start_year_out_q25_joint_rank`：`"<未设置>"` → `0.5`
  - `parameters.gates.minimum_sharpe_win_rate_vs_buy_hold`：`"<未设置>"` → `0.55`
  - `parameters.gates.minimum_worst_horizon_q25_joint_rank`：`"<未设置>"` → `0.55`
  - `parameters.gates.minimum_worst_local_3x3_score`：`"<未设置>"` → `0.45`
  - `parameters.gates.minimum_worst_time_block_median_joint_rank`：`"<未设置>"` → `0.55`
  - `parameters.horizon_last_start.3`：`"<未设置>"` → `"2023-09-30"`
  - `parameters.horizon_last_start.5`：`"<未设置>"` → `"2021-09-30"`
  - ……另有 19 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.5 → TIM-v0.70a.6 · QQQ Stochastic RSI 2005–2020稳健训练与2021–2026锁定验证

- 关系：`locks_training_and_tests_future_of`
- 为什么改：父实验用到2021年以后数据，只能提供全样本描述性稳健候选；用户要求把相同思考方法限制在2005至2020选参，再独立检验2021至2026。
- 策略修改：交易、网格、七类稳健门禁和0/5/10 bps不变；选参样本改为2005至2020的132个3/5/7年季度重启窗口，2021-01-04至2026-08-04冻结为单一连续样本外账户，仅在参数写定后打开。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70a.6__26-08-25__qqq_stochrsi_2005_2020_train_2021_2026_oos/experiment.json)
- 自动配置差异：

  - `parameters.chronological_blocks`：`[[2010,2013],[2014,2017],[2018,2023]]` → `[[2005,2009],[2010,2013],[2014,2017]]`
  - `parameters.expected_windows.3`：`55` → `52`
  - `parameters.expected_windows.5`：`47` → `44`
  - `parameters.expected_windows.7`：`39` → `36`
  - `parameters.holdout_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.holdout_start`：`"<未设置>"` → `"2021-01-04"`
  - `parameters.horizon_last_start.3`：`"2023-09-30"` → `"2017-12-31"`
  - `parameters.horizon_last_start.5`：`"2021-09-30"` → `"2015-12-31"`
  - `parameters.horizon_last_start.7`：`"2019-09-30"` → `"2013-12-31"`
  - `parameters.research_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.research_start`：`"2010-01-04"` → `"2005-01-03"`
  - `parameters.selection_end`：`"<未设置>"` → `"2020-12-31"`
  - `strategy.description`：`"Fix CROSS thresholds at 0.20/0.80 and scan 874 short/long period pairs over every fully observable quarterly restart using 3-, 5-, and 7-year accounts. Select only through cross-…` → `"Select the 14–50 by 70–160 period pair using only 2005–2020 and the frozen multidimensional robustness method, then evaluate the frozen pair once on the untouched continuous 2021…`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_multihorizon_robustness"` → `"qqq_dual_raw_stochrsi_cross_robust_train_oos"`
  - `strategy.plain_language.execution`：`"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则盘中只按开盘到收盘的方向检查触发价。"` → `"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则盘中只按开盘到收盘方向检查触发价。"`
  - `strategy.plain_language.position`：`"每个窗口都以10万美元现金独立开始，在满仓QQQ和现金之间切换，并与同期买入持有比较。"` → `"训练窗口和验证账户都从10万美元现金独立开始，在满仓QQQ与现金之间切换，并与同期持有比较。"`
  - `strategy.plain_language.summary`：`"把同一组周期放进长短不同、开始时间不同的独立账户，寻找换时期和轻微改参数后仍可靠的选择。"` → `"只用2005至2020寻找换期限、换起点和轻微改参数后仍稳定的周期，再把它原样放进2021至2026检验。"`
  - `strategy.positioning`：`"Every horizon-window is an independent long-only 0%/100% QQQ account starting with 100000 dollars cash, fractional shares, no leverage, and no cash interest."` → `"Every training window and the locked holdout are separate long-only 0%/100% QQQ accounts with 100000 dollars, fractional shares, no leverage, and no cash interest."`
  - `strategy.signal_time`：`"Eligibility and exact triggers use only completed prior data; pre-window history only warms indicators."` → `"Eligibility and exact triggers use only completed prior data. No observation dated 2021 or later may enter selection."`

### TIM-v0.70a.6 → TIM-v0.70a.7 · QQQ 双周期 Stochastic RSI 2000–2015窄网格稳健选参

- 关系：`narrows_grid_and_moves_training_window_of`
- 为什么改：22/88在2010年以后稳健而28/88在更早历史更均衡，用户需要只用2000至2015并在两者附近的小范围内寻找兼顾CAGR与稳定性的参数。
- 策略修改：固定0.20/0.80 CROSS和七类门禁；训练改为2000至2015的132个3/5/7年季度重启窗口，周期网格收窄为短20至30、长85至95、步长1共121组，不读取2016年以后数据。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.6__26-08-25__qqq_stochrsi_2005_2020_train_2021_2026_oos/experiment.json) · [子实验](../TIM/TIM-v0.70a.7__26-08-25__qqq_stochrsi_narrow_robust_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.chronological_blocks`：`[[2005,2009],[2010,2013],[2014,2017]]` → `[[2000,2004],[2005,2008],[2009,2012]]`
  - `parameters.combination_count`：`874` → `121`
  - `parameters.holdout_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.holdout_start`：`"2021-01-04"` → `"<未设置>"`
  - `parameters.horizon_last_start.3`：`"2017-12-31"` → `"2012-12-31"`
  - `parameters.horizon_last_start.5`：`"2015-12-31"` → `"2010-12-31"`
  - `parameters.horizon_last_start.7`：`"2013-12-31"` → `"2008-12-31"`
  - `parameters.long_period_end`：`160` → `95`
  - `parameters.long_period_start`：`70` → `85`
  - `parameters.period_step`：`2` → `1`
  - `parameters.reference_pairs`：`"<未设置>"` → `[[22,88],[28,88]]`
  - `parameters.research_start`：`"2005-01-03"` → `"2000-01-03"`
  - `parameters.selection_end`：`"2020-12-31"` → `"2015-12-31"`
  - `parameters.short_period_end`：`50` → `30`
  - `parameters.short_period_start`：`14` → `20`
  - `strategy.description`：`"Select the 14–50 by 70–160 period pair using only 2005–2020 and the frozen multidimensional robustness method, then evaluate the frozen pair once on the untouched continuous 2021…` → `"Use only 2000–2015 to compare every integer short period from 20 through 30 and long period from 85 through 95 under the fixed raw dual-StochRSI 0.20/0.80 CROSS rule, selecting f…`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_robust_train_oos"` → `"qqq_dual_raw_stochrsi_cross_narrow_robust_2000_2015"`
  - `strategy.plain_language.buy`：`"两个周期先同时处于低位，再一起向上越过0.20时买入QQQ。"` → `"两个周期先同时处于低位，随后一起向上越过0.20时买入QQQ。"`
  - `strategy.plain_language.execution`：`"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则盘中只按开盘到收盘方向检查触发价。"` → `"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则只按开盘到收盘方向检查触发价。"`
  - `strategy.plain_language.position`：`"训练窗口和验证账户都从10万美元现金独立开始，在满仓QQQ与现金之间切换，并与同期持有比较。"` → `"每个窗口从10万美元现金独立开始，在满仓QQQ与现金之间切换，并与同期持有比较。"`
  - `strategy.plain_language.sell`：`"两个周期先同时处于高位，再一起向下跌破0.80时卖出QQQ。"` → `"两个周期先同时处于高位，随后一起向下跌破0.80时卖出QQQ。"`
  - `strategy.plain_language.summary`：`"只用2005至2020寻找换期限、换起点和轻微改参数后仍稳定的周期，再把它原样放进2021至2026检验。"` → `"只用2000至2015，在22/88和28/88附近寻找兼顾收益与稳定性的双周期。"`
  - `strategy.positioning`：`"Every training window and the locked holdout are separate long-only 0%/100% QQQ accounts with 100000 dollars, fractional shares, no leverage, and no cash interest."` → `"Every window is a separate long-only 0%/100% QQQ account with 100000 dollars, fractional shares, no leverage, and no cash interest."`
  - `strategy.signal_time`：`"Eligibility and exact triggers use only completed prior data. No observation dated 2021 or later may enter selection."` → `"Eligibility and exact trigger prices use completed prior data only; all selection windows end no later than 2015-12-31."`

### TIM-v0.70a.5 → TIM-v0.70a.8 · QQQ Stochastic RSI 2000–2015首次入场对齐大范围稳健选参

- 关系：`corrects_benchmark_entry_and_broadens_training_grid_of`
- 为什么改：用户确认策略与持有基准都不应在窗口起点自动持仓，而应等到该参数第一次真实买入成交时再开始持有对照；同时窄域20至30乘85至95不足以判断是否存在兼顾CAGR和稳健性的更大参数平台。
- 策略修改：保留0.20/0.80双周期CROSS、2000至2015的132个3/5/7年季度重启窗口、七类稳健门禁和0/5/10bps；周期网格扩大为短14至50、长70至160逐整数共3367组，并把Buy & Hold改为逐参数逐窗口先持有现金、在策略第一笔实际买入时用同一含成本成交价一次买入后持有。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70a.8__26-08-25__qqq_stochrsi_first_entry_aligned_broad_robust_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.chronological_blocks`：`[[2010,2013],[2014,2017],[2018,2023]]` → `[[2000,2004],[2005,2008],[2009,2012]]`
  - `parameters.combination_count`：`874` → `3367`
  - `parameters.expected_windows.3`：`55` → `52`
  - `parameters.expected_windows.5`：`47` → `44`
  - `parameters.expected_windows.7`：`39` → `36`
  - `parameters.gates.minimum_cagr_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.4`
  - `parameters.gates.minimum_cagr_win_rate_vs_buy_hold`：`0.4` → `"<未设置>"`
  - `parameters.gates.minimum_drawdown_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.6`
  - `parameters.gates.minimum_drawdown_win_rate_vs_buy_hold`：`0.6` → `"<未设置>"`
  - `parameters.gates.minimum_sharpe_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.55`
  - `parameters.gates.minimum_sharpe_win_rate_vs_buy_hold`：`0.55` → `"<未设置>"`
  - `parameters.horizon_last_start.3`：`"2023-09-30"` → `"2012-12-31"`
  - `parameters.horizon_last_start.5`：`"2021-09-30"` → `"2010-12-31"`
  - `parameters.horizon_last_start.7`：`"2019-09-30"` → `"2008-12-31"`
  - `parameters.period_step`：`2` → `1`
  - `parameters.reference_pairs`：`"<未设置>"` → `[[22,88],[28,88],[42,100]]`
  - `parameters.research_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.research_start`：`"2010-01-04"` → `"2000-01-03"`
  - `strategy.description`：`"Fix CROSS thresholds at 0.20/0.80 and scan 874 short/long period pairs over every fully observable quarterly restart using 3-, 5-, and 7-year accounts. Select only through cross-…` → `"Use only 2000–2015 to scan every integer short period from 14 through 50 and long period from 70 through 160 under the fixed raw dual-StochRSI 0.20/0.80 CROSS rule. In every rest…`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_multihorizon_robustness"` → `"qqq_dual_raw_stochrsi_cross_first_entry_aligned_broad_robust_2000_2015"`
  - `strategy.plain_language.buy`：`"两个周期先同时处于低位，再一起向上越过0.20时买入QQQ。"` → `"两个周期先同时处于低位，随后一起向上越过0.20时买入QQQ。"`
  - `strategy.plain_language.execution`：`"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则盘中只按开盘到收盘的方向检查触发价。"` → `"前一日收盘后冻结触发价；跳空越线按开盘价成交，否则只按开盘到收盘方向检查精确触发价。"`
  - `strategy.plain_language.position`：`"每个窗口都以10万美元现金独立开始，在满仓QQQ和现金之间切换，并与同期买入持有比较。"` → `"策略和对照都先持有现金；策略第一次实际买入时，对照用相同资金和相同含成本成交价一次买入并持有。"`
  - `strategy.plain_language.sell`：`"两个周期先同时处于高位，再一起向下跌破0.80时卖出QQQ。"` → `"两个周期先同时处于高位，随后一起向下跌破0.80时卖出QQQ。"`
  - ……另有 3 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.5 → TIM-v0.70a.9 · QQQ Stochastic RSI 2000–2015超大网格四参数面

- 关系：`expands_valid_period_plane_and_visualizes_surfaces_of`
- 为什么改：局部和中等范围网格仍不足以直观看出高CAGR脊线、稳健区域和门禁失败是否属于孤立点；用户要求把短期0至70和长期50至150尽量完整展开，并分别查看稳健分、门禁数、CAGR和Sharpe参数面。
- 策略修改：保留0.20/0.80双周期CROSS、2000至2015的132个3/5/7年季度重启窗口、首次入场对齐持有和七项门禁；period=0无定义且period=1会退化为零宽StochRSI窗口，实际扫描短2至70、长50至150且short<long的6738组有效整数对，只使用5bps，并生成四张独立heatmap。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70a.9__26-08-26__qqq_stochrsi_supergrid_heatmaps_2000_2015/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5,10]` → `[5]`
  - `parameters.chronological_blocks`：`[[2010,2013],[2014,2017],[2018,2023]]` → `[[2000,2004],[2005,2008],[2009,2012]]`
  - `parameters.combination_count`：`874` → `6738`
  - `parameters.expected_windows.3`：`55` → `52`
  - `parameters.expected_windows.5`：`47` → `44`
  - `parameters.expected_windows.7`：`39` → `36`
  - `parameters.formal_costs_bps`：`[0,5,10]` → `[5]`
  - `parameters.gates.minimum_cagr_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.4`
  - `parameters.gates.minimum_cagr_win_rate_vs_buy_hold`：`0.4` → `"<未设置>"`
  - `parameters.gates.minimum_drawdown_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.6`
  - `parameters.gates.minimum_drawdown_win_rate_vs_buy_hold`：`0.6` → `"<未设置>"`
  - `parameters.gates.minimum_sharpe_win_rate_vs_aligned_buy_hold`：`"<未设置>"` → `0.55`
  - `parameters.gates.minimum_sharpe_win_rate_vs_buy_hold`：`0.55` → `"<未设置>"`
  - `parameters.heatmap_metrics`：`"<未设置>"` → `["maximin_score","gate_pass_count","median_cagr_pct","median_sharpe"]`
  - `parameters.horizon_last_start.3`：`"2023-09-30"` → `"2012-12-31"`
  - `parameters.horizon_last_start.5`：`"2021-09-30"` → `"2010-12-31"`
  - `parameters.horizon_last_start.7`：`"2019-09-30"` → `"2008-12-31"`
  - `parameters.long_period_end`：`160` → `150`
  - `parameters.long_period_start`：`70` → `50`
  - `parameters.period_step`：`2` → `1`
  - `parameters.reference_pairs`：`"<未设置>"` → `[[17,97],[30,143],[42,100]]`
  - `parameters.require_short_strictly_less_than_long`：`"<未设置>"` → `true`
  - `parameters.research_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.research_start`：`"2010-01-04"` → `"2000-01-03"`
  - ……另有 12 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.5 → TIM-v0.70b.1 · QQQ 单周期 Stochastic RSI 2005–2020稳健选参

- 关系：`reduces_to_single_period_of`
- 为什么改：双周期确认可能把真正有用的单一时间尺度与冗余确认混在一起；用户要求用同一稳健方法单独判断只看一条Stochastic RSI时会选出什么周期。
- 策略修改：固定0.20/0.80 CROSS交易语义，将14至50乘70至160的双周期网格改为14至200逐整数的187个单周期；选参限定为2005至2020的132个3/5/7年季度重启窗口，完整3×3邻域相应改为period-1、period、period+1三点邻域，不读取2021年以后数据。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70b.1__26-08-25__qqq_single_stochrsi_robust_2005_2020/experiment.json)
- 自动配置差异：

  - `parameters.chronological_blocks`：`[[2010,2013],[2014,2017],[2018,2023]]` → `[[2005,2009],[2010,2013],[2014,2017]]`
  - `parameters.combination_count`：`874` → `187`
  - `parameters.expected_windows.3`：`55` → `52`
  - `parameters.expected_windows.5`：`47` → `44`
  - `parameters.expected_windows.7`：`39` → `36`
  - `parameters.gates.minimum_worst_local_3point_score`：`"<未设置>"` → `0.45`
  - `parameters.gates.minimum_worst_local_3x3_score`：`0.45` → `"<未设置>"`
  - `parameters.horizon_last_start.3`：`"2023-09-30"` → `"2017-12-31"`
  - `parameters.horizon_last_start.5`：`"2021-09-30"` → `"2015-12-31"`
  - `parameters.horizon_last_start.7`：`"2019-09-30"` → `"2013-12-31"`
  - `parameters.long_period_end`：`160` → `"<未设置>"`
  - `parameters.long_period_start`：`70` → `"<未设置>"`
  - `parameters.period_end`：`"<未设置>"` → `200`
  - `parameters.period_start`：`"<未设置>"` → `14`
  - `parameters.period_step`：`2` → `1`
  - `parameters.reference_periods`：`"<未设置>"` → `[42,100]`
  - `parameters.research_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.research_start`：`"2010-01-04"` → `"2005-01-03"`
  - `parameters.selection_end`：`"<未设置>"` → `"2020-12-31"`
  - `parameters.short_period_end`：`50` → `"<未设置>"`
  - `parameters.short_period_start`：`14` → `"<未设置>"`
  - `strategy.buy_rule`：`"While flat, both completed prior StochRSI values are below 0.20 and both provisional same-session values cross to or above 0.20; buy at the larger joint solved trigger."` → `"While flat, the completed prior StochRSI is below 0.20 and its provisional same-session value crosses to or above 0.20; buy at the solved trigger."`
  - `strategy.description`：`"Fix CROSS thresholds at 0.20/0.80 and scan 874 short/long period pairs over every fully observable quarterly restart using 3-, 5-, and 7-year accounts. Select only through cross-…` → `"Use one raw StochRSI only. Scan every integer period from 14 through 200 with fixed 0.20 upward-cross entry and 0.80 downward-cross exit, selecting on 2005–2020 through the same …`
  - `strategy.name`：`"qqq_dual_raw_stochrsi_cross_multihorizon_robustness"` → `"qqq_single_raw_stochrsi_cross_robust_2005_2020"`
  - ……另有 8 项，完整定义见父子 `experiment.json`。

### TIM-v0.70a.5 → TIM-v0.70c.1 · QQQ双StochRSI与Bear9漂移再平衡比较

- 关系：`locks_two_timing_candidates_and_changes_to_next_open_comparison`
- 为什么改：用户希望把稳健型12/61与高收益平台代表62/111放在同一张全历史净值图中，并为两条择时路径增加空仓期Bear9版本。多资产轮换无法在不融资和不使用未来信息的情况下保留盘中精确触发成交，因此需要显式改成收盘确认、下一共同Open切换。
- 策略修改：冻结12/61和62/111、0.20/0.80双周期同日穿越；把盘中理论触发成交改为完成Close确认后下一共同Open；新增两条基础现金空仓路径、两条Bear9空仓路径和同日历QQQ持有基准，不重新选择StochRSI参数。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_STOCHRSI_BEAR9；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json) · [子实验](../TIM/TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5,10]` → `[5]`
  - `parameters.analysis_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2000-05-30"`
  - `parameters.bear9_drift_comparison`：`"<未设置>"` → `"strictly greater than threshold; absolute total-account member-weight deviation"`
  - `parameters.bear9_drift_threshold_absolute_weight`：`"<未设置>"` → `0.03`
  - `parameters.bear9_rebalance_scope`：`"<未设置>"` → `"restore all currently tradable Bear9 members to frozen total-account targets; unavailable positive weights remain cash"`
  - `parameters.bear9_weights.AZO`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.DG`：`"<未设置>"` → `0.09`
  - `parameters.bear9_weights.DLTR`：`"<未设置>"` → `0.08`
  - `parameters.bear9_weights.ED`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.MO`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.ORLY`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.SO`：`"<未设置>"` → `0.12`
  - `parameters.bear9_weights.WMT`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.WRB`：`"<未设置>"` → `0.12`
  - `parameters.benchmark_case_id`：`"<未设置>"` → `"QQQ_BUY_HOLD"`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.chronological_blocks`：`[[2010,2013],[2014,2017],[2018,2023]]` → `"<未设置>"`
  - `parameters.combination_count`：`874` → `"<未设置>"`
  - `parameters.cost_bps_per_side`：`"<未设置>"` → `5.0`
  - `parameters.cross_semantics`：`"<未设置>"` → `"both prior values strictly beyond the old side and both current completed-close values at or beyond the new side"`
  - `parameters.expected_windows.3`：`55` → `"<未设置>"`
  - `parameters.expected_windows.5`：`47` → `"<未设置>"`
  - `parameters.expected_windows.7`：`39` → `"<未设置>"`
  - ……另有 44 项，完整定义见父子 `experiment.json`。

### TIM-v0.40c.3 → TIM-v0.70c.1 · QQQ双StochRSI与Bear9漂移再平衡比较

- 关系：`expands_flat_substitution_to_fixed_bear9_basket`
- 为什么改：此前QQQ空仓替代研究提供了防守标的全历史表现线索；用户进一步指定沿用熊市研究形成的固定Bear9，并要求按成员绝对权重偏离三个百分点触发再平衡，而不是继续逐标的择时。
- 策略修改：空仓替代由七条独立单资产路径改为AZO/SO/ED/ORLY/MO/WRB/DLTR/DG/WMT固定非等权组合；删除个股SMA，冻结10/12/13/10/13/12/8/9/13权重，并在任一可交易成员绝对偏离目标严格超过3个百分点时于下一Open恢复全部目标。
- 修改前：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_STOCHRSI_BEAR9；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json) · [子实验](../TIM/TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.analysis_start`：`"first_valid_qqq_sma200_close"` → `"2000-05-30"`
  - `parameters.bear9_drift_comparison`：`"<未设置>"` → `"strictly greater than threshold; absolute total-account member-weight deviation"`
  - `parameters.bear9_drift_threshold_absolute_weight`：`"<未设置>"` → `0.03`
  - `parameters.bear9_rebalance_scope`：`"<未设置>"` → `"restore all currently tradable Bear9 members to frozen total-account targets; unavailable positive weights remain cash"`
  - `parameters.bear9_weights.AZO`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.DG`：`"<未设置>"` → `0.09`
  - `parameters.bear9_weights.DLTR`：`"<未设置>"` → `0.08`
  - `parameters.bear9_weights.ED`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.MO`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.ORLY`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.SO`：`"<未设置>"` → `0.12`
  - `parameters.bear9_weights.WMT`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.WRB`：`"<未设置>"` → `0.12`
  - `parameters.bear_interval_source`：`"research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`true` → `"<未设置>"`
  - `parameters.benchmark_case_id`：`"<未设置>"` → `"QQQ_BUY_HOLD"`
  - `parameters.buy_threshold`：`"<未设置>"` → `0.2`
  - `parameters.cost_bps_per_side`：`"<未设置>"` → `5.0`
  - `parameters.cross_semantics`：`"<未设置>"` → `"both prior values strictly beyond the old side and both current completed-close values at or beyond the new side"`
  - `parameters.dotcom_exclusion_rule`：`"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"` → `"<未设置>"`
  - `parameters.entry_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.entry_semantics_after_initialization`：`"true close crossing from <= upper rail to > upper rail"` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`3.0` → `"<未设置>"`
  - ……另有 43 项，完整定义见父子 `experiment.json`。

### TIM-v0.20a.1 → TIM-v0.60b.1 · RKLB 日内动态 SMA 双阈值网格

- 关系：`cross_asset_parameter_search_of`
- 为什么改：检验盘前可解的日内动态 SMA 阈值状态机在获批 RKLB 个股历史上，跨 15～115 日窗口和九种窄滞回组合是否形成稳定区域。
- 策略修改：标的从 QQQ 改为 RKLB；移除 c/d 纠错线；把固定 SMA200 改为 SMA15～115、步长 2，并扫描独立的买入/卖出 1%、2%、3% 阈值；所有 case 统一使用最长窗口完成预热后的共同起点。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：RKLB；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json) · [子实验](../TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid/experiment.json)
- 自动配置差异：

  - `parameters.a_pct_range.start`：`-20.0` → `"<未设置>"`
  - `parameters.a_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.a_pct_range.stop`：`20.0` → `"<未设置>"`
  - `parameters.analysis_end`：`"2025-12-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2021-01-04"` → `"<未设置>"`
  - `parameters.b_pct_range.start`：`-20.0` → `"<未设置>"`
  - `parameters.b_pct_range.step`：`0.25` → `"<未设置>"`
  - `parameters.b_pct_range.stop`：`20.0` → `"<未设置>"`
  - `parameters.buy_above_sma_pct`：`"<未设置>"` → `[1,2,3]`
  - `parameters.combination_count_per_cost`：`77763` → `"<未设置>"`
  - `parameters.combination_count_per_mode`：`25921` → `"<未设置>"`
  - `parameters.common_start_rule`：`"<未设置>"` → `"All cases begin on the first RKLB session with 114 completed prior closes, so every SMA window uses the same evaluation dates; earlier canonical RKLB bars are warmup only."`
  - `parameters.correction_constraint`：`"c=d within each enabled scenario"` → `"<未设置>"`
  - `parameters.correction_scenarios_pct`：`[null,5.0,10.0]` → `"<未设置>"`
  - `parameters.requested_interval`：`"2021-01-01 inclusive to 2026-01-01 exclusive; normalized to available QQQ sessions"` → `"<未设置>"`
  - `parameters.sell_below_sma_pct`：`"<未设置>"` → `[1,2,3]`
  - `parameters.sma_windows.start`：`"<未设置>"` → `15`
  - `parameters.sma_windows.step`：`"<未设置>"` → `2`
  - `parameters.sma_windows.stop`：`"<未设置>"` → `115`
  - `parameters.sma_windows.values`：`"<未设置>"` → `[15,17,19,21,23,25,27,29,31,33,35,37,39,41,43,45,47,49,51,53,55,57,59,61,63,65,67,69,71,73,75,77,79,81,83,85,87,89,91,93,95,97,99,101,103,105,107,109,111,113,115]`
  - `parameters.threshold_pair_count`：`"<未设置>"` → `9`
  - `parameters.total_case_count_all_costs`：`155526` → `"<未设置>"`
  - `parameters.total_case_count_per_cost`：`"<未设置>"` → `459`
  - `strategy.buy_rule`：`"Flat ordinary entry: prior_close <= prior_sma200*(1+b/100), then buy if current regular-session price reaches P where P=(1+b/100)*(sum of prior 199 closes + P)/200. If correction…` → `"When flat and the prior completed close is at or below prior SMA(n)*(1+buy_pct/100), predeclare P_buy=(1+buy_pct/100)*sum(previous n-1 closes)/(n-(1+buy_pct/100)); buy if Open>=P…`
  - ……另有 8 项，完整定义见父子 `experiment.json`。

### TIM-v0.50 → TIM-v0.50a.1 · QQQ 独立买卖 SMA 周期锁定样本外

- 关系：`locked_test_of`
- 为什么改：把历史诊断较强但未通过PBO/DSR晋级门禁的310/190锚点带入完全未参与选择的2016年以后，区分真实样本外表现与2000-2015参数面偶然性，并检查锚点是否位于局部平滑区域。
- 策略修改：冻结c=3%、d关闭、买入SMA310/卖出SMA190及原有成交语义；所有case从2016-01-04独立空仓运行至批准数据末日2026-08-04。预登记买入270-350与卖出150-230、步长10的81组局部扰动仅作稳健性描述，禁止在保留期重新选冠军。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015/experiment.json) · [子实验](../TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2015-12-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-12-18"` → `"2016-01-04"`
  - `parameters.buy_sma_window_range.count`：`38` → `9`
  - `parameters.buy_sma_window_range.start`：`80` → `270`
  - `parameters.buy_sma_window_range.stop`：`450` → `350`
  - `parameters.combination_count`：`28880` → `81`
  - `parameters.fixed_subperiods`：`[{"end":"2005-12-30","period_id":"P1_2000_2005","start":"2000-12-18"},{"end":"2010-12-31","period_id":"P2_2006_2010","start":"2006-01-03"},{"end":"2015-12-31","period_id":"P3_2011…` → `[{"end":"2019-12-31","period_id":"P1_2016_2019","start":"2016-01-04"},{"end":"2022-12-30","period_id":"P2_2020_2022","start":"2020-01-02"},{"end":"2026-08-04","period_id":"P3_2023…`
  - `parameters.forced_rebuy_pct`：`[3.0,5.0,8.0,10.0]` → `[3.0]`
  - `parameters.formal_reconciliation.anchors`：`"SMA200/SMA200 for all 20 correction modes"` → `"<未设置>"`
  - `parameters.formal_reconciliation.cases`：`"<未设置>"` → `"all 81 predeclared combinations"`
  - `parameters.formal_reconciliation.objective_champions`：`"deduplicated full-history CAGR, Sharpe and maximum-drawdown champions"` → `"<未设置>"`
  - `parameters.formal_reconciliation.requirement`：`"Every formal candidate must match PyBroker and the independent Python ledger in dates, sides, shares, fills, cash and equity within 1e-6. The two compiled full-grid ledgers must …` → `"Every case must match both independently structured compiled ledgers and then match PyBroker plus the independent Python ledger in dates, sides, shares, fills, cash and equity wi…`
  - `parameters.formal_reconciliation.surface_representatives`：`"Reconcile the deterministic 3x3-local surface representative for every correction mode when defined. A representative from a boundary-touching or otherwise structurally failed co…` → `"<未设置>"`
  - `parameters.identifiability_gate.purpose`：`"Exclude one-entry hold artifacts from period selection and label enabled stop losses with too few primary events as descriptive insurance only."` → `"<未设置>"`
  - `parameters.identifiability_gate.scope`：`"<未设置>"` → `"descriptive robustness only; it cannot select or replace the locked anchor"`
  - `parameters.identifiability_gate.stop_loss_activity_count`：`10` → `"<未设置>"`
  - `parameters.locked_anchor.buy_window`：`"<未设置>"` → `310`
  - `parameters.locked_anchor.forced_rebuy_pct`：`"<未设置>"` → `3.0`
  - `parameters.locked_anchor.selection_policy`：`"<未设置>"` → `"This is the sole locked test case. It was fixed from TIM-v0.50 before any 2016+ result is read and cannot be replaced by a perturbation winner."`
  - `parameters.locked_anchor.sell_window`：`"<未设置>"` → `190`
  - `parameters.locked_anchor.stop_loss_pct`：`"<未设置>"` → `null`
  - `parameters.multiple_testing.dsr_minimum_probability`：`0.95` → `"<未设置>"`
  - `parameters.multiple_testing.pbo_block_count`：`12` → `"<未设置>"`
  - `parameters.multiple_testing.pbo_maximum`：`0.2` → `"<未设置>"`
  - ……另有 44 项，完整定义见父子 `experiment.json`。

### TIM-v0.40a.1 → TIM-v0.40a.2 · 八标的 SMA200 峰值回撤阈值网格

- 关系：`narrows_universe_and_scans_trailing_stop`
- 为什么改：上一版显示星标权重没有稳定作用，且6%单点收益主要受2000-2002影响；需要移除星标自由度，收窄到用户复核后的八个候选，并逐标的及组合细扫峰值回撤阈值。
- 策略修改：候选池收窄为LMT/EQT/ORLY/AZO/TLT/COR/SO/HRL，全部等权；保留SMA200±3%、无10%锁、止损后重新武装再上穿和次日Open语义；止损由关闭/6/8/10/12扩为关闭及3%至20%逐1%，新增八张单标的表、一张等权组合表和剔除2000-2002复合诊断。
- 修改前：BEAR_EVENT_WEIGHTED_STOPS；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR_SELECTED8_STOP_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation/experiment.json) · [子实验](../TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"<未设置>"` → `"总小熊、总大熊、总熊及剔除2000-2002总熊均按对应逐段账户收益几何复合；某段因SMA择时全程无持仓时该段收益严格记为0。"`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"` → `"以SPY日历为基础；八个标的上市前缺席不影响日历，任何标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"`
  - `parameters.entry_semantics`：`"start使用严格水平条件；途中和强制退出后要求先观察上轨之下，再严格上穿上轨"` → `"start使用严格水平条件；途中和峰值/SMA退出后要求先观察上轨之下或等于上轨，再严格上穿上轨"`
  - `parameters.forced_exit_priority`：`"trailing stop before SMA exit; record both active reasons but emit one sell target"` → `"trailing stop before SMA exit; record one sell target"`
  - `parameters.formal_case_count_per_cost`：`"<未设置>"` → `171`
  - `parameters.formal_cases`：`["star_off__stop_off","star_off__stop_06","star_off__stop_08","star_off__stop_10","star_off__stop_12","star_on__stop_off","star_on__stop_06","star_on__stop_08","star_on__stop_10",…` → `"<未设置>"`
  - `parameters.portfolio_target`：`"<未设置>"` → `"PORTFOLIO_8"`
  - `parameters.portfolio_weighting`：`"<未设置>"` → `"equal_weight_among_currently_active_symbols"`
  - `parameters.report_scopes`：`"<未设置>"` → `["12_individual_intervals","minor_compound","major_compound","all_compound","all_excluding_2000_2002_compound"]`
  - `parameters.slot_multipliers.star_off.default`：`1` → `"<未设置>"`
  - `parameters.slot_multipliers.star_off.starred`：`1` → `"<未设置>"`
  - `parameters.slot_multipliers.star_on.default`：`1` → `"<未设置>"`
  - `parameters.slot_multipliers.star_on.starred`：`2` → `"<未设置>"`
  - `parameters.star_modes`：`["star_off","star_on"]` → `"<未设置>"`
  - `parameters.starred_symbols`：`["EQT","GIS","LMT","ORLY","AZO"]` → `"<未设置>"`
  - `parameters.targets`：`"<未设置>"` → `["LMT","EQT","ORLY","AZO","TLT","COR","SO","HRL","PORTFOLIO_8"]`
  - `parameters.threshold_step_pct`：`"<未设置>"` → `1.0`
  - `parameters.trailing_stop_drawdown_pct`：`[null,6.0,8.0,10.0,12.0]` → `[null,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0,11.0,12.0,13.0,14.0,15.0,16.0,17.0,18.0,19.0,20.0]`
  - `parameters.universe`：`["WEC","LMT","EQT","GIS","WRB","ORLY","HSY","GILD","HRL","GLD","ED","SO","COR","TLT","AZO"]` → `["LMT","EQT","ORLY","AZO","TLT","COR","SO","HRL"]`
  - `strategy.buy_rule`：`"每段start完成Close后，SMA200已形成且Close>SMA200×1.03的标的产生初始进入信号。熊市途中，空仓标的只有在本次空仓期曾观察到Close<=SMA200×1.03后，后续完成Close严格上穿SMA200×1.03才进入；强制卖出后同样必须完成这一重新武装过程，不能立即买回。信号下一共同交易日Open执行。"` → `"每段start完成Close后，SMA200已形成且Close>SMA200×1.03时产生初始进入信号。熊市途中及峰值/SMA退出后，空仓标的必须先观察到Close<=SMA200×1.03完成重新武装，之后某日完成Close严格上穿SMA200×1.03才产生买入信号；信号下一共同交易日Open执行。"`
  - `strategy.description`：`"只在既有12段事后峰值至谷底熊市窗口内交易15个固定候选。所有case使用SMA200上下3%滞回且取消成交价10%锁：熊市起点以Close高于SMA200×1.03的水平条件建仓，途中空仓标的必须先处于上轨之下再严格上穿上轨才可进入，持仓标的Close低于SMA200×0.97退出。以每次真实状态进入成交价和随后完成Close的最高值维护持仓峰值；比较…` → `"只在既有12段事后峰值至谷底熊市窗口内，分别回测LMT、EQT、ORLY、AZO、TLT、COR、SO、HRL八个单标的100%仓位路径，以及八标的当前有效成员等权组合。全部路径保留SMA200上下3%滞回，比较关闭峰值回撤强制退出与3%至20%、步长1%的18个阈值。峰值退出后允许再次买入，但必须先在本次空仓期观察到Close位于SMA200×1.03…`
  - `strategy.initial_position`：`"每段熊市start的Close之前均为空仓；start信号下一共同Open才建立初始组合。"` → `"每段熊市start的Close之前均为空仓；start信号下一共同Open才建立初始仓位。"`
  - `strategy.name`：`"bear_market_weighted_trailing_stop_ablation"` → `"bear_selected8_sma200_trailing_stop_grid"`
  - `strategy.positioning`：`"long-only、允许小数股、不融资、总目标权重不超过100%。普通标的坑位乘数1；EQT/GIS/LMT/ORLY/AZO在star_on时乘数2。起点按活跃坑位归一化；途中新进入标的按其坑位数除以最终活跃坑位总数取得预算，原持仓按当时市值比例腾仓；退出释放的资金按幸存持仓当时市值比例分配。被动缩放不重置该标的运行峰值。"` → `"long-only、允许小数股、不融资、总目标权重不超过100%。单标的路径在有效时持有100%，无有效标的时全现金。八标的组合在当前有效标的之间等权；退出释放的资金按幸存持仓当时市值比例分配，新进入标的获得最终活跃标的数的1/n预算，原持仓按当时市值比例腾仓。被动缩放不重置运行峰值。"`
  - ……另有 4 项，完整定义见父子 `experiment.json`。

### TIM-v0.30a.1 → TIM-v0.50 · QQQ 独立买卖 SMA 周期与纠错网格

- 关系：`reparameterizes_with_sma_periods`
- 为什么改：固定 SMA200 的百分比买卖线在不同时期表现漂移，因此检验把入场与退出分别交给独立长期均线周期后，是否能形成跨阶段的二维稳定区域，同时保留已验证的强制买回与止损状态机。
- 策略修改：普通买卖从 SMA200×(1+b/a) 动态缓冲改为分别向上穿越 buy-SMA 与向下穿越 sell-SMA；两周期均扫描80～450/步长10，强制买回扫描3/5/8/10%，止损扫描关闭/3/5/8/10%，窗口冻结为共同 SMA450 预热后的2000-12-18～2015-12-31。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity/experiment.json) · [子实验](../TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2025-12-31"` → `"2015-12-31"`
  - `parameters.analysis_start`：`"2000-01-03"` → `"2000-12-18"`
  - `parameters.buy_sma_window_range.count`：`"<未设置>"` → `38`
  - `parameters.buy_sma_window_range.start`：`"<未设置>"` → `80`
  - `parameters.buy_sma_window_range.step`：`"<未设置>"` → `10`
  - `parameters.buy_sma_window_range.stop`：`"<未设置>"` → `450`
  - `parameters.causal_ablation`：`[{"c_pct":null,"d_pct":null},{"c_pct":5.0,"d_pct":null},{"c_pct":null,"d_pct":5.0},{"c_pct":5.0,"d_pct":5.0}]` → `"<未设置>"`
  - `parameters.combination_count`：`18` → `28880`
  - `parameters.cost_bps_per_side`：`"<未设置>"` → `5.0`
  - `parameters.d_sweep_pct`：`[null,2.5,5.0,7.5,10.0,12.5,15.0]` → `"<未设置>"`
  - `parameters.d_sweep_with_c_fixed_pct`：`5.0` → `"<未设置>"`
  - `parameters.event_attribution.forward_sessions`：`[20,60]` → `"<未设置>"`
  - `parameters.event_attribution.paired_counterfactual`：`"For each a/b and d-sweep case, compare the saved daily equity with its c=5,d=disabled control; report terminal, CAGR, Sharpe and maximum-drawdown deltas. This is a whole-path pai…` → `"<未设置>"`
  - `parameters.event_attribution.path_metrics`：`"For every primary SELL_CORRECTION, report the minimum QQQ Low and final Close relative to the raw sell fill over the next 20 and 60 available sessions beginning on the next tradi…` → `"<未设置>"`
  - `parameters.fixed_ab_pairs`：`[{"a_pct":3.75,"b_pct":-12.75,"pair_id":"primary","purpose":"上一轮 S5 冠军及冻结观察候选"},{"a_pct":3.0,"b_pct":-12.75,"pair_id":"drawdown_reference","purpose":"上一轮最大回撤较低的预登记对照"}]` → `"<未设置>"`
  - `parameters.fixed_subperiods`：`"<未设置>"` → `[{"end":"2005-12-30","period_id":"P1_2000_2005","start":"2000-12-18"},{"end":"2010-12-31","period_id":"P2_2006_2010","start":"2006-01-03"},{"end":"2015-12-31","period_id":"P3_2011…`
  - `parameters.forced_rebuy_pct`：`"<未设置>"` → `[3.0,5.0,8.0,10.0]`
  - `parameters.formal_reconciliation.anchors`：`"<未设置>"` → `"SMA200/SMA200 for all 20 correction modes"`
  - `parameters.formal_reconciliation.objective_champions`：`"<未设置>"` → `"deduplicated full-history CAGR, Sharpe and maximum-drawdown champions"`
  - `parameters.formal_reconciliation.requirement`：`"<未设置>"` → `"Every formal candidate must match PyBroker and the independent Python ledger in dates, sides, shares, fills, cash and equity within 1e-6. The two compiled full-grid ledgers must …`
  - `parameters.formal_reconciliation.surface_representatives`：`"<未设置>"` → `"Reconcile the deterministic 3x3-local surface representative for every correction mode when defined. A representative from a boundary-touching or otherwise structurally failed co…`
  - `parameters.identifiability_gate.minimum_ordinary_buy_count`：`"<未设置>"` → `3`
  - `parameters.identifiability_gate.minimum_ordinary_sell_count`：`"<未设置>"` → `3`
  - `parameters.identifiability_gate.purpose`：`"<未设置>"` → `"Exclude one-entry hold artifacts from period selection and label enabled stop losses with too few primary events as descriptive insurance only."`
  - ……另有 47 项，完整定义见父子 `experiment.json`。

### TIM-v0.50 → TIM-v0.50b.1 · QQQ 双均线恢复试探与失败退出网格

- 关系：`repairs_recovery_state_semantics`
- 为什么改：旧语义把盘中触及长均线直接视为恢复买入，并且买入后在尚未站上短均线时缺少与恢复失败相对应的退出状态；用户希望两根均线表达短线快速避险、长线确认恢复，同时能承认熊市小反弹买错。
- 策略修改：固定c=3%、d关闭并把搜索收窄为80～450/步长10的1,444组；普通买入改为完成Close上穿长均线、次日Open成交，所有买入先进入试探持仓。普通试探跌回长均线、c试探跌回原确认卖出成交价均在次日Open失败退出；站上短均线后转为确认多头并恢复盘中动态短均线卖出。每个熊市阶段c最多一次，失败退出不刷新c锚。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015/experiment.json) · [子实验](../TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.combination_count`：`28880` → `1444`
  - `parameters.confirmation_rule`：`"<未设置>"` → `"a probation position becomes confirmed when a completed Close is at or above the current sell SMA"`
  - `parameters.confirmed_exit_timing`：`"<未设置>"` → `"pre-open dynamic sell-SMA line, next-session Open gap or intraday Low touch"`
  - `parameters.forced_probation_failure`：`"<未设置>"` → `"completed close below original confirmed sell effective fill before ordinary buy-SMA recovery or confirmation, next-session Open exit"`
  - `parameters.forced_rebuy_attempts_per_bear_episode`：`"<未设置>"` → `1`
  - `parameters.forced_rebuy_pct`：`[3.0,5.0,8.0,10.0]` → `3.0`
  - `parameters.formal_reconciliation.anchors`：`"SMA200/SMA200 for all 20 correction modes"` → `"SMA200/SMA200 and inherited SMA310/SMA190"`
  - `parameters.formal_reconciliation.objective_champions`：`"deduplicated full-history CAGR, Sharpe and maximum-drawdown champions"` → `"Deduplicated full-history CAGR, Sharpe and maximum-drawdown champions."`
  - `parameters.formal_reconciliation.requirement`：`"Every formal candidate must match PyBroker and the independent Python ledger in dates, sides, shares, fills, cash and equity within 1e-6. The two compiled full-grid ledgers must …` → `"Every case must match two independently structured compiled ledgers within 1e-9; all formal candidates must match PyBroker and the Python reference ledger in dates, sides, shares…`
  - `parameters.formal_reconciliation.surface_representative`：`"<未设置>"` → `"Reconcile the deterministic local-stability representative even when a structural or statistical gate fails."`
  - `parameters.formal_reconciliation.surface_representatives`：`"Reconcile the deterministic 3x3-local surface representative for every correction mode when defined. A representative from a boundary-touching or otherwise structurally failed co…` → `"<未设置>"`
  - `parameters.identifiability_gate.minimum_confirmed_sell_count`：`"<未设置>"` → `3`
  - `parameters.identifiability_gate.minimum_ordinary_sell_count`：`3` → `"<未设置>"`
  - `parameters.identifiability_gate.minimum_probation_failure_count`：`"<未设置>"` → `3`
  - `parameters.identifiability_gate.purpose`：`"Exclude one-entry hold artifacts from period selection and label enabled stop losses with too few primary events as descriptive insurance only."` → `"Require both SMA roles and the new failed-recovery mechanism to occur often enough before interpreting a parameter pair."`
  - `parameters.identifiability_gate.stop_loss_activity_count`：`10` → `"<未设置>"`
  - `parameters.legacy_symbol_mapping`：`"forced_rebuy_pct is prior c; stop_loss_pct is prior d"` → `"<未设置>"`
  - `parameters.multiple_testing.pbo_metric`：`"Annualized daily Sharpe from the selected six-block training half and its six-block complement; the 12 chronological blocks are fixed before screening."` → `"Annualized daily Sharpe from the selected six-block training half and its complement across all 1,444 eligible cases."`
  - `parameters.multiple_testing.trial_count_policy`：`"Report correlation-adjusted effective trials and the conservative full 28,880-case count. DSR is applied only to predeclared surface representatives after their identities are fr…` → `"Report correlation-adjusted effective trials and the conservative full 1,444-case count; apply DSR only after the surface representative is frozen."`
  - `parameters.ordinary_entry_timing`：`"<未设置>"` → `"completed close crossing above buy SMA, next-session Open fill"`
  - `parameters.ordinary_probation_failure`：`"<未设置>"` → `"completed close below current buy SMA before confirmation, next-session Open exit"`
  - `parameters.paired_correction_analysis`：`"For every buy/sell period pair and forced-rebuy value, compare each enabled stop loss with the stop-loss-disabled control; report metric deltas and primary stop-loss event counts…` → `"<未设置>"`
  - `parameters.parent_control.anchor_buy_window`：`"<未设置>"` → `310`
  - `parameters.parent_control.anchor_sell_window`：`"<未设置>"` → `190`
  - ……另有 22 项，完整定义见父子 `experiment.json`。

### TIM-v0.50b.1 → TIM-v0.50b.2 · QQQ 双均线恢复试探角色约束网格

- 关系：`constrains_sma_role_ordering`
- 为什么改：无角色约束的首轮平台代表为买入SMA270、卖出SMA390，交换了原策略中长均线确认恢复和短均线快速退出的职责，虽然账本正确却不能回答用户要寻找合适短线/长线组合的问题。
- 策略修改：成交状态机、c=3%、d关闭、成本、区间和1,444格计算全部不变；新增buy_window>sell_window硬门禁，只有703个角色一致的周期对能参与冠军、二维平台、PBO和DSR。平台触及buy=450、sell=80或buy=sell+10语义边界时失败，代表还必须拥有完整合法且事件可识别的3×3邻域。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015/experiment.json) · [子实验](../TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.formal_reconciliation.anchors`：`"SMA200/SMA200 and inherited SMA310/SMA190"` → `"SMA200/SMA200 is retained as an intentionally role-ineligible execution control; SMA310/SMA190 is the inherited valid anchor."`
  - `parameters.formal_reconciliation.objective_champions`：`"Deduplicated full-history CAGR, Sharpe and maximum-drawdown champions."` → `"Deduplicated role-consistent full-history CAGR, Sharpe and maximum-drawdown champions."`
  - `parameters.formal_reconciliation.requirement`：`"Every case must match two independently structured compiled ledgers within 1e-9; all formal candidates must match PyBroker and the Python reference ledger in dates, sides, shares…` → `"Every one of the 1,444 calculated cases must match two independently structured compiled ledgers within 1e-9; all formal candidates must match PyBroker and the Python reference l…`
  - `parameters.formal_reconciliation.surface_representative`：`"Reconcile the deterministic local-stability representative even when a structural or statistical gate fails."` → `"Reconcile the deterministic role-constrained local-stability representative even when a structural or statistical gate fails."`
  - `parameters.identifiability_gate.purpose`：`"Require both SMA roles and the new failed-recovery mechanism to occur often enough before interpreting a parameter pair."` → `"Require the strict long-buy/short-sell role relation, both SMA events and the new failed-recovery mechanism before interpreting a parameter pair."`
  - `parameters.multiple_testing.pbo_metric`：`"Annualized daily Sharpe from the selected six-block training half and its complement across all 1,444 eligible cases."` → `"Annualized daily Sharpe from the selected six-block training half and its complement across role-consistent identifiable cases only."`
  - `parameters.multiple_testing.trial_count_policy`：`"Report correlation-adjusted effective trials and the conservative full 1,444-case count; apply DSR only after the surface representative is frozen."` → `"Report correlation-adjusted effective trials and the conservative full eligible role-consistent case count; apply DSR only after the constrained surface representative is frozen."`
  - `parameters.parent_control.comparison`：`"Compare all matching 38x38 cells and the old/new 310/190 paths without modifying the validated parent run."` → `"Compare all matching 38x38 cells and the old/new 310/190 paths without modifying the validated parent run; selection and statistical gates use only buy_window > sell_window."`
  - `parameters.role_consistent_pair_count`：`"<未设置>"` → `703`
  - `parameters.selection_role_constraint.execution_policy`：`"<未设置>"` → `"Calculate and retain all 1,444 cells for paired diagnostics, but reversed and equal-role cells cannot enter champions, surfaces, PBO, DSR or promotion gates."`
  - `parameters.selection_role_constraint.meaning`：`"<未设置>"` → `"The recovery-entry SMA must be strictly longer than the confirmed-exit SMA."`
  - `parameters.selection_role_constraint.rule`：`"<未设置>"` → `"buy_window > sell_window"`
  - `parameters.semantic_parent.experiment_id`：`"<未设置>"` → `"qqq_sma_recovery_probation_grid_2000_2015_v1"`
  - `parameters.semantic_parent.finding`：`"<未设置>"` → `"The unrestricted representative buy270/sell390 reversed the intended SMA roles, so it is a rejection diagnostic rather than a valid candidate."`
  - `parameters.semantic_parent.run_id`：`"<未设置>"` → `"run_20260821T161353Z_a44cfa59"`
  - `parameters.surface_selection.boundary_policy`：`"A component touching 80 or 450 is reported but cannot nominate a stable period region."` → `"A component touching buy=450, sell=80 or the semantic diagonal buy=sell+10 is reported but cannot nominate a stable region."`
  - `parameters.surface_selection.connectivity`：`"four-neighbor adjacency at one 10-session step"` → `"four-neighbor adjacency at one 10-session step within buy_window > sell_window"`
  - `parameters.surface_selection.representative_rule`：`"Within the largest eligible top-decile component require a complete 3x3 neighborhood and maximize local median primary metric minus 0.5 times local standard deviation; break ties…` → `"Within the largest eligible top-decile component require a complete role-consistent and event-identifiable 3x3 neighborhood; maximize local median primary metric minus 0.5 times …`
  - `parameters.surface_selection.supplementary_champions`：`["full-history CAGR","full-history Sharpe","least-negative maximum drawdown"]` → `["full-history CAGR among role-consistent identifiable cells","full-history Sharpe among role-consistent identifiable cells","least-negative maximum drawdown among role-consistent…`
  - `strategy.buy_rule`：`"空仓时，普通买入要求 prior_close <= prior_buy_sma 且 current_close > current_buy_sma；该收盘信号在下一交易日 Open 全仓买入。若最近一次确认多头由 sell-SMA 向下穿越卖出后，本熊市阶段尚未使用 c，则同时保留一次 last_confirmed_sell_effective_fill…` → `"空仓时，普通买入要求 prior_close <= prior_buy_sma 且 current_close > current_buy_sma；该收盘信号在下一交易日 Open 全仓买入。若最近一次确认多头由 sell-SMA 向下穿越卖出后，本熊市阶段尚未使用 c，则同时保留一次 last_confirmed_sell_effective_fill…`
  - `strategy.description`：`"QQQ 初始空仓，以较短的卖出 SMA 负责下跌时快速退出，以较长的买入 SMA 负责熊市后的恢复入场。普通买入必须由完成收盘确认从长均线下方向上穿越，并在下一交易日 Open 成交；任何新买入先进入试探持仓。普通买入若在站上短均线前完成收盘重新落到长均线下方，则下一 Open 失败退出。短均线卖出后的 3% 强制买回每个熊市阶段最多一次；其试探持仓若在…` → `"QQQ 初始空仓，以较短的卖出 SMA 负责下跌时快速退出，以较长的买入 SMA 负责熊市后的恢复入场。普通买入必须由完成收盘确认从长均线下方向上穿越，并在下一交易日 Open 成交；任何新买入先进入试探持仓。普通买入若在站上短均线前完成收盘重新落到长均线下方，则下一 Open 失败退出。短均线卖出后的 3% 强制买回每个熊市阶段最多一次；其试探持仓若在…`
  - `strategy.name`：`"qqq_two_sma_recovery_probation_with_single_forced_rebuy"` → `"qqq_two_sma_recovery_probation_role_constrained_selection"`
  - `strategy.plain_language.buy`：`"空仓后，只有收盘真正从长均线下方回到上方，才在下一交易日开盘买入；短均线卖出后另保留一次上涨3%的纠错买回机会。"` → `"空仓后，只有收盘真正从较长买入均线下方回到上方，才在下一交易日开盘买入；短均线卖出后另保留一次上涨3%的纠错买回机会。"`
  - `strategy.plain_language.sell`：`"新买入在站上短均线前若重新跌破自己的失败边界就退出；站上短均线后，下一次盘中向下穿过短均线时正常卖出。"` → `"新买入在站上较短卖出均线前若重新跌破自己的失败边界就退出；站上短均线后，下一次盘中向下穿过短均线时正常卖出。"`
  - ……另有 1 项，完整定义见父子 `experiment.json`。

### TIM-v0.40 → TIM-v0.40a.1 · 熊市双坑位与峰值回撤止损消融

- 关系：`ablates_weighting_and_trailing_stop`
- 为什么改：父实验的SMA路径显著落后且增加换手，需要按用户重新收窄15个候选，隔离五个重点标的双倍仓位是否有效，并检验按持仓峰值回撤直接退出能否改善熊市尾部风险。
- 策略修改：候选池改为15个唯一标的，EQT/GIS/LMT/ORLY/AZO可占双坑；SMA滞回由±2%改为±3%，移除成交价±10%锁，途中进入改为重新上穿；新增从实际进入后最高完成Close回撤6%/8%/10%/12%的强制退出，并与无止损及星标开关做2×5完整消融。
- 修改前：BEAR_EVENT_PORTFOLIOS；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR_EVENT_WEIGHTED_STOPS；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios/experiment.json) · [子实验](../TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation/experiment.json)
- 自动配置差异：

  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何池内标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"` → `"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"`
  - `parameters.core12`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]` → `"<未设置>"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics`：`"<未设置>"` → `"start使用严格水平条件；途中和强制退出后要求先观察上轨之下，再严格上穿上轨"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.filter_modes`：`["no_filter","sma200_hysteresis"]` → `"<未设置>"`
  - `parameters.forced_exit_priority`：`"<未设置>"` → `"trailing stop before SMA exit; record both active reasons but emit one sell target"`
  - `parameters.formal_cases`：`["core12_near8__no_filter","core12_near8__sma200_hysteresis","core12_retail4__no_filter","core12_retail4__sma200_hysteresis","core12_near8_retail4__no_filter","core12_near8_retail…` → `["star_off__stop_off","star_off__stop_06","star_off__stop_08","star_off__stop_10","star_off__stop_12","star_on__stop_off","star_on__stop_06","star_on__stop_08","star_on__stop_10",…`
  - `parameters.initial_entry_buffer_pct`：`0.0` → `"<未设置>"`
  - `parameters.lock_anchor_updates`：`"own_state_change_fills_only"` → `"<未设置>"`
  - `parameters.mid_bear_entry_buffer_pct`：`2.0` → `"<未设置>"`
  - `parameters.mid_bear_exit_buffer_pct`：`2.0` → `"<未设置>"`
  - `parameters.near_core8`：`["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]` → `"<未设置>"`
  - `parameters.no_filter_rebalancing`：`"equal_weight_at_bear_start_then_hold_until_end"` → `"<未设置>"`
  - `parameters.retail4`：`["DLTR","DG","WMT","TSCO"]` → `"<未设置>"`
  - `parameters.same_day_transition_order`：`"remove_exits_then_add_all_entries_simultaneously"` → `"remove exits then add all entries simultaneously"`
  - `parameters.slot_multipliers.star_off.default`：`"<未设置>"` → `1`
  - `parameters.slot_multipliers.star_off.starred`：`"<未设置>"` → `1`
  - `parameters.slot_multipliers.star_on.default`：`"<未设置>"` → `1`
  - `parameters.slot_multipliers.star_on.starred`：`"<未设置>"` → `2`
  - `parameters.star_modes`：`"<未设置>"` → `["star_off","star_on"]`
  - `parameters.starred_symbols`：`"<未设置>"` → `["EQT","GIS","LMT","ORLY","AZO"]`
  - `parameters.state_change_lock_band_pct`：`10.0` → `null`
  - `parameters.trailing_peak_price`：`"<未设置>"` → `"max(actual state-entry cost-adjusted Open fill, subsequent completed adjusted Close values)"`
  - ……另有 18 项，完整定义见父子 `experiment.json`。

### TIM-v0.10 → TIM-v0.20b.1 · QQQ 满仓长期斜率与短趋势质量训练

- 关系：`branches_from`
- 为什么改：SMA200价格状态在QQQ上仍有较深回撤，需要在坚持0%/100%满仓切换的前提下，检验长期趋势方向与短期趋势质量能否比固定止损更稳定地限定持有区间。
- 策略修改：从SMA200缓冲穿越改为可重复进入的Close>SMA长期状态，加入长期SMA平均日斜率F2和短SMA的OLS斜率×R²质量F4；QQQ/SPY相对强弱F5只作冻结P24后的消融。2000–2015训练先按每维回撤优先保留前三个OAT值，再完整运行至多2,187个联合case；OAT连续带只作诊断，最终代表才执行CAGR与1pp平台门禁。
- 修改前：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json) · [子实验](../TIM/TIM-v0.20b.1__26-08-15__qqq_full_position_sma_trend_quality_training_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.a_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.ablation_cases.B0`：`"<未设置>"` → `[]`
  - `parameters.ablation_cases.B2`：`"<未设置>"` → `["F2_LONG_SMA_SLOPE"]`
  - `parameters.ablation_cases.B4`：`"<未设置>"` → `["F4_SHORT_TREND_QUALITY"]`
  - `parameters.ablation_cases.P24`：`"<未设置>"` → `["F2_LONG_SMA_SLOPE","F4_SHORT_TREND_QUALITY"]`
  - `parameters.ablation_cases.P245`：`"<未设置>"` → `["F2_LONG_SMA_SLOPE","F4_SHORT_TREND_QUALITY","F5_QQQ_SPY_RELATIVE_STRENGTH"]`
  - `parameters.b_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.baseline.entry_confirmation_sessions`：`"<未设置>"` → `1`
  - `parameters.baseline.long_slope_lookback`：`"<未设置>"` → `20`
  - `parameters.baseline.long_slope_threshold_daily_pct`：`"<未设置>"` → `0.0`
  - `parameters.baseline.long_sma_window`：`"<未设置>"` → `200`
  - `parameters.baseline.relative_strength_lookback`：`"<未设置>"` → `60`
  - `parameters.baseline.short_quality_threshold_daily_pct`：`"<未设置>"` → `0.0`
  - `parameters.baseline.short_regression_window`：`"<未设置>"` → `10`
  - `parameters.baseline.short_sma_window`：`"<未设置>"` → `30`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.combination_count_per_surface`：`1089` → `"<未设置>"`
  - `parameters.formal_verification`：`"<未设置>"` → `"PyBroker independently verifies B0/B2/B4/P24/P245 at the frozen center and final P24 representative; the reference engine evaluates every screening case."`
  - `parameters.fractional_shares`：`"<未设置>"` → `true`
  - `parameters.relative_strength_ablation.core_parameters_frozen_before_ablation`：`"<未设置>"` → `true`
  - `parameters.relative_strength_ablation.lookbacks`：`"<未设置>"` → `[20,40,60,90,120]`
  - `parameters.relative_strength_ablation.threshold_log_return`：`"<未设置>"` → `0.0`
  - `parameters.robustness_subwindows`：`"<未设置>"` → `[{"end":"2003-12-31","start":"2000-01-03","window_id":"W1_2000_2003"},{"end":"2007-12-31","start":"2004-01-01","window_id":"W2_2004_2007"},{"end":"2011-12-31","start":"2008-01-01"…`
  - `parameters.selection.minimum_cagr_pct`：`"<未设置>"` → `2.0`
  - ……另有 26 项，完整定义见父子 `experiment.json`。

### TIM-v0.20b.1 → TIM-v0.20b.2 · QQQ 满仓F2/F4四维边界扩展

- 关系：`expands_search_bounds_of`
- 为什么改：父训练代表在长期SMA、长期斜率回看、F2门槛和短期SMA四维同时触及边界，无法判断低回撤来自稳定平台还是搜索截断，因此不能直接打开2016–2026。
- 策略修改：保持P24状态、0%/100%仓位、2000-03-17至2015-12-31、W10/F4门槛0/C2和0/5bps不变，只把L扩到120–200、K扩到合法自然下限1至20、F2门槛扩到0.01%–0.05%/日、S扩到8–30，共4,032组；新增四维Manhattan连通1pp平台、非边界和邻居数门禁，F5仅在代表冻结后固定R120消融。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.20b.1__26-08-15__qqq_full_position_sma_trend_quality_training_2000_2015/experiment.json) · [子实验](../TIM/TIM-v0.20b.2__26-08-15__qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.ablation_cases.B0`：`[]` → `"<未设置>"`
  - `parameters.ablation_cases.B2`：`["F2_LONG_SMA_SLOPE"]` → `"<未设置>"`
  - `parameters.ablation_cases.B4`：`["F4_SHORT_TREND_QUALITY"]` → `"<未设置>"`
  - `parameters.ablation_cases.P24`：`["F2_LONG_SMA_SLOPE","F4_SHORT_TREND_QUALITY"]` → `"<未设置>"`
  - `parameters.ablation_cases.P245`：`["F2_LONG_SMA_SLOPE","F4_SHORT_TREND_QUALITY","F5_QQQ_SPY_RELATIVE_STRENGTH"]` → `"<未设置>"`
  - `parameters.baseline.entry_confirmation_sessions`：`1` → `"<未设置>"`
  - `parameters.baseline.long_slope_lookback`：`20` → `"<未设置>"`
  - `parameters.baseline.long_slope_threshold_daily_pct`：`0.0` → `"<未设置>"`
  - `parameters.baseline.long_sma_window`：`200` → `"<未设置>"`
  - `parameters.baseline.relative_strength_lookback`：`60` → `"<未设置>"`
  - `parameters.baseline.short_quality_threshold_daily_pct`：`0.0` → `"<未设置>"`
  - `parameters.baseline.short_regression_window`：`10` → `"<未设置>"`
  - `parameters.baseline.short_sma_window`：`30` → `"<未设置>"`
  - `parameters.expanded_grid.combination_count`：`"<未设置>"` → `4032`
  - `parameters.expanded_grid.long_slope_lookback`：`"<未设置>"` → `[1,2,3,5,7,10,15,20]`
  - `parameters.expanded_grid.long_slope_threshold_daily_pct`：`"<未设置>"` → `[0.01,0.015,0.02,0.025,0.03,0.04,0.05]`
  - `parameters.expanded_grid.long_sma_window`：`"<未设置>"` → `[120,130,140,150,160,170,180,190,200]`
  - `parameters.expanded_grid.short_sma_window`：`"<未设置>"` → `[8,10,12,15,18,20,25,30]`
  - `parameters.fixed_f5_ablation.core_parameters_frozen_before_ablation`：`"<未设置>"` → `true`
  - `parameters.fixed_f5_ablation.enabled`：`"<未设置>"` → `true`
  - `parameters.fixed_f5_ablation.relative_strength_lookback`：`"<未设置>"` → `120`
  - `parameters.fixed_f5_ablation.threshold_log_return`：`"<未设置>"` → `0.0`
  - `parameters.fixed_parameters.entry_confirmation_sessions`：`"<未设置>"` → `2`
  - `parameters.fixed_parameters.relative_strength_lookback`：`"<未设置>"` → `120`
  - ……另有 38 项，完整定义见父子 `experiment.json`。

### TIM-v0.40 → TIM-v0.40b.1 · 24标的 ATR 固定硬止损四政策消融

- 关系：`branches_to_fixed_atr_stop_ablation`
- 为什么改：父实验显示SMA200完整择时在三个重叠股票池中都显著拖累收益且仍接近满仓，需要拆成24个单标的与三个原始分组，直接比较不择时、完整SMA、ATR灾难止损和只在止损后使用SMA确认四种实用政策。
- 策略修改：股票池展示从12+8、12+4、12+8+4改为24个单标的及核心12/近核心8/零售4三个固定等权袖套组合；SMA统一为上下3%且无10%锁；新增入场时Wilder ATR20决定、12%至20%截断的固定日内硬止损，以及价格线或SMA上轨两种止损后重新买入；组内退出资金保留现金，并强制报告剔除2000-2002结果。
- 修改前：BEAR_EVENT_PORTFOLIOS；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR24_ATR_ABLATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios/experiment.json) · [子实验](../TIM/TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"<未设置>"` → `"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"`
  - `parameters.atr_for_entry`：`"<未设置>"` → `"the last completed ATR20 on the buy signal date; the next-Open effective entry fill fixes the stop distance and line"`
  - `parameters.atr_method`：`"<未设置>"` → `"Wilder RMA initialized by the simple mean of the first 20 true ranges"`
  - `parameters.atr_multiplier`：`"<未设置>"` → `3.0`
  - `parameters.atr_stop_max_pct`：`"<未设置>"` → `20.0`
  - `parameters.atr_stop_min_pct`：`"<未设置>"` → `12.0`
  - `parameters.atr_window`：`"<未设置>"` → `20`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何池内标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"` → `"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"<未设置>"` → `"2000-2002"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`"<未设置>"` → `1`
  - `parameters.filter_modes`：`["no_filter","sma200_hysteresis"]` → `"<未设置>"`
  - `parameters.formal_case_count_per_cost`：`"<未设置>"` → `108`
  - `parameters.formal_cases`：`["core12_near8__no_filter","core12_near8__sma200_hysteresis","core12_retail4__no_filter","core12_retail4__sma200_hysteresis","core12_near8_retail4__no_filter","core12_near8_retail…` → `"<未设置>"`
  - `parameters.group_rebalance_policy`：`"<未设置>"` → `"re-equalize eligible member sleeves only at the start of each bear interval"`
  - `parameters.group_weighting`：`"<未设置>"` → `"equal independent capital sleeves among members with valid OHLC, SMA200 and ATR20 at each bear start"`
  - `parameters.groups.GROUP_CORE12`：`"<未设置>"` → `["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]`
  - `parameters.groups.GROUP_NEAR8`：`"<未设置>"` → `["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]`
  - `parameters.groups.GROUP_RETAIL4`：`"<未设置>"` → `["DLTR","DG","WMT","TSCO"]`
  - `parameters.hard_stop_activation`：`"<未设置>"` → `"immediately after the entry Open fill, including the entry session"`
  - `parameters.hard_stop_fill`：`"<未设置>"` → `"Open when Open is at or below the fixed line; otherwise the fixed line when Low touches it; symmetric adverse cost adjustment is then applied"`
  - `parameters.hard_stop_type`：`"<未设置>"` → `"fixed_from_effective_entry_fill_not_trailing"`
  - `parameters.inactive_sleeve_policy`：`"<未设置>"` → `"cash retained in the same member sleeve; never redistributed within the bear interval"`
  - `parameters.initial_entry_buffer_pct`：`0.0` → `"<未设置>"`
  - `parameters.interval_liquidation`：`"end_close_signal_next_common_open"` → `"end completed Close signal, next common adjusted Open"`
  - ……另有 32 项，完整定义见父子 `experiment.json`。

### TIM-v0.40a.2 → TIM-v0.40b.1 · 24标的 ATR 固定硬止损四政策消融

- 关系：`informs_fixed_policy_and_reentry_design`
- 为什么改：八标的峰值回撤网格表明孤立历史阈值和2000-2002贡献会误导选择，因此新实验停止扫描阈值，改用入场波动冻结的单一止损公式，并把止损后重新买入方式作为明确政策消融。
- 策略修改：由峰值移动回撤3%至20%网格改为固定的3倍Wilder ATR20、12%至20%截断；恢复全部24个候选和三个分组；保留可再次买入，但分别测试原止损线恢复与SMA200+3%重新武装上穿，并对每张表增加剔除首段复合收益。
- 修改前：BEAR_SELECTED8_STOP_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR24_ATR_ABLATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid/experiment.json) · [子实验](../TIM/TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"总小熊、总大熊、总熊及剔除2000-2002总熊均按对应逐段账户收益几何复合；某段因SMA择时全程无持仓时该段收益严格记为0。"` → `"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"`
  - `parameters.atr_for_entry`：`"<未设置>"` → `"the last completed ATR20 on the buy signal date; the next-Open effective entry fill fixes the stop distance and line"`
  - `parameters.atr_method`：`"<未设置>"` → `"Wilder RMA initialized by the simple mean of the first 20 true ranges"`
  - `parameters.atr_multiplier`：`"<未设置>"` → `3.0`
  - `parameters.atr_stop_max_pct`：`"<未设置>"` → `20.0`
  - `parameters.atr_stop_min_pct`：`"<未设置>"` → `12.0`
  - `parameters.atr_window`：`"<未设置>"` → `20`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；八个标的上市前缺席不影响日历，任何标的上市后缺少Open/Close的日期从全部case共同剔除，禁止填造价格或在非共同日成交；熊市start/end必须保留。"` → `"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"`
  - `parameters.core12`：`"<未设置>"` → `["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]`
  - `parameters.entry_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.entry_semantics`：`"start使用严格水平条件；途中和峰值/SMA退出后要求先观察上轨之下或等于上轨，再严格上穿上轨"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"<未设置>"` → `"2000-2002"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`"<未设置>"` → `1`
  - `parameters.exit_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.forced_exit_priority`：`"trailing stop before SMA exit; record one sell target"` → `"<未设置>"`
  - `parameters.formal_case_count_per_cost`：`171` → `108`
  - `parameters.group_rebalance_policy`：`"<未设置>"` → `"re-equalize eligible member sleeves only at the start of each bear interval"`
  - `parameters.group_weighting`：`"<未设置>"` → `"equal independent capital sleeves among members with valid OHLC, SMA200 and ATR20 at each bear start"`
  - `parameters.groups.GROUP_CORE12`：`"<未设置>"` → `["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]`
  - `parameters.groups.GROUP_NEAR8`：`"<未设置>"` → `["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]`
  - `parameters.groups.GROUP_RETAIL4`：`"<未设置>"` → `["DLTR","DG","WMT","TSCO"]`
  - `parameters.hard_stop_activation`：`"<未设置>"` → `"immediately after the entry Open fill, including the entry session"`
  - `parameters.hard_stop_fill`：`"<未设置>"` → `"Open when Open is at or below the fixed line; otherwise the fixed line when Low touches it; symmetric adverse cost adjustment is then applied"`
  - `parameters.hard_stop_type`：`"<未设置>"` → `"fixed_from_effective_entry_fill_not_trailing"`
  - ……另有 33 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.1 → TIM-v0.40b.2 · 24标的完整SMA窗口高原诊断

- 关系：`replaces_stop_ablation_with_sma_window_stability_scan`
- 为什么改：父实验显示ATR固定硬止损没有改善三个分组，而用户逐标的复核认为完整SMA择时仍有作用；需要移除无效止损自由度，并检查SMA200的结论是否只是固定窗口造成，还是每个标的在相邻长期窗口上存在稳定高原。
- 策略修改：保留A持有、24个单标的、核心12/近核心8/零售4固定袖套、上下3%滞回、卖出后再买入、次日Open和0/5bps；删除ATR与峰值止损政策，把固定SMA200替换为SMA30至SMA300步长10的28条完整择时路径；全部窗口共用SMA300预热样本，并把逐标的16行表替换为SMA收益实线、Hold水平虚线和观察范围下拉。
- 修改前：BEAR24_ATR_ABLATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR24_SMA_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json) · [子实验](../TIM/TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json)
- 自动配置差异：

  - `parameters.atr_for_entry`：`"the last completed ATR20 on the buy signal date; the next-Open effective entry fill fixes the stop distance and line"` → `"<未设置>"`
  - `parameters.atr_method`：`"Wilder RMA initialized by the simple mean of the first 20 true ranges"` → `"<未设置>"`
  - `parameters.atr_multiplier`：`3.0` → `"<未设置>"`
  - `parameters.atr_stop_max_pct`：`20.0` → `"<未设置>"`
  - `parameters.atr_stop_min_pct`：`12.0` → `"<未设置>"`
  - `parameters.atr_window`：`20` → `"<未设置>"`
  - `parameters.baseline_strategy_id`：`"<未设置>"` → `"hold"`
  - `parameters.common_warmup_window`：`"<未设置>"` → `300`
  - `parameters.default_report_scope`：`"<未设置>"` → `"all_ex_2000_2002"`
  - `parameters.descriptive_plateau_rule`：`"<未设置>"` → `"within 5 percentage points of the target-and-scope maximum, contiguous in 10-day steps, at least three windows; longest run wins, then higher mean return, then smaller starting w…`
  - `parameters.formal_case_count_per_cost`：`108` → `783`
  - `parameters.group_weighting`：`"equal independent capital sleeves among members with valid OHLC, SMA200 and ATR20 at each bear start"` → `"equal independent capital sleeves among members with valid OHLC and common SMA300 warmup at each bear start"`
  - `parameters.hard_stop`：`"<未设置>"` → `null`
  - `parameters.hard_stop_activation`：`"immediately after the entry Open fill, including the entry session"` → `"<未设置>"`
  - `parameters.hard_stop_fill`：`"Open when Open is at or below the fixed line; otherwise the fixed line when Low touches it; symmetric adverse cost adjustment is then applied"` → `"<未设置>"`
  - `parameters.hard_stop_type`：`"fixed_from_effective_entry_fill_not_trailing"` → `"<未设置>"`
  - `parameters.policies`：`["A_hold","B_sma200_full","C_atr_stop_price_reentry","D_atr_stop_sma_reentry"]` → `"<未设置>"`
  - `parameters.simple_reentry`：`"ignore the stop session; from the next session, first completed Close strictly above the original fixed stop line signals next-Open buy"` → `"<未设置>"`
  - `parameters.sma_entry`：`"<未设置>"` → `"initial Close strictly above SMA_N plus 3%, or later armed strict cross above the same upper rail"`
  - `parameters.sma_exit`：`"<未设置>"` → `"held completed Close strictly below SMA_N minus 3%"`
  - `parameters.sma_reentry`：`"ignore the stop session; later Close at or below SMA200 plus 3% arms, and a still later strict Close cross above that level signals next-Open buy"` → `"after an SMA exit the path is armed flat and may buy at the next completed Close strictly above SMA_N plus 3%"`
  - `parameters.sma_window`：`200` → `"<未设置>"`
  - `parameters.sma_window_end`：`"<未设置>"` → `300`
  - `parameters.sma_window_start`：`"<未设置>"` → `30`
  - ……另有 15 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.2 → TIM-v0.40b.3 · 六标的SMA局部细网格诊断

- 关系：`refines_asset_specific_sma_windows`
- 为什么改：父实验的步长10全窗口图已确认不同标的响应尺度明显不同，用户需要放大六只重点标的的局部范围，区分宽高原、平滑斜坡与粗网格遗漏的孤立尖峰。
- 策略修改：保留事后12段熊市、Hold基准、上下3%滞回、卖出后再买入、次日Open、0/5bps和同款v5折线UI；目标收窄为AZO/TLT/SO/ED/MO/GIS六只单标的，分别扫描190–270/1、130–200/1、1–50/1、1–50/1、80–120/1和270–500/10，并统一使用SMA500预热。用户给SO/ED的0下界解释为独立Hold基准加最小有效SMA1，不构造SMA0。
- 修改前：BEAR24_SMA_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：BEAR6_SMA_FINE_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json) · [子实验](../TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json)
- 自动配置差异：

  - `parameters.common_warmup_window`：`300` → `500`
  - `parameters.core12`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous in 10-day steps, at least three windows; longest run wins, then higher mean return, then smaller starting w…` → `"within 5 percentage points of the target-and-scope maximum, contiguous using that target's frozen grid step, at least three windows; longest run wins, then higher mean return, th…`
  - `parameters.formal_case_count_per_cost`：`783` → `323`
  - `parameters.formal_sma_windows_by_target.AZO`：`"<未设置>"` → `[190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,23…`
  - `parameters.formal_sma_windows_by_target.ED`：`"<未设置>"` → `[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50]`
  - `parameters.formal_sma_windows_by_target.GIS`：`"<未设置>"` → `[270,280,290,300,310,320,330,340,350,360,370,380,390,400,410,420,430,440,450,460,470,480,490,500]`
  - `parameters.formal_sma_windows_by_target.MO`：`"<未设置>"` → `[80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120]`
  - `parameters.formal_sma_windows_by_target.SO`：`"<未设置>"` → `[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50]`
  - `parameters.formal_sma_windows_by_target.TLT`：`"<未设置>"` → `[130,131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,17…`
  - `parameters.group_rebalance_policy`：`"re-equalize eligible member sleeves only at the start of each bear interval"` → `"<未设置>"`
  - `parameters.group_weighting`：`"equal independent capital sleeves among members with valid OHLC and common SMA300 warmup at each bear start"` → `null`
  - `parameters.groups.GROUP_CORE12`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]` → `"<未设置>"`
  - `parameters.groups.GROUP_NEAR8`：`["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]` → `"<未设置>"`
  - `parameters.groups.GROUP_RETAIL4`：`["DLTR","DG","WMT","TSCO"]` → `"<未设置>"`
  - `parameters.inactive_sleeve_policy`：`"cash retained in the same member sleeve; never redistributed within the bear interval"` → `"<未设置>"`
  - `parameters.near_core8`：`["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]` → `"<未设置>"`
  - `parameters.retail4`：`["DLTR","DG","WMT","TSCO"]` → `"<未设置>"`
  - `parameters.sma_window_end`：`300` → `"<未设置>"`
  - `parameters.sma_window_start`：`30` → `"<未设置>"`
  - `parameters.sma_window_step`：`10` → `"<未设置>"`
  - `parameters.sma_windows`：`[30,40,50,60,70,80,90,100,110,120,130,140,150,160,170,180,190,200,210,220,230,240,250,260,270,280,290,300]` → `"<未设置>"`
  - `parameters.target_window_ranges.AZO.end`：`"<未设置>"` → `270`
  - `parameters.target_window_ranges.AZO.start`：`"<未设置>"` → `190`
  - ……另有 30 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.3 → TIM-v0.40c.1 · MO/AZO/TLT SMA200空仓期归因

- 关系：`narrows_to_fixed_full_history_timing_and_attributes_flat_spells`
- 为什么改：六标的局部扫描后，用户只保留MO、AZO和TLT，并希望停止继续挑窗口，回到统一SMA200上下3%的简单机械择时，直接检查每次卖出后的空仓究竟避开多少下跌或错过多少上涨。
- 策略修改：目标由六只收窄为MO/AZO/TLT，删除事后熊市内交易限制和全部窗口网格，改为各自完整历史上的固定SMA200真实上穿买入、下轨卖出、卖出后可再入与下一Open成交；新增每段空仓的Open到Open回报、Low/High极值、5/10/20/60日表现和大小熊重叠归因，同时保留0/5bps完整策略与Hold对照。
- 修改前：BEAR6_SMA_FINE_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json) · [子实验](../TIM/TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-03-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-03-24"` → `"symbol_specific_first_valid_sma200"`
  - `parameters.baseline_strategy_id`：`"hold"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"` → `"<未设置>"`
  - `parameters.common_warmup_window`：`500` → `"<未设置>"`
  - `parameters.default_report_scope`：`"all_ex_2000_2002"` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous using that target's frozen grid step, at least three windows; longest run wins, then higher mean return, th…` → `"<未设置>"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"2000-2002"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`1` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.exit_semantics`：`"<未设置>"` → `"held completed close strictly below lower rail"`
  - `parameters.fill_time`：`"<未设置>"` → `"next symbol-session adjusted open"`
  - `parameters.first_valid_sma_bar_can_enter`：`"<未设置>"` → `false`
  - `parameters.flat_spell_end`：`"<未设置>"` → `"next buy fill adjusted open, exclusive of the reentry session after its open"`
  - `parameters.flat_spell_path_extremes`：`"<未设置>"` → `"adjusted low/high from sell-fill session through the session before reentry; open final spell through the final session"`
  - `parameters.flat_spell_start`：`"<未设置>"` → `"sell fill adjusted open, inclusive"`
  - `parameters.formal_case_count_per_cost`：`323` → `3`
  - `parameters.formal_path_count`：`"<未设置>"` → `6`
  - `parameters.formal_sma_windows_by_target.AZO`：`[190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,23…` → `"<未设置>"`
  - ……另有 57 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.3 → TIM-v0.40c.2 · QQQ空仓期三替代标的择时

- 关系：`freezes_selected_asset_specific_windows_for_substitutes`
- 为什么改：用户从六标的局部细网格图中只保留MO、AZO、TLT，并明确沿用先前确定的237、160、110三个窗口；新实验必须保留这一选择来源而不能把它误写成样本外固定参数。
- 策略修改：从父诊断固定AZO237、TLT160、MO110，不再使用统一SMA200或重新扫描参数；三个窗口只控制QQQ空仓期间各替代品是否可持有。
- 修改前：BEAR6_SMA_FINE_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json) · [子实验](../TIM/TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-03-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-03-24"` → `"first_valid_qqq_sma200_close"`
  - `parameters.baseline_strategy_id`：`"hold"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"` → `"<未设置>"`
  - `parameters.common_warmup_window`：`500` → `"<未设置>"`
  - `parameters.default_report_scope`：`"all_ex_2000_2002"` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous using that target's frozen grid step, at least three windows; longest run wins, then higher mean return, th…` → `"<未设置>"`
  - `parameters.dotcom_exclusion_rule`：`"<未设置>"` → `"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics_after_initialization`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"2000-2002"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`1` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.exit_semantics`：`"<未设置>"` → `"held completed close strictly below lower rail"`
  - `parameters.fill_time`：`"<未设置>"` → `"next QQQ common-session adjusted open"`
  - `parameters.formal_case_count_per_cost`：`323` → `4`
  - `parameters.formal_cases`：`"<未设置>"` → `["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110"]`
  - `parameters.formal_path_count`：`"<未设置>"` → `8`
  - `parameters.formal_sma_windows_by_target.AZO`：`[190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,23…` → `"<未设置>"`
  - `parameters.formal_sma_windows_by_target.ED`：`[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50]` → `"<未设置>"`
  - `parameters.formal_sma_windows_by_target.GIS`：`[270,280,290,300,310,320,330,340,350,360,370,380,390,400,410,420,430,440,450,460,470,480,490,500]` → `"<未设置>"`
  - ……另有 63 项，完整定义见父子 `experiment.json`。

### TIM-v0.40c.1 → TIM-v0.40c.2 · QQQ空仓期三替代标的择时

- 关系：`corrects_attribution_target_to_qqq_master_flat_substitution`
- 为什么改：用户澄清上一实验分析错了空仓主体：问题不是MO、AZO、TLT自身SMA200卖出后怎样，而是QQQ的SMA200择时空仓时，用这三只标的分别接替会怎样。
- 策略修改：把QQQ SMA200上下3%设为账户总开关；QQQ持仓时只持有QQQ，QQQ空仓时分别用AZO/SMA237、TLT/SMA160、MO/SMA110上下3%连续择时接替，不满足自身条件则现金；新增QQQ_CASH直接基线、逐QQQ空仓期贡献和剔除2000-2002汇总。
- 修改前：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution/experiment.json) · [子实验](../TIM/TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json)
- 自动配置差异：

  - `parameters.analysis_start`：`"symbol_specific_first_valid_sma200"` → `"first_valid_qqq_sma200_close"`
  - `parameters.dotcom_exclusion_rule`：`"<未设置>"` → `"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"`
  - `parameters.entry_semantics`：`"true close crossing from <= upper rail to > upper rail"` → `"<未设置>"`
  - `parameters.entry_semantics_after_initialization`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.fill_time`：`"next symbol-session adjusted open"` → `"next QQQ common-session adjusted open"`
  - `parameters.first_valid_sma_bar_can_enter`：`false` → `"<未设置>"`
  - `parameters.flat_spell_end`：`"next buy fill adjusted open, exclusive of the reentry session after its open"` → `"<未设置>"`
  - `parameters.flat_spell_path_extremes`：`"adjusted low/high from sell-fill session through the session before reentry; open final spell through the final session"` → `"<未设置>"`
  - `parameters.flat_spell_start`：`"sell fill adjusted open, inclusive"` → `"<未设置>"`
  - `parameters.formal_case_count_per_cost`：`3` → `4`
  - `parameters.formal_cases`：`"<未设置>"` → `["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110"]`
  - `parameters.formal_path_count`：`6` → `8`
  - `parameters.forward_return_definition`：`"return from raw sell-fill Open to adjusted Close after N cash sessions counting the sell-fill session as session one; require a complete N-session horizon"` → `"<未设置>"`
  - `parameters.forward_sessions_after_sell`：`[5,10,20,60]` → `"<未设置>"`
  - `parameters.initial_wait_reported_separately`：`"<未设置>"` → `true`
  - `parameters.master_first_valid_sma_bar_can_enter`：`"<未设置>"` → `false`
  - `parameters.master_priority_on_reentry`：`"<未设置>"` → `true`
  - `parameters.master_sma_window`：`"<未设置>"` → `200`
  - `parameters.master_symbol`：`"<未设置>"` → `"QQQ"`
  - `parameters.open_final_spell_mark_to_market`：`"last approved adjusted close"` → `"<未设置>"`
  - `parameters.open_final_spell_reported_separately`：`"<未设置>"` → `true`
  - `parameters.primary_flat_spell_scope`：`"<未设置>"` → `"post_QQQ_sell_fill_to_next_QQQ_buy_fill"`
  - `parameters.reentry`：`"unlimited, using the same true upper-rail crossing"` → `"<未设置>"`
  - `parameters.rotation_order`：`"<未设置>"` → `"sell old asset first, then buy new asset with all available cash"`
  - ……另有 21 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.2 → TIM-v0.40c.3 · QQQ空仓期七替代候选扩展

- 关系：`freezes_additional_post_2000_candidate_windows`
- 为什么改：24标的完整窗口诊断提供了三只新增股票在剔除2000熊市后的候选证据，需要把选择来源显式保留，防止把全样本事后窗口误解为样本外参数。
- 策略修改：从历史诊断固定EQT240、WMT30、ORLY260、LMT30：EQT作为剔除2000后仍为正且邻近窗口同向的强候选，WMT/ORLY/LMT作为边界或近零观察组；不把SO、ED、GIS、COR、HRL重新纳入。
- 修改前：BEAR24_SMA_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json) · [子实验](../TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-03-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-03-24"` → `"first_valid_qqq_sma200_close"`
  - `parameters.baseline_strategy_id`：`"hold"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"` → `"<未设置>"`
  - `parameters.common_warmup_window`：`300` → `"<未设置>"`
  - `parameters.core12`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]` → `"<未设置>"`
  - `parameters.default_report_scope`：`"all_ex_2000_2002"` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous in 10-day steps, at least three windows; longest run wins, then higher mean return, then smaller starting w…` → `"<未设置>"`
  - `parameters.dotcom_exclusion_rule`：`"<未设置>"` → `"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics_after_initialization`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"2000-2002"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`1` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.exit_semantics`：`"<未设置>"` → `"held completed close strictly below lower rail"`
  - `parameters.fill_time`：`"<未设置>"` → `"next QQQ common-session adjusted open"`
  - `parameters.formal_case_count_per_cost`：`783` → `8`
  - `parameters.formal_cases`：`"<未设置>"` → `["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110","QQQ_EQT240","QQQ_WMT30","QQQ_ORLY260","QQQ_LMT30"]`
  - `parameters.formal_path_count`：`"<未设置>"` → `16`
  - `parameters.group_rebalance_policy`：`"re-equalize eligible member sleeves only at the start of each bear interval"` → `"<未设置>"`
  - `parameters.group_weighting`：`"equal independent capital sleeves among members with valid OHLC and common SMA300 warmup at each bear start"` → `"<未设置>"`
  - ……另有 52 项，完整定义见父子 `experiment.json`。

### TIM-v0.40c.2 → TIM-v0.40c.3 · QQQ空仓期七替代候选扩展

- 关系：`expands_substitute_candidate_comparison`
- 为什么改：父实验已经冻结并验证QQQ总开关、下一Open成交和三条替代路径；用户希望根据此前结果再加入少数有潜力的候选，因此应只扩展独立对比路径，避免同时改变账户规则。
- 策略修改：完整保留QQQ SMA200上下3%、AZO237、TLT160、MO110、现金基线、0/5bps、逐空仓段和剔除2000归因；新增EQT240、WMT30、ORLY260、LMT30四条独立替代路径，不建立混合组合，也不重新扫描参数。
- 修改前：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json) · [子实验](../TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json)
- 自动配置差异：

  - `parameters.formal_case_count_per_cost`：`4` → `8`
  - `parameters.formal_cases`：`["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110"]` → `["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110","QQQ_EQT240","QQQ_WMT30","QQQ_ORLY260","QQQ_LMT30"]`
  - `parameters.formal_path_count`：`8` → `16`
  - `parameters.substitute_windows.EQT`：`"<未设置>"` → `240`
  - `parameters.substitute_windows.LMT`：`"<未设置>"` → `30`
  - `parameters.substitute_windows.ORLY`：`"<未设置>"` → `260`
  - `parameters.substitute_windows.WMT`：`"<未设置>"` → `30`
  - `strategy.buy_rule`：`"QQQ从不高于SMA200上轨变为严格高于上轨时，账户下一Open全仓买入QQQ。QQQ处于空仓状态时，若对应替代品的连续滞回状态为可持有，下一Open全仓买入AZO、TLT或MO；替代品首次形成自身SMA时，若已严格高于上轨可直接建立可持有状态，之后卖出后必须再次真实上穿上轨才重新进入。"` → `"QQQ从不高于SMA200上轨变为严格高于上轨时，账户下一Open全仓买入QQQ。QQQ处于空仓状态时，若对应替代品的连续滞回状态为可持有，下一Open全仓买入该替代品；替代品首次形成自身SMA时若已严格高于上轨可直接建立可持有状态，之后卖出后必须再次真实上穿上轨才重新进入。"`
  - `strategy.description`：`"以QQQ的SMA200上下3%滞回择时作为账户总开关。QQQ空仓时分别建立三条互斥替代路径：AZO使用SMA237、TLT使用SMA160、MO使用SMA110，替代品也采用上下3%滞回；替代品状态连续计算，QQQ空仓且替代品处于可持有状态时全仓持有该替代品，否则持有现金。QQQ重新进入可持有状态时优先恢复QQQ。所有信号使用完成Close并在下一QQQ…` → `"以QQQ的SMA200上下3%滞回择时作为账户总开关。QQQ空仓时分别建立七条互斥替代路径：AZO使用SMA237、TLT使用SMA160、MO使用SMA110、EQT使用SMA240、WMT使用SMA30、ORLY使用SMA260、LMT使用SMA30；替代品也采用上下3%滞回，状态在QQQ持仓期间继续计算。QQQ空仓且指定替代品处于可持有状态时全仓持…`
  - `strategy.name`：`"qqq_sma200_flat_trio_asset_specific_substitution"` → `"qqq_sma200_flat_expanded_asset_specific_substitution"`
  - `strategy.plain_language.buy`：`"QQQ处于自己的多头状态就持有QQQ；QQQ空仓时，只在替代品通过自己的长期均线门槛时持有它，否则留现金。"` → `"QQQ处于自己的多头状态就持有QQQ；QQQ空仓时，只在指定替代品通过自己的长期均线门槛时持有它，否则留现金。"`
  - `strategy.plain_language.position`：`"三条路径分别只持有QQQ、一个指定替代品或现金，不把AZO、TLT、MO混在同一个账户里。"` → `"八条路径彼此独立，每条路径只持有QQQ、一个指定替代品或现金，不把七只候选混在同一个账户里。"`
  - `strategy.plain_language.summary`：`"QQQ该空仓时，不只放现金，而是分别尝试用已通过各自长期均线的AZO、TLT或MO接替。"` → `"QQQ该空仓时，分别尝试用七只已通过各自长期均线的候选标的接替，并与继续持有现金比较。"`
  - `strategy.positioning`：`"long-only、不融资、现金不计息。每条路径任一时刻只可能100%持有QQQ、100%持有一个指定替代品或100%现金。AZO、TLT、MO三条路径各使用独立10万美元账户。"` → `"long-only、不融资、现金不计息。每条路径任一时刻只可能100%持有QQQ、100%持有一个指定替代品或100%现金。七条替代路径及QQQ_CASH基线各使用独立10万美元账户。"`
  - `strategy.sell_rule`：`"持有QQQ且其完成Close严格低于SMA200下轨时，下一Open卖出QQQ；若替代品当时可持有则同一Open接替，否则转现金。持有替代品时，其完成Close严格低于自身SMA下轨就下一Open卖出并转现金。QQQ一旦重新上穿自己的上轨，下一Open无条件先卖出替代品再恢复QQQ。"` → `"持有QQQ且其完成Close严格低于SMA200下轨时，下一Open卖出QQQ；若指定替代品当时可持有则同一Open接替，否则转现金。持有替代品时，其完成Close严格低于自身SMA下轨就下一Open卖出并转现金。QQQ一旦重新上穿自己的上轨，下一Open无条件先卖出替代品再恢复QQQ。"`
  - `strategy.signal_time`：`"QQQ与替代品均只用各自当日常规时段已完成的拆股及股息调整Close和更早数据计算SMA、上下轨与持有状态。主观熊市区间仅用于事后标记，不参与信号。"` → `"QQQ与替代品均只用各自当日常规时段已完成的拆股及股息调整Close和更早数据计算SMA、上下轨与持有状态。主观熊市区间仅用于事后标记和剔除2000汇总，不参与信号。"`

### TIM-v0.40b.2 → TIM-v0.40c.4 · QQQ空仓期24替代标的SMA筛选消融

- 关系：`freezes_full_bear24_universe_and_remaining_windows`
- 为什么改：新实验需要恢复原24只固定股票池，并为未进入七候选实验的资产使用既有长期窗口；窗口来源必须显式保留，避免把同样本事后最优误写成预先设定。
- 策略修改：恢复核心12、近核心8和零售4分组；COR170、EXE90、DVA40、SJM30、GLD40、CHD30、HRL200、GILD140、HSY300、WRB30、WEC40、DLTR190、DG40和TSCO280沿用剔除2000口径的粗网格最高窗口。
- 修改前：BEAR24_SMA_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json) · [子实验](../TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-03-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-03-24"` → `"first_valid_qqq_sma200_close"`
  - `parameters.baseline_strategy_id`：`"hold"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"` → `"compute every signal on its complete own history, then execute and value every formal path on the QQQ sessions shared by all 24 substitutes after their listing; exclude only unav…`
  - `parameters.common_warmup_window`：`300` → `"<未设置>"`
  - `parameters.core12`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]` → `"<未设置>"`
  - `parameters.default_report_scope`：`"all_ex_2000_2002"` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous in 10-day steps, at least three windows; longest run wins, then higher mean return, then smaller starting w…` → `"<未设置>"`
  - `parameters.dotcom_exclusion_rule`：`"<未设置>"` → `"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics_after_initialization`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"2000-2002"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`1` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.exit_semantics`：`"<未设置>"` → `"held completed close strictly below lower rail"`
  - `parameters.expected_common_calendar_exclusions`：`"<未设置>"` → `["2001-10-02","2001-10-03","2002-08-06"]`
  - `parameters.fill_time`：`"<未设置>"` → `"next all-24-common-session adjusted open"`
  - `parameters.formal_case_count_per_cost`：`783` → `49`
  - `parameters.formal_cases`：`"<未设置>"` → `["QQQ_CASH","QQQ_AZO_DIRECT","QQQ_AZO_SMA237","QQQ_TLT_DIRECT","QQQ_TLT_SMA160","QQQ_COR_DIRECT","QQQ_COR_SMA170","QQQ_EXE_DIRECT","QQQ_EXE_SMA90","QQQ_DVA_DIRECT","QQQ_DVA_SMA40"…`
  - `parameters.formal_path_count`：`"<未设置>"` → `98`
  - `parameters.group_rebalance_policy`：`"re-equalize eligible member sleeves only at the start of each bear interval"` → `"<未设置>"`
  - ……另有 74 项，完整定义见父子 `experiment.json`。

### TIM-v0.40b.3 → TIM-v0.40c.4 · QQQ空仓期24替代标的SMA筛选消融

- 关系：`freezes_fine_scan_windows_for_restored_assets`
- 为什么改：SO、ED和GIS在六标的局部细网格中已有更精确的冻结结果，恢复完整24只时应使用最新诊断而不是退回粗网格，并保持选择来源透明。
- 策略修改：SO使用SMA13、ED使用SMA7、GIS使用SMA500；AZO237和MO110也继续沿用细网格结果，TLT按后续正式替代实验已冻结的SMA160，不重新扫描。
- 修改前：BEAR6_SMA_FINE_WINDOW_GRID；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json) · [子实验](../TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json)
- 自动配置差异：

  - `parameters.aggregate_return_policy`：`"12段逐段收益分别计算；总小熊、总大熊、总熊和剔除2000-2002总熊按对应逐段账户收益几何复合；未持有区间收益为0。"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-03-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2000-03-24"` → `"first_valid_qqq_sma200_close"`
  - `parameters.baseline_strategy_id`：`"hold"` → `"<未设置>"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.common_calendar_policy`：`"以SPY日历为基础；标的上市前缺席不影响日历，任何标的上市后缺少Open/High/Low/Close的日期从全部case共同剔除，禁止填造价格；熊市start/end及其下一共同交易日必须保留。"` → `"compute every signal on its complete own history, then execute and value every formal path on the QQQ sessions shared by all 24 substitutes after their listing; exclude only unav…`
  - `parameters.common_warmup_window`：`500` → `"<未设置>"`
  - `parameters.default_report_scope`：`"all_ex_2000_2002"` → `"<未设置>"`
  - `parameters.descriptive_plateau_rule`：`"within 5 percentage points of the target-and-scope maximum, contiguous using that target's frozen grid step, at least three windows; longest run wins, then higher mean return, th…` → `"<未设置>"`
  - `parameters.dotcom_exclusion_rule`：`"<未设置>"` → `"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"`
  - `parameters.entry_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.entry_semantics_after_initialization`：`"<未设置>"` → `"true close crossing from <= upper rail to > upper rail"`
  - `parameters.excluded_bear_for_post_2000_scope.label`：`"2000-2002"` → `"<未设置>"`
  - `parameters.excluded_bear_for_post_2000_scope.ordinal`：`1` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`"<未设置>"` → `3.0`
  - `parameters.exit_semantics`：`"<未设置>"` → `"held completed close strictly below lower rail"`
  - `parameters.expected_common_calendar_exclusions`：`"<未设置>"` → `["2001-10-02","2001-10-03","2002-08-06"]`
  - `parameters.fill_time`：`"<未设置>"` → `"next all-24-common-session adjusted open"`
  - `parameters.formal_case_count_per_cost`：`323` → `49`
  - `parameters.formal_cases`：`"<未设置>"` → `["QQQ_CASH","QQQ_AZO_DIRECT","QQQ_AZO_SMA237","QQQ_TLT_DIRECT","QQQ_TLT_SMA160","QQQ_COR_DIRECT","QQQ_COR_SMA170","QQQ_EXE_DIRECT","QQQ_EXE_SMA90","QQQ_DVA_DIRECT","QQQ_DVA_SMA40"…`
  - `parameters.formal_path_count`：`"<未设置>"` → `98`
  - `parameters.formal_sma_windows_by_target.AZO`：`[190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,23…` → `"<未设置>"`
  - `parameters.formal_sma_windows_by_target.ED`：`[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50]` → `"<未设置>"`
  - ……另有 89 项，完整定义见父子 `experiment.json`。

### TIM-v0.40c.3 → TIM-v0.40c.4 · QQQ空仓期24替代标的SMA筛选消融

- 关系：`expands_to_bear24_and_ablates_substitute_sma`
- 为什么改：七候选实验只给每只资产保留了自身SMA筛选，无法回答筛选本身究竟改善还是拖累QQQ空仓替代；用户要求恢复原核心12、近核心8和零售4全部24只，并同时测试筛选与不筛选。
- 策略修改：完整保留QQQ SMA200上下3%总开关、七个最新冻结窗口、下一共同Open、0/5bps、逐空仓段和剔除2000归因；把候选扩展为24只，为每只新增DIRECT全程替代路径并与自身SMA上下3%路径成对比较，不建立混合组合。
- 修改前：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json) · [子实验](../TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json)
- 自动配置差异：

  - `parameters.common_calendar_policy`：`"<未设置>"` → `"compute every signal on its complete own history, then execute and value every formal path on the QQQ sessions shared by all 24 substitutes after their listing; exclude only unav…`
  - `parameters.expected_common_calendar_exclusions`：`"<未设置>"` → `["2001-10-02","2001-10-03","2002-08-06"]`
  - `parameters.fill_time`：`"next QQQ common-session adjusted open"` → `"next all-24-common-session adjusted open"`
  - `parameters.formal_case_count_per_cost`：`8` → `49`
  - `parameters.formal_cases`：`["QQQ_CASH","QQQ_AZO237","QQQ_TLT160","QQQ_MO110","QQQ_EQT240","QQQ_WMT30","QQQ_ORLY260","QQQ_LMT30"]` → `["QQQ_CASH","QQQ_AZO_DIRECT","QQQ_AZO_SMA237","QQQ_TLT_DIRECT","QQQ_TLT_SMA160","QQQ_COR_DIRECT","QQQ_COR_SMA170","QQQ_EXE_DIRECT","QQQ_EXE_SMA90","QQQ_DVA_DIRECT","QQQ_DVA_SMA40"…`
  - `parameters.formal_path_count`：`16` → `98`
  - `parameters.groups.core12`：`"<未设置>"` → `["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD"]`
  - `parameters.groups.near_core8`：`"<未设置>"` → `["HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]`
  - `parameters.groups.retail4`：`"<未设置>"` → `["DLTR","DG","WMT","TSCO"]`
  - `parameters.substitute_modes`：`"<未设置>"` → `["DIRECT","SMA"]`
  - `parameters.substitute_windows.CHD`：`"<未设置>"` → `30`
  - `parameters.substitute_windows.COR`：`"<未设置>"` → `170`
  - `parameters.substitute_windows.DG`：`"<未设置>"` → `40`
  - `parameters.substitute_windows.DLTR`：`"<未设置>"` → `190`
  - `parameters.substitute_windows.DVA`：`"<未设置>"` → `40`
  - `parameters.substitute_windows.ED`：`"<未设置>"` → `7`
  - `parameters.substitute_windows.EXE`：`"<未设置>"` → `90`
  - `parameters.substitute_windows.GILD`：`"<未设置>"` → `140`
  - `parameters.substitute_windows.GIS`：`"<未设置>"` → `500`
  - `parameters.substitute_windows.GLD`：`"<未设置>"` → `40`
  - `parameters.substitute_windows.HRL`：`"<未设置>"` → `200`
  - `parameters.substitute_windows.HSY`：`"<未设置>"` → `300`
  - `parameters.substitute_windows.SJM`：`"<未设置>"` → `30`
  - `parameters.substitute_windows.SO`：`"<未设置>"` → `13`
  - ……另有 16 项，完整定义见父子 `experiment.json`。

### TIM-v0.50a.1 → TIM-v0.40d.1 · QQQ六择时器Bear9与再平衡稳健性

- 关系：`redefines_two_sma_roles_as_close_confirmed_allocation_ladder`
- 为什么改：既有310/190实验把两条均线用于盘中动态全仓穿越，不能表达用户新提出的均线次序、价格所在区间和30%/70%分层仓位。
- 策略修改：不继承盘中理论价、强制买回或全仓状态机；改为完成Close后先判断SMA310与SMA190次序，再按Close相对SMA190、两均线均值和SMA310的位置设定0%/30%/70%/100% QQQ，下一Open成交，并为非QQQ袖套增加现金、零仓位Bear9和全部剩余Bear9三种消融。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_BEAR9_REBALANCE；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026/experiment.json) · [子实验](../TIM/TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[5]` → `[0,5]`
  - `parameters.analysis_start`：`"2016-01-04"` → `"2000-05-30"`
  - `parameters.bear9_individual_sma_enabled`：`"<未设置>"` → `false`
  - `parameters.bear9_weights.AZO`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.DG`：`"<未设置>"` → `0.09`
  - `parameters.bear9_weights.DLTR`：`"<未设置>"` → `0.08`
  - `parameters.bear9_weights.ED`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.MO`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.ORLY`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.SO`：`"<未设置>"` → `0.12`
  - `parameters.bear9_weights.WMT`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.WRB`：`"<未设置>"` → `0.12`
  - `parameters.bear_interval_source`：`"<未设置>"` → `"research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json"`
  - `parameters.bear_intervals_are_attribution_only`：`"<未设置>"` → `true`
  - `parameters.bear_modes.BEAR_RESIDUAL`：`"<未设置>"` → `"Bear9 fills all allocation not assigned to QQQ"`
  - `parameters.bear_modes.BEAR_ZERO_ONLY`：`"<未设置>"` → `"Bear9 is 100% only when QQQ target is 0%; intermediate layered residual stays cash"`
  - `parameters.bear_modes.CASH`：`"<未设置>"` → `"all non-QQQ allocation remains cash"`
  - `parameters.buy_sma_window_range.count`：`9` → `"<未设置>"`
  - `parameters.buy_sma_window_range.start`：`270` → `"<未设置>"`
  - `parameters.buy_sma_window_range.step`：`10` → `"<未设置>"`
  - `parameters.buy_sma_window_range.stop`：`350` → `"<未设置>"`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.combination_count`：`81` → `"<未设置>"`
  - `parameters.common_warmup_window`：`"<未设置>"` → `310`
  - ……另有 85 项，完整定义见父子 `experiment.json`。

### TIM-v0.40c.4 → TIM-v0.40d.1 · QQQ六择时器Bear9与再平衡稳健性

- 关系：`narrows_to_fixed_bear9_and_tests_timing_rebalance_robustness`
- 为什么改：逐标的消融显示用户最终保留的九只资产在QQQ空仓期直接持有普遍优于再加自身SMA；需要把九只合成一套固定非等权袖套，并检查结论是否依赖SMA200或特定再平衡频率。
- 策略修改：候选池收窄为AZO/SO/ED/ORLY/MO/WRB/DLTR/DG/WMT，删除所有个股SMA，冻结10/12/13/10/13/12/8/9/13权重；QQQ总开关扩为SMA160/180/200/220/240上下3%及SMA190/310四档分层状态，比较现金、两种Bear填充模式与不再平衡及1至30交易日十二档再平衡。
- 修改前：QQQ_FLAT_SUBSTITUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ_BEAR9_REBALANCE；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json) · [子实验](../TIM/TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness/experiment.json)
- 自动配置差异：

  - `parameters.analysis_start`：`"first_valid_qqq_sma200_close"` → `"2000-05-30"`
  - `parameters.bear9_individual_sma_enabled`：`"<未设置>"` → `false`
  - `parameters.bear9_weights.AZO`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.DG`：`"<未设置>"` → `0.09`
  - `parameters.bear9_weights.DLTR`：`"<未设置>"` → `0.08`
  - `parameters.bear9_weights.ED`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.MO`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.ORLY`：`"<未设置>"` → `0.1`
  - `parameters.bear9_weights.SO`：`"<未设置>"` → `0.12`
  - `parameters.bear9_weights.WMT`：`"<未设置>"` → `0.13`
  - `parameters.bear9_weights.WRB`：`"<未设置>"` → `0.12`
  - `parameters.bear_modes.BEAR_RESIDUAL`：`"<未设置>"` → `"Bear9 fills all allocation not assigned to QQQ"`
  - `parameters.bear_modes.BEAR_ZERO_ONLY`：`"<未设置>"` → `"Bear9 is 100% only when QQQ target is 0%; intermediate layered residual stays cash"`
  - `parameters.bear_modes.CASH`：`"<未设置>"` → `"all non-QQQ allocation remains cash"`
  - `parameters.common_calendar_policy`：`"compute every signal on its complete own history, then execute and value every formal path on the QQQ sessions shared by all 24 substitutes after their listing; exclude only unav…` → `"<未设置>"`
  - `parameters.common_warmup_window`：`"<未设置>"` → `310`
  - `parameters.cost_bps_per_side`：`"<未设置>"` → `[0.0,5.0]`
  - `parameters.dotcom_exclusion_rule`：`"exclude any master flat spell overlapping the first subjective 2000-2002 bear interval"` → `"<未设置>"`
  - `parameters.entry_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.entry_semantics_after_initialization`：`"true close crossing from <= upper rail to > upper rail"` → `"<未设置>"`
  - `parameters.exit_buffer_pct`：`3.0` → `"<未设置>"`
  - `parameters.exit_semantics`：`"held completed close strictly below lower rail"` → `"<未设置>"`
  - `parameters.expected_common_calendar_exclusions`：`["2001-10-02","2001-10-03","2002-08-06"]` → `"<未设置>"`
  - `parameters.fill_time`：`"next all-24-common-session adjusted open"` → `"next QQQ common-session adjusted open"`
  - ……另有 75 项，完整定义见父子 `experiment.json`。

### TIM-v0.70 → TIM-v0.80 · QQQ StochRSI 分档补仓与双虚拟池减仓

- 关系：`evolves_to_scaled_position_management`
- 为什么改：固定42/100双周期研究只比较全仓进出语义，无法检验低位重复补仓、追加本金与分层减仓能否改善持仓路径和现金流调整后表现。
- 策略修改：保留QQQ和raw StochRSI42/100，改为2015至2026零成本单case：每日低位分档买入并设置a/b最低金额，外部注资同步modified Buy & Hold；卖出改为StochRSI100优先池和拥挤时StochRSI42辅助池，并加入严格快速下跌0.30理论价特例。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing/experiment.json) · [子实验](../TIM/TIM-v0.80__26-08-25__qqq_stochrsi_scaled_pools_2015_2026/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[0]`
  - `parameters.analysis_start`：`"2020-01-02"` → `"2015-01-02"`
  - `parameters.buy_b1_cash_fraction`：`"<未设置>"` → `0.05`
  - `parameters.buy_b1_minimum_fraction_of_a`：`"<未设置>"` → `0.25`
  - `parameters.buy_b1_threshold`：`"<未设置>"` → `0.2`
  - `parameters.buy_b2_cash_fraction_sequence`：`"<未设置>"` → `[0.2,0.3,0.4,0.3]`
  - `parameters.buy_b2_later_minimum_fraction_of_buy_b`：`"<未设置>"` → `0.6`
  - `parameters.buy_b2_threshold_pairs`：`"<未设置>"` → `[[0.05,0.01],[0.01,0.05]]`
  - `parameters.buy_threshold`：`0.2` → `"<未设置>"`
  - `parameters.combination_count_per_cost`：`3` → `"<未设置>"`
  - `parameters.fast_drop_allows_same_close_buy`：`"<未设置>"` → `true`
  - `parameters.fast_drop_current_strict_threshold`：`"<未设置>"` → `0.3`
  - `parameters.fast_drop_ignores_ohlc_touch`：`"<未设置>"` → `true`
  - `parameters.fast_drop_prior_strict_threshold`：`"<未设置>"` → `0.6`
  - `parameters.fast_drop_theoretical_fill_stochrsi`：`"<未设置>"` → `0.3`
  - `parameters.gap_policy`：`"fill_at_open_when_open_is_already_beyond_trigger"` → `"<未设置>"`
  - `parameters.intraday_path`：`"directed_open_to_close_only"` → `"<未设置>"`
  - `parameters.ordinary_sale_blocks_same_close_buy`：`"<未设置>"` → `true`
  - `parameters.performance_return_method_with_flows`：`"<未设置>"` → `"daily time weighted returns; XIRR also reported"`
  - `parameters.same_close_execution`：`"<未设置>"` → `true`
  - `parameters.sell_a_arm_threshold`：`"<未设置>"` → `0.8`
  - `parameters.sell_a_daily_pool_fraction_of_total_shares`：`"<未设置>"` → `0.01`
  - `parameters.sell_a_daily_pool_sale_fraction`：`"<未设置>"` → `0.2`
  - `parameters.sell_a_decay_zone`：`"<未设置>"` → `[0.5,0.8]`
  - ……另有 27 项，完整定义见父子 `experiment.json`。

### TIM-v0.80 → TIM-v0.80a.1 · QQQ StochRSI 分档补仓与虚拟池结构消融

- 关系：`structurally_ablates_and_defers_entry_of`
- 为什么改：父实验显著降低回撤但牺牲约4.76个百分点CAGR，并需要较多外部资金；需要用固定消融识别收益拖累和回撤贡献来自哪个模块，同时检验低位计划份额延迟到长周期恢复确认后统一买入。
- 策略修改：同一QQQ窗口和零成本口径下预先固定六案：完整策略、删除Pool B、取消Pool A每日递减、删除B1的a/4下限、删除B2的0.6b下限，以及按原规则虚拟累计买入金额并在StochRSI100严格上穿0.40时统一成交；新增持仓市值与现金报告序列。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.80__26-08-25__qqq_stochrsi_scaled_pools_2015_2026/experiment.json) · [子实验](../TIM/TIM-v0.80a.1__26-08-25__qqq_stochrsi_pool_ablation/experiment.json)
- 自动配置差异：

  - `parameters.ablation_cases`：`"<未设置>"` → `[{"case_id":"FULL","change":"none"},{"case_id":"NO_POOL_B","change":"disable all Pool B accumulation and sales"},{"case_id":"NO_A_DAILY_DECAY","change":"disable the daily 20% Pool…`
  - `parameters.buy_b1_cash_fraction`：`0.05` → `"<未设置>"`
  - `parameters.buy_b1_minimum_fraction_of_a`：`0.25` → `"<未设置>"`
  - `parameters.buy_b1_threshold`：`0.2` → `"<未设置>"`
  - `parameters.buy_b2_cash_fraction_sequence`：`[0.2,0.3,0.4,0.3]` → `"<未设置>"`
  - `parameters.buy_b2_later_minimum_fraction_of_buy_b`：`0.6` → `"<未设置>"`
  - `parameters.buy_b2_threshold_pairs`：`[[0.05,0.01],[0.01,0.05]]` → `"<未设置>"`
  - `parameters.deferred_queue_sizing`：`"<未设置>"` → `"Virtual remaining cash follows the counterfactual immediate-buy cash path; a and b use queued dollar notionals. External cash is recognized only at the combined fill. Any actual …`
  - `parameters.fast_drop_allows_same_close_buy`：`true` → `"<未设置>"`
  - `parameters.fast_drop_current_strict_threshold`：`0.3` → `"<未设置>"`
  - `parameters.fast_drop_prior_strict_threshold`：`0.6` → `"<未设置>"`
  - `parameters.fast_drop_theoretical_fill_stochrsi`：`0.3` → `"<未设置>"`
  - `parameters.ordinary_sale_blocks_same_close_buy`：`true` → `"<未设置>"`
  - `parameters.sell_a_arm_threshold`：`0.8` → `"<未设置>"`
  - `parameters.sell_a_daily_pool_fraction_of_total_shares`：`0.01` → `"<未设置>"`
  - `parameters.sell_a_daily_pool_sale_fraction`：`0.2` → `"<未设置>"`
  - `parameters.sell_a_decay_zone`：`[0.5,0.8]` → `"<未设置>"`
  - `parameters.sell_a_downcross_threshold`：`0.5` → `"<未设置>"`
  - `parameters.sell_a_remaining_sale_fraction`：`0.65` → `"<未设置>"`
  - `parameters.sell_b_arm_threshold`：`0.8` → `"<未设置>"`
  - `parameters.sell_b_crowded_weight_threshold`：`0.75` → `"<未设置>"`
  - `parameters.sell_b_daily_pool_fraction`：`0.1` → `"<未设置>"`
  - `parameters.sell_b_downcross_threshold`：`0.7` → `"<未设置>"`
  - `parameters.sell_b_total_position_sale_cap`：`0.55` → `"<未设置>"`
  - ……另有 13 项，完整定义见父子 `experiment.json`。

### TIM-v0.80a.1 → TIM-v0.80a.2 · QQQ StochRSI 低仓位恢复买入三因子消融

- 关系：`factorially_tests_sparse_recovery_entries_of`
- 为什么改：父实验显示累计到0.40再买入显著拖累CAGR，但尚未检验更早在0.20释放，以及仓位低于15%时用短长周期恢复信号主动补回QQQ能否修复收益路径。
- 策略修改：固定原卖出状态机，以累计B1/B2并在StochRSI100上穿0.20释放、低仓位StochRSI42下穿后回升买40%、低仓位StochRSI100上穿买50%三个开关做完整2×2×2八案；同日累计金额优先，长周期50%规则压过短周期40%规则。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.80a.1__26-08-25__qqq_stochrsi_pool_ablation/experiment.json) · [子实验](../TIM/TIM-v0.80a.2__26-08-25__qqq_stochrsi_sparse_entry_factorial/experiment.json)
- 自动配置差异：

  - `parameters.ablation_cases`：`[{"case_id":"FULL","change":"none"},{"case_id":"NO_POOL_B","change":"disable all Pool B accumulation and sales"},{"case_id":"NO_A_DAILY_DECAY","change":"disable the daily 20% Pool…` → `"<未设置>"`
  - `parameters.deferred_queue_sizing`：`"Virtual remaining cash follows the counterfactual immediate-buy cash path; a and b use queued dollar notionals. External cash is recognized only at the combined fill. Any actual …` → `"<未设置>"`
  - `parameters.factorial_cases`：`"<未设置>"` → `[{"D":false,"F":false,"S":false,"case_id":"D0_F0_S0"},{"D":false,"F":false,"S":true,"case_id":"D0_F0_S1"},{"D":false,"F":true,"S":false,"case_id":"D0_F1_S0"},{"D":false,"F":true,"…`
  - `parameters.factorial_switches.D`：`"<未设置>"` → `"queue B1/B2 and release on strict StochRSI100 upcross above 0.20"`
  - `parameters.factorial_switches.F`：`"<未设置>"` → `"when pre-trade weight<15%, buy 40% cash on armed StochRSI42 recovery above 0.20 while StochRSI100>0.20"`
  - `parameters.factorial_switches.S`：`"<未设置>"` → `"when pre-trade weight<15%, buy 50% cash on strict StochRSI100 upcross above 0.20"`
  - `parameters.fast_drop_allows_same_close_buy`：`"<未设置>"` → `true`
  - `parameters.fast_drop_ignores_ohlc_touch`：`true` → `"<未设置>"`
  - `parameters.ordinary_sale_blocks_same_close_buy`：`"<未设置>"` → `true`
  - `parameters.same_close_buy_priority`：`"<未设置>"` → `["deferred_queue_release","sparse_100_50pct","sparse_42_40pct"]`
  - `strategy.buy_rule`：`"FULL preserves the frozen B1/B2 rules. NO_POOL_B and NO_A_DAILY_DECAY change only sells. NO_B1_FLOOR removes max(5% cash,a/4) so B1 spends 5% cash. NO_B2_FLOOR removes the 0.6b m…` → `"For every combination D/F/S in {off,on}^3: D off preserves immediate B2-over-B1 buys; D on queues the identical virtual B1/B2 dollar tranches and releases them on prior StochRSI1…`
  - `strategy.description`：`"Six zero-cost long-only QQQ cases over the same 2015-2026 window isolate Pool B, Pool A daily decay, the B1 a/4 floor, the B2 0.6b floor, and a deferred-entry alternative from th…` → `"Eight zero-cost long-only QQQ cases form the full 2x2x2 factorial of three buy switches on top of the frozen TIM-v0.80 sell state machine. Switch D replaces immediate B1/B2 fills…`
  - `strategy.execution_time`：`"Ordinary sales and buys, including the deferred combined buy, fill at the same adjusted Close that confirms the signal. The unchanged strict fast-drop override fills at the theor…` → `"Ordinary buys and sells fill at the same adjusted Close that confirms the signal. When multiple buys share a Close, the deferred queue fills first and the 50% StochRSI100 recover…`
  - `strategy.name`：`"qqq_stochrsi_scaled_pool_structural_ablation"` → `"qqq_stochrsi_sparse_entry_factorial"`
  - `strategy.plain_language.buy`：`"五个版本沿用低位分批买入但各删除一个指定模块；第六个版本先累计计划买入金额，等长周期指标上穿0.4再一次买入。"` → `"原有低位补仓可以立即执行或累计到长周期指标上穿0.2再执行；仓位很低时，还可分别启用短周期恢复买入和长周期恢复买入。"`
  - `strategy.plain_language.execution`：`"当天收盘确认并按同一收盘价成交；原策略的快速下跌特例仍按指标0.30对应的理论价格先卖。"` → `"当天收盘确认并成交；同日先执行累计金额，随后长周期50%买入优先于短周期40%买入。"`
  - `strategy.plain_language.position`：`"每个版本独立持有QQQ和现金，必要追加本金分别同步给自己的Modified Buy & Hold作公平比较。"` → `"账户只持有QQQ和现金，新增恢复买入只使用剩余现金，原规则所需追加本金仍同步投入对应的Modified Buy & Hold。"`
  - `strategy.plain_language.sell`：`"卖出原则沿用原策略，只有删除Pool B或取消Pool A每日递减的版本不执行对应卖法。"` → `"八个版本都沿用原策略的两个虚拟池减仓规则，不改变卖出逻辑。"`
  - `strategy.plain_language.summary`：`"用六个固定版本拆开检验原策略中哪些补仓和减仓模块真正改善收益与回撤。"` → `"用八个组合检验低位金额延迟买入和两种低仓位恢复买入能否改善QQQ的持仓路径。"`
  - `strategy.positioning`：`"Long-only fractional QQQ and cash from $100,000 initial capital. Each case has its own external cash path and its own contribution-matched Buy & Hold. The deferred case reserves …` → `"Long-only fractional QQQ and cash from $100,000 initial capital. The original B1/B2 minimum shortfalls remain explicit external contributions and are mirrored into each case's ow…`
  - `strategy.sell_rule`：`"FULL preserves both virtual-pool mechanisms. NO_POOL_B never accumulates or sells Pool B. NO_A_DAILY_DECAY does not sell 20% of Pool A in the 0.50-0.80 zone but retains Pool A ac…` → `"All eight cases preserve TIM-v0.80's Pool A and Pool B sell mechanisms, including Pool A priority, daily decay in the 0.50-0.80 zone, armed downcross below 0.50, crowded-position…`
  - `strategy.signal_time`：`"All ordinary states, ablations, and the deferred 0.40 upcross use completed adjusted Close data. The unchanged fast-drop special case uses prior completed indicator state and its…` → `"Raw StochRSI42/100 levels, sparse-weight tests, arm state, and strict crossings are confirmed using the completed current adjusted Close. Pre-trade QQQ weight is measured before …`

### TIM-v0.80a.2 → TIM-v0.80a.3 · QQQ StochRSI 母策略与持仓门控

- 关系：`converts_position_paths_to_binary_timing_signals`
- 为什么改：复杂累计策略未必适合直接跑赢单一QQQ，但其持仓比例可能识别上涨区间，并可作为未来多标的轮动的候选排序信号；因此需要把原始策略、D0_F1_S1和一个更简单的四状态目标仓位同时转成独立固定资本的全仓或空仓路径。
- 策略修改：固定2015至2026、零成本与同Close口径，比较D0_F0_S0、D0_F1_S1和StochRSI42/100迟滞四状态三条母策略；分别以母策略收盘后QQQ仓位严格大于70%、80%、90%生成九条不继承注资的10万美元全仓/空仓账户，并与Naive QQQ直接比较。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.80a.2__26-08-25__qqq_stochrsi_sparse_entry_factorial/experiment.json) · [子实验](../TIM/TIM-v0.80a.3__26-08-26__qqq_stochrsi_position_gates/experiment.json)
- 自动配置差异：

  - `parameters.factorial_cases`：`[{"D":false,"F":false,"S":false,"case_id":"D0_F0_S0"},{"D":false,"F":false,"S":true,"case_id":"D0_F0_S1"},{"D":false,"F":true,"S":false,"case_id":"D0_F1_S0"},{"D":false,"F":true,"…` → `"<未设置>"`
  - `parameters.factorial_switches.D`：`"queue B1/B2 and release on strict StochRSI100 upcross above 0.20"` → `"<未设置>"`
  - `parameters.factorial_switches.F`：`"when pre-trade weight<15%, buy 40% cash on armed StochRSI42 recovery above 0.20 while StochRSI100>0.20"` → `"<未设置>"`
  - `parameters.factorial_switches.S`：`"when pre-trade weight<15%, buy 50% cash on strict StochRSI100 upcross above 0.20"` → `"<未设置>"`
  - `parameters.fast_drop_allows_same_close_buy`：`true` → `"<未设置>"`
  - `parameters.final_comparison_paths`：`"<未设置>"` → `"nine position gates plus naive QQQ Buy & Hold"`
  - `parameters.four_state.long_period`：`"<未设置>"` → `100`
  - `parameters.four_state.off_threshold`：`"<未设置>"` → `0.1`
  - `parameters.four_state.on_threshold`：`"<未设置>"` → `0.2`
  - `parameters.four_state.rebalance_frequency`：`"<未设置>"` → `"every completed Close"`
  - `parameters.four_state.short_period`：`"<未设置>"` → `42`
  - `parameters.four_state.target_weights.both_off`：`"<未设置>"` → `0.0`
  - `parameters.four_state.target_weights.both_on`：`"<未设置>"` → `1.0`
  - `parameters.four_state.target_weights.long_only`：`"<未设置>"` → `0.6`
  - `parameters.four_state.target_weights.short_only`：`"<未设置>"` → `0.4`
  - `parameters.gate_capital_policy`：`"<未设置>"` → `"independent fixed $100,000 initial capital; no external contributions"`
  - `parameters.gate_signal_source`：`"<未设置>"` → `"mother post-trade QQQ market value divided by mother equity on the same Close"`
  - `parameters.mother_strategies`：`"<未设置>"` → `[{"definition":"TIM-v0.80a.2 D0_F0_S0","mother_id":"M1_D0_F0_S0"},{"definition":"TIM-v0.80a.2 D0_F1_S1","mother_id":"M2_D0_F1_S1"},{"definition":"StochRSI42/100 hysteresis target …`
  - `parameters.ordinary_sale_blocks_same_close_buy`：`true` → `"<未设置>"`
  - `parameters.performance_return_method_with_flows`：`"daily time weighted returns; XIRR also reported"` → `"daily time-weighted returns; XIRR also reported"`
  - `parameters.position_gate_operator`：`"<未设置>"` → `"strictly greater than"`
  - `parameters.position_gate_thresholds`：`"<未设置>"` → `[0.7,0.8,0.9]`
  - `parameters.same_close_buy_priority`：`["deferred_queue_release","sparse_100_50pct","sparse_42_40pct"]` → `"<未设置>"`
  - `parameters.stochrsi_periods`：`[42,100]` → `"<未设置>"`
  - ……另有 13 项，完整定义见父子 `experiment.json`。

### TIM-v0.80a.3 → TIM-v0.80a.4 · QQQ StochRSI 真正1/2/4母策略与持仓门控

- 关系：`corrects_mother_selection_of`
- 为什么改：上一版把对话中的四状态草案作为第4个母策略，但用户所指的真正策略4是针对累计仓位母策略做的泛化精简版；历史validated结果保持不变，本实验显式纠正母策略选择并重跑完整1/2/4门控矩阵。
- 策略修改：保留D0_F0_S0与D0_F1_S1，使用精简累计仓位替换四状态母策略；仍生成严格大于70%、80%、90%的九条固定资本门控，并为所有路径新增按有仓位交易日比例几何压缩的持仓期间CAGR，同时保留日历CAGR和暴露率。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.80a.3__26-08-26__qqq_stochrsi_position_gates/experiment.json) · [子实验](../TIM/TIM-v0.80a.4__26-08-26__qqq_stochrsi_true124_position_gates/experiment.json)
- 自动配置差异：

  - `parameters.four_state.long_period`：`100` → `"<未设置>"`
  - `parameters.four_state.off_threshold`：`0.1` → `"<未设置>"`
  - `parameters.four_state.on_threshold`：`0.2` → `"<未设置>"`
  - `parameters.four_state.rebalance_frequency`：`"every completed Close"` → `"<未设置>"`
  - `parameters.four_state.short_period`：`42` → `"<未设置>"`
  - `parameters.four_state.target_weights.both_off`：`0.0` → `"<未设置>"`
  - `parameters.four_state.target_weights.both_on`：`1.0` → `"<未设置>"`
  - `parameters.four_state.target_weights.long_only`：`0.6` → `"<未设置>"`
  - `parameters.four_state.target_weights.short_only`：`0.4` → `"<未设置>"`
  - `parameters.holding_period_cagr.cash_return_assumption`：`"<未设置>"` → `0.0`
  - `parameters.holding_period_cagr.definition`：`"<未设置>"` → `"geometric time compression of calendar CAGR over the fraction of trading sessions with shares greater than zero"`
  - `parameters.holding_period_cagr.formula`：`"<未设置>"` → `"(1 + calendar_cagr) ** (1 / holding_time_fraction) - 1"`
  - `parameters.holding_period_cagr.interpretation`：`"<未设置>"` → `"descriptive intensity metric, not a separately simulated return or subperiod IRR"`
  - `parameters.mother_strategies`：`[{"definition":"TIM-v0.80a.2 D0_F0_S0","mother_id":"M1_D0_F0_S0"},{"definition":"TIM-v0.80a.2 D0_F1_S1","mother_id":"M2_D0_F1_S1"},{"definition":"StochRSI42/100 hysteresis target …` → `[{"definition":"TIM-v0.80a.2 D0_F0_S0","mother_id":"M1_D0_F0_S0"},{"definition":"TIM-v0.80a.2 D0_F1_S1","mother_id":"M2_D0_F1_S1"},{"definition":"Pruned accumulation with capped o…`
  - `parameters.pruned_accumulation.deep_failure_exit_threshold`：`"<未设置>"` → `0.2`
  - `parameters.pruned_accumulation.deep_failure_priority_over_pool_a_exit`：`"<未设置>"` → `true`
  - `parameters.pruned_accumulation.external_contributions`：`"<未设置>"` → `false`
  - `parameters.pruned_accumulation.extreme_low_cash_fraction`：`"<未设置>"` → `0.2`
  - `parameters.pruned_accumulation.extreme_low_long_threshold`：`"<未设置>"` → `0.05`
  - `parameters.pruned_accumulation.extreme_low_position_cap`：`"<未设置>"` → `0.3`
  - `parameters.pruned_accumulation.extreme_low_short_threshold`：`"<未设置>"` → `0.01`
  - `parameters.pruned_accumulation.long_recovery_cash_fraction`：`"<未设置>"` → `0.5`
  - `parameters.pruned_accumulation.long_recovery_threshold`：`"<未设置>"` → `0.2`
  - `parameters.pruned_accumulation.normal_low_cash_fraction`：`"<未设置>"` → `0.05`
  - ……另有 24 项，完整定义见父子 `experiment.json`。

### TIM-v0.80a.4 → TIM-v0.80a.5 · QQQ StochRSI 90%持仓信号交集

- 关系：`intersects_90pct_position_signals_of`
- 为什么改：策略1的90%门控在上一实验中呈现更短暴露、更高持仓期间CAGR和更低回撤；需要检验它与策略2、策略4的高仓位信号取严格交集后，能否进一步减少误报并集中上涨区间。
- 策略修改：固定三条母策略和严格大于90%的同Close信号，重算三条单独90%门控，并新增1∩2、1∩4、2∩4、1∩2∩4四个全仓或空仓账户；每条固定10万美元、无注资，继续并列报告日历CAGR、持仓期间CAGR和持仓时间。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.80a.4__26-08-26__qqq_stochrsi_true124_position_gates/experiment.json) · [子实验](../TIM/TIM-v0.80a.5__26-08-26__qqq_stochrsi_90_and_gates/experiment.json)
- 自动配置差异：

  - `parameters.final_comparison_paths`：`"nine position gates plus naive QQQ Buy & Hold"` → `"<未设置>"`
  - `parameters.gate_signal_source`：`"mother post-trade QQQ market value divided by mother equity on the same Close"` → `"strict conjunction of completed same-Close source-mother post-trade weights greater than 0.90"`
  - `parameters.individual_baseline_paths`：`"<未设置>"` → `["S1_90","S2_90","S4_90"]`
  - `parameters.intersection_paths`：`"<未设置>"` → `[{"path_id":"S1_AND_S2_90","required_signals":["S1","S2"]},{"path_id":"S1_AND_S4_90","required_signals":["S1","S4"]},{"path_id":"S2_AND_S4_90","required_signals":["S2","S4"]},{"pa…`
  - `parameters.mother_strategies`：`[{"definition":"TIM-v0.80a.2 D0_F0_S0","mother_id":"M1_D0_F0_S0"},{"definition":"TIM-v0.80a.2 D0_F1_S1","mother_id":"M2_D0_F1_S1"},{"definition":"Pruned accumulation with capped o…` → `"<未设置>"`
  - `parameters.performance_return_method_with_flows`：`"daily time-weighted returns; XIRR also reported"` → `"<未设置>"`
  - `parameters.position_gate_operator`：`"strictly greater than"` → `"<未设置>"`
  - `parameters.position_gate_thresholds`：`[0.7,0.8,0.9]` → `"<未设置>"`
  - `parameters.pruned_accumulation.deep_failure_exit_threshold`：`0.2` → `"<未设置>"`
  - `parameters.pruned_accumulation.deep_failure_priority_over_pool_a_exit`：`true` → `"<未设置>"`
  - `parameters.pruned_accumulation.external_contributions`：`false` → `"<未设置>"`
  - `parameters.pruned_accumulation.extreme_low_cash_fraction`：`0.2` → `"<未设置>"`
  - `parameters.pruned_accumulation.extreme_low_long_threshold`：`0.05` → `"<未设置>"`
  - `parameters.pruned_accumulation.extreme_low_position_cap`：`0.3` → `"<未设置>"`
  - `parameters.pruned_accumulation.extreme_low_short_threshold`：`0.01` → `"<未设置>"`
  - `parameters.pruned_accumulation.long_recovery_cash_fraction`：`0.5` → `"<未设置>"`
  - `parameters.pruned_accumulation.long_recovery_threshold`：`0.2` → `"<未设置>"`
  - `parameters.pruned_accumulation.normal_low_cash_fraction`：`0.05` → `"<未设置>"`
  - `parameters.pruned_accumulation.normal_low_position_cap`：`0.15` → `"<未设置>"`
  - `parameters.pruned_accumulation.normal_low_threshold`：`0.2` → `"<未设置>"`
  - `parameters.pruned_accumulation.ordinary_sale_blocks_same_close_buy`：`true` → `"<未设置>"`
  - `parameters.pruned_accumulation.pool_a_arm_threshold`：`0.8` → `"<未设置>"`
  - `parameters.pruned_accumulation.pool_a_daily_add_fraction_of_current_shares`：`0.01` → `"<未设置>"`
  - `parameters.pruned_accumulation.pool_a_exit_threshold`：`0.5` → `"<未设置>"`
  - ……另有 23 项，完整定义见父子 `experiment.json`。

### TIM-v0.70b.1 → TIM-v0.70b.2 · QQQ单周期StochRSI五年滚动漂移诊断

- 关系：`diagnoses_rolling_best_period_drift_of`
- 为什么改：单周期稳健选参没有找到通过全部门禁的冠军，用户希望直接观察机械最优周期是否随五年样本窗口逐年呈现有序漂移，从而判断参数不稳定是随机跳动还是制度性移动。
- 策略修改：完全保留单周期raw StochRSI、0.20上穿买入、0.80下穿卖出、满仓/现金和精确触发成交；把14–200逐日周期稳健筛选改为14–210步长7，并固定2005起至2015起的11个逐年左移五年窗口，逐窗绘制累计收益、CAGR和Sharpe响应曲线及机械最佳周期序列。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70b.1__26-08-25__qqq_single_stochrsi_robust_2005_2020/experiment.json) · [子实验](../TIM/TIM-v0.70b.2__26-08-29__qqq_single_stochrsi_rolling_drift_2005_2020/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5,10]` → `[5]`
  - `parameters.chronological_blocks`：`[[2005,2009],[2010,2013],[2014,2017]]` → `"<未设置>"`
  - `parameters.combination_count`：`187` → `319`
  - `parameters.expected_windows.3`：`52` → `"<未设置>"`
  - `parameters.expected_windows.5`：`44` → `"<未设置>"`
  - `parameters.expected_windows.7`：`36` → `"<未设置>"`
  - `parameters.formal_costs_bps`：`[0,5,10]` → `"<未设置>"`
  - `parameters.gates.minimum_cagr_win_rate_vs_buy_hold`：`0.4` → `"<未设置>"`
  - `parameters.gates.minimum_drawdown_win_rate_vs_buy_hold`：`0.6` → `"<未设置>"`
  - `parameters.gates.minimum_leave_one_start_year_out_q25_joint_rank`：`0.5` → `"<未设置>"`
  - `parameters.gates.minimum_sharpe_win_rate_vs_buy_hold`：`0.55` → `"<未设置>"`
  - `parameters.gates.minimum_worst_horizon_q25_joint_rank`：`0.55` → `"<未设置>"`
  - `parameters.gates.minimum_worst_local_3point_score`：`0.45` → `"<未设置>"`
  - `parameters.gates.minimum_worst_time_block_median_joint_rank`：`0.55` → `"<未设置>"`
  - `parameters.horizon_last_start.3`：`"2017-12-31"` → `"<未设置>"`
  - `parameters.horizon_last_start.5`：`"2015-12-31"` → `"<未设置>"`
  - `parameters.horizon_last_start.7`：`"2013-12-31"` → `"<未设置>"`
  - `parameters.horizons_years`：`[3,5,7]` → `"<未设置>"`
  - `parameters.local_radius_steps`：`1` → `"<未设置>"`
  - `parameters.period_count`：`"<未设置>"` → `29`
  - `parameters.period_end`：`200` → `210`
  - `parameters.period_step`：`1` → `7`
  - `parameters.reference_periods`：`[42,100]` → `"<未设置>"`
  - `parameters.research_start`：`"2005-01-03"` → `"<未设置>"`
  - ……另有 15 项，完整定义见父子 `experiment.json`。

### TIM-v0.10 → TIM-v0.90 · AAPL双均线双窗口收益网格

- 关系：`branches_to_single_stock_dual_sma_state_surface`
- 为什么改：用户建立AAPL个股研究分支，希望先用最简单的双均线状态规则直接比较两个不重叠五年窗口的完整收益网格，判断参数面是否跨时期保持形状，而不是继续只研究价格相对固定SMA200的阈值。
- 策略修改：标的从QQQ/SPY改为AAPL；单条SMA200价格阈值改为快线严格高于慢线时满仓、否则空仓的收盘状态；快线扫描1–50、慢线扫描15–250且步长1，只允许快线短于慢线，并把账户独立重启于[2013,2018)和[2018,2023)两个窗口，统一下一Open和5bps。
- 修改前：CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：AAPL；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json) · [子实验](../TIM/TIM-v0.90__26-08-30__aapl_dual_sma_two_window_grid/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[5]`
  - `parameters.a_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.b_pct`：`[-3.0,-2.75,-2.5,-2.25,-2.0,-1.75,-1.5,-1.25,-1.0,-0.75,-0.5,-0.25,0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `"<未设置>"`
  - `parameters.combination_count`：`"<未设置>"` → `22268`
  - `parameters.combination_count_per_surface`：`1089` → `"<未设置>"`
  - `parameters.derived_zero_cost_sensitivity`：`"<未设置>"` → `"Reuse the identical reconciled order schedule and remove adverse fill costs; do not treat it as a second formal engine block."`
  - `parameters.fast_window_end`：`"<未设置>"` → `50`
  - `parameters.fast_window_start`：`"<未设置>"` → `1`
  - `parameters.fast_window_step`：`"<未设置>"` → `1`
  - `parameters.formal_cost_bps`：`"<未设置>"` → `5`
  - `parameters.invalid_role_cells_per_window`：`"<未设置>"` → `666`
  - `parameters.rectangular_cells_per_window`：`"<未设置>"` → `11800`
  - `parameters.role_constraint`：`"<未设置>"` → `"fast_window < slow_window"`
  - `parameters.slow_window_end`：`"<未设置>"` → `250`
  - `parameters.slow_window_start`：`"<未设置>"` → `15`
  - `parameters.slow_window_step`：`"<未设置>"` → `1`
  - `parameters.valid_pair_count`：`"<未设置>"` → `11134`
  - `parameters.window_convention`：`"<未设置>"` → `"[calendar-year start inclusive, end-label-year start exclusive); each account uses the first and last available AAPL sessions inside that interval"`
  - `parameters.window_count`：`"<未设置>"` → `2`
  - `parameters.windows`：`"<未设置>"` → `[{"end_exclusive":"2018-01-01","label":"2013–2018","start_inclusive":"2013-01-01","window_id":"W2013_2018"},{"end_exclusive":"2023-01-01","label":"2018–2023","start_inclusive":"20…`
  - `strategy.buy_rule`：`"flat and previous_close <= previous_sma200 * (1 + b_pct / 100) and close > sma200 * (1 + b_pct / 100)"` → `"flat and completed fast_sma > completed slow_sma"`
  - `strategy.description`：`"After a 200-session SMA warmup, enter only when the close crosses from at-or-below the upper SMA buffer to above it; exit whenever the close is below the lower SMA buffer. Signal…` → `"In each independently restarted window, calculate simple moving averages from adjusted AAPL closes using pre-window history. At every completed regular-session close, target a fu…`
  - `strategy.execution_time`：`"next regular-session open"` → `"next regular-session open with an explicit adverse cost; a last-window-close signal has no later fill"`
  - `strategy.first_valid_sma_bar_can_enter`：`false` → `"<未设置>"`
  - ……另有 11 项，完整定义见父子 `experiment.json`。

### TIM-v0.90 → TIM-v0.90a.1 · AAPL双均线双窗口持仓CAGR网格

- 关系：`reexpresses_returns_over_actual_holding_sessions_of`
- 为什么改：用户希望在完全相同的AAPL双均线网格中，不只看五年日历区间收益，而要直接观察净账户收益仅按实际有仓位交易日折算后的持仓CAGR。
- 策略修改：交易对象、快1–50/慢15–250网格、角色约束、两个右开五年窗口、收盘确认、次日Open、全仓/现金和5bps全部不变；主参数面改为按252个持仓交易日年化的持仓CAGR，并强制并列展示持仓日、持仓率、日历CAGR、累计收益和风险，防止低暴露路径被误当成可部署冠军。
- 修改前：AAPL；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：AAPL；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.90__26-08-30__aapl_dual_sma_two_window_grid/experiment.json) · [子实验](../TIM/TIM-v0.90a.1__26-08-30__aapl_dual_sma_holding_cagr_grid/experiment.json)
- 自动配置差异：

  - `parameters.holding_period_cagr.annualization_sessions`：`"<未设置>"` → `252`
  - `parameters.holding_period_cagr.formula`：`"<未设置>"` → `"(final_equity / initial_cash) ** (252 / holding_sessions) - 1"`
  - `parameters.holding_period_cagr.holding_session_definition`：`"<未设置>"` → `"a regular-session row whose post-open account state has positive AAPL shares; an entry-open session is included and an exit-open session is excluded"`
  - `parameters.holding_period_cagr.interpretation`：`"<未设置>"` → `"descriptive geometric time compression under zero cash return; not a separate backtest, trade-level compounded CAGR, or IRR"`
  - `parameters.primary_surface_metric`：`"<未设置>"` → `"holding_period_cagr_pct"`
  - `strategy.description`：`"In each independently restarted window, calculate simple moving averages from adjusted AAPL closes using pre-window history. At every completed regular-session close, target a fu…` → `"In each independently restarted window, calculate simple moving averages from adjusted AAPL closes using pre-window history. At every completed regular-session close, target a fu…`
  - `strategy.name`：`"aapl_close_confirmed_dual_sma_state_grid"` → `"aapl_close_confirmed_dual_sma_holding_cagr_grid"`
  - `strategy.plain_language.position`：`"每个窗口都从10万美元现金独立开始，只在满仓AAPL与现金之间切换，允许碎股且不融资。"` → `"每个窗口都从10万美元现金独立开始，只在满仓AAPL与现金之间切换；报告同时展示持仓CAGR、持仓日和持仓率。"`
  - `strategy.plain_language.summary`：`"在两个互不重叠的五年窗口中，比较AAPL快慢均线状态策略的完整收益网格。"` → `"在两个互不重叠的五年窗口中，比较AAPL快慢均线策略只按实际持仓日折算的年化收益网格。"`

### TIM-v0.70b.2 → TIM-v0.70b.3 · QQQ单周期StochRSI年度CAGR冠军换参测试

- 关系：`walks_forward_annual_cagr_winners_from`
- 为什么改：滚动窗口显示机械最佳周期会跳动，用户希望把每个窗口的CAGR冠军延迟用于更晚的单一年份，检验这种年度换参在连续账户中是否比持有和固定14/140更有效。
- 策略修改：冻结父run的11个CAGR最佳周期14/14/28/14/28/140/42/14/14/14/14，依次用于2011–2021；年度边界只更换指标周期并延续原现金或QQQ仓位，新周期以自身前一日状态判断穿越，不强制交易；新增固定14、固定140和Buy & Hold基线，统一5bps。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../TIM/TIM-v0.70b.2__26-08-29__qqq_single_stochrsi_rolling_drift_2005_2020/experiment.json) · [子实验](../TIM/TIM-v0.70b.3__26-08-30__qqq_single_stochrsi_annual_dynamic_oos_2011_2021/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"<未设置>"` → `"2021-12-31"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2011-01-03"`
  - `parameters.baseline_periods`：`"<未设置>"` → `[14,140]`
  - `parameters.case_ids`：`"<未设置>"` → `["DYNAMIC_CAGR","FIXED_014","FIXED_140"]`
  - `parameters.combination_count`：`319` → `"<未设置>"`
  - `parameters.cost_bps`：`"<未设置>"` → `5`
  - `parameters.dynamic_schedule`：`"<未设置>"` → `[{"application_year":2011,"period":14,"source_end":"2009-12-31","source_label":"2005–2010","source_window":"W2005_2010"},{"application_year":2012,"period":14,"source_end":"2010-12…`
  - `parameters.formal_case_count`：`"<未设置>"` → `3`
  - `parameters.parent_experiment_id`：`"<未设置>"` → `"qqq_single_stochrsi_rolling_drift_2005_2020_v1"`
  - `parameters.parent_run_id`：`"<未设置>"` → `"run_20260829T160927Z_f1d1fec3"`
  - `parameters.parent_selection_artifact`：`"<未设置>"` → `"analysis/best_by_window.csv"`
  - `parameters.parent_selection_artifact_sha256`：`"<未设置>"` → `"569d477de31ca5c4197a3dbee3ee196f7a4421982e50b57e8159ea7ef63948f4"`
  - `parameters.period_count`：`29` → `"<未设置>"`
  - `parameters.period_end`：`210` → `"<未设置>"`
  - `parameters.period_start`：`14` → `"<未设置>"`
  - `parameters.period_step`：`7` → `"<未设置>"`
  - `parameters.schedule_selection_metric`：`"<未设置>"` → `"maximum CAGR within the parent 14–210 step-7 grid, with lower period as the frozen tie-break"`
  - `parameters.screen_cost_bps`：`5` → `"<未设置>"`
  - `parameters.window_convention`：`"[start-year first QQQ session, end-label-year first QQQ session); labels show the exclusive end year"` → `"<未设置>"`
  - `parameters.window_count`：`11` → `"<未设置>"`
  - `parameters.window_interval_years`：`5` → `"<未设置>"`
  - `parameters.window_last_start_year`：`2015` → `"<未设置>"`
  - `parameters.window_start_year`：`2005` → `"<未设置>"`
  - `parameters.year_boundary_policy`：`"<未设置>"` → `"carry cash/shares unchanged; calculate both prior and current StochRSI state with the new application's period; no forced order"`
  - ……另有 12 项，完整定义见父子 `experiment.json`。
