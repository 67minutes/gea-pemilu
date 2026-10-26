import csv
import io
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from django.contrib.auth.models import Permission
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from standardwebhooks.webhooks import Webhook

from elections.mail import cipher, enqueue, process_one
from elections.models import AuditEvent, EmailDelivery, User, Vote, Voter
from elections.services import (
    ElectionError,
    activate,
    import_roster,
    issue_credentials,
    parse_roster,
    request_credentials,
    submit_vote,
)

pytestmark = pytest.mark.django_db


def staff_client(*, review=False):
    user = User.objects.create_user(username="committee", email="committee@itb.ac.id",
                                    password="Committee-password-123!", is_staff=True)
    codes = ["operate_election"] + (["review_ballots"] if review else [])
    user.user_permissions.set(Permission.objects.filter(codename__in=codes))
    device = TOTPDevice.objects.create(user=user, name="Authenticator", confirmed=True)
    client = Client()
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return user, client, device


@pytest.mark.parametrize("data", [
    "nim,email\n12000001,a@students.itb.ac.id\n12000001,b@students.itb.ac.id",
    "nim,email\n12000001,a@students.itb.ac.id\n12000002,A@students.itb.ac.id",
    "nim,email\n12000001,a@example.com",
    "nim,email\ninvalid,a@itb.ac.id",
    "name,email\nTest,a@itb.ac.id",
    "nim,email\n",
])
def test_roster_rejects_bad_input(data):
    with pytest.raises(ElectionError):
        parse_roster(data.encode())


def test_roster_preview_import_and_freeze(make_election):
    e, _o, _c, _v = make_election(active=False, closes_in=timedelta(days=4))
    data = b"\xef\xbb\xbfnim,email,display_name\n12000002,TEST@students.itb.ac.id,Test Voter\n"
    rows = parse_roster(data)
    assert rows[0]["email"] == "test@students.itb.ac.id"
    assert Voter.objects.count() == 1
    assert import_roster(None, rows) == 1
    assert not User.objects.get(username="12000002").has_usable_password()
    activate(None)
    with pytest.raises(ElectionError):
        import_roster(None, rows)
    e.refresh_from_db()
    assert e.activated_at


def test_roster_http_preview_does_not_import(make_election):
    make_election(active=False)
    _staff, client, _device = staff_client()
    from django.core.files.uploadedfile import SimpleUploadedFile

    file = SimpleUploadedFile("roster.csv", b"nim,email\n12000002,test@itb.ac.id\n", content_type="text/csv")
    response = client.post(reverse("roster_import"), {"file": file, "action": "preview"})
    assert response.status_code == 200 and Voter.objects.count() == 1
    assert client.post(reverse("roster_import"), {"action": "confirm"}).status_code == 302
    assert Voter.objects.count() == 2


def test_credentials_encrypted_once_and_hash_matches(make_election):
    _e, _o, _c, voters = make_election(voter_count=2)
    assert issue_credentials(None) == 2
    assert issue_credentials(None) == 0
    passwords = []
    for delivery in EmailDelivery.objects.all():
        payload = json.loads(cipher().decrypt(delivery.encrypted_payload.encode()))
        password = payload["text"].split("Password: ")[1].split("\n")[0]
        passwords.append(password)
        assert len(password) == 20
        assert password not in delivery.encrypted_payload
        delivery.user.refresh_from_db()
        assert delivery.user.check_password(password)
    assert len(set(passwords)) == 2
    assert voters[0].user.password not in passwords


def test_credential_request_does_not_hash_in_web_request(make_election):
    e, _o, _c, voters = make_election()
    original = voters[0].user.password
    request_credentials(None)
    e.refresh_from_db()
    voters[0].user.refresh_from_db()
    assert e.credentials_requested_at
    assert voters[0].user.password == original
    assert not EmailDelivery.objects.exists()
    from django.core.management import call_command

    with patch("elections.management.commands.process_outbox.time.sleep"):
        call_command("process_outbox", limit=10)
    assert EmailDelivery.objects.filter(status="sent").count() == 1


