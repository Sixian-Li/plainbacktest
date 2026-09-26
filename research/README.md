# Research

本目录保存不产生交易信号或账户收益的市场观察工具与产物；正式策略实验仍放在 `backtest/experiments/`。

View 不是“没有价值的测试”，而是没有策略账本的观察证据。它与正式实验的关系记录在 `../backtest/experiments/lineage.json`：短均线观察图支持 `DER`，熊市标注与韧性筛选同时支持 `ROT`/`TIM`，人工日程观察支持 `TIM`。研究演化、Sharpe 台账和分叉图统一从 `../backtest/experiments/research_map.html` 进入。

## Streamlit 动态指标与策略实验台

`indicator_lab/app.py` 是可重复使用的本地互动页面。数据源选择与指标参数彼此独立：默认可在批准的 QQQ、SPY、RKLB 和 1,299 个 S&P 500 历史证券价格文件之间切换，也可上传 canonical CSV；上传文件只标为 `uploaded_unapproved`，不会自动取得批准状态。页面支持任意数量的 SMA/EMA/WMA/Wilder RMA、每条均线的可选二次平滑，以及分别设置 RSI 周期、Stoch 区间周期、K 第一次平滑和 D 第二次平滑。当前配置可以下载为 JSON 并重新载入到另一个标的。

启动命令：

```bash
cd <project-root>
backtest/.venv/bin/streamlit run research/indicator_lab/app.py \
  --server.headless true \
  --browser.gatherUsageStats false
```

该页面只负责动态计算、图层和未来插件配置，不产生订单、交易账本或策略收益。以后加入买卖信号时可以复用 `indicator_lab/core.py` 的插件注册边界，但正式绩效必须进入 `backtest/` 的 experiment/run、成交语义和独立账本验证流程。完整使用与扩展说明见 `indicator_lab/README.md`。

## 熊市区间手工标注器

打开 `market_views/spy_qqq_bear_market_annotator.html`，在 QQQ 或 SPY 折线图上横向拖拽即可新增主观熊市区间。上下图共享日期并同步标红；表格可改名、调整起止日期、添加备注或删除，并自动计算区间收益与区间内最大回撤。标注自动保存在当前浏览器，也可导出 JSON/CSV 或重新导入 JSON。将导出的文件交给分析脚本或 Codex，即可批量提取这些区间，不必逐个输入日期。

当前用户标注分为三层：原始浏览器导出 `subjective_spy_qqq_bear_markets_source.json`；保留 12 个手工边界的候选窗口 `subjective_spy_qqq_bear_markets.json/.csv`；供后续研究使用、剔除回弹的 `subjective_spy_qqq_bear_markets_peak_to_trough.json/.csv`。正式下跌段只在各候选窗口内部调整：先比较 QQQ/SPY 的区间最大回撤，选择较深者为驱动标的，再将边界收紧到该标的回撤峰值至谷底，绝不向候选窗口外扩张。大小分类仍按 QQQ 或 SPY 任一标的最大回撤是否达到 20%。

## 当前成分股熊市逆势研究

`market_views/bear_market_resilience_current_constituents.html` 是第一版回顾性事件研究：把 2026-08-11 官方 SPY 持仓中的 S&P 500 与 2026-08-04 供应商快照中的 Nasdaq-100 当前成分证券代码合并去重，再加入 TLT、GLD、SPY、QQQ，对上述 12 段峰值至谷底熊市计算复权收盘收益。筛选条件固定为“至少一段可用熊市收益大于 0，且所有可用熊市收益复合后大于 0”；最差单次熊市收益醒目展示但不参与淘汰。尚未上市或缺少端点的区间保留为 N/A，汇总同时显示覆盖数。官方 S&P 500 的 FERG 暂无批准行情，仍保留在股票池中并显示全 N/A。

该版本只用于跑通股票池、事件收益、汇总筛选和互动报告管线，明确存在当前成分股幸存者偏差；未来历史成分股版本应只替换股票池生成模块，不能覆盖本版产物或悄悄改变本版口径。

### 12 + 8 + 4 候选完整历史图

