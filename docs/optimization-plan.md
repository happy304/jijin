# 基金量化回测系统优化计划

> 基于量化金融最佳实践，对现有系统进行系统性优化。
> 按优先级分阶段实施，每个阶段可独立交付。

---

## 现状评估

### 已有能力（✅ 做得好的部分）

- 事件驱动回测引擎，按交易日迭代执行完整事件循环
- 防未来函数机制（BarContext 强制 T-1 数据截止）
- T+N 结算模拟（按基金类型区分：stock T+1、QDII T+2 等）
- 阶梯费率计算（申购外扣法 + 赎回按持有天数）
- 分红/拆分处理
- 限购/暂停申购检查
- 规则链风控引擎（仓位限制、类型敞口、现金保留、回撤熔断、波动率目标）
- 11 种策略实现（DCA × 3、动量轮动、MV 优化、BL 模型、风险平价、择时 × 3、FOF）
- 绩效指标：Sharpe、Sortino、Calmar、最大回撤、胜率、盈亏比
- 归因分析：Fama-French 三/五因子 + Brinson 归因

### 核心缺陷（❌ 需要修复）

| 问题 | 影响 | 严重程度 |
|------|------|----------|
| 交易日历硬编码到 2025 年 | 2026 年回测日期判断错误 | 🔴 阻断性 |
| 无基准对比体系 | 无法衡量超额收益，回测报告不专业 | 🔴 高 |
| 幸存者偏差 | 只用当前活跃基金回测，高估策略收益 | 🟡 中 |
| 无大额赎回限制模拟 | 回测中的赎回可能在实盘无法执行 | 🟡 中 |
| 无交易成本感知调仓 | 频繁小额调仓侵蚀收益 | 🟡 中 |
| 无尾部风险度量 | 无法评估极端行情下的损失 | 🟡 中 |
| 无滚动指标 | 无法判断策略稳定性 | 🟢 低 |

---

## 第一阶段：基础修复（预计 2-3 天）✅ 已完成

> 目标：修复阻断性问题，确保系统在 2026 年正常运行。

### 1.1 交易日历扩展与自动更新 ✅

**问题**：`backend/app/domain/backtest/calendar.py` 中节假日数据硬编码到 2025 年。

**方案**：
- 增加 2026 年节假日数据（已知数据）
- 增加动态日历加载机制：优先从数据库/配置文件读取，回退到硬编码
- 增加日历数据采集任务（从交易所网站获取下一年度休市安排）
- 对超出已知范围的日期，回退到"周末非交易日"规则并记录 WARNING

**涉及文件**：
```
backend/app/domain/backtest/calendar.py      # 核心修改
backend/app/tasks/ingest.py                  # 新增日历采集任务
backend/app/data/models/trading_calendar.py  # 新增模型（可选）
```

### 1.2 基金成立日期数据补全

**问题**：很多基金的 `inception_date` 为 NULL，导致回测日期校验被跳过。

**方案**：
- 在数据采集任务中补全 `inception_date` 字段
- 对已有基金批量回填成立日期
- 采集时将 `inception_date` 设为必填（有数据源时）

**涉及文件**：
```
backend/app/tasks/ingest.py                  # 补全采集逻辑
backend/app/data/fetchers/                   # 数据源适配
```

---

## 第二阶段：基准对比体系（预计 3-4 天）✅ 已完成

> 目标：建立完整的基准对比框架，让回测结果具备专业可比性。

### 2.1 基准数据模型与采集 ✅

**方案**：
- 新增基准指数数据表（沪深300、中证500、中证全债等）
- 在策略配置中支持指定基准（已有 `benchmark` 字段）
- 采集主流指数的日收益率数据

**基准选择建议**：
| 策略类型 | 推荐基准 |
|----------|----------|
| 股票型基金策略 | 沪深300 (000300) |
| 债券型基金策略 | 中证全债 (H11001) |
| 混合型策略 | 60/40 股债组合 |
| 货币基金策略 | 7 天回购利率 |

**涉及文件**：
```
backend/app/data/models/benchmark.py         # 新增基准数据模型
backend/app/tasks/ingest_benchmark.py        # 新增基准采集任务
backend/app/domain/backtest/engine_event.py  # 引擎中跟踪基准净值
```

### 2.2 相对绩效指标

**新增指标**：
- **Alpha**：Jensen's Alpha = R_p - [R_f + β × (R_m - R_f)]
- **Beta**：组合收益对基准收益的回归系数
- **信息比率 (IR)**：(R_p - R_b) / σ(R_p - R_b)
- **跟踪误差 (TE)**：超额收益的标准差，年化
- **Treynor 比率**：(R_p - R_f) / β
- **超额收益**：组合收益 - 基准收益

