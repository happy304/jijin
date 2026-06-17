# 个人基金量化平台专业优化执行计划

> 目标：将现有偏“全功能/机构化”的基金量化平台，收敛为适合个人长期使用的专业基金研究、筛选、组合管理、回测验证与风险辅助系统。
>
> 原则：不删除核心能力；先通过配置、菜单、文案、调度分层降低复杂度；再逐步重构超大模块；最后强化数据质量、个人评分模型与组合风险闭环。

---

## 1. 执行范围

本轮执行分为两个层级：

### 1.1 本轮立即执行范围

优先完成低风险、高收益的个人化收敛改造：

1. 新增一份专业优化执行计划 Markdown 文档。
2. 增加个人模式相关配置项。
3. 前端导航支持个人模式，隐藏不适合个人默认使用的高级功能。
4. 将“交易建议”前端入口调整为更合适的“组合检查”。
5. 为后续轻量/研究/完整调度模式预留配置结构。
6. 补充必要的风险提示文案。
7. 尽量不改动核心量化算法，避免引入计算口径变化。

### 1.2 后续分阶段执行范围

后续按优先级推进：

1. Advisor 前端大文件拆分。
2. Advisor 后端 API 拆分。
3. `trading_advisor.py` 服务拆分。
4. 数据质量状态统一建模。
5. 首页数据健康看板。
6. 基金详情页数据质量展示。
7. 个人版基金评分模型。
8. 调度任务 light/research/full 三档化。

---

## 2. 当前系统专业评估摘要

### 2.1 已具备的专业能力

当前平台已经具备较完整的基金量化研究平台能力：

- FastAPI 后端。
- React + TypeScript 前端。
- SQLAlchemy + Alembic 数据模型与迁移。
- Celery + Redis 后台任务。
- PostgreSQL/TimescaleDB 时序数据存储设计。
- 多源数据采集与降级。
- 原始快照归档。
- NAV 数据校验。
- 复权净值重算。
- 因子、策略、回测、模拟、组合分析。
- AI 辅助层。
- Prometheus/Grafana 监控。
- 覆盖面较广的测试体系。

### 2.2 当前主要问题

#### 问题一：功能偏重

当前系统包含不少偏机构化功能：

- Advisor 参数集审核。
- 参数发布、回滚、门禁。
- OOS/PBO 快照。
- 学习版本治理。
- 多渠道提醒。
- 完整监控栈。
- 自动日度建议生成。

这些能力专业但不一定适合个人默认使用。

#### 问题二：核心文件过大

已确认以下文件规模偏大：

```text
frontend/src/pages/Advisor/index.tsx          4568 行
backend/app/api/v1/advisor.py                 3362 行
backend/app/services/trading_advisor.py       5887 行
```

这会导致维护难度、测试难度和迭代风险显著增加。

#### 问题三：数据质量没有成为前端主流程

底层已经有 NAV 校验、复权、快照等能力，但前端用户在做筛选、回测、组合检查前，不一定能直观看到：

- 数据是否更新。
- 最新 NAV 日期。
- 是否存在异常收益。
- 是否发生复权重算。
- 是否跨源一致。
- 当前结果是否依赖过期数据。

---

## 3. 产品定位调整

### 3.1 推荐定位

平台应定位为：

> 个人基金研究、筛选、组合风险监控、回测验证与投资决策辅助系统。

不建议将默认产品表达定位为“交易建议系统”或“自动投顾系统”。

### 3.2 推荐主流程

```text
数据更新
  ↓
数据质量检查
  ↓
基金筛选
  ↓
基金详情分析
  ↓
组合风险检查
  ↓
回测验证
  ↓
调仓辅助
  ↓
执行记录与复盘
```

### 3.3 推荐默认导航

个人模式下建议保留：

```text
概览
基金发现
基金检索
组合检查
回测分析
系统设置
```

默认隐藏或放入高级模式：

```text
策略管理
模拟预测
AI 助手
参数治理
OOS/PBO 诊断
完整监控
提醒推送
```

---

## 4. 功能分层设计

### 4.1 基础层，个人默认启用

| 模块 | 状态 | 说明 |
|---|---|---|
| 数据采集 | 启用 | NAV、元数据、基准数据 |
| 数据质量 | 启用 | 异常、缺失、滞后提示 |
| 基金筛选 | 启用 | 个人核心功能 |
| 基金检索 | 启用 | 个人核心功能 |
| 组合检查 | 启用 | 替代“交易建议”表达 |
| 回测分析 | 启用 | 用于验证思路 |
| 系统设置 | 启用 | 管理配置 |

### 4.2 研究层，按需启用

| 模块 | 状态 | 说明 |
|---|---|---|
| 策略管理 | 研究模式 | 低频研究使用 |
| 模拟预测 | 研究模式 | 仅辅助，不作为决策 |
| 截面因子评分 | 研究模式 | 用于候选池排序 |
| IC 验证 | 研究模式 | 用于模型有效性观察 |
| Walk-forward | 研究模式 | 用于策略稳健性验证 |

### 4.3 高级层，默认隐藏

| 模块 | 状态 | 说明 |
|---|---|---|
| AI 助手 | 默认关闭 | 有成本、隐私和解释风险 |
| 参数集审核 | 默认隐藏 | 偏机构治理 |
| 参数发布/回滚 | 默认隐藏 | 个人使用低频 |
| OOS/PBO 门禁 | 默认隐藏 | 专业但复杂 |
| 多渠道通知 | 默认关闭 | 个人本地系统不必默认开启 |
| Grafana/Alertmanager | 完整模式 | 运维成本较高 |

---

## 5. 本轮执行任务清单

### 任务 1：生成专业优化执行计划文档

目标：在项目中新增一份 Markdown 文档，记录平台优化方向、执行路线和分阶段任务。

建议文件：

```text
docs/personal-platform-optimization-plan.md
```

文档内容包括：

- 平台定位。
- 当前问题。
- 功能分层。
- 个人模式设计。
- 调度模式设计。
- Advisor 重构计划。
- 数据质量增强计划。
- 分阶段验收标准。

