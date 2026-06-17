# 基金量化平台 - 实施任务

> 任务按阶段组织，每个任务都是一次可提交的、可测试的增量开发。序号用 `阶段.子任务` 表示。
> 每个任务标注对应的需求编号，便于验收追溯。

---

## 阶段 0：项目基础设施

- [x] 0.1 初始化项目骨架与工程化配置
  - 创建 `backend/`、`frontend/`、`deploy/`、`docs/` 目录结构
  - 配置 `pyproject.toml`（Python 3.11+）并声明依赖分组（core / dev / test）
  - 配置 Ruff + Black + mypy，加入 pre-commit hook
  - 创建 `.env.example`、`.gitignore`、`README.md`
  - _需求: 9.3, 9.7_

- [x] 0.2 搭建 Docker Compose 开发环境
  - 编写 `deploy/docker-compose.yml`：postgres(timescaledb)、redis、api、worker、beat、frontend
  - 编写 `backend/Dockerfile` 与 `frontend/Dockerfile`（多阶段构建）
  - 编写 `docker-compose.dev.yml` 支持代码挂载与热重载
  - 验证 `docker compose up` 能拉起全部服务且健康检查通过
  - _需求: 9.1, 9.5, 9.6_

- [x] 0.3 配置 FastAPI 应用骨架
  - 在 `backend/app/main.py` 创建 FastAPI 应用，挂载 `/api/v1` 路由
  - 实现 `core/config.py`（Pydantic Settings，从 .env 加载）
  - 实现 `core/logging.py`（结构化 JSON 日志）
  - 实现 `/health` 健康检查端点
  - 配置 CORS、异常处理中间件、请求 ID 注入
  - _需求: 7.6, 9.3_

- [x] 0.4 配置数据库与迁移
  - 集成 SQLAlchemy 2.0 异步 + asyncpg
  - 配置 Alembic，生成初始迁移骨架
  - 编写 `data/session.py` 异步会话工厂
  - 启动时自动运行迁移（生产模式可关闭）
  - _需求: 9.2, 9.4_

- [x] 0.5 配置 Celery + Redis
  - 在 `tasks/celery_app.py` 初始化 Celery，使用 Redis broker + backend
  - 定义 4 条队列：ingest / backtest / ai / notify
  - 配置 Celery Beat 定时调度（空 schedule，后续填充）
  - 编写一个 ping 任务验证端到端链路
  - _需求: 8.1, 8.8_

- [x] 0.6 搭建观测性基础设施
  - 集成 `prometheus-fastapi-instrumentator`，暴露 `/metrics`
  - 实现 `observability/metrics.py` 定义核心指标（Counter、Histogram）
  - 在 docker-compose 中加入 Prometheus + Grafana 服务
  - 创建基础 Grafana dashboard JSON
  - _需求: 8.5, 8.6_

---

## 阶段 1：数据采集与存储

- [x] 1.1 定义数据领域模型（Pydantic DTO）
  - 在 `data/schemas/` 创建 `FundMeta`、`NavRecord`、`HoldingSnapshot`、`DividendRecord`、`Announcement`、`FeeTier`
  - 字段与单位统一（Decimal 用于金额，date 用于日期）
  - 编写单元测试覆盖序列化/反序列化
  - _需求: 2.6_

- [x] 1.2 创建数据库 ORM 模型与初始迁移
  - 在 `data/models/` 实现 funds / fund_nav / fund_holdings / fund_dividends / fund_announcements / fund_fees
  - 对时序表用 TimescaleDB 创建 hypertable（迁移中执行 SQL）
  - 创建必要索引（fund_type、company_id、nav 降序）
  - 生成并执行 Alembic 迁移
  - _需求: 2.7, 2.8_

- [x] 1.3 实现 Repository 层
  - 在 `data/repositories/` 实现 FundRepo、NavRepo、HoldingRepo、DividendRepo、FeeRepo
  - 统一异步接口：`upsert_many`、`get_by_date_range`、`latest_date`、`missing_dates`
  - 编写单元测试（使用测试数据库或 sqlite 内存模式）
  - _需求: 2.6, 1.11_

