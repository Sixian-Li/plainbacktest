# QQQ 日内 SMA 三窗口参数稳定性

本实验在上一轮完整历史赢家附近搜索同一批参数，并在 1999–2014、2005–2020、2010–2026 三个重叠窗口分别优化 CAGR 与 Sharpe，再进行代表参数的跨窗口正式核验。

- 权威定义：`experiment.json`
- 运行入口：`python -m scripts.run_intraday_sma_window_stability`
- 报告入口：`python -m scripts.analyze_intraday_sma_window_stability`
- 报告不展示逐笔买卖表；完整订单仅保存在 run 的机器账本中。
