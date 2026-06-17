# 内置策略说明

本文档介绍平台内置的 6 类基金量化策略，包括策略逻辑、参数说明和使用建议。

## 策略总览

| 策略类型 | 标识 | 适用场景 | 复杂度 |
|---------|------|---------|--------|
| 定投策略 | `dca` | 长期定投、纪律性投资 | 低 |
| 动量轮动 | `momentum` | 趋势跟踪、基金轮动 | 中 |
| 风险平价 | `risk_parity` | 稳健配置、风险均衡 | 中 |
| 均值-方差 | `mean_variance` | 最优配置、主动观点 | 高 |
| 择时策略 | `timing` | 趋势判断、仓位管理 | 中 |
| FOF 策略 | `fof` | 多因子选基、组合优化 | 高 |

---

## 定投策略（DCA）

### 策略逻辑

定期定额或变额投入基金，通过时间分散降低择时风险。

支持三种模式：

1. **定额定投** — 每期投入固定金额
2. **价值平均** — 使组合价值按目标增长率增长，差额部分补投或赎回
3. **智能定投** — 基于均线偏离度动态调整投入金额（低于均线加倍，高于均线减半）

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `amount` | float | 1000 | > 0 | 每期基础投入金额 |
| `frequency` | str | "monthly" | weekly/biweekly/monthly | 定投频率 |
| `mode` | str | "fixed" | fixed/value_avg/smart | 定投模式 |
| `ma_window` | int | 250 | 20-500 | 智能模式均线窗口（交易日） |
| `multiplier` | float | 2.0 | 1.0-5.0 | 智能模式低位加倍系数 |
| `target_growth_rate` | float | 0.01 | 0.005-0.05 | 价值平均模式月目标增长率 |

### 使用示例

```json
{
  "strategy_type": "dca",
  "params": {
    "amount": 2000,
    "frequency": "monthly",
    "mode": "smart",
    "ma_window": 250,
    "multiplier": 2.0
  },
  "universe": ["110020"],
  "benchmark": "000300"
}
```

### 适用建议

- 适合长期投资（3 年以上）
- 智能模式在震荡市中效果优于定额
- 建议搭配宽基指数基金使用

---

## 动量轮动策略（Momentum）

### 策略逻辑

定期评估基金池中各基金的动量得分，持有得分最高的 Top-N 只基金，等权或按得分加权配置。

得分因子可选：区间收益率、Sharpe 比率、Information Ratio。

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `lookback_months` | int | 6 | 1-24 | 动量回看期（月） |
| `top_n` | int | 3 | 1-20 | 持有基金数量 |
| `rebalance_freq` | str | "monthly" | weekly/monthly/quarterly | 调仓频率 |
| `score_factor` | str | "return" | return/sharpe/ir | 排名因子 |
| `weight_mode` | str | "equal" | equal/score | 权重分配方式 |
| `exclude_bottom` | int | 0 | 0-10 | 排除末位 N 只（避免极端亏损） |

### 使用示例

```json
{
  "strategy_type": "momentum",
  "params": {
    "lookback_months": 6,
    "top_n": 3,
    "rebalance_freq": "monthly",
    "score_factor": "sharpe"
  },
  "universe": ["110020", "000001", "519300", "161725", "001938", "005827"],
  "benchmark": "000300"
}
```

### 适用建议

- 基金池建议 10-30 只，覆盖不同风格/行业
- 趋势市中表现优异，震荡市可能频繁换仓
- 建议配合 Walk-forward 分析验证参数稳健性

---

## 风险平价策略（Risk Parity）

### 策略逻辑

使组合中每只基金对总风险的贡献相等。通过优化求解等风险贡献权重，实现风险层面的"均衡配置"。

