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


def bucket_key(value):
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def current_window():
    return int(time.time()) // settings.THROTTLE_WINDOW_SECONDS


@transaction.atomic
def consume_bucket(value, limit):
    bucket, _created = ThrottleBucket.objects.select_for_update().get_or_create(key=bucket_key(value),
                                                                                defaults={"window": 0})
    window = current_window()
    if bucket.window != window:
        bucket.window, bucket.count = window, 0
    bucket.count += 1
    bucket.save(update_fields=["window", "count"])
    return bucket.count <= limit


def bucket_count(value):
    bucket = ThrottleBucket.objects.filter(key=bucket_key(value), window=current_window()).first()
    return bucket.count if bucket else 0


def clear_bucket(value):
    ThrottleBucket.objects.filter(key=bucket_key(value)).delete()


def client_ip(request):
    # Behind Render's TLS proxy, REMOTE_ADDR is the proxy for every client. The proxy appends the
    # address it saw to X-Forwarded-For, so read that entry, counting back over trusted proxy hops.
    # Entries further left are client-supplied and never used. Without a trusted proxy, use REMOTE_ADDR.
    if settings.TRUST_PROXY:
        hops = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        if len(hops) >= settings.TRUSTED_PROXY_HOPS:
            return hops[-settings.TRUSTED_PROXY_HOPS]
    return request.META.get("REMOTE_ADDR", "unknown")


class AuthenticationThrottleMiddleware:
    # Logins count only failures, per account and client address, and a success clears them, so a
    # stranger cannot lock a voter out. Password reset counts every request: it sends email.
    login_paths = {"/login/", "/committee/admin/login/"}
    paths = login_paths | {"/password-reset/"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST" or request.path not in self.paths:
            return self.get_response(request)
        identity = (request.POST.get("username") or request.POST.get("email") or "").strip().lower()[:254]
        ip = client_ip(request)
        if not consume_bucket(f"ip:{ip}", settings.THROTTLE_IP_LIMIT):
            return self.too_many()
        if request.path not in self.login_paths:
            if not consume_bucket(f"account:{identity}", settings.THROTTLE_ACCOUNT_LIMIT):
                return self.too_many()
            return self.get_response(request)
        failures = f"failed:{identity}:{ip}"
        if bucket_count(failures) >= settings.THROTTLE_ACCOUNT_LIMIT:
            return self.too_many()
        response = self.get_response(request)
        # Both login views redirect on success and re-render the form (200) on failure.
        if response.status_code == 302:
            clear_bucket(failures)
        elif response.status_code == 200:
            consume_bucket(failures, settings.THROTTLE_ACCOUNT_LIMIT)
        return response

    @staticmethod
    def too_many():
        return HttpResponse(_("Too many attempts. Try again in five minutes."), status=429,
                            headers={"Retry-After": "300"})


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
