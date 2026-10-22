#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WMS_APP_DIR:-/opt/wms}"
SRC="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/.." && pwd)}"

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "На сервере нет $APP_DIR/.env. Скопируйте .env.example и заполните базу, SECRET_KEY и DEBUG=0." >&2
  exit 1
fi

mkdir -p "$APP_DIR"
# Runner и приложение — один пользователь (wms). acme.json не трогаем.
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude 'pgdata/' \
  --exclude 'staticfiles/' \
  --exclude '__pycache__/' \
  --exclude 'deploy/traefik/acme.json' \
  "$SRC"/ "$APP_DIR"/

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi

"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-server.txt"
"$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
"$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" seed
"$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

if [[ ! -f /etc/systemd/system/wms.service ]]; then
  echo "Служба wms ещё не установлена. Один раз на сервере:" >&2
  echo "  sudo cp $APP_DIR/deploy/wms.service /etc/systemd/system/wms.service" >&2
  echo "  sudo systemctl daemon-reload && sudo systemctl enable --now wms" >&2
  exit 1
fi

if ! command -v libreoffice >/dev/null && ! command -v soffice >/dev/null; then
  echo "Нет LibreOffice: паспорт паллеты не соберётся. От root: bash $APP_DIR/deploy/install-print.sh" >&2
fi

sudo -n systemctl restart wms
sudo -n systemctl is-active --quiet wms