验收标准：

- Markdown 文件存在。
- 内容完整、专业、可执行。
- 不包含收益承诺或投资建议表述。

---

### 任务 2：新增个人模式配置

目标：通过配置控制个人默认体验，不直接删除高级功能。

建议修改：

```text
backend/app/core/config.py
```

新增配置项：

```text
personal_mode: bool = Field(default=True, alias="PERSONAL_MODE")
feature_ai: bool = Field(default=False, alias="FEATURE_AI")
feature_advisor_governance: bool = Field(default=False, alias="FEATURE_ADVISOR_GOVERNANCE")
feature_full_monitoring: bool = Field(default=False, alias="FEATURE_FULL_MONITORING")
schedule_mode: Literal["light", "research", "full"] = Field(default="light", alias="SCHEDULE_MODE")
```

说明：

- `PERSONAL_MODE=true` 为个人默认模式。
- 不删除后端能力，只控制默认暴露程度。
- 后续前端可通过 meta/settings 接口读取这些开关。

---

### 任务 3：前端导航个人模式收敛

目标：个人模式下隐藏不适合个人日常使用的高级入口。

建议修改：

```text
frontend/src/components/layout/AppSidebar.tsx
```

初步处理：

- 将“交易建议”显示为“组合检查”。
- 默认隐藏或后续可配置隐藏：
  - AI 助手
  - 策略管理
  - 模拟预测

更稳妥的第一步：

- 先重命名“交易建议”为“组合检查”。
- 将高级功能集中到后续“高级研究”分组，而不是立即删除路由。

---

### 任务 4：前端文案风险降级

目标：避免系统表现得像“投顾建议”或“交易指令”。

建议修改：

```text
frontend/src/pages/Advisor/index.tsx
```

文案替换方向：

| 当前表达 | 建议表达 |
|---|---|
| 交易建议 | 组合检查 |
| 买入 | 可关注增配 |
| 卖出 | 可关注减配 |
| 持有 | 继续观察 |
| 建议金额 | 参考调整金额 |
| 目标仓位 | 参考仓位区间 |

注意：

- 第一轮不建议大规模改动业务逻辑。
- 只改可见文案和风险提示。
- API 字段可暂时保持不变。

---

### 任务 5：调度模式设计落地准备

目标：为后续减少个人运行负担做准备。

建议修改：

```text
backend/app/tasks/schedule.py
```

设计三档调度：

#### light，个人默认

保留：

- daily-benchmark-nav
- daily-nav-ingest
- daily-fund-meta
- weekly-database-backup

可选保留：

- daily-dividends

#### research，研究模式

在 light 基础上增加：

- daily-fund-discovery
- daily-cross-sectional-scoring
- daily-strategy-signals
- monthly-cs-ic-validation
- quarterly-holdings

#### full，完整模式

保留当前全部任务。

第一轮建议只完成结构准备，不强行大改调度。

---

### 任务 6：Advisor 大文件拆分方案固化

目标：先形成明确拆分边界，后续逐步重构。

建议拆分前端：

```text
frontend/src/pages/Advisor/
  index.tsx
  components/
    AdvisorSummary.tsx
    PositionEditor.tsx
    PortfolioCheckPanel.tsx
    FundAdviceTable.tsx
    RiskWarnings.tsx
    AdvisorHistory.tsx
    ExecutionRecords.tsx
    DiagnosticsPanel.tsx
```

建议拆分后端 API：

```text
backend/app/api/v1/advisor/
  __init__.py
  analyze.py
  portfolio.py
  history.py
  positions.py
  execution_records.py
  diagnostics.py
  parameters.py
```

建议拆分后端服务：

```text
backend/app/services/advisor/
  engine.py
  technical.py
  momentum.py
  cross_section.py
  risk_budget.py
  constraints.py
  explanation.py
  data_quality.py
```

本轮不一定执行完整拆分，但应在文档中记录，并避免继续向大文件堆代码。

---

## 6. 后续专业优化路线

### 阶段 A：个人模式可用性

目标：让平台默认打开就是个人可用状态。

任务：

1. 菜单收敛。
2. 文案降级。
3. 默认关闭 AI 和高级治理。
4. 首页突出数据状态和组合状态。
5. 调度轻量化。

### 阶段 B：数据质量主流程化

目标：分析前先判断数据是否可信。

任务：

1. 建立统一数据质量状态对象。
2. 基金详情展示数据质量。
3. 回测前进行数据 gate。
4. 组合检查前进行数据 gate。
5. 结果记录数据版本、复权版本、指标版本。

### 阶段 C：个人版基金评分模型

目标：用稳健筛选替代强预测。

推荐评分结构：

```text
总分 = 收益质量 25%
     + 风险控制 25%
     + 稳定性 15%
     + 成本/规模 15%
     + 基准表现 10%
     + 组合适配 10%
```

所有评分应在同类基金内做分位数归一化。

### 阶段 D：工程重构

目标：降低维护成本。

任务：

1. 拆分 Advisor 前端页面。
2. 拆分 Advisor API。
3. 拆分交易辅助服务。
4. 为拆分后的模块补测试。
5. 保持接口兼容。

---

## 7. 风险控制与合规表达原则

所有页面和报告应坚持：

1. 仅作为个人研究辅助。
2. 不构成投资建议。
3. 不承诺收益。
4. 不输出确定性预测。
5. 明确展示数据日期和数据质量。
6. 明确展示模型局限。
7. 将“买卖指令”改为“关注/检查/参考”。

建议统一风险提示：

```text
本结果仅用于个人基金研究和组合风险辅助，不构成投资建议或交易指令。基金过往业绩不代表未来表现，模型结果依赖数据质量、计算口径和参数假设，请结合自身风险承受能力独立判断。
```

---

## 8. 执行进展

截至本次推进，已完成以下低风险个人化收敛工作：

