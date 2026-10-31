import json
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from elections.models import Candidate, Election, Office, User, Voter


class Command(BaseCommand):
    help = "Create an isolated DEMO election and synthetic voters. Never run against live election data."

    def add_arguments(self, parser):
        parser.add_argument("--voters", type=int, default=5)
        parser.add_argument("--open", action="store_true", help="Open demo immediately, without the production activation gate")
        parser.add_argument("--output", default="output/demo-voters.json")

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo seeding requires DEBUG=true in an isolated development environment.")
        if Election.objects.exists() or User.objects.exists():
            raise CommandError("Demo seeding requires a completely empty database. Existing data is untouched.")
        if not 1 <= options["voters"] <= 10000:
            raise CommandError("Choose 1–10000 synthetic voters.")
        start = timezone.now() - timedelta(minutes=1) if options["open"] else timezone.now() + timedelta(days=1)
        election = Election.objects.create(name="DEMO — GEA ITB", opens_at=start, closes_at=start + timedelta(hours=72))
        for slug in ["kahim", "senator"]:
            office = Office.objects.create(election=election, slug=slug)
            for n in [1, 2]:
                Candidate.objects.create(office=office, number=n, name=f"Calon {office} {n}",
                    profile_id="Visi dan misi calon akan ditampilkan di sini.",
                    profile_en="The candidate's vision and mission will appear here.")
        credentials = []
        for index in range(options["voters"]):
            nim = str(99000000 + index)
            password = secrets.token_urlsafe(18)
            user = User.objects.create_user(username=nim, email=f"{nim}@students.itb.ac.id", password=password)
            Voter.objects.create(election=election, user=user, credentials_issued_at=timezone.now())
            credentials.append({"nim": nim, "password": password})
        if options["open"]:
            election.activated_at = timezone.now()
            election.save(update_fields=["activated_at"])
        path = Path(options["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
        self.stdout.write(f"Created {len(credentials)} DEMO voters. Private credentials: {path.resolve()}")
