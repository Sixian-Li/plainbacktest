# S&P 500 历史成分候选数据质量报告

> 状态：`pending_review` / `candidate`。本产物不是 approved 数据，也没有写入正式价格库。

## 结论

- 工具可解析 1299 个独立来源证券文件，共 7,377,574 行。
- `InIndex=1` 观察范围：1990-01-02 至 2026-08-04；直接可见行每日成分数 498–507，按连续区间重建后为 498–507。
- 阻断问题：0；官方变更抽查：7/7 通过。
- 该数据保留历史剔除/退市文件和历史时点成员状态，能避免“用今日成分回看历史”这一类幸存者偏差；但来源商、授权、取得日期和早期覆盖仍待人工确认。

## 原始包一致性

- 两个 ZIP 成员数：1299 / 1299；成员集合一致：true。
- 原始目录没有已解压证据；审计直接读取两个 ZIP，且没有为本任务创建解压副本。
- `Date/CompanyName/Symbol/InIndex/Unadjusted Close` 差异行：0。
- 复权 OHLC 差异行：6,109,726；Volume 差异行：6,109,726。

| 字段 | 差异行 | 最大绝对差 | 最大相对差 |
|---|---:|---:|---:|
| Open | 6,109,726 | 67059.6 | 99.377288% |
| High | 6,109,726 | 68344.25 | 99.377288% |
| Low | 6,109,726 | 66474.48 | 99.377288% |
| Close | 6,109,726 | 67059.6 | 99.377288% |
| Volume | 6,109,726 | 1.4421616e+09 | 99.377288% |

## 复权口径边界

- 价格趋势指标：根据策略经济含义明确选择“仅拆股” Close（价格趋势）或“拆股及股息” Close（总收益趋势近似），不得隐式混用。
- 实际成交价：`Unadjusted Close` 是唯一明确未复权价列；源中没有完整未复权 OHLC，因而无法仅凭本包精确重建开高低成交。
- 总收益：可使用股息复权 Close 作近似，但没有独立股息/拆股事件账本，不能证明精确现金再投资。
- 股息复权会随后续股息回溯改写历史比例；用于实时信号时必须记录数据快照，否则存在可解释性/可复现性问题。

## 阻断问题

- 无。

## 待审警告

- `missing_in_index_session`: 24
- `outlier_close_return`: 1653
- `member_outlier_close_return`: 117
- `reentered_index`: 33
- `filename_suffix_not_last_in_index_month`: 226
- `daily_member_count_below_500`: 87
- `daily_member_count_above_500`: 3101
- `left_censored_history`: 1

## 成分证券数不等于 500 的原因

S&P 500 的名义目标是 500 家公司，不等于任何时点都必须正好有 500 条上市证券线。S&P DJI 允许符合条件的公开多股类分别纳入，公司行动和过渡日期也可能临时增加或减少证券线。因此本包的 498–507 只不能直接判为错误；但所有非 500 区间和未能与公司行动对应的日期仍需保留为待审证据，不能用方法论一概豁免。

## 官方一手来源

- [S&P U.S. Indices Methodology](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-sp-us-indices.pdf)
- [S&P 500 official index page](https://www.spglobal.com/spdji/en/indices/equity/sp-500/)
- [2024-03-18 成分调整](https://press.spglobal.com/2024-03-01-Super-Micro-Computer-and-Deckers-Outdoor-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400-and-S-P-SmallCap-600)：pass
- [2024-06-24 成分调整](https://press.spglobal.com/2024-06-07-KKR%2C-CrowdStrike-Holdings-and-GoDaddy-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600)：pass
- [2024-09-23 成分调整](https://press.spglobal.com/2024-09-06-Palantir-Technologies%2C-Dell-Technologies%2C-and-Erie-Indemnity-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600)：pass
- [2025-03-24 成分调整](https://press.spglobal.com/2025-03-07-DoorDash%2C-TKO-Group-Holdings%2C-Williams-Sonoma-and-Expand-Energy-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400-and-S-P-SmallCap-600)：pass
- [2025-09-22 成分调整](https://press.spglobal.com/2025-09-05-AppLovin%2C-Robinhood-Markets-and-Emcor-Group-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400-and-S-P-SmallCap-600)：pass
- [2026-03-23 成分调整](https://press.spglobal.com/2026-03-06-Vertiv-Holdings%2C-Lumentum-Holdings%2C-Coherent%2C-and-EchoStar-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400%2C-and-S-P-SmallCap-600)：pass_with_identity_review
- [2026-06-22 成分调整](https://press.spglobal.com/2026-06-05-Marvell-Technology-and-Flex-Set-to-Join-S-P-500-Others-to-Join-S-P-MidCap-400-and-S-P-SmallCap-600)：pass

## 使用门禁

- 建议的机器可读范围为 1990-01-02 至 2026-08-04；起点明确标为左截断/待复核。
- 在 provider、license/authorization、acquired_date、身份映射和复权用途未完成人工签核前，不得用于正式 ROT 结论或晋升 approved。
- 决策时间早于收盘或没有公告时间证据时，对成分状态至少滞后一个 XNYS 交易日。
