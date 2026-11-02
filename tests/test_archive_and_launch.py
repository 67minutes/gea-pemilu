import json
import subprocess
import sys
import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from elections.models import AuditEvent, EmailDelivery
from elections.services import issue_credentials, submit_ballot

pytestmark = pytest.mark.django_db


def test_archive_encryption_and_manifest(make_election, tmp_path):
    make_election(closes_in=timedelta(seconds=-1))
    dump = tmp_path / "database.dump"
    # Only archive-format plumbing is tested here. A real pg_restore is a release gate.
    dump.write_bytes(b"PGDMP synthetic-archive-test")
    target, key = tmp_path / "test.archive", tmp_path / "test.fernet"
    call_command("archive_election", str(dump), output=str(target), key_file=str(key), stdout=StringIO())
    assert b"synthetic" not in target.read_bytes()
    result = subprocess.run([sys.executable, "scripts/verify_archive.py", str(target), "--key-file", str(key)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "checksums verified" in result.stdout
    assert AuditEvent.objects.filter(action="archive.created").exists()
    with pytest.raises(CommandError):
        call_command("archive_election", str(dump), output=str(target), key_file=str(key))


def test_verify_rehearsal_counts_ballots(make_election, tmp_path):
    _e, _o, candidates, voters = make_election(name="DEMO — Rehearsal", voter_count=3)
    for voter in voters[:2]:
        submit_ballot(voter.user, {slug: candidates[slug][0].pk for slug in candidates}, uuid.uuid4())
    path = tmp_path / "ballots.jsonl"

    def verify(*nims, expected=2):
        path.write_text("\n".join(json.dumps({"type": "ballot", "nim": nim}) for nim in nims))
        call_command("verify_rehearsal", str(path), expected_voters=expected, stdout=StringIO())

    nims = [voter.user.username for voter in voters[:2]]
    verify(*nims)
    for bad in [nims[:1], nims + nims[:1], [nims[0], "99999999"]]:
        with pytest.raises(CommandError):
            verify(*bad)
    with pytest.raises(CommandError, match="votes in the database"):  # third voter logged, never stored
        verify(*nims, voters[2].user.username, expected=3)


def test_password_change_cancels_stale_initial_credentials(make_election):
    _e, _o, _c, voters = make_election()
    issue_credentials(None)
    user = voters[0].user
    user.refresh_from_db()
    user.set_password("Replacement-password-123!")
    user.save(update_fields=["password"])
    delivery = EmailDelivery.objects.get()
    assert delivery.status == "failed" and not delivery.encrypted_payload


def test_activated_election_name_and_opening_are_frozen(make_election):
    from django.core.exceptions import ValidationError

    election, _o, _c, _v = make_election()
    for field, value in [("name", "Changed"), ("opens_at", election.opens_at + timedelta(hours=1))]:
        changed = type(election).objects.get(pk=election.pk)
        setattr(changed, field, value)
        with pytest.raises(ValidationError):
            changed.save()


def test_sensitive_receipt_and_reset_urls_are_redacted():
    import logging

    from elections.logging import RedactSensitivePaths

    record = logging.LogRecord("django.request", logging.WARNING, "", 0, "Not Found: %s",
        ("/receipt/12345678-1234-1234-1234-123456789012/ /password-reset/abc/secret-token/",), None)
    RedactSensitivePaths().filter(record)
    message = record.getMessage()
    assert "secret-token" not in message and "12345678" not in message
