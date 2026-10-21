import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def database_from_url(url: str) -> dict:
    parsed = urlparse(url)
    name = unquote(parsed.path.lstrip("/"))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
    }


SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-wms-bolrus")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.common",
    "apps.accounts",
    "apps.catalog",
    "apps.receiving",
    "apps.planning",
    "apps.storage",
    "apps.inventory",
    "apps.picking",
    "apps.shipping",
    "apps.logistics",
    "apps.printing",
    "apps.tsd",
    "apps.onec",
]

if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.gate.StaffWebGate",
    "apps.accounts.gate.ScreenGate",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context.wms",
            ],
        },
    }
]

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://wms:wms@127.0.0.1:5432/wms_django")
DATABASES = {"default": database_from_url(DATABASE_URL)}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False
SESSION_COOKIE_AGE = 60 * 60 * 14
LOGIN_URL = "/login"

ONEC_ODATA_URL = os.environ.get("ONEC_ODATA_URL", "").rstrip("/")
ONEC_ODATA_USER = os.environ.get("ONEC_ODATA_USER", "")
ONEC_ODATA_PASSWORD = os.environ.get("ONEC_ODATA_PASSWORD", "")
ONEC_ODATA_TIMEOUT = float(os.environ.get("ONEC_ODATA_TIMEOUT", "60"))

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

if DEBUG:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "::1"]
