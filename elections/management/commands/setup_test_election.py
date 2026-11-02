import secrets
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from elections.models import Candidate, Election, Office, User, Voter, VotingWindow
from elections.services import audit, sync_envelope

from .create_committee import COMMITTEE_PERMISSIONS


class Command(BaseCommand):
    help = ("Create an open TEST election with test voters and committee logins on an empty election "
            "database. Remove it all with wipe_test_election before setting up the real election.")

    def add_arguments(self, parser):
        parser.add_argument("--voters", type=int, default=5)
        parser.add_argument("--committee", type=int, default=5)
        parser.add_argument("--hours", type=int, default=72, help="Length of the open test voting session")
        parser.add_argument("--email", help="Send voter credentials by the real email flow to plus-addresses "
                                            "of this inbox (e.g. you+voter1@gmail.com) instead of printing them")

    @transaction.atomic
    def handle(self, *args, **options):
        if Election.objects.exists():
            raise CommandError("An election already exists. Run wipe_test_election first; real data is untouched.")
        if not (1 <= options["voters"] <= 50 and 1 <= options["committee"] <= 10 and options["hours"] >= 1):
            raise CommandError("Choose 1–50 voters, 1–10 committee logins and at least 1 hour.")
        now = timezone.now()
        election = Election.objects.create(pk=1, name="TEST — Pemilu HMTG GEA ITB")
        VotingWindow.objects.create(election=election, starts_at=now - timedelta(minutes=1),
                                    ends_at=now + timedelta(hours=options["hours"]))
        sync_envelope(election)
        for slug in ["kahim", "senator"]:
            office = Office.objects.create(election=election, slug=slug)
            for n in [1, 2]:
                Candidate.objects.create(office=office, number=n, name=f"Calon Uji {office} {n}",
                    profile_id="Profil calon uji coba.", profile_en="Test candidate profile.")

        self.stdout.write("Voters" + (" (credentials will be emailed)" if options["email"] else "") + ":")
        local, _, domain = (options["email"] or "").lower().partition("@")
        for n in range(1, options["voters"] + 1):
            nim = str(99000000 + n)
            if options["email"]:
                user = User(username=nim, email=f"{local}+voter{n}@{domain}")
                user.set_unusable_password()
                user.save()
                Voter.objects.create(election=election, user=user)
                self.stdout.write(f"  NIM {nim}  email {user.email}")
            else:
                password = secrets.token_urlsafe(9)
                user = User.objects.create_user(username=nim, email=f"{nim}@test.invalid", password=password)
                Voter.objects.create(election=election, user=user, credentials_issued_at=now)
                self.stdout.write(f"  NIM {nim}  password {password}")

        self.stdout.write("\nCommittee (log in at /committee/admin/login/; as the device choose "
                          "\"Recovery codes\" and type one code, or add the authenticator secret to an app):")
        permissions = Permission.objects.filter(content_type__app_label="elections",
                                                codename__in=[*COMMITTEE_PERMISSIONS, "review_ballots"])
        for n in range(1, options["committee"] + 1):
            username, password = f"test-admin-{n}", secrets.token_urlsafe(12)
            user = User.objects.create_user(username=username, email=f"{username}@test.invalid",
                                            password=password, is_staff=True)
            user.user_permissions.set(permissions)
            totp = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=True)
            recovery = StaticDevice.objects.create(user=user, name="Recovery codes")
            codes = [secrets.token_hex(4) for _ in range(5)]
            for code in codes:
                StaticToken.objects.create(device=recovery, token=code)
            self.stdout.write(f"  username {username}  password {password}\n"
                              f"    authenticator secret: {totp.config_url.split('secret=')[1].split('&')[0]}\n"
                              f"    single-use recovery codes: {' '.join(codes)}")

        election.activated_at = now
        election.save(update_fields=["activated_at"])
        audit(None, "test_election.created", voters=options["voters"], committee=options["committee"])
        if options["email"]:
            self.stdout.write("\nTo email the voter passwords: log in to the committee page and click "
                              "\"Queue credentials\". The outbox cron sends them within a few minutes.")
        self.stdout.write(self.style.SUCCESS("\nTEST election is open. Wipe it with "
                                             "`python manage.py wipe_test_election --confirm WIPE` before the real setup."))
