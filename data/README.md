# 随包数据快照

本目录的数据由项目权利人确认拥有版权，并于 2026-09-26 授权按 **CC BY 4.0** 提供。数据使用权与研究中的数据质量状态分别记录；许可不会把待审成员表或异常价格自动变为 approved。

## 内容与边界

| 内容 | 路径和口径 |
| --- | --- |
| QQQ、SPY、RKLB | `processed/daily/` 下已批准的标准复权 OHLCV；截止 2026-08-04 |
| VOO | 保留原始质量问题的标准 CSV，状态 `quality_failed`；默认示例拒绝使用 |
| S&P 历史证券 | `processed/daily/equities/` 下 1,299 个 CSV，包含历史证券；每个标的实际起止日不同 |
| S&P 历史成员基线 | `processed/universes/sp500/`，保留成员缺口和边界警告 |
| 独立复审的 S&P / Nasdaq 成员产品 | 各自 `pending_review` 目录；保持 `candidate_pending_review`，不能当作完成质量晋级 |
| Nasdaq 原始包 | `2026-08-05多个数据包_rethink/纳斯达克 100 成分股/` 下两份 ZIP；保留原相对路径、文件名和哈希以兼容现有读取器 |
| 质量和溯源 | registries、manifest、quality reports、交易日历及 source comparisons |

共 1,303 个标准日线 CSV。数据快照以已整理价格为主要入口；它不代表全球所有标的覆盖，也不是实时行情服务。复权口径以各 registry 为准；调整后 OHLC 无法单独还原税费、真实现金分红和可成交流动性。

完整源工作区的美股总包、S&P 原始大 ZIP、在线 provider 响应档案、影子更新历史和重复解压目录未附带。已有 manifest 中相应源路径是历史溯源，不是承诺该档案存在于此发行目录。默认直接读取标准数据，**不要为了启动项目运行 `build_canonical_data.py` 或完整原始包重建**。

读取和完整性检查不需要联网：

```bash
backtest/.venv/bin/python backtest/scripts/release_data.py status
backtest/.venv/bin/python backtest/scripts/release_data.py check
```

哈希来自 `backtest/docs/release/source_snapshot.json`，覆盖所有复制进本版本的数据文件。该检查证明文件与发布快照一致，不证明未来行情、待审数据或策略收益可靠。旧候选 manifest 中的 provider/authorization 待审字段保留为冻结历史记录；本次版权授权单独记在 `distribution.json`，不改写这些文件的哈希和质量状态。

## 复用与署名

许可原文：[Creative Commons Attribution 4.0 International](LICENSE)。署名名称为 **PlainBacktest data contributors**，数据集名称为 **PlainBacktest market-data snapshot (2026-09-26)**。

旧版本中的 Quant Research 数据署名对应同一组贡献者，保留在 `distribution.json` 中。

建议在复用项目中保留以下说明，并添加你对数据所作修改的描述：

> Data: PlainBacktest market-data snapshot (2026-09-26), PlainBacktest data contributors. Licensed under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/ . Source: https://github.com/Sixian-Li/plainbacktest . Changes: [describe changes, or state unchanged].

来源仓库为 [Sixian-Li/plainbacktest](https://github.com/Sixian-Li/plainbacktest)；引用具体快照时同时保留 commit 或版本号。今后另行下载的数据需要单独记录来源与许可，不自动继承本次授权。
