# API 使用示例

本文档提供基金量化平台 REST API 的使用示例。完整的 API 文档可通过 Swagger UI 访问：http://localhost:8000/docs

## 基础信息

- 基础路径：`/api/v1`
- 数据格式：JSON
- 认证：当前版本无需认证（单用户模式）

---

## 基金数据

### 基金检索

```bash
# 按名称模糊搜索
curl "http://localhost:8000/api/v1/funds?keyword=沪深300&page=1&page_size=20"

# 按类型过滤
curl "http://localhost:8000/api/v1/funds?fund_type=stock&page=1&page_size=50"

# 多条件组合
curl "http://localhost:8000/api/v1/funds?fund_type=mixed&keyword=易方达&page=1&page_size=10"
```

响应示例：

```json
{
  "total": 156,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "code": "110020",
      "name": "易方达沪深300ETF联接A",
      "fund_type": "index",
      "company_id": "80000229",
      "inception_date": "2009-08-26",
      "status": "active"
    }
  ]
}
```

### 基金详情

```bash
curl "http://localhost:8000/api/v1/funds/110020"
```

### 净值查询

```bash
# 查询指定日期范围的净值
curl "http://localhost:8000/api/v1/funds/110020/nav?start=2023-01-01&end=2024-01-01"
```

响应示例：

```json
{
  "fund_code": "110020",
  "data": [
    {
      "trade_date": "2024-01-02",
      "unit_nav": 1.5234,
      "accum_nav": 2.1456,
      "adj_nav": 2.1456,
      "daily_return": 0.0012
    }
  ]
}
```

---

## 因子计算

### 列出可用因子

```bash
curl "http://localhost:8000/api/v1/factors"
```

响应示例：

```json
{
  "factors": [
    {"name": "annualized_return", "category": "return", "window": null},
    {"name": "sharpe", "category": "risk_adjusted", "window": 252},
    {"name": "max_drawdown", "category": "risk", "window": null},
    {"name": "beta", "category": "benchmark", "window": 252}
  ]
}
```

### 批量计算因子

```bash
curl -X POST "http://localhost:8000/api/v1/factors/compute" \
  -H "Content-Type: application/json" \
  -d '{
    "fund_codes": ["110020", "000001", "519300"],
    "factors": ["sharpe", "max_drawdown", "annualized_return"],
    "start": "2020-01-01",
    "end": "2024-01-01",
    "window": 252,
    "frequency": "daily"
  }'
```

响应示例：

```json
{
  "result": {
    "110020": {
      "sharpe": 0.85,
      "max_drawdown": -0.2134,
      "annualized_return": 0.0923
    },
    "000001": {
      "sharpe": 0.62,
      "max_drawdown": -0.3012,
      "annualized_return": 0.0712
    }
  }
}
```

### 单基金因子查询

```bash
curl "http://localhost:8000/api/v1/funds/110020/factors?factors=sharpe,beta,max_drawdown&window=252"
```

---

## 策略管理

### 创建策略

```bash
curl -X POST "http://localhost:8000/api/v1/strategies" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "沪深300动量轮动",
    "strategy_type": "momentum",
    "params": {
      "lookback_months": 6,
      "top_n": 3,
      "rebalance_freq": "monthly",
      "score_factor": "return"
    },
    "universe": ["110020", "000001", "519300", "161725", "001938"],
    "benchmark": "000300"
  }'
```

### 列出策略

```bash
curl "http://localhost:8000/api/v1/strategies"
```

### 更新策略

```bash
curl -X PUT "http://localhost:8000/api/v1/strategies/1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "沪深300动量轮动（优化版）",
    "params": {
      "lookback_months": 3,
      "top_n": 5,
      "rebalance_freq": "weekly"
    }
  }'
```

### 删除策略

```bash
curl -X DELETE "http://localhost:8000/api/v1/strategies/1"
```

---

## 回测

### 发起回测

```bash
curl -X POST "http://localhost:8000/api/v1/backtests" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": 1,
    "start_date": "2019-01-01",
    "end_date": "2024-01-01",
    "initial_capital": 1000000,
    "dividend_mode": "reinvest",
    "engine": "event"
  }'
```

