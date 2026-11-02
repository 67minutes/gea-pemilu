import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from elections.models import AuditEvent, Election, Vote, VotingWindow
from elections.services import (
    ElectionError,
    activate,
    add_window,
    extend_window,
    is_voting_open,
    remove_window,
    set_paused,
    sync_envelope,
)

pytestmark = pytest.mark.django_db
H = timedelta(hours=1)


def build(make_election, *ranges, active=True):
    """Election whose windows are the given (start, end) offsets from now."""
    election, _offices, candidates, voters = make_election(active=False)
    election.windows.all().delete()
    now = timezone.now()
    for start, end in ranges:
        VotingWindow.objects.create(election=election, starts_at=now + start, ends_at=now + end)
    sync_envelope(election)
    if active:
        election.activated_at = now
        election.save(update_fields=["activated_at"])
    return Election.objects.get(pk=election.pk), candidates, voters


@pytest.mark.parametrize("ranges,state", [
    ([(-H, H), (2 * H, 3 * H)], "open"),
    ([(-2 * H, -H), (H, 2 * H)], "between"),
    ([(H, 2 * H)], "scheduled"),
    ([(-3 * H, -2 * H), (-H * 1.5, -H)], "closed"),
])
def test_state_follows_windows(make_election, ranges, state):
    election, _c, _v = build(make_election, *ranges)
    assert election.state == state


def test_current_and_next_window(make_election):
    election, _c, _v = build(make_election, (-2 * H, -H), (H, 2 * H), (3 * H, 4 * H))
    upcoming = election.windows.all()[1]
    assert election.current_window() is None
    assert election.next_window() == upcoming
    assert election.current_window(upcoming.starts_at) == upcoming
    assert election.next_window(upcoming.starts_at) == election.windows.all()[2]


def test_is_voting_open_is_half_open(make_election):
    election, _c, _v = build(make_election, (-H, H))
    window = election.windows.get()
    assert is_voting_open(election, window.starts_at)
    assert not is_voting_open(election, window.ends_at)
    set_paused(None, True)
    election.refresh_from_db()
    assert not is_voting_open(election, timezone.now())


def test_overlap_rejected_touching_allowed(make_election):
    election, _c, _v = build(make_election, (H, 3 * H), active=False)
    window = election.windows.get()
    with pytest.raises(ElectionError):
        add_window(None, window.starts_at + H, window.ends_at + H)
    with pytest.raises(ElectionError):
        add_window(None, window.ends_at, window.ends_at)
    add_window(None, window.ends_at, window.ends_at + H)
    assert election.windows.count() == 2


def test_envelope_tracks_windows(make_election):
    election, _c, _v = build(make_election, active=False)
    now = timezone.now()
    first = add_window(None, now + H, now + 2 * H)
    second = add_window(None, now + 5 * H, now + 6 * H)
    election.refresh_from_db()
    assert (election.opens_at, election.closes_at) == (first.starts_at, second.ends_at)
    remove_window(None, first.pk)
    remove_window(None, second.pk)
    election.refresh_from_db()
    assert election.opens_at is None and election.closes_at is None


def test_after_activation_append_and_extend_only(make_election):
    election, _c, _v = build(make_election, (-H, H), (3 * H, 4 * H))
    current, later = election.windows.all()
    now = timezone.now()
    for change in [lambda: remove_window(None, later.pk),
                   lambda: add_window(None, now - 2 * H, now - 1.5 * H),
                   lambda: extend_window(None, current.pk, current.ends_at - timedelta(minutes=10))]:
        with pytest.raises(ElectionError):
            change()
    extend_window(None, current.pk, current.ends_at + timedelta(minutes=30))
    added = add_window(None, later.ends_at + H, later.ends_at + 2 * H)
    election.refresh_from_db()
    assert election.closes_at == added.ends_at
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"window.added", "window.extended"} <= actions


def test_ended_window_cannot_be_extended(make_election):
    election, _c, _v = build(make_election, (-2 * H, -H), (H, 2 * H))
    ended = election.windows.first()
    with pytest.raises(ElectionError):
        extend_window(None, ended.pk, ended.ends_at + timedelta(minutes=30))


def test_window_before_opening_rejected_after_activation(make_election):
    election, _c, _v = build(make_election, (2 * H, 3 * H))
    with pytest.raises(ElectionError):
        add_window(None, election.opens_at - H, election.opens_at - timedelta(minutes=30))


def test_closed_election_refuses_window_changes(make_election):
    election, _c, _v = build(make_election, (-2 * H, -H))
    window = election.windows.get()
    now = timezone.now()
    with pytest.raises(ElectionError):
        add_window(None, now + H, now + 2 * H)
    with pytest.raises(ElectionError):
        extend_window(None, window.pk, now + H)


def test_activation_requires_a_window(make_election):
    election, _c, _v = build(make_election, active=False)
    with pytest.raises(ElectionError):
        activate(None)
    election.refresh_from_db()
    assert election.activated_at is None


def test_activation_with_future_window(make_election):
    build(make_election, (H, 2 * H), active=False)
    assert activate(None).activated_at


def test_service_rejects_vote_between_windows(make_election):
    from elections.services import submit_vote

    election, candidates, voters = build(make_election, (-2 * H, -H), (H, 2 * H))
    with pytest.raises(ElectionError):
        submit_vote(voters[0].user, "kahim", candidates["kahim"][0].pk, uuid.uuid4())
    with patch("elections.services.database_now", return_value=election.windows.last().starts_at):
        assert is_voting_open(election, election.windows.last().starts_at)