`market_views/bear_resilience_24_full_history_sma_bear_intervals.html` 把核心层 12、近核心层 8 和零售补充 4 共 24 个标的分别画成独立的完整历史图。统一横轴为 1999-01-01～2026-08-04；上市较晚的 TLT、GLD、DG、EXE 保留真实起点，不回填价格。每张图可独立勾选 SMA30/150/200/250/300，在不显示、仅小熊、仅大熊和全部熊市之间切换高亮，并支持对数/线性纵轴、缩放和可见窗口 Y 轴重算。默认显示 SMA200 与全部 12 段峰谷熊市；页面按滚动位置懒加载图表。

该文件是帮助复核“候选收益是否主要由 2000–2002 单一事件贡献”的市场 View，不计算组合收益或产生交易信号。22 只股票读取批准的标准复权日线，TLT/GLD 读取购买数据中的拆股及股息调整日线；均线在可能时使用 1999 年前的原始历史预热。

重建自包含 HTML 与来源元数据：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_bear_resilience_history_view.py \
  --equity-price-dir data/processed/daily/equities \
  --tlt 'data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/国债系列 ETF/TLT - 美国 20 年以上国债 ETF iShares/TLT_1day_拆股股息调整_20260805.csv' \
  --gld 'data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/核心商品 ETF/GLD - 黄金 ETF SPDR/GLD_1day_拆股股息调整_20260805.csv' \
  --intervals research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json \
  --start 1999-01-01 --end 2026-08-04 \
  --output-html research/market_views/bear_resilience_24_full_history_sma_bear_intervals.html \
  --output-metadata research/market_views/bear_resilience_24_full_history_sma_bear_intervals.json
node research/scripts/smoke_bear_resilience_history_view.mjs \
  research/market_views/bear_resilience_24_full_history_sma_bear_intervals.html
```

重建汇总 CSV、逐区间明细、元数据和自包含 HTML：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_bear_market_resilience.py \
  --intervals research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json \
  --sp500-security-master data/processed/universes/sp500/security_master.csv \
  --sp500-current data/processed/universes/sp500/current_constituents.csv \
  --sp500-manifest data/processed/universes/sp500/manifest.json \
  --sp500-prices data/processed/daily/equities \
  --nasdaq100-archive 'data/2026-08-05多个数据包_rethink/纳斯达克 100 成分股/纳斯达克 100 成分股-拆股及股息调整_20260805.zip' \
  --tlt 'data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/国债系列 ETF/TLT - 美国 20 年以上国债 ETF iShares/TLT_1day_拆股股息调整_20260805.csv' \
  --gld 'data/2026-08-05多个数据包_rethink/核心 ETF 日线数据/核心商品 ETF/GLD - 黄金 ETF SPDR/GLD_1day_拆股股息调整_20260805.csv' \
  --spy data/processed/daily/SPY.csv --qqq data/processed/daily/QQQ.csv \
  --output-summary research/market_views/bear_market_resilience_current_constituents_summary.csv \
  --output-detail research/market_views/bear_market_resilience_current_constituents_detail.csv \
  --output-metadata research/market_views/bear_market_resilience_current_constituents.json \
  --output-html research/market_views/bear_market_resilience_current_constituents.html
```

重建自包含 HTML：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_bear_market_annotator.py \
  --qqq data/processed/daily/QQQ.csv \
  --spy data/processed/daily/SPY.csv \
  --output research/market_views/spy_qqq_bear_market_annotator.html \
  --metadata research/market_views/spy_qqq_bear_market_annotator.json
```

重新标准化浏览器导出：

```bash
backtest/.venv/bin/python research/scripts/normalize_bear_market_intervals.py \
  --input research/market_views/subjective_spy_qqq_bear_markets_source.json \
  --qqq data/processed/daily/QQQ.csv \
  --spy data/processed/daily/SPY.csv \
  --major-threshold 0.20 \
  --boundary-policy worst-peak-to-trough \
  --output-json research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.json \
  --output-csv research/market_views/subjective_spy_qqq_bear_markets_peak_to_trough.csv
