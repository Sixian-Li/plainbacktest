# GitHub 回测框架调研与初步选择

> 调研日期：2026-08-05
> 文档用途：记录候选框架、淘汰理由、初步结论和下一步实测门槛，供用户和后续 agent 共同使用。框架需求的唯一基准仍是 `framework_requirements.md`。

## 1. 结论先行

当前建议不是安装一个“大而全”的系统，而是采用以下组合：

```text
本地 CSV + 自建数据校验层
                ↓
自建统一策略配置 / SignalIntent
                ↓
PyBroker（第一版回测账本和组合执行）
                ↓
标准化 Parquet / CSV / JSON 结果
                ↓
Plotly 交互式 HTML + Markdown/PDF 报告

未来：同一 SignalIntent → 独立 IBKR 模拟盘执行器
```

第一版正式账户回测执行器现已锁定为 **PyBroker 1.2.12**。它最初只是有条件主选；2026-08-08 完成隔离安装、黄金序列、共享现金、费用、真实数据和独立账本实测后，才解除该条件。

选择它的核心原因：

- 原生支持多个标的、组合级仓位、排名、仓位大小、轮换和再平衡。
- 执行上下文只暴露已经完成的 K 线；订单延迟到未来 K 线，适合明确表达“今日收盘信号，下一交易日开盘成交”。
- 原生提供 walk-forward、bootstrap 指标和结构化的订单、交易、持仓、每日组合结果。
- 可以直接编写自定义 CSV `DataSource`，不绑定数据商。
- Python API 相对小，适合由 agent 根据自然语言生成策略代码。
- 官方仓库已经提供 `pybroker-strategy-creator` skill，可作为以后自然语言转策略代码的参考或起点。

不选择单一框架包办可视化和 IBKR。Plotly 与回测引擎解耦；IBKR 等到模拟盘阶段再选择或编写执行适配器。

## 2. 必须正视的 PyBroker 缺点

PyBroker 不是无条件胜出，以下问题必须由我们处理：

1. **默认成交价不适合本项目。** 如果策略不显式设置成交价，普通订单默认使用未来 K 线最高价与最低价的中点 `PriceType.MIDDLE`。这不是我们想要的真实开盘或收盘口径。封装层必须强制每个订单显式选择 `OPEN` 或 `CLOSE`，不能允许框架默认值进入正式实验。
2. **现金分红、拆股和总回报不是一等账户事件。** 当前公开文档没有显示完整的公司行动账本。第一版需要由数据层明确选择复权/总回报口径；若以后要求用原始成交价加现金分红精确复算，需扩展账户流水或更换引擎。
3. **没有原生 IBKR 实盘执行。** 它适合近期回测，不是未来实盘系统本身。
4. **长期融资并非其强项。** 它有现金、权益、仓位和部分 margin 概念，但没有发现符合本项目要求的美股融资利息、初始/维持保证金完整模型。融资进入正式需求时要单独做账务扩展评审。
5. **许可证不是纯 Apache 2.0。** 实际为 Apache 2.0 加 Commons Clause，禁止出售价值主要来自 PyBroker 本身的软件或服务。个人与三四位朋友的非收费研究不构成当前障碍，但若未来商业化必须重新做许可证评审。
6. **框架自带报告不等于我们的研究报告。** 参数平台、固定/动态样本外比较、K 线叠加买卖点等仍需自建标准结果和 Plotly 报告层。

因此，策略不能直接到处调用 PyBroker API。我们要在外面保留自己的数据模型、实验配置、信号/订单意图和结果格式，以降低以后替换引擎的成本。

## 3. 候选框架对比

说明：`强` 表示框架原生适合；`可做` 表示需要薄封装或外部模块；`弱` 表示需要明显改造；`否` 表示与当前硬需求冲突。

