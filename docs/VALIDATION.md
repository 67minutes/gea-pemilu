# Verification status — 24 October 2026

The backbone is implemented and verified locally. **Production launch and 500-user capacity
are not certified.** No live infrastructure was provisioned and no real ITB mail was sent.

> **2 November 2026:** voting moved to in-person kiosk laptops with admin-defined sessions, a single
> whole-ballot finalize and committee Gmail delivery. The evidence below predates that change; the
> test suite covers the new flow, but the k6 smoke and browser runs must be repeated against it.

## Passed

- 45 pytest tests against isolated PostgreSQL 17.11, including concurrent submissions from one
  voter, database uniqueness/immutability, eligibility and candidate checks, pause/revocation,
  an in-flight vote across closing, frozen configuration, finalization, result visibility,
  encrypted credential delivery/retries, recovery/session invalidation, CSRF, shared throttling,
  MFA/recovery codes, reviewer permissions, bilingual screens, exports/audit, archive format/checksums,
  and background credential generation. No skipped tests or warnings in the final run.
- Django system checks, migration-drift check, Ruff, and installed dependency consistency.
- Render Blueprint validated against `https://render.com/schema/render.yaml.json`.
- Local Docker Compose configuration parsed successfully. Production configuration checks pass with
  structurally valid test environment values; this does not verify real provider credentials.
- k6 2.3.0 **two-user smoke only**: four complete iterations; 18/18 checks; zero unexpected errors.
  Both voters submitted both offices and retried without changing receipts. All four acknowledged
  receipts reconciled exactly with PostgreSQL. Local login p95 374 ms and voting-request p95 58.2 ms
  are smoke measurements, not a forecast for 500 users or managed hosting.
- Playwright Chromium desktop/mobile: Indonesian login → dashboard → ballot → review → receipt;
  English language switch preserved the receipt route; public results hid totals while open.
  Final browser page had zero console errors/warnings. Mobile viewport: 390 × 844.
- Local files in `output/playwright/` capture the landing, mobile ballot and receipt. Synthetic
  credentials and all generated runtime/test artifacts are ignored by Git.

## Required before opening production

| Gate | Status / required evidence |
|---|---|
| Docker image build and container smoke | Not run locally: Docker Desktop failed during startup. CI includes an image build. |
| Render deployment | Needs a pushed Git repository, connected hosting account and production environment values (the `*.onrender.com` address is enough). |
| Approved content and roster | Needs four real candidate profiles, the voting sessions, approved NIM/email CSV and verified domain allowlist. |
| Committee enrollment | Real committee members must provision separate MFA accounts and store recovery codes. |
| Real ITB email delivery | Committee Gmail with an app password; `send_test_email` lands in the Inbox (not Junk) of two ITB mailboxes; recovery delivery works. |
| Kiosk laptops | Each laptop enrolled in a dedicated Chrome profile; voter login refused on an unenrolled browser; idle logout and finalize → done → login observed on the real laptops. |
| Load rehearsal (optional) | Three kiosk laptops make little load. If run: `KIOSK_REQUIRED=false` on an isolated host only; login p95 below 10 seconds. |
| Vote reconciliation | If the rehearsal runs, every acknowledged ballot must match one stored vote per office with no duplicates or loss. |
| Real dump/restore rehearsal | Archive checksum tests passed; a real `pg_dump` → isolated `pg_restore` and vote/count comparison remains required. |
| Monitoring and shutdown owner | Configure hosting alerts, name an operator, and schedule export/archive/shutdown after the last session. |

The Starter web instance is sized for three kiosk laptops, not a demonstrated capacity result.
Do not activate a production election until the applicable release gates pass.
