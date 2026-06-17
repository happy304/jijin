# 扩展开发指南

本文档说明如何在不修改核心代码的前提下扩展平台功能。

## 新增数据源

平台已内置三个数据源，按优先级链式调用：

| 数据源 | Provider 类 | priority | 特点 |
|--------|------------|----------|------|
| 天天基金 | `EastmoneyProvider` | 1（主源） | 零售端数据最全，HTML/JSONP 解析 |
| AkShare | `AkshareProvider` | 2（备源） | 开源库封装，DataFrame 输出 |
| 巨潮资讯 | `CnInfoProvider` | 3（兜底） | 证监会法定披露平台，数据权威 |

`CompositeProvider` 按 priority 顺序尝试，主源失败自动降级到备源，集成熔断器避免雪崩。

### 已实现：CnInfoProvider（巨潮资讯网）

**选型理由：**

- **完全免费**：无需注册、无需 Token、无调用次数限制
- **数据权威**：巨潮资讯是中国证监会指定的信息披露网站，基金公告、分红等数据为法定披露源
- **与天天基金互补**：天天基金偏向零售展示（可能有延迟/加工），巨潮是原始披露数据
- **结构化 API**：提供 JSON 接口（`webapi.cninfo.com.cn`），比 HTML 爬虫更稳定

**接口映射：**

| Protocol 方法 | 巨潮接口 | 说明 |
|--------------|---------|------|
| `fetch_fund_meta` | `/api/stock/p_public0001` | 基金基本信息 |
| `fetch_nav_history` | `/api/fund/fundNavList` | 基金净值（含分页） |
| `fetch_holdings` | `/api/fund/fundPortfolio` | 季报持仓（定期报告解析） |
| `fetch_dividends` | `/api/fund/fundDividend` | 分红公告（法定披露源） |
| `fetch_announcements` | `/api/disc/announcement` | 公告（巨潮核心优势，含自动分类） |
| `health_check` | 轻量级接口探活 | 检测连通性 |

**实现路径：** `backend/app/data/providers/cninfo.py`

**注册方式：** 在 `app/tasks/ingest.py` 的 `_get_composite_provider()` 中自动加载：

```python
from app.data.providers.cninfo import CnInfoProvider

providers = [EastmoneyProvider(), AkshareProvider(), CnInfoProvider()]
composite = CompositeProvider(providers=providers, ...)
```

### 自定义新增数据源

如需接入其他数据源，在 `backend/app/data/providers/` 下创建新文件：

```python
# backend/app/data/providers/my_source.py
from app.data.providers.base import FundDataProvider, HealthStatus
from app.data.schemas.funds import FundMeta, NavRecord, HoldingSnapshot, DividendRecord, Announcement
from datetime import date


class MySourceProvider(FundDataProvider):
    name = "my_source"
    priority = 4  # 数字越小优先级越高

    async def fetch_fund_meta(self, code: str) -> FundMeta:
        # 实现基金元数据获取
        ...

    async def fetch_nav_history(self, code: str, start: date, end: date) -> list[NavRecord]:
        # 实现历史净值获取
        ...

    async def fetch_holdings(self, code: str, quarter: str) -> HoldingSnapshot:
        ...

    async def fetch_dividends(self, code: str) -> list[DividendRecord]:
        ...

    async def fetch_announcements(self, code: str, since: date) -> list[Announcement]:
        ...

    async def health_check(self) -> HealthStatus:
        # 返回数据源健康状态
        ...
```

### 注册到 CompositeProvider

在 `app/tasks/ingest.py` 的 `_get_composite_provider()` 中加入新 Provider：

```python
from app.data.providers.my_source import MySourceProvider

providers = [
    EastmoneyProvider(),   # priority=1
    AkshareProvider(),     # priority=2
    CnInfoProvider(),      # priority=3
    MySourceProvider(),    # priority=4
]
composite = CompositeProvider(providers=providers, ...)
```

