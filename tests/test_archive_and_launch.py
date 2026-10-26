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
from elections.services import issue_credentials, submit_vote

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


def test_rehearsal_receipts_reconcile(make_election, tmp_path):
    _e, _o, candidates, voters = make_election(name="DEMO — Rehearsal")
    records = []
    for slug in ["kahim", "senator"]:
        vote, _ = submit_vote(voters[0].user, slug, candidates[slug][0].pk, uuid.uuid4())
        records.append({"type": "receipt", "nim": voters[0].user.username, "office": slug,
                        "receipt_id": str(vote.receipt_id)})
    path = tmp_path / "receipts.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))
    call_command("verify_rehearsal", str(path), expected_voters=1, stdout=StringIO())
    path.write_text(json.dumps(records[0]))
    with pytest.raises(CommandError, match="unique acknowledged"):
        call_command("verify_rehearsal", str(path), expected_voters=1)


def test_password_change_cancels_stale_initial_credentials(make_election):
    _e, _o, _c, voters = make_election()
    issue_credentials(None)
    user = voters[0].user
    user.refresh_from_db()
    user.set_password("Replacement-password-123!")
    user.save(update_fields=["password"])
    delivery = EmailDelivery.objects.get()
    assert delivery.status == "failed" and not delivery.encrypted_payload


def test_launch_check_rejects_non_72_hour_election(make_election):
    from django.core.exceptions import ValidationError

    election, _o, _c, _v = make_election(active=False)
    election.closes_at += timedelta(hours=1)
    with pytest.raises(ValidationError):
        election.save()


def test_sensitive_receipt_and_reset_urls_are_redacted():
    import logging

    from elections.logging import RedactSensitivePaths

    record = logging.LogRecord("django.request", logging.WARNING, "", 0, "Not Found: %s",
        ("/receipt/12345678-1234-1234-1234-123456789012/ /password-reset/abc/secret-token/",), None)
    RedactSensitivePaths().filter(record)
    message = record.getMessage()
    assert "secret-token" not in message and "12345678" not in message
