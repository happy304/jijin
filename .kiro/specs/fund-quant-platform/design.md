# 基金量化平台 - 技术设计文档

## 概述

本文档描述基金量化平台的技术架构与关键设计决策。平台采用**分层 + 事件驱动**架构，核心设计理念是：

1. **研究与实盘同构** - 策略代码在回测与实盘下无需修改
2. **确定性核心 + AI 外围** - 核心决策链路纯确定性计算，LLM 仅作辅助
3. **数据外部不稳 → 本地稳** - 多源采集 + 本地持久化 + 快照归档
4. **正确性优先于性能** - 优先正确处理 T+1 结算、费率、分红等基金特性

## 技术选型

| 层次 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 语言 | Python | 3.11+ | 量化生态成熟，类型提示完善 |
| Web 框架 | FastAPI | 0.115+ | 异步 + 自动文档 + Pydantic v2 |
| ORM | SQLAlchemy | 2.0+ | 异步支持，类型安全 |
| DB 迁移 | Alembic | 1.13+ | 与 SQLAlchemy 深度集成 |
| 数据库 | PostgreSQL + TimescaleDB | 16 + 2.x | 时序数据首选 |
| 缓存 | Redis | 7.x | 缓存 + Celery backend |
| 任务队列 | Celery | 5.x | 分布式任务成熟方案 |
| 调度 | Celery Beat | 5.x | 与 Celery 一体化 |
| 数据科学 | Pandas / NumPy / Polars / SciPy / statsmodels | latest | 因子计算与归因 |
| HTTP 客户端 | httpx | 0.27+ | 异步 + HTTP/2 |
| 爬虫辅助 | selectolax / parsel | latest | HTML 解析 |
| 备源 | akshare | latest | 备份数据源 |
| LLM 客户端 | openai / anthropic SDK | latest | 统一用 OpenAI 兼容协议 |
| 前端 | React 18 + Vite + TypeScript | latest | 生态成熟 |
| 图表 | ECharts + react-financial-charts | latest | 金融图表表现力强 |
| 状态管理 | TanStack Query + Zustand | latest | 服务端状态 + 客户端状态分离 |
| 测试 | pytest + pytest-asyncio + hypothesis | latest | 单元 + 属性测试 |
| 质量工具 | Ruff + Black + mypy | latest | 格式化 + lint + 类型检查 |
| 容器化 | Docker + Docker Compose | latest | 一键启动 |

## 系统架构

### 总体分层

```
┌───────────────────────────────────────────────────────────────┐
│ 表现层 Presentation                                            │
│   React SPA  ·  ECharts/TradingView  ·  WebSocket 客户端       │
└───────────────────────────┬───────────────────────────────────┘
                            │ HTTPS / WebSocket
┌───────────────────────────▼───────────────────────────────────┐
│ API 网关层 API Gateway                                         │
│   FastAPI  ·  Pydantic 校验  ·  认证(预留)  ·  限流  ·  CORS   │
└───┬───────────────────────┬───────────────────┬───────────────┘
    │                       │                   │
┌───▼──────────┐   ┌────────▼────────┐   ┌──────▼──────────┐
│ 应用服务层    │   │  LLM 辅助服务    │   │ 任务编排层       │
│ FundService  │   │  LLMService     │   │ Celery Worker   │
│ FactorSvc    │   │  PromptRegistry │   │ Celery Beat     │
│ BacktestSvc  │   │  LLMCache       │   │ 每日调度         │
│ StrategySvc  │   └────────┬────────┘   └──────┬──────────┘
│ PerfSvc      │            │                   │
└───┬──────────┘            │                   │
    │                       │                   │
┌───▼───────────────────────▼───────────────────▼──────────────┐
│ 领域核心 Domain Core                                          │
│ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌──────────┐ │
│ │ Factor库    │ │ 回测引擎     │ │ 策略库       │ │ 风控     │ │
│ │ (vector)   │ │ (event)     │ │ (BaseStrat) │ │ Engine   │ │
│ └─────────────┘ └─────────────┘ └─────────────┘ └──────────┘ │
└───────────────────────────┬───────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────┐
│ 数据访问层 DAL                                                 │
│ Repository 模式 · 统一异步接口 · 事务管理                       │
└───┬───────────────────────┬───────────────────┬───────────────┘
    │                       │                   │
┌───▼──────────┐   ┌────────▼────────┐   ┌──────▼──────────┐
│ PostgreSQL   │   │ Redis 缓存       │   │ 冷存储 Snapshot  │
│ + Timescale  │   │                 │   │ Parquet + 原始   │
└──────────────┘   └─────────────────┘   └─────────────────┘
    ▲
    │
┌───┴────────────────────────────────────────────────────────────┐
│ 数据采集层 Data Ingestion                                       │
│ FundDataProvider 抽象                                          │
│ ├─ EastmoneyProvider(主)  ├─ AkshareProvider(备)  ├─ Snapshot  │
│ ├─ 限流器 RateLimiter  ├─ 重试器 Retry  ├─ 熔断器 CircuitBreaker│
│ └─ 代理/UA 轮换  ·  原始响应归档                                 │
└────────────────────────────────────────────────────────────────┘
```

