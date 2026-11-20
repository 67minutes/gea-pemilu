import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from elections.models import Candidate, Election, EmailDelivery, Office, User, Vote, Voter
from elections.services import finalize_results, submit_ballot

pytestmark = pytest.mark.django_db


def configured(make_election):
    election, offices, _, voters = make_election(active=False, voter_count=3)
    call_command("configure_candidates")
    candidates = {slug: list(office.candidates.all()) for slug, office in offices.items()}
    election.activated_at = timezone.now()
    election.save(update_fields=["activated_at"])
    return election, candidates, voters


def preview(name, response):
    if os.getenv("GEA_EXPORT_PREVIEWS"):
        path = Path("output/playwright") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)


def test_candidate_roster_preserves_draft_schedule(make_election):
    election, offices, _, _ = make_election(active=False)
    before = list(election.windows.values())
    call_command("configure_candidates")
    call_command("configure_candidates")
    election.refresh_from_db()
    assert not election.activated_at
    assert list(election.windows.values()) == before
    assert Election.objects.count() == 1
    assert Candidate.objects.count() == 4
    assert list(offices["kahim"].candidates.values_list("name", flat=True)) == [
        "Rafael Putra Adventus Naibaho", "Dzaki Fachri Rusadhi"]
    senator = list(offices["senator"].candidates.all())
    assert senator[0].name == "Juni Martha Octavia" and senator[0].nim == "12024071"
    assert senator[1].is_blank and not senator[1].photo_path
    assert not offices["kahim"].candidates.filter(is_blank=True).exists()
    assert offices["kahim"].candidates.first().profile_id == ""


def test_configuration_cannot_create_election():
    with pytest.raises(CommandError):
        call_command("configure_candidates")
    assert not Election.objects.exists()


def test_configuration_refuses_frozen_election(make_election):
    make_election()
    before = list(Candidate.objects.values())
    with pytest.raises(CommandError):
        call_command("configure_candidates")
    assert list(Candidate.objects.values()) == before


@pytest.mark.parametrize("slug,number,nim", [("kahim", 2, ""), ("senator", 1, ""), ("senator", 2, "12024071")])
def test_empty_box_restrictions(make_election, slug, number, nim):
    _, _, candidates, _ = make_election(active=False)
    candidate = candidates[slug][number - 1]
    candidate.is_blank, candidate.nim = True, nim
    with pytest.raises(ValidationError):
        candidate.full_clean()
    if connection.vendor == "postgresql":
        with pytest.raises(DatabaseError), transaction.atomic():
            Candidate.objects.filter(pk=candidate.pk).update(is_blank=True, nim=nim)


def test_office_change_cannot_move_empty_box_to_bph(make_election):
    _, offices, _, _ = make_election(active=False)
    call_command("configure_candidates")
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger")
    with pytest.raises(DatabaseError), transaction.atomic():
        Office.objects.filter(pk=offices["senator"].pk).update(slug="other")


def test_empty_box_can_be_reviewed_and_voted(client, make_election):
    _, candidates, voters = configured(make_election)
    client.force_login(voters[0].user)
    response = client.get("/vote/")
    preview("ballot.html", response)
    page = response.content.decode()
    for nim in ["12024017", "12024001", "12024071"]:
        assert f"img/candidates/{nim}.jpeg" in page
    assert "Kotak kosong" in page
    preview("home.html", client.get("/"))
    data = {"office_kahim": candidates["kahim"][0].pk, "office_senator": candidates["senator"][1].pk,
            "request_id": uuid.uuid4(), "action": "review"}
    response = client.post("/vote/", data)
    assert response.status_code == 200 and "Kotak kosong" in response.content.decode()
    assert not Vote.objects.exists()
    preview("review.html", response)
    data["action"] = "finalize"
    assert client.post("/vote/", data).status_code == 302
    assert Vote.objects.filter(candidate__is_blank=True).count() == 1
    assert Vote.objects.count() == 2


