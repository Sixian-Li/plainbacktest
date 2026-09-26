# ROT · 上涨区间与轮动 · 策略演化史

> 本文件由 `scripts.build_research_catalog` 自动生成，请勿手工修改。修改原因与策略变化只写入 `lineage.json`；参数差异来自父子 `experiment.json`，指标来自当前代表 run。

导航：[总策略演化史](../strategy_evolution.md) · [实验登记册](../index.md) · [研究谱系图](../research_map.html)

指标只用于还原研究轨迹，不把样本内结果升级为样本外证据。`completed_unvalidated` 会原样显示；确定性门禁失败和已清理的会话中断不成为研究节点，其精简历史由 `research_events.jsonl` 记录。

## ROT · 上涨区间与轮动

先研究单标的上涨状态和快速退出，再扩展到多标的评分、排名、风险预算与组合轮动。

### 起点 · ROT-v0.10 · QQQ 三条件上涨状态消融

- 策略：QQQ 初始空仓。每个交易日收盘后检查三个持仓条件：Close 高于 SMA200；SMA30、SMA200、SMA250、SMA300 每一条都连续三个交易日严格增加；SMA200 严格高于 SMA250 且 SMA250 严格高于 SMA300。只有全部条件成立才目标持有，否则目标空仓；条件状态改变后的下一交易日 Open 全仓切换。同时运行完整规则与逐一移除条件 1、2、3 的三个消融版本。
- 代表结果：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history/experiment.json)

### 起点 · ROT-v0.30 · 熊市韧性候选统一趋势评分组合

- 策略：在此前当前成分股熊市事件研究得到的 13 个核心和 8 个近核心候选上，冻结一套不按个股寻优的牛熊评分。个股分由 Close 相对 SMA200 的一倍 ATR20 滞回状态、SMA200 二十日方向及 3/6/12 个月动量组成；总分再加入 SPY、QQQ 和当前成分股 SMA200 广度。底层使用可用标的 63 日逆波动风险预算，评分只做阶梯减仓，释放资金留现金。
- 代表结果：RESILIENCE_21；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio/experiment.json)

### 起点 · ROT-v0.40a.2 · QQQ 长短趋势与下行风险三因子消融

- 策略：QQQ只在0%和100%仓位之间切换。长期趋势因子要求完成Close高于SMA180且SMA180最近3个交易日的平均每日百分比斜率严格高于0.04%；短期趋势因子要求SMA20最近10个完成值的OLS每日对数斜率乘R²严格高于0；风险因子比较短期与长期波动率，风险比率严格高于报警阈值时进入危险状态，之后只有严格低于报警阈值减0.4才恢复安全。主候选使用只保留下跌收益的下行波动率与风险否决结构；普通总波动率、严格3/3和普通2/3只作消融。所有信号仅使用完成Close，不使用固定百分比止损。
- 代表结果：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../ROT/ROT-v0.40a.2__26-08-21__qqq_three_factor_downside_risk_training_2000_2015/experiment.json)

### ROT-v0.10 → ROT-v0.10a.1 · QQQ 持仓峰值回撤训练与样本外