- [x] 1.4 实现网络基础设施
  - `fetchers/http_client.py`：基于 httpx 的异步客户端，支持 UA 池、默认 headers
  - `fetchers/rate_limiter.py`：令牌桶限流器，按 provider name 隔离
  - `fetchers/retry.py`：tenacity 装饰器，指数退避 + 最多 3 次
  - `fetchers/circuit_breaker.py`：失败计数 + OPEN/HALF_OPEN/CLOSED 状态机
  - `fetchers/proxy_pool.py`：可选代理池接口（先实现内存实现）
  - 为以上每个组件编写单元测试
  - _需求: 1.6, 1.7, 1.8, 1.10_

- [x] 1.5 定义 Provider 抽象与快照归档
  - 在 `data/providers/base.py` 定义 `FundDataProvider` Protocol 与异常类型
  - 实现 `data/providers/snapshot.py`：原始响应压缩存储（本地 Parquet + gzip 原始 JSON/HTML）
  - 快照以 `{provider}/{date}/{code}/{endpoint}.{ext}.gz` 组织
  - _需求: 1.9_

- [x] 1.6 实现天天基金 Provider（主源）
  - `providers/eastmoney.py` 实现 `FundDataProvider` 全部方法
  - 覆盖 9 个接口：基本信息、历史净值、实时估值、pingzhongdata、持仓、分红拆分、排名、经理、公告
  - 必带 Referer 与 UA，接入限流器与重试器
  - 为每个接口编写集成测试（使用 VCR 录制真实响应）
  - _需求: 1.1, 1.2, 1.3, 1.6_

- [x] 1.7 实现 AkShare Provider（备源）
  - `providers/akshare.py` 实现 `FundDataProvider`
  - 封装 akshare 相关函数，统一字段命名与异常
  - 单元测试覆盖主要方法
  - _需求: 1.4_

- [x] 1.8 实现 CompositeProvider 多源编排
  - `providers/composite.py`：按 priority 链式调用，主源失败降级
  - 集成熔断器，OPEN 状态跳过 provider
  - 返回结果同时记录命中的 source
  - 编写测试：主源失败自动降级、全部失败抛 AllProvidersFailedError
  - _需求: 1.4, 1.5_

- [x] 1.9 实现数据校验器
  - `data/validators/`：净值校验（日涨跌幅阈值按基金类型分档）、持仓占比校验、日期单调性校验
  - 校验失败的数据标记 `status=suspect` 不直接覆盖
  - 跨源差异校验：同日多源数据比对，差异超阈值记录告警
  - 单元测试覆盖各类异常场景
  - _需求: 2.1, 2.2, 2.3, 2.5_

- [x] 1.10 实现复权净值计算
  - `data/services/adj_nav.py`：基于分红拆分事件全量重算 `adj_nav`
  - 每次分红/拆分 upsert 后自动触发重算该基金历史
  - 单元测试对拍几只真实基金的复权序列
  - _需求: 2.6_

- [x] 1.11 实现数据采集 Celery 任务
  - `tasks/ingest.py`：`update_fund_meta`、`update_daily_nav`、`update_holdings`、`update_dividends`、`update_announcements`
  - 每个任务：读取 last_updated → 调用 CompositeProvider → 校验 → 入库 → 记录 metrics
  - 支持单基金与批量两种触发模式
  - 配置 Beat：每日 21:00 净值、季度更新持仓
  - _需求: 1.1, 1.2, 1.11, 8.1_

- [x] 1.12 实现 Redis 缓存层
  - `data/cache.py`：基金元数据、近期净值的缓存读写
  - 缓存键规范：`fund:meta:{code}`、`fund:nav:{code}:{start}:{end}`
  - 数据入库后自动失效相关缓存
  - _需求: 2.9_

- [x] 1.13 实现数据库备份任务
  - `tasks/backup.py`：`pg_dump` 到本地冷存储目录，文件命名含日期
  - Beat 配置每周日凌晨 2:00 执行
  - 保留最近 8 周备份，更早的自动清理
  - _需求: 2.10_

