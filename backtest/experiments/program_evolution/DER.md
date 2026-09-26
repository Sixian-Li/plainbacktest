# DER · 短均线导数卖点 · 策略演化史

> 本文件由 `scripts.build_research_catalog` 自动生成，请勿手工修改。修改原因与策略变化只写入 `lineage.json`；参数差异来自父子 `experiment.json`，指标来自当前代表 run。

导航：[总策略演化史](../strategy_evolution.md) · [实验登记册](../index.md) · [研究谱系图](../research_map.html)

指标只用于还原研究轨迹，不把样本内结果升级为样本外证据。`completed_unvalidated` 会原样显示；确定性门禁失败和已清理的会话中断不成为研究节点，其精简历史由 `research_events.jsonl` 记录。

## DER · 短均线导数卖点

用短均线导数、长期趋势线与再入场规则研究单标的退出时机，并执行参数搜索、稳定性和跨标的检验。

### 起点 · DER-v0.10 · QQQ 短均线导数 OR 规则基线

- 策略：从 2021-01-04 Open 持有 100 股 QQQ 出发，持仓时由成本止损、短均线慢性转弱、短均线快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则把全部现金买回。所有技术阈值只用前一交易日及更早的已完成 Close 推导，再用下一交易日复权 OHLC 判断是否触及。
- 代表结果：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[experiment.json](../DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021/experiment.json)

### DER-v0.10 → DER-v0.20a.1 · QQQ 初始空仓与强制买回网格（2021–2026）

- 关系：`evolves_to`
- 为什么改：消除初始持仓带来的比较偏差，并检查卖出后的强制买回阈值是否能减少长期踏空。
- 策略修改：改为初始空仓和共同首买起点；快速转弱要求 SMA25/30/35 各自日变化均不高于 -0.20%，并扫描 R=0%～10% 强制买回。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021/experiment.json) · [子实验](../DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021/experiment.json)
- 自动配置差异：

  - `parameters.C_fast_derivative_mode`：`"<未设置>"` → `"all_short_smas"`
  - `parameters.C_fast_derivative_pct`：`-0.15` → `-0.2`
  - `parameters.R_forced_rebuy_pct`：`1.5` → `"<未设置>"`
  - `parameters.R_forced_rebuy_pct_grid`：`"<未设置>"` → `[0,1,2,3,4,5,6,7,8,9,10]`
  - `parameters.display_R_forced_rebuy_pct`：`"<未设置>"` → `5`
  - `parameters.initial_position`：`"<未设置>"` → `"flat"`
  - `parameters.initial_shares`：`100` → `"<未设置>"`
  - `strategy.buy_rule`：`"空仓时 OR：价格达到上次实际卖出价的 101.5%；前收盘位于前 SMA200 下方且当日价格向上穿越动态 SMA200；前收盘位于前短均线平均值下方 1% 且当日价格向上穿越该动态阈值。"` → `"初始空仓且没有上次卖价，因此仅允许动态 SMA200 上穿或短均线恢复产生第一笔买入；以后空仓时再额外允许价格达到上次实际卖出价的 1+R。"`
  - `strategy.description`：`"从 2021-01-04 Open 持有 100 股 QQQ 出发，持仓时由成本止损、短均线慢性转弱、短均线快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则把全部现金买回。所有技术阈值只用前一交易日及更早的已完成 Close 推导，再用下一交易日复权 OHLC 判断是否触及。"` → `"2021-01-04 起先持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。本实验只扫描强制买回幅度 R=0% 至 10%。"`
  - `strategy.initial_position`：`"2021-01-04 Open 以 305.07175 美元持有 100 股 QQQ；该种子建仓计作当日唯一成交。"` → `"2021-01-04 起空仓等待；第一笔正常买入成交前不计入策略与 Buy & Hold 的正式比较区间。"`
  - `strategy.name`：`"intraday_sma_or_guardrails"` → `"intraday_sma_or_flat_start_reentry_grid"`
  - `strategy.position_sizing`：`"long-only，全仓持有或全现金，允许小数股，不融资，现金不计息。"` → `"long-only，初始 100,000 美元现金；触发后全仓持有或全现金，允许小数股，不融资，现金不计息。"`
  - `strategy.sell_rule`：`"持仓时 OR：价格跌至本次成本的 98.5%；短均线平均变化率此前连续 6 日为负且当日仍为负并跌破动态 SMA130；此前连续 2 日为负且当日动态变化率达到 -0.15%；前收盘不低于前 SMA200 且当日价格向下穿越动态 SMA200。"` → `"持仓时 OR：成本价 -1.5%；短均线平均变化率此前连续 6 日为负且当日仍为负并跌破动态 SMA130；此前连续 2 日短均线平均变化率为负且当日动态 SMA25、SMA30、SMA35 各自变化率全部不高于 -0.20%；动态 SMA200 下穿。"`

### DER-v0.20a.1 → DER-v0.20a.2 · QQQ 强制买回网格早期窗口

