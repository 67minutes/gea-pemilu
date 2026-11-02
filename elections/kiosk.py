import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import KioskDevice, Voter

SEEN = "kiosk_seen"
COOKIE = "gea_kiosk"
SALT = "gea.kiosk"


def sign_device(device):
    return signing.dumps(str(device.pk), salt=SALT)


def current_device(request):
    """The enrolled, unrevoked kiosk this browser's signed cookie names, or None."""
    try:
        device_id = signing.loads(request.COOKIES.get(COOKIE, ""), salt=SALT, max_age=settings.KIOSK_COOKIE_AGE)
        return KioskDevice.objects.filter(pk=device_id, revoked_at__isnull=True).first()
    except (signing.BadSignature, ValueError, ValidationError):
        return None


class KioskMiddleware:
    """Voter sessions on shared committee laptops: end them when idle, off an enrolled laptop, or when
    a newer login for the same voter exists."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not user.is_staff:
            now = int(time.time())
            seen = request.session.get(SEEN)
            if seen is not None and now - seen > settings.KIOSK_IDLE_SECONDS:
                return self.end(request, _("Your session ended after a period of inactivity."), "kiosk-idle")
            recorded = Voter.objects.filter(user=user).values_list("session_key", flat=True).first()
            if recorded and recorded != request.session.session_key:
                return self.end(request, _("You signed in on another laptop, so this session ended."),
                                "kiosk-replaced")
            if settings.KIOSK_REQUIRED and not current_device(request):
                return self.end(request, _("This device is not a registered voting laptop."), "kiosk-device")
            request.session[SEEN] = now
        return self.get_response(request)

    @staticmethod
    def end(request, message, tag):
        logout(request)
        messages.info(request, message, extra_tags=tag)
        return redirect("login")


@login_required
@require_POST
def kiosk_ping(request):
    return HttpResponse(status=204)
