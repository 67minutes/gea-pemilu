import getpass
import secrets
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from elections.models import User
from elections.services import audit


class Command(BaseCommand):
    help = "Provision a separate committee account and authenticator/recovery codes interactively."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("email")
        parser.add_argument("--review-ballots", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("Username already exists. Use a separate committee identity.")
        password = getpass.getpass("Committee password (12+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise CommandError("Passwords do not match.")
        user = User(username=options["username"], email=options["email"], is_staff=True)
        user.full_clean(exclude=["password"])
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        user.set_password(password)
        user.save()
        codes = ["operate_election", "view_election", "add_election", "change_election",
                 "view_office", "add_office", "change_office", "view_candidate", "add_candidate",
                 "change_candidate", "view_voter", "view_emaildelivery", "view_auditevent"]
        if options["review_ballots"]:
            codes.append("review_ballots")
        user.user_permissions.set(Permission.objects.filter(content_type__app_label="elections", codename__in=codes))
        device = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=False)
        self.stdout.write("Add this private provisioning URI to your authenticator. Do not share it:")
        self.stdout.write(device.config_url)
        self.stdout.write("Manual-entry secret: " + parse_qs(urlparse(device.config_url).query)["secret"][0])
        token = getpass.getpass("Enter the six-digit authenticator code to finish enrollment: ")
        if not device.verify_token(token):
            raise CommandError("Invalid authenticator code. No account was created; retry provisioning.")
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        recovery = StaticDevice.objects.create(user=user, name="Recovery codes")
        self.stdout.write("Store these single-use recovery codes offline:")
        for _ in range(8):
            value = secrets.token_hex(8)
            StaticToken.objects.create(device=recovery, token=value)
            self.stdout.write(value)
        audit(user, "committee.provisioned", ballot_reviewer=options["review_ballots"])
        self.stdout.write(self.style.SUCCESS("Account created. Sign in at /committee/admin/login/."))