1. 已新增并维护本执行计划文档。
2. 后端已具备个人模式配置基础：`PERSONAL_MODE`、`FEATURE_AI`、`FEATURE_ADVISOR_GOVERNANCE`、`FEATURE_FULL_MONITORING`、`SCHEDULE_MODE`。
3. 后端已提供 `/v1/settings/features` 功能开关接口，前端可读取个人模式与高级功能开关。
4. 前端侧边栏已按个人模式收敛，默认保留个人核心入口，并将 Advisor 入口表达为“组合检查”。
5. AI 助手入口已支持功能开关控制，直接访问 `/ai` 时也会按 `FEATURE_AI` 做安全拦截。
6. Advisor 页面已强化风险提示，默认表达为个人研究辅助与组合风险检查，不作为投资建议或交易指令。
7. Advisor 高级研究入口已按 `FEATURE_ADVISOR_GOVERNANCE` 控制，默认隐藏 OOS/PBO、Walk-Forward、截面 IC 等专家诊断入口。
8. 调度模式已具备 `light / research / full` 三档结构，Celery Beat 会按 `SCHEDULE_MODE` 加载对应任务集合。
9. 基金详情页已落地“个人研究评分（轻量版）”，基于近一年净值、数据质量、费率和持仓集中度做前端说明层评分，不作为投资建议或交易指令。
10. 回测列表与回测详情页已强化个人研究风险提示，强调历史回测仅用于样本验证，需结合数据质量、样本区间、参数和成本假设解读。
11. 回测详情页 AI 归因报告入口已按 `FEATURE_AI` 控制，默认隐藏高级 AI 辅助能力。
12. 策略配置页已强化为“研究层功能”表达：补充个人研究风险提示，并提示轻量调度模式下不会默认执行完整研究层任务。
13. 策略配置页的 AI 策略生成入口已按 `FEATURE_AI` 控制，个人默认模式下隐藏 AI 生成能力，保留手动策略配置。
14. 策略页的模拟入口、模拟列表页和模拟详情页已补充情景模拟风险提示，强调模拟预测仅用于压力测试和情景观察，不代表未来收益或交易指令。
15. `/v1/settings/features` 已扩展返回当前 `SCHEDULE_MODE` 实际启用与未启用的 Celery Beat 调度任务清单，便于前端展示 light/research/full 的真实任务差异。
16. 系统设置页已补充调度任务清单展示，个人用户可直接确认当前运行档位启用了哪些日常数据、研究或高级任务。
17. 基金详情页已进一步前置“分析前数据检查”：新增净值新鲜度提示，并补充分析区间、最新净值日期等信息，帮助用户先判断数据是否可用于研究。
18. 回测详情页已补充“样本与口径提示”卡片，并根据样本长度、交易次数、净值更新和 NAV 质量情况给出更前置的解读提醒。
19. 前端已新增 `DataTrustNotice` 复用组件，统一承载净值新鲜度、结果解读摘要和样本口径提示，基金详情与回测详情已改为复用同一套数据可信度表达。
20. Advisor/组合检查当前结果区已接入“组合检查数据可信度”面板，基于逐基金 `data_quality` 汇总展示净值新鲜度、样本充足性、质量状态、数据区间、缺口和异常跳变，进一步落实组合检查前/结果前的数据 gate 表达。
21. Advisor 历史详情页已复用同一“组合检查数据可信度”面板，复盘旧检查记录时也能先看到当时逐基金数据质量、样本区间与输入可信度。
22. 已将 Advisor 页面内联的“组合检查数据可信度”面板抽出为 `frontend/src/components/AdvisorDataTrustPanel.tsx`，减少 Advisor 大文件继续膨胀，并为后续组件化拆分打基础。
23. `DataTrustNotice` 已补充统一的数据质量状态文案、颜色和 Alert 类型函数，Advisor 数据可信度面板与基金详情页已改为复用同一套质量状态表达，减少前端重复口径。
24. 已新增 `frontend/src/components/FundDataQualityGate.tsx`，将基金详情页“分析前数据检查”抽成复用组件，统一承载净值新鲜度、NAV 质量状态、分析区间、覆盖率、缺口和异常跳变提示。
25. 已新增 `frontend/src/components/PersonalResearchScoreCard.tsx`，将基金详情页“个人研究评分（轻量版）”展示抽成独立组件，基金详情页仅保留轻量评分计算，为后续迁移到后端统一评分服务预留边界。
26. 已新增 `frontend/src/components/FundDataQualitySnapshot.tsx`，将基金详情页“数据质量快照（近一年）”抽成独立组件，继续统一复用数据质量状态标签与问题列表表达。
27. 已新增 `frontend/src/components/FundMetricsSummary.tsx`，将基金详情页“业绩指标”和“费率信息”两张基础表抽成独立组件，继续减少基金详情页展示层体积。
28. 已新增 `frontend/src/components/FundHoldingsDistribution.tsx`，将基金详情页“持仓分布”中的集中度、行业饼图和重仓股明细抽成独立组件，进一步降低基金详情页展示层复杂度。
29. 已新增 `frontend/src/components/FundBasicInfoCard.tsx`，将基金详情页“基础信息”展示抽成独立组件，使基金详情页进一步向数据编排与计算逻辑收敛。
30. 已新增 `frontend/src/components/FundNavChartCard.tsx`，将基金详情页“净值走势（近一年）”图表展示抽成独立组件，基金详情页继续保留净值图表配置计算并下发给组件展示。
31. 已新增 `frontend/src/utils/personalResearchScore.ts`，将基金详情页轻量个人研究评分计算迁出页面，形成清晰的前端评分工具边界，为后续替换为后端统一评分服务做准备。
32. 已新增 `frontend/src/utils/fundNavChart.ts`，将基金详情页净值走势 ECharts 配置构建逻辑迁出页面，基金详情页进一步收敛为数据获取、指标计算和组件编排。
33. 已新增 `frontend/src/utils/fundMetrics.ts`，将基金详情页业绩指标和费率表数据构建逻辑迁出页面，减少页面内计算细节并便于后续统一指标口径。
34. 已新增 `frontend/src/utils/fundType.ts`，统一基金类型选项、中文文案和标签颜色，基金列表页与基金详情页已改为复用共享基金类型工具。
35. 已新增 `frontend/src/utils/dateRange.ts`，统一常用日期格式化与近一年默认区间，基金详情页 NAV 与数据质量查询已改为复用该日期范围工具。
36. 已新增 `frontend/src/utils/advisorDisplay.ts`，将 Advisor 页面中高频复用的展示格式化、标签文案、颜色映射和导入治理摘要判断逻辑迁出页面，继续推进 Advisor 大文件的低风险逻辑工具化拆分。
37. 已新增 `frontend/src/components/AdvisorNavDataWarnings.tsx`，将 Advisor 页面内联的 NAV 数据更新提示、NAV 质量提示和历史列表质量标签抽成独立组件，进一步减少页面内联展示逻辑并统一组合检查数据可信度表达。
38. 已新增 `frontend/src/components/AdvisorSnapshotVersionLookupPanel.tsx`，将 Advisor 页面内联的原始快照版本查询、短哈希展示和快照下载按钮抽成独立组件，保持审计追溯能力不变，同时继续降低 Advisor 页面体积。
39. 已新增 `frontend/src/components/AdvisorRiskComparisonSection.tsx`，将 Advisor 页面内联的“三档风险对比”卡片、风险档摘要和对比明细表抽成独立组件，保持组合检查结果展示口径不变并继续推进页面组件化拆分。
40. 已新增 `frontend/src/components/AdvisorPositionImportGovernanceSummaryCard.tsx`，将 Advisor 页面内联的持仓导入治理诊断摘要抽成独立组件，保留重复基金、零值持仓和成本异常提示口径不变，进一步降低页面展示层复杂度。
41. 已新增 `frontend/src/components/AdvisorResultSection.tsx`，将 Advisor 页面中复用的结果区通用卡片壳抽成独立组件，保持各结果卡片展示结构不变并减少页面内联 UI 壳代码。
42. 已新增 `frontend/src/components/AdvisorQuickFundTags.tsx`，将 Advisor 页面内联的快速基金标签抽成独立组件，保持最近基金、热门基金和收藏分组的点击选择逻辑不变，继续减少页面内联交互组件。
43. 已新增 `frontend/src/components/AdvisorReminderCenter.tsx`，将 Advisor 页面内联的提醒中心、提醒分类筛选和提醒标签展示抽成独立组件，保留时效、风险、执行和计划四类提醒口径不变。
44. 已新增 `frontend/src/components/AdvisorActiveReminderListCard.tsx`，将历史页的活跃提醒列表、刷新按钮、查看/忽略操作入口和提醒标签展示抽成独立组件，保留提醒 API 与状态流转逻辑不变。
45. 已新增 `frontend/src/components/AdvisorHistoryDetailActions.tsx`，将历史详情顶部的返回列表、更新记录、导出审计 JSON 和加载到表单操作区抽成独立组件，保留操作回调与确认文案不变。
46. 已新增 `frontend/src/components/AdvisorHistoryDetailSummaryCard.tsx`，将历史详情的标题标签、资金/更新时间摘要和六项组合检查统计指标抽成独立组件，保持历史详情数据展示口径不变并继续降低 Advisor 页面体积。
47. 已新增 `frontend/src/components/AdvisorUserProfileSnapshot.tsx`，将历史详情中的“当时投资画像”展示抽成独立组件，保留风险偏好、目标、期限、流动性、回撤承受、月度预算和容忍度标签口径不变。
48. 已新增 `frontend/src/components/AdvisorHistoricalPositionsCard.tsx`，将历史详情中的“当时持仓”表格抽成独立组件，保留市值、份额、成本、买入日期展示以及旧字段 `amount/cost` 兼容逻辑不变。
49. 已新增 `frontend/src/components/AdvisorHistoryListCard.tsx`，将历史页“已保存的检查记录”列表、分页、数据提示标签和查看/更新/删除操作入口抽成独立组件，保留历史记录状态流转与操作回调不变。
50. 已新增 `frontend/src/components/AdvisorAdvancedResearchNotice.tsx`，将 Advisor 高级研究入口说明抽成独立提示组件，保留个人模式默认隐藏和开启高级治理后使用的产品表达不变。
51. 已新增 `frontend/src/components/AdvisorReviewAuditNotice.tsx`，将当前结果区“历史复盘与审计”中的免责声明、保存提示和模型局限性说明抽成独立组件，保留个人研究辅助与审计复盘口径不变。
52. 已新增 `frontend/src/components/AdvisorReferenceActionsSection.tsx`，将当前结果区“参考操作”中的导出审计 JSON、保存检查结果按钮和通用卡片壳抽成独立组件，保留结果明细表和展开详情逻辑不变。
53. 已新增 `frontend/src/components/AdvisorEmptyResultGuide.tsx`，将当前结果为空时的引导空状态和引擎版本说明抽成独立组件，保留生成组合检查提示和基金类型数量展示口径不变。
54. 已新增 `frontend/src/components/AdvisorReasoningNotice.tsx`，将当前结果区“为什么这样判断”中的交易时间、申赎受理日、截止时间、净值日、确认/到账日期和兜底说明抽成独立组件，保留字段展示与风险提示口径不变。
55. 已新增 `frontend/src/components/AdvisorCurrentProfileSection.tsx`，将当前结果区“我的投资情况”中的新手/专家模式提示、本次投资画像标签和未填写画像兜底说明抽成独立组件，保留展示字段与文案口径不变。
56. 已新增 `frontend/src/components/AdvisorCheckResultSummarySection.tsx`，将当前结果区“组合检查结果”中的提醒中心、六项摘要统计和三档风险对比抽成独立组件，保留统计口径、提醒筛选和风险对比展示逻辑不变。
57. 已新增 `frontend/src/components/AdvisorAdviceTable.tsx`，将当前结果与历史详情复用的组合检查明细表抽成轻量组件，保留列定义、滚动宽度和展开详情渲染由 Advisor 页面传入，避免改动业务细节。
58. 已新增 `frontend/src/components/AdvisorExecutionAuditCard.tsx`，将历史详情中的执行审计、NAV/信号/OOS/申赎规则快照表格和原始快照追溯入口抽成独立组件，保留审计字段、折叠结构和数据质量提示口径不变，继续降低 Advisor 页面体积。
59. 已新增 `frontend/src/components/AdvisorTradeTimingCard.tsx`，将组合检查明细中的申赎时间线、截止状态、受理/净值/确认/到账日期和申赎规则提示抽成独立组件，保留交易时间展示口径不变并清理 Advisor 页面内联展示代码。
60. 已新增 `frontend/src/components/AdvisorExecutionPlanTaskList.tsx`，将组合检查明细中的“未来待执行任务”列表、任务时效标签、金额区间、匹配执行状态和触发/最近执行说明抽成独立组件，Advisor 页面继续负责生成任务数据，展示口径不变。
61. 已新增 `frontend/src/components/AdvisorQualityRiskAlerts.tsx`，将组合检查明细中的数据质量提示和过拟合风险提示抽成独立组件，保留质量分、覆盖率、NAV 样本、来源一致性、复权覆盖、OOS/PBO 和门禁提示口径不变，继续减少 Advisor 页面内联风险展示代码。
62. 已新增 `frontend/src/components/AdvisorDecisionAuditCard.tsx`，将专家模式下的“决策审计”卡片、阈值状态、信号贡献表、单一信号主导提示、市场状态和审计备注抽成独立组件，保留审计字段和展示口径不变。
63. 已新增 `frontend/src/components/AdvisorProfileConstraintsCard.tsx`，将组合检查明细中的“投资画像约束”卡片、触发约束筛选和约束效果标签抽成独立组件，保留画像约束展示口径不变并继续减少 Advisor 页面内联 JSX。
64. 已新增 `frontend/src/components/AdvisorTradePlanImpactSection.tsx`，将组合检查明细中的“参考计划”和“组合影响”展示区抽成独立组件，保留参考方式、金额区间、仓位变化、条件触发规则、执行计划任务和集中度风险提示口径不变，Advisor 页面继续负责传入已生成的执行计划任务数据。
65. 已新增 `frontend/src/components/AdvisorValidityRiskNotice.tsx`，将组合检查明细中的检查结果有效期折叠说明和风险提示 Alert 抽成独立组件，保留生成时间、数据截至、失效规则和风险警示列表展示口径不变，进一步减少 Advisor 页面提示类 JSX。
66. 已新增 `frontend/src/components/AdvisorExpertAnalysisSection.tsx`，将专家模式下的动量分析、Bootstrap 预测、风险预算、技术指标和模型局限性展示抽成独立组件，保留高级指标字段、标签颜色和模型局限性折叠展示口径不变；非专家模式下的模型局限性仍保留在 Advisor 页面兜底展示，避免改变原有可见范围。
67. 已新增 `frontend/src/components/AdvisorAdviceOverviewSection.tsx`，将组合检查明细顶部的新手提示、三条主要理由、三条风险提示和参考操作概览抽成独立组件，保留强度说明、主要理由提取、风险摘要、参考金额区间和参考方式展示口径不变，并清理 Advisor 页面中对应的摘要辅助函数。
68. 已新增 `frontend/src/components/AdvisorAdviceExplanationSection.tsx`，将组合检查明细中的“检查结果解释”、风险等级适配性提示和防过拟合可靠性调整提示抽成独立组件，保留解释因子、匹配提示、OOS 来源、IC/PBO、评分/置信度/金额折扣等字段展示口径不变，并清理 Advisor 页面中对应的展示工具导入。
69. 已新增 `frontend/src/components/AdvisorLimitationsNotice.tsx`，将组合检查明细中的“模型局限性”折叠提示抽成轻量复用组件，非专家模式下继续复用该组件兜底展示，保留局限性条数和列表口径不变，并清理 Advisor 页面中不再使用的 Collapse/Panel 依赖。
70. 已新增 `frontend/src/components/AdvisorBacktestPanel.tsx`，将 Advisor 高级研究页签中的“引擎历史验证”面板整体迁出页面，保留基金选择、全部/自定义数据范围、回测运行、核心指标、命中率/信号统计、模拟组合权益曲线、检查结论样本和免责声明展示口径不变，并清理 Advisor 页面中对应的回测 hook、类型与展示依赖。
71. 已新增 `frontend/src/components/AdvisorPerformanceCard.tsx`，将历史详情中的“检查结果跟踪效果（实际表现）”卡片迁出页面，保留执行效果跟踪状态、执行归因、命中率统计、效果标签、逐基金收益、执行状态、采纳和金额偏离展示口径不变，并清理 Advisor 页面中对应的性能跟踪 hook 与效果标签工具导入。
72. 已新增 `frontend/src/components/AdvisorExecutionRecordsCard.tsx`，将历史详情中的“用户实际执行记录”卡片迁出页面，保留执行记录导入、执行归因摘要、计划任务状态、逐基金执行明细、编辑/删除和任务执行记录弹窗逻辑不变；当前页面已切换为引用新组件，并通过 `npm run type-check --prefix frontend` 验证。
73. 已新增 `frontend/src/components/AdvisorOOSStatusCard.tsx`，将 Advisor 高级研究页签中的“OOS 缓存状态”卡片迁出页面，保留缓存覆盖率表、nightly 刷新配置、手动触发刷新、基金池示例和提示口径不变，并清理 Advisor 页面中对应的 OOS hook、类型和刷新图标导入。
74. 已清理 Advisor 页面中已迁出的旧“用户实际执行记录”内联实现，并移除对应的执行记录 hook、执行状态类型、任务上下文类型、列表/Input 组件和执行归因展示工具导入，避免页面保留重复组件代码。
75. 已新增 `frontend/src/components/AdvisorEngineHealthCard.tsx`，将 Advisor 高级研究页签中的“引擎健康度”卡片迁出页面，保留健康状态、滚动 IC、样本量、趋势、增配/减配关注命中率和阈值说明展示口径不变，并清理 Advisor 页面中对应的健康度 hook 导入。
76. 已新增 `frontend/src/components/AdvisorWalkForwardPanel.tsx`，将 Advisor 高级研究页签中的“Walk-Forward 样本外验证”面板迁出页面，保留基金选择、全部/自定义数据范围、OOS 运行、核心指标、baseline 对照、CPCV/PBO 诊断、IC 对比图表、折叠详情和免责声明展示口径不变，并清理 Advisor 页面中对应的 Walk-Forward hook、响应类型、图表和 baseline 工具导入。
77. 已新增 `frontend/src/components/AdvisorCrossSectionalPanel.tsx`，将 Advisor 高级研究页签中的“截面因子选基”面板迁出页面，保留基金类型选择、Top N、截面排名、IC 验证、Top 基金标签、基金评分表和空状态说明不变，并清理 Advisor 页面中对应的截面评分 hook、IC hook、响应类型和统计组件导入。
78. 已新增 `frontend/src/components/AdvisorAdviceDetail.tsx`，将当前结果和历史详情复用的组合检查明细展开详情迁出页面，保留建议概览、交易时机、解释说明、质量风险、决策审计、画像约束、参考计划影响、有效期提示、专家分析和模型局限性展示口径不变，并清理 Advisor 页面中对应的详情子组件导入。
79. 已新增 `frontend/src/components/AdvisorInvestmentProfileFields.tsx`，将手动选基金与基于策略生成组合检查两处重复的“投资画像（可选）”字段抽成复用组件，保留投资目标、期限、流动性、最大回撤、月度预算、集中度、QDII 汇率风险、费率敏感度和三档风险对比开关字段口径不变，并清理 Advisor 页面中对应的画像选项常量与 Switch 导入。
80. 已新增 `frontend/src/components/AdvisorFundSelectionShortcuts.tsx`，将手动选基金表单中的“最近使用 / 自选组合 / 热门基金 / 保存当前选择为自选组合”快捷选择区抽成独立组件，保留快捷标签点击、自选组合应用和保存入口口径不变，并清理 Advisor 页面中对 QuickFundTags 的直接导入。
81. 已新增 `frontend/src/components/AdvisorPositionImportHistoryCard.tsx`，将当前持仓区中的“最近导入历史”卡片迁出页面，保留导入历史分页、状态标签、恢复持仓、治理摘要展开、逐行失败原因和空状态展示口径不变，并清理 Advisor 页面中对应的 Empty、Popconfirm、导入历史状态和治理摘要工具导入。
82. 已新增 `frontend/src/components/AdvisorPositionsEditor.tsx`，将当前持仓区中的持仓行过滤、基金选择、当前市值、持有份额、持仓成本、买入日期、删除和添加持仓按钮迁出页面，保留手动/策略模式下按所选基金池过滤持仓的口径不变，并清理 Advisor 页面中对应的新增/删除图标导入。
83. 已新增 `frontend/src/components/AdvisorPositionsImportControls.tsx`，将当前持仓区中的持仓说明、服务端同步状态、CSV/Excel 模板下载、持仓导入按钮和模板字段提示迁出页面，保留导入格式说明和按钮状态口径不变，并清理 Advisor 页面中对应的 Upload 与上传图标导入。
84. 已新增 `frontend/src/components/AdvisorCapitalRiskSubmitRow.tsx`，将手动选基金和基于策略两处重复的“总可用资金 / 风险偏好 / 提交按钮”行抽成复用组件，保留风险偏好选项、默认表单字段名和提交按钮加载状态口径不变，并清理 Advisor 页面中的 RISK_OPTIONS、InputNumber、Row 与 Col 导入。
85. 已新增 `frontend/src/components/AdvisorAdviceColumns.tsx`，将当前结果与历史详情共用的组合检查明细表列定义、结论图标、强度/数据质量/过拟合标签、专家模式评分列和理由摘要展示迁出页面，Advisor 页面仅保留按视图模式构建列的编排逻辑，并通过 `npm run type-check --prefix frontend` 验证。
86. 已新增 `frontend/src/utils/advisorAuditExport.ts`，将当前结果与历史详情共用的审计 JSON payload 构建逻辑迁出 Advisor 页面，保留导出字段、标签口径和执行计划任务快照不变，并通过 `npm run type-check --prefix frontend` 验证。
87. 已新增 `frontend/src/components/AdvisorPositionImportFailureContent.tsx`，将持仓导入完成弹窗中的失败行表格、治理摘要和复核提示抽成独立组件，保留失败行展示与治理提示口径不变，并进一步清理 Advisor 页面内联 UI 与 Table 导入。
88. 已新增 `frontend/src/utils/advisorPreferences.ts`，将 Advisor 视图模式、最近基金、自选组合与提醒偏好的本地存储 key、读取函数和视图模式文案迁出页面，保留 localStorage 兼容口径不变，并通过 `npm run type-check --prefix frontend` 验证。
89. 已新增 `frontend/src/utils/advisorFundOptions.ts`，将 Advisor 基金选择项类型、单项构建和合并排序逻辑迁出页面，保留额外基金代码兼容与按代码排序口径不变，并通过 `npm run type-check --prefix frontend` 验证。
90. 已新增 `frontend/src/utils/advisorPositions.ts`，将 Advisor 持仓条目类型、本地持仓存储 key、旧字段兼容归一化、已保存持仓读取和服务端持久化 payload 构建逻辑迁出页面，保留去重排序与旧本地缓存兼容口径不变，并通过 `npm run type-check --prefix frontend` 验证。
91. 已新增 `frontend/src/utils/advisorReminderBuilders.ts`，将服务端提醒归一化、当前组合检查结果提醒、历史详情提醒和执行计划到期提醒构建逻辑迁出页面，保留时效、风险、执行和计划四类提醒口径不变，并通过 `npm run type-check --prefix frontend` 验证。
92. 已新增 `frontend/src/utils/fileDownload.ts`，将 JSON 审计文件下载与 Blob 模板文件下载的 DOM 操作抽成通用工具，Advisor 页面改为复用 `downloadJsonFile` 和 `downloadBlobFile`，保留文件名与导出内容口径不变，并通过 `npm run type-check --prefix frontend` 验证。
93. 已新增 `frontend/src/utils/advisorHistoryRestore.ts`，将历史检查详情加载到表单时的持仓恢复、手动模式字段构建和策略模式字段构建逻辑迁出页面，保留历史持仓覆盖、无持仓不清空以及投资画像字段映射口径不变，并通过 `npm run type-check --prefix frontend` 验证。
94. 已扩展 `frontend/src/utils/advisorPositions.ts` 并新增 `frontend/src/utils/advisorRequestPayloads.ts`，将当前持仓市值 map、持仓明细 map、手动组合检查请求和策略组合检查请求的 payload 构建逻辑迁出页面，保留持仓明细旧字段兼容、资金/画像字段和三档风险对比字段传递口径不变，并通过 `npm run type-check --prefix frontend` 验证。
95. 已新增 `frontend/src/utils/advisorSavePayload.ts`，将保存组合检查结果时的基金代码排序、策略元信息、持仓快照、用户画像兜底、结果明细/摘要 payload 构建和高风险保存确认判断迁出页面，保留保存字段与风险确认口径不变，并通过 `npm run type-check --prefix frontend` 验证。
96. 已新增 `frontend/src/utils/advisorDerivedOptions.ts`，将策略下拉选项构建、策略基金池提取和热门基金候选构建逻辑迁出 Advisor 页面，保留策略基金数量、策略基金池兼容和热门基金优先/兜底口径不变，并通过 `npm run type-check --prefix frontend` 验证。
97. 已扩展 `frontend/src/utils/advisorPreferences.ts`，将最近基金去重合并、自选组合构建和自选组合前置保存规则迁出 Advisor 页面，保留最近基金最多 8 个、自选组合最多 6 个、空选择提示和自选组合命名口径不变，并通过 `npm run type-check --prefix frontend` 验证。
98. 已扩展 `frontend/src/utils/advisorPositions.ts`，将空持仓创建、追加持仓、删除指定持仓和更新持仓字段的纯状态更新逻辑迁出 Advisor 页面，保留持仓编辑交互口径不变，并通过 `npm run type-check --prefix frontend` 验证。
99. 已扩展 `frontend/src/utils/advisorReminderBuilders.ts`，将活跃提醒列表所需的服务端提醒到 UI 项包装逻辑迁出 Advisor 页面，保留提醒列表分类、严重级别和原始提醒字段展示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
100. 已扩展 `frontend/src/utils/advisorDerivedOptions.ts`，将基金选择项所需的额外基金代码收集逻辑迁出 Advisor 页面，统一从当前持仓、手动选择、策略基金池、历史详情、当前结果和最近请求元信息中合并代码，保留去重与额外代码补全口径不变，并通过 `npm run type-check --prefix frontend` 验证。
101. 已扩展 `frontend/src/utils/advisorRequestPayloads.ts`，统一 `AdvisorLastRequestMeta` 类型，并将手动组合检查与策略组合检查后的最近请求元信息构建逻辑迁出 Advisor 页面；`advisorSavePayload` 与 `advisorDerivedOptions` 已改为复用同一类型，保留基金代码、策略 ID 和策略名称记录口径不变，并通过 `npm run type-check --prefix frontend` 验证。
102. 已扩展 `frontend/src/utils/advisorSavePayload.ts`，将保存组合检查结果时手动表单与策略表单的用户画像兜底字段合并逻辑迁出 Advisor 页面，保留手动优先、策略兜底、数值字段空值合并和三档风险对比默认值口径不变，并通过 `npm run type-check --prefix frontend` 验证。
103. 已新增 `frontend/src/components/AdvisorPageHeader.tsx`，将 Advisor 页面顶部标题、视图模式切换、个人研究风险提示和个人默认视图高级入口隐藏说明抽成独立组件，保留页面文案与新手/专家模式切换口径不变，并通过 `npm run type-check --prefix frontend` 验证。
104. 已新增 `frontend/src/components/AdvisorAdvancedResearchPanel.tsx`，将高级研究中的引擎验证面板编排和截面选基页签内容迁出 Advisor 页面，保留引擎健康度、OOS 状态、历史验证、Walk-Forward 与截面选基展示顺序和口径不变，并通过 `npm run type-check --prefix frontend` 验证。
105. 已新增 `frontend/src/components/AdvisorCurrentResultPanel.tsx`，将当前组合检查结果区中的投资画像、数据可信度、结果摘要、判断理由、参考操作明细表、历史复盘与审计提示以及空结果引导迁出 Advisor 页面，保留展示顺序、展开详情和风险提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
106. 已新增 `frontend/src/components/AdvisorHistoryDetailPanel.tsx`，将历史详情中的顶部操作、NAV 数据提示、历史提醒、执行审计、投资画像快照、风险对比、数据可信度、检查明细表、执行记录、实际表现、备注和历史持仓迁出 Advisor 页面，保留历史详情展示顺序、展开详情和操作回调口径不变，并通过 `npm run type-check --prefix frontend` 验证。
107. 已新增 `frontend/src/components/AdvisorHistoryPanel.tsx`，将历史页签中的活跃提醒列表、历史详情面板与历史记录列表切换编排迁出 Advisor 页面，保留提醒刷新/忽略、历史查看/刷新/删除、详情返回和加载到表单等回调口径不变，并通过 `npm run type-check --prefix frontend` 验证。
108. 已新增 `frontend/src/components/AdvisorAnalyzePanel.tsx`，将生成组合检查页签中的手动选基金表单、基于策略表单、快捷基金选择、持仓模板下载/导入、导入历史和持仓编辑器编排迁出 Advisor 页面，保留表单初始值、校验、提交、持仓导入恢复和持仓编辑回调口径不变，并通过 `npm run type-check --prefix frontend` 验证。
109. 已新增 `frontend/src/components/AdvisorMainTabs.tsx`，将 Advisor 主页签中的生成组合检查、历史记录、引擎验证和截面选基页签组装迁出页面，Advisor 页面进一步收敛为状态、数据请求和回调编排，保留各页签标签、顺序和高级研究开关口径不变，并通过 `npm run type-check --prefix frontend` 验证。
110. 已扩展 `frontend/src/utils/advisorPreferences.ts` 与 `frontend/src/utils/advisorPositions.ts`，将视图模式、最近基金、自选组合、提醒偏好和本地持仓的 localStorage 保存逻辑迁出 Advisor 页面，保留持久化 key、最近基金 8 个和自选组合 6 个的截断口径不变，并通过 `npm run type-check --prefix frontend` 验证。
111. 已新增 `frontend/src/hooks/useAdvisorPositionsSync.ts`，将 Advisor 服务端持仓水合、首次服务端同步和持仓变化后的延迟保存逻辑迁出页面，保留服务端优先、本地回填、跳过首轮重复同步和 400ms 防抖保存口径不变，并通过 `npm run type-check --prefix frontend` 验证。
112. 已新增 `frontend/src/utils/advisorPositionImportResult.ts`，将持仓导入失败行提取、是否展示复核弹窗、导入完成弹窗标题、导入成功提示和恢复持仓成功提示迁出 Advisor 页面，保留治理告警、失败行和成功提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
113. 已新增 `frontend/src/utils/advisorQueryInvalidation.ts`，将 Advisor 历史、提醒、表现、持仓和持仓导入历史相关的 React Query 失效逻辑迁出页面，减少保存、刷新、忽略、导入和恢复 handler 中的重复 queryKey 细节，并通过 `npm run type-check --prefix frontend` 验证。
114. 已新增 `frontend/src/hooks/useAdvisorHistoryFormLoader.ts`，将历史检查详情加载到生成组合检查表单时的持仓恢复、手动/策略表单切换、字段回填、页签切换和成功提示流程迁出 Advisor 页面，保留历史无持仓不清空、策略优先切换和加载后返回生成页签口径不变，并通过 `npm run type-check --prefix frontend` 验证。
115. 已清理 `frontend/src/pages/Advisor/index.tsx` 中高级研究、执行效果和引擎健康度组件迁出后遗留的空占位注释，使页面尾部不再保留已拆分模块的旧结构标记，并通过 `npm run type-check --prefix frontend` 验证。
116. 已新增 `frontend/src/hooks/useAdvisorAuditExportHandlers.ts`，将当前结果与历史详情的审计 JSON 导出 handler、文件名构建、payload 组装和成功提示迁出 Advisor 页面，保留导出内容、文件命名和提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
117. 已新增 `frontend/src/hooks/useAdvisorPositionEditorActions.ts`，将新增空持仓、删除持仓和更新持仓字段的页面 handler 封装为复用 hook，页面不再直接依赖持仓编辑纯工具函数，保留持仓编辑交互口径不变，并通过 `npm run type-check --prefix frontend` 验证。
118. 已新增 `frontend/src/hooks/useAdvisorFundShortcuts.ts`，将手动追加基金、最近基金记录、自选组合保存和自选组合应用的页面 handler 封装为复用 hook，保留去重、最近基金上限、自选组合命名、空选择提示和切回手动模式口径不变，并通过 `npm run type-check --prefix frontend` 验证。
119. 已新增 `frontend/src/hooks/useAdvisorSaveResult.ts`，将当前组合检查结果保存、用户画像兜底、历史记录失效刷新和高风险保存确认 handler 封装为复用 hook，保留保存 payload、确认弹窗文案、高风险判断和成功/失败提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
120. 已新增 `frontend/src/hooks/useAdvisorReminderInbox.ts`，将活跃提醒查询、提醒列表 UI 项构建、提醒刷新和忽略提醒 handler 封装为复用 hook，保留查询参数、刷新窗口、提醒状态流转和成功/失败提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。
121. 已新增 `frontend/src/hooks/useAdvisorHistoryActions.ts`，将历史记录删除、历史检查刷新、历史详情查看和加载历史参数到表单的页面 handler 编排封装为复用 hook，保留历史/表现查询失效、详情 ID 切换和成功/失败提示口径不变，并通过 `npm run type-check --prefix frontend` 验证。