### 模块划分与项目结构

```
fund-quant-platform/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI 入口
│   │   ├── core/                      # 配置、日志、常量
│   │   │   ├── config.py              # Pydantic Settings
│   │   │   ├── logging.py
│   │   │   └── constants.py
│   │   ├── api/                       # HTTP 路由
│   │   │   ├── v1/
│   │   │   │   ├── funds.py
│   │   │   │   ├── factors.py
│   │   │   │   ├── backtests.py
│   │   │   │   ├── strategies.py
│   │   │   │   └── ai.py
│   │   │   └── deps.py                # 依赖注入
│   │   ├── domain/                    # 领域核心
│   │   │   ├── assets/                # Asset 基类、FundAsset
│   │   │   ├── factors/               # 因子库
│   │   │   │   ├── registry.py        # @factor 装饰器注册
│   │   │   │   ├── returns.py
│   │   │   │   ├── risk.py
│   │   │   │   ├── risk_adjusted.py
│   │   │   │   ├── benchmark.py
│   │   │   │   ├── holding.py
│   │   │   │   └── attribution.py
│   │   │   ├── backtest/              # 回测引擎
│   │   │   │   ├── events.py
│   │   │   │   ├── engine_event.py    # 事件驱动引擎
│   │   │   │   ├── engine_vector.py   # 向量化引擎
│   │   │   │   ├── calendar.py        # 交易日历
│   │   │   │   ├── portfolio.py
│   │   │   │   ├── order.py
│   │   │   │   ├── settlement.py      # T+1/T+2 结算
│   │   │   │   └── fees.py            # 申赎费
│   │   │   ├── strategy/              # 策略库
│   │   │   │   ├── base.py            # BaseStrategy
│   │   │   │   ├── dca.py             # 定投
│   │   │   │   ├── momentum.py        # 动量轮动
│   │   │   │   ├── risk_parity.py
│   │   │   │   ├── mean_variance.py
│   │   │   │   ├── timing.py          # 择时
│   │   │   │   └── fof.py
│   │   │   ├── risk/                  # 风控
│   │   │   │   ├── limits.py
│   │   │   │   ├── drawdown_control.py
│   │   │   │   └── vol_target.py
│   │   │   └── performance/           # 绩效分析
│   │   │       ├── metrics.py
│   │   │       ├── brinson.py
│   │   │       └── fama_french.py
│   │   ├── services/                  # 应用服务
│   │   │   ├── fund_service.py
│   │   │   ├── factor_service.py
│   │   │   ├── backtest_service.py
│   │   │   ├── strategy_service.py
│   │   │   └── performance_service.py
│   │   ├── ai/                        # LLM 辅助层
│   │   │   ├── provider.py            # LLMProvider 抽象
│   │   │   ├── providers/
│   │   │   │   ├── openai_compat.py
│   │   │   │   ├── anthropic.py
│   │   │   │   ├── deepseek.py
│   │   │   │   └── tongyi.py
│   │   │   ├── prompts/               # Prompt 模板
│   │   │   ├── schemas/               # LLM 输出 JSON Schema
│   │   │   ├── cache.py
│   │   │   ├── budget.py              # Token 预算
│   │   │   └── use_cases/
│   │   │       ├── nl_query.py        # 自然语言查询
│   │   │       ├── strategy_gen.py
│   │   │       ├── attribution_report.py
│   │   │       ├── announcement_parse.py
│   │   │       └── factor_brainstorm.py
│   │   ├── data/                      # 数据层
│   │   │   ├── providers/             # 数据源适配器
│   │   │   │   ├── base.py            # FundDataProvider
│   │   │   │   ├── eastmoney.py
│   │   │   │   ├── akshare.py
│   │   │   │   ├── composite.py       # 多源编排 fallback
│   │   │   │   └── snapshot.py        # 快照归档
│   │   │   ├── fetchers/              # 网络访问基础设施
│   │   │   │   ├── http_client.py
│   │   │   │   ├── rate_limiter.py
│   │   │   │   ├── retry.py
│   │   │   │   ├── circuit_breaker.py
│   │   │   │   └── proxy_pool.py
│   │   │   ├── validators/            # 数据校验
│   │   │   ├── models/                # SQLAlchemy ORM
│   │   │   ├── repositories/          # Repository
│   │   │   └── cache.py               # Redis 缓存
│   │   ├── tasks/                     # Celery 任务
│   │   │   ├── celery_app.py
│   │   │   ├── schedule.py            # Beat 调度定义
│   │   │   ├── ingest.py              # 数据采集任务
│   │   │   ├── backtest.py            # 回测任务
│   │   │   └── signals.py             # 信号生成
│   │   ├── notify/                    # 告警推送
│   │   │   ├── email.py
│   │   │   ├── wecom.py               # 企业微信
│   │   │   └── telegram.py
│   │   └── observability/             # 监控
│   │       ├── metrics.py             # Prometheus
│   │       └── tracing.py
│   ├── migrations/                    # Alembic
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/                       # API client
│   │   ├── stores/
│   │   └── utils/
│   ├── package.json
│   └── Dockerfile
├── deploy/
│   ├── docker-compose.yml
│   ├── docker-compose.dev.yml
│   └── .env.example
└── docs/
```