- [x] 1.14 实现基金检索与净值查询 API
  - `api/v1/funds.py`：`GET /funds`（分页+过滤）、`GET /funds/{code}`、`GET /funds/{code}/nav`
  - 使用 Pydantic v2 响应模型，OpenAPI 文档齐全
  - 集成 Redis 缓存
  - 编写 API 集成测试
  - _需求: 7.1, 7.2, 2.9_

---

## 阶段 2：因子计算库

- [x] 2.1 实现因子注册机制
  - `domain/factors/registry.py`：`@factor(name, category, window)` 装饰器
  - `FactorDef` 数据类：记录元数据（类别、窗口要求、返回类型）
  - `list_factors()` / `get_factor(name)` 查询接口
  - 单元测试
  - _需求: 10.4, 3.11_

- [x] 2.2 实现收益类因子
  - `factors/returns.py`：total_return、annualized_return、excess_return、jensen_alpha
  - 约定输入 `pd.Series` nav，输出 float 或 Series
  - 空数据返回 NaN 不抛异常
  - 单元测试 + 对拍（与天天基金展示值误差 < 0.01%）
  - _需求: 3.1, 3.10, 3.12_

- [x] 2.3 实现风险类因子
  - `factors/risk.py`：volatility、downside_deviation、max_drawdown、calmar、var、cvar
  - 支持滚动窗口模式
  - 单元测试覆盖极值与边界
  - _需求: 3.2_

- [x] 2.4 实现风险调整收益因子
  - `factors/risk_adjusted.py`：sharpe、sortino、information_ratio、treynor
  - 支持自定义无风险利率
  - 单元测试
  - _需求: 3.3_

- [x] 2.5 实现基准相关因子
  - `factors/benchmark.py`：beta、tracking_error、r_squared、up_capture、down_capture
  - 支持滚动窗口
  - 单元测试
  - _需求: 3.4_

- [x] 2.6 实现持仓类因子
  - `factors/holding.py`：concentration_hhi、top10_weight、industry_exposure、turnover
  - 基于 fund_holdings 表数据
  - 单元测试
  - _需求: 3.5_

- [x] 2.7 实现规模与经理因子
  - `factors/manager.py`：fund_size、size_change_rate、manager_tenure、manager_fund_count
  - 单元测试
  - _需求: 3.6_

- [x] 2.8 实现 Fama-French 归因
  - `domain/performance/fama_french.py`：三因子与五因子回归
  - 输入：基金收益序列 + 因子收益矩阵 → 输出：β 暴露 + α + R²
  - 内置中国市场因子构建（基于中证全指等公开指标）
  - 单元测试对拍已知结果
  - _需求: 3.7_

- [x] 2.9 实现 Brinson 归因
  - `domain/performance/brinson.py`：配置效应、选股效应、交互效应
  - 输入：组合权重、基准权重、板块收益 → 输出归因拆解
  - 单元测试
  - _需求: 3.8_

- [x] 2.10 实现 FactorEngine 服务
  - `services/factor_service.py`：批量计算因子，返回宽表 DataFrame
  - 支持滚动窗口配置、频率（日/周/月）
  - 向量化实现，100 只基金 10 年数据目标 < 1 秒
  - 性能基准测试
  - _需求: 3.9, 3.11_

- [x] 2.11 实现因子计算 API
  - `api/v1/factors.py`：`GET /factors`、`POST /factors/compute`、`GET /funds/{code}/factors`
  - OpenAPI 文档完备
  - 集成测试
  - _需求: 7.2, 7.6_

---

## 阶段 3：回测引擎

- [x] 3.1 实现交易日历
  - `domain/backtest/calendar.py`：中国 A 股交易日历（含节假日）
  - 提供 `trading_days(start, end)`、`next_trading_day`、`is_trading_day`
  - 数据源：akshare 或本地 CSV 维护
  - 单元测试覆盖 10 年日历
  - _需求: 4.9_

- [x] 3.2 实现事件类型与事件总线
  - `backtest/events.py`：定义所有事件 Pydantic 模型
  - 实现最小化事件分发（同步，按时间排序）
  - 单元测试
  - _需求: 4.9_

