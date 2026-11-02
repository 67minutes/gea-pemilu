import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from elections.models import AuditEvent, Candidate, Election, Vote
from elections.services import ElectionError, finalize_results, revoke_access, set_paused, submit_ballot

pytestmark = pytest.mark.django_db


def ballot(voter, c, k=0, s=0, request_id=None):
    return submit_ballot(voter.user, {"kahim": c["kahim"][k].pk, "senator": c["senator"][s].pk},
                         request_id or uuid.uuid4())


def choices(c, k=0, s=0, action="review", request_id=None):
    return {"office_kahim": c["kahim"][k].pk, "office_senator": c["senator"][s].pk,
            "request_id": str(request_id or uuid.uuid4()), "action": action}


def test_finalize_writes_every_office_atomically(make_election):
    _e, _o, c, v = make_election()
    request_id = uuid.uuid4()
    votes, created = ballot(v[0], c, 0, 1, request_id)
    assert created and len(votes) == 2
    assert {vote.request_id for vote in votes} == {request_id}
    assert {(vote.office.slug, vote.candidate_id) for vote in votes} == {
        ("kahim", c["kahim"][0].pk), ("senator", c["senator"][1].pk)}


def test_second_finalize_returns_existing_and_changes_nothing(make_election):
    _e, _o, c, v = make_election()
    first, _created = ballot(v[0], c, 0, 0)
    again, created = ballot(v[0], c, 1, 1)
    assert not created
    assert {vote.pk for vote in again} == {vote.pk for vote in first}
    assert set(Vote.objects.values_list("candidate_id", flat=True)) == {c["kahim"][0].pk, c["senator"][0].pk}
    assert Vote.objects.count() == 2


@pytest.mark.parametrize("bad", ["missing", "extra", "wrong_office"])
def test_incomplete_or_mismatched_choices_rejected(make_election, bad):
    _e, _o, c, v = make_election()
    picks = {"kahim": c["kahim"][0].pk, "senator": c["senator"][0].pk}
    if bad == "missing":
        del picks["senator"]
    elif bad == "extra":
        picks["bendahara"] = c["kahim"][0].pk
    else:
        picks["kahim"] = c["senator"][0].pk
    with pytest.raises(ElectionError):
        submit_ballot(v[0].user, picks, uuid.uuid4())
    assert Vote.objects.count() == 0


def test_failure_on_second_office_saves_nothing(make_election):
    _e, _o, c, v = make_election()
    real_create = Vote.objects.create
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise DatabaseError("simulated failure")
        return real_create(**kwargs)

    with patch.object(Vote.objects, "create", side_effect=create), pytest.raises(DatabaseError):
        ballot(v[0], c)
    assert Vote.objects.count() == 0


@pytest.mark.parametrize("state", ["draft", "scheduled", "closed", "paused", "revoked", "at_ends_at"])
def test_finalize_refused_when_not_open(make_election, state):
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
        if state == "at_ends_at":
            with patch("elections.services.database_now", return_value=election.windows.get().ends_at):
                ballot(voters[0], candidates)
        else:
            ballot(voters[0], candidates)
    assert Vote.objects.count() == 0


def test_finalize_refused_between_sessions(make_election):
    from tests.test_windows import H, build

    _e, candidates, voters = build(make_election, (-2 * H, -H), (H, 2 * H))
    with pytest.raises(ElectionError):
        ballot(voters[0], candidates)
    assert Vote.objects.count() == 0


def test_pause_preserves_deadline_and_existing_votes(make_election):
    e, _o, c, v = make_election()
    ballot(v[0], c)
    original = e.closes_at
    set_paused(None, True)
    set_paused(None, False)
    revoke_access(None, v[0].pk)
    e.refresh_from_db()
    assert e.closes_at == original and Vote.objects.count() == 2


def test_database_uniqueness(make_election):
    _e, _o, c, v = make_election()
    votes, _ = ballot(v[0], c)
    vote = next(x for x in votes if x.office.slug == "kahim")
    with pytest.raises(IntegrityError), transaction.atomic():
        Vote.objects.create(election_id=vote.election_id, voter=vote.voter, office=vote.office,
            candidate=c["kahim"][1], submitted_at=timezone.now(), request_id=uuid.uuid4())


