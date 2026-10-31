from datetime import timedelta

import pytest
from django.urls import reverse

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