**涉及文件**：
```
backend/app/domain/backtest/result.py        # 扩展 BacktestMetrics
backend/app/services/performance_service.py  # 扩展分析服务
```

### 2.3 前端基准曲线展示

- 在净值曲线图中叠加基准走势（双 Y 轴或归一化）
- 在指标卡片中展示 Alpha、IR 等相对指标
- 增加超额收益曲线图

**涉及文件**：
```
frontend/src/pages/Backtests/BacktestDetail.tsx
frontend/src/api/backtests.ts
```

---

## 第三阶段：风险度量增强（预计 2-3 天）✅ 已完成

> 目标：补充尾部风险度量和滚动指标，提升风控决策能力。

### 3.1 VaR / CVaR 计算 ✅

**方案**：
- **历史模拟法 VaR(95%)**：取日收益率序列的第 5 百分位
- **CVaR(95%)**：低于 VaR 的所有收益率的均值
- **参数法 VaR**：假设正态分布，μ - 1.645σ
- 在回测结果和风控规则中同时使用

**公式**：
```
VaR(α) = -Percentile(returns, 1-α)
CVaR(α) = -E[R | R ≤ -VaR(α)]
```

**涉及文件**：
```
backend/app/domain/backtest/result.py        # 新增 VaR/CVaR 计算
backend/app/domain/risk/var_limit.py         # 新增 VaR 风控规则（可选）
```

### 3.2 滚动指标计算

**新增**：
- 滚动 Sharpe（60 日窗口）
- 滚动最大回撤（60 日窗口）
- 滚动波动率（20 日窗口）
- 月度收益率序列
- 年度收益率汇总

**数据结构**：
```python
@dataclass
class RollingMetrics:
    dates: list[date]
    rolling_sharpe: list[float]       # 60日滚动Sharpe
    rolling_drawdown: list[float]     # 60日滚动最大回撤
    rolling_volatility: list[float]   # 20日滚动波动率
    monthly_returns: dict[str, float] # "2024-01": 0.023
    yearly_returns: dict[str, float]  # "2024": 0.156
```

**涉及文件**：
```
backend/app/domain/backtest/result.py
backend/app/api/v1/backtests.py              # 新增 rolling 端点
frontend/src/pages/Backtests/BacktestDetail.tsx
```

---

## 第四阶段：交易成本优化（预计 2-3 天）✅ 已完成

> 目标：让策略在决策时感知交易成本，减少无效调仓。

### 4.1 调仓成本阈值 ✅

**问题**：当前 `rebalance_to` 只有 100 元最小金额阈值，不考虑费率。

**方案**：
- 在生成调仓指令前，估算该笔交易的费率成本
- 只有当预期收益（权重偏离 × 预期收益率）> 交易成本时才执行
- 引入 `min_trade_benefit` 参数（默认：交易成本的 2 倍）

**伪代码**：
```python
estimated_fee = amount * subscribe_rate / (1 + subscribe_rate)
expected_benefit = abs(weight_diff) * lookback_return * total_value
if expected_benefit < estimated_fee * cost_multiplier:
    skip_this_trade()
```

**涉及文件**：
```
backend/app/domain/strategy/base.py          # 修改 rebalance_to
backend/app/domain/backtest/engine_event.py  # 传递费率信息到 context
```

### 4.2 换手率统计与限制

**新增指标**：
- 年化换手率 = Σ|交易金额| / 平均组合市值 / 回测年数
- 交易成本占比 = 总费用 / 初始资金
- 单次调仓成本

**新增风控规则**：
```python
class MaxTurnoverRule(RiskRule):
    """限制年化换手率不超过指定倍数。"""
    max_annual_turnover: Decimal  # 如 12.0 表示年换手 12 倍
```

**涉及文件**：
```
backend/app/domain/backtest/result.py        # 换手率计算
backend/app/domain/risk/turnover_limit.py    # 新增规则
```

---

## 第五阶段：幸存者偏差与数据质量（预计 2-3 天）✅ 已完成

> 目标：确保回测数据的完整性和真实性。

### 5.1 已清盘基金数据保留

**方案**：
- 在数据采集中保留已清盘/合并基金的历史数据
- `funds.status` 增加 `liquidated`、`merged` 状态
- 回测时的基金池应包含回测期间内存在过的所有基金

**涉及文件**：
```
backend/app/tasks/ingest.py
backend/app/tasks/backtest.py                # 加载基金池时考虑历史状态
```

### 5.2 数据质量检查增强

**新增检查**：
- 净值跳变检测（单日涨跌幅 > 15% 标记为异常）
- 连续停牌检测（连续 N 天无净值更新）
- 数据缺失率统计
- 回测前自动检查数据完整性，不足时给出警告