def test_outbox_sends_individually_and_purges_payload(make_election):
    _e, _o, _c, v = make_election()
    delivery = enqueue(v[0].user, "credentials", "Test", "Private content")
    assert process_one()
    delivery.refresh_from_db()
    assert delivery.status == "sent" and delivery.encrypted_payload == ""
    assert mail.outbox[0].to == [v[0].user.email]
    assert not process_one()


def test_provider_retry_uses_same_payload_and_key(make_election, settings):
    _e, _o, _c, v = make_election()
    settings.EMAIL_TRANSPORT = "resend"
    delivery = enqueue(v[0].user, "credentials", "Test", "Private password")
    request = httpx.Request("POST", "https://api.resend.com/emails")
    failed = httpx.Response(429, request=request)
    success = httpx.Response(200, json={"id": "provider-id"}, request=request)
    with patch("elections.mail.httpx.post", side_effect=[failed, success]) as provider:
        process_one()
        delivery.refresh_from_db()
        assert delivery.status == "pending" and delivery.encrypted_payload
        assert "Private" not in delivery.last_error
        EmailDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
        process_one()
        assert provider.call_args_list[0].kwargs["headers"]["Idempotency-Key"] == provider.call_args_list[1].kwargs["headers"]["Idempotency-Key"]
        assert provider.call_args_list[0].kwargs["json"] == provider.call_args_list[1].kwargs["json"]
    delivery.refresh_from_db()
    assert delivery.status == "sent" and delivery.encrypted_payload == ""


def test_expired_jobs_do_not_resend(make_election):
    _e, _o, _c, v = make_election()
    d = enqueue(v[0].user, "credentials", "Test", "secret")
    EmailDelivery.objects.filter(pk=d.pk).update(created_at=timezone.now() - timedelta(hours=24))
    with patch("elections.mail.httpx.post") as provider:
        process_one()
        assert not provider.called
    d.refresh_from_db()
    assert d.status == "failed" and d.encrypted_payload == ""


def test_reset_only_approved_addresses_and_preserves_votes(client, make_election):
    _e, _o, c, v = make_election()
    voter = v[0]
    submit_vote(voter.user, "kahim", c["kahim"][0].pk, uuid.uuid4())
    client.force_login(voter.user)
    other = Client()
    assert other.post(reverse("password_reset"), {"email": "unknown@itb.ac.id"}).status_code == 302
    assert EmailDelivery.objects.count() == 0
    assert other.post(reverse("password_reset"), {"email": voter.user.email}).status_code == 302
    assert EmailDelivery.objects.filter(kind="reset").count() == 1
    payload = json.loads(cipher().decrypt(EmailDelivery.objects.get().encrypted_payload.encode()))
    assert "http://localhost:8000/password-reset/" in payload["text"]
    uid = urlsafe_base64_encode(force_bytes(voter.user.pk))
    token = default_token_generator.make_token(voter.user)
    response = other.get(reverse("password_reset_confirm", args=[uid, token]))
    assert response.status_code == 302
    response = other.post(response.url, {"new_password1": "New-example-password-456!", "new_password2": "New-example-password-456!"})
    assert response.status_code == 302
    assert client.get(reverse("dashboard")).status_code == 302  # old session invalidated
    voter.user.refresh_from_db()
    assert voter.user.check_password("New-example-password-456!")
    assert Vote.objects.count() == 1


def test_throttle_is_shared_across_clients(make_election, settings):
    make_election()
    settings.THROTTLE_ACCOUNT_LIMIT = 2
    first, second = Client(), Client()
    for client in [first, second]:
        assert client.post(reverse("login"), {"username": "12000001", "password": "wrong"}).status_code == 200
    assert Client().post(reverse("login"), {"username": "12000001", "password": "wrong"}).status_code == 429