```

## SMA 市场观察图

当前生成器使用已经锁定依赖的回测 Python 环境，但只读取 `data/processed/` 标准数据，不导入回测策略或账本代码：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_sma_market_view.py \
  --input data/processed/daily/QQQ.csv \
  --symbol QQQ --start 1999-03-10 --end 2026-08-04 \
  --sma-start 25 --sma-end 35 --sma-step 5 \
  --overlay-start 70 --overlay-end 450 --overlay-step 10 \
  --output research/market_views/qqq_1999-03-10_2026-08-04_sma25-35_70-450.html \
  --metadata research/market_views/qqq_1999-03-10_2026-08-04_sma25-35_70-450.json
```

当前视图只使用每日 Close，并在完整历史上计算 SMA25、SMA30、SMA35 及三者算术平均。下方面板分别显示这四条线相对前一交易日的百分比变化，即 `(今日值 / 前一交易日值 - 1) × 100%`。主图另有 SMA70～450、间隔 10 的 39 条长期均线，默认隐藏，可展开逐条勾选或一键全开/全关；长期组不参与短均线平均，也不增加下图变化率。上市初期的预热空值保留，不会为了填满均线而删除早期 Close。生成的 HTML 自包含 Plotly，可离线打开，属于可重建产物，不进入 Git。

## RSI / Stochastic RSI 市场观察图

`market_views/qqq_2020-01-01_2026-08-04_rsi_stochrsi_periods.html` 上图显示 QQQ Close，下图把 Wilder RSI 除以 100 后与未平滑 Raw StochRSI 放在同一个 0～1 坐标中。Period 14、28、42、56、70、100、140 可逐个勾选；一个周期勾选项同时控制该周期的 RSI 实线与 StochRSI 虚线，默认只显示14日。RSI周期与StochRSI的滚动高低区间周期保持相同，不计算K/D；所有指标使用2020年前历史预热。本图只用于因子观察，不产生交易信号或账户收益。

重建自包含 HTML、元数据并运行真实 Chrome 交互检查：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_rsi_stochrsi_market_view.py \
  --input data/processed/daily/QQQ.csv \
  --symbol QQQ --start 2020-01-01 --end 2026-08-04 \
  --periods 14,28,42,56,70,100,140 \
  --output research/market_views/qqq_2020-01-01_2026-08-04_rsi_stochrsi_periods.html \
  --metadata research/market_views/qqq_2020-01-01_2026-08-04_rsi_stochrsi_periods.json
node research/scripts/smoke_rsi_stochrsi_market_view.mjs \
  research/market_views/qqq_2020-01-01_2026-08-04_rsi_stochrsi_periods.html
```

## 个股全历史 SMA / StochRSI 互动图

四张独立自包含互动图覆盖 AAPL、NVDA、MSFT 和 GOOGL 的全部批准日线。每张图的上半区显示调整收盘价与 SMA10～350（步长 10）的 35 条曲线，下半区显示 Raw StochRSI14～210（步长 14）的 15 条曲线；Close、每一条 SMA 和每一条 StochRSI 都可用复选框单独控制，共 51 个可选序列。图中还提供各组全选、全不选、恢复默认、对数/线性价格轴和全历史重置；缩放时间区间后，价格轴会按当前可见曲线自动重算。默认显示 Close、SMA50/100/200/350 与 StochRSI14/42/98/210，长历史价格轴默认使用对数尺度。

StochRSI 口径与既有观察图及正式研究一致：先计算 Wilder RSI，再以同一周期计算 RSI 的滚动最低/最高值，使用未平滑的 `(RSI - low) / (high - low)`；零区间映射为 0.5，不计算 K/D。上市初期未完成预热的指标留空，但 Close 历史不截断。Google 按 A 类股 GOOGL 展示，不把 GOOG 作为第五张图。

单图重建示例；其余标的只需替换 `--input`、`--symbol` 和输出文件名，SMA 与 StochRSI 参数默认就是上述范围：

```bash
cd <project-root>
backtest/.venv/bin/python research/scripts/build_indicator_graph_view.py \
  --input data/processed/daily/equities/AAPL.csv --symbol AAPL \
  --output research/market_views/aapl_1990-01-02_2026-08-04_sma10-350_stochrsi14-210_graph.html \
  --metadata research/market_views/aapl_1990-01-02_2026-08-04_sma10-350_stochrsi14-210_graph.json
node research/scripts/smoke_indicator_graph_view.mjs \
  research/market_views/aapl_1990-01-02_2026-08-04_sma10-350_stochrsi14-210_graph.html
```