优化目标：`min Σ(w_i × σ_i - TargetRC)²`

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `rebalance_freq` | str | "monthly" | weekly/monthly/quarterly | 调仓频率 |
| `lookback_days` | int | 252 | 60-504 | 协方差估计窗口 |
| `cov_method` | str | "sample" | sample/ewm/shrinkage | 协方差估计方法 |
| `ewm_halflife` | int | 63 | 21-252 | 指数加权半衰期（仅 ewm 模式） |
| `risk_budget` | dict | null | - | 自定义风险预算（默认等权） |

### 使用示例

```json
{
  "strategy_type": "risk_parity",
  "params": {
    "rebalance_freq": "monthly",
    "lookback_days": 252,
    "cov_method": "shrinkage"
  },
  "universe": ["110020", "000001", "000003", "000016"],
  "benchmark": "000300"
}
```

### 适用建议

- 适合多资产配置（股票型 + 债券型 + 货币型）
- 收缩估计（shrinkage）在样本量不足时更稳健
- 波动率较低但收益也相对温和

---

## 均值-方差策略（Mean-Variance）

### 策略逻辑

基于 Markowitz 均值-方差优化框架，求解给定风险水平下的最大收益组合。支持 Black-Litterman 模型融入主观观点。

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `rebalance_freq` | str | "monthly" | weekly/monthly/quarterly | 调仓频率 |
| `lookback_days` | int | 252 | 60-504 | 历史数据窗口 |
| `target_return` | float | null | - | 目标年化收益（与 target_risk 二选一） |
| `target_risk` | float | null | - | 目标年化波动率 |
| `max_weight` | float | 0.4 | 0.1-1.0 | 单资产最大权重 |
| `min_weight` | float | 0.0 | 0.0-0.3 | 单资产最小权重 |
| `use_bl` | bool | false | - | 是否使用 Black-Litterman |
| `views` | list | [] | - | BL 观点矩阵 |
| `tau` | float | 0.05 | 0.01-0.5 | BL 不确定性参数 |

### 使用示例

```json
{
  "strategy_type": "mean_variance",
  "params": {
    "rebalance_freq": "quarterly",
    "lookback_days": 504,
    "target_risk": 0.15,
    "max_weight": 0.3,
    "use_bl": true,
    "views": [
      {"asset": "110020", "view": 0.10, "confidence": 0.8}
    ]
  },
  "universe": ["110020", "000001", "000003", "519300"],
  "benchmark": "000300"
}
```

### 适用建议

- 对预期收益估计敏感，建议使用 BL 模型稳定输入
- 设置合理的权重约束避免极端配置
- 适合季度调仓，频繁调仓会增加交易成本

---

## 择时策略（Timing）

### 策略逻辑

基于技术指标或估值指标判断市场趋势，动态调整仓位（满仓/半仓/空仓）。

支持三种择时信号：

1. **双均线** — 短期均线上穿长期均线做多，下穿做空
2. **MACD** — DIF 上穿 DEA 做多，下穿做空
3. **估值分位数** — 指数 PE 低于历史分位数加仓，高于减仓

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `signal_type` | str | "dual_ma" | dual_ma/macd/valuation | 择时信号类型 |
| `short_window` | int | 20 | 5-60 | 短期均线窗口 |
| `long_window` | int | 60 | 20-250 | 长期均线窗口 |
| `macd_fast` | int | 12 | 5-30 | MACD 快线周期 |
| `macd_slow` | int | 26 | 10-60 | MACD 慢线周期 |
| `macd_signal` | int | 9 | 5-20 | MACD 信号线周期 |
| `valuation_index` | str | "000300" | - | 估值参考指数 |
| `low_percentile` | float | 0.3 | 0.1-0.5 | 低估阈值分位数 |
| `high_percentile` | float | 0.7 | 0.5-0.9 | 高估阈值分位数 |
| `position_levels` | list | [0, 0.5, 1.0] | - | 仓位档位 |

### 使用示例

```json
{
  "strategy_type": "timing",
  "params": {
    "signal_type": "dual_ma",
    "short_window": 20,
    "long_window": 60
  },
  "universe": ["110020"],
  "benchmark": "000300"
}
```