- 关系：`branches_from`
- 为什么改：三条件消融显示删除条件 2 可降低回撤，因此进一步测试持仓峰值止损能否改善尾部风险。
- 策略修改：基线只保留条件 1+3；在 2000–2004 从 5%～15% 训练峰值回撤阈值并冻结 8%，随后测试 2005–2026。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history/experiment.json) · [子实验](../ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"shared first bar after SMA300 plus three completed daily increases"` → `"<未设置>"`
  - `parameters.base_conditions`：`"<未设置>"` → `["price_above_sma200","sma200_above_sma250_above_sma300"]`
  - `parameters.cases.all_conditions`：`"conditions 1 + 2 + 3"` → `"<未设置>"`
  - `parameters.cases.without_condition_1`：`"conditions 2 + 3 only"` → `"<未设置>"`
  - `parameters.cases.without_condition_2`：`"conditions 1 + 3 only"` → `"<未设置>"`
  - `parameters.cases.without_condition_3`：`"conditions 1 + 2 only"` → `"<未设置>"`
  - `parameters.disabled_prior_condition`：`"<未设置>"` → `"all_smas_rising_3d"`
  - `parameters.fixed_ablation_stop_pct`：`"<未设置>"` → `8`
  - `parameters.requested_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.requested_train_start`：`"<未设置>"` → `"1999-03-10"`
  - `parameters.rising_definition`：`"strictly positive SMA first difference on each of the last three completed trading sessions for every one of SMA30, SMA200, SMA250 and SMA300"` → `"<未设置>"`
  - `parameters.rising_sessions_parameter_status`：`"<未设置>"` → `"retained only for shared indicator preparation compatibility; it does not affect readiness, signals, or trading in this experiment"`
  - `parameters.stop_grid_pct`：`"<未设置>"` → `[5,6,7,8,9,10,11,12,13,14,15]`
  - `parameters.stop_peak_source`：`"<未设置>"` → `"maximum of the actual entry fill and completed closes observed during the current position"`
  - `parameters.stop_trigger_operator`：`"<未设置>"` → `">"`
  - `parameters.test_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.test_start`：`"<未设置>"` → `"2005-01-01"`
  - `parameters.train_end`：`"<未设置>"` → `"2004-12-31"`
  - `parameters.windows_are_independent`：`"<未设置>"` → `true`
  - `strategy.buy_rule`：`"空仓且当日完成 Close 计算出的全部启用条件为真时产生买入信号；这是条件状态，不要求从假到真的穿越事件。完整策略启用三项条件；三个消融 case 各关闭且仅关闭一项。"` → `"空仓且完成 Close 后 Close>SMA200 与 SMA200>SMA250>SMA300 同时为真时产生买入信号。止损卖出后不设置冷却期；若基础条件仍为真，卖出成交当日完成 Close 后可再次发出买入信号，最早下一交易日 Open 买回。"`
  - `strategy.description`：`"QQQ 初始空仓。每个交易日收盘后检查三个持仓条件：Close 高于 SMA200；SMA30、SMA200、SMA250、SMA300 每一条都连续三个交易日严格增加；SMA200 严格高于 SMA250 且 SMA250 严格高于 SMA300。只有全部条件成立才目标持有，否则目标空仓；条件状态改变后的下一交易日 Open 全仓切换。同时运行完整规则…` → `"在上一轮去掉条件 2 的基础策略上，只保留 Close>SMA200 与 SMA200>SMA250>SMA300 两个持仓条件。初始空仓；两个基础条件均成立才目标持有，任一不成立则目标空仓。另叠加一个可关闭的持仓内峰值回撤卖出：从每次实际买入成交价开始，用其后每个已完成 Close 更新峰值，若当前 Close 相对该峰值的跌幅严格大于阈值，则产生卖出…`
  - `strategy.execution_time`：`"信号后的下一交易日常规时段 Open，按明确 Open 成交；不允许同日 Close 成交或 PyBroker 默认 middle price。"` → `"所有信号均在下一交易日常规时段 Open 执行；按明确 Open 成交，不允许同日 Close、盘中假设成交或 PyBroker 默认 middle price。"`
  - `strategy.initial_position`：`"数据起点空仓；所有 case 共享 SMA300 加三次日变化所需的完整预热，预热结束前保持现金。"` → `"每个窗口独立从现金开始；测试期不继承训练期仓位、现金或峰值状态。训练期前使用更早历史仅预热 SMA，不发生交易。"`
  - ……另有 4 项，完整定义见父子 `experiment.json`。

### ROT-v0.10 → ROT-v0.10b.1 · QQQ 短均线上升与 3% 锁定回买消融

- 关系：`branches_from`
- 为什么改：探索比单一峰值止损更贴近“只吃上涨区间”的入场确认和快速止损复位机制。
- 策略修改：加入 SMA25/30/35 分别上涨的入场措施和 3% 盘中止损后锁定回买措施，在 2010–2015、2020–2026 做 2×2 消融。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history/experiment.json) · [子实验](../ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"shared first bar after SMA300 plus three completed daily increases"` → `"<未设置>"`
  - `parameters.base_conditions`：`"<未设置>"` → `["price_above_sma200","sma200_above_sma250_above_sma300"]`
  - `parameters.cases.all_conditions`：`"conditions 1 + 2 + 3"` → `"<未设置>"`
  - `parameters.cases.base.measure_1`：`"<未设置>"` → `false`
  - `parameters.cases.base.measure_2`：`"<未设置>"` → `false`
  - `parameters.cases.measure_1.measure_1`：`"<未设置>"` → `true`
  - `parameters.cases.measure_1.measure_2`：`"<未设置>"` → `false`
  - `parameters.cases.measure_2.measure_1`：`"<未设置>"` → `false`
  - `parameters.cases.measure_2.measure_2`：`"<未设置>"` → `true`
  - `parameters.cases.measures_1_2.measure_1`：`"<未设置>"` → `true`
  - `parameters.cases.measures_1_2.measure_2`：`"<未设置>"` → `true`
  - `parameters.cases.without_condition_1`：`"conditions 2 + 3 only"` → `"<未设置>"`
  - `parameters.cases.without_condition_2`：`"conditions 1 + 3 only"` → `"<未设置>"`
  - `parameters.cases.without_condition_3`：`"conditions 1 + 2 only"` → `"<未设置>"`
  - `parameters.measure_1_operator`：`"<未设置>"` → `"each current SMA must be strictly greater than its own previous-session value"`
  - `parameters.measure_1_short_sma_windows`：`"<未设置>"` → `[25,30,35]`
  - `parameters.measure_2_drawdown_stop_pct`：`"<未设置>"` → `3`
  - `parameters.measure_2_recovery_operator`：`"<未设置>"` → `"completed Close >= actual 3% stop sell fill"`
  - `parameters.measure_2_reset_sequence`：`"<未设置>"` → `["completed Close < SMA200","completed Close <= SMA200 * 0.95","completed Close crosses from <= SMA200 to > SMA200"]`
  - `parameters.measure_2_stop_peak`：`"<未设置>"` → `"maximum of actual entry fill and completed closes strictly before the current session"`
  - `parameters.old_8pct_stop`：`"<未设置>"` → `"removed and not tested"`
  - `parameters.ordering_windows`：`[200,250,300]` → `"<未设置>"`
  - `parameters.price_sma_window`：`200` → `"<未设置>"`
  - ……另有 17 项，完整定义见父子 `experiment.json`。

### ROT-v0.10b.1 → ROT-v0.20 · QQQ 双路径入场与四卖出规则消融

- 关系：`evolves_to`
- 为什么改：两项措施呈现明显时期依赖，需要把入场路径与退出规则拆开，识别真正有贡献的卖出组合。
- 策略修改：扩展为双路径入场资格、动态双阈值买单和四条独立卖出规则，在两个窗口运行全部 16 个退出掩码。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods/experiment.json) · [子实验](../ROT/ROT-v0.20__26-08-14__qqq_sma_entry_four_exit_ablation_two_periods/experiment.json)
- 自动配置差异：

  - `parameters.base_conditions`：`["price_above_sma200","sma200_above_sma250_above_sma300"]` → `"<未设置>"`
  - `parameters.cases.base.measure_1`：`false` → `"<未设置>"`
  - `parameters.cases.base.measure_2`：`false` → `"<未设置>"`
  - `parameters.cases.measure_1.measure_1`：`true` → `"<未设置>"`
  - `parameters.cases.measure_1.measure_2`：`false` → `"<未设置>"`
  - `parameters.cases.measure_2.measure_1`：`false` → `"<未设置>"`
  - `parameters.cases.measure_2.measure_2`：`true` → `"<未设置>"`
  - `parameters.cases.measures_1_2.measure_1`：`true` → `"<未设置>"`
  - `parameters.cases.measures_1_2.measure_2`：`true` → `"<未设置>"`
  - `parameters.combination_count`：`"<未设置>"` → `16`
  - `parameters.entry_post_qualification_price_filters`：`"<未设置>"` → `["price > dynamic SMA30 * 0.98","price > dynamic SMA200 * 0.98"]`
  - `parameters.entry_short_cross_operator`：`"<未设置>"` → `"the same completed Close crosses from <= to > each of SMA25, SMA30, SMA35 and their average"`
  - `parameters.entry_sma200_cross_operator`：`"<未设置>"` → `"completed Close crosses from <= SMA200 to > SMA200"`
  - `parameters.measure_1_operator`：`"each current SMA must be strictly greater than its own previous-session value"` → `"<未设置>"`
  - `parameters.measure_1_short_sma_windows`：`[25,30,35]` → `"<未设置>"`
  - `parameters.measure_2_drawdown_stop_pct`：`3` → `"<未设置>"`
  - `parameters.measure_2_recovery_operator`：`"completed Close >= actual 3% stop sell fill"` → `"<未设置>"`
  - `parameters.measure_2_reset_sequence`：`["completed Close < SMA200","completed Close <= SMA200 * 0.95","completed Close crosses from <= SMA200 to > SMA200"]` → `"<未设置>"`
  - `parameters.measure_2_stop_peak`：`"maximum of actual entry fill and completed closes strictly before the current session"` → `"<未设置>"`
  - `parameters.old_8pct_stop`：`"removed and not tested"` → `"<未设置>"`
  - `parameters.sell_rules.r1`：`"<未设置>"` → `"SMA200 strictly decreases for three consecutive completed-session differences"`
  - `parameters.sell_rules.r2`：`"<未设置>"` → `"one-day percentage change of mean(SMA25,SMA30,SMA35) < -0.15%"`
  - `parameters.sell_rules.r3`：`"<未设置>"` → `"completed Close < SMA200"`
  - `parameters.sell_rules.r4`：`"<未设置>"` → `"completed Close < SMA30"`
  - ……另有 9 项，完整定义见父子 `experiment.json`。

### ROT-v0.20 → ROT-v0.20a.1 · QQQ R1+R3 参数训练