- 关系：`tests_on_window`
- 为什么改：检查 2021 年以后观察到的 R 参数响应是否能在更早市场阶段重现。
- 策略修改：策略与 R 网格不变，只把观察窗口改为 2010-01-04～2015-06-01。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021/experiment.json) · [子实验](../DER/DER-v0.20a.2__26-08-13__qqq_intraday_sma_reentry_grid_2010_2015/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"2015-06-01"`
  - `parameters.analysis_start`：`"2021-01-04"` → `"2010-01-04"`
  - `parameters.requested_start`：`"<未设置>"` → `"2010-01-01"`
  - `strategy.description`：`"2021-01-04 起先持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。本实验只扫描强制买回幅度 R=0% 至 10%。"` → `"按用户随机指定的 2010-01-01 至 2015-06-01 区间复测同一策略；因 2010-01-01 休市，实际观察从 2010-01-04 开始。初始持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强…`
  - `strategy.initial_position`：`"2021-01-04 起空仓等待；第一笔正常买入成交前不计入策略与 Buy & Hold 的正式比较区间。"` → `"2010-01-04 起空仓等待；第一笔正常买入成交前不计入策略与 Buy & Hold 的正式比较区间。"`

### DER-v0.20a.1 → DER-v0.20a.3 · QQQ 强制买回网格全历史

- 关系：`tests_on_window`
- 为什么改：用 QQQ 全部批准历史检查参数漂移、长期回撤和各条规则的边际作用。
- 策略修改：窗口扩展到 1999-03-10～2026-08-04，并对每个 R 增加七条规则的逐条剔除诊断。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021/experiment.json) · [子实验](../DER/DER-v0.20a.3__26-08-13__qqq_intraday_sma_reentry_grid_full_history/experiment.json)
- 自动配置差异：

  - `parameters.analysis_start`：`"2021-01-04"` → `"1999-03-10"`
  - `parameters.requested_start`：`"<未设置>"` → `"1999-03-10"`
  - `parameters.single_rule_ablation.disabled_signals`：`"<未设置>"` → `["SELL_COST_STOP","SELL_SLOW_TREND","SELL_FAST_DROP","SELL_SMA200_CROSS","BUY_FORCED_REENTRY","BUY_SMA200_CROSS","BUY_SHORT_RECOVERY"]`
  - `parameters.single_rule_ablation.enabled`：`"<未设置>"` → `true`
  - `parameters.single_rule_ablation.metric_start`：`"<未设置>"` → `"Use the all-rules common first-entry date for every ablation; later entry caused by removing a buy rule remains cash and is included in performance."`
  - `parameters.single_rule_ablation.scope`：`"<未设置>"` → `"For every R=0% through 10%, compare all rules against disabling each of the seven buy/sell signals one at a time."`
  - `strategy.buy_rule`：`"初始空仓且没有上次卖价，因此仅允许动态 SMA200 上穿或短均线恢复产生第一笔买入；以后空仓时再额外允许价格达到上次实际卖出价的 1+R。"` → `"初始空仓且没有上次卖价，因此仅允许完成相应 SMA 预热后的动态 SMA200 上穿或短均线恢复产生第一笔买入；以后空仓时再额外允许价格达到上次实际卖出价的 1+R。"`
  - `strategy.description`：`"2021-01-04 起先持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。本实验只扫描强制买回幅度 R=0% 至 10%。"` → `"在本地已批准 QQQ 日线的完整覆盖区间 1999-03-10 至 2026-08-04 复测同一策略。初始持有现金；数据起点后的 SMA 预热期不产生信号，等待第一个普通买入信号后全仓持有 QQQ。持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SM…`
  - `strategy.initial_position`：`"2021-01-04 起空仓等待；第一笔正常买入成交前不计入策略与 Buy & Hold 的正式比较区间。"` → `"1999-03-10 起空仓等待；第一笔正常买入成交前不计入策略与 Buy & Hold 的正式比较区间。"`
  - `strategy.signal_time`：`"每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。"` → `"每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。指标尚未完成预热时不生成对应条件单。"`

### DER-v0.20a.3 → DER-v0.20b.1 · QQQ 三规则联合消融

- 关系：`ablates`
- 为什么改：验证逐条消融提示的规则能否同时删除，以及规则之间是否存在不可加总的交互。
- 策略修改：同时关闭强制买回、成本止损和慢性短均线转弱卖出；R 失效并停止扫描，其余买卖规则保持。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.20a.3__26-08-13__qqq_intraday_sma_reentry_grid_full_history/experiment.json) · [子实验](../DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history/experiment.json)
- 自动配置差异：

  - `parameters.R_forced_rebuy_pct`：`"<未设置>"` → `0.0`
  - `parameters.R_forced_rebuy_pct_grid`：`[0,1,2,3,4,5,6,7,8,9,10]` → `"<未设置>"`
  - `parameters.disabled_signals`：`"<未设置>"` → `["BUY_FORCED_REENTRY","SELL_COST_STOP","SELL_SLOW_TREND"]`
  - `parameters.display_R_forced_rebuy_pct`：`5` → `"<未设置>"`
  - `parameters.inactive_parameter_note`：`"<未设置>"` → `"A、B、L、R 仍保留在规格对象中以复用同一已测试引擎，但对应三条规则已关闭，因此不会影响成交。"`
  - `parameters.single_rule_ablation.disabled_signals`：`["SELL_COST_STOP","SELL_SLOW_TREND","SELL_FAST_DROP","SELL_SMA200_CROSS","BUY_FORCED_REENTRY","BUY_SMA200_CROSS","BUY_SHORT_RECOVERY"]` → `"<未设置>"`
  - `parameters.single_rule_ablation.enabled`：`true` → `"<未设置>"`
  - `parameters.single_rule_ablation.metric_start`：`"Use the all-rules common first-entry date for every ablation; later entry caused by removing a buy rule remains cash and is included in performance."` → `"<未设置>"`
  - `parameters.single_rule_ablation.scope`：`"For every R=0% through 10%, compare all rules against disabling each of the seven buy/sell signals one at a time."` → `"<未设置>"`
  - `strategy.buy_rule`：`"初始空仓且没有上次卖价，因此仅允许完成相应 SMA 预热后的动态 SMA200 上穿或短均线恢复产生第一笔买入；以后空仓时再额外允许价格达到上次实际卖出价的 1+R。"` → `"初始及以后空仓时 OR：动态 SMA200 上穿；价格上穿动态 SMA25/30/35 平均值下方 1%。明确禁用卖出价 +R% 强制买回。"`
  - `strategy.description`：`"在本地已批准 QQQ 日线的完整覆盖区间 1999-03-10 至 2026-08-04 复测同一策略。初始持有现金；数据起点后的 SMA 预热期不产生信号，等待第一个普通买入信号后全仓持有 QQQ。持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SM…` → `"在完整 QQQ 批准历史上同时删除卖出价上方强制买回、成本线强止损、短均线连续转弱且跌破 SMA130 三条规则。初始空仓，等待 SMA200 上穿或短均线恢复首次触发后全仓买入；持仓时只保留三条短均线同时快速下跌或 SMA200 下穿卖出；空仓时只保留 SMA200 上穿或短均线恢复买入。"`
  - `strategy.name`：`"intraday_sma_or_flat_start_reentry_grid"` → `"intraday_sma_three_rule_ablation_flat_start"`
  - `strategy.sell_rule`：`"持仓时 OR：成本价 -1.5%；短均线平均变化率此前连续 6 日为负且当日仍为负并跌破动态 SMA130；此前连续 2 日短均线平均变化率为负且当日动态 SMA25、SMA30、SMA35 各自变化率全部不高于 -0.20%；动态 SMA200 下穿。"` → `"持仓时 OR：此前连续 2 日短均线平均变化率为负且当日动态 SMA25、SMA30、SMA35 各自变化率全部不高于 -0.20%；动态 SMA200 下穿。明确禁用成本线 -1.5% 强止损和短均线连续转弱且跌破 SMA130。"`

### DER-v0.20a.3 → DER-v0.30 · QQQ A–H/L/R 全局搜索