### 适用建议

- 趋势明显的市场中效果好，震荡市容易被反复止损
- 估值择时适合长周期（年度级别）
- 建议与定投策略组合使用

---

## FOF 策略

### 策略逻辑

多因子打分筛选优质基金 + 组合优化确定权重。分两步：

1. **筛选阶段** — 对基金池中每只基金计算多个因子得分，加权汇总后选出 Top-N
2. **配置阶段** — 对筛选出的基金进行组合优化（等权/风险平价/MV）

### 参数

| 参数 | 类型 | 默认值 | 范围 | 说明 |
|------|------|--------|------|------|
| `rebalance_freq` | str | "quarterly" | monthly/quarterly | 调仓频率 |
| `top_n` | int | 10 | 3-30 | 最终持有基金数 |
| `factor_weights` | dict | - | - | 因子权重配置 |
| `optimization` | str | "equal" | equal/risk_parity/mv | 权重优化方法 |
| `max_single_weight` | float | 0.2 | 0.05-0.5 | 单基金最大权重 |
| `min_history_days` | int | 252 | 60-504 | 最短历史数据要求 |
| `exclude_types` | list | [] | - | 排除的基金类型 |

### 因子权重配置示例

```json
{
  "factor_weights": {
    "sharpe": 0.3,
    "max_drawdown": 0.2,
    "annualized_return": 0.2,
    "information_ratio": 0.15,
    "manager_tenure": 0.15
  }
}
```

### 使用示例

```json
{
  "strategy_type": "fof",
  "params": {
    "rebalance_freq": "quarterly",
    "top_n": 10,
    "factor_weights": {
      "sharpe": 0.3,
      "max_drawdown": 0.2,
      "annualized_return": 0.2,
      "information_ratio": 0.15,
      "manager_tenure": 0.15
    },
    "optimization": "risk_parity",
    "max_single_weight": 0.15
  },
  "universe": ["全市场股票型+混合型基金池"],
  "benchmark": "000300"
}
```

### 适用建议

- 适合大规模基金池（100+ 只）的系统化筛选
- 季度调仓较为合理，避免过度交易
- 因子权重建议通过 Walk-forward 分析确定

---

## 风控配置

所有策略均可叠加风控规则：

```json
{
  "risk_rules": {
    "max_position": 0.4,
    "max_type_exposure": 0.6,
    "max_drawdown_limit": 0.15,
    "min_cash_reserve": 0.05,
    "vol_target": 0.12
  }
}
```

| 规则 | 说明 |
|------|------|
| `max_position` | 单基金最大仓位比例 |
| `max_type_exposure` | 单类型基金最大仓位比例 |
| `max_drawdown_limit` | 最大回撤熔断阈值（触发后缩仓） |
| `min_cash_reserve` | 最小现金保留比例 |
| `vol_target` | 目标年化波动率（动态调整杠杆） |

---

## 参数优化

平台支持对策略参数进行系统化优化：

### 网格搜索

```bash
curl -X POST "http://localhost:8000/api/v1/backtests" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": 1,
    "optimization": {
      "method": "grid",
      "param_grid": {
        "lookback_months": [3, 6, 12],
        "top_n": [3, 5, 10]
      },
      "metric": "sharpe"
    }
  }'
```

### Walk-Forward 分析

```bash
curl -X POST "http://localhost:8000/api/v1/backtests" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": 1,
    "walk_forward": {
      "train_months": 24,
      "test_months": 6,
      "step_months": 3
    }
  }'
```

---

## 自定义策略

如需编写自定义策略，请参考 [扩展开发指南](./extending.md#新增策略)。

## 相关文档

- [API 使用示例](./api.md) — 完整 API 调用方式
- [架构总览](./architecture.md) — 回测引擎设计
- [扩展开发指南](./extending.md) — 自定义策略开发