- 关系：`parameter_search_of`
- 为什么改：四规则消融选出 R1+R3 结构后，需要在独立训练期优化数值参数而不重新选择规则集合。
- 策略修改：冻结只启用 R1+R3 的结构，在 2000–2015 对入场和退出数值执行分阶段细步长训练与正式候选复核。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.20__26-08-14__qqq_sma_entry_four_exit_ablation_two_periods/experiment.json) · [子实验](../ROT/ROT-v0.20a.1__26-08-14__qqq_sma_r1_r3_parameter_training_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.baseline.buy_long_buffer_pct`：`"<未设置>"` → `2.0`
  - `parameters.baseline.buy_short_buffer_pct`：`"<未设置>"` → `2.0`
  - `parameters.baseline.long_window`：`"<未设置>"` → `200`
  - `parameters.baseline.r1_decline_days`：`"<未设置>"` → `3`
  - `parameters.baseline.r1_min_daily_decline_pct`：`"<未设置>"` → `0.0`
  - `parameters.baseline.r3_sell_buffer_pct`：`"<未设置>"` → `0.0`
  - `parameters.baseline.short_center`：`"<未设置>"` → `30`
  - `parameters.baseline.short_spacing`：`"<未设置>"` → `5`
  - `parameters.combination_count`：`16` → `"<未设置>"`
  - `parameters.entry_post_qualification_price_filters`：`["price > dynamic SMA30 * 0.98","price > dynamic SMA200 * 0.98"]` → `"<未设置>"`
  - `parameters.entry_short_cross_operator`：`"the same completed Close crosses from <= to > each of SMA25, SMA30, SMA35 and their average"` → `"<未设置>"`
  - `parameters.entry_sma200_cross_operator`：`"completed Close crosses from <= SMA200 to > SMA200"` → `"<未设置>"`
  - `parameters.selection.maximum_orders`：`"<未设置>"` → `140`
  - `parameters.selection.minimum_closed_trades`：`"<未设置>"` → `10`
  - `parameters.selection.minimum_return_fraction_of_baseline`：`"<未设置>"` → `0.7`
  - `parameters.selection.plateau_drawdown_tolerance_pct_points`：`"<未设置>"` → `1.0`
  - `parameters.selection.plateau_return_tolerance_pct_points`：`"<未设置>"` → `10.0`
  - `parameters.sell_rules.r1`：`"SMA200 strictly decreases for three consecutive completed-session differences"` → `"<未设置>"`
  - `parameters.sell_rules.r2`：`"one-day percentage change of mean(SMA25,SMA30,SMA35) < -0.15%"` → `"<未设置>"`
  - `parameters.sell_rules.r3`：`"completed Close < SMA200"` → `"<未设置>"`
  - `parameters.sell_rules.r4`：`"completed Close < SMA30"` → `"<未设置>"`
  - `parameters.short_sma_average`：`"simple arithmetic mean of SMA25, SMA30 and SMA35"` → `"<未设置>"`
  - `parameters.short_sma_windows`：`[25,30,35]` → `"<未设置>"`
  - `parameters.stage_1_oat_sweeps`：`"<未设置>"` → `[{"label":"短线中心周期","parameter":"short_center","sweep_id":"short_center","values":[20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40]},{"label":"短线间距","parameter":"sho…`
  - ……另有 26 项，完整定义见父子 `experiment.json`。

### ROT-v0.30 → ROT-v0.50a.1 · Nasdaq-100历史成分Strategy1-90前20轮动

- 关系：`replaces_posthoc_candidate_score_with_point_in_time_strategy1_rotation`
- 为什么改：ROT-v0.30已经验证多标的评分、排名和组合账本，但候选池是事后名单；用户希望改用历史Nasdaq-100时点成分，并把已验证策略1的90%高仓位状态作为每只股票的横截面强度分数，定期持有最强的最多20只。
- 策略修改：从事后21候选的周度趋势评分改为滞后一交易日的历史Nasdaq-100成分池；信号冻结为Strategy1 D0_F0_S0收盘后仓位严格大于90%，按分数取前20并比较1/2/3/5日完整再平衡；新增所有合格股始终满仓等权与不足10只时用固定Bear9补足十槽位两种资金政策，并同时运行0/5bps。
- 修改前：RESILIENCE_21；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_PIT_STRATEGY1_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio/experiment.json) · [子实验](../ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20/experiment.json)
- 自动配置差异：

  - `parameters.allocation_modes.BEAR9_FLOOR10`：`"<未设置>"` → `"if N>=10 each selected security receives 1/N; if 0<=N<10 each receives 10%, and Bear9 receives 1-N/10"`
  - `parameters.allocation_modes.FULL_EQUAL`：`"<未设置>"` → `"if N>0 each selected security receives 1/N; if N=0 remain cash"`
  - `parameters.analysis_start`：`"2007-01-03"` → `"1999-03-10"`
  - `parameters.bear9_individual_timing_enabled`：`"<未设置>"` → `false`
  - `parameters.bear9_prelisting_policy`：`"<未设置>"` → `"unavailable positive target weight remains cash and is not redistributed"`
  - `parameters.bear9_source_definition`：`"<未设置>"` → `"codex/qqq-bear9-rebalance-robustness@337d8cf"`
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
  - `parameters.benchmark`：`"<未设置>"` → `"QQQ Buy & Hold enters on the same first executable Open and uses the same cost scenario"`
  - `parameters.candidate_symbols`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD","Q","HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]` → `"<未设置>"`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.combined_score.market_weight`：`0.3` → `"<未设置>"`
  - `parameters.combined_score.own_weight`：`0.7` → `"<未设置>"`
  - `parameters.core_symbols`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD","Q"]` → `"<未设置>"`
  - `parameters.defensive_assets`：`["TLT","GLD"]` → `"<未设置>"`
  - `parameters.defensive_minimum_own_score`：`50.0` → `"<未设置>"`
  - ……另有 76 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.1 → ROT-v0.50a.2 · Nasdaq-100逐股Strategy1-90持仓与退出归因

- 关系：`attributes_single_security_holding_quality_and_post_exit_rebounds_of`
- 为什么改：父实验显示Strategy1-90作为Top20轮动评分没有跑赢QQQ；用户怀疑主要问题是个股跌出90%后尚未等到反弹便被轮出，因此需要在不改信号、不重算480只股票状态的前提下逐证券检查持仓期间收益质量与卖出后反弹。
- 策略修改：完整复用父validated run的严格90%逐股资格、时点成员与354只曾合格证券价格面板；删除横截面排名、Top20、调仓频率、Bear9和组合资金分配，只为每只证券建立每日收盘确认、下一Open全仓或现金的独立账户，并新增持仓期间CAGR/Sharpe/最大回撤、样本量标记及卖出后5/10/20/60日收益和最大反弹归因，同时保留0/5bps。
- 修改前：NASDAQ100_PIT_STRATEGY1_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_90_ATTRIBUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20/experiment.json) · [子实验](../ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution/experiment.json)
- 自动配置差异：

  - `parameters.allocation_modes.BEAR9_FLOOR10`：`"if N>=10 each selected security receives 1/N; if 0<=N<10 each receives 10%, and Bear9 receives 1-N/10"` → `"<未设置>"`
  - `parameters.allocation_modes.FULL_EQUAL`：`"if N>0 each selected security receives 1/N; if N=0 remain cash"` → `"<未设置>"`
  - `parameters.bear9_individual_timing_enabled`：`false` → `"<未设置>"`
  - `parameters.bear9_prelisting_policy`：`"unavailable positive target weight remains cash and is not redistributed"` → `"<未设置>"`
  - `parameters.bear9_source_definition`：`"codex/qqq-bear9-rebalance-robustness@337d8cf"` → `"<未设置>"`
  - `parameters.bear9_weights.AZO`：`0.1` → `"<未设置>"`
  - `parameters.bear9_weights.DG`：`0.09` → `"<未设置>"`
  - `parameters.bear9_weights.DLTR`：`0.08` → `"<未设置>"`
  - `parameters.bear9_weights.ED`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.MO`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.ORLY`：`0.1` → `"<未设置>"`
  - `parameters.bear9_weights.SO`：`0.12` → `"<未设置>"`
  - `parameters.bear9_weights.WMT`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.WRB`：`0.12` → `"<未设置>"`
  - `parameters.benchmark`：`"QQQ Buy & Hold enters on the same first executable Open and uses the same cost scenario"` → `"<未设置>"`
  - `parameters.cash_interest_pct`：`0.0` → `"<未设置>"`
  - `parameters.data_status`：`"<未设置>"` → `"candidate_pending_review"`
  - `parameters.eligibility_threshold`：`"<未设置>"` → `0.9`
  - `parameters.formal_case_count_per_cost`：`8` → `"<未设置>"`
  - `parameters.formal_case_ids_per_cost`：`["RB01_FULL_EQUAL","RB02_FULL_EQUAL","RB03_FULL_EQUAL","RB05_FULL_EQUAL","RB01_BEAR9_FLOOR10","RB02_BEAR9_FLOOR10","RB03_BEAR9_FLOOR10","RB05_BEAR9_FLOOR10"]` → `"<未设置>"`
  - `parameters.formal_path_count`：`16` → `"<未设置>"`
  - `parameters.fractional_shares`：`true` → `"<未设置>"`
  - `parameters.holding_period_cagr_definition`：`"<未设置>"` → `"geometrically compress calendar CAGR by the Close-position holding-time fraction, matching the existing Strategy1 gate definition; for example 10% calendar CAGR at 50% holding ti…`
  - `parameters.holding_sharpe_definition`：`"<未设置>"` → `"annualized zero-risk-free Sharpe over the returns actually earned while exposed, including entry Open-to-Close, continuing Close-to-Close and exit prior-Close-to-Open components"`
  - ……另有 58 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.2 → ROT-v0.50a.3 · Nasdaq-100个股Strategy1-90三因子交叉消融

