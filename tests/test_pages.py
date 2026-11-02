from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from elections.views import COMMITTEE, COMMITTEE_CHAIR

pytestmark = pytest.mark.django_db


def test_about_lists_whole_committee(client):
    page = client.get(reverse("about")).content.decode()
    for _role, name, nim in [COMMITTEE_CHAIR, *COMMITTEE]:
        assert nim in page
    assert "Sahara Nada Maheswari" in page and "Wafda Agung Mulkinegara" in page


def test_helpdesk_shows_contact_and_qr(client):
    page = client.get(reverse("helpdesk")).content.decode()
    assert "https://wa.me/6285128047459" in page
    assert "0851-2804-7459" in page
    assert "img/helpdesk-qr.jpg" in page


def test_login_points_to_helpdesk(client):
    assert reverse("helpdesk") in client.get(reverse("login")).content.decode()


def test_home_has_no_visible_demo_marker(client, make_election):
    make_election(name="DEMO — GEA ITB")
    page = client.get(reverse("home")).content.decode()
    assert "DEMO" not in page
    assert 'data-rehearsal="true"' in page  # invisible gate for loadtest/voting.js
    assert "Ketua Himpunan" in page


def test_results_use_office_label(client, make_election):
    make_election(closes_in=-timedelta(minutes=1))
    page = client.get(reverse("results")).content.decode()
    assert "Ketua Himpunan" in page and ">Kahim<" not in page


def test_voting_pages_carry_the_arena_backdrop(client, make_election):
    election, _offices, _candidates, voters = make_election()
    client.force_login(voters[0].user)
    for url in [reverse("dashboard"), reverse("ballot", args=["kahim"])]:
        page = client.get(url).content.decode()
        assert 'class="voting-art" data-scene="arena" data-live="true"' in page
        assert f'data-state="{election.state}"' in page


def session_range(window):
    from django.utils import timezone

    return f"{timezone.localtime(window.starts_at):%H:%M}–{timezone.localtime(window.ends_at):%H:%M}"


def test_schedule_lists_sessions_and_next_opening(client, make_election):
    from tests.test_windows import H, build

    election, _c, _v = build(make_election, (-2 * H, -H), (H, 2 * H))
    ended, upcoming = election.windows.all()
    page = client.get("/").content.decode()
    assert session_range(ended) in page and session_range(upcoming) in page
    assert 'data-state="between"' in page
    assert f'data-countdown="{timezone.localtime(upcoming.starts_at).isoformat()}"' in page


def test_schedule_counts_down_to_the_current_session_end(client, make_election):
    from tests.test_windows import H, build

    election, _c, _v = build(make_election, (-H, H), (3 * H, 4 * H))
    current = election.windows.first()
    page = client.get("/").content.decode()
    assert f'data-countdown="{timezone.localtime(current.ends_at).isoformat()}"' in page


def test_credentials_email_lists_every_session(make_election):
    import json

    from elections.mail import cipher
    from elections.models import EmailDelivery
    from elections.services import issue_credentials
    from tests.test_windows import H, build

    election, _c, _v = build(make_election, (H, 2 * H), (25 * H, 26 * H))
    issue_credentials(None)
    body = json.loads(cipher().decrypt(EmailDelivery.objects.get().encrypted_payload.encode()))["text"]
    for window in election.windows.all():
        assert session_range(window) in body
    assert "Selesai / Closes" not in body and "langsung di laptop panitia" in body
