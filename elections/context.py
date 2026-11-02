from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _


def site_context(request):
    return {"site_name": 'HMTG “GEA” ITB', "now": timezone.now(), "kiosk_idle_seconds": settings.KIOSK_IDLE_SECONDS,
            "privacy_notice": _(
        "Your choices are linked to your NIM. Authorized committee reviewers can review them after closing. "
        "Keep your password private. Each submitted vote is final.")}