CompositeProvider 会按 priority 顺序尝试，主源失败自动降级到备源。

### Provider 开发注意事项

- 所有方法必须为 `async`，同步库需用 `asyncio.to_thread()` 包装
- 失败时抛出 `ProviderError` 子类（`ProviderTimeoutError` / `ProviderNotFoundError`）
- 接入 `RateLimiter` 控制请求频率，避免被封 IP
- 接入 `SnapshotArchive` 保存原始响应，便于事后审计和问题排查
- 不支持的接口返回空数据或抛出 `ProviderNotFoundError`，CompositeProvider 会自动降级

---

## 新增因子

使用 `@factor` 装饰器注册，无需修改注册表代码。

### 已实现因子一览

平台当前注册了 **30 个因子**，覆盖 7 个类别：

| 类别 | 模块 | 因子示例 |
|------|------|---------|
| return | `factors/returns.py` | `total_return`, `annualized_return`, `excess_return`, `jensen_alpha` |
| risk | `factors/risk.py` | `volatility`, `max_drawdown`, `downside_deviation`, `var`, `cvar`, `calmar` |
| risk_adjusted | `factors/risk_adjusted.py` | `sharpe_ratio`, `sortino_ratio`, `information_ratio` |
| benchmark | `factors/benchmark.py` | 基准相关因子 |
| holding | `factors/holding.py` | 持仓集中度等因子 |
| manager | `factors/manager.py` | 基金经理相关因子 |
| trend | `factors/trend.py` | `trend_strength`, `momentum_decay`, `dual_momentum` |

### 1. 创建因子函数

在 `backend/app/domain/factors/` 下创建或编辑文件：

```python
# backend/app/domain/factors/my_factors.py
import numpy as np
import pandas as pd
from app.domain.factors.registry import factor


@factor("my_custom_factor", category="custom", window=60)
def my_custom_factor(nav: pd.Series, window: int = 60) -> float:
    """自定义因子：示例计算逻辑"""
    if len(nav) < window:
        return np.nan
    
    # 你的因子计算逻辑
    returns = nav.pct_change().dropna()
    result = returns.rolling(window).mean().iloc[-1] / returns.rolling(window).std().iloc[-1]
    return float(result)
```

### 2. 注册因子模块

在 `backend/app/domain/factors/__init__.py` 中添加导入：

```python
import app.domain.factors.my_factors  # noqa: F401
```

### 3. 因子契约

所有因子函数必须遵循：

- **输入**：`pd.Series`（日期索引的净值序列）或 `pd.DataFrame`
- **输出**：`float`（整段计算）或 `pd.Series`（滚动计算）
- **空数据**：返回 `np.nan`，不抛异常
- **确定性**：相同输入必须产生相同输出

### 4. 验证

注册后可通过 API 查询：

```bash
# 列出所有因子（包含新注册的）
curl http://localhost:8000/api/v1/factors

# 计算因子
curl -X POST http://localhost:8000/api/v1/factors/compute \
  -H "Content-Type: application/json" \
  -d '{"fund_codes": ["000001"], "factors": ["my_custom_factor"], "start": "2020-01-01", "end": "2024-01-01"}'
```

---

## 新增策略

继承 `BaseStrategy` 并实现 `on_bar` 方法。

### 已实现策略一览

平台当前注册了 **12 个策略**：

| 策略名 | 模块 | 说明 |
|--------|------|------|
| `fixed_amount_dca` | `strategy/dca.py` | 固定金额定投 |
| `smart_dca` | `strategy/dca.py` | 智能定投（根据估值调整金额） |
| `value_averaging_dca` | `strategy/dca.py` | 价值平均定投 |
| `momentum_rotation` | `strategy/momentum.py` | 动量轮动（Top-N 等权） |
| `risk_parity` | `strategy/risk_parity.py` | 风险平价 |
| `mean_variance` | `strategy/mean_variance.py` | 均值方差优化 |
| `black_litterman` | `strategy/mean_variance.py` | Black-Litterman 模型 |
| `dual_ma` | `strategy/timing.py` | 双均线择时 |
| `macd_timing` | `strategy/timing.py` | MACD 择时 |
| `valuation_timing` | `strategy/timing.py` | 估值择时 |
| `fof` | `strategy/fof.py` | FOF 组合策略 |
| `mean_reversion` | `strategy/mean_reversion.py` | 均值回归 |

