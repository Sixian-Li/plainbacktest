# PlainBacktest

[English](README.md) | **中文**

用日常语言说清策略，让 Agent 把想法变成明确的交易规则并执行回测。PlainBacktest 用独立账本核对逐笔订单和每日账户，保留成交时序、数据与代码哈希，以及可复现的报告，让结果经得起检查。

源码与随包数据：[Sixian-Li/plainbacktest](https://github.com/Sixian-Li/plainbacktest)。

```text
自然语言策略 → Skill + Agent 澄清规则 → 冻结实验定义
→ 数据门禁 → PyBroker 执行 + 独立账本核对 → 交互报告与研究记录
```

自然语言的理解与代码实现由你使用的 Agent 完成；Python 部分负责可重复的计算和验证。仓库自带三个 Skill、整理后的本地数据、研究代码及 83 份实验定义，可以先离线跑通一个示例，再让 Agent 接续研究。

## 跑通第一个示例

在项目根目录执行。当前验证环境是 macOS、Python 3.13；安装依赖需要网络，示例运行不需要行情 API、密钥或在线数据。

```bash
git clone https://github.com/Sixian-Li/plainbacktest.git
cd plainbacktest
python3.13 -m venv backtest/.venv
backtest/.venv/bin/python -m pip install -r backtest/requirements.lock
backtest/.venv/bin/python backtest/scripts/release_data.py check
backtest/.venv/bin/python backtest/scripts/quickstart.py
```

打开最后打印的 `outputs/example_…/report.html`。每次执行创建新目录，同时保存输入数据、规则快照、逐日账户、逐笔订单、指标、代码/数据哈希和核对结果。

默认示例使用 QQQ：2016-01-01 至 2026-08-04，SMA20 高于 SMA100 时持仓，否则空仓；收盘确认信号，下一交易日开盘成交，单边成本 5 bps。初始资金 10 万美元，允许零碎股，不融资。它复用框架的 PyBroker 执行器、独立账本和 v5 报告模板。

可以换参数或批准标的：

```bash
backtest/.venv/bin/python backtest/scripts/quickstart.py --symbol SPY --fast 50 --slow 200
```

这是用于检查安装、执行与对账链路的探索性示例。它不会把原有实验标记为 validated，也不代替参数稳健性或样本外验证。均线使用区间前的历史预热；复权 OHLC 是总回报近似，不是逐笔现金分红账本。

## 用自然语言开展研究

用支持项目 Skill 的 Agent 打开本目录，先读 [AGENTS.md](AGENTS.md)，再提出策略，例如：

> 使用 quant-backtest。研究 QQQ 的 SMA20/SMA100 择时：收盘短均线高于长均线则下一日开盘全仓买入，否则卖出。先明确数据范围、成本、预热和基准，再实现、对账并解释结果。

项目介绍与快速上手提供中英双语；详细研究文档和报告文字目前以中文为主。

三个 Skill 随仓库保存，入口均为 `SKILL.md`：

| Skill | 用途 |
| --- | --- |
| [quant-backtest](.agents/skills/quant-backtest/SKILL.md) | 把策略描述变成实验、执行、独立对账、报告和研究记录 |
| [data-update](.agents/skills/data-update/SKILL.md) | 检查数据快照，按需操作隔离的数据更新与候选审核 |
| [quant-tidy](.agents/skills/quant-tidy/SKILL.md) | 维护目录、实验谱系和工作区结构 |

Skill 是本项目的组成部分。支持 `.agents/skills` 的 Agent 可直接发现；其他 Agent 可读取对应 `SKILL.md` 和其中引用的文件，不需要依赖作者的全局 Skill。正式研究的 Git 溯源、run 生命周期、门禁与工作树约定见 [研究架构](backtest/docs/architecture.md)；下载 ZIP 后也能直接运行上面的示例。

## 随包内容

- `backtest/quantkit/`、`scripts/`、`tests/`：执行、账本、策略、指标、数据和验证代码。
- `backtest/report_templates/`：自包含 Plotly 报告，默认 v5，先讲清策略再展示结果。
- `backtest/experiments/`：DER / ROT / TIM 三条研究线的 83 份定义与谱系。
- `data/`：1,303 个标准日线 CSV、交易日历、成员表、质量证据，以及两份 Nasdaq-100 原始 ZIP；详情见 [数据说明](data/README.md)。
- `research/`：不含策略账户的指标与市场观察工具。

这是独立发布快照。历史 run 和大批旧报告没有随包复制；实验的 active/latest run 指针已清空，不能把源工作区的历史结论当作在这里重新验证过的结果。部分历史实验还需要旧训练产物或未附带的原始包，入口与限制见 [发布范围](backtest/docs/release/scope.md)。源代码版本和复制时的数据哈希记录在 [source_snapshot.json](backtest/docs/release/source_snapshot.json)。

本版本优先使用随包数据。`yfinance` 已是 PyBroker 的间接依赖，但尚未作为本项目的默认数据供应器接入；快速示例不会调用 Yahoo。现有影子更新工具是可选高级功能。

## 验证和已有实验台

```bash
backtest/.venv/bin/python -m pip check
backtest/.venv/bin/python backtest/scripts/release_data.py check
cd backtest
.venv/bin/python -m unittest discover -s tests -t . -v
.venv/bin/python -m scripts.build_research_catalog --check
.venv/bin/python -m scripts.audit_workspace --workspace ..
```

只适用于未附带原始档案的测试会明确报告 skip，不把缺失档案算作通过；具体结果见 [验证记录](backtest/docs/release/verification.md)。测试不会隐式重建随包标准数据。

已有两个可选 Streamlit 实验台，在项目根目录启动：

```bash
backtest/.venv/bin/python -m streamlit run backtest/dual_sma_lab/app.py
backtest/.venv/bin/python -m streamlit run research/indicator_lab/app.py
```

浏览器交互验证和 PDF 导出另需 Node.js 与 Chrome/Chromium，可通过 `QUANT_CHROME_PATH` 指定浏览器路径。普通示例和 Python 测试不依赖它们。其他操作系统尚未进行完整验证。

## 许可证

自有代码、Skill 和文档采用 [MIT](LICENSE)；`data/` 中随本版本提供的数据采用 [CC BY 4.0](data/LICENSE)，署名方式见 [数据说明](data/README.md)。数据权利人已确认所有权并授权该许可。

第三方依赖保留各自许可证。尤其 PyBroker 1.2.12 使用 **Apache 2.0 with Commons Clause**，包含销售限制，不能把整个依赖栈描述成纯 MIT；原文随附于 [第三方声明](THIRD_PARTY_NOTICES.md)。
