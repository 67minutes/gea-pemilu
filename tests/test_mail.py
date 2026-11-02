from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from elections.checks import production_configuration
from elections.mail import enqueue, process_one, remaining_daily_budget
from elections.models import EmailDelivery, Voter
from elections.services import request_credentials

pytestmark = pytest.mark.django_db


def gmail(settings):
    settings.EMAIL_TRANSPORT = "gmail"
    settings.EMAIL_HOST_USER = "pemilu.gea@gmail.com"
    settings.EMAIL_HOST_PASSWORD = "app-pass"
    settings.DEFAULT_FROM_EMAIL = "GEA Pemilu <pemilu.gea@gmail.com>"


def test_gmail_transport_uses_authenticated_tls(settings, make_election):
    _e, _o, _c, v = make_election()
    gmail(settings)
    settings.DEBUG = False
    delivery = enqueue(v[0].user, "credentials", "Test", "Private content")
    with patch("elections.mail.SMTPBackend") as backend:
        backend.return_value.send_messages.return_value = 1
        assert process_one()
    backend.assert_called_once_with(host="smtp.gmail.com", port=587, use_tls=True,
                                    username="pemilu.gea@gmail.com", password="app-pass", timeout=10)
    message = backend.return_value.send_messages.call_args.args[0][0]
    assert message.to == [v[0].user.email] and message.from_email == settings.DEFAULT_FROM_EMAIL
    delivery.refresh_from_db()
    assert delivery.status == "sent" and delivery.sent_at and delivery.encrypted_payload == ""
    assert delivery.provider_id == f"gmail-{delivery.pk}"


def test_daily_limit_keeps_extra_jobs_pending(settings, make_election):
    _e, _o, _c, v = make_election()
    settings.EMAIL_DAILY_LIMIT = 2
    jobs = [enqueue(v[0].user, "credentials", f"Test {n}", "body") for n in range(3)]
    assert process_one() and process_one()
    assert not process_one()
    jobs[2].refresh_from_db()
    assert jobs[2].status == "pending" and jobs[2].attempts == 0


def test_sent_older_than_24h_frees_budget(settings, make_election):
    _e, _o, _c, v = make_election()
    settings.EMAIL_DAILY_LIMIT = 5
    for _ in range(2):
        enqueue(v[0].user, "credentials", "Test", "body")
        process_one()
    assert remaining_daily_budget() == 3
    EmailDelivery.objects.update(sent_at=timezone.now() - timedelta(hours=25))
    assert remaining_daily_budget() == 5
    settings.EMAIL_DAILY_LIMIT = 0
    assert remaining_daily_budget() is None


def test_password_resets_go_before_credentials(settings, make_election):
    _e, _o, _c, v = make_election()
    credentials = enqueue(v[0].user, "credentials", "Credentials", "body")
    reset = enqueue(v[0].user, "reset", "Reset", "body")
    process_one()
    reset.refresh_from_db()
    credentials.refresh_from_db()
    assert reset.status == "sent" and credentials.status == "pending"


def test_outbox_enqueues_credentials_only_within_budget(settings, make_election):
    make_election(voter_count=10)
    request_credentials(None)
    settings.EMAIL_DAILY_LIMIT = 5
    settings.EMAIL_RESET_RESERVE = 2
    call_command("process_outbox", limit=0)
    assert EmailDelivery.objects.filter(kind="credentials").count() == 3
    call_command("process_outbox", limit=0)
    assert EmailDelivery.objects.filter(kind="credentials").count() == 3


def check_ids():
    return {error.id for error in production_configuration(None)}


def test_production_check_accepts_configured_gmail(settings):
    gmail(settings)
    settings.RESEND_API_KEY = ""
    settings.RESEND_WEBHOOK_SECRET = ""
    assert not {"gea.E003", "gea.E005", "gea.E006"} & check_ids()
    settings.EMAIL_HOST_PASSWORD = ""
    assert "gea.E003" in check_ids()
    settings.EMAIL_HOST_PASSWORD = "app-pass"
    settings.DEFAULT_FROM_EMAIL = "GEA Pemilu <someone.else@gmail.com>"
    assert "gea.E005" in check_ids()


def test_send_test_email_uses_configured_transport(settings, capsys):
    from django.core import mail

    call_command("send_test_email", "someone@students.itb.ac.id")
    assert mail.outbox[0].to == ["someone@students.itb.ac.id"]
    assert "GEA" in mail.outbox[0].subject
    out = capsys.readouterr().out
    assert "local-" in out and settings.SECRET_KEY not in out


def request_reset(client, voter):
    return client.post(reverse("password_reset"), {"email": voter.user.email})


def test_reset_requests_have_a_per_voter_cooldown(client, make_election):
    _e, _o, _c, v = make_election()
    Voter.objects.filter(pk=v[0].pk).update(credentials_issued_at=timezone.now())
    for _ in range(3):
        request_reset(client, v[0])
    assert EmailDelivery.objects.filter(kind="reset").count() == 1
    EmailDelivery.objects.update(created_at=timezone.now() - timedelta(minutes=11))
    request_reset(client, v[0])  # an older request, sent or still pending, never locks the voter out
    assert EmailDelivery.objects.filter(kind="reset").count() == 2


def test_reset_jobs_expire_with_their_link(make_election):
    from elections.services import cleanup_expired_payloads

    _e, _o, _c, v = make_election()
    stale = enqueue(v[0].user, "reset", "Reset", "body")
    fresh = enqueue(v[0].user, "reset", "Reset", "body")
    credentials = enqueue(v[0].user, "credentials", "Credentials", "body")
    EmailDelivery.objects.filter(pk__in=[stale.pk, credentials.pk]).update(
        created_at=timezone.now() - timedelta(minutes=61))
    cleanup_expired_payloads()
    assert set(EmailDelivery.objects.filter(status="pending").values_list("pk", flat=True)) == {
        fresh.pk, credentials.pk}


def test_pending_resets_do_not_hold_back_credentials(settings, make_election):
    _e, _o, _c, v = make_election(voter_count=6)
    for voter in v[:3]:
        Voter.objects.filter(pk=voter.pk).update(credentials_issued_at=timezone.now())
        enqueue(voter.user, "reset", "Reset", "body")
    request_credentials(None)
    settings.EMAIL_DAILY_LIMIT = 6
    settings.EMAIL_RESET_RESERVE = 2
    call_command("process_outbox", limit=0)
    assert EmailDelivery.objects.filter(kind="credentials").count() == 3


def test_no_reset_email_before_credentials_are_issued(client, make_election):
    _e, _o, _c, v = make_election()
    assert request_reset(client, v[0]).status_code == 302
    assert not EmailDelivery.objects.filter(kind="reset").exists()


def test_resets_cannot_use_more_than_the_reserve(settings, make_election):
    _e, _o, _c, v = make_election()
    settings.EMAIL_DAILY_LIMIT = 10
    settings.EMAIL_RESET_RESERVE = 2
    resets = [enqueue(v[0].user, "reset", "Reset", "body") for _ in range(3)]
    credentials = enqueue(v[0].user, "credentials", "Credentials", "body")
    while process_one():
        pass
    assert EmailDelivery.objects.filter(kind="reset", status="sent").count() == 2
    credentials.refresh_from_db()
    resets[2].refresh_from_db()
    assert credentials.status == "sent" and resets[2].status == "pending"
