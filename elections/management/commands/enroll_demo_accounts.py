from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from elections.models import Election, User, Voter
from elections.services import audit


class Command(BaseCommand):
    help = "Enroll and enable the 21 prepared accounts in an existing draft DEMO/TEST election. Never opens voting."

    @transaction.atomic
    def handle(self, *args, **options):
        election = Election.objects.select_for_update().first()
        if not election or election.activated_at or not election.name.startswith(("DEMO", "TEST")):
            raise CommandError("An existing, unactivated DEMO/TEST election is required. Nothing was changed.")
        usernames = [str(99000101 + n) for n in range(21)]
        users = list(User.objects.select_for_update().filter(username__in=usernames).order_by("username"))
        if len(users) != 21 or any(
            user.is_active or user.is_staff or user.is_superuser or user.email != f"{user.username}@demo.invalid"
            or hasattr(user, "voter") for user in users
        ):
            raise CommandError("All 21 prepared, inactive and unenrolled demo accounts are required.")
        for user in users:
            Voter.objects.create(election=election, user=user, credentials_issued_at=timezone.now())
            user.is_active = True
            user.save(update_fields=["is_active"])
        audit(None, "demo_accounts.enrolled", count=len(users))
        self.stdout.write("Enrolled 21 demo voters. Existing passwords retained; election remains a draft.")