- [x] 3.3 实现订单与持仓模型
  - `backtest/order.py`：`OrderIntent`、`Order`、`OrderStatus`、`Fill`
  - `backtest/portfolio.py`：`Portfolio` 类，支持现金、持仓、未确认订单状态
  - 单元测试覆盖申购、赎回、确认、持有天数跟踪
  - _需求: 4.1, 4.2_

- [x] 3.4 实现结算规则
  - `backtest/settlement.py`：`SettlementRule` 表（按基金类型）+ 查询函数
  - 内置规则：stock/bond/mixed/money/qdii/index/fof
  - 单元测试
  - _需求: 4.1, 4.2, 4.3_

- [x] 3.5 实现费率计算
  - `backtest/fees.py`：阶梯申购费、持有期阶梯赎回费
  - 查询 fund_fees 表，返回费用与净成交金额
  - 单元测试覆盖多档阶梯、持有期边界
  - _需求: 4.4, 4.5_

- [x] 3.6 实现分红与拆分处理
  - `backtest/corporate_actions.py`：除权日调整份额/现金（按红利再投配置）
  - 单元测试覆盖现金分红、红利再投、拆分
  - _需求: 4.6, 4.7_

- [x] 3.7 实现事件驱动回测引擎核心
  - `backtest/engine_event.py`：`EventDrivenEngine`
  - 主循环按交易日迭代：MarketOpen → Dividend → Confirm pending → Strategy.on_bar → Risk → Queue → MarketClose → 记录权益
  - `BarContext` 强制只提供 T-1 及之前净值（防未来函数）
  - 详细日志 + progress 回调
  - _需求: 4.9, 4.10, 4.11_

- [x] 3.8 实现限购与状态检查
  - 在引擎订单处理流程中调用 fund 状态与限购额度检查
  - 违规订单拒绝并记录原因
  - 单元测试
  - _需求: 4.8_

- [x] 3.9 实现向量化回测引擎
  - `backtest/engine_vector.py`：纯 pandas 向量化实现
  - 支持信号矩阵 → 权重 → 扣除简化成本 → 权益曲线
  - 单元测试 + 性能测试（100 基金 10 年 < 5 秒）
  - _需求: 4.12_

- [x] 3.10 实现双引擎一致性校验测试
  - `tests/test_engine_consistency.py`：用相同策略与数据运行两个引擎
  - 断言最终净值差异 < 0.5%
  - 覆盖 3 种典型策略（买入持有、动量、定投）
  - _需求: 4.13_

- [x] 3.11 实现未来函数专项测试
  - `tests/test_no_lookahead.py`：策略试图访问 T 日及未来数据时应抛 LookaheadError
  - 覆盖 nav、factor、holding 三类数据
  - _需求: 4.10_

- [x] 3.12 实现回测结果模型与持久化
  - `backtest/result.py`：`BacktestResult` 包含 equity、trades、holdings_history、metrics
  - 实现结果入库（backtest_runs、backtest_equity、backtest_trades）
  - 单元测试
  - _需求: 4.11_

---

## 阶段 4：策略库与风控

- [x] 4.1 实现策略基类与上下文
  - `domain/strategy/base.py`：`BaseStrategy` 抽象类 + `StrategyParams` 基类
  - `StrategyContext`、`BarContext` 接口：提供 portfolio、factor、nav_history（受未来函数限制）
  - `rebalance_to(weights)` 辅助方法：自动生成申赎意图
  - _需求: 5.9, 10.5, 10.6_

- [x] 4.2 实现定投策略
  - `strategy/dca.py`：定额定投、价值平均、智能定投（均线偏离加倍）
  - 参数：金额、频率、均线参数
  - 单元测试
  - _需求: 5.1_

- [x] 4.3 实现基金轮动策略
  - `strategy/momentum.py`：基于 momentum/sharpe/IR 因子的 Top-N 轮动
  - 参数：lookback、top_n、rebalance_freq、score_factor
  - 单元测试
  - _需求: 5.2_

- [x] 4.4 实现风险平价策略
  - `strategy/risk_parity.py`：使用 scipy 或 cvxpy 求解等风险贡献权重
  - 支持协方差估计方法配置（样本、指数加权、收缩估计）
  - 单元测试对拍已知案例
  - _需求: 5.3_

