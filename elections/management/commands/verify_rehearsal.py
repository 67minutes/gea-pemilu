import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from elections.models import Election, Vote


class Command(BaseCommand):
    help = "Reconcile k6's acknowledged ballots with a synthetic DEMO database."

    def add_arguments(self, parser):
        parser.add_argument("ballots_file")
        parser.add_argument("--expected-voters", type=int, default=500)

    def handle(self, *args, **options):
        election = Election.objects.first()
        if not settings.DEBUG or not election or not election.name.startswith("DEMO"):
            raise CommandError("This command only inspects an isolated DEBUG=true DEMO election.")
        nims = []
        for line in Path(options["ballots_file"]).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CommandError("Use k6 --log-format raw --console-output for the ballot file.") from exc
            if record.get("type") == "ballot":
                nims.append(record["nim"])
        voters = options["expected_voters"]
        if len(nims) != voters or len(set(nims)) != voters:
            raise CommandError(f"Expected exactly {voters} unique acknowledged ballots; got {len(nims)}.")
        offices = set(election.offices.values_list("slug", flat=True))
        if Vote.objects.count() != voters * len(offices):
            raise CommandError(f"Expected {voters * len(offices)} votes in the database; got {Vote.objects.count()}.")
        stored = {}
        for nim, slug in Vote.objects.values_list("voter__user__username", "office__slug"):
            stored.setdefault(nim, []).append(slug)
        if set(stored) != set(nims) or any(sorted(slugs) != sorted(offices) for slugs in stored.values()):
            raise CommandError("Acknowledged and stored ballots differ. Do not open a production election.")
        self.stdout.write(self.style.SUCCESS(
            f"Reconciled all {voters} acknowledged ballots ({voters * len(offices)} votes); no losses or duplicates."))
