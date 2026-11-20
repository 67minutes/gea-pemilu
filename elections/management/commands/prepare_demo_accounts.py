import json
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from elections.models import User
from elections.services import audit


class Command(BaseCommand):
    help = "Prepare 21 inactive demo accounts without creating an election, changing a roster, or sending email."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="output/prepared-demo-accounts.json")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["output"])
        usernames = [str(99000101 + n) for n in range(21)]
        if User.objects.filter(username__in=usernames).exists():
            raise CommandError("Prepared demo usernames already exist; existing passwords were not changed.")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an earlier private credential file.
        try:
            stream = path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise CommandError("The output file already exists. It was not overwritten.") from exc
        try:
            credentials = []
            for nim in usernames:
                password = secrets.token_urlsafe(18)
                User.objects.create_user(username=nim, email=f"{nim}@demo.invalid", password=password,
                                         is_active=False)
                credentials.append({"nim": nim, "password": password})
            audit(None, "demo_accounts.prepared", count=len(credentials))
            with stream:
                json.dump({"status": "inactive; not enrolled in any election", "accounts": credentials},
                          stream, indent=2)
        except Exception:
            stream.close()
            path.unlink(missing_ok=True)
            raise
        self.stdout.write(f"Prepared 21 inactive demo accounts. Private credentials: {path.resolve()}")
