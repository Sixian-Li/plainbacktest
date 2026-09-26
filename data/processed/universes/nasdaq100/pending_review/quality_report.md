# Nasdaq-100 历史成分候选数据质量报告

> 状态：`pending_review` / `candidate`。本产物不是 approved 数据，也没有写入正式价格库。

## 结论

- 工具可解析 480 个独立来源证券文件，共 2,419,555 行。
- `InIndex=1` 观察范围：1993-10-01 至 2026-08-04；直接可见行每日成分数 95–109，按连续区间重建后为 96–109。
- 阻断问题：0；官方变更抽查：7/7 通过。
- 该数据保留历史剔除/退市文件和历史时点成员状态，能避免“用今日成分回看历史”这一类幸存者偏差；但来源商、授权、取得日期和早期覆盖仍待人工确认。

## 原始包一致性

- 两个 ZIP 成员数：480 / 480；成员集合一致：true。
- 已解压目录与股息复权 ZIP 逐文件一致：true。
- `Date/CompanyName/Symbol/InIndex/Unadjusted Close` 差异行：0。
- 复权 OHLC 差异行：1,490,624；Volume 差异行：1,490,624。

| 字段 | 差异行 | 最大绝对差 | 最大相对差 |
|---|---:|---:|---:|
| Open | 1,490,624 | 67059.6 | 98.699369% |
| High | 1,490,624 | 68344.25 | 98.699369% |
| Low | 1,490,624 | 66474.48 | 98.699369% |
| Close | 1,490,624 | 67059.6 | 98.699369% |
| Volume | 1,490,624 | 1.4421616e+09 | 98.699369% |

## 复权口径边界

- 价格趋势指标：根据策略经济含义明确选择“仅拆股” Close（价格趋势）或“拆股及股息” Close（总收益趋势近似），不得隐式混用。
- 实际成交价：`Unadjusted Close` 是唯一明确未复权价列；源中没有完整未复权 OHLC，因而无法仅凭本包精确重建开高低成交。
- 总收益：可使用股息复权 Close 作近似，但没有独立股息/拆股事件账本，不能证明精确现金再投资。
- 股息复权会随后续股息回溯改写历史比例；用于实时信号时必须记录数据快照，否则存在可解释性/可复现性问题。

## 阻断问题

- 无。

## 待审警告

- `missing_in_index_session`: 27
- `outlier_close_return`: 742
- `member_outlier_close_return`: 33
- `reentered_index`: 69
- `filename_suffix_not_last_in_index_month`: 188
- `daily_member_count_below_100`: 328
- `daily_member_count_above_100`: 3102
- `left_censored_history`: 1

## 成分数不等于 100 的原因

Nasdaq-100 方法允许同一公司多股类各自成为证券，Fast Entry、某些季度再平衡和分拆证券也可暂时只增不减。因此证券数可高于 100；低于 100 的日期则不能仅凭方法论解释，尤其早期样本需继续人工核对。

## 官方一手来源

- [index_methodology](https://indexes.nasdaqomx.com/docs/methodology_NDX.pdf)
- [official_component_count_snapshot](https://indexes.nasdaqomx.com/Index/overview/NDX)
- [HONA_spin_off_trading_date](https://www.nasdaq.com/press-release/honeywell-aerospace-completes-spin-honeywell-technologies-and-begins-trading-nasdaq)
- [2020-12-21 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/annual-changes-nasdaq-100-index-9)：pass
- [2021-12-20 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/annual-changes-nasdaq-100-indexr)：pass
- [2024-12-23 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/annual-changes-nasdaq-100-indexr-1)：pass
- [2025-12-22 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/annual-changes-nasdaq-100-indexr-2)：pass
- [2026-01-20 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/walmart-inc-join-nasdaq-100-indexr-beginning-january-20th-2026)：pass
- [2026-06-22 成分调整](https://ir.nasdaq.com/node/110541)：pass
- [2026-07-07 成分调整](https://ir.nasdaq.com/news-releases/news-release-details/space-exploration-technologies-corporation-join-nasdaq-100)：pass

## 使用门禁

- 建议的机器可用范围从 1993-10-01 开始，但 1993 初段明确标为左截断/待复核。
- 在 provider、license/authorization、acquired_date、身份映射和复权用途未完成人工签核前，不得用于正式 ROT 结论或晋升 approved。
- 决策时间早于收盘或没有公告时间证据时，对成分状态至少滞后一个 XNYS 交易日。
