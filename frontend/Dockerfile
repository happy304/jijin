# syntax=docker/dockerfile:1.7
# =====================================================================
# Fund Quant Platform — Frontend Dockerfile (multi-stage, STUB)
#
# The real React 18 + Vite + TypeScript app is scaffolded in task 5.4.
# Until then this Dockerfile ships a clearly-marked placeholder page so
# `docker compose up` can bring the full service graph online during
# phases 0 – 4 without blocking on frontend work.
#
# Stage layout (matches the final shape so nothing changes for 5.4):
#   1. deps       — install Node modules from package.json (when present)
#   2. builder    — run `vite build` to produce /app/dist
#   3. runtime    — nginx:alpine serving the built SPA (production)
#   4. dev        — node:alpine running `vite` dev server with HMR
#
# When package.json is absent (phases 0–4) stage 1/2 synthesise a tiny
# placeholder SPA so the image still builds and serves on port 80.
# =====================================================================

ARG NODE_VERSION=20
ARG NGINX_VERSION=1.27-alpine

# ---------------------------------------------------------------------
# 1. Deps — install node_modules. Runs in a dedicated stage so rebuilds
#    triggered by source-only changes don't redo `npm ci`.
# ---------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS deps

WORKDIR /app

# Copy manifest + lockfile if present. The `|| true` guard lets the
# image build today when the real package.json has not been scaffolded
# yet (task 5.4). Once it exists, Docker's layer cache kicks in.
COPY package.json* package-lock.json* pnpm-lock.yaml* yarn.lock* ./

RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    elif [ -f yarn.lock ]; then \
        corepack enable && yarn install --frozen-lockfile; \
    elif [ -f pnpm-lock.yaml ]; then \
        corepack enable && pnpm install --frozen-lockfile; \
    elif [ -f package.json ]; then \
        npm install --no-audit --no-fund; \
    else \
        echo "[frontend] package.json not found — stub build will run"; \
        mkdir -p node_modules; \
    fi

# ---------------------------------------------------------------------
# 2. Builder — produce /app/dist. Uses vite when available, otherwise
#    falls back to a static placeholder so downstream stages succeed.
# ---------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN if [ -f package.json ] && grep -q '"build"' package.json; then \
        echo "[frontend] running real build"; \
        npm run build; \
    else \
        echo "[frontend] no build script — emitting placeholder dist/"; \
        mkdir -p dist; \
        printf '%s\n' \
          '<!doctype html>' \
          '<html lang="zh-CN"><head><meta charset="utf-8">' \
          '<meta name="viewport" content="width=device-width,initial-scale=1">' \
          '<title>Fund Quant Platform — Frontend stub</title>' \
          '<style>body{font-family:-apple-system,Segoe UI,sans-serif;max-width:720px;margin:4rem auto;padding:0 1rem;color:#1f2937;line-height:1.6}code{background:#f3f4f6;padding:.1rem .35rem;border-radius:4px}a{color:#2563eb}</style>' \
          '</head><body>' \
          '<h1>🚧 Fund Quant Platform — Frontend stub</h1>' \
          '<p>真正的 React 18 + Vite + TypeScript 前端将在 <code>tasks.md</code> 的任务 <strong>5.4</strong> 中完成。</p>' \
          '<p>当前镜像仅用于验证 <code>docker compose up</code> 能够拉起完整服务图谱。</p>' \
          '<h2>后端健康检查</h2>' \
          '<ul>' \
          '<li>API 健康检查：<a href="/api/health"><code>/api/health</code></a></li>' \
          '<li>API 文档：<a href="/api/docs"><code>/api/docs</code></a></li>' \
          '<li>Prometheus 指标：<a href="/api/metrics"><code>/api/metrics</code></a></li>' \
          '</ul>' \
          '</body></html>' \
          > dist/index.html; \
    fi

# ---------------------------------------------------------------------
# 3. Runtime — nginx serves the built SPA. Also reverse-proxies /api
#    to the backend container so the frontend origin works standalone.
# ---------------------------------------------------------------------
FROM nginx:${NGINX_VERSION} AS runtime

# Lightweight curl for healthchecks
RUN apk add --no-cache curl

# SPA-friendly nginx config with /api proxying
RUN printf '%s\n' \
    'server {' \
    '  listen 80;' \
    '  server_name _;' \
    '  root /usr/share/nginx/html;' \
    '  index index.html;' \
    '  location / {' \
    '    try_files $uri $uri/ /index.html;' \
    '  }' \
    '  location /api/ {' \
    '    proxy_pass http://api:8000/;' \
    '    proxy_http_version 1.1;' \
    '    proxy_set_header Host $host;' \
    '    proxy_set_header X-Real-IP $remote_addr;' \
    '    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
    '    proxy_set_header X-Forwarded-Proto $scheme;' \
    '    proxy_read_timeout 120s;' \
    '  }' \
    '  location /ws/ {' \
    '    proxy_pass http://api:8000/ws/;' \
    '    proxy_http_version 1.1;' \
    '    proxy_set_header Upgrade $http_upgrade;' \
    '    proxy_set_header Connection "upgrade";' \
    '    proxy_set_header Host $host;' \
    '    proxy_read_timeout 3600s;' \
    '  }' \
    '}' \
    > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

# ---------------------------------------------------------------------
# 4. Dev — runs the Vite dev server with HMR. Used by
#          docker-compose.dev.yml with the frontend/ directory bind-mounted.
# ---------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS dev

WORKDIR /app

RUN apk add --no-cache curl

# We deliberately do NOT copy node_modules here — the dev compose file
# bind-mounts the whole frontend/ directory. If package.json is absent
# (phases 0–4) the container prints a helpful message and sleeps so the
# dev stack still reports healthy.
EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://localhost:5173/ || exit 1

CMD ["sh", "-c", "\
    if [ -f package.json ]; then \
        echo '[frontend-dev] installing node_modules'; \
        npm install --no-audit --no-fund; \
        echo '[frontend-dev] starting vite dev server'; \
        npx vite --host 0.0.0.0 --port 5173; \
    else \
        echo '[frontend-dev] package.json missing — frontend scaffolding pending (task 5.4).'; \
        echo '[frontend-dev] serving placeholder on :5173 so healthcheck passes'; \
        mkdir -p /tmp/stub && echo '<h1>frontend stub (task 5.4 pending)</h1>' > /tmp/stub/index.html; \
        cd /tmp/stub && npx --yes http-server -p 5173 -a 0.0.0.0; \
    fi \
"]