- 关系：`parameter_search_of`
- 为什么改：R 单参数网格未形成稳定结论，因此检验完整 A–H/L/R 规则族是否至少具有样本内超越持有的容量。
- 策略修改：恢复全部规则，把搜索扩展到 A、B、C、D、E、F、G、H、L、R，执行 50,000 组全域加 30,000 组前沿精搜。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.20a.3__26-08-13__qqq_intraday_sma_reentry_grid_full_history/experiment.json) · [子实验](../DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history/experiment.json)
- 自动配置差异：

  - `parameters.A_negative_days_slow`：`7` → `"<未设置>"`
  - `parameters.B_slow_sma_window`：`130` → `"<未设置>"`
  - `parameters.C_fast_derivative_pct`：`-0.2` → `"<未设置>"`
  - `parameters.D_negative_days_fast`：`3` → `"<未设置>"`
  - `parameters.E_fallback_sma_window`：`200` → `"<未设置>"`
  - `parameters.F_short_sma_center`：`30` → `"<未设置>"`
  - `parameters.F_short_sma_spacing`：`5` → `"<未设置>"`
  - `parameters.F_short_sma_windows`：`[25,30,35]` → `"<未设置>"`
  - `parameters.G_short_recovery_below_pct`：`1.0` → `"<未设置>"`
  - `parameters.H_reentry_sma_window`：`200` → `"<未设置>"`
  - `parameters.L_cost_stop_pct`：`1.5` → `"<未设置>"`
  - `parameters.R_forced_rebuy_pct_grid`：`[0,1,2,3,4,5,6,7,8,9,10]` → `"<未设置>"`
  - `parameters.anchors`：`"<未设置>"` → `[{"A_negative_days_slow":7,"B_slow_sma_window":130,"C_fast_derivative_pct":-0.2,"D_negative_days_fast":3,"E_fallback_sma_window":200,"F_short_sma_center":30,"F_short_sma_spacing":…`
  - `parameters.display_R_forced_rebuy_pct`：`5` → `"<未设置>"`
  - `parameters.formal_candidate_count_per_objective`：`"<未设置>"` → `3`
  - `parameters.frontier_parent_count_per_objective`：`"<未设置>"` → `50`
  - `parameters.global_sample_count`：`"<未设置>"` → `50000`
  - `parameters.random_seed`：`"<未设置>"` → `20260813`
  - `parameters.refined_sample_count`：`"<未设置>"` → `30000`
  - `parameters.requested_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.search_method`：`"<未设置>"` → `"seeded_discrete_global_sampling_then_frontier_mutation"`
  - `parameters.search_space.A_negative_days_slow`：`"<未设置>"` → `[3,4,5,6,7,8,9,10,11,12]`
  - `parameters.search_space.B_slow_sma_window`：`"<未设置>"` → `[80,90,100,110,120,130,140,150,160,170,180,190,200,210,220]`
  - `parameters.search_space.C_fast_derivative_pct`：`"<未设置>"` → `[-0.05,-0.1,-0.15,-0.2,-0.25,-0.3,-0.35,-0.4,-0.45,-0.5,-0.55,-0.6]`
  - ……另有 20 项，完整定义见父子 `experiment.json`。

### DER-v0.30 → DER-v0.30a.1 · QQQ 参数跨窗口稳定性

