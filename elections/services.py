import csv
import io
import re
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import AuditEvent, Candidate, Election, Office, User, Vote, Voter


class ElectionError(Exception):
    pass


def database_now():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]
    return timezone.now()


def audit(actor, action, **details):
    return AuditEvent.objects.create(actor=actor, action=action, details=details)


def lock_election():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '10s'")
    return Election.objects.select_for_update().get(pk=1)


@transaction.atomic
def activate(actor):
    election = lock_election()
    if election.activated_at:
        raise ElectionError(_("The election is already activated."))
    election.full_clean()
    if database_now() >= election.opens_at:
        raise ElectionError(_("Set a future opening time before activating."))
    if set(election.offices.values_list("slug", flat=True)) != {"kahim", "senator"}:
        raise ElectionError(_("Configure both offices before activating."))
    if any(office.candidates.count() != 2 for office in election.offices.all()):
        raise ElectionError(_("Each office must have exactly two candidates."))
    if not election.voters.exists():
        raise ElectionError(_("Import the approved voter list first."))
    election.activated_at = database_now()
    election.save(update_fields=["activated_at"])
    audit(actor, "election.activated", voter_count=election.voters.count())
    return election


@transaction.atomic
def set_paused(actor, paused):
    election = lock_election()
    if not election.activated_at or database_now() >= election.closes_at:
        raise ElectionError(_("Only an activated, unfinished election can be paused or resumed."))
    election.paused = paused
    election.save(update_fields=["paused"])
    audit(actor, "election.paused" if paused else "election.resumed")


@transaction.atomic
def submit_vote(user, office_slug, candidate_id, request_id):
    election = lock_election()
    # Lock voter access too: revocation and a submission cannot race.
    voter = Voter.objects.select_for_update().select_related("user").filter(
        user_id=user.pk, election=election, user__is_active=True, user__is_staff=False,
    ).first()
    if not voter:
        raise ElectionError(_("You are not eligible to vote."))
    office = Office.objects.filter(election=election, slug=office_slug).first()
    if not office:
        raise ElectionError(_("Invalid office."))
    existing = Vote.objects.filter(election=election, voter=voter, office=office).first()
    if existing:
        return existing, False
    now = database_now()
    if not election.activated_at or election.paused or not election.opens_at <= now < election.closes_at:
        raise ElectionError(_("Voting is not open."))
    candidate = Candidate.objects.filter(pk=candidate_id, office=office).first()
    if not candidate:
        raise ElectionError(_("Invalid candidate for this office."))
    vote = Vote.objects.create(election=election, voter=voter, office=office, candidate=candidate,
                               submitted_at=now, request_id=request_id)
    return vote, True


def published_results():
    """Public, read-only view of the results. Takes the election lock only once, after closing,
    to write the snapshot; every request before closing or after finalization is a plain read."""
    election = Election.objects.filter(pk=1).first()
    if not election or not election.activated_at:
        return None
    if election.result_snapshot is not None:
        return election.result_snapshot
    if database_now() < election.closes_at:
        return None
    return finalize_results()


@transaction.atomic
def finalize_results():
    election = lock_election()
    if not election.activated_at or database_now() < election.closes_at:
        return None
    if election.result_snapshot is not None:
        return election.result_snapshot
    eligible = election.voters.count()
    snapshot = {"eligible": eligible, "offices": []}
    counts = dict(Vote.objects.filter(election=election).values("candidate_id").annotate(
        total=Count("id")).values_list("candidate_id", "total"))
    for office in election.offices.prefetch_related("candidates"):
        candidates = [{"number": c.number, "name": c.name, "votes": counts.get(c.pk, 0)}
                      for c in office.candidates.all()]
        total = sum(c["votes"] for c in candidates)
        snapshot["offices"].append({"slug": office.slug, "candidates": candidates, "total": total,
                                     "turnout_percent": round(100 * total / eligible, 2) if eligible else 0,
                                     "tie": len({c["votes"] for c in candidates}) == 1})
    election.result_snapshot = snapshot
    election.finalized_at = database_now()
    election.save(update_fields=["result_snapshot", "finalized_at"])
    audit(None, "election.finalized", totals={o["slug"]: o["total"] for o in snapshot["offices"]})
    return snapshot