- [x] 4.5 实现均值-方差与 Black-Litterman
  - `strategy/mean_variance.py`：MV 优化 + BL 模型
  - BL 支持用户观点矩阵
  - 单元测试
  - _需求: 5.3_

- [x] 4.6 实现择时策略
  - `strategy/timing.py`：双均线、MACD、估值分位数（可接入指数估值数据）
  - 单元测试
  - _需求: 5.4_

- [x] 4.7 实现 FOF 策略
  - `strategy/fof.py`：多因子打分筛选 + 组合优化
  - 参数：因子权重、筛选规则、优化方法
  - 单元测试
  - _需求: 5.5_

- [x] 4.8 实现风控引擎
  - `domain/risk/limits.py`：`MaxPositionRule`、`MaxTypeExposureRule`、`MinCashReserveRule`
  - `risk/drawdown_control.py`：最大回撤熔断（按比例缩仓）
  - `risk/vol_target.py`：波动率目标自适应杠杆
  - `RiskEngine` 规则链组合
  - 单元测试覆盖各规则与组合场景
  - _需求: 6.1, 6.2, 6.3_

- [x] 4.9 实现参数优化
  - `services/optimization.py`：网格搜索、随机搜索（Sobol）
  - 支持并行回测（Celery 任务池）
  - 单元测试
  - _需求: 5.7_

- [x] 4.10 实现 Walk-forward 分析
  - `services/walk_forward.py`：滚动训练/测试窗口，聚合样本外指标
  - 单元测试
  - _需求: 5.8_

- [x] 4.11 实现 Monte Carlo 滚动回测
  - 基于收益序列 bootstrap 或区块 bootstrap
  - 输出策略稳健性分布图
  - 单元测试
  - _需求: 5.8_

- [x] 4.12 实现自定义策略注册
  - 支持通过入口点或目录扫描加载用户策略
  - 单元测试
  - _需求: 5.9, 10.5_

---

## 阶段 5：API、前端与绩效报告

- [x] 5.1 实现绩效分析服务
  - `services/performance_service.py`：汇总所有绩效指标
  - 输出结构化 JSON，供 API 和 AI 归因使用
  - 单元测试
  - _需求: 6.4, 6.5, 6.6_

- [x] 5.2 实现策略 CRUD API
  - `api/v1/strategies.py`：创建、列表、详情、更新、删除
  - 策略参数使用 Pydantic JSON Schema 校验
  - 集成测试
  - _需求: 7.5_

- [x] 5.3 实现回测 API 与 WebSocket 进度
  - `api/v1/backtests.py`：POST 发起（返回 run_id）、GET 状态、GET equity/trades/attribution
  - `api/v1/ws/backtest_progress.py`：WebSocket 订阅进度
  - 回测任务通过 Celery 异步执行，进度写入 Redis，由 WS 推送
  - 集成测试
  - _需求: 7.3, 7.4_

- [x] 5.4 初始化 React 前端项目
  - Vite + TypeScript + React 18 初始化
  - 集成 TanStack Query、Zustand、React Router
  - 配置 API client（axios + 拦截器 + 错误处理）
  - 基础布局与主题
  - _需求: 7.1_

- [x] 5.5 实现基金检索与详情页
  - 搜索页：代码、名称、类型、规模、经理过滤
  - 详情页：基础信息、净值曲线（ECharts）、业绩指标表、持仓分布、费率表
  - 加载态、空态、错误态处理
  - _需求: 7.1, 7.2, 7.7_

- [x] 5.6 实现策略配置与回测页面
  - 策略模板选择 + 参数表单（基于 JSON Schema 生成）
  - 基金池选择器
  - 提交后跳转到回测进度页
  - _需求: 7.3, 7.5_

- [x] 5.7 实现回测结果页
  - 净值曲线 + 回撤曲线叠加
  - 交易流水表（分页）
  - 绩效指标卡片 + 归因分析图
  - WebSocket 实时进度
  - _需求: 7.4, 6.6_

- [x] 5.8 实现策略对比页
  - 多策略曲线同屏叠加
  - 关键指标并排对比表
  - _需求: 7.8_

