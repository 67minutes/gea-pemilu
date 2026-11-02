from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from elections.models import Candidate, Election, Office, User, Voter, VotingWindow
from elections.services import sync_envelope


@pytest.fixture(autouse=True)
def test_settings(settings):
    settings.DEBUG = True
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.OUTBOX_KEY = Fernet.generate_key().decode()
    settings.EMAIL_TRANSPORT = "file"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.KIOSK_REQUIRED = False  # kiosk tests turn enrollment back on


@pytest.fixture
def make_election():
    def make(*, closes_in=timedelta(hours=2), active=True, voter_count=1, name='Pemilu HMTG "GEA" ITB'):
        closes = timezone.now() + closes_in
        election = Election.objects.create(name=name)
        VotingWindow.objects.create(election=election, starts_at=closes - timedelta(hours=72), ends_at=closes)
        sync_envelope(election)
        offices, candidates, voters = {}, {}, []
        for slug in ["kahim", "senator"]:
            offices[slug] = Office.objects.create(election=election, slug=slug)
            candidates[slug] = [Candidate.objects.create(office=offices[slug], number=n,
                name=f"{slug.title()} Candidate {n}", profile_id=f"Profil {n}", profile_en=f"Profile {n}") for n in [1, 2]]
        for index in range(voter_count):
            user = User.objects.create_user(username=str(12000001 + index), password="Example-password-123!",
                                             email=f"{12000001 + index}@students.itb.ac.id")
            voters.append(Voter.objects.create(election=election, user=user))
        if active:
            election.activated_at = timezone.now()
            election.save(update_fields=["activated_at"])
        return election, offices, candidates, voters
    return make


@pytest.fixture
def staff_client():
    """Committee member with a verified OTP session: (user, client, device)."""
    def make(*, review=False, operate=True):
        user = User.objects.create_user(username="committee", email="committee@itb.ac.id",
                                        password="Committee-password-123!", is_staff=True)
        codes = (["operate_election"] if operate else []) + (["review_ballots"] if review else [])
        user.user_permissions.set(Permission.objects.filter(codename__in=codes))
        device = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=True)
        client = Client()
        client.force_login(user)
        session = client.session
        session["otp_device_id"] = device.persistent_id
        session.save()
        return user, client, device
    return make