## 核心设计

### 1. 数据采集层

#### 1.1 Provider 抽象

所有数据源实现统一接口：

```python
class FundDataProvider(Protocol):
    name: str
    priority: int  # 数字越小优先级越高

    async def fetch_fund_meta(self, code: str) -> FundMeta: ...
    async def fetch_nav_history(
        self, code: str, start: date, end: date
    ) -> list[NavRecord]: ...
    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot: ...
    async def fetch_dividends(self, code: str) -> list[DividendRecord]: ...
    async def fetch_announcements(self, code: str, since: date) -> list[Announcement]: ...
    async def health_check(self) -> HealthStatus: ...
```

#### 1.2 CompositeProvider - 多源编排

```python
class CompositeProvider:
    """按优先级链式调用，主源失败自动降级到备源"""

    async def fetch_nav_history(self, code, start, end):
        errors = []
        for provider in self._providers_by_priority():
            if self._circuit_breaker.is_open(provider.name):
                continue
            try:
                data = await provider.fetch_nav_history(code, start, end)
                await self._snapshot.save_raw(provider.name, code, data)
                return data, provider.name
            except ProviderError as e:
                errors.append((provider.name, e))
                self._circuit_breaker.record_failure(provider.name)
        raise AllProvidersFailedError(errors)
```

**关键特性**：
- 按 priority 顺序尝试（天天基金 priority=1，AkShare priority=2）
- 熔断状态下跳过 provider
- 每次成功后写入原始快照供事后审计
- 记录命中的 provider 名称，便于追踪数据来源

#### 1.3 网络基础设施

**限流器**：令牌桶算法，每个 provider 独立限流（天天基金默认 2 req/s）

