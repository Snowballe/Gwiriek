"""Django settings for Gwiriek, configured through ``GWIRIEK_*`` environment variables.

See ``.env.example`` for the list of variables.
"""

import os
import secrets
from pathlib import Path

import django_stubs_ext
from django.utils.translation import gettext_lazy as _

from gwiriek.dburl import parse_database_url

# Makes generic classes such as ``ModelAdmin[Model]`` subscriptable at runtime.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable (``1``, ``true``, ``yes``, ``on``)."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable as a list of non-empty strings."""
    return [part.strip() for part in os.environ.get(name, default).split(",") if part.strip()]


DEBUG = _env_bool("GWIRIEK_DEBUG")

# Without an explicit key, an ephemeral one is generated: the CLI and MCP server work, but
# admin sessions do not survive a restart. The ``gwiriek.W001`` system check reports it.
SECRET_KEY_FROM_ENV = bool(os.environ.get("GWIRIEK_SECRET_KEY"))
SECRET_KEY = os.environ.get("GWIRIEK_SECRET_KEY") or secrets.token_urlsafe(50)

ALLOWED_HOSTS = _env_list("GWIRIEK_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _env_list("GWIRIEK_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "gwiriek.equipment",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gwiriek.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "gwiriek.wsgi.application"

DATABASES = {
    "default": parse_database_url(
        os.environ.get("GWIRIEK_DATABASE_URL", "sqlite:///db.sqlite3"), BASE_DIR
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = os.environ.get("GWIRIEK_LANGUAGE_CODE", "fr")
LANGUAGES = [("fr", _("French")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "gwiriek" / "locale"]
TIME_ZONE = os.environ.get("GWIRIEK_TIME_ZONE", "Europe/Paris")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("GWIRIEK_STATIC_ROOT", BASE_DIR / "staticfiles"))
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("GWIRIEK_MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# E-mail: the console backend prints messages, for development. In production, set
# GWIRIEK_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the host variables.
EMAIL_BACKEND_PATH = os.environ.get(
    "GWIRIEK_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
_SMTP_OPTIONS = {
    "host": os.environ.get("GWIRIEK_EMAIL_HOST", "localhost"),
    "port": int(os.environ.get("GWIRIEK_EMAIL_PORT", "25")),
    "username": os.environ.get("GWIRIEK_EMAIL_HOST_USER", ""),
    "password": os.environ.get("GWIRIEK_EMAIL_HOST_PASSWORD", ""),
    "use_tls": _env_bool("GWIRIEK_EMAIL_USE_TLS"),
    "use_ssl": _env_bool("GWIRIEK_EMAIL_USE_SSL"),
}
MAILERS = {
    "default": {
        "BACKEND": EMAIL_BACKEND_PATH,
        "OPTIONS": _SMTP_OPTIONS if EMAIL_BACKEND_PATH.endswith(".smtp.EmailBackend") else {},
    }
}
DEFAULT_FROM_EMAIL = os.environ.get("GWIRIEK_DEFAULT_FROM_EMAIL", "gwiriek@localhost")

# Addresses that receive the digest of due items (gwiriek notify).
GWIRIEK_ALERT_RECIPIENTS = _env_list("GWIRIEK_ALERT_RECIPIENTS")