- 关系：`factorially_tests_rotation_oriented_entry_and_exit_rules_of`
- 为什么改：逐股归因显示90%门禁的持仓质量跨证券差异很大，而轮动需要宁缺毋滥地确认恢复并更快离开转弱区间；需要在预先冻结的强、中、弱十二只证券和各自十年窗口上，用完整交叉而非孤立单因素实验区分严格入场、机械止损和快速退出的主效应及交互。
- 策略修改：从父实验零成本持仓超过200日的证券中固定持仓CAGR最高四只、最接近中位数四只和最低四只；每只取消Top20排名并回溯最后成分结束日前十年，运行基础严格90%全仓/现金门禁，再把次日起5%止损且母仓位低于10%解锁、入场时StochRSI42/100同时高于0.20、以及持仓中StochRSI100向下穿越0.80并在母仓位低于20%解锁三个开关做2×2×2八案，同时保留0/5bps和逐路径独立账本核对。
- 修改前：NASDAQ100_STRATEGY1_90_ATTRIBUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_GATE_FACTORIAL；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution/experiment.json) · [子实验](../ROT/ROT-v0.50a.3__26-08-27__nasdaq100_strategy1_gate_factorial/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.eligibility_threshold`：`0.9` → `"<未设置>"`
  - `parameters.factor_grid.fast_exit`：`"<未设置>"` → `[false,true]`
  - `parameters.factor_grid.mechanical_stop`：`"<未设置>"` → `[false,true]`
  - `parameters.factor_grid.strict_entry`：`"<未设置>"` → `[false,true]`
  - `parameters.fast_exit_cross`：`"<未设置>"` → `"previous completed value greater than or equal to 0.80 and current completed value strictly below 0.80"`
  - `parameters.fast_exit_fill`：`"<未设置>"` → `"current adjusted Close"`
  - `parameters.fast_exit_reset_weight`：`"<未设置>"` → `0.2`
  - `parameters.fast_exit_stochrsi_period`：`"<未设置>"` → `100`
  - `parameters.fast_exit_threshold`：`"<未设置>"` → `0.8`
  - `parameters.fill_time`：`"next XNYS adjusted Open"` → `"<未设置>"`
  - `parameters.formal_case_count_per_security_per_cost`：`"<未设置>"` → `8`
  - `parameters.formal_security_case_count_per_cost`：`"<未设置>"` → `96`
  - `parameters.gate_operator`：`"<未设置>"` → `"strictly greater than"`
  - `parameters.gate_threshold`：`"<未设置>"` → `0.9`
  - `parameters.holding_period_cagr_definition`：`"geometrically compress calendar CAGR by the Close-position holding-time fraction, matching the existing Strategy1 gate definition; for example 10% calendar CAGR at 50% holding ti…` → `"geometric compression of calendar CAGR by Close-position exposure fraction"`
  - `parameters.holding_sharpe_definition`：`"annualized zero-risk-free Sharpe over the returns actually earned while exposed, including entry Open-to-Close, continuing Close-to-Close and exit prior-Close-to-Open components"` → `"<未设置>"`
  - `parameters.holding_time_definition`：`"percentage of analysis sessions whose Close ends with positive shares"` → `"<未设置>"`
  - `parameters.lock_interaction`：`"<未设置>"` → `"stop and fast-exit locks are independent; every enabled active lock must clear before re-entry"`
  - `parameters.max_drawdown_definition`：`"maximum peak-to-trough drawdown of the single-security gated account, whose cash intervals are flat"` → `"<未设置>"`
  - `parameters.membership_after_selection`：`"<未设置>"` → `"not applied; membership history selects and timestamps the sample but does not shorten the requested ten-year security test"`
  - `parameters.membership_definition`：`"parent run's point-in-time Nasdaq-100 membership observed with one XNYS-session lag"` → `"<未设置>"`
  - `parameters.parent_experiment_id`：`"nasdaq100_pit_stochrsi_strategy1_top20_rotation_v1"` → `"nasdaq100_strategy1_90_holding_attribution_v1"`
  - ……另有 42 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.1 → ROT-v0.50a.4 · Nasdaq-100 Strategy1轮动三维消融（2005–2010）

- 关系：`factorially_revises_selection_exit_and_rebalancing_of`
- 为什么改：父轮动在全历史中几乎始终满仓、分数饱和且频繁恢复等权，未把单标的90门禁优势转成组合优势；用户希望集中观察2005至2010，保留后续个股诊断中唯一方向较好的快速退出，并分别检验扩大合格集合与取消存量再平衡。
- 策略修改：窗口固定为2005-01-03至2010-12-31，删除Bear9与1/3/5日频率，只保留两日决策；将快速退出开关、Top20>90/全部>90/全部>80三种范围、恢复等权/只处理进出两种资金管理做2×3×2完整12格，同时新增全部时点成分两日等权与QQQ Buy & Hold基准，并保留0/5bps。
- 修改前：NASDAQ100_PIT_STRATEGY1_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_ROTATION_2005_2010；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20/experiment.json) · [子实验](../ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010/experiment.json)
- 自动配置差异：

  - `parameters.allocation_modes.BEAR9_FLOOR10`：`"if N>=10 each selected security receives 1/N; if 0<=N<10 each receives 10%, and Bear9 receives 1-N/10"` → `"<未设置>"`
  - `parameters.allocation_modes.ENTRY_EXIT_ONLY`：`"<未设置>"` → `"at every scheduled decision, sell deselected holdings, preserve continuing shares, and divide actual available execution-Open cash equally among newly selected securities"`
  - `parameters.allocation_modes.FULL_EQUAL`：`"if N>0 each selected security receives 1/N; if N=0 remain cash"` → `"<未设置>"`
  - `parameters.allocation_modes.RESTORE_EQUAL`：`"<未设置>"` → `"at every scheduled decision, resize all selected holdings to equal target weights"`
  - `parameters.analysis_end`：`"2026-08-04"` → `"2010-12-31"`
  - `parameters.analysis_start`：`"1999-03-10"` → `"2005-01-03"`
  - `parameters.baseline1_case_id`：`"<未设置>"` → `"FAST0_TOP20_90_REBAL"`
  - `parameters.baseline2_case_id`：`"<未设置>"` → `"NDX100_ALL_REBAL"`
  - `parameters.baseline2_definition`：`"<未设置>"` → `"every two trading days equal-weight every lagged point-in-time Nasdaq-100 member with a valid signal Close and next Open"`
  - `parameters.baseline3_case_id`：`"<未设置>"` → `"QQQ_BUY_HOLD"`
  - `parameters.baseline3_definition`：`"<未设置>"` → `"buy QQQ at the first executable Open and hold through the final Close"`
  - `parameters.bear9_individual_timing_enabled`：`false` → `"<未设置>"`
  - `parameters.bear9_prelisting_policy`：`"unavailable positive target weight remains cash and is not redistributed"` → `"<未设置>"`
  - `parameters.bear9_source_definition`：`"codex/qqq-bear9-rebalance-robustness@337d8cf"` → `"<未设置>"`
  - `parameters.bear9_weights.AZO`：`0.1` → `"<未设置>"`
  - `parameters.bear9_weights.DG`：`0.09` → `"<未设置>"`
  - `parameters.bear9_weights.DLTR`：`0.08` → `"<未设置>"`
  - `parameters.bear9_weights.ED`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.MO`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.ORLY`：`0.1` → `"<未设置>"`
  - `parameters.bear9_weights.SO`：`0.12` → `"<未设置>"`
  - `parameters.bear9_weights.WMT`：`0.13` → `"<未设置>"`
  - `parameters.bear9_weights.WRB`：`0.12` → `"<未设置>"`
  - `parameters.benchmark`：`"QQQ Buy & Hold enters on the same first executable Open and uses the same cost scenario"` → `"<未设置>"`
  - ……另有 60 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.3 → ROT-v0.50a.4 · Nasdaq-100 Strategy1轮动三维消融（2005–2010）

