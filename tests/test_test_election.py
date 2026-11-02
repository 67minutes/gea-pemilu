import re
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice

from elections.models import Election, EmailDelivery, User, Vote, Voter
from elections.services import issue_credentials, submit_ballot

pytestmark = pytest.mark.django_db


def setup(*args):
    out = StringIO()
    call_command("setup_test_election", *args, stdout=out)
    return out.getvalue()


def test_setup_creates_open_test_election_with_voters_and_committee(settings):
    settings.DEBUG = False
    output = setup()
    election = Election.objects.get()
    assert election.pk == 1 and election.name.startswith("TEST") and election.state == "open"
    assert Voter.objects.count() == 5 and User.objects.filter(is_staff=True).count() == 5
    voters = re.findall(r"NIM (\d{8})\s+password (\S+)", output)
    assert len(voters) == 5
    nim, password = voters[0]
    voter = Client()
    assert voter.post(reverse("login"), {"username": nim, "password": password}).url == reverse("dashboard")
    assert voter.get(reverse("ballot")).status_code == 200


def test_committee_test_login_works_with_a_printed_recovery_code():
    output = setup()
    username, password = re.findall(r"username (test-admin-\d)\s+password (\S+)", output)[0]
    code = re.search(rf"{username}.*?recovery codes: (\w+)", output, re.S).group(1)
    user = User.objects.get(username=username)
    assert user.has_perm("elections.operate_election")
    device = StaticDevice.objects.get(user=user)
    client = Client()
    response = client.post(reverse("committee_admin:login"), {
        "username": username, "password": password, "otp_device": device.persistent_id, "otp_token": code})
    assert response.status_code == 302 and client.get(reverse("committee")).status_code == 200


def test_setup_with_email_sends_credentials_by_the_real_flow():
    output = setup("--email", "Panitia.Test@gmail.com")
    assert "password" not in output.split("Committee")[0]
    emails = sorted(Voter.objects.values_list("user__email", flat=True))
    assert emails == [f"panitia.test+voter{n}@gmail.com" for n in range(1, 6)]
    issue_credentials(None)
    assert EmailDelivery.objects.filter(kind="credentials").count() == 5


def test_setup_refuses_when_an_election_exists(make_election):
    make_election()
    with pytest.raises(CommandError):
        setup()


@pytest.mark.django_db(transaction=True)  # as in production: the command runs outside a transaction
def test_wipe_removes_test_election_including_votes():
    setup()
    voter = Voter.objects.first()
    submit_ballot(voter.user, {o.slug: o.candidates.first().pk for o in Election.objects.get().offices.all()},
                  uuid.uuid4())
    with pytest.raises(CommandError):
        call_command("wipe_test_election", stdout=StringIO())  # confirmation required
    call_command("wipe_test_election", "--confirm", "WIPE", stdout=StringIO())
    assert not Election.objects.exists() and not Vote.objects.exists() and not User.objects.exists()
    setup()  # a fresh election gets pk 1 again
    assert Election.objects.get().pk == 1


def test_wipe_refuses_a_real_activated_election(make_election):
    make_election()
    with pytest.raises(CommandError, match="TEST"):
        call_command("wipe_test_election", "--confirm", "WIPE", stdout=StringIO())
    assert Election.objects.exists()