### 1. 创建策略类

在 `backend/app/domain/strategy/` 下创建文件：

```python
# backend/app/domain/strategy/my_strategy.py
from app.domain.strategy.base import BaseStrategy, StrategyParams, rebalance_to
from app.domain.backtest.engine_event import BarContext
from app.domain.backtest.order import OrderIntent
from pydantic import Field


class MyStrategyParams(StrategyParams):
    """策略参数定义（类型、范围、默认值）"""
    lookback: int = Field(default=20, ge=5, le=252, description="回看窗口（交易日）")
    threshold: float = Field(default=0.05, ge=0.01, le=0.5, description="触发阈值")
    rebalance_days: int = Field(default=28, ge=7, le=90, description="调仓间隔天数")


class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def __init__(self, params=None, universe=None):
        super().__init__(params=params, universe=universe)
        self._last_rebalance_date = None

    def on_bar(self, context: BarContext) -> list[OrderIntent]:
        """每个交易日调用，返回订单意图"""
        # 检查调仓间隔
        if self._last_rebalance_date is not None:
            days_since = (context.date - self._last_rebalance_date).days
            if days_since < self.params.rebalance_days:
                return []

        # 获取历史净值（只能看到 T-1 及之前的数据）
        nav_series = context.nav_series(self.universe[0])
        if len(nav_series) < self.params.lookback:
            return []

        # 你的策略逻辑...
        target_weights = {code: 1.0 / len(self.universe) for code in self.universe}
        self._last_rebalance_date = context.date
        return rebalance_to(context, target_weights)
```

### 2. 注册策略

策略通过两种方式自动加载：

**方式一：内置策略（推荐）**

在 `app/domain/strategy/registry.py` 的 `load_builtin_strategies()` 中添加模块路径：

```python
builtin_modules = [
    ...
    "app.domain.strategy.my_strategy",
]
```

**方式二：目录扫描**

将策略文件放在 `backend/app/domain/strategy/` 目录下，`StrategyRegistry.load_from_directory()` 会自动发现。

**方式三：Entry Points（第三方包）**

在 `pyproject.toml` 中声明：

```toml
[project.entry-points."fundquant.strategies"]
my_strategy = "my_package.strategies:MyStrategy"
```

### 3. 策略接口说明

`BaseStrategy` 提供以下生命周期方法：

| 方法 | 调用时机 | 用途 |
|------|---------|------|
| `on_init(context)` | 回测/实盘启动时 | 初始化状态 |
| `on_bar(context)` | 每个交易日 | 生成订单意图（必须实现） |
| `on_dividend(context, fund_code, amount)` | 分红事件发生时 | 处理分红逻辑 |
| `on_order_filled(context, fill)` | 订单确认时 | 记录成交信息 |

`BarContext` 提供的数据访问（受未来函数限制）：

| 方法/属性 | 说明 |
|-----------|------|
| `context.nav(code, date)` | 获取指定日期净值（截止 T-1） |
| `context.nav_series(code)` | 获取完整净值序列（截止 T-1） |
| `context.portfolio` | 当前组合对象 |
| `context.cash` | 当前可用现金 |
| `context.positions` | 当前持仓 {code: shares} |
| `context.date` | 当前交易日（T 日） |

辅助函数：

| 函数 | 说明 |
|------|------|
| `rebalance_to(context, weights)` | 根据目标权重生成最小化调仓指令 |

---

## 新增 Broker（实盘接口）

实现 `Broker` 协议即可对接实盘交易系统。