- 关系：`promotes_only_fast_exit_mechanism_into_portfolio_test`
- 为什么改：十二证券三因子诊断中用户只保留快速退出，不再携带5%机械止损或严格双周期入场；需要把其精确真下穿和低于20%解锁语义移植到完整历史成分组合，而不是继续依赖事后挑选的十二只个股。
- 策略修改：保留StochRSI100由不低于0.80真下穿至0.80以下、穿越Close退出、母仓位严格低于20%解锁和母状态持续更新；删除机械止损与严格入场，在2005至2010完整时点成分集合中与选股范围和再平衡开关交叉。
- 修改前：NASDAQ100_STRATEGY1_GATE_FACTORIAL；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_ROTATION_2005_2010；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.3__26-08-27__nasdaq100_strategy1_gate_factorial/experiment.json) · [子实验](../ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010/experiment.json)
- 自动配置差异：

  - `parameters.allocation_modes.ENTRY_EXIT_ONLY`：`"<未设置>"` → `"at every scheduled decision, sell deselected holdings, preserve continuing shares, and divide actual available execution-Open cash equally among newly selected securities"`
  - `parameters.allocation_modes.RESTORE_EQUAL`：`"<未设置>"` → `"at every scheduled decision, resize all selected holdings to equal target weights"`
  - `parameters.analysis_end`：`"<未设置>"` → `"2010-12-31"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2005-01-03"`
  - `parameters.baseline1_case_id`：`"<未设置>"` → `"FAST0_TOP20_90_REBAL"`
  - `parameters.baseline2_case_id`：`"<未设置>"` → `"NDX100_ALL_REBAL"`
  - `parameters.baseline2_definition`：`"<未设置>"` → `"every two trading days equal-weight every lagged point-in-time Nasdaq-100 member with a valid signal Close and next Open"`
  - `parameters.baseline3_case_id`：`"<未设置>"` → `"QQQ_BUY_HOLD"`
  - `parameters.baseline3_definition`：`"<未设置>"` → `"buy QQQ at the first executable Open and hold through the final Close"`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.data_status`：`"candidate_pending_review"` → `"<未设置>"`
  - `parameters.factor_grid.fast_exit`：`[false,true]` → `"<未设置>"`
  - `parameters.factor_grid.mechanical_stop`：`[false,true]` → `"<未设置>"`
  - `parameters.factor_grid.strict_entry`：`[false,true]` → `"<未设置>"`
  - `parameters.fast_exit.cross`：`"<未设置>"` → `"previous completed StochRSI100 greater than or equal to 0.80 and current completed StochRSI100 strictly below 0.80"`
  - `parameters.fast_exit.eligible_position`：`"<未设置>"` → `"shares carried from the previous Close only"`
  - `parameters.fast_exit.enabled_values`：`"<未设置>"` → `[false,true]`
  - `parameters.fast_exit.fill`：`"<未设置>"` → `"current adjusted Close"`
  - `parameters.fast_exit.mother_state_continues_while_locked`：`"<未设置>"` → `true`
  - `parameters.fast_exit.reset_operator`：`"<未设置>"` → `"strictly below"`
  - `parameters.fast_exit.reset_weight`：`"<未设置>"` → `0.2`
  - `parameters.fast_exit.stochrsi_period`：`"<未设置>"` → `100`
  - `parameters.fast_exit.threshold`：`"<未设置>"` → `0.8`
  - `parameters.fast_exit_cross`：`"previous completed value greater than or equal to 0.80 and current completed value strictly below 0.80"` → `"<未设置>"`
  - ……另有 78 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.2 → ROT-v0.50a.5 · Nasdaq-100 Strategy1能量因子解剖

- 关系：`generalizes_binary_gate_attribution_into_full_energy_surface`
- 为什么改：逐股90%门禁归因只观察高能量持仓和退出后表现，无法判断0至1完整能量曲线是否单调预测未来收益，也无法区分高能量正在上升、保持或下降；在继续修改参数或组合前，需要先隔离因子本身。
- 策略修改：冻结原Strategy1母规则并重算所有历史时点成分的完整0至1收盘后能量，不建立逐股全仓账户；把每个可执行证券日按七档能量、五日方向和高能量穿越状态分类，从下一真实Open观察5/10/20/60日证券及同期QQQ收益、MFE/MAE、逐日Rank IC、五时期稳定性和月度区块置信区间，同时保留0/5bps。
- 修改前：NASDAQ100_STRATEGY1_90_ATTRIBUTION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_ENERGY_FACTOR；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution/experiment.json) · [子实验](../ROT/ROT-v0.50a.5__26-08-28__nasdaq100_strategy1_energy_factor_anatomy/experiment.json)
- 自动配置差异：

  - `parameters.aggregation.primary_weighting`：`"<未设置>"` → `"each signal date receives equal weight after averaging securities within date and energy group"`
  - `parameters.aggregation.secondary_weightings`：`"<未设置>"` → `["pooled security-date events","each security receives equal weight"]`
  - `parameters.aggregation.small_samples_are_reported_not_hidden`：`"<未设置>"` → `true`
  - `parameters.bootstrap.confidence_level`：`"<未设置>"` → `0.95`
  - `parameters.bootstrap.replications`：`"<未设置>"` → `1000`
  - `parameters.bootstrap.seed`：`"<未设置>"` → `20260828`
  - `parameters.bootstrap.unit`：`"<未设置>"` → `"calendar month block of daily cross-sectional aggregates"`
  - `parameters.cross_sectional_ic.factor_variants`：`"<未设置>"` → `["raw Strategy1 score","causal own-history score percentile"]`
  - `parameters.cross_sectional_ic.method`：`"<未设置>"` → `"daily Spearman rank correlation"`
  - `parameters.cross_sectional_ic.minimum_securities_per_date`：`"<未设置>"` → `10`
  - `parameters.cross_sectional_ic.outcome`：`"<未设置>"` → `"matched-QQQ forward return"`
  - `parameters.diagnostic_spreads`：`"<未设置>"` → `["score strictly above 0.90 minus score no greater than 0.90","E99_100 minus E90_95","high-energy RISING minus high-energy FALLING"]`
  - `parameters.eligibility_threshold`：`0.9` → `"<未设置>"`
  - `parameters.energy_buckets`：`"<未设置>"` → `[{"bucket_id":"E00_20","lower":0.0,"upper":0.2,"upper_inclusive":false},{"bucket_id":"E20_50","lower":0.2,"upper":0.5,"upper_inclusive":false},{"bucket_id":"E50_80","lower":0.5,"u…`
  - `parameters.energy_direction.falling`：`"<未设置>"` → `"current score is below the score five XNYS sessions earlier by more than tolerance"`
  - `parameters.energy_direction.flat`：`"<未设置>"` → `"absolute five-session score change is no greater than tolerance"`
  - `parameters.energy_direction.lookback_xnys_sessions`：`"<未设置>"` → `5`
  - `parameters.energy_direction.numerical_flat_tolerance`：`"<未设置>"` → `1e-12`
  - `parameters.energy_direction.rising`：`"<未设置>"` → `"current score exceeds the score five XNYS sessions earlier by more than tolerance"`
  - `parameters.entry_definition`：`"<未设置>"` → `"signal Close at t; adjusted Open at XNYS session t+1 must be a real non-synthetic non-prelisting non-terminal bar"`
  - `parameters.era_windows`：`"<未设置>"` → `[{"end":"2004-12-31","era_id":"ERA_2000_2004","start":"1999-03-10"},{"end":"2009-12-31","era_id":"ERA_2005_2009","start":"2005-01-03"},{"end":"2014-12-31","era_id":"ERA_2010_2014"…`
  - `parameters.event_notional_usd`：`"<未设置>"` → `1.0`
  - `parameters.exit_definition`：`"<未设置>"` → `"adjusted Close at XNYS session t+h; if the security's last real vendor session occurs earlier, end at that last real Close and flag terminal truncation"`
  - `parameters.fill_time`：`"next XNYS adjusted Open"` → `"<未设置>"`
  - ……另有 64 项，完整定义见父子 `experiment.json`。

### ROT-v0.50a.4 → ROT-v0.50a.5 · Nasdaq-100 Strategy1能量因子解剖

- 关系：`separates_score_predictiveness_from_portfolio_construction_failure`
- 为什么改：2005至2010完整组合消融中，原始Top20两日等权虽为十二格最优却落后QQQ，快卖、扩大范围和取消再平衡均未稳定改善；需要判断失败来自能量分数缺少横截面预测力，还是高换手和组合构建吞噬了有效毛信号。
- 策略修改：删除Top20、共享资本、两日再平衡和组合CAGR，把研究单位改为历史时点成分的独立证券日事件；固定下一Open起算、QQQ同期超额、日等权和证券等权聚合，并预声明能量排序、90%门禁和升降方向三项独立支持标准。
- 修改前：NASDAQ100_STRATEGY1_ROTATION_2005_2010；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_STRATEGY1_ENERGY_FACTOR；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010/experiment.json) · [子实验](../ROT/ROT-v0.50a.5__26-08-28__nasdaq100_strategy1_energy_factor_anatomy/experiment.json)
- 自动配置差异：

  - `parameters.aggregation.primary_weighting`：`"<未设置>"` → `"each signal date receives equal weight after averaging securities within date and energy group"`
  - `parameters.aggregation.secondary_weightings`：`"<未设置>"` → `["pooled security-date events","each security receives equal weight"]`
  - `parameters.aggregation.small_samples_are_reported_not_hidden`：`"<未设置>"` → `true`
  - `parameters.allocation_modes.ENTRY_EXIT_ONLY`：`"at every scheduled decision, sell deselected holdings, preserve continuing shares, and divide actual available execution-Open cash equally among newly selected securities"` → `"<未设置>"`
  - `parameters.allocation_modes.RESTORE_EQUAL`：`"at every scheduled decision, resize all selected holdings to equal target weights"` → `"<未设置>"`
  - `parameters.analysis_end`：`"2010-12-31"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"2005-01-03"` → `"1999-03-10"`
  - `parameters.baseline1_case_id`：`"FAST0_TOP20_90_REBAL"` → `"<未设置>"`
  - `parameters.baseline2_case_id`：`"NDX100_ALL_REBAL"` → `"<未设置>"`
  - `parameters.baseline2_definition`：`"every two trading days equal-weight every lagged point-in-time Nasdaq-100 member with a valid signal Close and next Open"` → `"<未设置>"`
  - `parameters.baseline3_case_id`：`"QQQ_BUY_HOLD"` → `"<未设置>"`
  - `parameters.baseline3_definition`：`"buy QQQ at the first executable Open and hold through the final Close"` → `"<未设置>"`
  - `parameters.bootstrap.confidence_level`：`"<未设置>"` → `0.95`
  - `parameters.bootstrap.replications`：`"<未设置>"` → `1000`
  - `parameters.bootstrap.seed`：`"<未设置>"` → `20260828`
  - `parameters.bootstrap.unit`：`"<未设置>"` → `"calendar month block of daily cross-sectional aggregates"`
  - `parameters.cash_interest_pct`：`0.0` → `"<未设置>"`
  - `parameters.cross_sectional_ic.factor_variants`：`"<未设置>"` → `["raw Strategy1 score","causal own-history score percentile"]`
  - `parameters.cross_sectional_ic.method`：`"<未设置>"` → `"daily Spearman rank correlation"`
  - `parameters.cross_sectional_ic.minimum_securities_per_date`：`"<未设置>"` → `10`
  - `parameters.cross_sectional_ic.outcome`：`"<未设置>"` → `"matched-QQQ forward return"`
  - `parameters.data_status`：`"<未设置>"` → `"candidate_pending_review"`
  - `parameters.diagnostic_spreads`：`"<未设置>"` → `["score strictly above 0.90 minus score no greater than 0.90","E99_100 minus E90_95","high-energy RISING minus high-energy FALLING"]`
  - `parameters.energy_buckets`：`"<未设置>"` → `[{"bucket_id":"E00_20","lower":0.0,"upper":0.2,"upper_inclusive":false},{"bucket_id":"E20_50","lower":0.2,"upper":0.5,"upper_inclusive":false},{"bucket_id":"E50_80","lower":0.5,"u…`
  - ……另有 76 项，完整定义见父子 `experiment.json`。

### ROT-v0.40a.2 → ROT-v0.40b.1 · QQQ P24长短趋势严格门槛网格

- 关系：`removes_risk_factor_and_tightens_p24_thresholds_of`
- 为什么改：三因子下行风险消融没有形成可晋级的稳健改善，用户决定回到不含风险因子的原P24，并继续探索此前仍接近边界的长短趋势严格程度；持仓时间与持仓CAGR也需要逐组显式报告，以区分回撤改善来自趋势质量还是单纯减少持仓。
- 策略修改：删除下行波动风险因子及投票结构，恢复原P24的L180、K10、S20、W10与连续2日确认；仅将F2长期门槛扫描0.02/0.03/0.04/0.05%每日、F4短期门槛扫描0/0.02/0.05/0.10%每日，共16组，并新增实际持仓日、持仓率与按252持仓日折算的持仓CAGR诊断。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.40a.2__26-08-21__qqq_three_factor_downside_risk_training_2000_2015/experiment.json) · [子实验](../ROT/ROT-v0.40b.1__26-08-28__qqq_p24_stricter_threshold_grid_2000_2015/experiment.json)
- 自动配置差异：

  - `parameters.center_risk_parameters.danger_ratio`：`1.5` → `"<未设置>"`
  - `parameters.center_risk_parameters.long_window`：`120` → `"<未设置>"`
  - `parameters.center_risk_parameters.recovery_ratio`：`1.1` → `"<未设置>"`
  - `parameters.center_risk_parameters.short_window`：`20` → `"<未设置>"`
  - `parameters.decision_structures`：`["STRICT_3_OF_3","MAJORITY_2_OF_3","RISK_VETO"]` → `"<未设置>"`
  - `parameters.fixed_parameters.entry_confirmation_sessions`：`"<未设置>"` → `2`
  - `parameters.fixed_parameters.long_slope_lookback`：`"<未设置>"` → `10`
  - `parameters.fixed_parameters.long_sma_window`：`"<未设置>"` → `180`
  - `parameters.fixed_parameters.relative_strength_lookback`：`"<未设置>"` → `120`
  - `parameters.fixed_parameters.short_regression_window`：`"<未设置>"` → `10`
  - `parameters.fixed_parameters.short_sma_window`：`"<未设置>"` → `20`
  - `parameters.frozen_trend_parameters.entry_confirmation_sessions`：`2` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.long_slope_lookback`：`3` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.long_slope_threshold_daily_pct`：`0.04` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.long_sma_window`：`180` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.relative_strength_lookback`：`120` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.short_quality_threshold_daily_pct`：`0.0` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.short_regression_window`：`10` → `"<未设置>"`
  - `parameters.frozen_trend_parameters.short_sma_window`：`20` → `"<未设置>"`
  - `parameters.holding_diagnostics.holding_cagr_pct`：`"<未设置>"` → `"若holding_sessions大于0，则(final_equity/initial_cash)^(252/holding_sessions)-1，再乘100；该值包含交易成本与全部账户收益，但压缩掉空仓日，只作为资金效率诊断"`
  - `parameters.holding_diagnostics.holding_sessions`：`"<未设置>"` → `"逐日账本中开盘成交后shares大于0的交易日数量"`
  - `parameters.holding_diagnostics.holding_years_252`：`"<未设置>"` → `"holding_sessions除以252"`
  - `parameters.original_p24.long_slope_threshold_daily_pct`：`"<未设置>"` → `0.02`
  - `parameters.original_p24.short_quality_threshold_daily_pct`：`"<未设置>"` → `0.0`
  - ……另有 26 项，完整定义见父子 `experiment.json`。