**重试策略**：
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
    reraise=True,
)
```

**熔断器**：5 次连续失败 → OPEN 状态 60 秒 → HALF_OPEN 尝试探测

**代理池**：可选，从配置或外部服务加载，失败代理自动剔除

#### 1.4 天天基金具体接口映射

| 数据类型 | 接口 URL | 方法 | 解析方式 |
|---|---|---|---|
| 基础信息 | `fundf10.eastmoney.com/jbgk_{code}.html` | GET | HTML 表格解析 |
| 历史净值 | `api.fund.eastmoney.com/f10/lsjz` | GET(JSON) | JSON |
| 实时估值 | `fundgz.1234567.com.cn/js/{code}.js` | GET(JSONP) | 正则提取 JSON |
| 综合数据 | `fund.eastmoney.com/pingzhongdata/{code}.js` | GET(JS) | 正则 + JS 变量提取 |
| 持仓 | `fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=20&year={year}&month={month}` | GET | HTML 解析 |
| 分红拆分 | `fundf10.eastmoney.com/fhsp_{code}.html` | GET | HTML 解析 |
| 排名榜单 | `fund.eastmoney.com/data/rankhandler.aspx` | GET | JSONP |
| 基金经理 | `fundf10.eastmoney.com/jjjl_{code}.html` | GET | HTML 解析 |
| 公告 | `api.fund.eastmoney.com/f10/JJGG` | GET | JSON |

**必带 Header**：
```
Referer: http://fundf10.eastmoney.com/
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)... (UA 池轮换)
```

### 2. 数据模型

#### 2.1 核心表结构

**基金元数据 funds**（标准表）
```sql
CREATE TABLE funds (
    code VARCHAR(10) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    fund_type VARCHAR(20),          -- stock/bond/mixed/money/qdii/fof/index
    sub_type VARCHAR(40),
    company_id VARCHAR(20),
    inception_date DATE,
    benchmark TEXT,
    management_fee NUMERIC(6,4),    -- 管理费率
    custodian_fee NUMERIC(6,4),
    currency VARCHAR(10) DEFAULT 'CNY',
    status VARCHAR(20) DEFAULT 'active',
    is_purchasable BOOLEAN DEFAULT true,
    purchase_limit NUMERIC(18,2),
    source VARCHAR(20),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_funds_type ON funds(fund_type);
CREATE INDEX idx_funds_company ON funds(company_id);
```

**基金净值 fund_nav**（TimescaleDB 超表）
```sql
CREATE TABLE fund_nav (
    fund_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    unit_nav NUMERIC(12,6),         -- 单位净值
    accum_nav NUMERIC(12,6),        -- 累计净值
    adj_nav NUMERIC(12,6),          -- 前复权净值（算法见 2.2）
    daily_return NUMERIC(10,6),
    status VARCHAR(20),             -- normal/suspended/limited
    source VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (fund_code, trade_date)
);
SELECT create_hypertable('fund_nav', 'trade_date');
CREATE INDEX idx_nav_code_date ON fund_nav(fund_code, trade_date DESC);
```

**持仓快照 fund_holdings**
```sql
CREATE TABLE fund_holdings (
    fund_code VARCHAR(10) NOT NULL,
    report_date DATE NOT NULL,      -- 报告期，季频
    stock_code VARCHAR(20),
    stock_name VARCHAR(100),
    weight NUMERIC(8,4),            -- 占净值比
    shares NUMERIC(20,2),
    market_value NUMERIC(20,2),
    industry VARCHAR(50),
    PRIMARY KEY (fund_code, report_date, stock_code)
);
```

**分红拆分 fund_dividends**
```sql
CREATE TABLE fund_dividends (
    fund_code VARCHAR(10),
    ex_date DATE,                   -- 除权日
    record_date DATE,               -- 权益登记日
    pay_date DATE,                  -- 派息日
    dividend_per_share NUMERIC(10,6),  -- 每份派现
    split_ratio NUMERIC(10,6),      -- 拆分比例，正常为 1
    PRIMARY KEY (fund_code, ex_date)
);
```

**公告 fund_announcements**
```sql
CREATE TABLE fund_announcements (
    id BIGSERIAL PRIMARY KEY,
    fund_code VARCHAR(10),
    title TEXT,
    category VARCHAR(40),           -- LLM 分类结果
    publish_date DATE,
    content_url TEXT,
    parsed_data JSONB,              -- LLM 解析后的结构化字段
    requires_review BOOLEAN DEFAULT false
);
```

**费率表 fund_fees**
```sql
CREATE TABLE fund_fees (
    fund_code VARCHAR(10),
    fee_type VARCHAR(20),           -- subscribe/redeem
    min_amount NUMERIC(20,2),       -- 申购阶梯
    max_amount NUMERIC(20,2),
    min_holding_days INT,           -- 赎回持有期阶梯
    max_holding_days INT,
    rate NUMERIC(8,6),
    PRIMARY KEY (fund_code, fee_type, min_amount, min_holding_days)
);
```

**策略与回测**
```sql
CREATE TABLE strategies (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    strategy_type VARCHAR(40),      -- dca/momentum/risk_parity/...
    params JSONB NOT NULL,
    universe JSONB NOT NULL,        -- 基金池
    benchmark VARCHAR(20),
    created_by VARCHAR(40),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT REFERENCES strategies(id),
    start_date DATE,
    end_date DATE,
    initial_capital NUMERIC(20,2),
    status VARCHAR(20),             -- pending/running/done/failed
    progress NUMERIC(5,2),
    metrics JSONB,                  -- 关键指标摘要
    error_msg TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE backtest_equity (   -- 资金曲线
    run_id BIGINT,
    trade_date DATE,
    equity NUMERIC(20,2),
    cash NUMERIC(20,2),
    position_value NUMERIC(20,2),
    benchmark_value NUMERIC(20,2),
    PRIMARY KEY (run_id, trade_date)
);
SELECT create_hypertable('backtest_equity', 'trade_date');

CREATE TABLE backtest_trades (
    run_id BIGINT,
    trade_id BIGSERIAL,
    order_date DATE,
    confirm_date DATE,
    fund_code VARCHAR(10),
    direction VARCHAR(10),          -- subscribe/redeem
    amount NUMERIC(20,2),
    shares NUMERIC(20,4),
    nav NUMERIC(12,6),
    fee NUMERIC(20,4),
    PRIMARY KEY (run_id, trade_id)
);
```

**LLM 审计**
```sql
CREATE TABLE llm_calls (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(40),
    model VARCHAR(80),
    use_case VARCHAR(60),
    prompt_hash CHAR(64),           -- sha256 用于缓存键
    prompt_text TEXT,
    response_text TEXT,
    prompt_tokens INT,
    completion_tokens INT,
    cost_usd NUMERIC(10,6),
    latency_ms INT,
    success BOOLEAN,
    error_msg TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_llm_calls_date ON llm_calls(created_at DESC);
CREATE INDEX idx_llm_calls_hash ON llm_calls(prompt_hash);
```

#### 2.2 复权净值算法

```
adj_nav_t = unit_nav_t × adj_factor_t
adj_factor_t = ∏(from T to today) [1 / (1 - dividend_t / nav_before_t) × split_ratio_t]
```

每次分红/拆分事件后，全量重算历史 `adj_nav`。这是因子计算的基础字段。

### 3. 因子库设计

#### 3.1 因子注册机制

```python
# registry.py
_FACTOR_REGISTRY: dict[str, FactorDef] = {}

def factor(name: str, category: str, window: Optional[int] = None):
    def decorator(fn):
        _FACTOR_REGISTRY[name] = FactorDef(
            name=name, category=category, window=window, fn=fn
        )
        return fn
    return decorator

@factor("annualized_return", category="return")
def annualized_return(nav: pd.Series, freq: int = 252) -> float:
    total = nav.iloc[-1] / nav.iloc[0] - 1
    years = len(nav) / freq
    return (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
```

#### 3.2 因子分层

```
FactorEngine
├── 单基金单窗口  → returns/risk/risk_adjusted 等
├── 滚动因子     → rolling_sharpe, rolling_beta (按窗口卷积)
├── 截面因子     → cross-sectional ranking / z-score
└── 归因因子     → Fama-French, Brinson (需要基准与因子收益数据)
```

#### 3.3 向量化契约

所有因子函数遵循：
- 输入：`pd.Series` 或 `pd.DataFrame`（日期索引）
- 输出：标量（整段） 或 `pd.Series`（滚动）
- 空数据返回 `np.nan`，不抛异常
- 纯函数，相同输入必相同输出

### 4. 回测引擎设计

#### 4.1 事件驱动引擎

**事件类型**：
```python
class Event(BaseModel):
    timestamp: datetime
    event_type: str

class MarketOpenEvent(Event): ...
class MarketCloseEvent(Event): ...
class NavUpdateEvent(Event):
    fund_code: str
    nav: Decimal
class OrderEvent(Event):
    fund_code: str
    direction: Literal["subscribe", "redeem"]
    amount: Decimal | None          # 金额申购
    shares: Decimal | None          # 份额赎回
class ConfirmEvent(Event):
    order_id: str
    confirmed_shares: Decimal
    confirmed_amount: Decimal
    fee: Decimal
class DividendEvent(Event):
    fund_code: str
    dividend_per_share: Decimal
    reinvest: bool
class RiskEvent(Event):
    reason: str                     # 风控触发原因
```

**事件循环**（伪代码）：

```python
class EventDrivenEngine:
    async def run(self, start, end, strategy, universe, initial_capital):
        self._init(universe, initial_capital)

        for trade_date in self.calendar.trading_days(start, end):
            # 1. 发出市场开盘事件
            self._emit(MarketOpenEvent(timestamp=trade_date))

            # 2. 发出分红事件（如果当日有除权）
            for ev in self._dividend_events_on(trade_date):
                self._emit(ev)

            # 3. 处理上一日未确认的订单（T+1 确认）
            self._confirm_pending_orders(trade_date)

            # 4. 策略 on_bar - 只看得到 trade_date 之前的净值
            context = BarContext(date=trade_date, portfolio=self.portfolio)
            orders = strategy.on_bar(context)

            # 5. 风控检查
            orders = self.risk_engine.validate(orders, self.portfolio)

            # 6. 订单入队，等待 T+1 确认
            for order in orders:
                self._queue_order(order)

            # 7. 日终 - 净值更新（T 日净值在当日 20:00+ 才公布，这里模拟）
            self._emit(MarketCloseEvent(timestamp=trade_date))
            self._update_portfolio_value(trade_date)
            self._snapshot_equity(trade_date)

        return self._build_result()
```

**防止未来函数的关键点**：
- `BarContext.nav(code)` 返回的是**T-1 日**净值（因为 T 日净值在盘后才公布）
- 或者提供 `BarContext.nav_at_close(code)` 但仅在 `MarketCloseEvent` 后可用
- 订单以 **T 日收盘净值**成交，但下单时点无法使用 T 日净值
- 单元测试专门检查 T 日策略决策不依赖 T 日数据

#### 4.2 结算模块（T+1）

```python
class SettlementRule:
    t_plus_confirm: int              # 申购确认 T+N
    t_plus_cash: int                 # 赎回到账 T+N

RULES = {
    "stock":  SettlementRule(1, 4),  # 股票型：T+1 确认，T+4 到账
    "bond":   SettlementRule(1, 3),
    "mixed":  SettlementRule(1, 4),
    "money":  SettlementRule(1, 1),
    "qdii":   SettlementRule(2, 7),
    "index":  SettlementRule(1, 4),
    "fof":    SettlementRule(2, 7),
}
```

#### 4.3 费率模块

查询 `fund_fees` 表，根据金额阶梯或持有天数阶梯计算：

```python
def calc_subscribe_fee(code, amount):
    tier = query_fee_tier(code, "subscribe", amount)
    return amount * tier.rate / (1 + tier.rate)  # 外扣

def calc_redeem_fee(code, shares, nav, holding_days):
    tier = query_fee_tier(code, "redeem", holding_days=holding_days)
    return shares * nav * tier.rate
```

#### 4.4 向量化引擎

用于研究阶段快速迭代：

```python
class VectorBacktest:
    """纯 pandas 向量化，不处理 T+1 和精确费率，速度快 100 倍"""

    def run(self, signals: pd.DataFrame, returns: pd.DataFrame):
        weights = self._normalize_weights(signals)
        # 考虑调仓成本的简化版（单一费率）
        turnover = weights.diff().abs().sum(axis=1)
        costs = turnover * self.cost_bps / 10000
        port_returns = (weights.shift(1) * returns).sum(axis=1) - costs
        equity = (1 + port_returns).cumprod() * self.initial_capital
        return BacktestResult(equity=equity, returns=port_returns)
```

#### 4.5 双引擎一致性校验

每次策略合入前，用同一套数据跑两个引擎，要求最终净值误差 < 0.5%。差异超过阈值时触发告警。

### 5. 策略库设计

#### 5.1 策略基类

```python
class BaseStrategy(ABC):
    name: str
    params: StrategyParams
    universe: list[str]

    def on_init(self, context: StrategyContext) -> None:
        """回测/实盘启动时调用"""

    @abstractmethod
    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日调用，返回订单意图（非 Order）"""

    def on_dividend(self, context: DividendContext) -> None: ...
    def on_order_filled(self, context: FillContext) -> None: ...
```

**OrderIntent** vs **Order**：
- `OrderIntent` 是策略产生的"我想要如此调仓"的意图
- 经过风控、资金检查、限购校验后才转为 `Order` 入队
- 实盘和回测共用同一个意图层

#### 5.2 经典策略实现要点

**动量轮动**：
```python
class MomentumRotation(BaseStrategy):
    params: 
        - lookback_months: int (default 6)
        - top_n: int (default 3)
        - rebalance_freq: 'monthly'

    def on_bar(self, ctx):
        if not ctx.is_rebalance_day():
            return []
        scores = {
            code: ctx.factor("return", window=self.params.lookback_months)
            for code in self.universe
        }
        top_k = sorted(scores, key=scores.get, reverse=True)[:self.params.top_n]
        target_weights = {code: 1/len(top_k) for code in top_k}
        return ctx.rebalance_to(target_weights)
```

**风险平价**：使用 cvxpy 或 scipy.optimize，优化目标 `min Σ(w_i × σ_i - TargetRC)²`

**Black-Litterman**：需要先验预期收益（从 benchmark 反推）+ 用户观点矩阵

#### 5.3 参数优化

- **网格搜索**：多进程并行
- **随机搜索**：Sobol 序列
- **Walk-forward**：滚动训练窗口 + 样本外测试窗口

### 6. 风控模块

```python
class RiskEngine:
    def validate(self, orders, portfolio) -> list[Order]:
        for rule in self.rules:  # 规则链
            orders = rule.apply(orders, portfolio)
        return orders

规则：
- MaxPositionRule（单基金仓位上限）
- MaxTypeExposureRule（单类型仓位上限）
- MaxDrawdownCircuitBreaker（最大回撤熔断）
- VolTargetRule（波动率目标）
- MinCashReserveRule（最小现金保留）
```

熔断触发时：将超限仓位**按比例缩放**到安全线，而非清仓（避免冲击成本）。

### 7. 绩效分析

#### 7.1 核心指标

全量在 `performance/metrics.py` 实现：

- 收益：Total Return、CAGR、Monthly/Annual Return Heatmap
- 风险：Volatility、Downside Deviation、Max Drawdown、VaR/CVaR、Drawdown Duration
- 风险调整：Sharpe、Sortino、Calmar、Information Ratio
- 相关性：Beta、Alpha、R²、Correlation、Tracking Error
- 分布：Skew、Kurtosis、Win Rate、Profit Factor

#### 7.2 Fama-French 归因

```python
r_fund - r_f = α + β_MKT × MKT + β_SMB × SMB + β_HML × HML + (β_RMW × RMW + β_CMA × CMA) + ε
```

国内因子数据采用央财 / 聚宽公开因子或自行构建（中证全指作为市场代理）。

#### 7.3 Brinson 归因

将组合超额收益分解为：
- Allocation Effect：`Σ(w_i - W_i) × R_i_benchmark`
- Selection Effect：`Σ W_i × (r_i - R_i_benchmark)`
- Interaction：`Σ(w_i - W_i) × (r_i - R_i_benchmark)`

### 8. API 设计

#### 8.1 资源与路径

```
GET    /api/v1/funds                      # 基金检索（分页+过滤）
GET    /api/v1/funds/{code}               # 基金详情
GET    /api/v1/funds/{code}/nav           # 净值时间序列
GET    /api/v1/funds/{code}/holdings      # 历史持仓
GET    /api/v1/funds/{code}/factors       # 单基金因子

GET    /api/v1/factors                    # 因子元数据列表
POST   /api/v1/factors/compute            # 批量计算因子

POST   /api/v1/strategies                 # 创建策略
GET    /api/v1/strategies                 # 列出策略
GET    /api/v1/strategies/{id}            # 策略详情
PUT    /api/v1/strategies/{id}
DELETE /api/v1/strategies/{id}

POST   /api/v1/backtests                  # 发起回测（异步）
GET    /api/v1/backtests/{run_id}         # 回测状态/结果
GET    /api/v1/backtests/{run_id}/equity  # 资金曲线
GET    /api/v1/backtests/{run_id}/trades
GET    /api/v1/backtests/{run_id}/attribution
WS     /api/v1/backtests/{run_id}/progress

POST   /api/v1/ai/query                   # 自然语言查询
POST   /api/v1/ai/strategy-gen            # 策略生成
POST   /api/v1/ai/attribution-report      # 归因报告
GET    /api/v1/ai/usage                   # 用量统计

GET    /health
GET    /metrics                           # Prometheus
```

#### 8.2 异步回测流程

```
Client            API              Celery          DB / Redis
  │──POST backtest─▶│                 │                │
  │                 │──enqueue task──▶│                │
  │◀──202 run_id────│                 │                │
  │                 │                 │──update prog─▶│(Redis)
  │──WS subscribe──▶│                 │                │
  │◀──progress──────│◀──pubsub────────│                │
  │◀──complete──────│                 │──save result─▶│(DB)
  │                 │                 │                │
  │──GET /result────▶│──query─────────────────────────▶│
  │◀──report────────│                                  │
```

### 9. LLM 辅助层设计

#### 9.1 Provider 抽象

```python
class LLMProvider(Protocol):
    name: str

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.1,
        response_format: Literal["text", "json"] = "text",
        json_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> LLMResponse: ...

    def estimate_cost(self, prompt_tokens, completion_tokens) -> float: ...
```

**实现**：
- `OpenAICompatProvider`：OpenAI、DeepSeek、智谱、月之暗面（大多数国内厂商兼容 OpenAI 协议）
- `AnthropicProvider`：Claude 系列
- `TongyiProvider`：通义千问（如果不完全兼容）

#### 9.2 统一调用管道

```python
class LLMService:
    async def call(
        self,
        use_case: str,              # 哪个用例
        prompt: str,
        *,
        schema: dict | None = None,
        cache_ttl: int = 7 * 86400,
        preferred_providers: list[str] | None = None,
    ) -> LLMResult:
        
        # 1. 预算检查
        if self.budget.is_exhausted():
            raise BudgetExhaustedError

        # 2. 缓存查询
        key = self._cache_key(use_case, prompt, schema)
        if cached := await self.cache.get(key):
            return cached

        # 3. 选择 provider（按偏好 + 健康状态）
        providers = self._select_providers(preferred_providers)

        # 4. 逐个尝试
        for provider, model in providers:
            try:
                resp = await provider.chat(
                    messages=[...], model=model,
                    response_format="json" if schema else "text",
                    json_schema=schema,
                )
                # 5. Schema 校验
                if schema:
                    parsed = self._validate_json(resp.content, schema)
                else:
                    parsed = resp.content

                # 6. 审计落库 + 缓存
                await self._audit_log(provider, model, prompt, resp, parsed)
                await self.cache.set(key, parsed, cache_ttl)
                self.budget.consume(resp.cost)
                return parsed

            except (ProviderError, ValidationError) as e:
                continue

        raise AllLLMProvidersFailedError
```

#### 9.3 用例设计

**A. 公告解析**
```python
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"enum": ["LIMIT_PURCHASE", "SUSPEND", "DIVIDEND", 
                              "MANAGER_CHANGE", "CONTRACT_CHANGE", "OTHER"]},
        "effective_date": {"type": "string", "format": "date"},
        "details": {"type": "object"},
    },
    "required": ["category"],
}

async def parse_announcement(text: str):
    result = await llm.call(
        use_case="announcement_parse",
        prompt=PROMPT.format(text=text),
        schema=SCHEMA,
    )
    # 规则引擎交叉验证
    if not self._rule_validate(result, text):
        result["requires_review"] = True
    return result
```

**B. 自然语言查询**
使用**两阶段**：
1. LLM 产出查询意图（IR）：`{intent: "search_funds", filters: {...}, sort_by: "sharpe"}`
2. IR → SQL 由代码生成，不由 LLM 直接生成 SQL（避免注入和幻觉）

**C. 归因报告**
只允许 LLM 看到**已经算好的数值**，禁止 LLM 做任何计算：
```python
prompt = f"""
基于以下已计算的归因数据，用中文写一段 300 字内的专业分析：
- Fama-French 归因: {ff_result.to_dict()}
- Brinson 归因: {brinson_result.to_dict()}
- 关键指标: {metrics.to_dict()}

要求：
1. 只解释已给数据，不要推测未提供的信息
2. 不要给出投资建议
"""
```

**D. 因子 brainstorm**
LLM 输出候选因子表达式（受限 DSL），代码端解析 + 执行 IC/IR 测试：
```python
SCHEMA = {
    "type": "object",
    "properties": {
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "formula": {"type": "string"},  # 受限 DSL
                    "rationale": {"type": "string"},
                }
            }
        }
    }
}
```

#### 9.4 预算与降级

```python
class LLMBudget:
    daily_usd_limit: float
    monthly_usd_limit: float
    critical_paths: set[str]        # 关键用例即使超预算也放行

    def is_exhausted(self, use_case=None):
        if use_case in self.critical_paths:
            return False
        return self.today_cost >= self.daily_usd_limit
```

降级策略：
- 首选高质量模型失败 → 降级到便宜模型
- 模型全部失败 → 返回占位文本 "AI 分析暂不可用，请查看原始数据"，不阻塞主流程

### 10. 调度与任务

#### 10.1 定时任务

```python
celery_app.conf.beat_schedule = {
    "daily-nav-ingest": {
        "task": "tasks.ingest.update_daily_nav",
        "schedule": crontab(hour=21, minute=0),  # 每日 21:00
    },
    "weekly-full-refresh": {
        "task": "tasks.ingest.weekly_refresh",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),
    },
    "daily-signal-generate": {
        "task": "tasks.signals.generate_for_subscribed",
        "schedule": crontab(hour=22, minute=0),
    },
    "quarterly-holdings": {
        "task": "tasks.ingest.update_holdings",
        "schedule": crontab(hour=4, minute=0, day_of_month="15-25"),
    },
    "daily-backup": {
        "task": "tasks.backup.dump_db",
        "schedule": crontab(hour=2, minute=0),
    },
}
```

#### 10.2 任务分队列

```
queue: ingest       → 数据采集（低优先级，可并发）
queue: backtest     → 回测计算（高 CPU，单独 worker 池）
queue: ai           → LLM 调用（IO 密集，异步 worker）
queue: notify       → 告警推送（快速）
```

### 11. 监控与告警

#### 11.1 Prometheus 指标

```
fund_ingest_requests_total{provider, status}
fund_ingest_latency_seconds{provider}
fund_data_completeness{fund_code}      # 近 30 日数据完整度
backtest_runs_total{status}
backtest_duration_seconds
llm_calls_total{provider, use_case, status}
llm_cost_usd_total{provider}
llm_tokens_total{provider, type}
task_queue_depth{queue}
```

#### 11.2 告警规则

- 连续 2 个交易日基金数据未更新 → P1
- Provider 熔断 > 10 分钟 → P2
- 回测任务失败率 > 20% → P2
- LLM 日成本超预算 80% → P3
- 任务队列积压 > 1000 → P2

告警通道：邮件 + 企业微信 + Telegram（可选）

### 12. 部署架构

#### 12.1 Docker Compose（开发/单机生产）

```yaml
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    volumes: [pgdata:/var/lib/postgresql/data]
  redis:
    image: redis:7-alpine
  api:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    depends_on: [postgres, redis]
  worker-ingest:
    build: ./backend
    command: celery -A app.tasks worker -Q ingest -c 4
  worker-backtest:
    build: ./backend
    command: celery -A app.tasks worker -Q backtest -c 2
  worker-ai:
    build: ./backend
    command: celery -A app.tasks worker -Q ai -c 8
  beat:
    build: ./backend
    command: celery -A app.tasks beat
  frontend:
    build: ./frontend
    ports: ["5173:80"]
  prometheus:
    image: prom/prometheus
  grafana:
    image: grafana/grafana
```

### 13. 测试策略

- **单元测试**：因子、费率、结算、策略 → pytest
- **属性测试**：因子在随机净值下的边界行为 → hypothesis
- **对拍测试**：关键因子与 AkShare / 天天基金展示值对齐（允许 0.01% 误差）
- **集成测试**：端到端回测流程（小规模数据）
- **契约测试**：数据源适配器返回 schema 稳定
- **双引擎一致性**：事件驱动 vs 向量化

## 关键技术决策

| 决策 | 选择 | 备选 | 理由 |
|---|---|---|---|
| 语言 | Python | Go / Rust | 量化生态压倒性优势 |
| 数据库 | PostgreSQL + Timescale | ClickHouse | 时序 + 关系一体，个人场景足够 |
| 回测引擎 | 自研事件驱动 | Backtrader | Backtrader 对基金 T+1 支持弱 |
| 任务队列 | Celery | ARQ / Dramatiq | 生态最成熟，能满足需求 |
| LLM 协议 | OpenAI 兼容 | 各厂商原生 SDK | 国内厂商大多兼容，切换成本低 |
| API 框架 | FastAPI | Django | 异步 + Pydantic v2 |
| 前端 | React + Vite | Vue / SvelteKit | 图表生态成熟 |
| 图表 | ECharts | TradingView Lib | 免费 + 中文文档好 |

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 天天基金反爬升级 | 高 | 多源 + 代理池 + 快照归档 |
| 基金数据错漏 | 高 | 入库校验 + 多源交叉 + 告警 |
| 回测未来函数 | 高 | 引擎层 time travel 防护 + 针对性测试 |
| LLM 输出幻觉 | 中 | Schema 校验 + 规则交叉 + 红线约束 |
| LLM 成本失控 | 中 | 预算模块 + 缓存 + 降级 |
| 策略过拟合 | 中 | Walk-forward + 样本外隔离 |
| 复杂性累积 | 中 | 接口抽象清晰 + 测试覆盖 + 文档 |

## 演进路线

按需求文档的阶段划分实施，每阶段都可独立交付：

1. **阶段 1 数据底座**：Provider、Ingest、Storage、Validation
2. **阶段 2 因子与指标**：FactorEngine + 常用因子全量
3. **阶段 3 回测引擎**：Event + Vector 双引擎，双引擎一致性校验
4. **阶段 4 策略与风控**：5-8 个经典策略 + RiskEngine
5. **阶段 5 API + 前端**：FastAPI + React，含 LLM 归因报告 + 自然语言查询
6. **阶段 6 调度与监控**：Celery Beat + Prometheus + 告警
7. **阶段 7 AI 数据增强**：公告/季报 LLM 解析
8. **阶段 8 AI 研究增强**：因子 brainstorm