后续仍需分阶段推进：Advisor 大文件拆分、后端 Advisor API 拆分、交易辅助服务拆分、统一数据质量状态对象，以及将个人评分模型升级为后端统一评分服务。

---

## 9. 本轮执行顺序

按照低风险到高风险执行：

1. 创建 `docs/personal-platform-optimization-plan.md`。
2. 新增后端个人模式配置项。
3. 调整前端侧边栏文案：交易建议 → 组合检查。
4. 增加或强化前端风险提示文案。
5. 为调度模式增加配置设计或最小代码结构。
6. 运行必要检查：
   - 前端 type-check 或 build。
   - 后端相关测试或最小 import 检查。
7. 汇总变更和后续建议。

---

## 10. 验收标准

本轮完成后应满足：

1. 项目中存在专业 Markdown 执行计划。
2. 平台具备个人模式配置基础。
3. 默认产品表达更符合“个人研究辅助”。
4. 高级/机构化功能有明确隐藏或降级路线。
5. 不破坏现有核心功能。
6. 不改变核心量化计算口径。
7. 后续重构路径清晰。

---

## 11. 重要约束

1. 不删除现有核心模块。
2. 不改变回测、复权、因子计算核心口径，除非单独评审。
3. 不做自动交易相关增强。
4. 不强化收益承诺式表达。
5. 不将 AI 输出作为核心决策依据。
6. 所有改动优先保持兼容。
