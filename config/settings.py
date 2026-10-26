import hashlib
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
SECRET_KEY_SOURCE = os.getenv("SECRET_KEY", "development-only-change-this-key-before-deploying")
# Render generates 256-bit base64 secrets (44 chars). Derive a stable 64-char
# Django key without discarding entropy; validate the source separately at release.
SECRET_KEY = hashlib.sha256(("gea-pemilu:" + SECRET_KEY_SOURCE).encode()).hexdigest()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv("CSRF_TRUSTED_ORIGINS", PUBLIC_BASE_URL).split(",") if x]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django_otp", "django_otp.plugins.otp_totp", "django_otp.plugins.otp_static", "elections",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "elections.middleware.RequestMetricsMiddleware", "elections.middleware.AuthenticationThrottleMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "elections.context.site_context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": dj_database_url.config(
    default=f"sqlite:///{BASE_DIR / 'development.sqlite3'}", conn_max_age=60, conn_health_checks=True,
)}
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES["default"].setdefault("OPTIONS", {}).update({"connect_timeout": 5})
AUTH_USER_MODEL = "elections.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "id"
LANGUAGES = [("id", "Bahasa Indonesia"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 3600
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^health/(live|ready)/$"]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
# Proxies in front of the app that append to X-Forwarded-For (Render's edge proxy is one).
TRUSTED_PROXY_HOPS = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
FILE_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
ITB_EMAIL_DOMAINS = [s.strip().lower() for s in os.getenv("ITB_EMAIL_DOMAINS", "mahasiswa.itb.ac.id,students.itb.ac.id,itb.ac.id").split(",") if s.strip()]
OUTBOX_KEY = os.getenv("OUTBOX_KEY", "")
EMAIL_TRANSPORT = os.getenv("EMAIL_TRANSPORT", "file" if DEBUG else "resend")
EMAIL_FILE_PATH = BASE_DIR / "output" / "mail"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "GEA Pemilu <pemilu@example.invalid>")
EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "1025"))
EMAIL_TIMEOUT = 10
PASSWORD_RESET_TIMEOUT = 3600
THROTTLE_ACCOUNT_LIMIT = int(os.getenv("THROTTLE_ACCOUNT_LIMIT", "10"))
# Failed logins for one account across all addresses; bounds distributed guessing.
THROTTLE_ACCOUNT_WIDE_LIMIT = int(os.getenv("THROTTLE_ACCOUNT_WIDE_LIMIT", "50"))
# Campus users can share one public IP; account limits do the primary protection.
THROTTLE_IP_LIMIT = int(os.getenv("THROTTLE_IP_LIMIT", "2000"))
THROTTLE_WINDOW_SECONDS = 300
LOGGING = {"version": 1, "disable_existing_loggers": False,
"filters": {"redact_paths": {"()": "elections.logging.RedactSensitivePaths"}}, "handlers": {
    "console": {"class": "logging.StreamHandler", "filters": ["redact_paths"]},
}, "root": {"handlers": ["console"], "level": "INFO"}, "loggers": {
    "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