| 候选 | 当前维护与许可证 | CSV / 多标的组合 | 开收盘及时序 | 参数研究 | IBKR 路径 | 结论 |
|---|---|---:|---:|---:|---:|---|
| **PyBroker** | 活跃；Apache 2.0 + Commons Clause | 强 | 强，但必须覆盖默认 `MIDDLE` | 强：原生 walk-forward，局部扫描可外层运行 | 弱 | **第一主选，先做验收 spike** |
| **VectorBT Community** | 2026 年恢复活跃并发布 1.0；Apache 2.0 + Commons Clause | 强 | 可做，但数组 shift/估值时点容易误用 | **最强**：批量广播、Numba/Rust、Plotly | 弱 | 暂不作为账本真相；参数扫描过慢时再引入 |
| **Lumibot** | 很活跃；GPL-3.0 | 强 | 有能力，但当前仍有开收盘默认行为相关 issue | 一般 | **强：同策略可接 IBKR 等券商** | 留作未来模拟盘候选，不做近期研究核心 |
| **LEAN** | 很活跃；Apache-2.0 | 强，但本地数据需转成 LEAN 格式 | 强 | 强 | **最强** | 功能最全，但 Docker/.NET/LEAN 数据格式和付费 CLI 对当前阶段过重 |
| **NautilusTrader** | 很活跃；LGPL-3.0 | 强 | **不匹配**：官方明确日线 bar 没有原生 next-bar-open 模式 | 可做但非研究重点 | 强：原生 IB 适配器 | 面向高精度事件/实盘，当前日线需求过度复杂 |
| **Backtesting.py** | 活跃；AGPL-3.0 | **否：核心账户是单标的** | 强：默认下一根开盘，支持当前收盘假设 | 强：优化与热力图简单 | 弱 | 可做单标的交叉校验，不可做主组合引擎 |
| **bt** | 活跃；MIT | **强：目标权重与再平衡** | 弱：核心输入更接近价格矩阵，不是 OHLC 撮合引擎 | 可做 | 弱 | 适合资产配置，不适合本项目开盘/收盘成交研究 |
| **Zipline Reloaded** | 主要做兼容性维护；Apache-2.0 | 强，但自定义 bundle 较重 | 可做 | 弱 | 弱 | 数据接入和维护成本高于收益，不选 |
| **Backtrader** | 功能丰富但主仓库技术栈明显陈旧；GPL-3.0 | 强 | 强 | 可做 | 名义上有，但依赖老旧 IbPy | 不用作新项目基础 |

## 4. 为什么不是 VectorBT 作为唯一核心

VectorBT 对本项目最有吸引力的部分是参数研究：一次广播大量均线周期、生成参数曲面、分析平滑平台，并用 Plotly 交互查看结果。2026 年的 1.0 版本还增加了可选 Rust 引擎。

但它更接近“把信号数组变成组合记录的高速研究引擎”。信号移动一行、共享现金时同一时点的订单顺序、估值价格是否来自下单前，都会改变结果。官方文档也专门警告共享现金时使用当前估值价格可能造成作弊。我们当然可以写对，但对一个由自然语言频繁生成策略的系统，默认安全边界不如 PyBroker 的“已完成 bar + 未来 bar 执行”直观。

所以第一阶段不同时维护两个正式收益真相：

- PyBroker 是正式账户曲线、订单和成交的唯一来源。
- Pandas/NumPy 负责指标和参数候选生成。
- 如果几千组参数扫描确实成为瓶颈，再让 VectorBT 负责粗筛，最终候选仍回到 PyBroker 复算。

## 5. 为什么不是 LEAN 作为当前核心

LEAN 在长期生产能力上最完整：组合账户、订单模型、公司行动、优化、报告、券商连接和 IBKR 都比轻量 Python 框架成熟，许可证也是标准 Apache-2.0。

它当前落选不是因为能力不足，而是总成本不合适：

- 推荐的本地工作流依赖 Docker 和 LEAN CLI。
- 官方说明使用 CLI 需要付费组织层级，即使运行本地回测也是如此。
- 本地 CSV 需要转成 LEAN 规定的目录和压缩数据格式。
- Python 策略运行在更大的 .NET/LEAN 领域模型里，agent 写代码、排查环境和生成轻量本地报告的复杂度更高。

