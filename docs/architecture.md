# 架构总览

本文档描述基金量化平台的系统架构、核心模块与数据流。

## 设计理念

1. **研究与实盘同构** — 策略代码在回测与实盘下无需修改
2. **确定性核心 + AI 外围** — 核心决策链路纯确定性计算，LLM 仅作辅助增强
3. **数据外部不稳 → 本地稳** — 多源采集 + 本地持久化 + 快照归档
4. **正确性优先于性能** — 优先正确处理 T+1 结算、费率、分红等基金特性

## 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI | 异步 API + 自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0 (async) | 数据库访问 |
| 数据库 | PostgreSQL 16 + TimescaleDB | 关系数据 + 时序数据 |
| 缓存 | Redis 7.x | 热数据缓存 + Celery Broker |
| 任务队列 | Celery 5.x + Beat | 异步任务 + 定时调度 |
| 数据科学 | Pandas / NumPy / SciPy | 因子计算与归因 |
| 前端 | React 18 + Vite + TypeScript | SPA 界面 |
| 图表 | ECharts | 金融数据可视化 |
| 容器化 | Docker Compose | 一键部署 |
| 监控 | Prometheus + Grafana | 指标采集与可视化 |

## 系统分层

```
┌─────────────────────────────────────────────────────────────┐
│  表现层 — React SPA + ECharts + WebSocket 客户端             │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS / WebSocket
┌────────────────────────────▼────────────────────────────────┐
│  API 网关层 — FastAPI + Pydantic 校验 + CORS + 限流          │
└───┬────────────────────┬───────────────────┬────────────────┘
    │                    │                   │
┌───▼──────────┐  ┌──────▼──────────┐  ┌────▼─────────────┐
│ 应用服务层    │  │ LLM 辅助服务     │  │ 任务编排层        │
│ FundService  │  │ LLMService      │  │ Celery Worker    │
│ FactorSvc    │  │ Cache + Budget  │  │ Celery Beat      │
│ BacktestSvc  │  │ Audit           │  │                  │
│ StrategySvc  │  └──────┬──────────┘  └────┬─────────────┘
│ PerfSvc      │         │                  │
└───┬──────────┘         │                  │
    │                    │                  │
┌───▼────────────────────▼──────────────────▼─────────────────┐
│  领域核心 — Factor库 · 回测引擎 · 策略库 · 风控引擎           │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  数据访问层 — Repository 模式 · 异步接口 · 事务管理           │
└───┬────────────────────┬───────────────────┬────────────────┘
    │                    │                   │
┌───▼──────────┐  ┌──────▼──────────┐  ┌────▼─────────────┐
│ PostgreSQL   │  │ Redis 缓存       │  │ 冷存储 Snapshot   │
│ + TimescaleDB│  │                  │  │ Parquet + 原始    │
└──────────────┘  └─────────────────┘  └──────────────────┘
    ▲
    │
┌───┴─────────────────────────────────────────────────────────┐
│  数据采集层 — FundDataProvider 抽象                           │
│  ├─ EastmoneyProvider(主) ├─ AkshareProvider(备)            │
│  ├─ 限流器  ├─ 重试器  ├─ 熔断器  ├─ 代理/UA 轮换            │
└─────────────────────────────────────────────────────────────┘
```

## 核心模块

### 数据采集层

采用 **Provider 抽象 + CompositeProvider 编排** 模式：

- `FundDataProvider` — 统一接口协议，所有数据源实现此接口
- `EastmoneyProvider` — 天天基金（主源，priority=1）
- `AkshareProvider` — AkShare（备源，priority=2）
- `CompositeProvider` — 按优先级链式调用，主源失败自动降级

网络基础设施：
- **令牌桶限流器** — 每个 Provider 独立限流（默认 2 req/s）
- **指数退避重试** — 最多 3 次，间隔 2-30 秒
- **熔断器** — 连续 5 次失败触发 OPEN，60 秒后 HALF_OPEN 探测
- **代理/UA 轮换** — 可选，从配置加载

### 因子计算库

基于 `@factor` 装饰器注册机制，支持 6 大类因子：

| 类别 | 因子示例 |
|------|---------|
| 收益类 | 区间收益、年化收益、超额收益、Jensen Alpha |
| 风险类 | 波动率、最大回撤、VaR、CVaR、Calmar |
| 风险调整 | Sharpe、Sortino、Information Ratio、Treynor |
| 基准相关 | Beta、跟踪误差、R²、上行/下行捕获比 |
| 持仓类 | 集中度 HHI、前十大占比、行业分布 |
| 归因 | Fama-French 三/五因子、Brinson 归因 |

所有因子函数遵循向量化契约：输入 `pd.Series`/`pd.DataFrame`，输出标量或 Series，空数据返回 NaN 不抛异常。

### 回测引擎

平台提供两种回测引擎：

