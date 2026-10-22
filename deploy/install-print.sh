#!/usr/bin/env bash
# Пакеты печати для Ubuntu: паспорт паллеты (LibreOffice) и очереди CUPS.
# Запуск от root: bash /opt/wms/deploy/install-print.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запустите от root: bash $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  libreoffice-writer \
  fonts-liberation \
  fonts-crosextra-carlito \
  fonts-crosextra-caladea \
  fonts-dejavu-core \
  cups-client

here="$(cd "$(dirname "$0")" && pwd)"
install -m 644 "$here/99-wms-fonts.conf" /etc/fonts/conf.d/99-wms-fonts.conf
fc-cache -f

echo "LibreOffice: $(command -v libreoffice || command -v soffice)"
echo "lp: $(command -v lp)"