### 已实现 Broker

| Broker | 文件 | 说明 |
|--------|------|------|
| `PaperBroker` | `broker/paper.py` | 纸面撮合（T+1 结算，用于回测） |
| `SimulatedBroker` | `broker/simulated.py` | 模拟实盘（含滑点、延迟、部分成交、市场冲击） |

### 1. 实现 Broker 接口

```python
# backend/app/domain/broker/my_broker.py
from decimal import Decimal
from app.domain.broker.base import Broker
from app.domain.backtest.order import Order, OrderIntent, OrderStatus


class MyBroker:
    """对接某券商/基金销售平台的 Broker 实现。

    实现 Broker Protocol 的 5 个方法即可。
    """

    def submit_order(self, intent: OrderIntent) -> Order:
        """提交订单意图，返回正式订单"""
        # 调用券商 API
        ...

    def cancel_order(self, order_id: str) -> bool:
        """取消未确认的订单"""
        ...

    def get_positions(self) -> dict[str, Decimal]:
        """获取当前持仓 {fund_code: shares}"""
        ...

    def get_cash(self) -> Decimal:
        """获取可用现金余额"""
        ...

    def get_order_status(self, order_id: str) -> OrderStatus | None:
        """查询订单状态"""
        ...
```

### 2. 策略代码无需修改

策略层通过依赖注入使用 Broker，同一策略代码在回测和实盘下均可运行：

```python
from app.domain.backtest.engine_event import EventDrivenEngine
from app.domain.broker.paper import PaperBroker
from app.domain.broker.simulated import SimulatedBroker

# 回测模式（理想撮合）
engine = EventDrivenEngine()

# 模拟实盘（含市场摩擦）
broker = SimulatedBroker(
    initial_cash=Decimal("100000"),
    slippage_bps=5,        # 5 基点滑点
    fill_ratio=0.95,       # 95% 成交率
    delay_probability=0.1, # 10% 延迟概率
    seed=42,               # 固定随机种子
)

# 实盘模式（未来）
broker = MyBroker(api_key="...")
```

### SimulatedBroker 特性

`SimulatedBroker` 在 `PaperBroker` 基础上增加了真实市场摩擦模拟：

| 特性 | 参数 | 说明 |
|------|------|------|
| 随机滑点 | `slippage_bps=5` | 成交价格正态分布偏移（基点） |
| 部分成交 | `fill_ratio=0.95` | 大额订单可能只成交一部分 |
| 市场冲击 | `impact_bps_per_million=2` | 每百万元额外成本（基点） |
| 订单延迟 | `delay_probability=0.1` | 模拟网络/系统延迟 |
| 可复现 | `seed=42` | 固定随机种子确保结果一致 |

---

## 新增资产类型

通过继承 `Asset` 基类扩展新资产类型（如股票、债券、ETF）。

### 已实现资产类型

| 类 | asset_type | settlement_days | 说明 |
|----|-----------|-----------------|------|
| `FundAsset` | `"fund"` | 1 | 开放式基金（默认） |
| `MoneyFundAsset` | `"money_fund"` | 0 | 货币基金（T+0） |
| `ETFAsset` | `"etf"` | 1 | 交易所 ETF |
| `StockAsset` | `"stock"` | 1 | 股票 |
| `BondAsset` | `"bond"` | 0 | 债券（T+0） |

### Asset 基类接口

`Asset` 基类定义在 `backend/app/domain/assets/base.py`：

| 属性/方法 | 说明 |
|-----------|------|
| `asset_type` | 资产类型标识（如 "fund", "stock", "bond", "etf"） |
| `settlement_days` | 结算天数（T+N 中的 N） |
| `trading_unit` | 最小交易单位（基金 1 份，股票 100 股） |
| `price_tick` | 最小价格变动单位 |
| `calc_fee(amount, direction)` | 计算交易费用（抽象方法，必须实现） |
| `calc_stamp_tax(amount, direction)` | 计算印花税（默认返回 0，股票子类覆盖） |
| `calc_total_cost(amount, direction)` | 计算总交易成本（手续费 + 印花税） |
| `validate_order(amount, shares, nav)` | 校验订单合法性 |
| `calc_settlement_date(trade_date)` | 计算结算日期（跳过周末） |