### ROT-v0.40b.1 → ROT-v0.40c.1 · Nasdaq-100 P24原版、差集与严格版全体等权

- 关系：`applies_original_strict_and_difference_states_to_point_in_time_constituents`
- 为什么改：QQQ参数扰动显示严格代表与原P24之间存在可解释的状态差集；用户希望判断P24用于个股筛选时，原状态、严格子集和被严格门槛排除的差集分别具有什么持仓质量，同时要求使用真实历史Nasdaq-100成员、全部持有而不排名。
- 策略修改：冻结原P24为F2>0.02%与F4>0、严格版为F2>0.04%与F4>0.05%，其余L180/K10/S20/W10/C2不变；在2005-2012滞后一日的历史Nasdaq-100成员中建立原版、原版非严格差集、严格版三条每日全体等权组合，下一Open执行，仅测试0/10bps并新增持仓时间、持仓CAGR与持股数。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_P24_THREE_STATE_2005_2012；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.40b.1__26-08-28__qqq_p24_stricter_threshold_grid_2000_2015/experiment.json) · [子实验](../ROT/ROT-v0.40c.1__26-08-29__nasdaq100_p24_three_state_2005_2012/experiment.json)
- 自动配置差异：

  - `cost_scenarios_bps_per_side`：`[0,5]` → `[0,10]`
  - `parameters.analysis_end`：`"<未设置>"` → `"2012-12-31"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2005-01-03"`
  - `parameters.case_definitions.ORIGINAL_NOT_STRICT`：`"<未设置>"` → `"original_target_long AND NOT strict_target_long"`
  - `parameters.case_definitions.ORIGINAL_P24`：`"<未设置>"` → `"original_target_long"`
  - `parameters.case_definitions.STRICT_P24`：`"<未设置>"` → `"strict_target_long"`
  - `parameters.fixed_parameters.relative_strength_lookback`：`120` → `"<未设置>"`
  - `parameters.formal_case_count_per_cost`：`"<未设置>"` → `3`
  - `parameters.formal_case_ids_per_cost`：`"<未设置>"` → `["ORIGINAL_P24","ORIGINAL_NOT_STRICT","STRICT_P24"]`
  - `parameters.formal_strategy_path_count`：`"<未设置>"` → `6`
  - `parameters.holding_diagnostics.holding_cagr_pct`：`"若holding_sessions大于0，则(final_equity/initial_cash)^(252/holding_sessions)-1，再乘100；该值包含交易成本与全部账户收益，但压缩掉空仓日，只作为资金效率诊断"` → `"full account terminal growth annualized over holding_sessions/252; includes costs but compresses cash-only sessions and is diagnostic rather than a selection metric"`
  - `parameters.holding_diagnostics.holding_sessions`：`"逐日账本中开盘成交后shares大于0的交易日数量"` → `"sessions with at least one positive position after that Open's execution"`
  - `parameters.holding_diagnostics.holding_years_252`：`"holding_sessions除以252"` → `"<未设置>"`
  - `parameters.holding_diagnostics.holdings_count`：`"<未设置>"` → `"number of positive security positions after daily Open execution"`
  - `parameters.initial_state`：`"<未设置>"` → `"all portfolios start in cash; indicator history may prewarm but confirmation streak resets at analysis_start"`
  - `parameters.numerical_zero_order_notional_usd`：`"<未设置>"` → `1e-09`
  - `parameters.original_p24.long_slope_threshold_daily_pct`：`0.02` → `"<未设置>"`
  - `parameters.original_p24.short_quality_threshold_daily_pct`：`0.0` → `"<未设置>"`
  - `parameters.original_thresholds.long_slope_threshold_daily_pct`：`"<未设置>"` → `0.02`
  - `parameters.original_thresholds.short_quality_threshold_daily_pct`：`"<未设置>"` → `0.0`
  - `parameters.parent_experiment_id`：`"<未设置>"` → `"qqq_p24_stricter_threshold_grid_2000_2015_v1"`
  - `parameters.parent_run_id`：`"<未设置>"` → `"run_20260828T115751Z_a4c38892"`
  - `parameters.robustness_subwindows`：`[{"end":"2003-12-31","start":"2000-03-17","window_id":"W1_2000_2003"},{"end":"2007-12-31","start":"2004-01-01","window_id":"W2_2004_2007"},{"end":"2011-12-31","start":"2008-01-01"…` → `"<未设置>"`
  - `parameters.selection.allocation`：`"<未设置>"` → `"restore equal weight at every completed Close for next Open"`
  - ……另有 49 项，完整定义见父子 `experiment.json`。

