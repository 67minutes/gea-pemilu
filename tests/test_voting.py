import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from elections.models import AuditEvent, Candidate, Election, Vote
from elections.services import ElectionError, finalize_results, revoke_access, set_paused, submit_vote

pytestmark = pytest.mark.django_db


def cast(voter, office, candidate):
    return submit_vote(voter.user, office, candidate.pk, uuid.uuid4())


def test_separate_votes_and_retries_cannot_replace(make_election):
    _e, _o, candidates, voters = make_election()
    voter = voters[0]
    first, created = cast(voter, "kahim", candidates["kahim"][0])
    assert created
    duplicate, created = cast(voter, "kahim", candidates["kahim"][1])
    assert not created and duplicate.pk == first.pk
    assert duplicate.candidate_id == candidates["kahim"][0].pk
    assert not Vote.objects.filter(voter=voter, office__slug="senator").exists()
    cast(voter, "senator", candidates["senator"][1])
    assert Vote.objects.filter(voter=voter).count() == 2


def test_wrong_office_and_unknown_office_rejected(make_election):
    _e, _o, candidates, voters = make_election()
    for slug, candidate in [("kahim", candidates["senator"][0]), ("missing", candidates["kahim"][0])]:
        with pytest.raises(ElectionError):
            cast(voters[0], slug, candidate)
    assert Vote.objects.count() == 0


@pytest.mark.parametrize("state", ["draft", "scheduled", "closed", "paused", "revoked"])
def test_closed_inactive_and_ineligible_rejected(make_election, state):
    kwargs = {"active": state != "draft"}
    if state == "closed":
        kwargs["closes_in"] = timedelta(seconds=-1)
    if state == "scheduled":
        kwargs["closes_in"] = timedelta(days=4)
    election, _o, candidates, voters = make_election(**kwargs)
    if state == "paused":
        set_paused(None, True)
    if state == "revoked":
        revoke_access(None, voters[0].pk)
    with pytest.raises(ElectionError):
        cast(voters[0], "kahim", candidates["kahim"][0])
    assert Vote.objects.count() == 0
    election.refresh_from_db()
    assert election.closes_at == kwargs.get("closes_at", election.closes_at)


def test_pause_preserves_deadline_and_existing_votes(make_election):
    e, _o, c, v = make_election()
    cast(v[0], "kahim", c["kahim"][0])
    original = e.closes_at
    set_paused(None, True)
    set_paused(None, False)
    revoke_access(None, v[0].pk)
    e.refresh_from_db()
    assert e.closes_at == original and Vote.objects.count() == 1


def test_database_uniqueness(make_election):
    _e, _o, c, v = make_election()
    vote, _ = cast(v[0], "kahim", c["kahim"][0])
    with pytest.raises(IntegrityError), transaction.atomic():
        Vote.objects.create(election_id=vote.election_id, voter=vote.voter, office=vote.office,
            candidate=c["kahim"][1], submitted_at=timezone.now(), request_id=uuid.uuid4())


def test_votes_immutable_model_and_database(make_election):
    _e, _o, c, v = make_election()
    vote, _ = cast(v[0], "kahim", c["kahim"][0])
    with pytest.raises(ValidationError):
        vote.save()
    with pytest.raises(ValidationError):
        vote.delete()
    if connection.vendor == "postgresql":
        for operation in [lambda: Vote.objects.filter(pk=vote.pk).update(candidate=c["kahim"][1]),
                          lambda: Vote.objects.filter(pk=vote.pk).delete()]:
            with pytest.raises(DatabaseError), transaction.atomic():
                operation()


def test_no_results_or_exports_before_close(client, make_election):
    _e, _o, c, v = make_election()
    cast(v[0], "kahim", c["kahim"][0])
    assert finalize_results() is None
    response = client.get(reverse("results"))
    assert response.status_code == 200
    assert "Kahim Candidate" not in response.content.decode()
    client.force_login(v[0].user)
    assert client.get(reverse("export_results", args=["ballots"])).status_code == 302


def test_public_results_do_not_lock_while_open(client, make_election):
    make_election()
    with patch("elections.services.lock_election", side_effect=AssertionError("locked")):
        assert client.get(reverse("results")).status_code == 200


def test_public_results_lock_once_after_close(client, make_election):
    e, _o, _c, _v = make_election(closes_in=timedelta(seconds=-1))
    assert client.get(reverse("results")).status_code == 200  # first request after close finalizes
    assert Election.objects.get(pk=e.pk).result_snapshot is not None
    with patch("elections.services.lock_election", side_effect=AssertionError("locked")):
        assert "Kahim Candidate 1" in client.get(reverse("results")).content.decode()


def test_finalization_is_stable_and_anonymous(client, make_election):
    e, _o, _c, _v = make_election(closes_in=timedelta(seconds=-1))
    snapshot = finalize_results()
    assert snapshot["eligible"] == 1
    assert len(snapshot["offices"]) == 2
    assert all(o["tie"] and o["total"] == 0 for o in snapshot["offices"])
    assert finalize_results() == snapshot
    assert AuditEvent.objects.filter(action="election.finalized").count() == 1
    response = client.get(reverse("results"))
    assert "12000001" not in response.content.decode()
    e.refresh_from_db()
    assert e.finalized_at and e.result_snapshot == snapshot
    if connection.vendor == "postgresql":
        with pytest.raises(DatabaseError), transaction.atomic():
            Election.objects.filter(pk=e.pk).update(result_snapshot={"tampered": True})


def test_http_review_and_confirmation_and_other_voter_receipt(client, make_election):
    _e, _o, c, v = make_election(voter_count=2)
    client.force_login(v[0].user)
    page = client.get(reverse("ballot", args=["kahim"]))
    assert b"checked" not in page.content  # no default selection
    payload = {"candidate": c["kahim"][0].pk, "request_id": str(uuid.uuid4()), "action": "review"}
    review = client.post(reverse("ballot", args=["kahim"]), payload)
    assert review.status_code == 200 and Vote.objects.count() == 0
    payload["action"] = "submit"
    receipt = client.post(reverse("ballot", args=["kahim"]), payload)
    assert receipt.status_code == 302 and Vote.objects.count() == 1
    assert client.post(reverse("ballot", args=["kahim"]), payload).url == receipt.url
    other = Client()
    other.force_login(v[1].user)
    assert other.get(receipt.url).status_code == 404


def test_submission_checks_database_deadline_not_browser(make_election):
    e, _o, c, v = make_election()
    with patch("elections.services.database_now", return_value=e.closes_at):
        with pytest.raises(ElectionError):
            cast(v[0], "kahim", c["kahim"][0])


def test_postgres_configuration_and_snapshot_guards(make_election):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger test")
    e, _o, c, _v = make_election()
    for operation in [lambda: Candidate.objects.filter(pk=c["kahim"][0].pk).update(name="Changed"),
                      lambda: Election.objects.filter(pk=e.pk).update(opens_at=e.opens_at + timedelta(hours=1)),
                      lambda: Election.objects.filter(pk=e.pk).update(result_snapshot={})]:
        with pytest.raises(DatabaseError), transaction.atomic():
            operation()