**事件驱动引擎**（精确模式）：
- 按交易日迭代，处理 MarketOpen → Dividend → Confirm → Strategy.on_bar → Risk → Queue → MarketClose
- 正确模拟 T+1 确认、阶梯费率、分红再投、限购检查
- 严格防止未来函数（策略只能看到 T-1 及之前数据）

**向量化引擎**（快速模式）：
- 纯 Pandas 向量化，不处理精确 T+1 和阶梯费率
- 速度快 100 倍，适合研究阶段快速迭代
- 与事件驱动引擎最终净值偏差 < 0.5%

### 策略库

所有策略继承 `BaseStrategy`，实现 `on_bar` 方法：

| 策略类型 | 说明 |
|---------|------|
| DCA（定投） | 定额定投、价值平均、智能定投 |
| Momentum（轮动） | 基于动量/Sharpe/IR 的 Top-N 轮动 |
| Risk Parity（风险平价） | 等风险贡献权重优化 |
| Mean-Variance | 均值-方差优化 + Black-Litterman |
| Timing（择时） | 双均线、MACD、估值分位数 |
| FOF | 多因子打分筛选 + 组合优化 |

### 风控引擎

规则链模式，支持：
- 单基金/单类型最大仓位限制
- 最大回撤熔断（按比例缩仓）
- 波动率目标自适应杠杆
- 最小现金保留

### AI 辅助层

定位为**外围增强模块**，不参与核心决策链路：

- 公告分类解析（限购、分红、经理变更等）
- 自然语言查询（翻译为内部 DSL/SQL）
- 策略生成（基于模板生成参数 JSON）
- 归因报告（基于已计算数值生成自然语言分析）
- 因子 brainstorm（生成候选因子公式并自动验证）

红线：AI 输出不驱动交易信号，必须经 Schema 校验，关闭 AI 后核心功能正常运行。

## 数据流

### 每日数据更新流程

```
21:00 Celery Beat 触发
    → Celery Worker 执行 update_daily_nav
    → CompositeProvider 采集净值
    → 数据校验器验证
    → 入库 fund_nav 表
    → 失效 Redis 缓存
    → 触发复权净值重算（如有分红）

22:00 Celery Beat 触发
    → 信号生成任务
    → 对订阅策略运行 on_bar
    → 生成信号存入 signals 表
    → 通知模块推送（邮件/企微/Telegram）
```

### 回测执行流程

```
用户提交回测请求
    → API 创建 backtest_run 记录（status=pending）
    → Celery 异步执行回测任务
    → 进度写入 Redis，WebSocket 推送给前端
    → 回测完成，结果入库（equity、trades、metrics）
    → 前端展示净值曲线、回撤、交易流水、归因分析
```

## 部署架构

Docker Compose 包含以下服务：

| 服务 | 镜像 | 职责 |
|------|------|------|
| postgres | timescale/timescaledb:2.16.1-pg16 | 主数据库 |
| redis | redis:7.2-alpine | 缓存 + 消息队列 |
| api | 自建 | FastAPI 应用（Gunicorn + Uvicorn Worker） |
| worker | 自建 | Celery Worker（4 队列：ingest/backtest/ai/notify） |
| beat | 自建 | Celery Beat 定时调度 |
| frontend | 自建 | React SPA（Nginx 反代） |
| prometheus | prom/prometheus | 指标采集 |
| alertmanager | prom/alertmanager | 告警路由 |
| grafana | grafana/grafana | 监控面板 |

## 目录结构

```
fund-quant-platform/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── core/                # 配置、日志
│   │   ├── api/v1/              # REST API 路由
│   │   ├── domain/              # 领域核心
│   │   │   ├── factors/         # 因子库
│   │   │   ├── backtest/        # 回测引擎
│   │   │   ├── strategy/        # 策略库
│   │   │   ├── risk/            # 风控
│   │   │   └── performance/     # 绩效分析
│   │   ├── services/            # 应用服务
│   │   ├── ai/                  # LLM 辅助层
│   │   ├── data/                # 数据层
│   │   │   ├── providers/       # 数据源适配器
│   │   │   ├── fetchers/        # 网络基础设施
│   │   │   ├── validators/      # 数据校验
│   │   │   ├── models/          # ORM 模型
│   │   │   └── repositories/    # Repository
│   │   ├── tasks/               # Celery 任务
│   │   ├── notify/              # 告警推送
│   │   └── observability/       # 监控
│   ├── migrations/              # Alembic 迁移
│   └── tests/                   # 测试
├── frontend/                    # React 前端
├── deploy/                      # Docker Compose + 监控配置
└── docs/                        # 项目文档
```

## 相关文档

- [快速启动指南](./getting-started.md)
- [API 使用示例](./api.md)
- [内置策略说明](./strategies.md)
- [扩展开发指南](./extending.md)
