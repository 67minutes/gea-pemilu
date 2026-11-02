from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db
T = 1_800_000_000.0
PASSWORD = "Example-password-123!"


def at(seconds):
    return patch("elections.kiosk.time.time", return_value=T + seconds)


def tags(response):
    return [m.extra_tags for m in response.context["messages"]]


def test_idle_session_expires_on_server(client, make_election, settings):
    settings.KIOSK_IDLE_SECONDS = 180
    _e, _o, _c, voters = make_election()
    client.force_login(voters[0].user)
    with at(0):
        assert client.get(reverse("dashboard")).status_code == 200
    with at(181):
        response = client.get(reverse("dashboard"), follow=True)
    assert response.redirect_chain[-1][0].startswith(reverse("login"))
    assert tags(response) == ["kiosk-idle"]
    assert not response.wsgi_request.user.is_authenticated
    with at(182):
        assert client.get(reverse("dashboard")).url.startswith(reverse("login"))


def test_ping_keeps_session_alive(client, make_election, settings):
    settings.KIOSK_IDLE_SECONDS = 180
    _e, _o, _c, voters = make_election()
    client.force_login(voters[0].user)
    with at(0):
        client.get(reverse("dashboard"))
    with at(150):
        assert client.post(reverse("kiosk_ping")).status_code == 204
    with at(300):
        assert client.get(reverse("dashboard")).status_code == 200
    with at(481):
        assert client.get(reverse("dashboard")).status_code == 302


def test_ping_requires_login_and_post(client, make_election):
    _e, _o, _c, voters = make_election()
    assert client.post(reverse("kiosk_ping")).status_code == 302
    client.force_login(voters[0].user)
    assert client.get(reverse("kiosk_ping")).status_code == 405


def test_new_login_ends_older_session(make_election):
    _e, _o, _c, voters = make_election()
    credentials = {"username": voters[0].user.username, "password": PASSWORD}
    first, second = Client(), Client()
    assert first.post(reverse("login"), credentials).status_code == 302
    assert second.post(reverse("login"), credentials).status_code == 302
    response = first.get(reverse("dashboard"), follow=True)
    assert response.redirect_chain[-1][0].startswith(reverse("login"))
    assert tags(response) == ["kiosk-replaced"]
    assert second.get(reverse("dashboard")).status_code == 200


def test_force_login_without_recorded_key_is_not_kicked(client, make_election):
    _e, _o, _c, voters = make_election()
    assert voters[0].session_key == ""
    client.force_login(voters[0].user)
    assert client.get(reverse("dashboard")).status_code == 200


def test_staff_sessions_ignore_kiosk_rules(make_election, staff_client, settings):
    settings.KIOSK_IDLE_SECONDS = 180
    make_election()
    _user, client, _device = staff_client()
    with at(0):
        assert client.get(reverse("committee")).status_code == 200
    with at(600):
        assert client.get(reverse("committee")).status_code == 200


def test_voter_pages_carry_idle_timer(client, make_election, settings):
    settings.KIOSK_IDLE_SECONDS = 180
    _e, _o, _c, voters = make_election()
    assert "data-idle-seconds" not in client.get(reverse("home")).content.decode()
    client.force_login(voters[0].user)
    page = client.get(reverse("dashboard")).content.decode()
    assert 'data-idle-seconds="180"' in page and "js/kiosk.js" in page and 'id="logout-form"' in page