响应（异步，返回 run_id）：

```json
{
  "run_id": 42,
  "status": "pending",
  "message": "回测任务已提交"
}
```

### 查询回测状态

```bash
curl "http://localhost:8000/api/v1/backtests/42"
```

响应示例：

```json
{
  "run_id": 42,
  "status": "running",
  "progress": 65.5,
  "started_at": "2024-01-15T10:30:00Z"
}
```

### WebSocket 订阅进度

```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/backtests/42/progress");
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`进度: ${data.progress}%`);
};
```

### 获取回测结果

```bash
# 资金曲线
curl "http://localhost:8000/api/v1/backtests/42/equity"

# 交易流水
curl "http://localhost:8000/api/v1/backtests/42/trades?page=1&page_size=50"

# 归因分析
curl "http://localhost:8000/api/v1/backtests/42/attribution"
```

资金曲线响应示例：

```json
{
  "run_id": 42,
  "equity": [
    {"trade_date": "2019-01-02", "equity": 1000000, "benchmark_value": 1000000},
    {"trade_date": "2019-01-03", "equity": 1002300, "benchmark_value": 1001500}
  ],
  "metrics": {
    "total_return": 0.4523,
    "annualized_return": 0.0892,
    "sharpe": 1.12,
    "max_drawdown": -0.1534,
    "calmar": 0.58,
    "win_rate": 0.56
  }
}
```

---

## AI 辅助

### 自然语言查询

```bash
curl -X POST "http://localhost:8000/api/v1/ai/query" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "过去一年夏普比率最高的前10只股票型基金"
  }'
```

响应示例：

```json
{
  "intent": "search_funds",
  "result": [
    {"code": "000001", "name": "华夏大盘精选", "sharpe": 1.85}
  ],
  "sql_generated": "SELECT ... (只读查询)",
  "ai_generated": true
}
```

### 策略生成

```bash
curl -X POST "http://localhost:8000/api/v1/ai/strategy-gen" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "每月定投沪深300指数基金，当指数低于250日均线时加倍投入"
  }'
```

响应示例：

```json
{
  "strategy_type": "dca",
  "params": {
    "amount": 1000,
    "frequency": "monthly",
    "smart_mode": true,
    "ma_window": 250,
    "multiplier": 2.0
  },
  "universe": ["110020"],
  "confidence": 0.92,
  "ai_generated": true
}
```

### 归因报告

```bash
curl -X POST "http://localhost:8000/api/v1/ai/attribution-report" \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": 42
  }'
```

### AI 用量统计

```bash
curl "http://localhost:8000/api/v1/ai/usage"
```

响应示例：

```json
{
  "period": "last_30_days",
  "total_calls": 156,
  "total_tokens": 1250000,
  "estimated_cost_usd": 12.50,
  "by_use_case": {
    "nl_query": {"calls": 80, "tokens": 600000},
    "attribution_report": {"calls": 30, "tokens": 400000},
    "strategy_gen": {"calls": 20, "tokens": 150000}
  }
}
```

---

## 通用说明

### 分页

支持分页的接口使用 `page` 和 `page_size` 参数：

```bash
curl "http://localhost:8000/api/v1/funds?page=2&page_size=20"
```

### 错误响应

所有错误返回统一格式：

```json
{
  "error": {
    "code": "FUND_NOT_FOUND",
    "message": "基金代码 999999 不存在",
    "detail": null
  }
}
```

常见 HTTP 状态码：

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 202 | 异步任务已接受 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 422 | 数据校验失败 |
| 500 | 服务器内部错误 |
| 501 | AI 功能已关闭 |

### 健康检查

```bash
curl "http://localhost:8000/health"
```

### Prometheus 指标

```bash
curl "http://localhost:8000/metrics"
```

## 相关文档

- [快速启动指南](./getting-started.md) — 环境搭建
- [架构总览](./architecture.md) — 系统设计
- [内置策略说明](./strategies.md) — 策略参数详解
- [扩展开发指南](./extending.md) — 自定义开发