def parse_roster(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ElectionError(_("Upload a UTF-8 CSV file.")) from exc
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if (not reader.fieldnames or not {"nim", "email"}.issubset(reader.fieldnames)
            or len(set(reader.fieldnames)) != len(reader.fieldnames)):
        raise ElectionError(_("CSV must contain nim and email columns."))
    rows, nims, emails = [], set(), set()
    try:
        raw_rows = list(reader)
    except csv.Error as exc:
        raise ElectionError(_("The CSV is malformed.")) from exc
    for line, raw in enumerate(raw_rows, 2):
        if None in raw:
            raise ElectionError(_("CSV has unexpected extra columns."))
        if len(rows) >= 10000:
            raise ElectionError(_("CSV exceeds 10,000 voters."))
        nim = (raw.get("nim") or "").strip()
        email = (raw.get("email") or "").strip().lower()
        name = (raw.get("display_name") or "").strip()
        if not re.fullmatch(r"[0-9]{8}", nim) or len(name) > 150:
            raise ElectionError(_("Invalid NIM or display name on CSV line %(line)s.") % {"line": line})
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ElectionError(_("Invalid email on CSV line %(line)s.") % {"line": line}) from exc
        if email.rsplit("@", 1)[-1] not in settings.ITB_EMAIL_DOMAINS:
            raise ElectionError(_("Email domain is not an approved ITB domain."))
        if nim in nims or email in emails:
            raise ElectionError(_("Duplicate NIM or email in the CSV."))
        nims.add(nim)
        emails.add(email)
        rows.append({"nim": nim, "email": email, "display_name": name})
    if not rows:
        raise ElectionError(_("The CSV is empty."))
    if User.objects.filter(username__in=nims).exists() or User.objects.filter(email__in=emails).exists():
        raise ElectionError(_("A NIM or email already exists. Import only new voters."))
    return rows


@transaction.atomic
def import_roster(actor, rows):
    election = lock_election()
    if election.activated_at:
        raise ElectionError(_("The activated voter list is frozen."))
    # Revalidate after the preview; another import may have happened meanwhile.
    data = io.StringIO()
    writer = csv.DictWriter(data, fieldnames=["nim", "email", "display_name"])
    writer.writeheader()
    writer.writerows(rows)
    rows = parse_roster(data.getvalue().encode())
    for row in rows:
        user = User(username=row["nim"], email=row["email"])
        user.set_unusable_password()
        user.save()
        Voter.objects.create(election=election, user=user, display_name=row["display_name"])
    audit(actor, "roster.imported", count=len(rows))
    return len(rows)


@transaction.atomic
def request_credentials(actor):
    election = lock_election()
    if not election.activated_at or database_now() >= election.closes_at:
        raise ElectionError(_("Activate the election before issuing credentials."))
    election.credentials_requested_at = database_now()
    election.save(update_fields=["credentials_requested_at"])
    audit(actor, "credentials.requested")


def issue_credentials(actor, limit=10):
    """Background-only issuance: expensive hashes run outside the election lock."""
    from .mail import enqueue

    election = Election.objects.get(pk=1)
    if not election.activated_at or database_now() >= election.closes_at:
        raise ElectionError(_("Activate the election before issuing credentials."))
    count = 0
    voter_ids = list(election.voters.filter(credentials_issued_at__isnull=True, user__is_active=True).order_by("pk").values_list("pk", flat=True)[:limit])
    for voter_id in voter_ids:
        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
        hashed = make_password(password)
        with transaction.atomic():
            election = lock_election()
            if database_now() >= election.closes_at:
                break
            voter = Voter.objects.select_for_update().select_related("user").get(pk=voter_id)
            if voter.credentials_issued_at or not voter.user.is_active:
                continue
            voter.user.password = hashed
            voter.user.save(update_fields=["password"])
            enqueue(voter.user, "credentials", "GEA ITB — Kredensial pemilu / Election credentials", (
                'Pemilu HMTG "GEA" ITB\n\n'
                f"NIM: {voter.user.username}\nPassword: {password}\n"
                f"Login: {settings.PUBLIC_BASE_URL}/login/\n\n"
                f"Mulai / Opens (WIB): {timezone.localtime(election.opens_at):%d %b %Y %H:%M}\n"
                f"Selesai / Closes (WIB): {timezone.localtime(election.closes_at):%d %b %Y %H:%M}\n\n"
                "Rahasiakan password ini. Pilihan Anda terhubung dengan NIM dan hanya dapat ditinjau "
                "oleh peninjau berwenang setelah pemilu berakhir. Setiap suara bersifat final.\n\n"
                "Keep this password private. Your choices are linked to your NIM and may be reviewed "
                "by authorized reviewers after closing. Each vote is final.\n"
            ))
            voter.credentials_issued_at = timezone.now()
            voter.save(update_fields=["credentials_issued_at"])
            count += 1
    if count:
        audit(actor, "credentials.queued", count=count)
    return count


@transaction.atomic
def revoke_access(actor, voter_id):
    lock_election()
    voter = Voter.objects.select_for_update().select_related("user").get(pk=voter_id)
    voter.user.is_active = False
    voter.user.save(update_fields=["is_active"])
    audit(actor, "voter.access_revoked", voter_id=voter.pk)


def delivery_summary():
    from .models import EmailDelivery

    rows = list(EmailDelivery.objects.values("status").annotate(count=Count("id")))
    for row in rows:
        row["status_label"] = EmailDelivery.Status(row["status"]).label
    return rows


def cleanup_expired_payloads():
    from .models import EmailDelivery

    return EmailDelivery.objects.filter(status="pending", created_at__lt=timezone.now() - timedelta(hours=23)).update(
        status="failed", encrypted_payload="", last_error="Expired; initiate a password reset")