**涉及文件**：
```
backend/app/data/validators/nav_validator.py # 增强校验规则
backend/app/tasks/backtest.py                # 回测前数据质量报告
```

---

## 第六阶段：高级功能（预计 4-5 天）✅ 已完成

> 目标：提升系统的专业度和实用性。

### 6.1 Walk-Forward 验证

**方案**：
- 将回测期分为多个滚动窗口：训练期（优化参数）+ 验证期（评估表现）
- 支持配置窗口大小和步进
- 输出每个窗口的 OOS（样本外）绩效
- 计算 WFE（Walk-Forward Efficiency）= OOS Sharpe / IS Sharpe

**API 设计**：
```python
class WalkForwardConfig(BaseModel):
    train_months: int = 12       # 训练窗口
    test_months: int = 3         # 验证窗口
    step_months: int = 3         # 步进
    optimization_target: str = "sharpe"  # 优化目标
```

**涉及文件**：
```
backend/app/domain/backtest/walk_forward.py  # 新增模块
backend/app/api/v1/backtests.py              # 新增端点
```

### 6.2 大额赎回限制模拟

**规则**：
- 单日赎回超过基金净资产 10% 时，可延期确认
- 延期部分按 T+2 或更晚确认
- 巨额赎回时可能触发强制按比例确认

**方案**：
- 在 FundMeta 中增加 `total_shares`（基金总份额）字段
- 引擎确认赎回时检查赎回比例
- 超限时拆分为多笔延期确认

**涉及文件**：
```
backend/app/domain/backtest/engine_event.py  # 赎回确认逻辑
backend/app/domain/backtest/engine_event.py  # FundMeta 扩展
```

### 6.3 C 类份额费率模拟

**方案**：
- 在 FundMeta 中增加 `share_class` 字段（A/C）
- C 类：无申购费，按持有天数收取年化销售服务费（通常 0.4%-0.8%/年）
- 销售服务费在每日净值中已扣除，回测中需模拟这一差异
- 或简化处理：C 类申购费为 0，赎回费按 7 天/30 天阶梯

**涉及文件**：
```
backend/app/domain/backtest/fees.py          # 扩展费率模型
backend/app/domain/backtest/engine_event.py  # 费率选择逻辑
```

### 6.4 持仓集中度与相关性风控

**新增规则**：
```python
class ConcentrationRule(RiskRule):
    """持仓集中度限制（HHI 指数）。"""
    max_hhi: Decimal = Decimal("0.25")  # HHI > 0.25 视为高度集中

class CorrelationRule(RiskRule):
    """持仓相关性限制。"""
    max_avg_correlation: float = 0.8  # 平均相关系数上限
```

**涉及文件**：
```
backend/app/domain/risk/concentration.py     # 新增
backend/app/domain/risk/correlation.py       # 新增
```

---

## 实施时间线

```
第一阶段（基础修复）        ████████░░░░░░░░░░░░░░░░░░  Week 1
第二阶段（基准对比）        ░░░░░░░░████████████░░░░░░  Week 1-2
第三阶段（风险度量）        ░░░░░░░░░░░░░░░░████████░░  Week 2-3
第四阶段（成本优化）        ░░░░░░░░░░░░░░░░░░░░████░░  Week 3
第五阶段（数据质量）        ░░░░░░░░░░░░░░░░░░░░░░████  Week 3-4
第六阶段（高级功能）        ░░░░░░░░░░░░░░░░░░░░░░░░██████  Week 4-5
```

**总预计工时**：15-20 天（单人全职）

---

## 技术原则

1. **向后兼容**：所有新增功能不破坏现有 API 和数据结构
2. **渐进增强**：每个阶段独立可交付，不依赖后续阶段
3. **测试覆盖**：每个新模块配套单元测试 + 集成测试
4. **性能意识**：VaR/滚动指标等计算密集型操作使用 NumPy 向量化
5. **配置驱动**：新功能通过参数控制开关，不强制所有用户使用

---

## 参考资料

- [Seven Sins of Quantitative Investing](https://bookdown.org/palomar/portfoliooptimizationbook/8.2-seven-sins.html) — 量化投资七宗罪
- [Common Backtesting Mistakes](https://quantstrategy.io/blog/7-common-backtesting-mistakes-that-lead-to-false-confidence) — 回测常见错误
- [Walk-Forward Optimization](https://paperswithbacktest.com/wiki/walk-forward-optimization) — 前推验证方法论
- [Slippage in Backtesting](https://www.interactivebrokers.com/campus/ibkr-quant-news/slippage-in-model-backtesting/) — 滑点建模

Content was rephrased for compliance with licensing restrictions.
