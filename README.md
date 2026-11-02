# HMTG “GEA” ITB — Pemilu

Django 5.2 LTS / PostgreSQL backbone for separate Ketua Himpunan (kahim) and senator voting. Indonesian and
English screens, approved CSV voter list, emailed random credentials, MFA-protected committee,
in-person voting on enrolled committee laptops during admin-defined sessions, a whole ballot
reviewed and finalized at once, and automatic aggregate results after the last session closes.

**Identity-linked ballots:** authorized reviewers can export individual choices after closing.
Voters see this notice before logging in and finalizing. Database operators remain trusted.

## Run locally on Windows

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). From this directory:

```powershell
uv sync --frozen
Copy-Item .env.example .env
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated value in `.env` as `OUTBOX_KEY`, and replace `SECRET_KEY` with a fresh random
secret. The local `.env` is ignored by Git. Default local email transport writes messages to
`output/mail/` for private inspection, rather than sending to any real address.

With Docker running:

```powershell
docker compose up -d db mail
```

Set `DATABASE_URL=postgresql://gea:local-development-only@localhost:55432/gea` in `.env`.
If Docker Desktop is unavailable, the optional Windows helper downloads checksum-verified
PostgreSQL 17 test binaries into `output/postgres/` and starts an isolated local server:

```powershell
uv run python scripts/windows_postgres.py start
```

Then:

```powershell
uv run python scripts/compile_translations.py
uv run python manage.py migrate
uv run python manage.py seed_demo --open
uv run python manage.py runserver 127.0.0.1:8000 --noreload
```

`.env.example` sets `KIOSK_REQUIRED=false`, so demo voters can log in from any browser. Set it to
`true` to try kiosk enrollment locally (enroll the browser at `/committee/kiosks/` first).

Open [localhost:8000](http://localhost:8000). Synthetic voter credentials are in
`output/demo-voters.json`; the seed command refuses a nonempty database. Never import real
voters into a demo database. SQLite is available when `DATABASE_URL` is unset for quick UI
inspection, but it does **not** exercise PostgreSQL triggers or row locking.

Optional all-container setup:

```powershell
docker compose build
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py seed_demo --open
docker compose up -d web outbox
```

Container emails arrive in [Mailpit](http://localhost:8025). With host-based Django, use the
file transport or set `EMAIL_TRANSPORT=smtp`, `EMAIL_HOST=localhost`, `EMAIL_PORT=1025`.
Process the host-based queue with `uv run python manage.py process_outbox`.

## Set up a real election

Use a fresh PostgreSQL database, not the demo. Provision committee accounts interactively:

```powershell
uv run python manage.py create_committee committee-name committee-email@itb.ac.id --review-ballots
```

This prompts for a strong password, displays a private authenticator provisioning URI and manual-entry secret,
requires a valid authenticator code, and outputs eight one-use recovery codes. Omit
`--review-ballots` for operators who must not access voter-level exports. Store recovery codes
offline. Never share committee accounts. Committee password recovery is an operator procedure,
not the public voter recovery form.

Sign in at `/committee/admin/login/`. On first login, enter credentials, then choose the
registered authenticator device and enter its code. Recovery codes use the separate
"Recovery codes" device. The Django admin and `/committee/` both enforce MFA.

1. In configuration, create the election. On `/committee/`, add its voting sessions (WIB); the
   first session's start and the last session's end become the opening and closing times.
2. Add `kahim` and `senator`, each with candidates numbered 1 and 2 and profiles in both languages.
3. From `/committee/`, preview and confirm the approved UTF-8 CSV. Required `nim,email`; optional
   `display_name`. NIM is an eight-digit string. The allowlist of ITB email domains is configurable;
   verify the actual domains against the approved roster before import.
4. Activate. This freezes the roster and candidate content. Afterwards, sessions can only be
   added in the future or extended before they end.
5. Queue credentials. The background job creates up to 10 independent 20-character passwords per minute
   and sends pending emails within the committee Gmail's daily limit (`EMAIL_DAILY_LIMIT`), so more than
   ~400 voters take two days. The email lists the voting sessions. Verify actual delivery.
   Repeating the action only requests credentials for voters whose initial credentials have not been issued.
6. Inspect provider acceptance, delivery, and bounce status. For permanently failed/expired initial
   emails, have the voter use recovery at their approved address after delivery is fixed. Old queued
   messages are never regenerated silently. Do not reset the election to repair delivery.
7. Enroll each voting laptop at `/committee/kiosks/` and log out of the committee on it. See
   [Kiosk laptops](docs/OPERATIONS.md#kiosk-laptops).

## Verify and deploy

```powershell
uv run pytest -q
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
```

Run tests with PostgreSQL configured: the suite includes real concurrent submissions, SQL-level
immutability checks, and finalization waiting for an in-flight closing-boundary vote.

See [deployment and election operations](docs/OPERATIONS.md),
[capacity rehearsal](loadtest/README.md), and [verification status](docs/VALIDATION.md).
The Render Blueprint provisions paid resources; applying it incurs hosting charges.
This workspace has not been published or deployed.

## Structure and contracts

- `elections/services.py`: short transactions serialize voting, activation, pause, revocation,
  and finalization on the election row. Database time determines whether a voting session is open.
- PostgreSQL triggers guard immutable votes/audit records, configuration freeze, candidate-office
  consistency, eligibility, deadlines, and finalized snapshots, including bulk ORM writes.
- POST `/vote/`: CSRF-protected fields `office_<slug>` (one candidate per office), UUID `request_id`,
  and `action=review|change|finalize`. The voter comes exclusively from the session. `finalize` writes
  every office in one transaction, logs the voter out and redirects to `/done/`, which shows no choice;
  a voter who already voted is sent to the dashboard and nothing is replaced.
- Voter sessions require an enrolled kiosk (`KIOSK_REQUIRED`), end after `KIOSK_IDLE_SECONDS` without
  activity (`/kiosk/ping/` keeps an active one alive), and a newer login ends an older session.
- `/results/` finalizes under the same lock after closing; identities are never in public results.
- `/committee/export/aggregate|audit|ballots/`: after-close exports with role checks and access audit.
- `/health/live/` and `/health/ready/`: process and database readiness; neither discloses secrets.
- `/webhooks/resend/`: signed provider delivery/failure events; duplicate events are safe.

No browser analytics, externally hosted fonts or scripts, accounts created by public registration, local application
state, or emails in the voting request path. The theme (p5.js backgrounds, OFL serif fonts) is self-hosted under `static/`.