def test_failed_logins_from_one_address_do_not_lock_out_the_voter(make_election, settings):
    make_election()
    settings.THROTTLE_ACCOUNT_LIMIT = 2
    attacker = Client(REMOTE_ADDR="10.0.0.1")
    for _attempt in range(2):
        assert attacker.post(reverse("login"), {"username": "12000001", "password": "wrong"}).status_code == 200
    assert attacker.post(reverse("login"), {"username": "12000001", "password": "wrong"}).status_code == 429
    voter = Client(REMOTE_ADDR="10.0.0.2")
    assert voter.post(reverse("login"), {"username": "12000001", "password": "Example-password-123!"}).status_code == 302


def test_successful_login_clears_failures(make_election, settings):
    make_election()
    settings.THROTTLE_ACCOUNT_LIMIT = 2
    wrong = {"username": "12000001", "password": "wrong"}
    assert Client().post(reverse("login"), wrong).status_code == 200
    assert Client().post(reverse("login"), {"username": "12000001", "password": "Example-password-123!"}).status_code == 302
    for _attempt in range(2):
        assert Client().post(reverse("login"), wrong).status_code == 200
    assert Client().post(reverse("login"), wrong).status_code == 429


def test_redirect_for_other_logged_in_session_does_not_clear_failures(make_election, settings):
    _e, _o, _c, voters = make_election(voter_count=2)
    settings.THROTTLE_ACCOUNT_LIMIT = 2
    wrong = {"username": "12000001", "password": "wrong"}
    assert Client().post(reverse("login"), wrong).status_code == 200
    other = Client()
    other.force_login(voters[1].user)
    # LoginView redirects an already-authenticated session without checking the password.
    assert other.post(reverse("login"), wrong).status_code == 302
    assert Client().post(reverse("login"), wrong).status_code == 429


def test_account_wide_limit_bounds_distributed_guessing(make_election, settings):
    make_election()
    settings.THROTTLE_ACCOUNT_WIDE_LIMIT = 3
    wrong = {"username": "12000001", "password": "wrong"}
    for index in range(3):
        assert Client(REMOTE_ADDR=f"10.0.1.{index}").post(reverse("login"), wrong).status_code == 200
    assert Client(REMOTE_ADDR="10.0.1.99").post(reverse("login"), wrong).status_code == 429


def test_trusted_proxy_address_keys_the_ip_limit(make_election, settings):
    make_election()
    settings.THROTTLE_IP_LIMIT = 1
    settings.TRUST_PROXY = True
    settings.TRUSTED_PROXY_HOPS = 1
    wrong = {"username": "12000001", "password": "wrong"}
    proxy = "203.0.113.9"  # every request reaches the app from the same proxy address

    def post(forwarded):
        return Client(REMOTE_ADDR=proxy, HTTP_X_FORWARDED_FOR=forwarded).post(reverse("login"), wrong).status_code

    assert post("198.51.100.1") == 200
    assert post("198.51.100.1") == 429
    assert post("198.51.100.2") == 200  # another client behind the same proxy is unaffected
    assert post("1.2.3.4, 198.51.100.1") == 429  # a client-supplied left entry cannot dodge the limit


def test_forwarded_header_ignored_without_trusted_proxy(make_election, settings):
    make_election()
    settings.THROTTLE_IP_LIMIT = 1
    settings.TRUST_PROXY = False
    wrong = {"username": "12000001", "password": "wrong"}
    assert Client(HTTP_X_FORWARDED_FOR="198.51.100.1").post(reverse("login"), wrong).status_code == 200
    assert Client(HTTP_X_FORWARDED_FOR="198.51.100.2").post(reverse("login"), wrong).status_code == 429


def test_csrf_required_for_votes(make_election):
    _e, _o, c, v = make_election()
    client = Client(enforce_csrf_checks=True)
    client.force_login(v[0].user)
    response = client.post(reverse("ballot", args=["kahim"]), {
        "candidate": c["kahim"][0].pk, "request_id": uuid.uuid4(), "action": "submit"})
    assert response.status_code == 403 and Vote.objects.count() == 0


def test_committee_requires_mfa_even_for_superuser(make_election):
    make_election()
    user, client, _device = staff_client()
    assert client.get(reverse("committee")).status_code == 200
    no_mfa = Client()
    user.is_superuser = True
    user.save()
    no_mfa.force_login(user)
    assert no_mfa.get(reverse("committee")).status_code == 302
    assert no_mfa.get(reverse("committee_admin:index")).status_code == 302
    assert Client().post(reverse("login"), {"username": user.username, "password": "Committee-password-123!"}).status_code == 200


