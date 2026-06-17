# 快速启动指南

本文档帮助你在 5 分钟内启动基金量化平台的完整开发环境。

## 前置条件

| 工具 | 最低版本 | 说明 |
|------|---------|------|
| Docker + Docker Compose | 24.x / v2 | 容器化运行全部服务 |
| Python | 3.11+ | 后端开发（可选，Docker 内已包含） |
| Node.js | 18+ | 前端开发（可选，Docker 内已包含） |
| Git | 2.x | 版本控制 |

## 一键启动（Docker Compose）

```bash
# 1. 克隆仓库
git clone <repo-url> fund-quant-platform
cd fund-quant-platform

# 2. 复制环境变量模板
cp .env.example .env
# 按需修改 .env 中的数据库密码、AI API Key 等

# 3. 启动全部服务
docker compose -f deploy/docker-compose.yml up -d
```

启动后可访问：

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端 | http://localhost:5173 | React SPA |
| API | http://localhost:8000 | FastAPI 后端 |
| API 文档 | http://localhost:8000/docs | OpenAPI Swagger UI |
| Grafana | http://localhost:3000 | 监控面板（admin/admin） |
| Prometheus | http://localhost:9090 | 指标查询 |

## 开发模式（热重载）

开发模式会挂载本地代码到容器内，修改代码后自动重载：

```bash
docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.dev.yml up
```

## 本地后端开发

如果你更习惯在本地直接运行后端（需要本地 PostgreSQL + Redis）：

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -U pip
pip install -e ".[dev,test]"

# 启用 pre-commit
pre-commit install

# 运行数据库迁移
alembic upgrade head

# 启动 API 服务（开发模式）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

在另一个终端启动 Celery Worker：

```bash
cd backend
celery -A app.tasks.celery_app worker --loglevel=INFO --queues=ingest,backtest,ai,notify
```

启动 Celery Beat 调度器：

```bash
cd backend
celery -A app.tasks.celery_app beat --loglevel=INFO
```

## 本地前端开发

```bash
cd frontend
npm install
npm run dev
```

前端默认在 http://localhost:5173 启动，API 请求代理到 http://localhost:8000。

## 环境变量说明

关键环境变量（完整列表见 `.env.example`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_ENV` | development | 运行环境 |
| `DATABASE_URL` | postgresql+asyncpg://... | 数据库连接（异步） |
| `REDIS_URL` | redis://localhost:6379/0 | Redis 连接 |
| `AI_ENABLED` | true | 是否启用 AI 辅助功能 |
| `AI_DEFAULT_PROVIDER` | openai_compat | 默认 LLM 提供商 |
| `OPENAI_API_KEY` | - | OpenAI 兼容 API 密钥 |
| `LLM_DAILY_TOKEN_LIMIT` | 2000000 | 每日 Token 预算 |

## 验证安装

```bash
# 检查 API 健康状态
curl http://localhost:8000/health

# 检查 Prometheus 指标
curl http://localhost:8000/metrics

# 运行后端测试
cd backend && pytest

# 运行代码质量检查
ruff check .
black --check .
mypy app
```

## CLI 工具

平台提供 CLI 用于手动触发任务：

```bash
cd backend

# 手动触发单只基金数据采集
python -m app.cli ingest --fund 000001

# 手动触发回测
python -m app.cli backtest --strategy 1

# 手动触发信号生成
python -m app.cli signal --strategy 1
```

## 常见问题

### Docker 启动失败

1. 确认 Docker Desktop 已启动
2. 检查端口是否被占用（5432、6379、8000、5173）
3. 运行 `docker compose -f deploy/docker-compose.yml logs` 查看日志

### 数据库连接失败

确认 `.env` 中的数据库配置与实际服务一致。Docker 环境下使用容器内部主机名（`postgres`），本地开发使用 `localhost`。

### AI 功能不可用

设置 `AI_ENABLED=false` 可关闭 AI 功能，平台核心功能（数据采集、因子计算、回测、策略）不受影响。

## 下一步

- [架构总览](./architecture.md) — 了解系统设计
- [API 使用示例](./api.md) — 快速上手 API
- [内置策略说明](./strategies.md) — 了解可用策略
- [扩展开发指南](./extending.md) — 添加自定义数据源、因子、策略
