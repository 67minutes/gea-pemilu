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


def enroll(staff, label="Laptop 1"):
    response = staff.post(reverse("kiosks"), {"action": "enroll", "label": label})
    assert response.status_code == 302
    return response


def voter_login(client, voter):
    return client.post(reverse("login"), {"username": voter.user.username, "password": PASSWORD})


def test_voter_login_refused_on_unenrolled_device(client, make_election, settings):
    settings.KIOSK_REQUIRED = True
    _e, _o, _c, voters = make_election()
    response = voter_login(client, voters[0])
    assert response.status_code == 200 and not response.wsgi_request.user.is_authenticated
    assert [e.code for e in response.context["form"].non_field_errors().as_data()] == ["kiosk"]
    assert 'data-kiosk="unenrolled"' in client.get(reverse("login")).content.decode()


def test_operator_enrolls_device_and_voter_can_log_in(make_election, staff_client, settings):
    from elections.models import AuditEvent, KioskDevice

    settings.KIOSK_REQUIRED = True
    _e, _o, _c, voters = make_election()
    _user, staff, _device = staff_client()
    cookie = enroll(staff).cookies["gea_kiosk"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax" and cookie["max-age"] == settings.KIOSK_COOKIE_AGE
    device = KioskDevice.objects.get()
    assert device.label == "Laptop 1" and AuditEvent.objects.filter(action="kiosk.enrolled").count() == 1
    assert "Laptop 1" in staff.get(reverse("kiosks")).content.decode()
    kiosk = Client()
    kiosk.cookies["gea_kiosk"] = cookie.value
    assert 'data-kiosk="unenrolled"' not in kiosk.get(reverse("login")).content.decode()
    response = voter_login(kiosk, voters[0])
    assert response.status_code == 302 and response.url == reverse("dashboard")
    assert kiosk.get(reverse("dashboard")).status_code == 200


def test_revoked_device_ends_voter_sessions(make_election, staff_client, settings):
    from elections.models import AuditEvent, KioskDevice

    settings.KIOSK_REQUIRED = True
    _e, _o, _c, voters = make_election()
    _user, staff, _device = staff_client()
    kiosk = Client()
    kiosk.cookies["gea_kiosk"] = enroll(staff).cookies["gea_kiosk"].value
    assert voter_login(kiosk, voters[0]).status_code == 302
    device = KioskDevice.objects.get()
    assert staff.post(reverse("kiosks"), {"action": "revoke", "device_id": str(device.pk)}).status_code == 302
    device.refresh_from_db()
    assert device.revoked_at and AuditEvent.objects.filter(action="kiosk.revoked").count() == 1
    response = kiosk.get(reverse("dashboard"), follow=True)
    assert response.redirect_chain[-1][0].startswith(reverse("login"))
    assert tags(response) == ["kiosk-device"]


def test_tampered_or_expired_cookie_rejected(make_election, staff_client, settings):
    settings.KIOSK_REQUIRED = True
    _e, _o, _c, voters = make_election()
    _user, staff, _device = staff_client()
    value = enroll(staff).cookies["gea_kiosk"].value
    for cookie, now in [("x", None), (value[:-2] + "aa", None), (value, T + 10**10)]:
        kiosk = Client()
        kiosk.cookies["gea_kiosk"] = cookie
        if now is None:
            response = voter_login(kiosk, voters[0])
        else:
            with patch("time.time", return_value=now):
                response = voter_login(kiosk, voters[0])
        assert response.status_code == 200 and not response.wsgi_request.user.is_authenticated


def test_unknown_or_invalid_revoke_is_rejected(make_election, staff_client, settings):
    make_election()
    _user, staff, _device = staff_client()
    for device_id in ["not-a-uuid", "00000000-0000-0000-0000-000000000000"]:
        response = staff.post(reverse("kiosks"), {"action": "revoke", "device_id": device_id}, follow=True)
        assert [m.level_tag for m in response.context["messages"]] == ["error"]


def test_kiosk_page_requires_operator(make_election, staff_client):
    _e, _o, _c, voters = make_election()
    _user, staff, _device = staff_client(operate=False)
    assert staff.get(reverse("kiosks")).status_code == 403
    voter = Client()
    voter.force_login(voters[0].user)
    assert voter.get(reverse("kiosks")).status_code == 302


def test_staff_login_and_password_reset_work_without_kiosk(client, make_election, settings):
    settings.KIOSK_REQUIRED = True
    make_election()
    assert client.get(reverse("committee_admin:login")).status_code == 200
    response = client.post(reverse("password_reset"), {"email": "12000001@students.itb.ac.id"})
    assert response.status_code == 302 and response.url == reverse("password_reset_done")


def test_unenrolled_device_never_checks_the_password(client, make_election, settings):
    settings.KIOSK_REQUIRED = True
    _e, _o, _c, voters = make_election()
    with patch("django.contrib.auth.forms.authenticate") as authenticate:
        for password in [PASSWORD, "wrong-password"]:
            response = client.post(reverse("login"), {"username": voters[0].user.username, "password": password})
            assert [e.code for e in response.context["form"].non_field_errors().as_data()] == ["kiosk"]
    authenticate.assert_not_called()
