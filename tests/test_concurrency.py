import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from django.db import close_old_connections, connection, transaction

from elections.models import Election, Vote, Voter
from elections.services import database_now, finalize_results, submit_vote

pytestmark = pytest.mark.django_db(transaction=True)


def require_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock and closing-boundary tests require PostgreSQL")


def test_parallel_submissions_same_voter_count_once(make_election):
    require_postgres()
    _e, _o, c, v = make_election()
    user_id = v[0].user_id

    def submit(index):
        close_old_connections()
        try:
            voter = Voter.objects.select_related("user").get(user_id=user_id)
            vote, _created = submit_vote(voter.user, "kahim", c["kahim"][index % 2].pk, uuid.uuid4())
            return vote.receipt_id
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        receipts = list(pool.map(submit, range(24)))
    assert len(set(receipts)) == 1
    assert Vote.objects.count() == 1


def test_finalization_waits_for_acknowledged_boundary_vote(make_election):
    require_postgres()
    e, _o, c, v = make_election(closes_in=timedelta(seconds=2))
    inserted, release, finalize_started = Event(), Event(), Event()

    def delayed_commit():
        close_old_connections()
        try:
            with transaction.atomic():
                voter = Voter.objects.select_related("user").get(pk=v[0].pk)
                submit_vote(voter.user, "kahim", c["kahim"][0].pk, uuid.uuid4())
                inserted.set()
                assert release.wait(5)
        finally:
            connection.close()

    def finalize():
        close_old_connections()
        try:
            finalize_started.set()
            return finalize_results()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_vote = pool.submit(delayed_commit)
        assert inserted.wait(2)
        while database_now() < e.closes_at:
            time.sleep(0.02)
        pending_results = pool.submit(finalize)
        assert finalize_started.wait(1)
        time.sleep(0.05)
        assert not pending_results.done()
        release.set()
        pending_vote.result(timeout=5)
        snapshot = pending_results.result(timeout=5)
    assert sum(o["total"] for o in snapshot["offices"]) == 1
    assert Election.objects.get().result_snapshot == snapshot