### ROT-v0.40c.1 → ROT-v0.40d.1 · Nasdaq-100 P24滞回与再平衡二乘二消融

- 关系：`factorially_tests_hysteresis_and_rebalance_suppression_of`
- 为什么改：父实验显示严格P24提高了零成本收益质量却没有降低组合回撤，差集单独持有为负且每日恢复等权带来极高换手；因此需要把差集从独立买入路径改为严格持仓的退出缓冲，并独立检验不为名单未变时的价格漂移每日调仓是否能减少摩擦。
- 策略修改：完全冻结2005-2012时点成员、原P24与严格P24参数、逐日Close确认、下一Open及0/10bps；建立持有政策（严格状态对称持有 vs 严格买入且原P24失效才退出）与再平衡政策（每日恢复等权 vs 仅名单改变或未完成目标时恢复等权）的2×2四案，不加入市场风险开关、排名、数量上限或新参数。
- 修改前：NASDAQ100_P24_THREE_STATE_2005_2012；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_P24_HYSTERESIS_REBALANCE_2005_2012；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.40c.1__26-08-29__nasdaq100_p24_three_state_2005_2012/experiment.json) · [子实验](../ROT/ROT-v0.40d.1__26-08-29__nasdaq100_p24_hysteresis_rebalance_ablation/experiment.json)
- 自动配置差异：

  - `parameters.case_definitions.ORIGINAL_NOT_STRICT`：`"original_target_long AND NOT strict_target_long"` → `"<未设置>"`
  - `parameters.case_definitions.ORIGINAL_P24`：`"original_target_long"` → `"<未设置>"`
  - `parameters.case_definitions.STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL.holding_policy`：`"<未设置>"` → `"STRICT_ENTRY_ORIGINAL_EXIT"`
  - `parameters.case_definitions.STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL.rebalance_policy`：`"<未设置>"` → `"SELECTION_CHANGE_EQUAL"`
  - `parameters.case_definitions.STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL.holding_policy`：`"<未设置>"` → `"STRICT_ENTRY_ORIGINAL_EXIT"`
  - `parameters.case_definitions.STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL.rebalance_policy`：`"<未设置>"` → `"DAILY_EQUAL"`
  - `parameters.case_definitions.STRICT_LEVEL_CHANGE_EQUAL.holding_policy`：`"<未设置>"` → `"STRICT_LEVEL"`
  - `parameters.case_definitions.STRICT_LEVEL_CHANGE_EQUAL.rebalance_policy`：`"<未设置>"` → `"SELECTION_CHANGE_EQUAL"`
  - `parameters.case_definitions.STRICT_LEVEL_DAILY_EQUAL.holding_policy`：`"<未设置>"` → `"STRICT_LEVEL"`
  - `parameters.case_definitions.STRICT_LEVEL_DAILY_EQUAL.rebalance_policy`：`"<未设置>"` → `"DAILY_EQUAL"`
  - `parameters.case_definitions.STRICT_P24`：`"strict_target_long"` → `"<未设置>"`
  - `parameters.factor_axes.holding_policy.STRICT_ENTRY_ORIGINAL_EXIT`：`"<未设置>"` → `"空仓只允许严格P24进入；进入后只要原P24仍为真就继续目标持有；原P24失效或离开滞后成员集合才取消目标持仓，之后再次入场仍要求严格P24。"`
  - `parameters.factor_axes.holding_policy.STRICT_LEVEL`：`"<未设置>"` → `"空仓和持仓都要求当日严格P24目标状态为真；严格状态失效即取消目标持仓。"`
  - `parameters.factor_axes.rebalance_policy.DAILY_EQUAL`：`"<未设置>"` → `"每个完成Close都为下一Open按当日信号账户净值和信号Close恢复全部目标股票等权。"`
  - `parameters.factor_axes.rebalance_policy.SELECTION_CHANGE_EQUAL`：`"<未设置>"` → `"仍逐日确认同一目标名单并在下一Open成交，但只有目标股票集合改变或前次真实行情不可用导致目标未完成时，才把全部目标股票恢复等权；名单不变时允许权重自然漂移。"`
  - `parameters.formal_case_count_per_cost`：`3` → `4`
  - `parameters.formal_case_ids_per_cost`：`["ORIGINAL_P24","ORIGINAL_NOT_STRICT","STRICT_P24"]` → `["STRICT_LEVEL_DAILY_EQUAL","STRICT_LEVEL_CHANGE_EQUAL","STRICT_ENTRY_ORIGINAL_EXIT_DAILY_EQUAL","STRICT_ENTRY_ORIGINAL_EXIT_CHANGE_EQUAL"]`
  - `parameters.formal_strategy_path_count`：`6` → `8`
  - `parameters.initial_state`：`"all portfolios start in cash; indicator history may prewarm but confirmation streak resets at analysis_start"` → `"all four portfolios start in cash; indicator history may prewarm but confirmation and hysteresis state reset at analysis_start"`
  - `parameters.parent_experiment_id`：`"qqq_p24_stricter_threshold_grid_2000_2015_v1"` → `"nasdaq100_p24_three_state_portfolios_2005_2012_v1"`
  - `parameters.parent_run_id`：`"run_20260828T115751Z_a4c38892"` → `"run_20260828T180707Z_6bcf13f0"`
  - `parameters.selection.allocation`：`"restore equal weight at every completed Close for next Open"` → `"<未设置>"`
  - `parameters.selection.rebalance_interval_trading_days`：`1` → `"<未设置>"`
  - `parameters.selection.signal_frequency_trading_days`：`"<未设置>"` → `1`
  - ……另有 15 项，完整定义见父子 `experiment.json`。

### ROT-v0.30 → ROT-v0.50b.1 · Nasdaq-100 12-1动量Top10/Top20缓冲基线