- [x] 5.9 实现绩效报告生成器
  - `services/report_service.py`：HTML/PDF 报告导出
  - 包含月度收益热力图、滚动 Sharpe、滚动 Beta、回撤持续时间分布
  - _需求: 6.6_

---

## 阶段 6：调度与监控

- [x] 6.1 完善 Celery Beat 调度
  - 每日 21:00 数据更新、22:00 策略信号生成、凌晨 2:00 备份、季度 15-25 日 4:00 持仓更新
  - 单元测试校验 crontab 表达式
  - _需求: 8.1, 8.2_

- [x] 6.2 实现信号生成任务
  - `tasks/signals.py`：对订阅策略每日生成信号，存入 signals 表
  - 调用通知模块推送
  - _需求: 8.2_

- [x] 6.3 实现告警通知模块
  - `notify/email.py`、`notify/wecom.py`、`notify/telegram.py` 三个通道
  - 统一 `NotificationService` 按用户配置路由
  - 单元测试（mock 外部调用）
  - _需求: 8.3, 8.4_

- [x] 6.4 实现完整 Prometheus 指标
  - 数据采集：ingest_requests_total、ingest_latency、data_completeness
  - 回测：backtest_runs_total、backtest_duration
  - LLM：llm_calls_total、llm_cost_usd_total、llm_tokens_total
  - 任务队列深度：task_queue_depth
  - _需求: 8.5_

- [x] 6.5 实现告警规则
  - Prometheus Alertmanager 配置：数据未更新、provider 熔断、任务失败率、LLM 成本超限
  - 连通通知模块
  - _需求: 8.4, 8.7_

- [x] 6.6 实现监控面板
  - Grafana dashboard：任务执行状态、数据完整度、数据源健康度、LLM 用量
  - 近 7 日视图
  - _需求: 8.6_

- [x] 6.7 实现 CLI 手动触发
  - `backend/cli.py`：`ingest --fund CODE`、`backtest --strategy ID`、`signal --strategy ID`
  - 基于 Typer
  - 前端提供"手动触发"按钮
  - _需求: 8.8_

---

## 阶段 7：AI 数据增强

- [x] 7.1 实现 LLMProvider 抽象与适配器
  - `ai/provider.py`：Protocol 定义
  - `ai/providers/openai_compat.py`：兼容 OpenAI、DeepSeek、智谱、月之暗面
  - `ai/providers/anthropic.py`：Claude
  - 单元测试（mock HTTP）
  - _需求: 11.1, 11.2_

- [x] 7.2 实现 LLM 缓存
  - `ai/cache.py`：基于 Redis，键为 `sha256(use_case + prompt + schema)`
  - 默认 7 天 TTL，可配置
  - 单元测试
  - _需求: 11.4_

- [x] 7.3 实现 Token 预算控制
  - `ai/budget.py`：日/月预算上限，按用例分级
  - 达到阈值暂停非关键调用
  - 单元测试
  - _需求: 11.6, 11.7_

- [x] 7.4 实现审计日志
  - `ai/audit.py`：所有调用落入 llm_calls 表
  - 提供统计查询（近 30 日）
  - _需求: 11.5_

- [x] 7.5 实现 LLMService 统一管道
  - 预算检查 → 缓存 → provider 选择 → 调用 → Schema 校验 → 审计 → 缓存写入 → 降级
  - 单元测试覆盖全流程
  - _需求: 11.3, 11.8_

- [x] 7.6 实现公告分类解析用例
  - `ai/use_cases/announcement_parse.py`：Schema + Prompt + 规则引擎交叉校验
  - 接入 ingest 流程：公告入库后异步触发解析
  - 不确定的标记 `requires_review=true`
  - 单元测试（用历史公告样本）
  - _需求: 11.9, 11.10, 11.11, 11.12_

- [x] 7.7 实现季报持仓说明提取用例
  - `ai/use_cases/report_extract.py`：从 PDF/HTML 季报中提取经理观点、风格描述
  - 集成 PDF 解析库（pypdf / pdfplumber）
  - 结构化 JSON 输出 + Schema 校验
  - _需求: 11.9_

---

## 阶段 8：AI 研究增强与收尾

