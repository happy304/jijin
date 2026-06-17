#!/usr/bin/env bash
set -euo pipefail

apt update && apt upgrade -y
apt install -y git curl wget vim unzip nginx postgresql postgresql-contrib redis-server python3 python3-venv python3-pip build-essential ca-certificates

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt install -y nodejs
fi

timedatectl set-timezone Asia/Shanghai

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  swapon /swapfile || true
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

systemctl enable postgresql redis-server nginx
systemctl start postgresql redis-server nginx

sudo -u postgres psql <<'SQL'
DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'fundquant') THEN
      CREATE ROLE fundquant LOGIN PASSWORD 'fundquant';
   END IF;
END
$$;
SQL

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'fundquant'" | grep -q 1 || sudo -u postgres createdb -O fundquant fundquant
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE fundquant TO fundquant;"

mkdir -p /opt/jijin

python3 --version
node -v
npm -v
psql --version
redis-server --version
free -h
