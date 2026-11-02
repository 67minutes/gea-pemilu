# Deployment and election operations

## Deploy to Render

The infrastructure follows the [Render deployment skill](C:/Users/mauli/.codex/skills/render-deploy/SKILL.md)
and the current [Blueprint specification](https://render.com/docs/blueprint-spec).
Publishing requires a GitHub/GitLab/Bitbucket repository and a connected Render account.

1. Put this source in your chosen Git repository and push it. Do not include `.env`, `output/`,
   credential files, or database archives. This workspace initially had no Git repository or remote.
2. Create a Render Blueprint from the repository. Optionally install the [Render CLI](https://render.com/docs/cli),
   authenticate with `render login`, and run `render blueprints validate render.yaml` first.
3. Review `render.yaml`: one Singapore **Starter** web instance (2 workers × 2 threads), PostgreSQL
   **Basic-256mb** with transaction pooling, and a once-per-minute outbox cron. Three kiosk laptops
   create very little load; the managed database, not the web size, holds the election record. The shared `gea-secrets` group generates `SECRET_KEY` and
   `OUTBOX_KEY` once for both web and cron. Confirm Render-generated `OUTBOX_KEY` is URL-safe base64
   for 32 bytes; if not, replace it with a Fernet-generated key before queueing any messages.
4. Fill launch inputs in the web service: `PUBLIC_BASE_URL` (HTTPS origin), `ALLOWED_HOSTS`
   (hostnames without schemes), `CSRF_TRUSTED_ORIGINS` (HTTPS origins), `DEFAULT_FROM_EMAIL`
   (the committee Gmail address, e.g. `Panitia Pemilu GEA <pemilu.gea@gmail.com>`), `EMAIL_HOST_USER`,
   `EMAIL_HOST_PASSWORD` (the Gmail app password), and the exact `ITB_EMAIL_DOMAINS` allowlist.
   The free `*.onrender.com` address is enough; no domain purchase is needed.
   Cron references web email settings; deployment will fail if they are missing. Never replace
   `OUTBOX_KEY` while messages are queued: their encrypted content requires the original key.
5. Set up the committee Gmail (see **Committee Gmail** below), then run
   `python manage.py send_test_email <address>` from the Render shell to two ITB inboxes.
6. Apply the Blueprint. Docker compiles translations and static assets. Pre-deploy runs the production
   checks and migrations using the direct database URL; normal traffic uses the managed pooler.
7. Check `/health/ready/`, login, cookies, CSRF, password recovery, and MFA. Provision committee accounts
   through the trusted Render shell. No voter credentials should appear in service logs.

The production check intentionally refuses SQLite, file/SMTP email, missing outbox encryption,
an HTTP origin, an example sender, and standard Django deployment-check failures. `TRUST_PROXY=true`
is for Render's controlled TLS proxy, never an arbitrary publicly exposed development server.
With it, login throttling keys on the client address Render's proxy appends to `X-Forwarded-For`
(`TRUSTED_PROXY_HOPS=1`). Raise the hop count only if another proxy or CDN is placed in front of Render.
Database public networking is disabled by default. Use the Render shell or explicitly allow only
your operator IP temporarily for backup/recovery; remove that access afterward.

## Committee Gmail

Credentials and password resets are sent through one committee Gmail account over SMTP.

1. Create a dedicated committee Gmail account and turn on **2-Step Verification**.
2. Create an **app password** (Google Account → Security → App passwords). Store it only in Render as
   `EMAIL_HOST_PASSWORD`; never in Git, chat or a shared document.
3. Run `send_test_email` to at least two ITB inboxes. Check that the message lands in **Inbox, not Junk**,
   and that the sender name and reply address look right.
4. Gmail allows roughly 500 messages a day; the app stops at `EMAIL_DAILY_LIMIT=450` in any rolling
   24 hours and keeps `EMAIL_RESET_RESERVE=50` of that for password resets. Queue credentials at least
   two days before the first session for more than ~400 voters. The committee page shows queued,
   sent and failed counts.
5. **Never change the Gmail password during the election.** Changing it revokes every app password and
   stops all mail until a new app password is set in Render.

Fallback: with a verified sending domain, set `EMAIL_TRANSPORT=resend`, `RESEND_API_KEY` and
`RESEND_WEBHOOK_SECRET`; configure SPF/DKIM/DMARC and a Resend webhook to
`https://your-host/webhooks/resend/` for delivered, bounced, complained and failed events, with
open/click tracking off.

## Kiosk laptops

Voting happens in person on committee laptops. Voters can log in only on enrolled laptops
(`KIOSK_REQUIRED=true`); committee login and password reset work anywhere.

1. On each laptop, create a **dedicated Chrome profile** and start it in kiosk mode, not incognito
   (incognito loses the enrollment cookie when closed):
   `chrome.exe --user-data-dir=C:\gea-kiosk --kiosk https://your-host/login/`
2. In that profile, turn off password saving, address/payment autofill and the translate prompt,
   so no voter's NIM or password is offered to the next voter.
3. Log in to the committee on that laptop, open **Committee → Kiosk laptops** (`/committee/kiosks/`),
   enroll it with a label (`Laptop 1`, …), then **log out of the committee** before any voter uses it.
   Enrollment lasts `KIOSK_COOKIE_AGE` (60 days by default).
4. Place laptops so screens face away from the queue; use privacy screens where possible. One
   voter at a time per laptop.
5. Each voter logs in, chooses a candidate for every office, reviews, and finalizes. Finalizing
   logs them out and shows a thank-you page that returns to login after 10 seconds. A session left
   idle for `KIOSK_IDLE_SECONDS` (180) ends automatically after a 30-second on-screen warning.
   A voter who logs in on a second laptop ends their session on the first.
6. **Laptop failure:** revoke it on the Kiosk laptops page (its voter session ends immediately),
   enroll a spare, and continue. If more than one laptop fails, pause voting until they are replaced.
7. **Network:** use the venue internet with a mobile hotspot on a *different* carrier as backup.
   If the connection drops during finalize, the voter logs in again: the dashboard shows
   "You have voted" if the ballot was saved, or the ballot again if it was not. A ballot is saved
   whole or not at all.

## Voting sessions and monitoring

Voting happens only inside the sessions configured on the committee page (times in WIB). Before
activation, sessions can be added and removed; after activation you can only add future sessions
or extend a session that has not ended. Voting closes for good at the end of the last session.

Initial credential generation runs in the background at up to 10 voters per minute, within the
Gmail daily budget; budget time to resolve delivery failures before the first session.

One instance × two workers × two threads bounds web database connections to 4, plus one cron
connection and migration/operator headroom. The application sets transaction-local lock/statement
timeouts compatible with PgBouncer transaction pooling. Enable the pool before the election;
enabling it later can restart the database. Do not weaken password hashing. The rehearsal in
`loadtest/README.md` is optional at this scale; if run, use a separate database.

Render dashboards: inspect CPU, memory, response latency, HTTP errors, database connections/pool waits,
and outbox failure counts. Configure notifications to the committee using Render's account settings.
Application logs contain route patterns, statuses and durations, not vote choices, NIMs, passwords,
reset tokens or ballot contents. The campus may share a public IP: account throttling is strict (10
attempts / five-minute bucket), while the shared-IP ceiling is 2,000 / five minutes. These database
counters work across instances. Provider/CDN abuse controls can supplement them.

Retain the measured passing instance sizes. During voting, do not deploy schema changes or modify
configuration. Pausing stops new submissions; already committed votes and the closing time remain.
Revoke voter access only for a documented incident. There is no ballot editing interface.

## Timeline

- Before the first session: deploy, verify backups, provision committee accounts, configure
  candidates and sessions, import the approved list, activate, queue credentials (allow two days
  for Gmail's daily limit), check inbox delivery, and enroll the kiosk laptops.
- During sessions: staff the laptops, monitor the committee page, and respond to incidents.
- After the last session: verify finalized totals, review authorized exports, archive, verify, shut down.

Session dates and times are configuration inputs and displayed in WIB. Pauses never extend a
session. Ties stay marked as ties for the committee to resolve under its rules.

## Back up and rehearse recovery

Paid PostgreSQL includes PITR, but [Render's Hobby recovery window is three days](https://render.com/docs/postgresql-backups).
Verify this in the account before relying on it. Download logical backups before they expire.

Use PostgreSQL **17** client tools for this schema (or a supported newer client). With a trusted direct
connection URL in the environment, after closing:

```powershell
pg_dump --format=custom --no-owner --file=output/election.dump $env:DATABASE_MIGRATION_URL
uv run python manage.py archive_election output/election.dump --output output/election.archive --key-file output/election.fernet
uv run python scripts/verify_archive.py output/election.archive --key-file output/election.fernet
```

The archive includes the dump, finalized results, audit log and SHA-256 manifest. Download aggregate
CSV and (if authorized/required) identity-linked CSV through the committee interface. Keep the archive
and key on separate access-controlled storage. Plain dumps/exports contain private information;
remove working copies after archive verification and a successful restore rehearsal.

Restore **only into a new, isolated empty database**, never over the active database:

```powershell
uv run python scripts/verify_archive.py output/election.archive --key-file output/election.fernet --extract-to output/restore-check
pg_restore --no-owner --no-acl --dbname=$env:ISOLATED_RESTORE_URL output/restore-check/database.dump
```

Set `DATABASE_URL` to the isolated restore URL in a separate terminal/environment and inspect counts,
votes, configuration, audit history, and final result snapshot. Match exported aggregate counts.
Disable email cron on recovery copies so old pending messages are not sent. This functional restore
check is separate from archive checksum verification.

If a live incident requires PITR, pause access first. Restore into a separate instance and reconcile
acknowledged ballots after the recovery point; a backup can be missing votes already acknowledged
to voters. Do not reopen until the committee resolves that gap. Do not advertise automatic zero-loss
recovery: this small deployment uses a single managed database without an HA standby.

## Shutdown and retention

After the election, download/verify archives and exports, rehearse restore, then remove the web, cron and
database resources from Render. Deleting a Blueprint alone may not remove all resources; verify the
dashboard and billing. Revoke the committee Gmail app password (and any Resend credentials). The application
has no scheduled automatic teardown that could destroy election records prematurely.

The committee owns archive retention and access under its election/dispute rules. Keep the key and
archive together only during an authorized restore. After shutdown, results are available only in
the downloaded aggregate files unless the committee separately publishes a static copy.

## Expected cost

Starter web, Basic-256mb PostgreSQL and a once-per-minute cron, billed by the time they run; Gmail
and the `*.onrender.com` address are free. Reconfirm current prices on Render's pricing page before
applying. This is an estimate, not a spending cap or a measured capacity guarantee.
