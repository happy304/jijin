# Fund Quant Platform（基金量化平台）

面向个人基金投资者与量化研究者的一体化研究 / 回测 / 监控平台。

## 核心特性

- **多源数据采集** — 天天基金（主源）+ AkShare（备源）+ 本地快照兜底，自动降级与熔断
- **专业因子库** — 6 大类 30+ 因子，覆盖收益、风险、风险调整、基准相关、持仓、归因
- **事件驱动回测** — 正确处理基金 T+1 结算、阶梯费率、分红再投、限购检查
- **内置策略库** — 定投、动量轮动、风险平价、均值-方差、择时、FOF 六类经典策略
- **个人组合检查** — 结合持仓、数据质量、风险约束和回测证据输出研究辅助
- **AI 辅助增强** — 自然语言查询、策略生成、归因报告、公告解析（不参与核心决策）
- **Windows 本地脚本** — 一键启动 / 关闭前端、后端、Celery、Redis、PostgreSQL

> 本平台仅用于个人研究、筛选、回测验证和组合风险辅助，不构成投资建议或交易指令。

## 技术栈

| 后端 | 前端 | 数据 | 部署 |
|------|------|------|------|
| FastAPI + SQLAlchemy 2.0 | React 18 + Vite + TypeScript | PostgreSQL + TimescaleDB | Docker Compose / Windows bat |
| Celery + Redis | Ant Design + ECharts | Redis 缓存 | Prometheus + Grafana |
| Python 3.11+ | TanStack Query + Zustand | Pandas / NumPy / SciPy | Alertmanager |

## Windows 本地快速启动

当前目录提供了适合本机开发/自用的脚本：

| 脚本 | 用途 |
|------|------|
| `启动.bat` | 启动 PostgreSQL、Redis、Celery Worker、Celery Beat、前端和后端 API |
| `关闭.bat` | 停止项目相关进程、Redis 和 PostgreSQL |
| `采集数据.bat` | 执行数据采集相关任务 |
| `每日更新数据.bat` | 执行每日数据更新任务 |

### 默认访问地址

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5173 |
| 后端 API | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |

### 启动前检查

请确认本机已有：

- Python 3.11+
- Node.js + npm
- PostgreSQL 16（或自行配置路径）
- Redis Windows 服务（默认服务名：`Redis`）

前端依赖如未安装：

```bash
cd frontend
npm install
```

后端依赖如未安装：

```bash
cd backend
pip install -e ".[dev,test]"
```

### 自定义 PostgreSQL / Redis / 端口

`启动.bat` 和 `关闭.bat` 支持环境变量覆盖默认配置，例如：

```bat
set PG_BIN=C:\Program Files\PostgreSQL\16\bin
set PG_DATA=D:\pgdata
set REDIS_SERVICE=Redis
set BACKEND_PORT=8000
set FRONTEND_PORT=5173
启动.bat
```

如果你的 Redis 服务名不是 `Redis`，请先设置：

```bat
set REDIS_SERVICE=你的Redis服务名
启动.bat
```

## Docker 快速开始

```bash
# 配置环境变量
cp .env.example .env

# 一键启动
docker compose -f deploy/docker-compose.yml up -d
```

启动后访问：

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5173 |
| API 文档 | http://localhost:8000/docs |
| Grafana 监控 | http://localhost:3000 |

详细说明见 [快速启动指南](docs/getting-started.md)。

## 前端开发

```bash
cd frontend

# 启动开发服务器
npm run dev

# 类型检查
npm run type-check

# 生产构建
npm run build
```

开发模式下，Vite 会将 `/api` 请求代理到 `http://localhost:8000`。

## 后端开发

```bash
cd backend

# 创建虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -e ".[dev,test]"

# 代码质量
ruff check .
black --check .
mypy app
pytest
```

## 文档

| 文档 | 说明 |
|------|------|
| [快速启动指南](docs/getting-started.md) | 环境搭建与首次运行 |
| [架构总览](docs/architecture.md) | 系统设计与模块划分 |
| [API 使用示例](docs/api.md) | REST API 调用示例 |
| [内置策略说明](docs/strategies.md) | 6 类策略的参数与用法 |
| [扩展开发指南](docs/extending.md) | 新增数据源、因子、策略、Broker |

## 仓库结构

```text
.
├── backend/          # Python 后端
│   ├── app/
│   │   ├── api/      # REST API 路由
│   │   ├── domain/   # 领域核心（因子、回测、策略、风控）
│   │   ├── services/ # 应用服务
│   │   ├── ai/       # LLM 辅助层
│   │   ├── data/     # 数据层（采集、校验、存储）
│   │   ├── tasks/    # Celery 异步任务
│   │   └── notify/   # 告警推送
│   ├── migrations/   # Alembic 数据库迁移
│   └── tests/        # 测试
├── frontend/         # React 前端
├── deploy/           # Docker Compose + 监控配置
└── docs/             # 项目文档
```

## 常见问题

### 1. `启动.bat` 找不到 PostgreSQL

检查 `PG_BIN` 和 `PG_DATA` 是否正确：

```bat
set PG_BIN=C:\Program Files\PostgreSQL\16\bin
set PG_DATA=D:\pgdata
启动.bat
```

### 2. Redis 启动提示失败

可能是 Redis 已运行，或服务名不是 `Redis`。可以在 Windows 服务管理器里查看实际名称，然后：

```bat
set REDIS_SERVICE=你的Redis服务名
启动.bat
```

### 3. 前端页面有接口错误

确认后端已启动，并且可以打开：

```text
http://localhost:8000/health
http://localhost:8000/docs
```

### 4. 页面数据为空

先执行数据采集脚本，或在前端“基金检索”页使用一键采集补齐基金净值。

## 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 项目基础设施 | ✅ |
| 1 | 数据采集与存储 | ✅ |
| 2 | 因子计算库 | ✅ |
| 3 | 回测引擎 | ✅ |
| 4 | 策略库与风控 | ✅ |
| 5 | API、前端与绩效报告 | ✅ |
| 6 | 调度与监控 | ✅ |
| 7 | AI 数据增强 | ✅ |
| 8 | AI 研究增强与收尾 | 进行中 |

## 许可

TBD.