### 创建自定义资产类型

```python
# backend/app/domain/assets/my_asset.py
from decimal import Decimal
from app.domain.assets.base import Asset
from app.domain.assets.registry import register_asset


class ConvertibleBondAsset(Asset):
    """可转债资产类型"""
    asset_type = "convertible_bond"
    settlement_days = 0       # T+0 结算
    trading_unit = 10         # 最小交易单位 10 张
    price_tick = Decimal("0.001")

    def calc_fee(self, amount: Decimal, direction: str) -> Decimal:
        """可转债佣金：万分之二，最低 1 元"""
        fee = amount * Decimal("0.0002")
        return max(fee, Decimal("1")).quantize(Decimal("0.01"))


# 注册
register_asset(ConvertibleBondAsset())
```

### 注册新资产类型

资产类型通过 `AssetRegistry` 注册，策略和回测引擎通过 `asset_type` 字符串查找：

```python
from app.domain.assets.registry import register_asset, get_asset

# 注册自定义资产类型
register_asset(ConvertibleBondAsset())

# 使用时通过 asset_type 获取
asset = get_asset("convertible_bond")
fee = asset.calc_fee(amount=Decimal("10000"), direction="buy")
settle_date = asset.calc_settlement_date(trade_date=date(2024, 1, 15))
```

---

## 开发规范

### 测试要求

- 新增因子：编写单元测试验证计算正确性
- 新增策略：编写单元测试覆盖 on_bar 逻辑
- 新增数据源：编写集成测试（使用 VCR 录制响应）

### 代码风格

```bash
# 格式化
black .
ruff check --fix .

# 类型检查
mypy app

# 运行测试
pytest tests/ -v
```

### 提交规范

- 每个扩展独立可提交，不破坏已有功能
- 通过 pre-commit hook 确保代码质量
- 新增公共接口需更新 OpenAPI 文档（FastAPI 自动生成）

## 相关文档

- [架构总览](./architecture.md) — 理解系统分层
- [API 使用示例](./api.md) — API 调用方式
- [内置策略说明](./strategies.md) — 已有策略参考

---

## 每日自动发现基金（已实现）

### 功能说明

系统每天 20:30 自动从天天基金排行榜抓取多维度排名数据，动态发现并注册新基金到采集列表。新基金会自动触发历史数据回填，并在 21:00 的正常采集窗口中被包含。

### 架构组件

| 组件 | 路径 | 作用 |
|------|------|------|
| ORM 模型 | `app/data/models/fund_ranking.py` | `fund_rankings` 表，存储每日排名快照 |
| Celery 任务 | `app/tasks/discovery.py` | `discover_funds` + `cleanup_stale_rankings` |
| API 端点 | `app/api/v1/discovery.py` | 排名查询、统计、手动触发 |
| Beat 调度 | `app/tasks/schedule.py` | 20:30 发现 + 周一 03:00 清理 |
| 数据库迁移 | `migrations/versions/2026_05_14_…` | 建表脚本 |

### 执行流程

```
20:30  discover_funds 执行
  ├── 从天天基金排行榜获取 9 个维度的 Top 30
  │   （3 排序维度 × 3 基金类型）
  ├── 存储排名快照到 fund_rankings 表（upsert）
  ├── 识别新基金（不在 funds 表中的代码）
  ├── 观察期过滤（连续 3 天上榜才纳入）
  ├── 检查总量上限（最多 200 只）
  ├── 创建 Fund 记录（status=active, source=discovery）
  └── 触发回填任务（meta → nav → dividends，错开执行）

21:00  正常数据采集开始（新基金已在 funds 表中，自动被包含）
```

