# Deploy（部署编排）

Docker Compose / Dockerfile / 生产部署脚本集中目录。

本阶段（0.2 搭建 Docker Compose 开发环境 + 0.6 观测性基础设施）已落地：

```
deploy/
├── docker-compose.yml                # 生产编排：postgres(timescaledb)/redis/api/worker/beat/frontend/prometheus/grafana
├── docker-compose.dev.yml            # 开发编排：代码挂载 + 热重载
├── prometheus/
│   └── prometheus.yml                # Prometheus scrape 配置
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/prometheus.yaml
│   │   └── dashboards/default.yaml
│   └── dashboards/
│       └── fund-quant-overview.json  # 基础概览 dashboard
└── README.md                         # 本文件
```

对应的镜像文件：

- `backend/Dockerfile` —— 四阶段（base / builder / runtime / dev），api、worker、beat 复用同一镜像。
- `frontend/Dockerfile` —— 四阶段（deps / builder / runtime / dev），当前为 **任务 5.4 之前的占位实现**，会渲染一个提示页面；nginx 同时反向代理 `/api` → `api:8000`。

## 服务图谱

| 服务 | 端口（容器） | 端口（主机默认） | 说明 |
|---|---|---|---|
| `postgres` | 5432 | `${POSTGRES_PORT_HOST:-5432}` | TimescaleDB 2.16 / PostgreSQL 16 |
| `redis` | 6379 | `${REDIS_PORT_HOST:-6379}` | 缓存 + Celery broker/backend |
| `api` | 8000 | `${API_PORT_HOST:-8000}` | FastAPI（gunicorn + uvicorn worker） |
| `worker` | — | — | Celery worker（ingest / backtest / ai / notify） |
| `beat` | — | — | Celery beat 调度器 |
| `frontend` | 80 | `${FRONTEND_PORT_HOST:-5173}` | React SPA（nginx） |
| `prometheus` | 9090 | `${PROMETHEUS_PORT_HOST:-9090}` | 指标采集，抓取 `api:8000/metrics` |
| `grafana` | 3000 | `${GRAFANA_PORT_HOST:-3000}` | 可视化，预置 Prometheus 数据源与概览 dashboard |

所有后端服务共享以下命名卷：

- `fqp-snapshots` → `/app/local_data/snapshots`（原始响应归档，需求 1.9）
- `fqp-backups` → `/app/local_data/backups`（数据库备份，需求 2.10）
- `fqp-logs` → `/app/logs`

## 环境变量

所有服务通过仓库根目录的 `.env` 读取配置（样例见 `../.env.example`）。compose 文件使用 `${VAR:-default}` 语法，**缺变量会在 `docker compose config` 阶段立即暴露**，不会静默失败。

首次使用：

```bash
cp .env.example .env    # 在仓库根目录执行
# 按需改 SECRET_KEY / POSTGRES_PASSWORD 等
```

## 启动（生产形态）

> 需求 9.1：`docker compose up` 一键启动全部服务。

```bash
# 仓库根目录
docker compose -f deploy/docker-compose.yml up -d

# 检查健康状态（req 9.1 验收）
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f api
```

> 阶段 0.3 完成 `/health` 端点、0.5 完成 Celery 应用后，api / worker / beat 的健康检查会自然转为 HTTP / `celery inspect ping`。

## 启动（开发形态，支持热重载）

> 需求 9.5：开发模式支持代码挂载与热重载。

```bash
# 仓库根目录
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up
```

特性：

- `backend/app`、`backend/pyproject.toml`、`backend/tests` 挂载进容器；`uvicorn --reload` 监听 `/app/app`。
- Celery worker / beat 使用 dev stage 镜像（含 dev + test 额外依赖），代码改动后 `docker compose restart worker beat` 即可生效。
- `frontend/` 整目录挂载，在 vite dev server 下启动 HMR；当前任务 5.4 尚未落地，镜像会退化为占位 `http-server`，不影响其它服务上线。
- 数据库 / Redis 使用独立的 `*-dev` 命名卷，避免污染生产数据。

## 验收对照

| 需求 | 要点 | 对应文件 |
|---|---|---|
| 9.1 | 单条命令拉起全部服务 | `docker-compose.yml` |
| 9.5 | 开发模式代码挂载 + 热重载 | `docker-compose.dev.yml` + `backend/Dockerfile` dev stage + `frontend/Dockerfile` dev stage |
| 9.6 | 生产模式使用 gunicorn/uvicorn 多进程、关闭 DEBUG | `docker-compose.yml` api 服务 command + `DEBUG=false` |

### 已知静态验证结果（本阶段）

- `docker-compose.yml`、`docker-compose.dev.yml` 已通过 YAML 解析校验。
- 服务依赖、健康检查、命名卷等字段完整且互相引用正确。
- 由于当前构建环境未安装 Docker，`docker compose up` / `docker compose config` 的实机验证需要在装有 Docker Desktop 或 Docker Engine 的机器上执行；对应命令见"启动（生产形态）"一节。

### 仍需在 docker 环境中执行的验证

```bash
# 1. 仅做 schema / 变量解析校验（无需拉起容器）
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml config

# 2. 真正拉起完整服务图谱并检查健康
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps

# 3. 清理
docker compose -f deploy/docker-compose.yml down -v
```

## 后续阶段的增量

本任务之后，下列 compose 扩展在各自任务中落地：

- 6.4 补齐 ingest / backtest / LLM / queue-depth 指标埋点
- 6.5 Alertmanager 服务 + 告警规则 `deploy/prometheus/rules/*.yml`
- 6.6 扩展 Grafana dashboard：任务执行状态、数据完整度、数据源健康度、LLM 用量
- 8.9 CI 中的镜像构建

## 观测性（任务 0.6）

- **Prometheus** 抓取 `api:8000/metrics`（由 `prometheus-fastapi-instrumentator` 暴露）；配置在 `prometheus/prometheus.yml`，全局 15s 采样。
- **Grafana** 自动装载 `grafana/provisioning/` 下的 datasource 与 dashboard provider，dashboard JSON 来自 `grafana/dashboards/`。
- 默认登录 `admin / admin`（由 `GRAFANA_ADMIN_PASSWORD` 覆盖），启动后访问 `http://localhost:${GRAFANA_PORT_HOST:-3000}` 即可看到 "Fund Quant Platform — Overview" dashboard。
- 首次启动后可在 Prometheus `http://localhost:${PROMETHEUS_PORT_HOST:-9090}/targets` 验证 `fqp-api` target 为 UP。