- 关系：`replaces_posthoc_pool_with_point_in_time_rank_buffer`
- 为什么改：此前多标的组合使用事后挑出的21只候选和复合趋势评分，无法回答在真实历史指数成分中最简单的横截面强弱排名是否有效；用户还希望用进入前10、跌出前20的缓冲直接减少换手。
- 策略修改：候选池改为滞后一日的Nasdaq-100历史时点成分，评分简化为跳过最近一个月的12-1总收益动量；月末比较点时等权、强制Top10和Top10进入/Top20退出三条路径，统一下一Open、0/5bps、单股10%上限，并把pending_review数据限制显式写入门禁。
- 修改前：RESILIENCE_21；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_MOMENTUM_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio/experiment.json) · [子实验](../ROT/ROT-v0.50b.1__26-08-29__nasdaq100_12_1_momentum_top10_top20_buffer/experiment.json)
- 自动配置差异：

  - `parameters.allocation.PIT_EQUAL_WEIGHT`：`"<未设置>"` → `"equal weight across the lagged point-in-time member set at each monthly signal"`
  - `parameters.allocation.TOP10_EXIT20_BUFFER`：`"<未设置>"` → `"if N<10 use 10% each and keep residual cash; if 10<=N<=20 use 1/N each"`
  - `parameters.allocation.TOP10_MONTHLY_REPLACE`：`"<未设置>"` → `"10% per selected security; unused slots remain cash"`
  - `parameters.allocation.maximum_single_security_weight`：`"<未设置>"` → `0.1`
  - `parameters.allocation.off_cycle_exit_cash`：`"<未设置>"` → `"remain cash until the next monthly rebalance"`
  - `parameters.analysis_signal_start`：`"<未设置>"` → `"1999-03-31"`
  - `parameters.analysis_start`：`"2007-01-03"` → `"<未设置>"`
  - `parameters.bear_interval_source`：`"research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json"` → `"<未设置>"`
  - `parameters.benchmark`：`"<未设置>"` → `"QQQ Buy & Hold enters on 1999-04-01 Open under the same cost scenario"`
  - `parameters.candidate_symbols`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD","Q","HSY","ORLY","MO","WRB","EQT","LMT","GIS","WEC"]` → `"<未设置>"`
  - `parameters.cash_interest_pct`：`"<未设置>"` → `0.0`
  - `parameters.combined_score.market_weight`：`0.3` → `"<未设置>"`
  - `parameters.combined_score.own_weight`：`0.7` → `"<未设置>"`
  - `parameters.core_symbols`：`["AZO","TLT","COR","EXE","DVA","SJM","SO","ED","GLD","CHD","HRL","GILD","Q"]` → `"<未设置>"`
  - `parameters.defensive_assets`：`["TLT","GLD"]` → `"<未设置>"`
  - `parameters.defensive_minimum_own_score`：`50.0` → `"<未设置>"`
  - `parameters.execution_order`：`"sell_all_differences_then_buy_differences_in_symbol_ascending_order; order differences with absolute notional <= 1e-12 USD are treated as numerical zero during ledger reconciliat…` → `"sell excess first by security_id, then buy deficits by security_id with available cash"`
  - `parameters.first_execution_date`：`"<未设置>"` → `"1999-04-01"`
  - `parameters.formal_cases`：`["unified_score","own_score_only","sma200_atr_only","risk_base_no_timing"]` → `["PIT_EQUAL_WEIGHT","TOP10_MONTHLY_REPLACE","TOP10_EXIT20_BUFFER"]`
  - `parameters.market_score.breadth_definition`：`"当前 S&P 500 与 Nasdaq-100 股票并集里，已形成 SMA200 且 Close>SMA200 的比例；成分列表固定于 2026 快照，因此只作带生存偏差的探索性环境变量。"` → `"<未设置>"`
  - `parameters.market_score.breadth_weight`：`0.3` → `"<未设置>"`
  - `parameters.market_score.minimum_breadth_assets`：`100` → `"<未设置>"`
  - `parameters.market_score.qqq_weight`：`0.3` → `"<未设置>"`
  - `parameters.market_score.spy_weight`：`0.4` → `"<未设置>"`
  - ……另有 66 项，完整定义见父子 `experiment.json`。

### ROT-v0.50b.1 → ROT-v0.50b.2 · Nasdaq-100 12-1排名绝对动量门槛消融

- 关系：`factorially_tests_absolute_momentum_eligibility_of`
- 为什么改：父实验表明12-1横截面排名具有一定收益能力，缓冲也能降低换手，但两条集中路径的最大回撤仍接近负80%，说明在全市场下跌时仍被迫持有相对最强者。用户希望采用宁缺毋滥的绝对上涨资格，让不够强的月份主动留现金。
- 策略修改：保持历史时点成分、12-1排名、月末收盘确认、次日开盘、Top10与Top10/Top20资金规则及0/5bps不变；新增精确月末6-1收益，并固定比较无门槛、12-1为正、6-1为正、两者同时为正四种资格。每种资格同时运行强制Top10与缓冲组合；资格先于排名且覆盖缓冲，资格不足时不补满并允许全现金。
- 修改前：NASDAQ100_MOMENTUM_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：NASDAQ100_MOMENTUM_ROTATION；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../ROT/ROT-v0.50b.1__26-08-29__nasdaq100_12_1_momentum_top10_top20_buffer/experiment.json) · [子实验](../ROT/ROT-v0.50b.2__26-08-29__nasdaq100_absolute_momentum_gate_factorial/experiment.json)
- 自动配置差异：

  - `parameters.absolute_gate_cases`：`"<未设置>"` → `[{"case_id":"TOP10_REPLACE__NO_GATE","gate":"none","portfolio_style":"monthly_replace"},{"case_id":"TOP10_BUFFER__NO_GATE","gate":"none","portfolio_style":"entry10_exit20_buffer"}…`
  - `parameters.allocation.PIT_EQUAL_WEIGHT`：`"equal weight across the lagged point-in-time member set at each monthly signal"` → `"<未设置>"`
  - `parameters.allocation.TOP10_EXIT20_BUFFER`：`"if N<10 use 10% each and keep residual cash; if 10<=N<=20 use 1/N each"` → `"<未设置>"`
  - `parameters.allocation.TOP10_MONTHLY_REPLACE`：`"10% per selected security; unused slots remain cash"` → `"<未设置>"`
  - `parameters.allocation.entry10_exit20_buffer`：`"<未设置>"` → `"if N<10 use 10% each and keep residual cash; if 10<=N<=20 use 1/N each"`
  - `parameters.allocation.monthly_replace`：`"<未设置>"` → `"10% per selected security; unused slots remain cash"`
  - `parameters.formal_cases`：`["PIT_EQUAL_WEIGHT","TOP10_MONTHLY_REPLACE","TOP10_EXIT20_BUFFER"]` → `["TOP10_REPLACE__NO_GATE","TOP10_BUFFER__NO_GATE","TOP10_REPLACE__M12_POS","TOP10_BUFFER__M12_POS","TOP10_REPLACE__M6_POS","TOP10_BUFFER__M6_POS","TOP10_REPLACE__M12_M6_POS","TOP1…`
  - `parameters.momentum.absolute_formula`：`"<未设置>"` → `"Close at prior calendar month-end / Close at the same calendar month-end 6 months earlier - 1"`
  - `parameters.momentum.absolute_lookback_months`：`"<未设置>"` → `6`
  - `parameters.momentum.absolute_name`：`"<未设置>"` → `"6-1 calendar-month total-return momentum"`
  - `parameters.momentum.endpoint_policy`：`"both endpoints must be real bars on the exact last XNYS session of their calendar month"` → `"every required endpoint must be a real bar on the exact last XNYS session of its calendar month"`
  - `parameters.momentum.formula`：`"Close at prior calendar month-end / Close at the same calendar month-end 12 months earlier - 1"` → `"<未设置>"`
  - `parameters.momentum.name`：`"12-1 calendar-month total-return momentum"` → `"<未设置>"`
  - `parameters.momentum.ranking_formula`：`"<未设置>"` → `"Close at prior calendar month-end / Close at the same calendar month-end 12 months earlier - 1"`
  - `parameters.momentum.ranking_name`：`"<未设置>"` → `"12-1 calendar-month total-return momentum"`
  - `parameters.primary_case`：`"TOP10_EXIT20_BUFFER"` → `"TOP10_BUFFER__M12_M6_POS"`
  - `parameters.primary_control_case`：`"<未设置>"` → `"TOP10_BUFFER__NO_GATE"`
  - `parameters.ranking.absolute_gate_overrides_buffer`：`"<未设置>"` → `true`
  - `parameters.ranking.missing_required_endpoint_is_ineligible`：`"<未设置>"` → `true`
  - `parameters.ranking.missing_score_is_ineligible`：`true` → `"<未设置>"`
  - `parameters.ranking.rank_within_gate_eligible_set`：`"<未设置>"` → `true`
  - `parameters.ranking.strict_positive_threshold`：`"<未设置>"` → `0.0`
  - `parameters.stress_windows.COVID_2020`：`"<未设置>"` → `["2020-01-02","2020-12-31"]`
  - `parameters.stress_windows.DOTCOM_2000_2002`：`"<未设置>"` → `["2000-01-03","2002-12-31"]`
  - ……另有 15 项，完整定义见父子 `experiment.json`。
