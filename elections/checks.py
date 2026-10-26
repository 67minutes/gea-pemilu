from urllib.parse import urlparse

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def production_configuration(app_configs, **kwargs):
    errors = []
    if settings.SECRET_KEY_SOURCE.startswith(("development-only", "replace-", "change-")) or len(settings.SECRET_KEY_SOURCE) < 32:
        errors.append(Error("Set a fresh random SECRET_KEY of at least 256 bits.", id="gea.E007"))
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        errors.append(Error("Production requires PostgreSQL.", id="gea.E001"))
    try:
        Fernet(settings.OUTBOX_KEY.encode())
    except (ValueError, TypeError):
        errors.append(Error("Set a valid OUTBOX_KEY shared by web and cron.", id="gea.E002"))
    if settings.EMAIL_TRANSPORT != "resend" or not settings.RESEND_API_KEY:
        errors.append(Error("Production requires configured Resend delivery.", id="gea.E003"))
    if urlparse(settings.PUBLIC_BASE_URL).scheme != "https":
        errors.append(Error("PUBLIC_BASE_URL must use HTTPS.", id="gea.E004"))
    if "example.invalid" in settings.DEFAULT_FROM_EMAIL:
        errors.append(Error("Set a verified sending-domain address.", id="gea.E005"))
    if not settings.RESEND_WEBHOOK_SECRET:
        errors.append(Error("Configure the Resend webhook signing secret to track delivery failures.", id="gea.E006"))
    return errors
