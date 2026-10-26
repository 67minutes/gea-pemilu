# Deployment and seven-day operations

## Deploy to Render

The infrastructure follows the [Render deployment skill](C:/Users/mauli/.codex/skills/render-deploy/SKILL.md)
and the current [Blueprint specification](https://render.com/docs/blueprint-spec).
Publishing requires a GitHub/GitLab/Bitbucket repository and a connected Render account.

1. Put this source in your chosen Git repository and push it. Do not include `.env`, `output/`,
   credential files, or database archives. This workspace initially had no Git repository or remote.
2. Create a Render Blueprint from the repository. Optionally install the [Render CLI](https://render.com/docs/cli),
   authenticate with `render login`, and run `render blueprints validate render.yaml` first.
3. Review `render.yaml`: Singapore web instance `1c-2g`, PostgreSQL `0.5c-1g` with transaction pooling,
   and a once-per-minute outbox cron. The shared `gea-secrets` group generates `SECRET_KEY` and
   `OUTBOX_KEY` once for both web and cron. Confirm Render-generated `OUTBOX_KEY` is URL-safe base64
   for 32 bytes; if not, replace it with a Fernet-generated key before queueing any messages.
4. Fill launch inputs in the web service: `PUBLIC_BASE_URL` (HTTPS origin), `ALLOWED_HOSTS`
   (hostnames without schemes), `CSRF_TRUSTED_ORIGINS` (HTTPS origins), `DEFAULT_FROM_EMAIL`,
   `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, and the exact `ITB_EMAIL_DOMAINS` allowlist.
   Cron references web email settings; deployment will fail if they are missing. Never replace
   `OUTBOX_KEY` while messages are queued: their encrypted content requires the original key.
5. Verify the sending domain with SPF/DKIM and configure DMARC. In Resend, create a webhook to
   `https://your-host/webhooks/resend/` for delivered, bounced, complained and failed email events.
   Disable open/click tracking for credential and recovery mail. Test actual ITB inbox placement.
6. Apply the Blueprint. Docker compiles translations and static assets. Pre-deploy runs the production
   checks and migrations using the direct database URL; normal traffic uses the managed pooler.
7. Check `/health/ready/`, login, cookies, CSRF, password recovery, and MFA. Provision committee accounts
   through the trusted Render shell. No voter credentials should appear in service logs.

The production check intentionally refuses SQLite, file/SMTP email, missing outbox encryption,
an HTTP origin, an example sender, and standard Django deployment-check failures. `TRUST_PROXY=true`
is for Render's controlled TLS proxy, never an arbitrary publicly exposed development server.
Database public networking is disabled by default. Use the Render shell or explicitly allow only
your operator IP temporarily for backup/recovery; remove that access afterward.

## Capacity gate and monitoring

Before opening, manually change the web service to **two instances**, run the rehearsal in
`loadtest/README.md`, and retain passing evidence. Manual scaling is deliberate; no paid
autoscaling workspace is required. A rehearsal must use a separate database.

Initial credential generation runs in the background at up to 10 voters per minute; budget at
least 50 minutes for 500 voters and additional time to resolve delivery failures before opening.

Default two instances × two workers × four threads bounds web database connections to 16,
plus one cron connection and migration/operator headroom. Use `WEB_WORKERS` / `WEB_THREADS` to tune
only after measuring. The application sets transaction-local lock/statement timeouts compatible
with PgBouncer transaction pooling. Enable the pool before the election; enabling it later can
restart the database. Do not weaken password hashing to make a load test pass.

Render dashboards: inspect CPU, memory, response latency, HTTP errors, database connections/pool waits,
and outbox failure counts. Configure notifications to the committee using Render's account settings.
Application logs contain route patterns, statuses and durations, not vote choices, NIMs, passwords,
reset tokens or receipt URLs. The campus may share a public IP: account throttling is strict (10
attempts / five-minute bucket), while the shared-IP ceiling is 2,000 / five minutes. These database
counters work across instances. Provider/CDN abuse controls can supplement them.

Retain the measured passing instance sizes. During voting, do not deploy schema changes or modify
configuration. Pausing stops new submissions; already committed votes and the closing time remain.
Revoke voter access only for a documented incident. There is no ballot editing interface.

## Timeline

- Days 1–2: deploy, rehearse, verify backups, provision committee, configure candidates,
  preview/import approved list, activate, distribute credentials, check mailbox delivery.
- Days 3–5: vote for exactly 72 hours; monitor and respond to incidents.
- Days 6–7: verify finalized totals, review authorized exports, archive, verify, shut down.

Exact dates are configuration inputs and displayed in WIB. A 72-hour window is enforced;
pauses never extend it. Ties stay marked as ties for the committee to resolve under its rules.

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
receipts, configuration, audit history, and final result snapshot. Match exported aggregate counts.
Disable email cron on recovery copies so old pending messages are not sent. This functional restore
check is separate from archive checksum verification.

If a live incident requires PITR, pause access first. Restore into a separate instance and reconcile
acknowledged receipts after the recovery point; a backup can be missing votes already acknowledged
to voters. Do not reopen until the committee resolves that gap. Do not advertise automatic zero-loss
recovery: this small deployment uses a single managed database without an HA standby.

## Shutdown and retention

After day 7, download/verify archives and exports, rehearse restore, then remove the web, cron and
database resources from Render. Deleting a Blueprint alone may not remove all resources; verify the
dashboard and billing. Cancel Resend renewal and remove sender API/webhook credentials. The application
has no scheduled automatic teardown that could destroy election records prematurely.

The committee owns archive retention and access under its election/dispute rules. Keep the key and
archive together only during an authorized restore. After shutdown, results are available only in
the downloaded aggregate files unless the committee separately publishes a static copy.

## Expected cost

Plan on approximately US$35–60 for the week, excluding the domain, taxes and extra load-test capacity;
reconfirm provider quotes before applying. Web scaling is prorated and Resend Pro is a monthly charge.
This is an estimate, not a spending cap or a measured capacity guarantee.
