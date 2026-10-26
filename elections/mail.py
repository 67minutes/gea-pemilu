import json
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.db import transaction
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


def process_one():
    """Row lock covers the provider call; a killed worker reuses the same provider key.

    The provider call has a short timeout. A maximum 23h retry horizon is shorter than
    Resend's 24h idempotency retention. The payload never changes during retries.
    """
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update(skip_locked=True).filter(
            status="pending", next_attempt_at__lte=timezone.now()).order_by("created_at").first()
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
            if settings.EMAIL_TRANSPORT in {"file", "smtp"}:
                if not settings.DEBUG:
                    raise ValueError("Development email transports are disabled")
                backend = SMTPBackend() if settings.EMAIL_TRANSPORT == "smtp" else None
                EmailMessage(payload["subject"], payload["text"], settings.DEFAULT_FROM_EMAIL,
                             [payload["to"]], connection=backend).send(fail_silently=False)
                delivery.provider_id = f"local-{delivery.pk}"
            elif settings.EMAIL_TRANSPORT == "resend":
                response = httpx.post("https://api.resend.com/emails", timeout=10,
                                      headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}",
                                               "Idempotency-Key": f"gea/{delivery.pk}"},
                                      json={"from": settings.DEFAULT_FROM_EMAIL, **payload})
                response.raise_for_status()
                delivery.provider_id = response.json()["id"]
            else:
                raise ValueError("Unknown email transport")
            delivery.status = "sent"
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