- 关系：`robustness_check_of`
- 为什么改：全历史最高点可能过拟合且触及边界，需要检查邻域参数在不同市场窗口的排名稳定性。
- 策略修改：围绕全局赢家建立局部空间，在 1999–2014、2005–2020、2010–2026 三个窗口搜索并交叉移植代表参数。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history/experiment.json) · [子实验](../DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.anchors`：`[{"A_negative_days_slow":7,"B_slow_sma_window":130,"C_fast_derivative_pct":-0.2,"D_negative_days_fast":3,"E_fallback_sma_window":200,"F_short_sma_center":30,"F_short_sma_spacing":…` → `[{"A_negative_days_slow":3,"B_slow_sma_window":190,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":290,"F_short_sma_center":80,"F_short_sma_spacing"…`
  - `parameters.common_sample_count`：`"<未设置>"` → `40000`
  - `parameters.forced_reentry_modes`：`"<未设置>"` → `[false,true]`
  - `parameters.formal_candidate_count_per_objective`：`3` → `"<未设置>"`
  - `parameters.frontier_parent_count_per_objective`：`50` → `40`
  - `parameters.global_sample_count`：`50000` → `"<未设置>"`
  - `parameters.refined_sample_count`：`30000` → `"<未设置>"`
  - `parameters.refined_sample_count_per_window`：`"<未设置>"` → `15000`
  - `parameters.search_method`：`"seeded_discrete_global_sampling_then_frontier_mutation"` → `"shared_seeded_local_sample_then_each_window_frontier_mutation_then_shared_union_rescore"`
  - `parameters.search_space.A_negative_days_slow`：`[3,4,5,6,7,8,9,10,11,12]` → `[2,3,4,5,6]`
  - `parameters.search_space.B_slow_sma_window`：`[80,90,100,110,120,130,140,150,160,170,180,190,200,210,220]` → `[150,160,170,180,190,200,210,220,230]`
  - `parameters.search_space.C_fast_derivative_pct`：`[-0.05,-0.1,-0.15,-0.2,-0.25,-0.3,-0.35,-0.4,-0.45,-0.5,-0.55,-0.6]` → `[-0.15,-0.2,-0.25,-0.3,-0.35,-0.4]`
  - `parameters.search_space.E_fallback_sma_window`：`[100,110,120,130,140,150,160,170,180,190,200,210,220,230,240,250,260,270,280,290,300]` → `[240,250,260,270,280,290,300,310,320,330,340,350,360]`
  - `parameters.search_space.F_short_sma_center`：`[15,20,25,30,35,40,45,50,55,60,65,70,75,80]` → `[60,65,70,75,80,85,90,95,100,105,110,115,120]`
  - `parameters.search_space.F_short_sma_spacing`：`[5,10]` → `[5,10,15,20]`
  - `parameters.search_space.G_short_recovery_below_pct`：`[0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0]` → `[0,0.25,0.5,0.75,1,1.25,1.5,1.75,2,2.25,2.5]`
  - `parameters.search_space.H_reentry_sma_window`：`[100,110,120,130,140,150,160,170,180,190,200,210,220,230,240,250,260,270,280,290,300]` → `[220,230,240,250,260,270,280,290,300,310,320,330,340]`
  - `parameters.search_space.L_cost_stop_pct`：`[0.5,1.0,1.5,2.0,2.5,3.0,3.5,4.0,4.5,5.0,5.5,6.0,6.5,7.0,7.5,8.0,8.5,9.0,9.5,10.0]` → `[5,5.5,6,6.5,7,7.5,8,8.5,9,9.5,10,10.5,11,11.5,12]`
  - `parameters.search_space.R_forced_rebuy_pct`：`[0.0,0.5,1.0,1.5,2.0,2.5,3.0,3.5,4.0,4.5,5.0,5.5,6.0,6.5,7.0,7.5,8.0,8.5,9.0,9.5,10.0]` → `[0,0.5,1,1.5,2,2.5,3]`
  - `parameters.top_fraction_for_stability`：`"<未设置>"` → `0.01`
  - `parameters.windows`：`"<未设置>"` → `[{"analysis_end":"2014-12-31","analysis_start":"1999-03-10","warmup":"QQQ inception; no pre-listing history exists","window_id":"W1999_2014"},{"analysis_end":"2020-12-31","analysi…`
  - `strategy.buy_rule`：`"初始空仓时由动态 H 日 SMA 上穿或价格上穿动态 F 三短均线平均值下方 G% 中任一规则全仓买入；已有卖出价后，再加入卖出成交价上方 R% 强制买回。"` → `"初始空仓时由动态 H 日 SMA 上穿或价格上穿动态 F 三短均线平均值下方 G% 中任一规则全仓买入；强制买回模式开启且已有卖出价时，再加入卖出成交价上方 R% 买回，关闭时该规则完全不存在。"`
  - ……另有 5 项，完整定义见父子 `experiment.json`。

### DER-v0.30a.1 → DER-v0.30b.1 · QQQ 双时期 OAT 敏感性

- 关系：`informs`
- 为什么改：窗口实验显示精确赢家仍漂移，需要分离每个参数在不重叠时期的敏感度。
- 策略修改：冻结共同基线，在 1999–2009 与 2010–2026 两段对十二组参数执行一次只改变一个维度的 OAT 扫描。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability/experiment.json) · [子实验](../DER/DER-v0.30b.1__26-08-13__qqq_intraday_sma_oat_sensitivity_two_periods/experiment.json)
- 自动配置差异：

  - `parameters.anchors`：`[{"A_negative_days_slow":3,"B_slow_sma_window":190,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":290,"F_short_sma_center":80,"F_short_sma_spacing"…` → `"<未设置>"`
  - `parameters.baseline.A_negative_days_slow`：`"<未设置>"` → `3`
  - `parameters.baseline.B_slow_sma_window`：`"<未设置>"` → `190`
  - `parameters.baseline.C_fast_derivative_pct`：`"<未设置>"` → `-0.25`
  - `parameters.baseline.D_negative_days_fast`：`"<未设置>"` → `4`
  - `parameters.baseline.E_fallback_sma_window`：`"<未设置>"` → `290`
  - `parameters.baseline.F_short_sma_center`：`"<未设置>"` → `80`
  - `parameters.baseline.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.baseline.G_short_recovery_below_pct`：`"<未设置>"` → `1.0`
  - `parameters.baseline.H_reentry_sma_window`：`"<未设置>"` → `270`
  - `parameters.baseline.L_cost_stop_pct`：`"<未设置>"` → `8.5`
  - `parameters.baseline.R_forced_rebuy_pct`：`"<未设置>"` → `0.0`
  - `parameters.baseline.forced_reentry_enabled`：`"<未设置>"` → `true`
  - `parameters.common_sample_count`：`40000` → `"<未设置>"`
  - `parameters.evaluation_start`：`"<未设置>"` → `"analysis_start"`
  - `parameters.forced_reentry_modes`：`[false,true]` → `"<未设置>"`
  - `parameters.frontier_parent_count_per_objective`：`40` → `"<未设置>"`
  - `parameters.random_seed`：`20260813` → `"<未设置>"`
  - `parameters.refined_sample_count_per_window`：`15000` → `"<未设置>"`
  - `parameters.search_method`：`"shared_seeded_local_sample_then_each_window_frontier_mutation_then_shared_union_rescore"` → `"one_parameter_at_a_time_around_frozen_baseline"`
  - `parameters.search_space.A_negative_days_slow`：`[2,3,4,5,6]` → `"<未设置>"`
  - `parameters.search_space.B_slow_sma_window`：`[150,160,170,180,190,200,210,220,230]` → `"<未设置>"`
  - `parameters.search_space.C_fast_derivative_pct`：`[-0.15,-0.2,-0.25,-0.3,-0.35,-0.4]` → `"<未设置>"`
  - `parameters.search_space.D_negative_days_fast`：`[2,3,4,5,6]` → `"<未设置>"`
  - ……另有 20 项，完整定义见父子 `experiment.json`。

### DER-v0.30b.1 → DER-v0.40 · QQQ 最终固定参数历史评估

- 关系：`informs`
- 为什么改：综合全局搜索、跨窗口排名和 OAT 敏感性后，冻结一组可解释参数作为后续稳健性基线。
- 策略修改：固定 A=3、B=175、C=-0.25%、D=4、E=305、F=70/80/90、G=1%、H=270、L=10%、R=0%，停止搜索并运行完整历史。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.30b.1__26-08-13__qqq_intraday_sma_oat_sensitivity_two_periods/experiment.json) · [子实验](../DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history/experiment.json)
- 自动配置差异：

  - `parameters.A_negative_days_slow`：`"<未设置>"` → `3`
  - `parameters.B_slow_sma_window`：`"<未设置>"` → `175`
  - `parameters.C_fast_derivative_pct`：`"<未设置>"` → `-0.25`
  - `parameters.D_negative_days_fast`：`"<未设置>"` → `4`
  - `parameters.E_fallback_sma_window`：`"<未设置>"` → `305`
  - `parameters.F_short_sma_center`：`"<未设置>"` → `80`
  - `parameters.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.F_short_sma_windows`：`"<未设置>"` → `[70,80,90]`
  - `parameters.G_short_recovery_below_pct`：`"<未设置>"` → `1.0`
  - `parameters.H_reentry_sma_window`：`"<未设置>"` → `270`
  - `parameters.L_cost_stop_pct`：`"<未设置>"` → `10.0`
  - `parameters.R_forced_rebuy_pct`：`"<未设置>"` → `0.0`
  - `parameters.analysis_end`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.analysis_start`：`"<未设置>"` → `"1999-03-10"`
  - `parameters.baseline.A_negative_days_slow`：`3` → `"<未设置>"`
  - `parameters.baseline.B_slow_sma_window`：`190` → `"<未设置>"`
  - `parameters.baseline.C_fast_derivative_pct`：`-0.25` → `"<未设置>"`
  - `parameters.baseline.D_negative_days_fast`：`4` → `"<未设置>"`
  - `parameters.baseline.E_fallback_sma_window`：`290` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_center`：`80` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_spacing`：`10` → `"<未设置>"`
  - `parameters.baseline.G_short_recovery_below_pct`：`1.0` → `"<未设置>"`
  - `parameters.baseline.H_reentry_sma_window`：`270` → `"<未设置>"`
  - `parameters.baseline.L_cost_stop_pct`：`8.5` → `"<未设置>"`
  - ……另有 17 项，完整定义见父子 `experiment.json`。

### DER-v0.40 → DER-v0.40a.1 · QQQ 最终参数局部高原

- 关系：`robustness_check_of`
- 为什么改：固定参数的单点表现不足以证明稳健，需要确认基线周围是否存在连续高原或窄峰。
- 策略修改：保持策略语义和完整历史不变，围绕最终基线执行 235 个单参数扰动并检查预声明 CAGR/Sharpe 门槛。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history/experiment.json) · [子实验](../DER/DER-v0.40a.1__26-08-14__qqq_intraday_sma_final_local_plateau_full_history/experiment.json)
- 自动配置差异：

  - `parameters.A_negative_days_slow`：`3` → `"<未设置>"`
  - `parameters.B_slow_sma_window`：`175` → `"<未设置>"`
  - `parameters.C_fast_derivative_pct`：`-0.25` → `"<未设置>"`
  - `parameters.D_negative_days_fast`：`4` → `"<未设置>"`
  - `parameters.E_fallback_sma_window`：`305` → `"<未设置>"`
  - `parameters.F_short_sma_center`：`80` → `"<未设置>"`
  - `parameters.F_short_sma_spacing`：`10` → `"<未设置>"`
  - `parameters.F_short_sma_windows`：`[70,80,90]` → `"<未设置>"`
  - `parameters.G_short_recovery_below_pct`：`1.0` → `"<未设置>"`
  - `parameters.H_reentry_sma_window`：`270` → `"<未设置>"`
  - `parameters.L_cost_stop_pct`：`10.0` → `"<未设置>"`
  - `parameters.R_forced_rebuy_pct`：`0.0` → `"<未设置>"`
  - `parameters.analysis_end`：`"2026-08-04"` → `"<未设置>"`
  - `parameters.analysis_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.baseline.A_negative_days_slow`：`"<未设置>"` → `3`
  - `parameters.baseline.B_slow_sma_window`：`"<未设置>"` → `175`
  - `parameters.baseline.C_fast_derivative_pct`：`"<未设置>"` → `-0.25`
  - `parameters.baseline.D_negative_days_fast`：`"<未设置>"` → `4`
  - `parameters.baseline.E_fallback_sma_window`：`"<未设置>"` → `305`
  - `parameters.baseline.F_short_sma_center`：`"<未设置>"` → `80`
  - `parameters.baseline.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.baseline.G_short_recovery_below_pct`：`"<未设置>"` → `1.0`
  - `parameters.baseline.H_reentry_sma_window`：`"<未设置>"` → `270`
  - `parameters.baseline.L_cost_stop_pct`：`"<未设置>"` → `10.0`
  - ……另有 26 项，完整定义见父子 `experiment.json`。

### DER-v0.40a.1 → DER-v0.41 · QQQ 重定基线局部高原

- 关系：`evolves_to`
- 为什么改：首轮局部检查显示部分维度存在更平滑的邻近区域，需要把基线移到高原内部并加密 H。
- 策略修改：基线调整为 B=176、E=301、G=0.95%、H=264，其余保持；新增 H=200～330、步长 1 的密集扫描。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.40a.1__26-08-14__qqq_intraday_sma_final_local_plateau_full_history/experiment.json) · [子实验](../DER/DER-v0.41__26-08-14__qqq_intraday_sma_recentered_local_plateau_full_history/experiment.json)
- 自动配置差异：

  - `parameters.baseline.B_slow_sma_window`：`175` → `176`
  - `parameters.baseline.E_fallback_sma_window`：`305` → `301`
  - `parameters.baseline.G_short_recovery_below_pct`：`1.0` → `0.95`
  - `parameters.baseline.H_reentry_sma_window`：`270` → `264`
  - `parameters.search_method`：`"one_parameter_at_a_time_around_user_frozen_final_baseline"` → `"one_parameter_at_a_time_around_user_recentered_baseline"`
  - `parameters.sweep_point_count`：`240` → `350`
  - `parameters.sweeps`：`[{"label":"B · 慢速卖出 SMA","parameter":"B_slow_sma_window","start":165,"step":1,"stop":185,"sweep_id":"B"},{"label":"E · 兜底卖出 SMA","parameter":"E_fallback_sma_window","start":285,"s…` → `[{"label":"B · 慢速卖出 SMA","parameter":"B_slow_sma_window","start":165,"step":1,"stop":185,"sweep_id":"B"},{"label":"E · 兜底卖出 SMA","parameter":"E_fallback_sma_window","start":285,"s…`
  - `parameters.unique_case_count`：`235` → `345`
  - `strategy.description`：`"以用户冻结的最终 QQQ 参数 A=3、B=175、C=-0.25%、D=4、E=305、F=80±10、G=1%、H=270、L=10%、R=0% 且开启强制买回为基线，在完整批准历史上每次只扰动 B、E、F 中心、G、H 或 L 的一个维度，用 CAGR 与 Sharpe 的局部响应判断该参数向量附近是宽高原还是孤立尖峰。"` → `"在上一轮最终参数局部高原检验后，将整套 QQQ 策略基线更新为 B=176、E=301、G=0.95%、H=264，保持 A=3、C=-0.25%、D=4、F=80±10、L=10%、R=0% 与强制买回开启不变；在完整批准历史中每次只扰动 B、E、F 中心、G、H 或 L 的一个维度，重新检查新联合基线附近的 CAGR 与 Sharpe 高原。"`
  - `strategy.name`：`"intraday_sma_final_local_plateau_full_history"` → `"intraday_sma_recentered_local_plateau_full_history"`

### DER-v0.41 → DER-v0.41a.1 · QQQ 高原参数迁移到 SPY

- 关系：`cross_asset_test_of`
- 为什么改：检查在 QQQ 上重定到局部高原的参数能否不经调参迁移到另一宽基指数。
- 策略修改：原样冻结重定基线，标的改为 SPY，评估窗口改为 2000–2020；2000 年前数据只用于预热。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.41__26-08-14__qqq_intraday_sma_recentered_local_plateau_full_history/experiment.json) · [子实验](../DER/DER-v0.41a.1__26-08-14__spy_intraday_sma_recentered_fixed_2000_2020/experiment.json)
- 自动配置差异：

  - `parameters.A_negative_days_slow`：`"<未设置>"` → `3`
  - `parameters.B_slow_sma_window`：`"<未设置>"` → `176`
  - `parameters.C_fast_derivative_pct`：`"<未设置>"` → `-0.25`
  - `parameters.D_negative_days_fast`：`"<未设置>"` → `4`
  - `parameters.E_fallback_sma_window`：`"<未设置>"` → `301`
  - `parameters.F_short_sma_center`：`"<未设置>"` → `80`
  - `parameters.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.F_short_sma_windows`：`"<未设置>"` → `[70,80,90]`
  - `parameters.G_short_recovery_below_pct`：`"<未设置>"` → `0.95`
  - `parameters.H_reentry_sma_window`：`"<未设置>"` → `264`
  - `parameters.L_cost_stop_pct`：`"<未设置>"` → `10.0`
  - `parameters.R_forced_rebuy_pct`：`"<未设置>"` → `0.0`
  - `parameters.analysis_end`：`"<未设置>"` → `"2020-12-31"`
  - `parameters.analysis_start`：`"<未设置>"` → `"2000-01-03"`
  - `parameters.baseline.A_negative_days_slow`：`3` → `"<未设置>"`
  - `parameters.baseline.B_slow_sma_window`：`176` → `"<未设置>"`
  - `parameters.baseline.C_fast_derivative_pct`：`-0.25` → `"<未设置>"`
  - `parameters.baseline.D_negative_days_fast`：`4` → `"<未设置>"`
  - `parameters.baseline.E_fallback_sma_window`：`301` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_center`：`80` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_spacing`：`10` → `"<未设置>"`
  - `parameters.baseline.G_short_recovery_below_pct`：`0.95` → `"<未设置>"`
  - `parameters.baseline.H_reentry_sma_window`：`264` → `"<未设置>"`
  - `parameters.baseline.L_cost_stop_pct`：`10.0` → `"<未设置>"`
  - ……另有 28 项，完整定义见父子 `experiment.json`。

### DER-v0.30 → DER-v0.50a.1 · SPY 宽域训练（1993–2002）

- 关系：`branches_from`
- 为什么改：QQQ 参数直接迁移不足以回答 SPY 自身的稳健参数问题，因此建立独立训练分支。
- 策略修改：标的改为 SPY，仅用 1993–2002 宽范围细步长搜索，以十一维一步邻域最差 CAGR/Sharpe 的 maximin 选择代表，封存 2003 年以后。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history/experiment.json) · [子实验](../DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2026-08-04"` → `"2002-12-31"`
  - `parameters.analysis_start`：`"1999-03-10"` → `"1993-01-29"`
  - `parameters.anchors`：`[{"A_negative_days_slow":7,"B_slow_sma_window":130,"C_fast_derivative_pct":-0.2,"D_negative_days_fast":3,"E_fallback_sma_window":200,"F_short_sma_center":30,"F_short_sma_spacing":…` → `[{"A_negative_days_slow":3,"B_slow_sma_window":176,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":302,"F_short_sma_center":80,"F_short_sma_spacing"…`
  - `parameters.evaluation_start`：`"<未设置>"` → `"analysis_start"`
  - `parameters.frontier_parent_count_per_objective`：`50` → `100`
  - `parameters.global_sample_count`：`50000` → `100000`
  - `parameters.held_out_access`：`"<未设置>"` → `"forbidden_in_this_experiment"`
  - `parameters.held_out_end_requested`：`"<未设置>"` → `"2023-12-31"`
  - `parameters.held_out_start`：`"<未设置>"` → `"2003-01-02"`
  - `parameters.oat_diagnostic.baseline`：`"<未设置>"` → `"selected stable representative"`
  - `parameters.oat_diagnostic.plateau_cagr_tolerance_pct_points`：`"<未设置>"` → `0.5`
  - `parameters.oat_diagnostic.plateau_sharpe_tolerance`：`"<未设置>"` → `0.03`
  - `parameters.oat_diagnostic.sweeps`：`"<未设置>"` → `"all 11 complete declared parameter grids, changing one dimension at a time"`
  - `parameters.random_seed`：`20260813` → `20260814`
  - `parameters.refined_sample_count`：`30000` → `100000`
  - `parameters.requested_train_start`：`"<未设置>"` → `"1993-01-01"`
  - `parameters.search_method`：`"seeded_discrete_global_sampling_then_frontier_mutation"` → `"seeded_broad_discrete_sampling_frontier_mutation_then_complete_two_sided_worst_neighbor_maximin"`
  - `parameters.search_space.A_negative_days_slow`：`[3,4,5,6,7,8,9,10,11,12]` → `"<未设置>"`
  - `parameters.search_space.B_slow_sma_window`：`[80,90,100,110,120,130,140,150,160,170,180,190,200,210,220]` → `"<未设置>"`
  - `parameters.search_space.C_fast_derivative_pct`：`[-0.05,-0.1,-0.15,-0.2,-0.25,-0.3,-0.35,-0.4,-0.45,-0.5,-0.55,-0.6]` → `"<未设置>"`
  - `parameters.search_space.D_negative_days_fast`：`[2,3,4,5,6]` → `"<未设置>"`
  - `parameters.search_space.E_fallback_sma_window`：`[100,110,120,130,140,150,160,170,180,190,200,210,220,230,240,250,260,270,280,290,300]` → `"<未设置>"`
  - `parameters.search_space.F_short_sma_center`：`[15,20,25,30,35,40,45,50,55,60,65,70,75,80]` → `"<未设置>"`
  - `parameters.search_space.F_short_sma_spacing`：`[5,10]` → `"<未设置>"`
  - ……另有 51 项，完整定义见父子 `experiment.json`。

### DER-v0.50a.1 → DER-v0.50a.2 · SPY 锁定窗口测试（2003–2013）

- 关系：`locked_test_of`
- 为什么改：训练期选出的参数必须在未参与选择的后续窗口接受锁定检验。
- 策略修改：冻结 1993–2002 训练代表，不再搜索，在 SPY 2003–2013 独立窗口运行固定参数测试。
- 修改前：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json) · [子实验](../DER/DER-v0.50a.2__26-08-14__spy_intraday_sma_trained_fixed_2003_2013/experiment.json)
- 自动配置差异：

  - `parameters.A_negative_days_slow`：`"<未设置>"` → `7`
  - `parameters.B_slow_sma_window`：`"<未设置>"` → `150`
  - `parameters.C_fast_derivative_pct`：`"<未设置>"` → `-0.55`
  - `parameters.D_negative_days_fast`：`"<未设置>"` → `9`
  - `parameters.E_fallback_sma_window`：`"<未设置>"` → `152`
  - `parameters.F_short_sma_center`：`"<未设置>"` → `22`
  - `parameters.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.F_short_sma_windows`：`"<未设置>"` → `[12,22,32]`
  - `parameters.G_short_recovery_below_pct`：`"<未设置>"` → `4.4`
  - `parameters.H_reentry_sma_window`：`"<未设置>"` → `156`
  - `parameters.L_cost_stop_pct`：`"<未设置>"` → `19.25`
  - `parameters.R_forced_rebuy_pct`：`"<未设置>"` → `9.25`
  - `parameters.analysis_end`：`"2002-12-31"` → `"2013-12-31"`
  - `parameters.analysis_start`：`"1993-01-29"` → `"2003-01-02"`
  - `parameters.anchors`：`[{"A_negative_days_slow":3,"B_slow_sma_window":176,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":302,"F_short_sma_center":80,"F_short_sma_spacing"…` → `"<未设置>"`
  - `parameters.forced_reentry_enabled`：`"<未设置>"` → `true`
  - `parameters.formal_candidate_count_per_objective`：`3` → `"<未设置>"`
  - `parameters.frontier_parent_count_per_objective`：`100` → `"<未设置>"`
  - `parameters.global_sample_count`：`100000` → `"<未设置>"`
  - `parameters.held_out_access`：`"forbidden_in_this_experiment"` → `"<未设置>"`
  - `parameters.held_out_end_requested`：`"2023-12-31"` → `"<未设置>"`
  - `parameters.held_out_start`：`"2003-01-02"` → `"<未设置>"`
  - `parameters.oat_diagnostic.baseline`：`"selected stable representative"` → `"<未设置>"`
  - `parameters.oat_diagnostic.plateau_cagr_tolerance_pct_points`：`0.5` → `"<未设置>"`
  - ……另有 56 项，完整定义见父子 `experiment.json`。

### DER-v0.50a.1 → DER-v0.50b.1 · SPY 扩展训练（1993–2020）

- 关系：`extends_training_window`
- 为什么改：建立一个使用更长历史的平行训练分支，比较训练样本长度对稳健代表和窄峰问题的影响。
- 策略修改：沿用相同宽域搜索和邻域选择协议，把训练窗口扩展到 1993–2020，并把 2021 年以后保留为未见期。
- 修改前：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：SPY；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json) · [子实验](../DER/DER-v0.50b.1__26-08-14__spy_intraday_sma_training_1993_2020/experiment.json)
- 自动配置差异：

  - `parameters.analysis_end`：`"2002-12-31"` → `"2020-12-31"`
  - `parameters.anchors`：`[{"A_negative_days_slow":3,"B_slow_sma_window":176,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":302,"F_short_sma_center":80,"F_short_sma_spacing"…` → `[{"A_negative_days_slow":3,"B_slow_sma_window":176,"C_fast_derivative_pct":-0.25,"D_negative_days_fast":4,"E_fallback_sma_window":302,"F_short_sma_center":80,"F_short_sma_spacing"…`
  - `parameters.held_out_end_available`：`"<未设置>"` → `"2026-08-04"`
  - `parameters.held_out_end_requested`：`"2023-12-31"` → `"<未设置>"`
  - `parameters.held_out_start`：`"2003-01-02"` → `"2021-01-04"`
  - `strategy.description`：`"只用 SPY 上市日至 2002 年末训练此前冻结的日内动态 SMA 规则族。保持四类 OR 卖出、三类 OR 买入、初始空仓和盘前解阈值语义不变；在 A–H/L/R 的宽范围细离散网格上固定种子全域抽样并前沿精搜，排除缺少任一侧相邻点的边界中心，再按每个候选相邻一步扰动后的最差 CAGR 与最差 Sharpe 选择双侧稳健代表。2003 年起数据完全不…` → `"把此前 SPY 宽范围细步长训练协议原样扩展到 1993-01-29～2020-12-31。保持四类 OR 卖出、三类 OR 买入、初始空仓和盘前解阈值语义不变；在同一 A–H/L/R 离散空间中固定种子全域抽样并前沿精搜，排除缺少任一侧相邻点的边界中心，再按每个候选相邻一步扰动后的最差 CAGR 与最差 Sharpe 选择双侧稳健代表。2021 年起数…`
  - `strategy.name`：`"intraday_sma_broad_stable_training_search"` → `"intraday_sma_broad_stable_training_search_1993_2020"`

### DER-v0.41 → DER-v0.60 · QQQ 移除 C/D 后联合高原训练与锁定测试

- 关系：`simplifies_and_retrains`
- 为什么改：后续核查显示 C/D 快速导数卖出没有独立主导成交或边际绩效，继续保留只会增加自由度；同时此前 QQQ 参数使用全历史反复选择，缺少真正封存的后期检验。
- 策略修改：完全关闭 SELL_FAST_DROP 并移除 C/D 自由参数；把有效参数扩展为 A、B、E、F 中心/间隔、G、H、L、R 的宽域搜索，只用 1999–2015 输入历史选择九维联合高原代表，再锁定到 2016–2026 一次性样本外回测。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.41__26-08-14__qqq_intraday_sma_recentered_local_plateau_full_history/experiment.json) · [子实验](../DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos/experiment.json)
- 自动配置差异：

  - `parameters.C_fast_derivative_mode`：`"all_short_smas"` → `"<未设置>"`
  - `parameters.baseline.A_negative_days_slow`：`3` → `"<未设置>"`
  - `parameters.baseline.B_slow_sma_window`：`176` → `"<未设置>"`
  - `parameters.baseline.C_fast_derivative_pct`：`-0.25` → `"<未设置>"`
  - `parameters.baseline.D_negative_days_fast`：`4` → `"<未设置>"`
  - `parameters.baseline.E_fallback_sma_window`：`301` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_center`：`80` → `"<未设置>"`
  - `parameters.baseline.F_short_sma_spacing`：`10` → `"<未设置>"`
  - `parameters.baseline.G_short_recovery_below_pct`：`0.95` → `"<未设置>"`
  - `parameters.baseline.H_reentry_sma_window`：`264` → `"<未设置>"`
  - `parameters.baseline.L_cost_stop_pct`：`10.0` → `"<未设置>"`
  - `parameters.baseline.R_forced_rebuy_pct`：`0.0` → `"<未设置>"`
  - `parameters.baseline.forced_reentry_enabled`：`true` → `"<未设置>"`
  - `parameters.fast_drop_enabled`：`"<未设置>"` → `false`
  - `parameters.fixed_parameters.A_negative_days_slow`：`3` → `"<未设置>"`
  - `parameters.fixed_parameters.C_fast_derivative_pct`：`-0.25` → `"<未设置>"`
  - `parameters.fixed_parameters.D_negative_days_fast`：`4` → `"<未设置>"`
  - `parameters.fixed_parameters.F_short_sma_spacing`：`10` → `"<未设置>"`
  - `parameters.fixed_parameters.R_forced_rebuy_pct`：`0.0` → `"<未设置>"`
  - `parameters.fixed_parameters.forced_reentry_enabled`：`true` → `"<未设置>"`
  - `parameters.forced_reentry_enabled`：`"<未设置>"` → `true`
  - `parameters.formal_training_candidates`：`"<未设置>"` → `["joint_plateau_representative","maximum_cagr","maximum_sharpe"]`
  - `parameters.global_sample_count`：`"<未设置>"` → `100000`
  - `parameters.indicator_history_start`：`"<未设置>"` → `"1999-03-10"`
  - ……另有 70 项，完整定义见父子 `experiment.json`。

### DER-v0.60 → DER-v0.60a.1 · QQQ 无 C/D 基线双窗口单参数高原诊断

- 关系：`robustness_check_of`
- 为什么改：DER-v0.60 的平台代表使用包含互联网泡沫时期的 1999–2015 历史选出，需要检查其局部形状是否由 1999–2004 特定行情驱动。
- 策略修改：策略语义和冻结参数不变；对九个有效维度分别在原 2000-07-27～2015-12-31 评价窗口与 2005-01-03～2015-12-31 子窗口执行 OAT 扰动，不重新优化或采用极值。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos/experiment.json) · [子实验](../DER/DER-v0.60a.1__26-08-15__qqq_no_fast_drop_oat_two_windows/experiment.json)
- 自动配置差异：

  - `parameters.baseline.A_negative_days_slow`：`"<未设置>"` → `8`
  - `parameters.baseline.B_slow_sma_window`：`"<未设置>"` → `178`
  - `parameters.baseline.C_fast_derivative_pct`：`"<未设置>"` → `-0.1`
  - `parameters.baseline.D_negative_days_fast`：`"<未设置>"` → `3`
  - `parameters.baseline.E_fallback_sma_window`：`"<未设置>"` → `187`
  - `parameters.baseline.F_short_sma_center`：`"<未设置>"` → `151`
  - `parameters.baseline.F_short_sma_spacing`：`"<未设置>"` → `10`
  - `parameters.baseline.G_short_recovery_below_pct`：`"<未设置>"` → `0.3`
  - `parameters.baseline.H_reentry_sma_window`：`"<未设置>"` → `238`
  - `parameters.baseline.L_cost_stop_pct`：`"<未设置>"` → `24.25`
  - `parameters.baseline.R_forced_rebuy_pct`：`"<未设置>"` → `23.5`
  - `parameters.baseline.fast_drop_enabled`：`"<未设置>"` → `false`
  - `parameters.baseline.forced_reentry_enabled`：`"<未设置>"` → `true`
  - `parameters.fast_drop_enabled`：`false` → `"<未设置>"`
  - `parameters.forced_reentry_enabled`：`true` → `"<未设置>"`
  - `parameters.formal_training_candidates`：`["joint_plateau_representative","maximum_cagr","maximum_sharpe"]` → `"<未设置>"`
  - `parameters.global_sample_count`：`100000` → `"<未设置>"`
  - `parameters.indicator_history_start`：`"1999-03-10"` → `"<未设置>"`
  - `parameters.joint_anchor_count`：`64` → `"<未设置>"`
  - `parameters.joint_cases_per_anchor`：`512` → `"<未设置>"`
  - `parameters.joint_neighborhood_radii.A_negative_days_slow`：`1` → `"<未设置>"`
  - `parameters.joint_neighborhood_radii.B_slow_sma_window`：`10` → `"<未设置>"`
  - `parameters.joint_neighborhood_radii.E_fallback_sma_window`：`10` → `"<未设置>"`
  - `parameters.joint_neighborhood_radii.F_short_sma_center`：`10` → `"<未设置>"`
  - ……另有 60 项，完整定义见父子 `experiment.json`。

### DER-v0.60a.1 → DER-v0.60a.2 · QQQ 无 C/D 基线 B=160 双窗口单参数高原诊断

- 关系：`recentered_robustness_check_of`
- 为什么改：父实验显示 B=160 位于 B 自身的跨窗口高原内，但一次只改变一个参数不能证明把 B 固定在 160 后其他参数仍然稳定，因此需要检查被 OAT 隐藏的参数交互。
- 策略修改：仅把冻结基线 B_slow_sma_window 从 178 改为 160；策略语义、其他参数、九组扰动范围、两个评价窗口、高原门槛和验证方式全部不变，并重新计算所有单参数曲线。
- 修改前：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 修改后：QQQ；CAGR —；Sharpe —；Max DD —；run `—`（unknown）。
- 定义：[父实验](../DER/DER-v0.60a.1__26-08-15__qqq_no_fast_drop_oat_two_windows/experiment.json) · [子实验](../DER/DER-v0.60a.2__26-08-15__qqq_no_fast_drop_oat_b160_two_windows/experiment.json)
- 自动配置差异：

  - `parameters.baseline.B_slow_sma_window`：`178` → `160`
  - `parameters.search_method`：`"one_parameter_at_a_time_around_frozen_DER_v0.60_baseline"` → `"one_parameter_at_a_time_around_B160_recentered_baseline"`
  - `strategy.description`：`"冻结 DER-v0.60 在 1999–2015 训练期选出的无 C/D 九参数高原代表，不改变任何策略语义；分别在原训练评价窗口和去掉 2005 年以前行情的子窗口执行单参数扰动，以诊断冻结点是一维高原还是时期驱动的尖峰。"` → `"以 DER-v0.60a.1 为父实验，只把无 C/D 九参数基线中的 B 慢趋势卖出 SMA 从 178 重定到 160；其余策略语义、参数、扰动范围、两个评价窗口与高原门槛全部冻结，重新执行全部九维单参数扰动，以检查 B=160 下其他参数的一维局部形状是否仍然稳定。"`
  - `strategy.name`：`"intraday_sma_no_fast_drop_oat_two_windows"` → `"intraday_sma_no_fast_drop_oat_b160_two_windows"`