### 数据来源

天天基金排行榜接口（已在 `EastmoneyProvider.fetch_fund_ranking()` 中实现）：

```
GET http://fund.eastmoney.com/data/rankhandler.aspx?op=ph&dt=kf&ft=all&sc=6yzf&st=desc&pi=1&pn=30
```

### 排名维度配置

当前默认配置（可在 `app/tasks/discovery.py` 中调整）：

**排序维度：**

| 排序方式 | 参数值 | 适用场景 |
|---------|--------|---------|
| 近1月涨幅 | `1yzf` | 短期热门 |
| 近3月涨幅 | `3yzf` | 中短期趋势 |
| 近6月涨幅 | `6yzf` | 中期表现（默认启用） |
| 近1年涨幅 | `1nzf` | 长期表现（默认启用） |
| 近3年涨幅 | `3nzf` | 长期稳定（默认启用） |

**基金类型：**

| 类型 | 参数值 | 说明 |
|------|--------|------|
| 股票型 | `stock` | 默认启用 |
| 混合型 | `mixed` | 默认启用 |
| 指数型 | `index` | 默认启用 |
| 债券型 | `bond` | 可选 |
| QDII | `qdii` | 可选 |

### 关键参数

```python
# app/tasks/discovery.py 中的配置常量

TOP_N = 30                  # 每个维度取 Top N
OBSERVATION_DAYS = 3        # 观察期：连续上榜 N 天才纳入
COOLDOWN_DAYS = 7           # 冷却期：掉出榜单后继续采集 N 天
MAX_WATCHLIST_SIZE = 200    # 最大采集基金数量上限
```

### API 接口

```bash
# 查询最新排名快照
curl "http://localhost:8000/api/v1/discovery/rankings?sort_metric=6yzf&fund_type=stock"

# 查看发现统计
curl http://localhost:8000/api/v1/discovery/stats

# 手动触发一次发现任务
curl -X POST http://localhost:8000/api/v1/discovery/trigger
```

### 自定义排名维度

修改 `app/tasks/discovery.py` 中的常量即可：

```python
# 添加近1月涨幅维度
RANKING_DIMENSIONS: list[tuple[str, str]] = [
    ("1yzf", "近1月涨幅"),   # 新增
    ("6yzf", "近6月涨幅"),
    ("1nzf", "近1年涨幅"),
    ("3nzf", "近3年涨幅"),
]

# 添加债券型基金
FUND_TYPE_FILTERS: list[tuple[str, str]] = [
    ("stock", "股票型"),
    ("mixed", "混合型"),
    ("index", "指数型"),
    ("bond", "债券型"),      # 新增
]
```

### 注意事项

- 排行榜数据有幸存者偏差，仅用于数据采集范围选择，不作为投资建议
- 建议保留一个"核心池"（宽基指数等）始终采集，排行榜推荐作为补充
- 每次推荐的基金会自动采集元数据+净值，已有数据的基金只更新增量
- 观察期机制避免了因偶然波动频繁增删基金
- 排名快照保留 30 天后自动清理（每周一 03:00）

---

## 智能交易建议（已实现）

### 功能说明

综合多维度分析，为用户生成专业的买卖建议，包含操作方向、建议金额、置信度和详细理由。

### 分析维度

| 维度 | 权重 | 方法 | 说明 |
|------|------|------|------|
| 技术分析 | 30% | MA/MACD/RSI/布林带 | 短中期趋势和动量判断 |
| 估值分析 | 25% | 历史净值百分位 | 当前价格在历史中的位置 |
| 策略信号 | 25% | 已配置策略的 on_bar 输出 | 量化策略的买卖信号 |
| 预测模型 | 20% | GBM 概率预测 | 未来正收益概率估计 |

### 仓位管理

使用 **Kelly Criterion（半 Kelly）** 计算最优仓位：

