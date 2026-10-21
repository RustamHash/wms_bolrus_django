#!/usr/bin/env bash
# Первый запуск на чистом/частично подготовленном Ubuntu.
# Запуск: sudo bash /tmp/wms-bootstrap/deploy/bootstrap-server.sh
set -euo pipefail

APP_DIR="${WMS_APP_DIR:-/opt/wms}"
SRC_DIR="${WMS_SRC_DIR:-/tmp/wms-bootstrap}"
DB_NAME="${WMS_DB_NAME:-wms_django}"
DB_USER="${WMS_DB_USER:-wms}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запустите от root: sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$SRC_DIR/manage.py" ]]; then
  echo "Нет исходников в $SRC_DIR. Сначала скопируйте репозиторий туда." >&2
  exit 1
fi

echo "==> Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  git curl ca-certificates rsync ufw \
  python3 python3-venv python3-pip \
  postgresql postgresql-contrib \
  docker.io docker-compose-v2

systemctl enable --now postgresql docker

echo "==> Firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable || true

echo "==> Пользователь wms"
id -u wms >/dev/null 2>&1 || useradd -r -m -d "$APP_DIR" -s /bin/bash wms
mkdir -p "$APP_DIR"
chown wms:wms "$APP_DIR"

echo "==> PostgreSQL"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  DB_PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"
  echo "Создан пользователь БД ${DB_USER}, пароль сохранён в ${APP_DIR}/.env"
else
  DB_PASS=""
  echo "Пользователь БД ${DB_USER} уже есть"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  if [[ -z "$DB_PASS" ]]; then
    echo "Базы ${DB_NAME} нет, а пароль пользователя неизвестен." >&2
    echo "Задайте пароль вручную и перезапустите, либо удалите роль ${DB_USER}." >&2
    exit 1
  fi
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
  sudo -u postgres psql -d "$DB_NAME" -c "GRANT ALL ON SCHEMA public TO ${DB_USER};" || true
fi

echo "==> Код → ${APP_DIR}"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude 'pgdata/' \
  --exclude 'staticfiles/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "$SRC_DIR"/ "$APP_DIR"/
chown -R wms:wms "$APP_DIR"

echo "==> .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  SECRET="$(openssl rand -base64 48 | tr -d '/+=' | head -c 64)"
  if [[ -z "$DB_PASS" ]]; then
    echo "Нет ${APP_DIR}/.env и неизвестен пароль БД — создайте .env вручную." >&2
    exit 1
  fi
  cat >"$APP_DIR/.env" <<EOF
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
SECRET_KEY=${SECRET}
DEBUG=0
CSRF_TRUSTED_ORIGINS=https://wms.bolrus.ru
EOF
  chown wms:wms "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "Создан ${APP_DIR}/.env"
else
  echo "Оставляем существующий ${APP_DIR}/.env"
  # Допишем CSRF/DEBUG, если пусто
  grep -q '^DEBUG=' "$APP_DIR/.env" || echo 'DEBUG=0' >>"$APP_DIR/.env"
  grep -q '^CSRF_TRUSTED_ORIGINS=' "$APP_DIR/.env" || \
    echo 'CSRF_TRUSTED_ORIGINS=https://wms.bolrus.ru' >>"$APP_DIR/.env"
fi

echo "==> venv + migrate"
sudo -u wms python3 -m venv "$APP_DIR/.venv"
sudo -u wms "$APP_DIR/.venv/bin/pip" install -U pip
sudo -u wms "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-server.txt"
sudo -u wms "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u wms "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" seed
sudo -u wms "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

echo "==> systemd wms"
cp "$APP_DIR/deploy/wms.service" /etc/systemd/system/wms.service
systemctl daemon-reload
systemctl enable --now wms
systemctl is-active --quiet wms
curl -sI http://127.0.0.1:8050/ | head -n 5 || true

echo "==> Traefik"
install -d -m 755 "$APP_DIR/deploy/traefik"
touch "$APP_DIR/deploy/traefik/acme.json"
chmod 600 "$APP_DIR/deploy/traefik/acme.json"
chown root:root "$APP_DIR/deploy/traefik/acme.json"
cd "$APP_DIR/deploy/traefik"
docker compose up -d
docker compose ps

echo
echo "Готово."
echo "  Локально:  curl -I http://127.0.0.1:8050/"
echo "  HTTPS:     curl -I https://wms.bolrus.ru   (нужны DNS + проброс 80/443)"
echo "  Логин:     admin / PIN 0000"
echo "  .env:      ${APP_DIR}/.env"