@pytest.mark.parametrize("blank_wins", [False, True])
def test_public_results_only_show_winners(client, make_election, blank_wins):
    election, candidates, voters = configured(make_election)
    for i, voter in enumerate(voters):
        submit_ballot(voter.user, {"kahim": candidates["kahim"][0 if i < 2 else 1].pk,
                                  "senator": candidates["senator"][int(blank_wins)].pk}, uuid.uuid4())
    with patch("elections.services.database_now", return_value=election.closes_at + timedelta(seconds=1)):
        # Finalization's PostgreSQL guard uses real database time, so project a complete synthetic snapshot
        # without changing the live schedule or weakening that guard.
        with patch.object(Election, "save"), patch("elections.services.audit"):
            snapshot = finalize_results()
    with patch("elections.views.published_results", return_value=snapshot):
        response = client.get("/results/")
    page = response.content.decode()
    assert "Rafael Putra Adventus Naibaho" in page
    assert "Dzaki Fachri Rusadhi" not in page and "12024001" not in page
    assert "turnout" not in page and "result-turnout" not in page
    assert "img/candidates/12024017.jpeg" in page
    assert "snapshot" not in response.context
    assert response.context["election"] is None
    assert len(snapshot["offices"][0]["candidates"]) == 2  # committee totals remain complete
    if blank_wins:
        assert "Juni Martha Octavia" not in page and "12024071" not in page
        assert "Tidak ada senator yang terpilih" in page
    else:
        assert "Juni Martha Octavia" in page and "Kotak kosong" not in page
    preview("results-blank.html" if blank_wins else "results.html", response)


@pytest.mark.parametrize("votes", [0, 7])
def test_no_false_winner_on_zero_votes_or_tie(client, make_election, votes):
    make_election()
    snapshot = {"offices": [{"slug": slug, "candidates": [
        {"name": "Hidden candidate A", "votes": votes}, {"name": "Hidden candidate B", "votes": votes}]
    } for slug in ["kahim", "senator"]]}
    with patch("elections.views.published_results", return_value=snapshot):
        response = client.get("/results/")
    assert "Hidden candidate" not in response.content.decode()
    assert all(office["winner"] is None for office in response.context["offices"])


def test_legacy_snapshot_still_renders(client, make_election):
    make_election()
    snapshot = {"offices": [{"slug": "kahim", "candidates": [
        {"number": 1, "name": "Original winner", "votes": 5},
        {"number": 2, "name": "Original runner-up", "votes": 3}]}]}
    with patch("elections.views.published_results", return_value=snapshot):
        page = client.get("/results/").content.decode()
    assert "Original winner" in page and "Original runner-up" not in page


def test_prepare_21_accounts_without_election_or_mail(tmp_path):
    path = tmp_path / "private.json"
    call_command("prepare_demo_accounts", output=str(path))
    accounts = json.loads(path.read_text())["accounts"]
    assert len(accounts) == User.objects.count() == 21
    assert not Election.objects.exists() and not Voter.objects.exists() and not EmailDelivery.objects.exists()
    assert not User.objects.filter(is_active=True).exists()
    for account in accounts:
        assert User.objects.get(username=account["nim"]).check_password(account["password"])
    with pytest.raises(CommandError):
        call_command("prepare_demo_accounts", output=str(path))
    assert json.loads(path.read_text())["accounts"] == accounts


def test_demo_preparation_preserves_closed_election(make_election, tmp_path):
    election, _, _, _ = make_election(closes_in=-timedelta(days=1))
    before = Election.objects.values().get(pk=election.pk)
    call_command("prepare_demo_accounts", output=str(tmp_path / "private.json"))
    assert Election.objects.values().get(pk=election.pk) == before
    assert Voter.objects.count() == 1


def test_prepared_accounts_can_be_enrolled_without_opening(make_election, tmp_path):
    election, _, _, _ = make_election(active=False, name="DEMO rehearsal")
    path = tmp_path / "private.json"
    call_command("prepare_demo_accounts", output=str(path))
    call_command("enroll_demo_accounts")
    election.refresh_from_db()
    assert election.activated_at is None and election.state == "draft"
    assert Voter.objects.count() == 22
    for account in json.loads(path.read_text())["accounts"]:
        user = User.objects.get(username=account["nim"])
        assert user.is_active and user.check_password(account["password"])
        assert user.voter.credentials_issued_at is not None


@pytest.mark.parametrize("name,active", [("Real election", False), ("TEST election", True)])
def test_enrollment_refuses_real_or_frozen_election(make_election, tmp_path, name, active):
    make_election(active=active, name=name)
    call_command("prepare_demo_accounts", output=str(tmp_path / "private.json"))
    with pytest.raises(CommandError):
        call_command("enroll_demo_accounts")
    assert Voter.objects.count() == 1
    assert User.objects.filter(is_active=False).count() == 21
