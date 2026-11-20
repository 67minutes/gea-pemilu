from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from elections.candidate_roster import ROSTER
from elections.models import Candidate, Election, Vote
from elections.services import audit


class Command(BaseCommand):
    help = "Replace candidates in a TEST election only when it has no votes or finalized results. Keeps its schedule."

    @transaction.atomic
    def handle(self, *args, **options):
        election = Election.objects.select_for_update().first()
        if not election or not election.name.startswith("TEST"):
            raise CommandError("Refusing: an existing TEST election is required.")
        if Vote.objects.exists() or election.result_snapshot is not None or election.finalized_at:
            raise CommandError("Refusing: votes or finalized results exist. Nothing was changed.")
        offices = {office.slug: office for office in election.offices.all()}
        if set(offices) != {"kahim", "senator"}:
            raise CommandError("Refusing: both existing offices are required.")
        old = list(Candidate.objects.filter(office__election=election).values())
        # The election row lock serializes this repair with ballot submission. DDL and
        # data changes roll back together on failure. No vote/audit guards are disabled.
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute("ALTER TABLE elections_candidate DISABLE TRIGGER gea_candidate_guard")
        Candidate.objects.filter(office__election=election).delete()
        for entry in ROSTER:
            candidate = Candidate(office=offices[entry["slug"]], number=entry["number"],
                                  name=entry["name"], nim=entry["nim"], is_blank=entry.get("is_blank", False),
                                  profile_id="", profile_en="")
            if candidate.is_blank:
                candidate.profile_id = "Pilih kotak kosong jika tidak memilih calon senator."
                candidate.profile_en = "Choose the empty box if you do not choose the senator candidate."
            candidate.full_clean()
            candidate.save()
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute("ALTER TABLE elections_candidate ENABLE TRIGGER gea_candidate_guard")
        # New primary keys reject stale ballots containing the old candidate IDs.
        audit(None, "test_candidates.repaired", previous_candidates=old)
        self.stdout.write("Repaired TEST candidates. Zero votes verified under lock; schedule and accounts unchanged.")