- [x] 8.1 实现自然语言查询用例
  - `ai/use_cases/nl_query.py`：两阶段（LLM 产出查询意图 IR → 代码生成 SQL）
  - 意图 Schema 严格定义，拒绝写操作
  - API 端点 `POST /ai/query`
  - 集成测试
  - _需求: 11.13, 11.14_

- [x] 8.2 实现自然语言策略生成用例
  - `ai/use_cases/strategy_gen.py`：基于策略模板生成参数 JSON
  - Schema 校验 + 参数范围检查
  - API 端点 `POST /ai/strategy-gen`
  - 前端在策略页集成"用自然语言描述"入口
  - _需求: 11.15, 11.16_

- [x] 8.3 实现智能归因报告用例
  - `ai/use_cases/attribution_report.py`：只接受已计算的 Brinson + Fama-French + metrics 输入
  - Prompt 明确禁止 LLM 计算数值
  - 输出带"AI 生成内容"标签 + 原始数据链接
  - 前端在回测结果页调用并展示
  - _需求: 11.17, 11.18, 11.19_

- [x] 8.4 实现因子 brainstorm 用例
  - `ai/use_cases/factor_brainstorm.py`：受限 DSL 输出候选因子
  - 实现 DSL 解析器（仅允许现有字段 + 白名单函数）
  - 自动提交 IC/IR 验证，不显著的不入库只记录
  - API 端点 + 前端研究页
  - _需求: 11.20, 11.21, 11.22, 11.23_

- [x] 8.5 实现 AI 用量与成本仪表盘
  - API `GET /ai/usage`：近 30 日统计
  - 前端仪表盘展示调用次数、token、费用估算
  - _需求: 11.6_

- [x] 8.6 实现 AI 功能开关与脱敏
  - 全局配置 `AI_ENABLED`：关闭后所有 AI API 返回 501 但核心功能正常
  - 持仓数据发送前按配置脱敏（行业聚合）
  - 集成测试：关闭 AI 后核心链路完整可用
  - _需求: 11.24, 11.25, 11.26_

- [x] 8.7 实现实盘 Broker 接口预留
  - `domain/broker/base.py`：`Broker` Protocol 定义
  - 实现 `PaperBroker`（纸面撮合）作为参考实现
  - 策略层通过依赖注入使用 Broker
  - 单元测试
  - _需求: 10.3, 10.6_

- [x] 8.8 完善可扩展性验证
  - 编写"扩展性测试"：新增一个假数据源、一个假策略、一个假因子，验证无需改核心代码
  - 文档化扩展指南（docs/extending.md）
  - _需求: 10.1, 10.2, 10.4, 10.5_

- [x] 8.9 完善 CI 流水线
  - GitHub Actions：lint（Ruff + Black）+ 类型检查（mypy）+ 单元测试 + 集成测试
  - 覆盖率报告（核心模块 ≥ 70%）
  - 构建 Docker 镜像
  - _需求: 9.7_

- [x] 8.10 撰写用户与开发文档
  - `docs/getting-started.md`：快速启动指南
  - `docs/architecture.md`：架构总览
  - `docs/extending.md`：扩展开发指南
  - `docs/api.md`：API 使用示例
  - `docs/strategies.md`：内置策略说明
  - `README.md` 总入口
  - _需求: 10 全部_

---

## 任务执行注意事项

1. **每个任务独立可提交**：完成后跑通自己的测试，不破坏已完成任务
2. **测试先行**：关键模块（因子、回测、结算、费率）必须有单元测试才能标记完成
3. **增量集成**：前端页面实现时，后端 API 应已完成并通过集成测试
4. **数据对拍**：因子与绩效指标实现后，挑 3-5 只真实基金对拍天天基金展示值，误差 < 0.01%
5. **文档同步**：涉及 API 变更时同步更新 OpenAPI 文档（自动生成）

## 非编码工作（不在本 tasks 范围）

以下工作需要人工或外部资源，未列入代码任务：
- 天天基金 / AkShare 接口变动监控
- LLM API 密钥申请与额度购买
- 生产部署的基础设施（域名、SSL、服务器、监控告警渠道 token）
- 数据质量人工抽查与历史数据补全
