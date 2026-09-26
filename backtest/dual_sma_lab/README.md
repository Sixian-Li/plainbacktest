# 双均线策略实验台

这是一个探索性的 Streamlit 回测工具。它允许在工作区批准日线上切换标的、日期、快慢 SMA、资金和单边成本，查看一组参数的账户路径与指标，也可以生成自定义 `fast × slow` 热力图。

## 启动

```bash
cd backtest
.venv/bin/python -m streamlit run dual_sma_lab/app.py
```

浏览器默认打开 `http://localhost:8501`。页面只读取批准数据，不会修改标准价格、experiment 或 validated run。

## 固定交易口径

- 价格为工作区 canonical 调整 OHLCV；SMA 使用所选区间前的历史预热。
- 每个完整收盘后，`fast SMA > slow SMA` 目标满仓，否则目标现金。
- 状态变化在下一正常交易日开盘执行；最后一个收盘没有下一根 K 线时不成交。
- 允许碎股，不加杠杆，现金收益为零；买入价上调、卖出价下调所设单边 bps。
- 每个页面日期区间都从现金独立开始。Buy & Hold 同样在第一日收盘确认、第二日开盘买入。

## 指标

- 日历 CAGR / Sharpe：完整账户日收益，包含空仓零收益日。
- 持仓 CAGR：净账户增长按开盘后有仓位的交易日数，以 252 日做几何年化；延续 `TIM-v0.90a.1` 的正式报告口径。
- 持仓 Sharpe：只用受仓位影响的账户收益，包含买入日、连续持有日和卖出日，因此退出开盘跳空及卖出成本不会遗漏。
- 热力图可切换持仓 CAGR、持仓 Sharpe、日历 CAGR、日历 Sharpe、累计收益、最大回撤、持仓率和订单数；完整网格可下载为 CSV。

网格使用与逐参数参考账本等价的分块向量状态机。默认快线 1–50、慢线 15–250、步长 1 共 11,134 个合法参数；所有格子共用最大均线完成预热后的实际起点。页面把机械前十明确标成样本内描述，不代表推荐或晋级。

## 验证

```bash
cd backtest
.venv/bin/python -m unittest tests.strategies.tim.test_dual_sma_lab -v
node scripts/smoke_dual_sma_lab.mjs
```