def test_committee_otp_login_and_recovery_code(make_election):
    make_election()
    user, _client, device = staff_client()
    client = Client()
    response = client.get(reverse("committee_admin:login"))
    assert response.status_code == 200
    credentials = {"username": user.username, "password": "Committee-password-123!", "otp_device": device.persistent_id,
                    "otp_token": f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits, drift=device.drift):06d}"}
    response = client.post(reverse("committee_admin:login"), credentials)
    assert response.status_code == 302
    assert client.get(reverse("committee")).status_code == 200
    recovery = StaticDevice.objects.create(user=user, name="Recovery")
    StaticToken.objects.create(device=recovery, token="1234567890abcdef")
    credentials.update(otp_device=recovery.persistent_id, otp_token="1234567890abcdef")
    assert Client().post(reverse("committee_admin:login"), credentials).status_code == 302
    assert not recovery.token_set.exists()


def test_reviewer_permission_and_export_audit(make_election):
    make_election(closes_in=timedelta(seconds=-1))
    user, client, _device = staff_client()
    assert client.get(reverse("export_results", args=["ballots"])).status_code == 403
    user.user_permissions.add(Permission.objects.get(codename="review_ballots"))
    response = client.get(reverse("export_results", args=["ballots"]))
    assert response.status_code == 200
    assert next(csv.reader(io.StringIO(response.content.decode())))[0] == "nim"
    assert AuditEvent.objects.filter(action="export.identity_ballots", actor=user).count() == 1


def test_language_switch_and_profiles(client, make_election):
    _e, _o, _c, voters = make_election()
    assert "Pemilu" in client.get("/").content.decode()
    client.post(reverse("set_language"), {"language": "en", "next": "/"})
    assert "How to vote" in client.get("/").content.decode()
    client.force_login(voters[0].user)
    assert "Profile 1" in client.get(reverse("ballot", args=["kahim"])).content.decode()
    client.post(reverse("set_language"), {"language": "id", "next": "/"})
    assert "Profil 1" in client.get(reverse("ballot", args=["kahim"])).content.decode()


def test_signed_webhooks_and_replay(client, make_election, settings):
    _e, _o, _c, voters = make_election()
    d = enqueue(voters[0].user, "credentials", "Test", "secret")
    EmailDelivery.objects.filter(pk=d.pk).update(status="sent", provider_id="email-123", encrypted_payload="")
    secret = "c3VwZXItc2VjcmV0LXNpZ25pbmcta2V5"
    settings.RESEND_WEBHOOK_SECRET = "whsec_" + secret
    payload = json.dumps({"type": "email.bounced", "data": {"email_id": "email-123"}})
    now = timezone.now()
    signature = Webhook(secret).sign("event-123", now, payload)
    headers = {"HTTP_SVIX_ID": "event-123", "HTTP_SVIX_TIMESTAMP": str(int(now.timestamp())), "HTTP_SVIX_SIGNATURE": signature}
    assert client.post(reverse("resend_events"), payload, content_type="application/json").status_code == 400
    for _ in range(2):
        assert client.post(reverse("resend_events"), payload, content_type="application/json", **headers).status_code == 204
    d.refresh_from_db()
    assert d.status == "failed" and d.bounced_at


def test_health_checks(client):
    assert client.get(reverse("health_live")).json()["status"] == "ok"
    assert client.get(reverse("health_ready")).json()["status"] == "ready"


def test_production_checks_reject_development(settings):
    from elections.checks import production_configuration

    settings.OUTBOX_KEY = ""
    settings.RESEND_API_KEY = ""
    errors = {item.id for item in production_configuration(None)}
    assert {"gea.E002", "gea.E003", "gea.E004", "gea.E005"}.issubset(errors)
    if connection.vendor != "postgresql":
        assert "gea.E001" in errors
