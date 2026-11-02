import uuid

from django.core.management.base import BaseCommand

from elections.mail import deliver


class Command(BaseCommand):
    help = "Send one test email through the configured transport to check inbox placement."

    def add_arguments(self, parser):
        parser.add_argument("address")

    def handle(self, *args, **options):
        provider_id = deliver({
            "to": options["address"],
            "subject": "GEA ITB — Uji email pemilu / Election email test",
            "text": ("Ini email uji dari sistem Pemilu HMTG \"GEA\" ITB. Tidak perlu dibalas.\n\n"
                     "This is a test email from the HMTG \"GEA\" ITB election system. No reply needed.\n"),
        }, f"test-{uuid.uuid4()}")
        self.stdout.write(f"Sent. Provider id: {provider_id}")
