import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from elections.models import Election, Vote


class Command(BaseCommand):
    help = "Reconcile k6's acknowledged receipts with a synthetic DEMO database."

    def add_arguments(self, parser):
        parser.add_argument("receipts_file")
        parser.add_argument("--expected-voters", type=int, default=500)

    def handle(self, *args, **options):
        election = Election.objects.first()
        if not settings.DEBUG or not election or not election.name.startswith("DEMO"):
            raise CommandError("This command only inspects an isolated DEBUG=true DEMO election.")
        receipts = []
        for line in Path(options["receipts_file"]).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CommandError("Use k6 --log-format raw --console-output for the receipt file.") from exc
            if record.get("type") == "receipt":
                receipts.append(record)
        expected = options["expected_voters"] * 2
        if len(receipts) != expected or len({r["receipt_id"] for r in receipts}) != expected:
            raise CommandError(f"Expected exactly {expected} unique acknowledged receipts; got {len(receipts)}.")
        if Vote.objects.count() != expected:
            raise CommandError("Database vote count does not match acknowledged receipt count.")
        actual = {(str(v.receipt_id), v.voter.user.username, v.office.slug) for v in
                  Vote.objects.select_related("voter__user", "office")}
        acknowledged = {(r["receipt_id"], r["nim"], r["office"]) for r in receipts}
        if actual != acknowledged:
            raise CommandError("Acknowledged and stored votes differ. Do not open a production election.")
        self.stdout.write(self.style.SUCCESS(f"Reconciled all {expected} acknowledged votes; no losses or duplicates."))
