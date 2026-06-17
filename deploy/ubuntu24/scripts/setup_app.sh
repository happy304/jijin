#!/usr/bin/env bash
set -euo pipefail

if [ ! -d /opt/jijin/backend ] || [ ! -d /opt/jijin/frontend ]; then
  echo '未找到 /opt/jijin/backend 或 /opt/jijin/frontend，请先上传项目代码到 /opt/jijin。'
  exit 1
fi

cd /opt/jijin/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e .

if [ ! -f /opt/jijin/backend/.env ]; then
  cp /opt/jijin/deploy/ubuntu24/env.production.example /opt/jijin/backend/.env
  echo '已创建 /opt/jijin/backend/.env，请把 API_CORS_ORIGINS 中的服务器 IP 改成真实公网 IP 后再重跑本脚本。'
  exit 1
fi

alembic upgrade head

cd /opt/jijin/frontend
npm install
npm run build

cp /opt/jijin/deploy/ubuntu24/nginx/jijin.conf /etc/nginx/sites-available/jijin
ln -sf /etc/nginx/sites-available/jijin /etc/nginx/sites-enabled/jijin
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

cp /opt/jijin/deploy/ubuntu24/systemd/jijin-api.service /etc/systemd/system/jijin-api.service
cp /opt/jijin/deploy/ubuntu24/systemd/jijin-celery-worker.service /etc/systemd/system/jijin-celery-worker.service
cp /opt/jijin/deploy/ubuntu24/systemd/jijin-celery-beat.service /etc/systemd/system/jijin-celery-beat.service

systemctl daemon-reload
systemctl enable jijin-api jijin-celery-worker jijin-celery-beat
systemctl restart jijin-api jijin-celery-worker jijin-celery-beat

systemctl --no-pager status jijin-api || true
systemctl --no-pager status jijin-celery-worker || true
systemctl --no-pager status jijin-celery-beat || true
