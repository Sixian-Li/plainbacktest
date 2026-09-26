# Quant 文件目录

```text
quant-research/
├── README.md                                      # 项目介绍、安装、离线示例、Skill 与许可入口。
├── LICENSE                                        # 自有代码、Skill 和文档的 MIT 许可。
├── THIRD_PARTY_NOTICES.md                          # 依赖许可与 PyBroker Commons Clause 声明。
├── .agents/skills/                                # 随项目分发的 quant-backtest、data-update、quant-tidy。
├── outputs/                                       # 本机示例产物，运行时生成并由 Git 忽略。
├── .gitattributes                                 # 保持冻结数据与第三方许可文本的原始字节。
├── .gitignore                                      # 忽略本机环境、示例结果和未来运行产物；随包数据进入版本管理。
├── worktrees/                                      # 可见的轻量 Agent 工作树；只隔离执行，不保存唯一报告或数据副本。
├── AGENTS.md                                       # Agent 分流、并行隔离、run 恢复和数据边界的统一入口。
├── catalog.md                                      # 本文件；只维护稳定目录结构和职责。
├── log.md                                          # 按日期记录工作区级实质改动与验证结果。
├── data/                                           # Quant 项目共享的数据层。
│   ├── README.md                                   # 数据来源、批准边界、质量门禁与更新方式。
│   ├── LICENSE                                     # 随包数据的 CC BY 4.0 许可。
│   ├── distribution.json                           # 权利人授权、署名与数据质量边界。
│   ├── source_registry.yaml                        # 标的来源、复权口径和批准状态。
│   ├── data_update_registry.json                   # 影子更新、限额和提升边界。
│   ├── sp500_history_registry.json                 # S&P 500 时点成员和历史价格构建契约。
│   ├── sp500_candidate_registry.json               # S&P 500 双原始包独立复审与 pending_review 契约。
│   ├── nasdaq100_history_registry.json             # Nasdaq-100 原始证据审计与待审候选契约。
│   ├── raw/                                        # 可选：后续原始响应与接收档案，本版未附带。
│   ├── processed/
│   │   ├── daily/                                  # 标准 OHLCV；使用前按 registry 区分批准与质量失败。
│   │   ├── universes/                              # 时点成员和当前快照；S&P/Nasdaq 候选隔离在 pending_review。
│   │   ├── updates/                                # 影子增量候选及报告。
│   │   └── validations/                            # 数据源交叉核验记录。
│   └── docs/data_audit.md                          # 数据覆盖、异常和验证结论。
├── research/                                       # 不含交易账本的市场观察 View。
│   ├── README.md                                   # View 与正式策略回测的边界。
│   ├── indicator_lab/                              # 可换数据源、自由调均线与 StochRSI K/D 的 Streamlit 实验台。
│   ├── scripts/                                    # View 数据处理和 HTML 构建器。
│   ├── tests/                                      # View 计算与交互契约测试。
│   └── market_views/                               # 可重建的观察产物、元数据和索引。
└── backtest/                                       # 标准化策略回测工程。
    ├── README.md                                   # 环境、流水线、验证和复现入口。
    ├── examples/                                   # 可直接运行的双均线示例配置。
    ├── dual_sma_lab/                               # 可换批准标的、日期、参数并生成收益热力图的 Streamlit 双均线实验台。
    ├── requirements.in                             # 直接 Python 依赖。
    ├── requirements.lock                           # 完整锁定环境。
    ├── .venv/                                      # 本项目独立虚拟环境，不进入 Git。
    ├── docs/
    │   ├── release/                                # 发布范围、来源清单、依赖清单和验证记录。
    │   ├── licenses/                               # 第三方许可证原文。
    │   ├── architecture.md                         # Data/View/Backtest/Experiment/Run 与三策略边界。
    │   ├── framework_requirements.md               # 框架稳定需求和验收清单。
    │   ├── research_protocol.md                    # 探索、样本外、模拟盘和实盘晋级门槛。
    │   ├── intraday_sma200_research_playbook.md    # TIM 动态 SMA200 研究协议。
    │   ├── future_requirements.md                  # 暂缓工程能力及启动条件。
    │   ├── github_ready_refactor_plan.md            # 整体平台开源的差距证据、模块边界、迁移路线与验收条件。
    │   └── ideas.md                                # 长期研究想法。
    ├── quantkit/                                   # 账本、成交、策略、指标、路径和报告公共模块。
    │   ├── experiment.py                           # 实验配置、冻结快照和不可覆盖 run 生命周期。
    │   ├── paths.py                                # 不依赖调用文件层级的稳定根路径。
    │   └── reporting.py                            # 强制先复述冻结策略的交互报告渲染器。
    ├── report_templates/                           # 版本化自包含交互报告模板。
    │   ├── interactive_research_v1/
    │   ├── interactive_research_v2/
    │   ├── interactive_research_v3/
    │   ├── interactive_research_v4/                # 标题后、指标前固定展示完整策略卡。
    │   └── interactive_research_v5/                # 用人类语言流程说明策略；打印版不展开机器参数。
    ├── scripts/                                    # 数据、run、报告、目录、审计和验证命令。
    │   ├── quickstart.py                           # 离线双账本示例和自包含交互报告。
    │   ├── release_data.py                         # 只读检查随包数据哈希和状态。
    │   ├── manage_worktree.py                      # 在本仓库 worktrees 创建工作树并发布回主目录。
    │   ├── build_research_catalog.py               # 同步登记册、台账、总/分策略演化史和分叉图。
    │   ├── audit_workspace.py                      # 审计目录、命名、谱系、run 和 worktree 契约。
    │   ├── data_update.py                          # 数据状态、影子更新、购买接收和 S&P/Nasdaq 候选审计入口。
    │   └── validate_run.py                         # 账本、哈希、测试、策略卡和浏览器最终门禁。
    ├── tests/                                      # 按工程职责和三条策略物理分类的自动测试。
    │   ├── core/                                   # 核心账本、成交与公共接口。
    │   ├── data/                                   # 标准数据、更新与来源质量。
    │   ├── lifecycle/                              # experiment/run、目录、谱系和 worktree。
    │   ├── reporting/                              # 模板、策略卡和浏览器计算契约。
    │   └── strategies/
    │       ├── der/                                # DER 短均线导数卖点策略测试。
    │       ├── rot/                                # ROT 上涨区间与轮动策略测试。
    │       └── tim/                                # TIM 简单择时与熊市对冲策略测试。
    └── experiments/                                # 正式研究节点；run 随实验整体移动且内容不可改写。
        ├── index.md                                # 按 DER/ROT/TIM 和版本登记全部实验。
        ├── lineage.json                            # 三策略、日期、版本和父子关系机器权威。
        ├── strategy_evolution.md                   # 三策略合并演化史。
        ├── program_evolution/
        │   ├── DER.md                              # DER 独立演化史。
        │   ├── ROT.md                              # ROT 独立演化史。
        │   └── TIM.md                              # TIM 独立演化史。
        ├── scorecard.csv                           # CAGR、Sharpe、回撤与 case 统一台账。
        ├── research_map.html                       # 分叉关系、关键指标和证据入口。
        ├── research_events.jsonl                   # 追加式研究/run 事件日志。
        ├── DER/                                    # DER-vX__YY-MM-DD__slug/ 命名的 DER 实验。
        ├── ROT/                                    # ROT-vX__YY-MM-DD__slug/ 命名的 ROT 实验。
        └── TIM/                                    # TIM-vX__YY-MM-DD__slug/ 命名的 TIM 实验。
```
