from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from elections.models import Election, User, Vote


class Command(BaseCommand):
    help = ("Delete the TEST election and everything else in the database (voters, votes, committee logins, "
            "email outbox, audit log) so the real election starts from empty. Refuses a non-TEST election.")

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="", help="Type WIPE to confirm")

    def handle(self, *args, **options):
        election = Election.objects.first()
        if election and not election.name.startswith("TEST"):
            raise CommandError(f"Refusing: “{election.name}” is not a TEST election. Real data is untouched.")
        if options["confirm"] != "WIPE":
            raise CommandError("This deletes every account, vote and log. Re-run with --confirm WIPE.")
        counts = f"{User.objects.count()} accounts, {Vote.objects.count()} votes"
        # TRUNCATE bypasses the row-level guards that keep votes and audit records immutable.
        call_command("flush", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS(f"Wiped the TEST election ({counts}). The database is empty; "
                                             "create committee accounts with create_committee next."))