def test_votes_immutable_model_and_database(make_election):
    _e, _o, c, v = make_election()
    vote = ballot(v[0], c)[0][0]
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
    ballot(v[0], c)
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


def test_review_then_finalize_logs_out_and_hides_choice(client, make_election):
    _e, _o, c, v = make_election()
    client.force_login(v[0].user)
    page = client.get(reverse("ballot"))
    assert page.status_code == 200 and b"checked" not in page.content  # no default selection
    payload = choices(c, 0, 1)
    review = client.post(reverse("ballot"), payload)
    assert review.status_code == 200 and Vote.objects.count() == 0
    content = review.content.decode()
    assert c["kahim"][0].name in content and c["senator"][1].name in content
    payload["action"] = "finalize"
    done = client.post(reverse("ballot"), payload)
    assert done.status_code == 302 and done.url == reverse("done") and Vote.objects.count() == 2
    page = client.get(reverse("done")).content.decode()
    assert c["kahim"][0].name not in page and c["senator"][1].name not in page
    assert v[0].user.username not in page and str(Vote.objects.first().receipt_id) not in page
    assert '<meta http-equiv="refresh"' in page
    assert client.get(reverse("ballot")).url.startswith(reverse("login"))
    assert client.get(reverse("dashboard")).url.startswith(reverse("login"))


def test_change_returns_to_ballot_with_selection(client, make_election):
    _e, _o, c, v = make_election()
    client.force_login(v[0].user)
    page = client.post(reverse("ballot"), choices(c, 1, 0, action="change")).content.decode()
    assert f'value="{c["kahim"][1].pk}" required checked' in page
    assert Vote.objects.count() == 0


def test_incomplete_review_shows_the_ballot_again(client, make_election):
    _e, _o, c, v = make_election()
    client.force_login(v[0].user)
    payload = choices(c)
    del payload["office_senator"]
    response = client.post(reverse("ballot"), payload)
    assert response.status_code == 200 and 'role="alert"' in response.content.decode()
    assert Vote.objects.count() == 0


def test_window_closes_during_review_keeps_voter_logged_in(client, make_election):
    _e, _o, c, v = make_election()
    client.force_login(v[0].user)
    payload = choices(c)
    client.post(reverse("ballot"), payload)
    set_paused(None, True)
    payload["action"] = "finalize"
    response = client.post(reverse("ballot"), payload)
    assert response.status_code == 302 and response.url == reverse("dashboard")
    assert Vote.objects.count() == 0
    dashboard = client.get(reverse("dashboard"))
    assert dashboard.status_code == 200
    assert [m.level_tag for m in dashboard.context["messages"]] == ["error"]


def test_already_voted_voter_sees_done_state_not_ballot(client, make_election):
    _e, _o, c, v = make_election()
    ballot(v[0], c)
    client.force_login(v[0].user)
    response = client.get(reverse("ballot"))
    assert response.status_code == 302 and response.url == reverse("dashboard")
    page = client.get(reverse("dashboard")).content.decode()
    assert 'data-voted="true"' in page and c["kahim"][0].name not in page


def test_voter_pages_are_no_store(client, make_election):
    _e, _o, c, v = make_election()
    client.force_login(v[0].user)
    responses = [client.get(reverse("ballot")), client.get(reverse("dashboard")),
                 client.post(reverse("ballot"), choices(c)), client.get(reverse("done"))]
    for response in responses:
        assert "no-store" in response["Cache-Control"]


def test_submission_checks_database_deadline_not_browser(make_election):
    e, _o, c, v = make_election()
    with patch("elections.services.database_now", return_value=e.closes_at):
        with pytest.raises(ElectionError):
            ballot(v[0], c)


def test_postgres_configuration_and_snapshot_guards(make_election):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger test")
    e, _o, c, _v = make_election()
    for operation in [lambda: Candidate.objects.filter(pk=c["kahim"][0].pk).update(name="Changed"),
                      lambda: Election.objects.filter(pk=e.pk).update(opens_at=e.opens_at + timedelta(hours=1)),
                      lambda: Election.objects.filter(pk=e.pk).update(result_snapshot={})]:
        with pytest.raises(DatabaseError), transaction.atomic():
            operation()
