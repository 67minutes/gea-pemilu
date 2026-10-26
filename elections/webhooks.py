from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from standardwebhooks.webhooks import Webhook, WebhookVerificationError

from .models import EmailDelivery


@csrf_exempt
@require_POST
def resend_events(request):
    if not settings.RESEND_WEBHOOK_SECRET or len(request.body) > 65536:
        return HttpResponse(status=400)
    try:
        # Resend uses Svix header names; Standard Webhooks uses webhook-* names.
        secret = settings.RESEND_WEBHOOK_SECRET.removeprefix("whsec_")
        event = Webhook(secret).verify(request.body, {
            "webhook-id": request.headers.get("svix-id", ""),
            "webhook-timestamp": request.headers.get("svix-timestamp", ""),
            "webhook-signature": request.headers.get("svix-signature", ""),
        })
    except (WebhookVerificationError, ValueError, TypeError, UnicodeDecodeError):
        return HttpResponse(status=400)
    if not isinstance(event, dict):
        return HttpResponse(status=400)
    kind = event.get("type")
    if kind not in {"email.delivered", "email.bounced", "email.complained", "email.failed"}:
        return HttpResponse(status=204)
    with transaction.atomic():
        delivery = EmailDelivery.objects.select_for_update().filter(
            provider_id=event.get("data", {}).get("email_id", ""), status__in=["sent", "failed"],
        ).exclude(provider_id="").first()
        if not delivery:
            # Provider may reach us before the sender commits; permit its retry.
            return HttpResponse(status=503)
        if kind == "email.delivered" and not delivery.delivered_at:
            delivery.delivered_at = timezone.now()
        elif kind in {"email.bounced", "email.complained", "email.failed"}:
            delivery.bounced_at = delivery.bounced_at or timezone.now()
            delivery.status = "failed"
            delivery.last_error = kind
        delivery.save(update_fields=["delivered_at", "bounced_at", "status", "last_error"])
    return HttpResponse(status=204)