```
Kelly 公式: f* = (p × b - q) / b
  p = 胜率（正收益交易日占比）
  q = 1 - p
  b = 盈亏比（平均盈利 / 平均亏损）

实际使用: f*/2（半 Kelly，降低波动风险）
```

### 风险等级

| 等级 | 买入阈值 | 最大单仓 | 单日交易上限 | 适用人群 |
|------|---------|---------|------------|---------|
| 保守型 | 0.4 | 20% | 10% | 风险厌恶型 |
| 稳健型 | 0.3 | 30% | 20% | 大多数投资者 |
| 进取型 | 0.2 | 50% | 30% | 风险偏好型 |

### 架构组件

| 组件 | 路径 | 作用 |
|------|------|------|
| 核心引擎 | `app/services/trading_advisor.py` | 多维度分析 + Kelly 仓位 + 建议生成 |
| API 端点 | `app/api/v1/advisor.py` | 分析/组合建议/信号查询/配置 |
| 定时任务 | `app/tasks/advisor.py` | 每日 22:30 自动生成并推送 |
| 前端 API | `frontend/src/api/advisor.ts` | TanStack Query hooks |

### API 接口

```bash
# 为指定基金生成交易建议
curl -X POST http://localhost:8000/api/v1/advisor/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "fund_codes": ["000001", "110011"],
    "total_capital": 100000,
    "current_positions": {"000001": 15000},
    "risk_level": "moderate"
  }'

# 基于策略生成组合调仓建议
curl -X POST http://localhost:8000/api/v1/advisor/portfolio \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": 1,
    "total_capital": 200000,
    "risk_level": "aggressive"
  }'

# 查询历史信号
curl "http://localhost:8000/api/v1/advisor/signals?fund_code=000001&page=1"

# 获取引擎配置
curl http://localhost:8000/api/v1/advisor/config
```

### 响应示例

```json
{
  "advice_date": "2026-05-19",
  "total_capital": 100000,
  "risk_level": "moderate",
  "fund_count": 2,
  "advices": [
    {
      "fund_code": "000001",
      "fund_name": "华夏成长",
      "action": "buy",
      "confidence": 0.72,
      "urgency": "high",
      "suggested_amount": 8500.00,
      "suggested_pct": 0.085,
      "scores": {
        "technical": 0.45,
        "valuation": 0.68,
        "strategy": 0.50,
        "prediction": 0.32,
        "composite": 0.49
      },
      "reasons": [
        "MACD 金叉，短期动能转强",
        "当前净值处于历史 18% 分位，属于低估区域",
        "模型预测未来30日正收益概率 65%",
        "Kelly 模型建议仓位 12.5%（胜率 56%，盈亏比 1.35）"
      ],
      "risk_warnings": [
        "30日 95% VaR: -5.2%（极端情况下可能亏损）",
        "以上建议仅供参考，不构成投资建议"
      ],
      "kelly": {
        "win_rate": 0.56,
        "half_kelly": 0.125,
        "suggested_amount": 8500.00
      }
    }
  ],
  "summary": {
    "buy_count": 1,
    "sell_count": 0,
    "hold_count": 1,
    "total_buy_amount": 8500.00,
    "high_confidence_signals": 1
  }
}
```

### 执行流程

```
22:00  策略信号生成（signals 任务）
22:30  交易建议生成（advisor 任务）
  ├── 加载所有策略基金池（去重，最多50只）
  ├── 加载净值数据（最近750个交易日）
  ├── 加载最新策略信号（7天内）
  ├── 对每只基金执行多维度分析
  │   ├── 技术指标计算（MA/MACD/RSI/布林带）
  │   ├── 估值百分位分析
  │   ├── 策略信号整合
  │   ├── GBM 概率预测
  │   └── Kelly 仓位计算
  ├── 综合评分 → 确定操作方向和金额
  ├── 筛选高置信度建议（confidence > 0.5）
  └── 推送通知（邮件/企业微信/Telegram）
```