如果将来要求“回测、模拟盘、IBKR 实盘必须共用同一完整引擎”，或融资/公司行动成为核心需求，应重新比较 LEAN 与当时版本的 NautilusTrader，而不是被第一版选择绑死。

## 6. 为什么 Lumibot 暂时只保留为实盘候选

Lumibot 的最大优势是同一策略类可以在回测和多个实盘券商之间切换，并且原生生成 HTML tearsheet、交易文件、指标图和机器可读指标。它也能读取 Pandas/CSV。

但当前 GitHub issue 中仍能看到“回测默认价格改成 open”“如何在 close 提交订单”“IB 数据回测时可能取到当前时间数据”等与本项目最敏感的成交时序相关问题。高频发布本身不是坏事，但意味着第一版需要追随更大的变化面。我们目前更重视研究口径可控，而不是马上获得券商连接，因此不把它作为正式回测真相。

## 7. 入选前必须通过的最小实测（已完成）

以下是锁定 PyBroker 前规定的验收门槛。对应测试和正式实验现均已完成；仍未具备的精确公司行动事件账本没有被误报为已完成。

### 7.1 安装与环境

- 在独立虚拟环境安装并固定确切版本。
- 验证当前 macOS、Python、Pandas 和 Numba 组合。
- 记录完整依赖锁文件，不直接跟随最新版漂移。

### 7.2 黄金价格序列

使用手工构造、结果可以人工计算的 6～10 根 OHLC K 线，验证：

- 第 `t` 日收盘生成信号，第 `t+1` 日开盘成交。
- 第 `t` 日收盘生成信号，第 `t+1` 日收盘成交。
- 不显式指定 `OPEN/CLOSE` 时测试必须失败，而不是悄悄使用 `MIDDLE`。
- 买卖信号不会看到未来 K 线。
- 最后一日未平仓如何计价和是否强制平仓。

### 7.3 组合与账本

- 两个标的同日争用现金时，订单优先级、排名和仓位上限符合配置。
- 手续费、固定费用和自定义滑点能从逐笔成交复算。
- 订单、成交、持仓、现金、每日净值之间能逐日对账。
- 只做多模式不会因错误信号产生负仓位。

### 7.4 数据口径

- 用下载包中的同一标的 `bfq/qfq/hfq` 样本核对列名、日期、重复值和交易日。
- 明确均线计算用哪条价格序列，成交和每日估值用哪条序列。
- 找一个发生过拆股和现金分红的标的，人工验证跨事件收益。
- 如果数据包没有独立现金分红字段，报告中不得声称实现了原始价格加分红的完整账户回放。

### 7.5 参数与复现

- 运行一维 MA170～MA190 和一个小型二维参数网格。
- 验证 walk-forward 每个窗口只使用当时可见的数据。
- 同样输入连续运行两次，订单、净值和指标必须一致。
- 每个 run 保存配置、数据指纹、代码提交、依赖版本和随机种子。

## 8. 第一版采用边界

上述核心实测已经通过，当前执行以下决定：

- 将 PyBroker 版本锁定为第一版回测执行器。
- 保留我们的统一实验配置、数据接口、`SignalIntent` 和标准结果 schema，不把策略直接绑定到框架内部结构。
- 参数扫描先用普通 Python 并行或向量化；不提前增加 VectorBT 依赖。
- Plotly 单独生成可缩放、拖动和滚动的 K 线与净值 HTML。
- 暂不连接 IBKR，不安装 LEAN、NautilusTrader 或 Lumibot。
- 暂不实现融资；先把接口和假设字段保留在配置中。

## 9. 主要一手资料

### PyBroker

