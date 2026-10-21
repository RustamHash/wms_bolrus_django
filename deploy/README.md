# Деплой WMS BolRus (сервер с нуля)

Стек: Ubuntu → PostgreSQL → Django (gunicorn/systemd на `127.0.0.1:8050`) → Traefik (Docker, HTTPS).  
Домен: `wms.bolrus.ru`. LAN-сервер: `192.168.1.109`.

После установки вход: пользователь `admin` / PIN `0000` (из `seed`).

---

## 0. До сервера

1. DNS: A-запись `wms.bolrus.ru` → **публичный** IP сервера (нужно для Let's Encrypt).
2. На роутере проброс **80** и **443** на `192.168.1.109` (если сервер за NAT).
3. Код репозитория доступен (git clone или rsync с вашей машины).

---

## 1. Базовые пакеты

Под root / sudo на Ubuntu:

```bash
sudo apt update
sudo apt install -y \
  git curl ca-certificates \
  python3 python3-venv python3-pip \
  postgresql postgresql-contrib \
  docker.io docker-compose-v2 \
  rsync ufw

sudo systemctl enable --now postgresql docker
```

Firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

---

## 2. PostgreSQL

Придумайте пароль БД (ниже `CHANGE_ME_DB_PASSWORD`):

```bash
sudo -u postgres psql <<'SQL'
CREATE USER wms WITH PASSWORD 'CHANGE_ME_DB_PASSWORD';
CREATE DATABASE wms_django OWNER wms;
GRANT ALL PRIVILEGES ON DATABASE wms_django TO wms;
SQL
```

На новых PostgreSQL (15+) дополнительно:

```bash
sudo -u postgres psql -d wms_django -c "GRANT ALL ON SCHEMA public TO wms;"
```

Проверка:

```bash
psql "postgresql://wms:CHANGE_ME_DB_PASSWORD@127.0.0.1:5432/wms_django" -c 'SELECT 1'
```

---

## 3. Пользователь и код

```bash
sudo useradd -r -m -d /opt/wms -s /bin/bash wms || true
sudo mkdir -p /opt/wms
sudo chown wms:wms /opt/wms
```

Кладёте код в `/opt/wms` (выберите один способ).

**A) git clone** (подставьте URL репозитория):

```bash
sudo -u wms git clone https://github.com/ORG/wms_BolRus_django.git /opt/wms
# или во временную папку и rsync, если /opt/wms уже не пустой
```

**B) с вашего ПК (PowerShell / scp):**

```bash
# пример: с машины разработки в каталог на сервере
scp -r D:\projects\wms_BolRus_django\* user@192.168.1.109:/tmp/wms-src/
```

На сервере:

```bash
sudo rsync -a --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.env' \
  --exclude 'pgdata/' --exclude 'staticfiles/' --exclude '__pycache__/' \
  /tmp/wms-src/ /opt/wms/
sudo chown -R wms:wms /opt/wms
```

---

## 4. `.env`

```bash
sudo -u wms cp /opt/wms/.env.example /opt/wms/.env
sudo -u wms nano /opt/wms/.env
```

Минимум:

```env
DATABASE_URL=postgresql://wms:CHANGE_ME_DB_PASSWORD@127.0.0.1:5432/wms_django
SECRET_KEY=сгенерируйте-длинную-случайную-строку
DEBUG=0
CSRF_TRUSTED_ORIGINS=https://wms.bolrus.ru
```

Секрет можно так:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

---

## 5. Python: venv, миграции, статика

```bash
sudo -u wms python3 -m venv /opt/wms/.venv
sudo -u wms /opt/wms/.venv/bin/pip install -U pip
sudo -u wms /opt/wms/.venv/bin/pip install -r /opt/wms/requirements-server.txt
sudo -u wms /opt/wms/.venv/bin/python /opt/wms/manage.py migrate --noinput
sudo -u wms /opt/wms/.venv/bin/python /opt/wms/manage.py seed
sudo -u wms /opt/wms/.venv/bin/python /opt/wms/manage.py collectstatic --noinput
```

---

## 6. systemd (gunicorn)

```bash
sudo cp /opt/wms/deploy/wms.service /etc/systemd/system/wms.service
sudo systemctl daemon-reload
sudo systemctl enable --now wms
sudo systemctl status wms --no-pager
curl -sI http://127.0.0.1:8050/ | head
```

Должен ответить Django (302 на логин или 200). Если ошибка — `journalctl -u wms -n 50 --no-pager`.

---

## 7. Traefik (HTTPS)

При необходимости поправьте email в `/opt/wms/deploy/traefik/traefik.yml` (`admin@bolrus.ru`).

```bash
cd /opt/wms/deploy/traefik
sudo touch acme.json
sudo chmod 600 acme.json
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=80
```

Проверка с сервера (после того как DNS уже указывает на этот хост):

```bash
curl -I https://wms.bolrus.ru
```

Ожидается `HTTP/2 200` или `302`, сертификат Let's Encrypt.  
Если ACME падает — проверьте DNS, проброс 80/443 и логи Traefik.

---

## 8. ПК в локальной сети (Windows)

Пока DNS с ПК резолвит публичный IP, внутри офиса удобнее hosts → `192.168.1.109`:

1. Скопировать на ПК `deploy/windows/`
2. ПКМ `set-wms-hosts.cmd` → **Запуск от имени администратора**
3. Открыть `https://wms.bolrus.ru`

---

## 9. (Позже) автодеплой GitHub Actions

Self-hosted runner на этом сервере, workflow уже зовёт [`update.sh`](update.sh).

Runner-пользователю нужен passwordless sudo только на:

```
systemctl restart wms
systemctl is-active wms
```

Файл `/opt/wms/.env` деплоем **не** перезаписывается.

После смены unit-файла:

```bash
sudo cp /opt/wms/deploy/wms.service /etc/systemd/system/wms.service
sudo systemctl daemon-reload
sudo systemctl restart wms
```

---

## Быстрая диагностика

| Симптом | Куда смотреть |
|--------|----------------|
| Нет ответа на 8050 | `systemctl status wms`, `journalctl -u wms -n 80` |
| Traefik не стартует | `acme.json` должен быть **файлом** chmod 600, не каталогом |
| Нет сертификата | DNS A, порт 80 с интернета, `docker compose logs` |
| CSRF / cookie | `DEBUG=0`, `CSRF_TRUSTED_ORIGINS=https://wms.bolrus.ru` |
| LAN не открывается | hosts на ПК, ufw 80/443, Traefik слушает 0.0.0.0 |

Локальная разработка на Windows: `DEBUG=1`, `py manage.py runserver 0.0.0.0:8050` — без Traefik.
