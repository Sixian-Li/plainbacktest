# 回测实验登记册

本文件按研究流派与演化版本集中登记正式实验；策略定义和不可覆盖的运行证据仍保存在各实验目录。

导航：[总策略演化史](strategy_evolution.md) · 分策略演化史：[DER](program_evolution/DER.md) / [ROT](program_evolution/ROT.md) / [TIM](program_evolution/TIM.md) · [研究谱系图](research_map.html) · [统一指标台账](scorecard.csv) · [机器谱系](lineage.json) · [事件日志](research_events.jsonl)

## DER · 短均线导数卖点

用短均线导数、长期趋势线与再入场规则研究单标的退出时机，并执行参数搜索、稳定性和跨标的检验。

<!-- EXPERIMENT:qqq_intraday_sma_or_2021_v1 -->
### DER-v0.10 · QQQ 短均线导数 OR 规则基线 · `qqq_intraday_sma_or_2021_v1`
- Experiment ID：`qqq_intraday_sma_or_2021_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：从 2021-01-04 Open 持有 100 股 QQQ 出发，持仓时由成本止损、短均线慢性转弱、短均线快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则把全部现金买回。所有技术阈值只用前一交易日及更早的已完成 Close 推导，再用下一交易日复权 OHLC 判断是否触及。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。；若前 Close 到当日 Open 已越过阈值，按当日 Open；否则常规时段 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_or_2021_v1](DER/DER-v0.10__26-08-13__qqq_intraday_sma_or_2021/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_reentry_grid_2021_v1 -->
### DER-v0.20a.1 · QQQ 初始空仓与强制买回网格（2021–2026） · `qqq_intraday_sma_reentry_grid_2021_v1`
- Experiment ID：`qqq_intraday_sma_reentry_grid_2021_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：2021-01-04 起先持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。本实验只扫描强制买回幅度 R=0% 至 10%。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。；若前 Close 到当日 Open 已越过阈值，按当日 Open；否则常规时段 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_reentry_grid_2021_v1](DER/DER-v0.20a.1__26-08-13__qqq_intraday_sma_reentry_grid_2021/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_reentry_grid_2010_2015_v1 -->
### DER-v0.20a.2 · QQQ 强制买回网格早期窗口 · `qqq_intraday_sma_reentry_grid_2010_2015_v1`
- Experiment ID：`qqq_intraday_sma_reentry_grid_2010_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：按用户随机指定的 2010-01-01 至 2015-06-01 区间复测同一策略；因 2010-01-01 休市，实际观察从 2010-01-04 开始。初始持有现金，等待第一个普通买入信号后全仓持有 QQQ；持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。扫描 R=0% 至 10%。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。；若前 Close 到当日 Open 已越过阈值，按当日 Open；否则常规时段 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_reentry_grid_2010_2015_v1](DER/DER-v0.20a.2__26-08-13__qqq_intraday_sma_reentry_grid_2010_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_reentry_grid_full_history_v1 -->
### DER-v0.20a.3 · QQQ 强制买回网格全历史 · `qqq_intraday_sma_reentry_grid_full_history_v1`
- Experiment ID：`qqq_intraday_sma_reentry_grid_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：在本地已批准 QQQ 日线的完整覆盖区间 1999-03-10 至 2026-08-04 复测同一策略。初始持有现金；数据起点后的 SMA 预热期不产生信号，等待第一个普通买入信号后全仓持有 QQQ。持仓时由成本止损、短均线慢性转弱、SMA25/30/35 当日变化率同时快速转弱或 SMA200 下穿中的任一规则全仓卖出；空仓时由卖出价上方强制买回、SMA200 上穿或短均线恢复中的任一规则全仓买回。扫描 R=0% 至 10%。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。指标尚未完成预热时不生成对应条件单。；若前 Close 到当日 Open 已越过阈值，按当日 Open；否则常规时段 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_reentry_grid_full_history_v1](DER/DER-v0.20a.3__26-08-13__qqq_intraday_sma_reentry_grid_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_three_rule_ablation_full_history_v1 -->
### DER-v0.20b.1 · QQQ 三规则联合消融 · `qqq_intraday_sma_three_rule_ablation_full_history_v1`
- Experiment ID：`qqq_intraday_sma_three_rule_ablation_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：在完整 QQQ 批准历史上同时删除卖出价上方强制买回、成本线强止损、短均线连续转弱且跌破 SMA130 三条规则。初始空仓，等待 SMA200 上穿或短均线恢复首次触发后全仓买入；持仓时只保留三条短均线同时快速下跌或 SMA200 下穿卖出；空仓时只保留 SMA200 上穿或短均线恢复买入。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值公式；盘中是否触发由该日常规时段调整后 OHLC 判定。指标尚未完成预热时不生成对应条件单。；若前 Close 到当日 Open 已越过阈值，按当日 Open；否则常规时段 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_three_rule_ablation_full_history_v1](DER/DER-v0.20b.1__26-08-13__qqq_intraday_sma_three_rule_ablation_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_global_search_full_history_v1 -->
### DER-v0.30 · QQQ A–H/L/R 全局搜索 · `qqq_intraday_sma_global_search_full_history_v1`
- Experiment ID：`qqq_intraday_sma_global_search_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：在 QQQ 全部批准历史上保留此前所有短均线导数用途并同时保留成本止损、SMA200 兜底、强制买回及两种普通买入。初始空仓；对 A、B、C、D、E、F、G、H、L、R 的预声明离散空间做固定种子的全域随机搜索和前沿附近变异搜索，分别优化 CAGR 与 Sharpe，并检查相同首买点下是否跑赢 Buy & Hold。
- 标的：QQQ
- 信号与成交：每个交易日开始前，仅用截至前一交易日 Close 的历史计算该日条件单及动态阈值；指标预热不足时不生成对应条件单。；若前 Close 到当日 Open 已越过阈值，按当日常规时段 Open；否则常规时段调整后 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_global_search_full_history_v1](DER/DER-v0.30__26-08-13__qqq_intraday_sma_global_search_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_window_stability_v1 -->
### DER-v0.30a.1 · QQQ 参数跨窗口稳定性 · `qqq_intraday_sma_window_stability_v1`
- Experiment ID：`qqq_intraday_sma_window_stability_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：以完整历史全局搜索赢家附近为预声明局部空间，保留全部短均线导数、成本止损、长期 SMA 兜底、长期 SMA/短均线恢复买入，并把强制买回作为可启用或完全关闭的离散模式。在 1999–2014、2005–2020、2010–2026 三个重叠窗口用同一候选全集分别优化 CAGR 与 Sharpe，再把每窗代表参数交叉应用到全部窗口，检查参数和排名是否稳定。
- 标的：QQQ
- 信号与成交：每个交易日开始前，只用截至前一交易日 Close 的历史计算条件单；2005 与 2010 窗口使用窗口开始前的 QQQ 历史预热指标，但预热期禁止交易。1999 窗口从 QQQ 上市首日开始，因不存在上市前数据只能自然预热。；若前 Close 到当日 Open 已越过阈值，按当日常规时段 Open；否则常规时段调整后 OHLC 触及阈值时按精确理论阈值；零滑点、零费用；每天最多一笔成交。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_window_stability_v1](DER/DER-v0.30a.1__26-08-13__qqq_intraday_sma_window_stability/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_oat_sensitivity_two_periods_v1 -->
### DER-v0.30b.1 · QQQ 双时期 OAT 敏感性 · `qqq_intraday_sma_oat_sensitivity_two_periods_v1`
- Experiment ID：`qqq_intraday_sma_oat_sensitivity_two_periods_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：固定此前 1999–2014 窗口代表参数作为基线，每次只扰动 A–H、L、R 或强制买回开关中的一个维度，在 1999–2009 与 2010–2026 两个不重叠时期分别观察 CAGR 和 Sharpe 曲线，以判断基线附近的局部平滑性、边界敏感性和跨时期方向一致性。
- 标的：QQQ
- 信号与成交：每个交易日开始前只用截至前一交易日 Close 的历史计算条件单；2010 窗口使用此前历史预热指标但预热期不交易。；若前 Close 到当日 Open 已越过阈值，按常规时段 Open；否则调整后日内 OHLC 触及阈值时按精确理论阈值；零滑点、零费用、每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_oat_sensitivity_two_periods_v1](DER/DER-v0.30b.1__26-08-13__qqq_intraday_sma_oat_sensitivity_two_periods/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_final_fixed_full_history_v1 -->
### DER-v0.40 · QQQ 最终固定参数历史评估 · `qqq_intraday_sma_final_fixed_full_history_v1`
- Experiment ID：`qqq_intraday_sma_final_fixed_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：冻结用户在全局搜索、分窗检验和逐参数敏感性分析后选定的最终参数，不再优化。策略从 QQQ 批准历史起点以现金开始，等待普通买入；随后以成本止损、慢速短均线转弱、三短均线快速下跌或长期均线下穿中的任一条件卖出，并以同价强制买回、长期均线上穿或短均线恢复中的任一条件买入。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史，解出当日条件单阈值；指标预热不足时对应规则不生成订单。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_final_fixed_full_history_v1](DER/DER-v0.40__26-08-13__qqq_intraday_sma_final_fixed_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_final_local_plateau_full_history_v1 -->
### DER-v0.40a.1 · QQQ 最终参数局部高原 · `qqq_intraday_sma_final_local_plateau_full_history_v1`
- Experiment ID：`qqq_intraday_sma_final_local_plateau_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：以用户冻结的最终 QQQ 参数 A=3、B=175、C=-0.25%、D=4、E=305、F=80±10、G=1%、H=270、L=10%、R=0% 且开启强制买回为基线，在完整批准历史上每次只扰动 B、E、F 中心、G、H 或 L 的一个维度，用 CAGR 与 Sharpe 的局部响应判断该参数向量附近是宽高原还是孤立尖峰。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史解出当日条件单阈值；指标预热不足时对应规则不产生订单。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_final_local_plateau_full_history_v1](DER/DER-v0.40a.1__26-08-14__qqq_intraday_sma_final_local_plateau_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_recentered_local_plateau_full_history_v1 -->
### DER-v0.41 · QQQ 重定基线局部高原 · `qqq_intraday_sma_recentered_local_plateau_full_history_v1`
- Experiment ID：`qqq_intraday_sma_recentered_local_plateau_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：在上一轮最终参数局部高原检验后，将整套 QQQ 策略基线更新为 B=176、E=301、G=0.95%、H=264，保持 A=3、C=-0.25%、D=4、F=80±10、L=10%、R=0% 与强制买回开启不变；在完整批准历史中每次只扰动 B、E、F 中心、G、H 或 L 的一个维度，重新检查新联合基线附近的 CAGR 与 Sharpe 高原。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史解出当日条件单阈值；指标预热不足时对应规则不产生订单。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_recentered_local_plateau_full_history_v1](DER/DER-v0.41__26-08-14__qqq_intraday_sma_recentered_local_plateau_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:spy_intraday_sma_recentered_fixed_2000_2020_v1 -->
### DER-v0.41a.1 · QQQ 高原参数迁移到 SPY · `spy_intraday_sma_recentered_fixed_2000_2020_v1`
- Experiment ID：`spy_intraday_sma_recentered_fixed_2000_2020_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：把 QQQ 全历史局部高原复测后由用户确认的联合参数完整冻结，不在 SPY 上重新选参；使用 2000 年前 SPY 历史预热指标，2000-01-03 起空仓等待第一笔普通买入，在 2020-12-31 收盘停止评价。
- 标的：SPY
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史，解出当日条件单阈值；2000 年前数据只用于指标预热，窗口前禁止交易。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[spy_intraday_sma_recentered_fixed_2000_2020_v1](DER/DER-v0.41a.1__26-08-14__spy_intraday_sma_recentered_fixed_2000_2020/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:spy_intraday_sma_training_1993_2002_v1 -->
### DER-v0.50a.1 · SPY 宽域训练（1993–2002） · `spy_intraday_sma_training_1993_2002_v1`
- Experiment ID：`spy_intraday_sma_training_1993_2002_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：只用 SPY 上市日至 2002 年末训练此前冻结的日内动态 SMA 规则族。保持四类 OR 卖出、三类 OR 买入、初始空仓和盘前解阈值语义不变；在 A–H/L/R 的宽范围细离散网格上固定种子全域抽样并前沿精搜，排除缺少任一侧相邻点的边界中心，再按每个候选相邻一步扰动后的最差 CAGR 与最差 Sharpe 选择双侧稳健代表。2003 年起数据完全不参与本实验。
- 标的：SPY
- 信号与成交：每个交易日开始前仅用截至前一交易日 Close 的完整可用历史解出当日条件单；SPY 上市后指标尚未预热完成时，相应规则不产生订单。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则当日常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[spy_intraday_sma_training_1993_2002_v1](DER/DER-v0.50a.1__26-08-14__spy_intraday_sma_training_1993_2002/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:spy_intraday_sma_trained_fixed_2003_2013_v1 -->
### DER-v0.50a.2 · SPY 锁定窗口测试（2003–2013） · `spy_intraday_sma_trained_fixed_2003_2013_v1`
- Experiment ID：`spy_intraday_sma_trained_fixed_2003_2013_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：把仅用 SPY 1993-01-29～2002-12-31 训练选出的唯一双侧邻域代表完整冻结，在紧接其后的 2003-01-02～2013-12-31 运行一次锁定样本外测试；窗口前历史只预热指标，本实验不搜索、不扰动、不依据结果替换参数。
- 标的：SPY
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史解出当日条件单阈值；2003 年前数据只用于指标预热，窗口前禁止交易。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[spy_intraday_sma_trained_fixed_2003_2013_v1](DER/DER-v0.50a.2__26-08-14__spy_intraday_sma_trained_fixed_2003_2013/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:spy_intraday_sma_training_1993_2020_v1 -->
### DER-v0.50b.1 · SPY 扩展训练（1993–2020） · `spy_intraday_sma_training_1993_2020_v1`
- Experiment ID：`spy_intraday_sma_training_1993_2020_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：把此前 SPY 宽范围细步长训练协议原样扩展到 1993-01-29～2020-12-31。保持四类 OR 卖出、三类 OR 买入、初始空仓和盘前解阈值语义不变；在同一 A–H/L/R 离散空间中固定种子全域抽样并前沿精搜，排除缺少任一侧相邻点的边界中心，再按每个候选相邻一步扰动后的最差 CAGR 与最差 Sharpe 选择双侧稳健代表。2021 年起数据完全不参与本实验。
- 标的：SPY
- 信号与成交：每个交易日开始前仅用截至前一交易日 Close 的完整可用历史解出当日条件单；SPY 上市后指标尚未预热完成时，相应规则不产生订单。；若前 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则当日常规时段调整后 OHLC 触及预先解出的理论阈值时按该阈值成交；每天最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[spy_intraday_sma_training_1993_2020_v1](DER/DER-v0.50b.1__26-08-14__spy_intraday_sma_training_1993_2020/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_no_fast_drop_train_oos_v1 -->
### DER-v0.60 · QQQ 移除 C/D 后联合高原训练与锁定测试 · `qqq_intraday_sma_no_fast_drop_train_oos_v1`
- Experiment ID：`qqq_intraday_sma_no_fast_drop_train_oos_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：回到 QQQ 重新开发此前的日内动态 SMA 条件单策略，完全移除 C/D 快速导数卖出。只在共同预热后的 2000-07-27 至 2015-12-31 训练并以九维联合邻域的下四分位 CAGR/Sharpe 选择高原代表；参数锁定后仅运行一次 2016-01-04 至 2026-08-04 样本外回测，样本外结果不参与选参。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史，解出当日动态条件单阈值；训练和样本外均可使用各自评价起点之前的 QQQ 历史做指标预热，但不能在评价起点前成交。；若前一 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则当日调整后 OHLC 触及盘前解出的理论阈值时按该阈值成交。每天最多一笔，不使用 PyBroker middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_no_fast_drop_train_oos_v1](DER/DER-v0.60__26-08-14__qqq_intraday_sma_no_fast_drop_train_oos/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_no_fast_drop_oat_two_windows_v1 -->
### DER-v0.60a.1 · QQQ 无 C/D 基线双窗口单参数高原诊断 · `qqq_intraday_sma_no_fast_drop_oat_two_windows_v1`
- Experiment ID：`qqq_intraday_sma_no_fast_drop_oat_two_windows_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：冻结 DER-v0.60 在 1999–2015 训练期选出的无 C/D 九参数高原代表，不改变任何策略语义；分别在原训练评价窗口和去掉 2005 年以前行情的子窗口执行单参数扰动，以诊断冻结点是一维高原还是时期驱动的尖峰。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史，解出当日动态条件单阈值；两个窗口均可使用评价起点之前的 QQQ 历史做指标预热，但不能在评价起点前成交。；若前一 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则当日调整后 OHLC 触及盘前解出的理论阈值时按该阈值成交。每天最多一笔，不使用 PyBroker middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_no_fast_drop_oat_two_windows_v1](DER/DER-v0.60a.1__26-08-15__qqq_no_fast_drop_oat_two_windows/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_no_fast_drop_oat_b160_two_windows_v1 -->
### DER-v0.60a.2 · QQQ 无 C/D 基线 B=160 双窗口单参数高原诊断 · `qqq_intraday_sma_no_fast_drop_oat_b160_two_windows_v1`
- Experiment ID：`qqq_intraday_sma_no_fast_drop_oat_b160_two_windows_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：以 DER-v0.60a.1 为父实验，只把无 C/D 九参数基线中的 B 慢趋势卖出 SMA 从 178 重定到 160；其余策略语义、参数、扰动范围、两个评价窗口与高原门槛全部冻结，重新执行全部九维单参数扰动，以检查 B=160 下其他参数的一维局部形状是否仍然稳定。
- 标的：QQQ
- 信号与成交：每个交易日开始前只使用截至前一交易日 Close 的完整历史，解出当日动态条件单阈值；两个窗口均可使用评价起点之前的 QQQ 历史做指标预热，但不能在评价起点前成交。；若前一 Close 到当日常规时段 Open 已越过阈值，按当日 Open 成交；否则当日调整后 OHLC 触及盘前解出的理论阈值时按该阈值成交。每天最多一笔，不使用 PyBroker middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_no_fast_drop_oat_b160_two_windows_v1](DER/DER-v0.60a.2__26-08-15__qqq_no_fast_drop_oat_b160_two_windows/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

## ROT · 上涨区间与轮动

先研究单标的上涨状态和快速退出，再扩展到多标的评分、排名、风险预算与组合轮动。

<!-- EXPERIMENT:qqq_sma_three_conditions_ablation_full_history_v1 -->
### ROT-v0.10 · QQQ 三条件上涨状态消融 · `qqq_sma_three_conditions_ablation_full_history_v1`
- Experiment ID：`qqq_sma_three_conditions_ablation_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：QQQ 初始空仓。每个交易日收盘后检查三个持仓条件：Close 高于 SMA200；SMA30、SMA200、SMA250、SMA300 每一条都连续三个交易日严格增加；SMA200 严格高于 SMA250 且 SMA250 严格高于 SMA300。只有全部条件成立才目标持有，否则目标空仓；条件状态改变后的下一交易日 Open 全仓切换。同时运行完整规则与逐一移除条件 1、2、3 的三个消融版本。
- 标的：QQQ
- 信号与成交：每个交易日常规时段 Close 完成后。SMA 使用含当日 Close 的已完成日线；连续三个交易日增加定义为 SMA(t)>SMA(t-1)>SMA(t-2)>SMA(t-3)，四条指定 SMA 必须各自同时满足。；信号后的下一交易日常规时段 Open，按明确 Open 成交；不允许同日 Close 成交或 PyBroker 默认 middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_three_conditions_ablation_full_history_v1](ROT/ROT-v0.10__26-08-13__qqq_sma_three_conditions_ablation_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_regime_peak_drawdown_train_oos_v1 -->
### ROT-v0.10a.1 · QQQ 持仓峰值回撤训练与样本外 · `qqq_sma_regime_peak_drawdown_train_oos_v1`
- Experiment ID：`qqq_sma_regime_peak_drawdown_train_oos_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：在上一轮去掉条件 2 的基础策略上，只保留 Close>SMA200 与 SMA200>SMA250>SMA300 两个持仓条件。初始空仓；两个基础条件均成立才目标持有，任一不成立则目标空仓。另叠加一个可关闭的持仓内峰值回撤卖出：从每次实际买入成交价开始，用其后每个已完成 Close 更新峰值，若当前 Close 相对该峰值的跌幅严格大于阈值，则产生卖出信号。固定 8% 作为预声明消融基准，并在训练期扫描 5%～15% 后冻结所选值到测试期。
- 标的：QQQ
- 信号与成交：每个交易日常规时段完成 Close 后。基础 SMA 含当日 Close；持仓峰值只使用实际入场价及截至当日的已完成 Close，不使用当日 High 或未来数据。；所有信号均在下一交易日常规时段 Open 执行；按明确 Open 成交，不允许同日 Close、盘中假设成交或 PyBroker 默认 middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_regime_peak_drawdown_train_oos_v1](ROT/ROT-v0.10a.1__26-08-13__qqq_sma_regime_peak_drawdown_train_oos/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_regime_two_measure_ablation_two_periods_v1 -->
### ROT-v0.10b.1 · QQQ 短均线上升与 3% 锁定回买消融 · `qqq_sma_regime_two_measure_ablation_two_periods_v1`
- Experiment ID：`qqq_sma_regime_two_measure_ablation_two_periods_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：以已经去掉旧条件 2 和旧 8% 止损的条件 1+3 策略为基础，做两个新措施的完整 2×2 消融。基础主条件是完成 Close 后 Close>SMA200 且 SMA200>SMA250>SMA300。措施 1 把 SMA25、SMA30、SMA35 各自严格高于前一交易日加入主条件，任一持平或下跌即不满足。措施 2 在单次持仓中用实际入场价及此前已完成 Close 维护盘前已知峰值；当日 Low 首次到达峰值下方 3% 时立即卖出，开盘跳空低于止损价则按 Open，否则按精确 3% 阈值。只有该 3% 卖出会进入特殊回买锁定：路径 A 是完成 Close 回到或高于实际卖出价且主条件全满足；否则路径 B 必须在止损后依次由完成 Close 下穿 SMA200、到达或低于 SMA200×95%、再从下方向上穿越 SMA200，之后主条件全满足。两个窗口和四个 case 均独立从现金开始。
- 标的：QQQ
- 信号与成交：主条件、卖价恢复和 SMA200 重置路径均在完成 Close 后判定。3% 盘中止损阈值在当日开盘前，只用实际入场价和此前完成 Close 计算；不使用当日 High 或未来数据。；主条件买卖和两条锁定回买路径均在下一交易日 Open 执行。3% 止损为同一交易日预放条件单：跳空按 Open，非跳空 Low 触及时按精确阈值。允许次日 Open 买回后同日盘中再次止损。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_regime_two_measure_ablation_two_periods_v1](ROT/ROT-v0.10b.1__26-08-13__qqq_sma_regime_two_measure_ablation_two_periods/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_entry_four_exit_ablation_two_periods_v1 -->
### ROT-v0.20 · QQQ 双路径入场与四卖出规则消融 · `qqq_sma_entry_four_exit_ablation_two_periods_v1`
- Experiment ID：`qqq_sma_entry_four_exit_ablation_two_periods_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：每次独立窗口初始空仓，每次卖出信号都会清空并重置买入资格。买入资格有两条 OR 路径：A，完成 Close 在同一交易日从下方向上穿越 SMA25、SMA30、SMA35 及三者平均线，随后且在任何启用的卖出信号出现前，完成 Close 位于 SMA200 上方；B，完成 Close 从下方向上穿越 SMA200，随后且在任何启用的卖出信号出现前，完成 Close 同时位于 SMA25、SMA30、SMA35 及三者平均线上方。资格形成后持续有效，直至成交或任一启用卖出信号重置；随后盘中 price 同时高于动态 SMA30×98% 和动态 SMA200×98% 时全仓买入。四个完成 Close 卖出开关为：SMA200 连续三个交易日严格下降、短 SMA 平均单日严格下跌超过 0.15%、Close<SMA200、Close<SMA30；对四开关执行全部 2^4=16 组合。
- 标的：QQQ
- 信号与成交：上穿、均线上方状态、三日下降、短均线平均变化和 Close 破位均由完成日线 Close 判定。买入资格形成后，当日收盘之后才能使用；下一交易日的动态 SMA30/SMA200 买入边界只使用此前完成 Close，在开盘前可解。；资格形成后的最早下一交易日，以预放日内买单执行：Open 已高于两条动态 98% 边界时按 Open，否则当日 High 触及较高边界时按精确阈值；若未触及则资格延续。卖出信号一律在下一交易日 Open 执行。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_entry_four_exit_ablation_two_periods_v1](ROT/ROT-v0.20__26-08-14__qqq_sma_entry_four_exit_ablation_two_periods/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_r1_r3_parameter_training_2000_2015_v1 -->
### ROT-v0.20a.1 · QQQ R1+R3 参数训练 · `qqq_sma_r1_r3_parameter_training_2000_2015_v1`
- Experiment ID：`qqq_sma_r1_r3_parameter_training_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：冻结四规则消融中的R1+R3结构，只训练其数值参数。每个训练case从2000-01-03空仓开始；路径A为同一完成Close上穿三条短均线后在卖出重置前Close位于长均线上方，路径B为完成Close上穿长均线后在卖出重置前Close位于三条短均线上方。资格形成后从下一交易日起，以动态短中线与动态长线的折价边界中较高者买入。卖出为R1长均线连续N日按指定最小幅度下降，或R3完成Close跌破带缓冲的长均线；二者OR。
- 标的：QQQ
- 信号与成交：路径资格、R1与R3只使用完成Close；动态买入边界只使用入场日之前完成的Close。；买入资格形成后最早下一交易日Open或日内High触及动态边界；卖出信号下一交易日Open。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_r1_r3_parameter_training_2000_2015_v1](ROT/ROT-v0.20a.1__26-08-14__qqq_sma_r1_r3_parameter_training_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear_resilience_unified_trend_score_portfolio_v1 -->
### ROT-v0.30 · 熊市韧性候选统一趋势评分组合 · `bear_resilience_unified_trend_score_portfolio_v1`
- Experiment ID：`bear_resilience_unified_trend_score_portfolio_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：在此前当前成分股熊市事件研究得到的 13 个核心和 8 个近核心候选上，冻结一套不按个股寻优的牛熊评分。个股分由 Close 相对 SMA200 的一倍 ATR20 滞回状态、SMA200 二十日方向及 3/6/12 个月动量组成；总分再加入 SPY、QQQ 和当前成分股 SMA200 广度。底层使用可用标的 63 日逆波动风险预算，评分只做阶梯减仓，释放资金留现金。
- 标的：RESILIENCE_21
- 信号与成交：每周最后一个共同交易日的常规时段 Close 完成后，只使用截至该 Close 的 OHLC、SMA、ATR、动量、波动率与市场广度；ATR 滞回带内保持此前状态。；信号后的下一共同交易日按拆股与股息调整 Open 成交；同日先卖后买，买单按固定字母序执行，单边成本作为买高卖低的成交价冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear_resilience_unified_trend_score_portfolio_v1](ROT/ROT-v0.30__26-08-14__bear_resilience_unified_trend_score_portfolio/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_three_factor_downside_risk_training_2000_2015_v1 -->
### ROT-v0.40a.2 · QQQ 长短趋势与下行风险三因子消融 · `qqq_three_factor_downside_risk_training_2000_2015_v1`
- Experiment ID：`qqq_three_factor_downside_risk_training_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-21
- 策略：QQQ只在0%和100%仓位之间切换。长期趋势因子要求完成Close高于SMA180且SMA180最近3个交易日的平均每日百分比斜率严格高于0.04%；短期趋势因子要求SMA20最近10个完成值的OLS每日对数斜率乘R²严格高于0；风险因子比较短期与长期波动率，风险比率严格高于报警阈值时进入危险状态，之后只有严格低于报警阈值减0.4才恢复安全。主候选使用只保留下跌收益的下行波动率与风险否决结构；普通总波动率、严格3/3和普通2/3只作消融。所有信号仅使用完成Close，不使用固定百分比止损。
- 标的：QQQ
- 信号与成交：长期SMA、长期斜率、短期趋势质量、每日对数收益和波动率比值只使用当日常规时段完成Close及更早数据；当日Close完成后才确认状态变化。；所有买卖信号最早在下一QQQ交易日的拆股及股息调整Open成交；每边成本作为买高卖低的成交价冲击，禁止同日Close成交和PyBroker默认middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_three_factor_downside_risk_training_2000_2015_v1](ROT/ROT-v0.40a.2__26-08-21__qqq_three_factor_downside_risk_training_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_pit_stochrsi_strategy1_top20_rotation_v1 -->
### ROT-v0.50a.1 · Nasdaq-100历史成分Strategy1-90前20轮动 · `nasdaq100_pit_stochrsi_strategy1_top20_rotation_v1`
- Experiment ID：`nasdaq100_pit_stochrsi_strategy1_top20_rotation_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：对每一只历史时点Nasdaq-100成分证券独立运行已验证的策略1（D0_F0_S0），把当日收盘后策略1模拟账户中的股票市值占比作为分数。只有分数严格高于90%的证券才有资格参与排名；在每个预定调仓收盘从当时已知成分中取分数最高的20只，下一交易日开盘按目标权重完整再平衡。比较1、2、3、5个交易日调仓，以及不足10只时将未满十个10%槽位交给固定Bear9或始终把全部资金等权分配给所有合格股。每条政策同时以单边0和5bps执行，因此共有16条正式路径。历史成分和个股行情来自pending_review候选包，结果只能作探索性证据。
- 标的：NASDAQ100_PIT_STRATEGY1_ROTATION
- 信号与成交：成员资格、策略1仓位分数、排名和目标权重只使用当日已完成的拆股及股息调整Close以及此前信息。为避免把生效日才观察到的InIndex变化倒灌进决策，成员状态统一使用前一XNYS交易日的观察值。；调仓收盘确认目标，下一XNYS交易日的拆股及股息调整Open先卖出超额份额，再按稳定security_id顺序买入不足份额；允许小数股。成交价分别加入单边0或5bps不利冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_pit_stochrsi_strategy1_top20_rotation_v1](ROT/ROT-v0.50a.1__26-08-26__nasdaq100_stochrsi_strategy1_top20/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_strategy1_90_holding_attribution_v1 -->
### ROT-v0.50a.2 · Nasdaq-100逐股Strategy1-90持仓与退出归因 · `nasdaq100_strategy1_90_holding_attribution_v1`
- Experiment ID：`nasdaq100_strategy1_90_holding_attribution_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-27
- 策略：复用父实验已冻结的逐证券Strategy1收盘后股票仓位分数和时点成员资格，对354只曾严格超过90%的历史Nasdaq-100证券分别建立独立的全仓或现金账户。某证券当日收盘分数严格高于90%时，下一可成交交易日Open全仓买入；分数不再严格高于90%时，下一可成交交易日Open全部卖出。报告逐证券的日历CAGR、按持仓时间几何压缩的持仓期间CAGR、持仓收益Sharpe、最大回撤、持仓时间、持仓段数，以及卖出后5/10/20/60个交易日的表现，用于判断90%门槛是否经常在反弹前退出。历史成分和个股行情仍是pending_review候选数据，因此只作探索性归因。
- 标的：NASDAQ100_STRATEGY1_90_ATTRIBUTION
- 信号与成交：复用父run在每个XNYS交易日完成后的拆股及股息调整Close、Strategy1收盘后仓位分数和滞后一交易日的历史成分状态；没有重算StochRSI或修改资格。；收盘信号在下一XNYS交易日的拆股及股息调整Open成交；分别按单边0和5bps不利冲击，允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_strategy1_90_holding_attribution_v1](ROT/ROT-v0.50a.2__26-08-27__nasdaq100_strategy1_90_holding_attribution/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_strategy1_gate_factorial_v1 -->
### ROT-v0.50a.3 · Nasdaq-100个股Strategy1-90三因子交叉消融 · `nasdaq100_strategy1_gate_factorial_v1`
- Experiment ID：`nasdaq100_strategy1_gate_factorial_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-27
- 策略：从父实验中严格筛出Strategy1-90持仓超过200个交易日的证券，再按父实验零成本持仓期间CAGR选取最高四只、最接近横截面中位数四只和最低四只。每只证券在退出现有Nasdaq-100成分身份之前的最近十个日历年内单独运行全仓或现金账户，完整交叉检验5%机械止损、双周期StochRSI都高于0.20才允许入场、以及持仓期间StochRSI100从0.80上方向下穿越便退出三项开关。母Strategy1仓位分数及全部指标使用窗口前完整历史预热；选入样本后不再施加成分资格或Top20排名门槛。本研究因按历史结果选样且行情仍为pending_review，只作机制诊断，不是样本外证据。
- 标的：NASDAQ100_STRATEGY1_GATE_FACTORIAL
- 信号与成交：母Strategy1分数、严格入场条件和快速退出穿越均在拆股及股息调整后的有效交易日Close完成后确认；止损使用入场后交易日的调整Open与Low。母策略先用证券完整可用历史计算，再截取各自十年评价窗口。；普通90%门禁和严格入场在信号后的下一有效交易日调整Open成交；5%止损按下一交易日起的调整Open跳空价或精确止损价成交；快速下穿退出在穿越当日调整Close成交。分别报告单边0和5bps不利冲击，允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_strategy1_gate_factorial_v1](ROT/ROT-v0.50a.3__26-08-27__nasdaq100_strategy1_gate_factorial/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_strategy1_rotation_2005_2010_factorial_v1 -->
### ROT-v0.50a.4 · Nasdaq-100 Strategy1轮动三维消融（2005–2010） · `nasdaq100_strategy1_rotation_2005_2010_factorial_v1`
- Experiment ID：`nasdaq100_strategy1_rotation_2005_2010_factorial_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-28
- 策略：在2005至2010年的历史时点Nasdaq-100成分中，对每只证券独立运行冻结的Strategy1，并把完成Close后的股票仓位作为分数。正式12格由快速退出开关、Top20且分数严格高于90%/全部高于90%/全部高于80%三种选股范围，以及每两日恢复等权/每两日只处理进出而让存量漂移两种资金管理完整交叉。普通名单在每两个XNYS交易日的Close确认并于下一Open成交；快速退出严格沿用父诊断，在持仓跨日时StochRSI100由不低于0.80真下穿至0.80以下的当日Close全卖，并在母Strategy1仓位严格低于20%前锁定。另报告历史时点全部成分每两日等权和同期QQQ Buy & Hold两个基准。候选历史成分与行情仍为pending_review，因此结果只作探索性证据。
- 标的：NASDAQ100_STRATEGY1_ROTATION_2005_2010
- 信号与成交：母Strategy1分数、StochRSI100穿越、名单和目标均只使用拆股及股息调整后的完成Close与此前信息；成分身份统一使用前一XNYS交易日已观察到的状态。；普通名单变化和RESTORE_EQUAL目标在决策Close确认后于下一有效调整Open先卖后买；快速退出在真下穿当日调整Close成交并优先于该Close产生的下一期名单。每条路径分别使用单边0或5bps不利冲击，允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_strategy1_rotation_2005_2010_factorial_v1](ROT/ROT-v0.50a.4__26-08-28__nasdaq100_strategy1_rotation_2005_2010/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_strategy1_energy_factor_anatomy_v1 -->
### ROT-v0.50a.5 · Nasdaq-100 Strategy1能量因子解剖 · `nasdaq100_strategy1_energy_factor_anatomy_v1`
- Experiment ID：`nasdaq100_strategy1_energy_factor_anatomy_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-28
- 策略：在1999-03-10至2026-08-04的滞后一交易日历史Nasdaq-100成分中，对每只证券重新运行冻结且未经调参的Strategy1母状态，并把完成Close后的股票仓位比例称为能量。研究不做Top20、不构造共享资本轮动账户，也不按结果修改42/100周期或任何母策略规则；它把每个证券日按能量水平、五个交易日前后的能量方向以及高能量穿越状态分类。每个可执行观察在信号Close后的下一真实Open开始，用独立1美元事件账户观察未来5/10/20/60个XNYS交易日的证券收益、同期QQQ超额收益、最大有利涨幅和最大不利回撤。结果同时报告日等权、证券等权、逐日横截面Rank IC、五个时期和按月区块自助置信区间，用来判断能量究竟是可排序因子、只适合门禁，还是只是过去交易路径的状态记录。历史成分与个股行情仍为pending_review候选数据，所有结论只作探索。
- 标的：NASDAQ100_STRATEGY1_ENERGY_FACTOR
- 信号与成交：Strategy1能量、能量五日方向、历史自身252日百分位和高能量状态只使用当日完成Close及更早信息；成员身份使用滞后一XNYS交易日后可观察的历史时点状态。；能量在交易日Close确认，标准化事件从下一XNYS交易日真实调整Open开始，在预声明期限的调整Close或更早的最后真实Close结束；分别应用单边0或5bps不利冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_strategy1_energy_factor_anatomy_v1](ROT/ROT-v0.50a.5__26-08-28__nasdaq100_strategy1_energy_factor_anatomy/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_p24_stricter_threshold_grid_2000_2015_v1 -->
### ROT-v0.40b.1 · QQQ P24长短趋势严格门槛网格 · `qqq_p24_stricter_threshold_grid_2000_2015_v1`
- Experiment ID：`qqq_p24_stricter_threshold_grid_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-28
- 策略：QQQ只在0%和100%仓位之间切换。价格必须高于SMA180；SMA180最近10个交易日的平均每日百分比涨幅必须严格高于待测长期门槛；SMA20最近10个完成值的OLS每日对数斜率乘R²再乘100必须严格高于待测短期门槛。长期门槛测试0.02%、0.03%、0.04%和0.05%每日，短期门槛测试0、0.02%、0.05%和0.10%每日，共16组；原P24的0.02%与0组合保留为对照。不使用风险因子或固定百分比止损。
- 标的：QQQ
- 信号与成交：全部价格、SMA与趋势质量只使用当日常规时段完成Close及更早数据；当日Close完成后才确认资格、连续日计数和退出。；所有买卖信号最早在下一QQQ交易日的拆股及股息调整Open成交；每边成本作为买高卖低的成交价冲击，禁止同日Close成交和PyBroker默认middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_p24_stricter_threshold_grid_2000_2015_v1](ROT/ROT-v0.40b.1__26-08-28__qqq_p24_stricter_threshold_grid_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_p24_three_state_portfolios_2005_2012_v1 -->
### ROT-v0.40c.1 · Nasdaq-100 P24原版、差集与严格版全体等权 · `nasdaq100_p24_three_state_portfolios_2005_2012_v1`
- Experiment ID：`nasdaq100_p24_three_state_portfolios_2005_2012_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-29
- 策略：在2005至2012年的历史时点Nasdaq-100成分中，对每只证券独立计算原P24与严格P24。原P24要求价格高于SMA180、SMA180最近10日平均每日涨幅严格高于0.02%、SMA20最近10个完成值的OLS对数日斜率乘R²再乘100严格高于0，并连续2个完成交易日合格；严格P24只把两个门槛提高为0.04%和0.05%，其余完全不变。比较三条不排名、无数量上限的每日等权组合：全部原P24状态、原P24但不属于严格状态的差集、全部严格P24状态。历史成员身份延迟一个XNYS交易日使用；普通信号在完成Close确认并于下一Open成交。候选历史成分与行情仍为pending_review，所以结果只作探索性证据。
- 标的：NASDAQ100_P24_THREE_STATE_2005_2012
- 信号与成交：SMA、长期平均涨幅、短期趋势质量和连续日确认只使用拆股及股息调整后的当日完成Close及更早价格；成员身份统一延迟一个XNYS交易日，分析窗口开始时不继承此前连续日计数或仓位。；每个Close产生下一交易日Open的完整目标名单。执行时先卖出或减持，再买入或加仓；每次都按信号Close账户净值和信号Close价格把全部合格股恢复为等权。每边分别测试0或10bps不利成交价冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_p24_three_state_portfolios_2005_2012_v1](ROT/ROT-v0.40c.1__26-08-29__nasdaq100_p24_three_state_2005_2012/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_p24_hysteresis_rebalance_ablation_2005_2012_v1 -->
### ROT-v0.40d.1 · Nasdaq-100 P24滞回与再平衡二乘二消融 · `nasdaq100_p24_hysteresis_rebalance_ablation_2005_2012_v1`
- Experiment ID：`nasdaq100_p24_hysteresis_rebalance_ablation_2005_2012_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-29
- 策略：在2005至2012年的滞后一交易日历史Nasdaq-100成员中，完全冻结父实验的原P24与严格P24参数，做两项二元结构消融。持有轴比较严格P24每日状态与严格P24买入、原P24退出的滞回状态机；再平衡轴比较每日恢复等权与仅在目标名单变化或前次未完成时恢复等权。四条路径都逐日检查全部历史合格股，不排名、不限数量，完成Close确认后最早下一Open成交，并分别测试0与10bps。候选历史成分和行情仍为pending_review，所以结果只作探索性证据。
- 标的：NASDAQ100_P24_HYSTERESIS_REBALANCE_2005_2012
- 信号与成交：SMA、长期平均涨幅、短期趋势质量、连续日确认和滞回状态都只使用拆股及股息调整后的当日完成Close及更早价格；成员身份统一延迟一个XNYS交易日，分析窗口开始时不继承此前确认计数、滞回状态或仓位。；每个Close确认下一交易日Open的目标名单。DAILY_EQUAL路径每个Open先卖后买并恢复等权；SELECTION_CHANGE_EQUAL路径只在名单变化或上次目标因真实行情不可用而未完成时执行同样的先卖后买等权调整，名单不变时不为价格漂移交易。每边分别测试0或10bps不利成交价冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_p24_hysteresis_rebalance_ablation_2005_2012_v1](ROT/ROT-v0.40d.1__26-08-29__nasdaq100_p24_hysteresis_rebalance_ablation/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_pit_12_1_momentum_top10_top20_buffer_v1 -->
### ROT-v0.50b.1 · Nasdaq-100 12-1动量Top10/Top20缓冲基线 · `nasdaq100_pit_12_1_momentum_top10_top20_buffer_v1`
- Experiment ID：`nasdaq100_pit_12_1_momentum_top10_top20_buffer_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-29
- 策略：在每个日历月最后一个XNYS交易日收盘后，只从滞后一个交易日可知的Nasdaq-100历史时点成分证券中排名。分数是上个月末相对再往前12个月末的拆股及股息调整Close总收益，因而完整跳过信号月这个最近月。实验同时运行三条组合：点时成分等权、每月强制替换为前10名，以及进入前10名后只有跌出前20名才退出的缓冲组合。成员离开指数时不等待月末，待滞后成员状态可见后发出强制卖出。数据仍是candidate_pending_review，结论只属于探索研究。
- 标的：NASDAQ100_MOMENTUM_ROTATION
- 信号与成交：排名、成员资格、目标集合和目标权重只使用月末已完成Close及此前数据；成员状态使用前一XNYS交易日观察值。月中成分退出也只在当日完成Close后确认。；信号后的下一XNYS交易日按拆股及股息调整Open成交；信号Close先确定股数，成交时按稳定security_id顺序先卖后买，允许小数股，单边成本作为买高卖低的价格冲击。临时缺少真实Open时不在合成价格买卖；成员退出卖单逐日重试。历史证券最终无后续报价时，只允许用最后可见调整OHLC作明确标注的终止清算代理。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_pit_12_1_momentum_top10_top20_buffer_v1](ROT/ROT-v0.50b.1__26-08-29__nasdaq100_12_1_momentum_top10_top20_buffer/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:nasdaq100_pit_12_1_momentum_absolute_gate_factorial_v1 -->
### ROT-v0.50b.2 · Nasdaq-100 12-1排名绝对动量门槛消融 · `nasdaq100_pit_12_1_momentum_absolute_gate_factorial_v1`
- Experiment ID：`nasdaq100_pit_12_1_momentum_absolute_gate_factorial_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-29
- 策略：在每个日历月最后一个XNYS交易日收盘后，只从滞后一个交易日可知的Nasdaq-100历史时点成分证券中排名。横截面分数沿用父实验的12-1总收益动量，即上个月末相对再往前12个月末的拆股及股息调整Close收益，并另算跳过信号月的6-1收益。实验固定比较无绝对门槛、12-1为正、6-1为正、12-1与6-1同时为正四种资格；每种资格分别运行每月强制Top10与前10进入/跌出前20退出。绝对门槛先于排名与缓冲，一只原持仓若不再通过当月门槛，即使原始12-1排名仍在前20也必须退出。数据仍是candidate_pending_review，结论只属于探索研究。
- 标的：NASDAQ100_MOMENTUM_ROTATION
- 信号与成交：所有成员资格、12-1与6-1端点、绝对门槛、排名和目标权重都在月末已完成Close后确定；成员状态使用前一XNYS交易日观察值。月中成分退出也只在完成Close后确认。；信号后的下一XNYS交易日按拆股及股息调整Open成交；信号Close先确定股数，成交时按稳定security_id顺序先卖后买，允许小数股，单边成本作为买高卖低的价格冲击。临时缺少真实Open时不在合成价格成交；成员退出卖单逐日重试，终止报价只允许明确标注的卖出清算代理。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[nasdaq100_pit_12_1_momentum_absolute_gate_factorial_v1](ROT/ROT-v0.50b.2__26-08-29__nasdaq100_absolute_momentum_gate_factorial/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

## TIM · 简单择时与熊市对冲

研究 QQQ/SPY 的简单趋势择时、熊市识别和未来空仓期对冲配置。

<!-- EXPERIMENT:manual_qqq_close_schedule_2026q1_v1 -->
### TIM-v0.05 · QQQ 人工择时路径核算 · `manual_qqq_close_schedule_2026q1_v1`
- Experiment ID：`manual_qqq_close_schedule_2026q1_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-10
- 策略：从 2026-01-16 收盘时已经持有的 100 股 QQQ 出发，严格按照用户给出的日期，在每个所列交易日的复权收盘价全仓卖出或把全部现金买回；日期序列本身不由行情生成。
- 标的：QQQ
- 信号与成交：外部给定并视为在各成交日前已知的固定日期日程；不读取当日 Close 生成交易信号。；清单所列交易日的常规时段复权 Close；PyBroker 在前一交易日提交对应的预定指令。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[manual_qqq_close_schedule_2026q1_v1](TIM/TIM-v0.05__26-08-10__manual_qqq_close_schedule_2026q1/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:sma200_threshold_grid_v1 -->
### TIM-v0.10 · QQQ/SPY 收盘确认 SMA200 双阈值 · `sma200_threshold_grid_v1`
- Experiment ID：`sma200_threshold_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-08
- 策略：After a 200-session SMA warmup, enter only when the close crosses from at-or-below the upper SMA buffer to above it; exit whenever the close is below the lower SMA buffer. Signals are confirmed after the regular-session close and filled at the next regular-session open.
- 标的：QQQ, SPY
- 信号与成交：regular-session close after the bar is complete；next regular-session open。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[sma200_threshold_grid_v1](TIM/TIM-v0.10__26-08-08__sma200_threshold_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma200_threshold_grid_2021_2025_v1 -->
### TIM-v0.20a.1 · QQQ 日内动态 SMA200 网格（2021–2025） · `qqq_intraday_sma200_threshold_grid_2021_2025_v1`
- Experiment ID：`qqq_intraday_sma200_threshold_grid_2021_2025_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：SMA200 uses completed history through the prior close to solve the exact next-session price at which the provisional current SMA200 reaches each a/b buffer. Start flat. When flat and the prior completed close is at or below the completed buy boundary, place the ordinary upward trigger; when long, keep the ordinary downward trigger active. Optionally also place a correction buy c% above the last effective sell fill or a correction sell d% below the last effective buy fill, with c=d in this experiment. The first eligible trigger in the current position direction executes and the position becomes fully invested or fully flat.
- 标的：QQQ
- 信号与成交：All trigger eligibility and prices are known before each regular session using only completed closes through the preceding session plus prior effective account fills.；If the regular-session Open has already crossed a predeclared trigger, fill at Open; otherwise fill at the exact theoretical trigger when the adjusted regular-session OHLC touches it. Apply the configured adverse cost to the raw fill. At most one fill per session.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma200_threshold_grid_2021_2025_v1](TIM/TIM-v0.20a.1__26-08-13__qqq_intraday_sma200_threshold_grid_2021_2025/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma200_threshold_grid_2000_2004_v1 -->
### TIM-v0.20a.2 · QQQ 日内动态 SMA200 历史窗口（2000–2004） · `qqq_intraday_sma200_threshold_grid_2000_2004_v1`
- Experiment ID：`qqq_intraday_sma200_threshold_grid_2000_2004_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：SMA200 uses completed history through the prior close to solve the exact next-session price at which the provisional current SMA200 reaches each a/b buffer. Start flat. When flat and the prior completed close is at or below the completed buy boundary, place the ordinary upward trigger; when long, keep the ordinary downward trigger active. Optionally also place a correction buy c% above the last effective sell fill or a correction sell d% below the last effective buy fill, with c=d in this experiment. The first eligible trigger in the current position direction executes and the position becomes fully invested or fully flat.
- 标的：QQQ
- 信号与成交：All trigger eligibility and prices are known before each regular session using only completed closes through the preceding session plus prior effective account fills.；If the regular-session Open has already crossed a predeclared trigger, fill at Open; otherwise fill at the exact theoretical trigger when the adjusted regular-session OHLC touches it. Apply the configured adverse cost to the raw fill. At most one fill per session.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma200_threshold_grid_2000_2004_v1](TIM/TIM-v0.20a.2__26-08-13__qqq_intraday_sma200_threshold_grid_2000_2004/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma200_threshold_grid_2010_2014_v1 -->
### TIM-v0.20a.3 · QQQ 日内动态 SMA200 历史窗口（2010–2014） · `qqq_intraday_sma200_threshold_grid_2010_2014_v1`
- Experiment ID：`qqq_intraday_sma200_threshold_grid_2010_2014_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-13
- 策略：SMA200 uses completed history through the prior close to solve the exact next-session price at which the provisional current SMA200 reaches each a/b buffer. Start flat. When flat and the prior completed close is at or below the completed buy boundary, place the ordinary upward trigger; when long, keep the ordinary downward trigger active. Optionally also place a correction buy c% above the last effective sell fill or a correction sell d% below the last effective buy fill, with c=d in this experiment. The first eligible trigger in the current position direction executes and the position becomes fully invested or fully flat.
- 标的：QQQ
- 信号与成交：All trigger eligibility and prices are known before each regular session using only completed closes through the preceding session plus prior effective account fills.；If the regular-session Open has already crossed a predeclared trigger, fill at Open; otherwise fill at the exact theoretical trigger when the adjusted regular-session OHLC touches it. Apply the configured adverse cost to the raw fill. At most one fill per session.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma200_threshold_grid_2010_2014_v1](TIM/TIM-v0.20a.3__26-08-13__qqq_intraday_sma200_threshold_grid_2010_2014/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma200_robust_selection_v1 -->
### TIM-v0.30 · QQQ SMA200 多窗口稳健选参 · `qqq_intraday_sma200_robust_selection_v1`
- Experiment ID：`qqq_intraday_sma200_robust_selection_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：完全沿用既有盘前可解的动态 SMA200 双阈值交易语义，固定 c=d=5%、5 bps 单边成本，在 2000-01-03 至 2025-12-31 连续运行每组 a/b；持仓、现金、成本价和上次卖出价跨年份连续传递，再以滚动五年下四分位、空仓重启十年敏感性、局部平台、PBO 和 DSR 选择唯一稳健代表，同时详细展示全历史 CAGR 与 Sharpe 机械冠军作为参照。
- 标的：QQQ
- 信号与成交：每个常规交易日开盘前，只使用截至前一交易日 Close 的完成历史与既有实际账户成交价，解出当日全部资格和触发价。；若常规时段 Open 已越过预挂触发线则按 Open 成交，否则调整后日线 OHLC 触及时按精确理论线成交；随后施加单边5 bps不利成本；每日最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma200_robust_selection_v1](TIM/TIM-v0.30__26-08-14__qqq_intraday_sma200_robust_selection/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma200_forced_sell_sensitivity_v1 -->
### TIM-v0.30a.1 · QQQ SMA200 强制卖出敏感性 · `qqq_intraday_sma200_forced_sell_sensitivity_v1`
- Experiment ID：`qqq_intraday_sma200_forced_sell_sensitivity_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：完全沿用盘前可解的动态 SMA200 双阈值、日内触线/跳空 Open、每日最多一笔和实际成交价纠错锚；不重新搜索 a/b，只比较两组预先冻结的 a/b 下，强制买回 c 与强制卖出 d 独立开关及 d 的宽步长敏感性。
- 标的：QQQ
- 信号与成交：每个常规交易日开盘前，只使用截至前一交易日 Close 的完成历史与既有实际账户成交价，解出当日所有资格和触发价。；若常规时段 Open 已越过预挂触发线则按 Open 成交，否则调整后日线 OHLC 触及时按精确理论线成交；随后施加单边5 bps不利成本；每日最多一笔。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma200_forced_sell_sensitivity_v1](TIM/TIM-v0.30a.1__26-08-14__qqq_intraday_sma200_forced_sell_sensitivity/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear_market_event_sma200_hysteresis_portfolios_v1 -->
### TIM-v0.40 · 熊市事件 SMA200 滞回组合 · `bear_market_event_sma200_hysteresis_portfolios_v1`
- Experiment ID：`bear_market_event_sma200_hysteresis_portfolios_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-14
- 策略：只在12段事后标注并收紧为峰值至谷底的熊市窗口内持仓，比较三个固定候选池的无筛选路径与统一SMA200筛选路径。无筛选在每段熊市起点把当日已有价格的标的等权买入并持有到熊市结束；SMA路径在起点只纳入Close>SMA200的标的，途中使用SMA200上下2%滞回、最近一次状态切换成交价上下10%解锁和路径依赖的比例再分配。所有熊市之间保持现金。
- 标的：BEAR_EVENT_PORTFOLIOS
- 信号与成交：所有进入、退出和目标股数只使用当日常规时段已完成Close、当日及更早的SMA200、当时持仓市值与此前真实成交锚。熊市start/end来自事后人工区间，是外生oracle事件，不宣称当时可知。；所有Close确认目标在下一共同交易日按拆股及股息调整Open成交；同日先卖后买，买单按代码字母序，在现金不足时只成交可负担股数。单边成本作为买高卖低的成交价冲击。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear_market_event_sma200_hysteresis_portfolios_v1](TIM/TIM-v0.40__26-08-14__bear_market_event_sma200_hysteresis_portfolios/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:rklb_stochrsi_threshold_grid_v1 -->
### TIM-v0.60a.1 · RKLB Stochastic RSI 阈值与周期网格 · `rklb_stochrsi_threshold_grid_v1`
- Experiment ID：`rklb_stochrsi_threshold_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-16
- 策略：Compute unsmoothed Stochastic RSI from adjusted RKLB daily closes. The same period is used for Wilder RSI and for the rolling RSI minimum/maximum range. Starting flat, buy when the completed close has StochRSI at or below the configured buy threshold and sell when long and StochRSI is at or above the configured sell threshold; fill each signal at the next regular-session open.
- 标的：RKLB
- 信号与成交：regular-session close after the daily bar is complete；next regular-session open。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[rklb_stochrsi_threshold_grid_v1](TIM/TIM-v0.60a.1__26-08-16__rklb_stochrsi_threshold_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_timing_v1 -->
### TIM-v0.70 · QQQ 双周期 Stochastic RSI 三种择时语义 · `qqq_dual_stochrsi_timing_v1`
- Experiment ID：`qqq_dual_stochrsi_timing_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-24
- 策略：Use raw unsmoothed Stochastic RSI with periods 42 and 100 as a joint all-in/all-out QQQ timing signal. Compare three paired semantics: joint oversold/overbought level states, joint rolling extrema, and recovery/reversal crossings. Each next-session trigger price is solved only from completed data through the prior close. An opening gap beyond the trigger fills at Open; otherwise the trigger fills only when it lies on the directed Open-to-Close path. The market chart displays Close only.
- 标的：QQQ
- 信号与成交：Trigger prices and crossing eligibility are frozen before each regular session from adjusted daily data completed through the prior close.；Same regular session: Open when it has already gapped through the predeclared boundary; otherwise the exact boundary price only when the directed Open-to-Close segment reaches it. High and Low are not used to infer a touch.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_timing_v1](TIM/TIM-v0.70__26-08-24__qqq_dual_stochrsi_timing/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_cross_threshold_grid_full_history_v1 -->
### TIM-v0.70a.1 · QQQ 双周期 Stochastic RSI CROSS 全历史阈值网格 · `qqq_dual_stochrsi_cross_threshold_grid_full_history_v1`
- Experiment ID：`qqq_dual_stochrsi_cross_threshold_grid_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-24
- 策略：Extend the parent CROSS strategy to QQQ history from 2000 through the approved 2026-08-04 endpoint. Raw unsmoothed Stochastic RSI periods 42 and 100 jointly confirm each event. While flat, both completed prior values must be strictly below the candidate buy threshold and both provisional values must cross to or above it. While long, both completed prior values must be strictly above the candidate sell threshold and both provisional values must cross to or below it. Buy and sell thresholds are scanned independently over a frozen Cartesian grid. Trigger prices use only completed data through the prior close; opening gaps fill at Open and other fills require the directed Open-to-Close segment to reach the exact boundary. High and Low are ignored for touch inference.
- 标的：QQQ
- 信号与成交：Crossing eligibility and both period-specific trigger equations are frozen before each regular session using adjusted daily data completed through the prior close.；On the same regular session, fill at Open if it has already gapped through the frozen joint boundary; otherwise fill at the exact boundary only when the directed Open-to-Close segment reaches it. High and Low are not used to infer a touch.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_cross_threshold_grid_full_history_v1](TIM/TIM-v0.70a.1__26-08-24__qqq_stochrsi_cross_grid_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_cross_period_grid_full_history_v1 -->
### TIM-v0.70a.2 · QQQ 双周期 Stochastic RSI CROSS 周期网格 · `qqq_dual_stochrsi_cross_period_grid_full_history_v1`
- Experiment ID：`qqq_dual_stochrsi_cross_period_grid_full_history_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Keep the parent dual-StochRSI CROSS thresholds fixed at buy 0.20 and sell 0.80 while independently scanning one short and one long raw unsmoothed Stochastic RSI period. While flat, both completed prior values must be strictly below 0.20 and both provisional values must cross to or above 0.20. While long, both completed prior values must be strictly above 0.80 and both provisional values must cross to or below 0.80. Trigger prices use only completed data through the prior close; opening gaps fill at Open and other fills require the directed Open-to-Close segment to reach the exact joint boundary. High and Low are ignored for touch inference.
- 标的：QQQ
- 信号与成交：Crossing eligibility and both period-specific trigger equations are frozen before each regular session using adjusted daily data completed through the prior close.；On the same regular session, fill at Open if it has already gapped through the frozen joint boundary; otherwise fill at the exact boundary only when the directed Open-to-Close segment reaches it. High and Low are not used to infer a touch.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_cross_period_grid_full_history_v1](TIM/TIM-v0.70a.2__26-08-25__qqq_stochrsi_period_grid_full_history/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_cross_multistart_robustness_v1 -->
### TIM-v0.70a.3 · QQQ 双周期 Stochastic RSI 多起点稳健选参 · `qqq_dual_stochrsi_cross_multistart_robustness_v1`
- Experiment ID：`qqq_dual_stochrsi_cross_multistart_robustness_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Fix raw unsmoothed dual-StochRSI CROSS thresholds at buy 0.20 and sell 0.80, scan short periods 14–50 and long periods 70–160 by two, and restart every candidate from cash at deterministic quarterly five-year windows. Indicators may use approved history before each window for warmup, but cash, shares, and position state reset at every window start.
- 标的：QQQ
- 信号与成交：Eligibility and exact trigger equations use only data completed through the prior close, including pre-window history solely for indicator warmup.；Each window starts flat with 100000 dollars. On an eligible session, a gap beyond the frozen trigger fills at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_cross_multistart_robustness_v1](TIM/TIM-v0.70a.3__26-08-25__qqq_stochrsi_multistart_robustness/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_cross_multistart_full_2010_2026_v1 -->
### TIM-v0.70a.4 · QQQ 双周期 Stochastic RSI 2010–2026全样本多起点 · `qqq_dual_stochrsi_cross_multistart_full_2010_2026_v1`
- Experiment ID：`qqq_dual_stochrsi_cross_multistart_full_2010_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Fix dual raw StochRSI CROSS thresholds at buy 0.20 and sell 0.80 and scan short periods 14–50 against long periods 70–160 by two. Evaluate every pair on all quarterly-start five-year windows fully observable in QQQ from 2010 through 2026. Every window resets cash, shares, and position state while allowing pre-window history only for indicator warmup.
- 标的：QQQ
- 信号与成交：Eligibility and exact triggers are frozen before each session using completed prior data; pre-window data warms indicators but never creates inherited account state.；Every window starts with 100000 dollars cash and no shares. Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_cross_multistart_full_2010_2026_v1](TIM/TIM-v0.70a.4__26-08-25__qqq_stochrsi_multistart_full_2010_2026/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_cross_multihorizon_robustness_v1 -->
### TIM-v0.70a.5 · QQQ 双周期 Stochastic RSI 多维稳健筛选 · `qqq_dual_stochrsi_cross_multihorizon_robustness_v1`
- Experiment ID：`qqq_dual_stochrsi_cross_multihorizon_robustness_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Fix CROSS thresholds at 0.20/0.80 and scan 874 short/long period pairs over every fully observable quarterly restart using 3-, 5-, and 7-year accounts. Select only through cross-horizon downside ranks, chronological regimes, leave-one-start-year-out stability, benchmark comparisons, and a complete local 3x3 neighborhood.
- 标的：QQQ
- 信号与成交：Eligibility and exact triggers use only completed prior data; pre-window history only warms indicators.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_cross_multihorizon_robustness_v1](TIM/TIM-v0.70a.5__26-08-25__qqq_stochrsi_multihorizon_robustness/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_robust_train_2005_2020_oos_2021_2026_v1 -->
### TIM-v0.70a.6 · QQQ Stochastic RSI 2005–2020稳健训练与2021–2026锁定验证 · `qqq_dual_stochrsi_robust_train_2005_2020_oos_2021_2026_v1`
- Experiment ID：`qqq_dual_stochrsi_robust_train_2005_2020_oos_2021_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Select the 14–50 by 70–160 period pair using only 2005–2020 and the frozen multidimensional robustness method, then evaluate the frozen pair once on the untouched continuous 2021–2026 account.
- 标的：QQQ
- 信号与成交：Eligibility and exact triggers use only completed prior data. No observation dated 2021 or later may enter selection.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_robust_train_2005_2020_oos_2021_2026_v1](TIM/TIM-v0.70a.6__26-08-25__qqq_stochrsi_2005_2020_train_2021_2026_oos/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_narrow_robust_2000_2015_v1 -->
### TIM-v0.70a.7 · QQQ 双周期 Stochastic RSI 2000–2015窄网格稳健选参 · `qqq_dual_stochrsi_narrow_robust_2000_2015_v1`
- Experiment ID：`qqq_dual_stochrsi_narrow_robust_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Use only 2000–2015 to compare every integer short period from 20 through 30 and long period from 85 through 95 under the fixed raw dual-StochRSI 0.20/0.80 CROSS rule, selecting for joint CAGR, Sharpe, benchmark wins, chronological stability, leave-year-out stability, and a complete 3x3 neighborhood.
- 标的：QQQ
- 信号与成交：Eligibility and exact trigger prices use completed prior data only; all selection windows end no later than 2015-12-31.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_narrow_robust_2000_2015_v1](TIM/TIM-v0.70a.7__26-08-25__qqq_stochrsi_narrow_robust_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_first_entry_aligned_broad_robust_2000_2015_v1 -->
### TIM-v0.70a.8 · QQQ Stochastic RSI 2000–2015首次入场对齐大范围稳健选参 · `qqq_dual_stochrsi_first_entry_aligned_broad_robust_2000_2015_v1`
- Experiment ID：`qqq_dual_stochrsi_first_entry_aligned_broad_robust_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Use only 2000–2015 to scan every integer short period from 14 through 50 and long period from 70 through 160 under the fixed raw dual-StochRSI 0.20/0.80 CROSS rule. In every restart window the strategy begins flat. Its candidate-specific Buy & Hold comparator also remains in cash until that candidate's first actual buy fill, then buys once at the identical cost-inclusive fill price and holds through the window end. If no buy occurs, both remain in cash.
- 标的：QQQ
- 信号与成交：Eligibility and exact trigger prices use completed prior data only; every selection window ends no later than 2015-12-31.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_first_entry_aligned_broad_robust_2000_2015_v1](TIM/TIM-v0.70a.8__26-08-25__qqq_stochrsi_first_entry_aligned_broad_robust_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_dual_stochrsi_supergrid_heatmaps_2000_2015_v1 -->
### TIM-v0.70a.9 · QQQ Stochastic RSI 2000–2015超大网格四参数面 · `qqq_dual_stochrsi_supergrid_heatmaps_2000_2015_v1`
- Experiment ID：`qqq_dual_stochrsi_supergrid_heatmaps_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：Use only 2000–2015 to scan every valid integer pair with short StochRSI period 2 through 70, long period 50 through 150, and short strictly below long. Period zero is undefined and period one is a degenerate zero-width StochRSI window; both are excluded by the shared indicator contract. The fixed raw dual-StochRSI 0.20/0.80 CROSS rule and candidate-specific first-entry-aligned Buy & Hold comparator are unchanged.
- 标的：QQQ
- 信号与成交：Eligibility and exact trigger prices use completed prior data only; every scoring window ends no later than 2015-12-31.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_dual_stochrsi_supergrid_heatmaps_2000_2015_v1](TIM/TIM-v0.70a.9__26-08-26__qqq_stochrsi_supergrid_heatmaps_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_single_stochrsi_robust_2005_2020_v1 -->
### TIM-v0.70b.1 · QQQ 单周期 Stochastic RSI 2005–2020稳健选参 · `qqq_single_stochrsi_robust_2005_2020_v1`
- Experiment ID：`qqq_single_stochrsi_robust_2005_2020_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Use one raw StochRSI only. Scan every integer period from 14 through 200 with fixed 0.20 upward-cross entry and 0.80 downward-cross exit, selecting on 2005–2020 through the same cross-horizon, chronological, leave-year-out, benchmark, and local-neighborhood robustness framework.
- 标的：QQQ
- 信号与成交：Eligibility and the exact trigger use only completed prior data. Selection windows end no later than 2020-12-31.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_single_stochrsi_robust_2005_2020_v1](TIM/TIM-v0.70b.1__26-08-25__qqq_single_stochrsi_robust_2005_2020/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_bear9_drift_comparison_v1 -->
### TIM-v0.70c.1 · QQQ双StochRSI与Bear9漂移再平衡比较 · `qqq_stochrsi_bear9_drift_comparison_v1`
- Experiment ID：`qqq_stochrsi_bear9_drift_comparison_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：Compare QQQ Buy & Hold with two frozen dual raw-StochRSI close-cross timing pairs, 12/61 and 62/111. Each timing path starts in cash and buys QQQ only after both completed StochRSI values cross upward through 0.20 on the same close; it exits after both cross downward through 0.80 on the same close. Two companion paths use the identical timing state but hold the fixed Bear9 basket whenever QQQ is not held, including before the first QQQ entry. Bear9 is AZO/SO/ED/ORLY/MO/WRB/DLTR/DG/WMT at 10/12/13/10/13/12/8/9/13 percent. While Bear9 is active, if any tradable member's completed-close total-account weight differs from its frozen target by strictly more than three percentage points, the entire available basket is restored to target weights at the next common Open. DG's unavailable prelisting sleeve remains cash.
- 标的：QQQ_STOCHRSI_BEAR9
- 信号与成交：All StochRSI crossings, tradable-member changes, portfolio weights, and three-percentage-point drift checks use the completed adjusted Close and information available by that Close only.；Every target change executes at the next common adjusted Open with sells before buys and one-sided price-impact costs. No same-session trigger fill is used in this experiment.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_bear9_drift_comparison_v1](TIM/TIM-v0.70c.1__26-08-26__qqq_stochrsi_bear9_drift_comparison/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:rklb_intraday_sma_threshold_grid_v1 -->
### TIM-v0.60b.1 · RKLB 日内动态 SMA 双阈值网格 · `rklb_intraday_sma_threshold_grid_v1`
- Experiment ID：`rklb_intraday_sma_threshold_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-16
- 策略：For each SMA window n, solve before every session the exact price at which the provisional current-day SMA(n), formed from the prior n-1 completed closes plus the hypothetical current price, is 1%, 2%, or 3% above or below price. Start flat. While flat, buy on an upward crossing of the selected upper buffer; while long, sell on a downward crossing of the selected lower buffer. Overnight gaps that already satisfy a live trigger execute at the regular-session Open; an intraday High/Low touch executes at the solved trigger price. At most one fill is allowed per session.
- 标的：RKLB
- 信号与成交：Trigger eligibility and exact prices are computed before each regular session using only completed closes through the preceding session and current position state.；Same-session regular-hours execution: gap-through fills at Open; otherwise an OHLC touch fills at the exact predeclared trigger. One adverse cost is applied per side and at most one fill occurs per session.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[rklb_intraday_sma_threshold_grid_v1](TIM/TIM-v0.60b.1__26-08-16__rklb_intraday_sma_threshold_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_period_cross_locked_2016_2026_v1 -->
### TIM-v0.50a.1 · QQQ 独立买卖 SMA 周期锁定样本外 · `qqq_intraday_sma_period_cross_locked_2016_2026_v1`
- Experiment ID：`qqq_intraday_sma_period_cross_locked_2016_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：把 TIM-v0.50 在2000-2015探索期机械选出的历史诊断锚点完整冻结为 c=3%、d关闭、买入SMA310、卖出SMA190，并从2016年第一个交易日起以独立空仓状态运行到批准数据末日。另运行买入SMA270-350与卖出SMA150-230、步长10的81组预登记局部扰动，但这些扰动只描述锚点附近的稳定性，禁止根据2016年后的结果替换310/190。
- 标的：QQQ
- 信号与成交：每个常规交易日开盘前，只使用截至前一交易日 Close 的完成历史、前一完成SMA状态和既有实际账户成交价，冻结当日资格与触发价；2016年前数据只用于均线预热，窗口前禁止交易。；若常规时段 Open 已越过预挂触发线则按 Open 成交；否则调整后日线 OHLC 触及时按精确理论线成交；随后施加单边5 bps不利成本。每日最多一笔，不允许买入后同日卖出或卖出后同日买回。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_period_cross_locked_2016_2026_v1](TIM/TIM-v0.50a.1__26-08-15__qqq_intraday_sma_period_cross_locked_2016_2026/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear_selected8_sma200_trailing_stop_grid_v1 -->
### TIM-v0.40a.2 · 八标的 SMA200 峰值回撤阈值网格 · `bear_selected8_sma200_trailing_stop_grid_v1`
- Experiment ID：`bear_selected8_sma200_trailing_stop_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：只在既有12段事后峰值至谷底熊市窗口内，分别回测LMT、EQT、ORLY、AZO、TLT、COR、SO、HRL八个单标的100%仓位路径，以及八标的当前有效成员等权组合。全部路径保留SMA200上下3%滞回，比较关闭峰值回撤强制退出与3%至20%、步长1%的18个阈值。峰值退出后允许再次买入，但必须先在本次空仓期观察到Close位于SMA200×1.03之下或等于上轨，随后再严格上穿上轨。熊市之间全现金，星标和双坑位完全移除。
- 标的：BEAR_SELECTED8_STOP_GRID
- 信号与成交：只使用当日常规时段已完成Close、当日及更早的SMA200、此前真实状态进入成交价和此前完成Close形成的运行峰值。12段熊市start/end为外生事后oracle，不宣称当时可知。；所有Close确认目标在下一共同交易日按拆股及股息调整Open成交；同日先卖后买，代码字母序执行，单边成本通过买高卖低的成交价冲击计入。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear_selected8_sma200_trailing_stop_grid_v1](TIM/TIM-v0.40a.2__26-08-15__bear_selected8_sma200_trailing_stop_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_intraday_sma_period_cross_grid_1999_2015_v1 -->
### TIM-v0.50 · QQQ 独立买卖 SMA 周期与纠错网格 · `qqq_intraday_sma_period_cross_grid_1999_2015_v1`
- Experiment ID：`qqq_intraday_sma_period_cross_grid_1999_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：QQQ 初始空仓。买入与卖出分别使用可独立选择的长期 SMA 周期；每个交易日开盘前只用截至前一收盘的完成历史，解出当日价格等于相应 provisional SMA 的精确触发价。普通买入必须从前一日位于买入 SMA 下方或相等开始向上穿越，普通卖出必须从前一日位于卖出 SMA 上方或相等开始向下穿越。卖出后保留基于实际计成本卖出价的强制买回线，买入后可选基于实际计成本买入价的止损线。
- 标的：QQQ
- 信号与成交：每个常规交易日开盘前，只使用截至前一交易日 Close 的完成历史、前一完成 SMA 状态和既有实际账户成交价，冻结当日资格与触发价；当日 Close 不参与当日订单。；若常规时段 Open 已越过预挂触发线则按 Open 成交；否则调整后日线 OHLC 触及时按精确理论线成交；随后施加单边 5 bps 不利成本。每日最多一笔，不允许买入后同日止损或卖出后同日买回。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_intraday_sma_period_cross_grid_1999_2015_v1](TIM/TIM-v0.50__26-08-15__qqq_intraday_sma_period_cross_grid_1999_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_recovery_probation_grid_2000_2015_v1 -->
### TIM-v0.50b.1 · QQQ 双均线恢复试探与失败退出网格 · `qqq_sma_recovery_probation_grid_2000_2015_v1`
- Experiment ID：`qqq_sma_recovery_probation_grid_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-21
- 策略：QQQ 初始空仓，以较短的卖出 SMA 负责下跌时快速退出，以较长的买入 SMA 负责熊市后的恢复入场。普通买入必须由完成收盘确认从长均线下方向上穿越，并在下一交易日 Open 成交；任何新买入先进入试探持仓。普通买入若在站上短均线前完成收盘重新落到长均线下方，则下一 Open 失败退出。短均线卖出后的 3% 强制买回每个熊市阶段最多一次；其试探持仓若在完成普通长均线上穿前收盘跌回原短均线卖出计成本成交价下方，则下一 Open 失败退出。试探持仓完成收盘站上短均线后升级为确认多头，之后按盘前可知的动态短均线向下穿越快速卖出并开启新的熊市阶段。
- 标的：QQQ
- 信号与成交：普通长均线买入、试探失败和试探转为确认多头均只在常规交易日 Close 完成后确认；确认多头的下一日短均线卖出线和未使用的 c 买回线在开盘前只用已完成收盘、均线与既有账户成交价冻结。；所有完成 Close 才确认的普通买入和试探失败信号最早在下一常规交易日 Open 成交。盘前冻结的 sell-SMA 与 c 触发线若 Open 已越线则按 Open，否则按调整后日线 Low/High 首次可判定触线的理论价成交；随后施加单边 5 bps 不利成本。每日最多一笔，不利用当日收盘结果追溯获得更早价格。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_recovery_probation_grid_2000_2015_v1](TIM/TIM-v0.50b.1__26-08-21__qqq_sma_recovery_probation_grid_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_sma_recovery_probation_role_grid_2000_2015_v1 -->
### TIM-v0.50b.2 · QQQ 双均线恢复试探角色约束网格 · `qqq_sma_recovery_probation_role_grid_2000_2015_v1`
- Experiment ID：`qqq_sma_recovery_probation_role_grid_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-22
- 策略：QQQ 初始空仓，以较短的卖出 SMA 负责下跌时快速退出，以较长的买入 SMA 负责熊市后的恢复入场。普通买入必须由完成收盘确认从长均线下方向上穿越，并在下一交易日 Open 成交；任何新买入先进入试探持仓。普通买入若在站上短均线前完成收盘重新落到长均线下方，则下一 Open 失败退出。短均线卖出后的 3% 强制买回每个熊市阶段最多一次；其试探持仓若在完成普通长均线上穿前收盘跌回原短均线卖出计成本成交价下方，则下一 Open 失败退出。试探持仓完成收盘站上短均线后升级为确认多头，之后按盘前可知的动态短均线向下穿越快速卖出并开启新的熊市阶段。完整计算80至450日的全部买卖周期对，但只有买入周期严格长于卖出周期的组合可以参与选参和统计门禁。
- 标的：QQQ
- 信号与成交：普通长均线买入、试探失败和试探转为确认多头均只在常规交易日 Close 完成后确认；确认多头的下一日短均线卖出线和未使用的 c 买回线在开盘前只用已完成收盘、均线与既有账户成交价冻结。；所有完成 Close 才确认的普通买入和试探失败信号最早在下一常规交易日 Open 成交。盘前冻结的 sell-SMA 与 c 触发线若 Open 已越线则按 Open，否则按调整后日线 Low/High 首次可判定触线的理论价成交；随后施加单边 5 bps 不利成本。每日最多一笔，不利用当日收盘结果追溯获得更早价格。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_sma_recovery_probation_role_grid_2000_2015_v1](TIM/TIM-v0.50b.2__26-08-22__qqq_sma_recovery_probation_role_grid_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear_market_weighted_trailing_stop_ablation_v1 -->
### TIM-v0.40a.1 · 熊市双坑位与峰值回撤止损消融 · `bear_market_weighted_trailing_stop_ablation_v1`
- Experiment ID：`bear_market_weighted_trailing_stop_ablation_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：只在既有12段事后峰值至谷底熊市窗口内交易15个固定候选。所有case使用SMA200上下3%滞回且取消成交价10%锁：熊市起点以Close高于SMA200×1.03的水平条件建仓，途中空仓标的必须先处于上轨之下再严格上穿上轨才可进入，持仓标的Close低于SMA200×0.97退出。以每次真实状态进入成交价和随后完成Close的最高值维护持仓峰值；比较关闭强制退出和从峰值严格回撤超过6%、8%、10%、12%时退出。另比较全部标的一坑与五个星标标的双坑的完整二维消融。熊市之间全现金。
- 标的：BEAR_EVENT_WEIGHTED_STOPS
- 信号与成交：只使用当日常规时段已完成Close、当日及更早SMA200、此前实际状态进入成交和此前完成Close形成的运行峰值。熊市start/end为外生事后oracle，不宣称当时可知。；所有Close确认目标在下一共同交易日按拆股及股息调整Open成交；同日先卖后买，代码字母序执行，单边成本通过买高卖低的成交价冲击计入。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear_market_weighted_trailing_stop_ablation_v1](TIM/TIM-v0.40a.1__26-08-15__bear_market_weighted_trailing_stop_ablation/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_full_position_sma_trend_quality_training_2000_2015_v1 -->
### TIM-v0.20b.1 · QQQ 满仓长期斜率与短趋势质量训练 · `qqq_full_position_sma_trend_quality_training_2000_2015_v1`
- Experiment ID：`qqq_full_position_sma_trend_quality_training_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：QQQ只在0%和100%仓位之间切换。基础价格状态要求完成Close高于长期SMA；因子2要求长期SMA在指定回看期内的平均每日百分比斜率严格高于门槛；因子4对短期SMA最近若干完成值的对数做OLS回归，以每日对数斜率乘R²衡量趋势方向和质量，并要求严格高于门槛。主策略P24同时启用因子2和4；因子5只在P245消融中要求QQQ/SPY比值的指定期对数动量严格为正。所有条件都是可重复进入的状态门槛，不要求每次重新上穿SMA；入场可要求连续若干日合格，任一已启用条件单日失效即退出。
- 标的：QQQ
- 信号与成交：所有SMA、长期斜率、短趋势回归和QQQ/SPY相对强弱只使用当日常规时段完成Close及更早数据；当日Close完成后才能形成信号。；所有买卖信号最早在下一QQQ/SPY共同交易日的拆股及股息调整Open成交；每边成本作为买高卖低的成交价冲击，禁止同日Close成交和PyBroker默认middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_full_position_sma_trend_quality_training_2000_2015_v1](TIM/TIM-v0.20b.1__26-08-15__qqq_full_position_sma_trend_quality_training_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015_v1 -->
### TIM-v0.20b.2 · QQQ 满仓F2/F4四维边界扩展 · `qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015_v1`
- Experiment ID：`qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-15
- 策略：完全复用父实验P24的QQQ 0%/100%状态策略，只扩展父训练代表触及边界的四个参数：长期SMA窗口L、长期斜率回看K、F2每日斜率门槛和短期SMA窗口S。基础状态要求完成Close高于SMA_L；F2=((SMA_L(t)/SMA_L(t-K))-1)/K×100严格高于门槛；F4对最近W个完成log(SMA_S)做OLS，以每日斜率×R²×100衡量短趋势质量并严格高于门槛。W=10、F4门槛=0、连续入场确认C=2保持父代表不变。F5不参与扩边选参，只在新P24代表冻结后以R=120作固定消融。
- 标的：QQQ
- 信号与成交：所有SMA、长期斜率、短趋势回归和固定F5消融只使用当日常规时段完成Close及更早数据；当日Close完成后才能形成信号。；所有信号最早在下一QQQ/SPY共同交易日的拆股及股息调整Open成交；每边成本作为买高卖低的成交价冲击，禁止同日Close成交和PyBroker默认middle price。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015_v1](TIM/TIM-v0.20b.2__26-08-15__qqq_full_position_sma_trend_quality_boundary_expansion_2000_2015/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear24_atr_hard_stop_policy_ablation_v1 -->
### TIM-v0.40b.1 · 24标的 ATR 固定硬止损四政策消融 · `bear24_atr_hard_stop_policy_ablation_v1`
- Experiment ID：`bear24_atr_hard_stop_policy_ablation_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-21
- 策略：只在既有12段事后峰值至谷底熊市窗口内，分别对24个单标的100%仓位路径，以及核心12、近核心8、零售4三个固定等权分组，比较四种冻结政策：A不使用SMA或止损并持有到熊市结束；B使用SMA200上下3%完整择时；C不使用普通SMA退出，按每次真实入场时的Wilder ATR20设置12%至20%的固定硬止损，止损后在后续完成Close重新高于原止损线时买回；D与C使用相同硬止损，但止损后必须先在SMA200上方3%水平之下或等于该水平完成重新武装，再严格上穿才买回。10%成交锁和星标权重均关闭。
- 标的：BEAR24_ATR_ABLATION
- 信号与成交：熊市start/end来自事后人工区间，是外生oracle事件，不宣称当时可知。SMA与重新买入只使用当日常规时段已完成Close和更早历史；ATR止损线只使用买入信号日已经完成的ATR20与下一日真实Open成交价。固定止损在入场成交后成为已知挂单水平，之后只用当日Open/Low判断是否触发，不使用当日High或Close抬高止损。；Close确认的初始买入、SMA买卖、止损后重新买入和熊市结束清仓均在下一共同交易日的拆股及股息调整Open成交。固定硬止损可在持仓当日成交：若Open已跌破则按Open，否则Low触线则按固定止损线；入场当日先按Open买入，随后硬止损立即生效，若当日Low触线可同日卖出。单边成本通过买高、卖低调整每笔成交价。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear24_atr_hard_stop_policy_ablation_v1](TIM/TIM-v0.40b.1__26-08-21__bear24_atr_hard_stop_ablation/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear24_sma_window_plateau_v1 -->
### TIM-v0.40b.2 · 24标的完整SMA窗口高原诊断 · `bear24_sma_window_plateau_v1`
- Experiment ID：`bear24_sma_window_plateau_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-21
- 策略：只在既有12段事后峰值至谷底熊市窗口内，对24个单标的100%独立账户和核心12、近核心8、零售4三个固定等权袖套分组，比较A全程持有与28条完整SMA择时路径。SMA窗口从30到300、步长10；每条SMA路径使用同一条均线的上下3%滞回，允许卖出后重新买入。所有窗口共用SMA300预热样本，ATR硬止损、峰值回撤止损、10%成交锁和星标权重均关闭。
- 标的：BEAR24_SMA_WINDOW_GRID
- 信号与成交：熊市start/end来自事后人工区间，是外生oracle事件，不宣称当时可知。SMA_N只使用当日常规时段已完成Close及更早历史；共同可比样本要求该标的在熊市start已经完成SMA300预热。；熊市初始买入、SMA买卖、重新买入和熊市结束清仓，均在信号后的下一共同交易日拆股及股息调整Open成交；单边成本通过买高、卖低调整每笔成交价。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear24_sma_window_plateau_v1](TIM/TIM-v0.40b.2__26-08-21__bear24_sma_window_plateau/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:bear6_sma_fine_window_scan_v1 -->
### TIM-v0.40b.3 · 六标的SMA局部细网格诊断 · `bear6_sma_fine_window_scan_v1`
- Experiment ID：`bear6_sma_fine_window_scan_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-24
- 策略：只在父实验相同的12段事后峰值至谷底熊市窗口内，对AZO、TLT、SO、ED、MO、GIS六个独立100%账户比较A全程持有与标的专属SMA细网格。AZO扫描190至270、TLT扫描130至200、SO与ED扫描1至50、MO扫描80至120，以上步长均为1；GIS扫描270至500、步长10。每条SMA路径使用同一条均线的上下3%滞回并允许卖出后重新买入；全部路径共用SMA500预热样本。用户给SO/ED的下界0解释为横轴起点，SMA0不存在，因此从最小有效正周期SMA1开始；SMA1在3%上轨规则下预期无法入场，作为明确的退化边界保留。所有止损、10%成交锁、星标和分组资金再分配均关闭。
- 标的：BEAR6_SMA_FINE_WINDOW_GRID
- 信号与成交：熊市start/end来自事后人工区间，是外生oracle事件，不宣称当时可知。SMA_N只使用当日常规时段已完成Close及更早历史；全部六个标的和所有窗口统一要求熊市start时已经完成SMA500预热。；熊市初始买入、SMA买卖、重新买入和熊市结束清仓，均在信号后的下一共同交易日拆股及股息调整Open成交；单边成本通过买高、卖低调整每笔成交价。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[bear6_sma_fine_window_scan_v1](TIM/TIM-v0.40b.3__26-08-24__bear6_sma_fine_window_scan/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:trio_sma200_flat_spell_attribution_v1 -->
### TIM-v0.40c.1 · MO/AZO/TLT SMA200空仓期归因 · `trio_sma200_flat_spell_attribution_v1`
- Experiment ID：`trio_sma200_flat_spell_attribution_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：对MO、AZO、TLT分别在各自完整批准历史上运行独立的SMA200上下3%全仓择时。SMA200首次形成时只建立前一状态并保持空仓；空仓时，只有前一完成Close不高于前一SMA200×1.03、当日完成Close严格高于当日SMA200×1.03，才产生买入信号。持仓时，当日完成Close严格低于当日SMA200×0.97产生卖出信号。卖出后可无限次按同一上穿规则重新买入。所有信号下一标的交易日拆股及股息调整Open成交；没有熊市oracle、止损、成交锁、星标、组合资金再分配或参数扫描。
- 标的：MO, AZO, TLT
- 信号与成交：SMA200、上下轨和买卖状态只使用当日常规时段已完成Close及更早数据；主观大小熊区间只在成交完成后的事件归因中标记重叠，不参与任何信号。；买卖信号均在当日Close完成后确认，并在下一标的有效交易日的拆股及股息调整Open成交；单边成本通过买高、卖低调整成交价。卖出成交Open至下一买入成交Open定义为一段空仓期。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[trio_sma200_flat_spell_attribution_v1](TIM/TIM-v0.40c.1__26-08-25__trio_sma200_flat_spell_attribution/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_flat_trio_substitution_v1 -->
### TIM-v0.40c.2 · QQQ空仓期三替代标的择时 · `qqq_flat_trio_substitution_v1`
- Experiment ID：`qqq_flat_trio_substitution_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：以QQQ的SMA200上下3%滞回择时作为账户总开关。QQQ空仓时分别建立三条互斥替代路径：AZO使用SMA237、TLT使用SMA160、MO使用SMA110，替代品也采用上下3%滞回；替代品状态连续计算，QQQ空仓且替代品处于可持有状态时全仓持有该替代品，否则持有现金。QQQ重新进入可持有状态时优先恢复QQQ。所有信号使用完成Close并在下一QQQ共同交易日调整Open卖出在先、买入在后；无止损、成交锁、熊市oracle或样本末强制清仓。
- 标的：QQQ_FLAT_SUBSTITUTION
- 信号与成交：QQQ与替代品均只用各自当日常规时段已完成的拆股及股息调整Close和更早数据计算SMA、上下轨与持有状态。主观熊市区间仅用于事后标记，不参与信号。；信号在完成Close后确认，下一QQQ共同交易日的调整Open成交。同一Open发生资产切换时先把旧资产全部卖出，再用扣除单边成本后的全部现金买入新资产；允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_flat_trio_substitution_v1](TIM/TIM-v0.40c.2__26-08-25__qqq_flat_trio_substitution/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_flat_expanded_substitution_v1 -->
### TIM-v0.40c.3 · QQQ空仓期七替代候选扩展 · `qqq_flat_expanded_substitution_v1`
- Experiment ID：`qqq_flat_expanded_substitution_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：以QQQ的SMA200上下3%滞回择时作为账户总开关。QQQ空仓时分别建立七条互斥替代路径：AZO使用SMA237、TLT使用SMA160、MO使用SMA110、EQT使用SMA240、WMT使用SMA30、ORLY使用SMA260、LMT使用SMA30；替代品也采用上下3%滞回，状态在QQQ持仓期间继续计算。QQQ空仓且指定替代品处于可持有状态时全仓持有该替代品，否则持有现金；QQQ重新转强时优先恢复QQQ。所有信号使用完成Close并在下一QQQ共同交易日调整Open卖出在先、买入在后；无止损、成交锁、熊市oracle或样本末强制清仓。
- 标的：QQQ_FLAT_SUBSTITUTION
- 信号与成交：QQQ与替代品均只用各自当日常规时段已完成的拆股及股息调整Close和更早数据计算SMA、上下轨与持有状态。主观熊市区间仅用于事后标记和剔除2000汇总，不参与信号。；信号在完成Close后确认，下一QQQ共同交易日的调整Open成交。同一Open发生资产切换时先把旧资产全部卖出，再用扣除单边成本后的全部现金买入新资产；允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_flat_expanded_substitution_v1](TIM/TIM-v0.40c.3__26-08-25__qqq_flat_expanded_substitution/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_flat_bear24_sma_ablation_v1 -->
### TIM-v0.40c.4 · QQQ空仓期24替代标的SMA筛选消融 · `qqq_flat_bear24_sma_ablation_v1`
- Experiment ID：`qqq_flat_bear24_sma_ablation_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：以QQQ的SMA200上下3%滞回择时作为账户总开关。恢复核心12、近核心8和零售4共24只替代标的，并为每只建立两条独立路径：DIRECT在QQQ空仓时只要标的已有价格就直接满仓持有；SMA路径还要求标的通过其已冻结的自身SMA上下3%滞回状态，否则持有现金。替代品状态在QQQ持仓期间继续计算，QQQ重新转强时优先恢复QQQ。所有信号使用完成Close，下一全体资产共同交易日调整Open卖出在先、买入在后；无止损、成交锁、熊市oracle或样本末强制清仓。
- 标的：QQQ_FLAT_SUBSTITUTION
- 信号与成交：QQQ与SMA筛选路径均只用当日常规时段已完成的拆股及股息调整Close和更早数据。DIRECT没有替代品技术信号。主观熊市区间仅用于事后标记和剔除2000汇总，不参与交易信号。；信号在完成Close后确认，下一全体24只标的共同拥有价格的QQQ交易日调整Open成交。同一Open发生资产切换时先卖旧仓，再用扣除单边成本后的全部现金买入新仓；允许小数股。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_flat_bear24_sma_ablation_v1](TIM/TIM-v0.40c.4__26-08-25__qqq_flat_bear24_sma_ablation/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_timing_bear9_rebalance_robustness_v1 -->
### TIM-v0.40d.1 · QQQ六择时器Bear9与再平衡稳健性 · `qqq_timing_bear9_rebalance_robustness_v1`
- Experiment ID：`qqq_timing_bear9_rebalance_robustness_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：比较六个QQQ长期均线择时器：SMA160、180、200、220、240均使用完成Close相对均线上下3%的连续滞回状态；第六个使用SMA190与SMA310的分层仓位状态机。分层状态在SMA310不低于SMA190时持有0% QQQ；只有SMA190高于SMA310时才按完成Close所在区间持有0%、30%、70%或100% QQQ。每个对称择时器都比较非QQQ仓位留现金与改持固定Bear9；分层择时器比较全部留现金、只在QQQ为0%时持Bear9、以及用Bear9填满所有非QQQ仓位。Bear9固定为AZO/SO/ED/ORLY/MO/WRB/DLTR/DG/WMT，权重10/12/13/10/13/12/8/9/13，不使用任何个股SMA。Bear9内部除不再平衡外，还按1、2、3、4、5、6、8、10、15、20、25或30个共同交易日恢复目标权重。所有状态使用完成Close，下一共同Open先卖后买；允许小数股、无融资、现金不计息。
- 标的：QQQ_BEAR9_REBALANCE
- 信号与成交：所有均线、价格区间、QQQ目标仓位和周期性再平衡资格都在当日常规时段完成Close后计算，只使用该日及更早的拆股和股息调整数据。；完成Close确认后的目标在下一共同交易日调整Open执行；同一开盘先按字母顺序卖出超额份额，再按字母顺序使用可用现金买入不足份额并施加单边成本。。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_timing_bear9_rebalance_robustness_v1](TIM/TIM-v0.40d.1__26-08-25__qqq_timing_bear9_rebalance_robustness/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_scaled_accumulation_virtual_pools_2015_2026_v1 -->
### TIM-v0.80 · QQQ StochRSI 分档补仓与双虚拟池减仓 · `qqq_stochrsi_scaled_accumulation_virtual_pools_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_scaled_accumulation_virtual_pools_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：A zero-cost, long-only QQQ state machine using raw unsmoothed Stochastic RSI periods 42 and 100. Every completed Close can cause a same-Close fill. Extreme-low buy rule B2 supersedes ordinary-low buy rule B1. B1 spends max(5% of pre-buy cash, one quarter of the cycle's first B1 amount a after a exists). B2 spends 20%, 30%, 40%, then 30% of pre-buy cash; its fourth amount defines buy_b and later B2 buys spend max(30% of cash, 60% of buy_b). Any minimum shortfall is an external contribution, mirrored into modified Buy & Hold. Every actual sale resets a, buy_b, and the B2 count. Sell mechanism A arms when StochRSI100 first closes above 0.80, clears pool_B without selling, disables mechanism B, and earmarks 1% of total QQQ shares per above-0.80 Close into pool_A. While armed and StochRSI100 is in [0.50,0.80], each Close sells 20% of remaining pool_A. An armed strict downcross below 0.50 sells all pool_A plus 65% of other shares. A strict fast-drop override, prior StochRSI100 >0.60 and current <0.30, instead fills that same sale at the theoretical price solving provisional StochRSI100=0.30, without an OHLC-touch gate, then permits a normal same-Close buy. When A is not armed, mechanism B adds 10% of shares outside pool_B per Close while QQQ weight exceeds 75% and StochRSI42 >0.80; a strict downcross below 0.70 sells min(pool_B, 55% of total shares) and clears pool_B regardless of the cap.
- 标的：QQQ
- 信号与成交：Raw StochRSI42/100 state and ordinary crossings are confirmed using the completed current adjusted Close. The fast-drop theoretical 0.30 price uses indicator state completed through the prior Close plus the frozen same-day provisional-price equation.；Ordinary buys and sells fill at the same adjusted Close that confirms the signal, an idealized market-on-close assumption. The strict fast-drop override fills before the Close at the theoretical provisional-StochRSI100=0.30 price and deliberately ignores whether adjusted OHLC touched that price; a qualifying buy can then fill at the final Close.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_scaled_accumulation_virtual_pools_2015_2026_v1](TIM/TIM-v0.80__26-08-25__qqq_stochrsi_scaled_pools_2015_2026/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_scaled_pool_structural_ablation_2015_2026_v1 -->
### TIM-v0.80a.1 · QQQ StochRSI 分档补仓与虚拟池结构消融 · `qqq_stochrsi_scaled_pool_structural_ablation_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_scaled_pool_structural_ablation_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Six zero-cost long-only QQQ cases over the same 2015-2026 window isolate Pool B, Pool A daily decay, the B1 a/4 floor, the B2 0.6b floor, and a deferred-entry alternative from the frozen TIM-v0.80 state machine. The FULL case is unchanged. In DEFERRED_BUY_CROSS_040, every B1/B2 signal computes the same virtual tranche sequence against virtual remaining cash, including a/b floors and B2 priority, but holds the notional pending rather than buying QQQ. Pending floor shortfalls are contributed only when raw StochRSI100 strictly crosses from <=0.40 to >0.40; the pending total then buys at that Close. Any actual sale cancels pending tranches and resets virtual cash, a, b, and B2 count. Other cases each disable exactly one named rule.
- 标的：QQQ
- 信号与成交：All ordinary states, ablations, and the deferred 0.40 upcross use completed adjusted Close data. The unchanged fast-drop special case uses prior completed indicator state and its frozen provisional-price equation.；Ordinary sales and buys, including the deferred combined buy, fill at the same adjusted Close that confirms the signal. The unchanged strict fast-drop override fills at the theoretical StochRSI100=0.30 price without an OHLC-touch gate.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_scaled_pool_structural_ablation_2015_2026_v1](TIM/TIM-v0.80a.1__26-08-25__qqq_stochrsi_pool_ablation/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_sparse_entry_factorial_2015_2026_v1 -->
### TIM-v0.80a.2 · QQQ StochRSI 低仓位恢复买入三因子消融 · `qqq_stochrsi_sparse_entry_factorial_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_sparse_entry_factorial_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-25
- 策略：Eight zero-cost long-only QQQ cases form the full 2x2x2 factorial of three buy switches on top of the frozen TIM-v0.80 sell state machine. Switch D replaces immediate B1/B2 fills with the same virtual tranche accounting and releases the accumulated notional on a strict raw StochRSI100 upcross from <=0.20 to >0.20. Switch F arms after a strict StochRSI42 downcross below 0.20 and, on the next strict recovery upcross above 0.20, buys 40% of remaining cash only when pre-trade QQQ weight is below 15% and StochRSI100 is above 0.20. Switch S buys 50% of remaining cash when pre-trade QQQ weight is below 15% and StochRSI100 strictly upcrosses 0.20. On a shared Close, a deferred-queue release executes first; S then supersedes F and sizes from the remaining cash. Sparse buys do not change a, buy_b, the B2 counter, or a pending queue. Any actual sale resets the F arm along with the existing buy-cycle state.
- 标的：QQQ
- 信号与成交：Raw StochRSI42/100 levels, sparse-weight tests, arm state, and strict crossings are confirmed using the completed current adjusted Close. Pre-trade QQQ weight is measured before every same-Close order as QQQ market value divided by total account equity.；Ordinary buys and sells fill at the same adjusted Close that confirms the signal. When multiple buys share a Close, the deferred queue fills first and the 50% StochRSI100 recovery buy sizes from the remaining cash; the 40% StochRSI42 recovery buy is suppressed. The unchanged fast-drop sale uses its theoretical StochRSI100=0.30 price without an OHLC-touch gate.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_sparse_entry_factorial_2015_2026_v1](TIM/TIM-v0.80a.2__26-08-25__qqq_stochrsi_sparse_entry_factorial/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_position_gate_comparison_2015_2026_v1 -->
### TIM-v0.80a.3 · QQQ StochRSI 母策略与持仓门控 · `qqq_stochrsi_position_gate_comparison_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_position_gate_comparison_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：Compare three frozen QQQ mother strategies and nine all-in-or-cash timing accounts derived from each mother's completed post-trade QQQ weight. Mother M1 is D0_F0_S0 from TIM-v0.80a.2. Mother M2 is D0_F1_S1, which adds the sparse StochRSI42 recovery and StochRSI100 recovery buys to the original accumulation and pool-sale state machine. Mother M4 is a separate four-state target-weight process: StochRSI42 turns its short state on at a strict upcross above 0.20 and off at a strict downcross below 0.10; StochRSI100 does the same for its long state; the daily target QQQ weights for neither/short-only/long-only/both states are 0%/40%/60%/100%. For each mother, three independent fixed-capital accounts hold 100% QQQ only when the mother's same-Close post-trade QQQ weight is strictly greater than 70%, 80%, or 90%, otherwise 100% cash.
- 标的：QQQ
- 信号与成交：All StochRSI crossings and mother post-trade QQQ weights use completed current adjusted-Close data. M4 state history is causally warmed on approved observations before 2015-01-02. A gate reads the deterministic completed post-trade weight produced by its mother on the same Close.；All ordinary mother rebalances and all gate entries/exits execute at the same adjusted Close that confirms the signal, with zero transaction cost. M1/M2 retain the frozen fast-drop theoretical-price exception. Gate orders always use the ordinary adjusted Close and never inherit that theoretical fill price.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_position_gate_comparison_2015_2026_v1](TIM/TIM-v0.80a.3__26-08-26__qqq_stochrsi_position_gates/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_true124_position_gate_comparison_2015_2026_v1 -->
### TIM-v0.80a.4 · QQQ StochRSI 真正1/2/4母策略与持仓门控 · `qqq_stochrsi_true124_position_gate_comparison_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_true124_position_gate_comparison_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：Correct TIM-v0.80a.3's mother-selection mismatch and compare the user's true strategy 1, strategy 2 and strategy 4. Mother 1 is the frozen D0_F0_S0 accumulation and dual-pool process. Mother 2 is frozen D0_F1_S1, which adds the short- and long-period sparse recovery entries. Mother 4 is the pruned accumulation process: normal and extreme oversold entries are capped at 15% and 30% QQQ weight, two strict 0.20 recovery crossings buy remaining cash, Pool A is the sole staged exit mechanism, and a strict StochRSI100 downcross below 0.20 exits the residual position. Each mother produces three independent fixed-capital all-in-or-cash accounts based on whether the mother's completed same-Close QQQ weight is strictly greater than 70%, 80% or 90%.
- 标的：QQQ
- 信号与成交：All StochRSI levels, strict crossings, position caps and mother post-trade QQQ weights use completed current adjusted-Close data. A gate reads its mother's deterministic completed post-trade weight on that same Close.；All Mother 4 and gate orders execute at the same adjusted Close that confirms the signal, with zero transaction cost. Mother 1 and Mother 2 retain their previously frozen execution rules, including their theoretical fast-drop exception; gates never inherit that theoretical price.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_true124_position_gate_comparison_2015_2026_v1](TIM/TIM-v0.80a.4__26-08-26__qqq_stochrsi_true124_position_gates/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_stochrsi_90pct_and_gate_comparison_2015_2026_v1 -->
### TIM-v0.80a.5 · QQQ StochRSI 90%持仓信号交集 · `qqq_stochrsi_90pct_and_gate_comparison_2015_2026_v1`
- Experiment ID：`qqq_stochrsi_90pct_and_gate_comparison_2015_2026_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-26
- 策略：Use the three corrected TIM-v0.80a.4 mother strategies only as same-Close binary trend signals. Signal 1 is true when D0_F0_S0's completed post-trade QQQ weight is strictly greater than 90%. Signal 2 is true when D0_F1_S1's completed post-trade weight is strictly greater than 90%. Signal 4 is true when the pruned accumulation mother's completed post-trade weight is strictly greater than 90%. Recompute the three individual 90% gates as baselines and test four fixed-capital intersection accounts: 1 AND 2, 1 AND 4, 2 AND 4, and 1 AND 2 AND 4. Each intersection holds QQQ only while every named component signal is simultaneously true.
- 标的：QQQ
- 信号与成交：The three source mothers first complete all of their own current adjusted-Close transactions. Their post-trade QQQ weights are then compared strictly with 0.90. The intersection is evaluated from the three resulting same-date booleans without looking ahead.；Each independent gate account buys or sells at the same adjusted Close that confirms its completed source-signal state, with zero transaction cost. Source Mother 1 and Mother 2 retain their frozen theoretical fast-drop execution only inside their signal-generating ledgers; every gate order uses the ordinary adjusted Close.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_stochrsi_90pct_and_gate_comparison_2015_2026_v1](TIM/TIM-v0.80a.5__26-08-26__qqq_stochrsi_90_and_gates/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_single_stochrsi_rolling_drift_2005_2020_v1 -->
### TIM-v0.70b.2 · QQQ单周期StochRSI五年滚动漂移诊断 · `qqq_single_stochrsi_rolling_drift_2005_2020_v1`
- Experiment ID：`qqq_single_stochrsi_rolling_drift_2005_2020_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-29
- 策略：Use one raw StochRSI only. For periods 14 through 210 in steps of 7, enter on the 0.20 upward recovery cross and exit on the 0.80 downward reversal cross. Evaluate the unchanged strategy in eleven overlapping five-calendar-year windows whose start year advances by one from 2005 through 2015.
- 标的：QQQ
- 信号与成交：Eligibility and the exact trigger use only completed prior data. Each rolling account is restarted independently at its declared first session.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact trigger. High and Low are ignored.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_single_stochrsi_rolling_drift_2005_2020_v1](TIM/TIM-v0.70b.2__26-08-29__qqq_single_stochrsi_rolling_drift_2005_2020/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:aapl_dual_sma_two_window_grid_v1 -->
### TIM-v0.90 · AAPL双均线双窗口收益网格 · `aapl_dual_sma_two_window_grid_v1`
- Experiment ID：`aapl_dual_sma_two_window_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-30
- 策略：In each independently restarted window, calculate simple moving averages from adjusted AAPL closes using pre-window history. At every completed regular-session close, target a full long position when the fast SMA is strictly above the slow SMA and target cash when it is at or below the slow SMA. Fill any required state change at the next regular-session open.
- 标的：AAPL
- 信号与成交：after the regular-session close, using only that completed close and earlier AAPL history；next regular-session open with an explicit adverse cost; a last-window-close signal has no later fill。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[aapl_dual_sma_two_window_grid_v1](TIM/TIM-v0.90__26-08-30__aapl_dual_sma_two_window_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:aapl_dual_sma_holding_cagr_grid_v1 -->
### TIM-v0.90a.1 · AAPL双均线双窗口持仓CAGR网格 · `aapl_dual_sma_holding_cagr_grid_v1`
- Experiment ID：`aapl_dual_sma_holding_cagr_grid_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-30
- 策略：In each independently restarted window, calculate simple moving averages from adjusted AAPL closes using pre-window history. At every completed regular-session close, target a full long position when the fast SMA is strictly above the slow SMA and target cash when it is at or below the slow SMA. Fill any required state change at the next regular-session open. The primary report annualizes the net account growth over sessions whose post-open account state holds AAPL.
- 标的：AAPL
- 信号与成交：after the regular-session close, using only that completed close and earlier AAPL history；next regular-session open with an explicit adverse cost; a last-window-close signal has no later fill。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[aapl_dual_sma_holding_cagr_grid_v1](TIM/TIM-v0.90a.1__26-08-30__aapl_dual_sma_holding_cagr_grid/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->

<!-- EXPERIMENT:qqq_single_stochrsi_annual_dynamic_oos_2011_2021_v1 -->
### TIM-v0.70b.3 · QQQ单周期StochRSI年度CAGR冠军换参测试 · `qqq_single_stochrsi_annual_dynamic_oos_2011_2021_v1`
- Experiment ID：`qqq_single_stochrsi_annual_dynamic_oos_2011_2021_v1`
- 发布状态：定义快照；历史 run 未附带，本版尚无正式验证结果。
- 创建日期：2026-08-30
- 策略：Trade QQQ from 2011 through 2021 with one raw StochRSI. At the start of each calendar year, replace the indicator period with the frozen maximum-CAGR period from the corresponding parent rolling window, beginning with the parent label 2005–2010 for application year 2011. Carry the existing QQQ or cash position through the parameter change; do not force a trade at year boundaries.
- 标的：QQQ
- 信号与成交：The annual period schedule is frozen before the run from the validated parent evidence. On every session, eligibility and trigger calculations use completed prior data for the period assigned to the current application year; no application-year return is used to select that period.；Gaps beyond the trigger fill at Open; otherwise only the directed Open-to-Close path may fill at the exact solved trigger. High and Low are ignored. A January period change itself never creates or forces an order.。
- 参数、成本、数据来源与研究门禁：以机器定义为准；历史依赖范围见发布说明。
- 配置：[qqq_single_stochrsi_annual_dynamic_oos_2011_2021_v1](TIM/TIM-v0.70b.3__26-08-30__qqq_single_stochrsi_annual_dynamic_oos_2011_2021/experiment.json)
- 正确性与产物：需要在当前版本重新执行并通过完整门禁后登记。
<!-- END_EXPERIMENT -->
