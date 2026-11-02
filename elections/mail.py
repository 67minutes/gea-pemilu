import json
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.db import transaction
from django.db.models import Case, When
from django.utils import timezone

from .models import EmailDelivery


def cipher():
    if not settings.OUTBOX_KEY:
        raise ValueError("OUTBOX_KEY is required. Generate one with Fernet.generate_key().")
    return Fernet(settings.OUTBOX_KEY.encode())


def enqueue(user, kind, subject, body):
    payload = {"to": user.email, "subject": subject, "text": body}
    return EmailDelivery.objects.create(user=user, kind=kind,
                                        encrypted_payload=cipher().encrypt(json.dumps(payload).encode()).decode())


def remaining_daily_budget():
    """Sends left in the rolling 24-hour window, or None when no daily limit is configured."""
    if not settings.EMAIL_DAILY_LIMIT:
        return None
    sent = EmailDelivery.objects.filter(sent_at__gte=timezone.now() - timedelta(hours=24)).count()
    return max(0, settings.EMAIL_DAILY_LIMIT - sent)


def deliver(payload, key):
    """Send one payload through the configured transport and return the provider id."""
    if settings.EMAIL_TRANSPORT in {"file", "smtp"}:
        if not settings.DEBUG:
            raise ValueError("Development email transports are disabled")
        backend = SMTPBackend() if settings.EMAIL_TRANSPORT == "smtp" else None
        EmailMessage(payload["subject"], payload["text"], settings.DEFAULT_FROM_EMAIL,
                     [payload["to"]], connection=backend).send(fail_silently=False)
        return f"local-{key}"
    if settings.EMAIL_TRANSPORT == "gmail":
        backend = SMTPBackend(host="smtp.gmail.com", port=587, use_tls=True, username=settings.EMAIL_HOST_USER,
                              password=settings.EMAIL_HOST_PASSWORD, timeout=settings.EMAIL_TIMEOUT)
        EmailMessage(payload["subject"], payload["text"], settings.DEFAULT_FROM_EMAIL,
                     [payload["to"]], connection=backend).send(fail_silently=False)
        return f"gmail-{key}"
    if settings.EMAIL_TRANSPORT == "resend":
        response = httpx.post("https://api.resend.com/emails", timeout=10,
                              headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}",
                                       "Idempotency-Key": f"gea/{key}"},
                              json={"from": settings.DEFAULT_FROM_EMAIL, **payload})
        response.raise_for_status()
        return response.json()["id"]
    raise ValueError("Unknown email transport")


def process_one():
    """Row lock covers the provider call; a killed worker reuses the same provider key.

    The provider call has a short timeout. A maximum 23h retry horizon is shorter than
    Resend's 24h idempotency retention. The payload never changes during retries.
    Password resets go before credentials, and a spent daily budget leaves jobs pending.
    """
    if remaining_daily_budget() == 0:
        return False
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update(skip_locked=True).filter(
            status="pending", next_attempt_at__lte=timezone.now()).order_by(
            Case(When(kind="reset", then=0), default=1), "created_at").first()
        if not delivery:
            return False
        if delivery.created_at < timezone.now() - timedelta(hours=23):
            delivery.status = "failed"
            delivery.last_error = "Expired; initiate a password reset"
            delivery.encrypted_payload = ""
            delivery.save()
            return True
        delivery.attempts += 1
        try:
            payload = json.loads(cipher().decrypt(delivery.encrypted_payload.encode()))
            delivery.provider_id = deliver(payload, delivery.pk)
            delivery.status = "sent"
            delivery.sent_at = timezone.now()
            delivery.encrypted_payload = ""
            delivery.last_error = ""
        except Exception as exc:
            # Never log exception bodies: provider errors can echo credentials.
            delivery.last_error = type(exc).__name__
            if delivery.attempts >= 6:
                delivery.status = "failed"
                delivery.encrypted_payload = ""
            else:
                delivery.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 30 * 2**delivery.attempts))
        delivery.save()
        return True
