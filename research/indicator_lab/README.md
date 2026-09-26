# Quant 动态指标与策略实验台

这个 Streamlit 应用用于交互式观察指标。它会把数据源、指标计算、Plotly 图和未来信号插件分开，因此同一套参数可以直接切换到另一个标的，而不复制指标代码。

当前支持：

- 自动发现工作区已登记的 approved QQQ、SPY、RKLB，以及 `data/processed/daily/equities/` 下 1,299 个通过基线门禁的 S&P 500 历史证券价格文件；VOO 等 provisional 数据不进入默认列表。
- 上传带 `Date`、`Close` 的 canonical CSV；最好同时提供 `Symbol/Open/High/Low/Volume`。上传数据始终标为 `uploaded_unapproved`，不会改变数据登记状态。
- 任意数量的 SMA、EMA、线性 WMA、Wilder RMA；每一条都能选择第二次平滑方法和周期。
- RSI 周期与 Stoch 区间周期分别设置；K 是 Raw StochRSI 的第一次平滑，D 是 K 的第二次平滑，两个阶段可分别选择 SMA/EMA/WMA/RMA 和周期。
- 调整 Close 或 K 线、对数或线性价格轴、日期范围、Raw/K/D/RSI 图层及上下阈值；日期只裁切显示，指标始终先用全部历史预热。
- 将不包含标的的数据无关指标参数下载为 JSON，并可在侧栏重新载入；同一预设可直接切换到另一个标的。

计算口径固定为：EMA 使用 `adjust=False`；WMA 对窗口内最旧至最新值使用 `1..period` 线性权重；Wilder RMA 以每段首个完整窗口的 SMA 为种子再递推。Raw StochRSI 先计算 Wilder RSI，再用独立的 Stoch 区间周期标准化；K 对 Raw 做第一次平滑，D 再对 K 做第二次平滑。RSI 滚动最高值与最低值相同时映射为 0.5。

启动：

```bash
cd <project-root>
backtest/.venv/bin/streamlit run research/indicator_lab/app.py \
  --server.headless true \
  --browser.gatherUsageStats false
```

浏览器通常会自动打开 `http://localhost:8501`。停止服务时在终端按 `Control-C`。

## 扩展边界

`core.PluginRegistry` 是观察模块的扩展点。新模块实现唯一的 `plugin_id` 和 `calculate(frame, parameters)`，返回价格线、振荡线及可选信号标记即可接入页面。当前内置 `moving_averages` 和 `stochrsi`。

页面当前不是回测器：它不处理信号确认时点、下一根成交、成本、仓位、现金或独立账本。以后增加买卖策略时，页面可以负责编辑参数和预览信号，但正式收益必须交给 `backtest/` 的 experiment/run、PyBroker和独立账本流水线，不能把图上的观察结果冒充已验证策略。
