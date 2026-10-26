import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils.translation import gettext as _

from .models import ThrottleBucket

logger = logging.getLogger("gea.requests")


@transaction.atomic
def consume_bucket(value, limit):
    key = hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    bucket, _created = ThrottleBucket.objects.select_for_update().get_or_create(key=key, defaults={"window": 0})
    window = int(time.time()) // settings.THROTTLE_WINDOW_SECONDS
    if bucket.window != window:
        bucket.window, bucket.count = window, 0
    bucket.count += 1
    bucket.save(update_fields=["window", "count"])
    return bucket.count <= limit


class AuthenticationThrottleMiddleware:
    paths = {"/login/", "/committee/admin/login/", "/password-reset/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path in self.paths:
            identity = (request.POST.get("username") or request.POST.get("email") or "").strip().lower()[:254]
            # Do not trust client-supplied X-Forwarded-For. The canonical IP header
            # must be overwritten by a trusted proxy; REMOTE_ADDR is the safe default.
            ip = request.META.get("REMOTE_ADDR", "unknown")
            allowed_ip = consume_bucket(f"ip:{ip}", settings.THROTTLE_IP_LIMIT)
            allowed = allowed_ip and consume_bucket(f"account:{identity}", settings.THROTTLE_ACCOUNT_LIMIT)
            if not allowed or not allowed_ip:
                return HttpResponse(_("Too many attempts. Try again in five minutes."), status=429,
                                    headers={"Retry-After": "300"})
        return self.get_response(request)


class RequestMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        # Route pattern excludes password-reset tokens and receipt IDs; no query/body/user data.
        route = getattr(getattr(request, "resolver_match", None), "route", "unmatched")
        logger.info("request method=%s route=%s status=%s duration_ms=%.1f", request.method, route,
                    response.status_code, 1000 * (time.perf_counter() - started))
        if request.path not in {"/", "/results/", "/health/live/", "/health/ready/"}:
            response["Cache-Control"] = "no-store, private"
        return response