def test_postgres_window_guards(make_election):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger test")
    election, candidates, voters = build(make_election, (-2 * H, -H), (H, 2 * H))
    ended, upcoming = election.windows.all()
    for operation in [
        lambda: VotingWindow.objects.filter(pk=upcoming.pk).delete(),
        lambda: VotingWindow.objects.filter(pk=upcoming.pk).update(starts_at=upcoming.starts_at + H),
        lambda: VotingWindow.objects.filter(pk=upcoming.pk).update(ends_at=upcoming.ends_at - H / 2),
        lambda: VotingWindow.objects.filter(pk=ended.pk).update(ends_at=ended.ends_at + H / 2),
        lambda: VotingWindow.objects.create(election=election, starts_at=timezone.now() - H / 2,
                                            ends_at=timezone.now() + H / 2),
        lambda: Election.objects.filter(pk=election.pk).update(closes_at=election.closes_at - H),
        lambda: Vote.objects.create(election=election, voter=voters[0], office=candidates["kahim"][0].office,
                                    candidate=candidates["kahim"][0], submitted_at=timezone.now(),
                                    request_id=uuid.uuid4()),
    ]:
        with pytest.raises(DatabaseError), transaction.atomic():
            operation()
    VotingWindow.objects.filter(pk=upcoming.pk).update(ends_at=upcoming.ends_at + H)
    Election.objects.filter(pk=election.pk).update(closes_at=upcoming.ends_at + H)


def test_postgres_guards_fail_closed_on_null_and_after_close(make_election):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger test")
    election, _c, _v = build(make_election, (-2 * H, -H))
    for operation in [
        lambda: Election.objects.filter(pk=election.pk).update(closes_at=None),
        lambda: VotingWindow.objects.create(election=election, starts_at=timezone.now() + H,
                                            ends_at=timezone.now() + 2 * H),
    ]:
        with pytest.raises(DatabaseError), transaction.atomic():
            operation()


def test_no_votes_after_closes_at_even_inside_a_window(make_election):
    election, candidates, voters = build(make_election, (-H, H), active=False)
    now = timezone.now()
    Election.objects.filter(pk=election.pk).update(closes_at=now - timedelta(minutes=1), activated_at=now)
    election.refresh_from_db()
    assert not is_voting_open(election, timezone.now())
    if connection.vendor == "postgresql":
        with pytest.raises(DatabaseError), transaction.atomic():
            Vote.objects.create(election=election, voter=voters[0], office=candidates["kahim"][0].office,
                                candidate=candidates["kahim"][0], submitted_at=now, request_id=uuid.uuid4())


def window_post(client, **data):
    from django.urls import reverse

    return client.post(reverse("committee_action"), data, follow=True)


def test_committee_adds_and_removes_windows_before_activation(make_election, staff_client):
    build(make_election, active=False)
    _user, client, _device = staff_client()
    response = window_post(client, action="add_window", starts_at="2026-12-01T09:00", ends_at="2026-12-01T15:00")
    assert response.status_code == 200
    window = VotingWindow.objects.get()
    assert window.starts_at == datetime(2026, 12, 1, 2, 0, tzinfo=dt_timezone.utc)
    page = client.get("/committee/").content.decode()
    assert "09:00–15:00" in page
    window_post(client, action="remove_window", window_id=window.pk)
    assert not VotingWindow.objects.exists()


def test_committee_extends_a_running_window(make_election, staff_client):
    election, _c, _v = build(make_election, (-H, H))
    window = election.windows.get()
    _user, client, _device = staff_client()
    later = timezone.localtime(window.ends_at + H).strftime("%Y-%m-%dT%H:%M")
    window_post(client, action="extend_window", window_id=window.pk, ends_at=later)
    window.refresh_from_db()
    assert timezone.localtime(window.ends_at).strftime("%Y-%m-%dT%H:%M") == later


def test_committee_window_errors_are_messages_not_500(make_election, staff_client):
    build(make_election, (2 * H, 3 * H), active=False)
    window = VotingWindow.objects.get()
    _user, client, _device = staff_client()
    start = timezone.localtime(window.starts_at + H / 2).strftime("%Y-%m-%dT%H:%M")
    end = timezone.localtime(window.ends_at + H).strftime("%Y-%m-%dT%H:%M")
    for starts_at in [start, "not a date"]:
        response = window_post(client, action="add_window", starts_at=starts_at, ends_at=end)
        assert response.status_code == 200
        assert [m.level_tag for m in response.context["messages"]] == ["error"]
    assert VotingWindow.objects.count() == 1


def test_committee_window_actions_need_operate_permission(make_election, staff_client):
    build(make_election, active=False)
    _user, client, _device = staff_client(operate=False)
    from django.urls import reverse

    response = client.post(reverse("committee_action"), {
        "action": "add_window", "starts_at": "2026-12-01T09:00", "ends_at": "2026-12-01T15:00"})
    assert response.status_code == 403 and not VotingWindow.objects.exists()


def test_admin_shows_sessions_read_only(make_election, staff_client):
    from django.contrib.auth.models import Permission

    election, _c, _v = build(make_election, (H, 2 * H), active=False)
    user, client, _device = staff_client()
    user.user_permissions.add(*Permission.objects.filter(codename__in=["change_election", "view_election"]))
    page = client.get(f"/committee/admin/elections/election/{election.pk}/change/").content.decode()
    assert 'name="name"' in page and 'name="opens_at"' not in page
    assert "windows-0-starts_at" not in page  # sessions are listed, never edited here
    assert str(election.windows.get()) in page
