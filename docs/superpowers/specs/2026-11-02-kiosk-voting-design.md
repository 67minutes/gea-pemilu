# Kiosk Voting — Design

**Date:** 2026-11-02 · **Status:** agreed with the committee; hosted on Render (web hosting)

## Context

Voting is now in person on three committee laptops instead of on voters' own devices. Every
voter logs in on a laptop, fills in the whole ballot, reviews it, finalizes, and is logged out.
Admins choose the voting dates and hours. Credentials stay emailed passwords, now sent from a
dedicated committee Gmail account:
- SMTP with an app password;
- a daily cap of about 450 to stay under Gmail's ~500/day;
- password resets sent before credentials.

ITB Microsoft 365 rejected SMTP login (535 5.7.3), so a no-domain setup is the goal: the app is
served on the free `*.onrender.com` address.

## Requirements

### 1. Voting windows
- An election has one or more **voting windows** (`starts_at`, `ends_at`), half-open
  `[starts_at, ends_at)`, in WIB. The fixed 72-hour rule is removed.
- Windows never overlap (back-to-back windows are allowed).
- `Election.opens_at` / `closes_at` become derived: earliest window start / latest window end.
  Results, finalization, exports and credential cut-offs keep using `closes_at`.
- Voting is open when the election is activated, not paused, and now is inside a window.
- New public state `between`: activated, after `opens_at`, before `closes_at`, outside every
  window. Pages show the next session's start.
- **Before activation:** committee operators may add and remove windows freely.
- **After activation:** windows are append/extend-only:
  - add a window only if it starts in the future and not before `opens_at`;
  - extend a window's end only if that window has not ended yet;
  - never remove a window or move a start.
  - All changes are refused once the election is closed. Every change is audited.
- Enforced in the service layer and, on PostgreSQL, by database triggers (same pattern as
  migration 0002).
- Windows are managed on the committee page (`/committee/`), not in the Django admin.

### 2. Whole-ballot review and atomic finalize
- One ballot page shows every office. The voter picks one candidate per office.
- A review page lists all picks with "Change" and "Finalize" buttons.
- Finalize writes every office's vote in one transaction: all or nothing. A retry or a second
  finalize returns the existing votes and never adds or replaces any.
- If voting closes or is paused while the voter is reviewing, finalize is refused, nothing is
  saved, and the voter stays logged in with a message.

### 3. Hand-off between voters
- Finalize logs the voter out **before** the thank-you page renders.
- The thank-you page (`/done/`) shows no candidate, receipt ID or NIM. It has a
  "Selesai / Next voter" button to the login page and returns there on its own after 10 s.
- **Idle timeout:** a voter session ends after `KIOSK_IDLE_SECONDS` (default 180) without
  activity.
  - The server enforces it.
  - Client JS shows a countdown for the last 30 s and pings the server on pointer/keyboard
    activity, so reading candidate profiles doesn't time out.
- **One active session per voter:** a new login on another laptop ends the older session.
- All voter pages stay `never_cache`. After logout, Back must not show a ballot.

### 4. Kiosk-only voter login
- A committee operator enrolls each laptop once at `/committee/kiosks/`. This sets a signed,
  long-lived `gea_kiosk` cookie tied to a revocable `KioskDevice` row.
- Voter login, and every authenticated voter request, require a valid, unrevoked kiosk
  cookie when `KIOSK_REQUIRED` is true (default true; tests and load rehearsals turn it off).
- These don't require the cookie:
  - committee login and pages;
  - password reset (voters may reset from their phone);
  - public pages.

### 5. Operations
- `docs/OPERATIONS.md` gains a kiosk setup checklist:
  - a dedicated Chrome profile in kiosk mode, **not** incognito, so the enrollment cookie
    survives a restart;
  - password saving and autofill off;
  - enrollment;
  - privacy screens and booth placement;
  - what to do when a laptop fails.
- The load test and `verify_rehearsal` move to the single-finalize flow.
- **Hosting stays on Render over HTTPS**, sized down for three kiosks:
  - one Starter web instance;
  - a Basic-256mb Postgres;
  - no two-instance capacity gate.
- The venue needs internet, with a mobile hotspot on a different carrier as backup.

## Out of scope / open
- No venue field; the committee announces the location through its own channels.
- Desk-issued one-time tokens were considered and rejected: credentials stay emailed.