- [官方 GitHub 仓库](https://github.com/edtechre/pybroker)
- [发布记录](https://github.com/edtechre/pybroker/releases)
- [自定义 CSV DataSource](https://www.pybroker.com/en/latest/notebooks/7.%20Creating%20a%20Custom%20Data%20Source.html)
- [已完成 K 线与未来 K 线执行语义](https://www.pybroker.com/en/latest/reference/pybroker.context.html)
- [排名和仓位大小](https://www.pybroker.com/en/latest/notebooks/4.%20Ranking%20and%20Position%20Sizing.html)
- [策略、walk-forward 与结构化结果](https://www.pybroker.com/en/latest/reference/pybroker.strategy.html)
- [许可证：Apache 2.0 + Commons Clause](https://github.com/edtechre/pybroker/blob/master/LICENSE)
- [官方 pybroker-strategy-creator skill](https://github.com/edtechre/pybroker/blob/master/skills/pybroker-strategy-creator/SKILL.md)

### 其他主要候选

- [VectorBT 官方仓库](https://github.com/polakowo/vectorbt)
- [VectorBT Portfolio 与共享现金注意事项](https://vectorbt.dev/api/portfolio/base/)
- [Lumibot 官方仓库](https://github.com/Lumiwealth/lumibot)
- [Lumibot Pandas/CSV 回测](https://lumibot.lumiwealth.com/backtesting.pandas.html)
- [Lumibot 当前 issues](https://github.com/Lumiwealth/lumibot/issues)
- [LEAN CLI 安装与付费层级说明](https://www.quantconnect.com/docs/v2/lean-cli/installation/installing-lean-cli)
- [LEAN 本地数据格式](https://www.quantconnect.com/docs/v2/lean-cli/datasets/format-and-storage)
- [NautilusTrader 日线 bar 执行限制](https://nautilustrader.io/docs/latest/concepts/backtesting/)
- [NautilusTrader IBKR 适配器](https://nautilustrader.io/docs/nightly/integrations/ib/)
- [Backtesting.py 成交与优化 API](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html)
- [bt 组合再平衡 API](https://pmorissette.github.io/bt/bt.html)
- [Zipline Reloaded 官方仓库](https://github.com/stefan-jansen/zipline-reloaded)
- [Backtrader 官方仓库](https://github.com/mementum/backtrader)
- [Backtrader 老式 IbPy 集成说明](https://www.backtrader.com/docu/live/ib/ib/)

## 10. 当前决策状态

```text
需求澄清                 已完成
GitHub 候选广度筛选       已完成
官方文档深度筛选          已完成
第一版正式执行器           PyBroker 1.2.12
隔离安装与依赖锁定         已完成（Python 3.13.9）
本地 CSV 适配             已完成（QQQ/SPY approved）
黄金序列正确性测试         已完成
共享现金与费用测试         已完成
独立账本复核              已完成（1,156 个正式 run）
最终锁定回测核心           已完成；边界见第 8 节
```

## 11. 决策记录

### 2026-08-07：保留 VectorBT 作为后续研究加速层

VectorBT 没有被淘汰。当前决定是先让 PyBroker 承担正式订单、成交和账户曲线；当局部参数扫描达到实际性能瓶颈时，再接入 VectorBT 做批量粗筛、参数曲面和稳健平台分析。

VectorBT 的输出不是第二套并列的正式收益真相。它筛出的候选参数需要回到正式账户引擎复算；这样既利用它的速度和 Plotly 优势，也避免数组 shift、同日共享现金订单顺序和估值时点差异造成结果分叉。

### 2026-08-08：PyBroker 1.2.12 通过第一版锁定门槛

项目专用 `.venv` 已固定 Python 3.13.9、PyBroker 1.2.12 及完整依赖。18 项自动测试覆盖数据门禁、下一开盘/下一收盘显式成交、禁止默认 `MIDDLE`、防未来数据、费用、末日估值、多标的共享现金、独立账本和参数平台识别。

QQQ 与 SPY 的零成本/5 bps 参数网格共执行 1,156 个 PyBroker run，并同时执行 1,156 个独立参考账本；最坏逐日净值绝对差约 3.73e-09 美元。因此 PyBroker 已从“有条件主选”转为第一版正式执行器。

锁定不代表所有长期需求已经满足：原始 OHLC 加现金分红/拆股事件、融资利息与保证金、IBKR 执行仍属于未来独立模块或重新评审项。
