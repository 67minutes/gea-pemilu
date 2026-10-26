import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from elections.mail import process_one
from elections.models import Election, ThrottleBucket
from elections.services import cleanup_expired_payloads, issue_credentials


class Command(BaseCommand):
    help = "Send up to 50 queued emails (under one minute at <=2 requests/second)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        cleanup_expired_payloads()
        ThrottleBucket.objects.filter(window__lt=int(time.time()) // settings.THROTTLE_WINDOW_SECONDS - 12).delete()
        if Election.objects.filter(credentials_requested_at__isnull=False, activated_at__isnull=False,
                                   closes_at__gt=timezone.now()).exists():
            issue_credentials(None, limit=10)
        count = 0
        for _ in range(max(0, options["limit"])):
            if not process_one():
                break
            count += 1
            time.sleep(0.55)
        self.stdout.write(f"Processed {count} email jobs. See delivery records for outcomes.")
