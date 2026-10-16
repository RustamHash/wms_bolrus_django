# WMS

Нужен PostgreSQL: база `wms_django`, пользователь `wms`, пароль `wms`.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
py manage.py makemigrations
py manage.py migrate
py manage.py seed
py manage.py runserver
```

Сайт: http://127.0.0.1:8000/login

После `seed`, если пользователей ещё не было: `admin` / `admin`, `receiver` / `receiver`, `warehouse` / `warehouse`.
