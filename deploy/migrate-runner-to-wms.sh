#!/usr/bin/env bash
# Перенос GitHub Actions runner на пользователя wms (приложение + деплой = один uid).
# Не трогает Docker, PostgreSQL, Traefik, ControlGate и прочие службы.
# Запуск: sudo bash deploy/migrate-runner-to-wms.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужен root: sudo bash $0" >&2
  exit 1
fi

OLD="${OLD_RUNNER_DIR:-/home/bolrus/actions-runner}"
NEW="${NEW_RUNNER_DIR:-/home/wms/actions-runner}"
APP_DIR="${WMS_APP_DIR:-/opt/wms}"
UNIT="actions.runner.RustamHash-wms_bolrus_django.wms.service"

echo "==> Stop/uninstall runner (bolrus)"
if [[ -x "$OLD/svc.sh" ]]; then
  (cd "$OLD" && ./svc.sh stop) || true
  (cd "$OLD" && ./svc.sh uninstall) || true
fi
systemctl stop "$UNIT" 2>/dev/null || true
systemctl disable "$UNIT" 2>/dev/null || true
rm -f /etc/systemd/system/"$UNIT"
systemctl daemon-reload

if [[ ! -f "$OLD/.runner" ]]; then
  echo "Нет $OLD/.runner — runner не найден у bolrus." >&2
  exit 1
fi

if [[ -e "$NEW" ]]; then
  echo "Уже есть $NEW — удалите вручную, если это старый мусор, и перезапустите." >&2
  exit 1
fi

echo "==> Copy runner → $NEW (outside $APP_DIR, чтобы rsync --delete не стёр)"
install -d -o wms -g wms /home/wms
rsync -a "$OLD"/ "$NEW"/
chown -R wms:wms /home/wms
chown -R wms:wms "$NEW"

echo "==> App tree owner wms"
chown -R wms:wms "$APP_DIR"
if [[ -f "$APP_DIR/deploy/traefik/acme.json" ]]; then
  chown root:root "$APP_DIR/deploy/traefik/acme.json"
  chmod 600 "$APP_DIR/deploy/traefik/acme.json"
fi

echo "==> Install/start runner service as wms"
cd "$NEW"
./svc.sh install wms
./svc.sh start
sleep 2
./svc.sh status || systemctl status "$UNIT" --no-pager -l

echo "==> sudoers for deploy restart"
cat >/etc/sudoers.d/wms-deploy <<'EOF'
wms ALL=(root) NOPASSWD: /bin/systemctl restart wms, /bin/systemctl is-active wms
EOF
chmod 440 /etc/sudoers.d/wms-deploy
visudo -cf /etc/sudoers.d/wms-deploy

echo "==> Checks"
systemctl is-active "$UNIT"
sudo -u wms -n systemctl is-active wms
sudo -u wms test -w "$APP_DIR" && echo "wms can write $APP_DIR"

echo
echo "Готово. Runner должен быть Idle на GitHub."
echo "Старый каталог можно удалить позже: rm -rf $OLD"
echo "Re-run failed Deploy в GitHub Actions."
