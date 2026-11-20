from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from elections.candidate_roster import ROSTER
from elections.models import Candidate, Election, Office
from elections.services import audit


class Command(BaseCommand):
    help = "Apply the supplied BPH and senator candidates to an existing draft. Never creates or opens an election."

    @transaction.atomic
    def handle(self, *args, **options):
        election = Election.objects.select_for_update().first()
        if not election or election.activated_at:
            raise CommandError("An existing, unactivated draft election is required. Nothing was changed.")
        for entry in ROSTER:
            office, _ = Office.objects.get_or_create(election=election, slug=entry["slug"])
            candidate, _ = Candidate.objects.get_or_create(office=office, number=entry["number"], defaults={
                "name": entry["name"], "profile_id": "", "profile_en": "",
            })
            if candidate.name != entry["name"]:
                candidate.profile_id = ""
                candidate.profile_en = ""
            candidate.name = entry["name"]
            candidate.nim = entry["nim"]
            candidate.is_blank = entry.get("is_blank", False)
            if candidate.is_blank:
                candidate.profile_id = "Pilih kotak kosong jika tidak memilih calon senator."
                candidate.profile_en = "Choose the empty box if you do not choose the senator candidate."
            candidate.full_clean()
            candidate.save()
        audit(None, "configuration.candidate_roster_applied")
        self.stdout.write("Configured Rafael and Dzaki for BPH; Juni and the empty box for senator. Schedule unchanged.")
