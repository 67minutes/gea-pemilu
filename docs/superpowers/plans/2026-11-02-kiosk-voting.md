# Kiosk Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the remote 72-hour voting app into in-person kiosk voting on three laptops:
- admin-defined voting windows;
- whole-ballot review with one atomic finalize;
- automatic logout and idle timeout;
- voter login only on enrolled laptops.

**Architecture:**
- **Windows:** a `VotingWindow` table replaces the fixed window. `Election.opens_at`/`closes_at` stay, as a derived envelope, so results, exports and credential code keep working.
- **Finalize:** `submit_ballot()` replaces per-office `submit_vote()`.
- **Kiosk rules:** a new `elections/kiosk.py` holds the kiosk cookie, enrollment and one `KioskMiddleware`, which enforces device, idle timeout and single session for authenticated voters.
- **Database guards:** PostgreSQL triggers are updated to match, following migration 0002.

**Tech Stack:** Django 5, pytest-django, PostgreSQL triggers (SQLite in dev/tests), plain JS (no build step), Django i18n (`id` default, `en`).

**Spec:** `docs/superpowers/specs/2026-11-02-kiosk-voting-design.md`

## Global Constraints

- Every new user-facing string is wrapped for translation and gets an Indonesian `msgstr` in `locale/id/LC_MESSAGES/django.po`. Then run `uv run python scripts/compile_translations.py`.
- Every task ends green on these commands:
  - `uv run pytest -q`
  - `uv run ruff check .` (line length 110)
  - `uv run python manage.py makemigrations --check --dry-run`
- Times are stored in UTC and shown in WIB (`Asia/Jakarta`), formatted like the existing `"d M Y, H:i"`.
- Windows are half-open `[starts_at, ends_at)`.
- Every committee mutation calls `services.audit(actor, "<noun>.<verb>", ...)`.
- Settings read from the environment with defaults:
  - `KIOSK_REQUIRED` (bool, default `true`);
  - `KIOSK_IDLE_SECONDS` (int, default `180`);
  - `KIOSK_COOKIE_AGE` (int seconds, default `60*24*3600`);
  - `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (default `""`);
  - `EMAIL_DAILY_LIMIT` (int, default `0` = unlimited; Gmail deploys set `450`);
  - `EMAIL_RESET_RESERVE` (int, default `50`).
- `tests/conftest.py` autouse settings set `KIOSK_REQUIRED = False`. Tests that need it turn it on.
- Do not store the kiosk device on `Vote` (it would weaken ballot secrecy).

## Review Focus

1. **Window edges:** finalize at exactly `ends_at` is refused; at `starts_at` it is accepted. Between two windows, state is `between` and finalize is refused. *(Task 1, Task 4 tests)*
2. **Voting closes or is paused mid-review:** finalize is refused, no partial rows are written, and the voter stays logged in with a message. *(Task 4)*
3. **Double finalize** (double-click, or the same voter on two laptops): the second call returns the existing votes and creates nothing. The older session is ended. *(Task 4, Task 5)*
4. **Back button after auto-logout:** `/vote/` redirects to login, and voter responses carry `no-store`. *(Task 4)*
5. **Long reading without clicks:** activity pings keep the server session alive past `KIOSK_IDLE_SECONDS`. Without pings, it expires. *(Task 5)*

---

### Task 0: Send credential email through a committee Gmail account

**Files:**
- Modify: `elections/mail.py` (extract `deliver`, add the `gmail` transport, daily cap, reset priority)
- Modify: `elections/models.py` (`EmailDelivery.sent_at`), migration `0006_emaildelivery_sent_at`.
- Modify: `elections/management/commands/process_outbox.py` (enqueue credentials only within the remaining budget)
- Create: `elections/management/commands/send_test_email.py`
- Modify: `elections/checks.py`, `config/settings.py`, `.env.example`, `render.yaml`
- Test: `tests/test_mail.py`

**Interfaces:**
- Produces:
  - `mail.deliver(payload: dict) -> str`: sends one `{"to", "subject", "text"}` payload through the configured transport and returns a provider id.
  - `mail.remaining_daily_budget() -> int | None`: `None` when `EMAIL_DAILY_LIMIT == 0`.
  - `EMAIL_TRANSPORT="gmail"`, allowed in production.

- [ ] **Step 1: Write failing tests in `tests/test_mail.py`**

```python
def test_gmail_transport_uses_authenticated_tls(settings, make_election):
    # EMAIL_TRANSPORT="gmail", EMAIL_HOST_USER="pemilu.gea@gmail.com", EMAIL_HOST_PASSWORD="app-pass",
    # DEBUG=False; patch elections.mail.SMTPBackend; enqueue one job; process_one()
    # SMTPBackend called with host="smtp.gmail.com", port=587, use_tls=True,
    # username="pemilu.gea@gmail.com", password="app-pass", timeout=10
    # delivery.status == "sent", sent_at set, provider_id == f"gmail-{delivery.pk}", payload cleared

def test_daily_limit_keeps_extra_jobs_pending(settings, make_election):
    # EMAIL_DAILY_LIMIT=2, file transport, 3 jobs -> process_one() True, True, then False;
    # third job still "pending" with attempts == 0

def test_sent_older_than_24h_frees_budget(settings, make_election):
    # two jobs sent with sent_at = now-25h -> remaining_daily_budget() == EMAIL_DAILY_LIMIT

def test_password_resets_go_before_credentials(settings, make_election):
    # credentials job created first, reset job second; process_one() sends the reset first

def test_outbox_enqueues_credentials_only_within_budget(settings, make_election):
    # make_election(voter_count=10); request_credentials(None); EMAIL_DAILY_LIMIT=5, EMAIL_RESET_RESERVE=2
    # call_command("process_outbox", limit=0) -> EmailDelivery(kind="credentials").count() == 3

def test_production_check_accepts_configured_gmail(settings):
    # transport gmail with user, password, DEFAULT_FROM_EMAIL "GEA Pemilu <pemilu.gea@gmail.com>"
    # -> no gea.E003/E005/E006; empty password -> gea.E003; From not matching user -> gea.E005
```

  The existing Resend tests in `test_accounts_and_operations.py` must keep passing unchanged.

- [ ] **Step 2: Run** `uv run pytest tests/test_mail.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement the transport in `mail.py`**
  - Move the per-transport branch out of `process_one` into `deliver(payload)`.
  - `gmail` builds `SMTPBackend(host="smtp.gmail.com", port=587, use_tls=True, username=settings.EMAIL_HOST_USER, password=settings.EMAIL_HOST_PASSWORD, timeout=settings.EMAIL_TIMEOUT)`. It sends from `DEFAULT_FROM_EMAIL` and returns `f"gmail-{...}"`. Pass the delivery pk in, or let `process_one` set it.
  - The DEBUG-only rule still applies to `file`/`smtp`.
  - Keep the "never log exception bodies" behavior: an SMTP auth error can echo the username.

- [ ] **Step 4: Implement the daily cap and reset priority**
  - **Budget:** `remaining_daily_budget()` counts `EmailDelivery` with `sent_at >= now - 24h`.
  - **When the budget is spent:** `process_one` returns `False` before taking a job.
  - **On success:** set `sent_at`.
  - **Reset priority:** order jobs with `Case(When(kind="reset", then=0), default=1)`, then `created_at`.

- [ ] **Step 5: Implement `process_outbox`**
  - Call `issue_credentials(None, limit=n)` only with `n = min(10, budget - EMAIL_RESET_RESERVE - pending_count)`, and only when `n > 0`.
  - With no budget configured, keep today's `limit=10`.
  - This stops queued credentials from hitting the 23-hour expiry while waiting for tomorrow's budget.

- [ ] **Step 6: Implement `send_test_email <address>`.** It calls `deliver()` directly with a fixed bilingual test message, prints the provider id, and never prints settings.

- [ ] **Step 7: Implement the production checks, settings and `render.yaml`**
  - **Checks:**
    - E003 passes for `resend` with a key, or for `gmail` with user and password.
    - E005 also requires the From address to equal `EMAIL_HOST_USER` under Gmail.
    - E006 applies only under `resend`.
  - **Settings:** read the new variables.
  - **`render.yaml`:**
    - web: `EMAIL_TRANSPORT=gmail`, `EMAIL_DAILY_LIMIT=450`, `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` with `sync: false`;
    - cron: references those, replacing the Resend references, and uses `--limit 10`.

- [ ] **Step 8: Run** `uv run pytest -q` and `DEBUG=false` `uv run python manage.py check --deploy` with Gmail env set. Expected: PASS, and no gea.E003/E005/E006.

- [ ] **Step 9: Commit** `git commit -m "Send credential email through a committee Gmail account with a daily cap"`

---

### Task 1: Voting windows model, services and database guards

**Files:**
- Modify: `elections/models.py` (Election fields/`clean`/`state`, new `VotingWindow`)
- Modify: `elections/services.py` (window services, `is_voting_open`, `activate`)
- Create: `elections/migrations/0007_voting_windows.py`, `elections/migrations/0008_window_guards.py`
- Modify: `tests/conftest.py` (`make_election` creates a window), `elections/management/commands/seed_demo.py`
- Test: `tests/test_windows.py`

**Interfaces:**
- Produces:
  - `VotingWindow(election FK related_name="windows", starts_at DateTimeField, ends_at DateTimeField)`
    - `Meta.ordering = ["starts_at"]`
    - `CheckConstraint(ends_at__gt=F("starts_at"), name="window_order")`
  - `Election.opens_at` / `closes_at`: `null=True, blank=True, editable=False`
  - `Election.current_window(now=None) -> VotingWindow | None`
  - `Election.next_window(now=None) -> VotingWindow | None`
  - `Election.state` adds `"between"`
  - `services.sync_envelope(election) -> None`
  - `services.is_voting_open(election, now) -> bool`
  - `services.add_window(actor, starts_at, ends_at) -> VotingWindow`
  - `services.extend_window(actor, window_id, ends_at) -> VotingWindow`
  - `services.remove_window(actor, window_id) -> None`
  - All raise `ElectionError`.
  - `make_election(...)` keeps its signature and creates one window `[closes-72h, closes)`.

- [ ] **Step 1: Write failing tests in `tests/test_windows.py`**

```python
def test_state_between_and_open_follow_windows(make_election):
    # election with windows [t0, t0+2h) and [t0+4h, t0+6h); patch timezone.now
    # t0+1h -> "open"; t0+3h -> "between"; t0+6h -> "closed"; t0-1h -> "scheduled"
    # election.next_window(t0+3h).starts_at == t0+4h

def test_is_voting_open_is_half_open(make_election):
    # is_voting_open(e, w.starts_at) is True; is_voting_open(e, w.ends_at) is False; paused -> False

def test_overlap_rejected_touching_allowed(make_election):
    # add_window overlapping an existing window raises ElectionError; [ends_at, ends_at+1h) succeeds

def test_envelope_tracks_windows():
    # inactive election: add two windows -> opens_at == min start, closes_at == max end;
    # remove both -> opens_at is None and closes_at is None

def test_after_activation_append_and_extend_only(make_election):
    # remove_window raises; add_window starting in the past raises; add_window before opens_at raises;
    # extend_window to an earlier ends_at raises; extend of an ended window raises;
    # extend of the current window to a later end succeeds and closes_at grows;
    # AuditEvent actions include "window.added" and "window.extended"

def test_closed_election_refuses_window_changes(make_election):
    # make_election(closes_in=timedelta(seconds=-1)); add_window and extend_window raise

def test_activation_requires_a_window():
    # election with offices, candidates, voters but no windows -> activate(None) raises ElectionError

def test_postgres_window_guards(make_election):
    # skip unless postgresql. Direct ORM .update()/.delete() on VotingWindow after activation raises DatabaseError
    # for: delete, moving starts_at, shrinking ends_at. Vote insert outside every window raises DatabaseError.
```

Fill in each body. Build windows with `VotingWindow.objects.create` plus `sync_envelope`, or with `add_window(None, ...)` before activation. Patch time with `patch("elections.services.database_now", return_value=...)` for services and `patch("django.utils.timezone.now", ...)` for `state`.

- [ ] **Step 2: Run** `uv run pytest tests/test_windows.py -q`. Expected: FAIL (`ImportError: VotingWindow`).

- [ ] **Step 3: Implement the models**
  - `Election.clean()`: drop the 72-hour rule. The frozen check compares only `(name, opens_at)`, because `closes_at` may grow after activation through `sync_envelope`.
  - `state`, checked in this order:
    1. not activated → `draft`
    2. `opens_at is None or now < opens_at` → `scheduled`
    3. `now >= closes_at` → `closed`
    4. paused → `paused`
    5. inside a window → `open`
    6. otherwise → `between`

- [ ] **Step 4: Implement the services in `elections/services.py`**
  - All three mutators run under `@transaction.atomic` and `lock_election()`, and use `database_now()`.
  - **Both phases:**
    - `ends_at > starts_at`;
    - no overlap with other windows (`starts_at < other.ends_at and ends_at > other.starts_at`);
    - refuse once `closes_at` is set and `now >= closes_at` after activation.
  - **After activation:**
    - `add_window` needs `starts_at > now and starts_at >= opens_at`;
    - `extend_window` needs `window.ends_at > now and ends_at > window.ends_at`;
    - `remove_window` is always refused.
  - Each mutator calls `sync_envelope(election)` (save with `update_fields=["opens_at", "closes_at"]`) and then audits:
    - `"window.added"` with `starts_at`/`ends_at` as ISO strings;
    - `"window.extended"` with `window_id` and `ends_at`;
    - `"window.removed"` with `window_id`.
  - `is_voting_open(election, now)` = `activated_at and not paused and windows.filter(starts_at__lte=now, ends_at__gt=now).exists()`.
  - `activate` adds `if not election.windows.exists(): raise ElectionError(_("Add at least one voting window before activating."))` before its existing opening-time check.
  - `submit_vote`'s window test switches to `is_voting_open`. It is removed entirely in Task 4.

- [ ] **Step 5: Write migration `0007_voting_windows`**
  - `makemigrations` covers the schema.
  - Hand-add a `RunPython` that creates one `VotingWindow(opens_at, closes_at)` for an existing election that has both set. Its reverse is a no-op.

- [ ] **Step 6: Write migration `0008_window_guards`**
  - Postgres only, same `install`/`uninstall` shape as `0002`.
  - `CREATE OR REPLACE FUNCTION gea_vote_guard()`: replace the `clock_timestamp() < e.opens_at OR clock_timestamp() >= e.closes_at` clause with:

```sql
NOT EXISTS (SELECT 1 FROM elections_votingwindow w WHERE w.election_id = e.id
            AND clock_timestamp() >= w.starts_at AND clock_timestamp() < w.ends_at)
```

  - `CREATE OR REPLACE FUNCTION gea_election_guard()`: after activation, `(name, opens_at, activated_at)` stay frozen. `closes_at` may change only if `NEW.closes_at > OLD.closes_at AND OLD.closes_at > clock_timestamp()`. The rest stays as in 0002.
  - New `gea_window_guard()` on `elections_votingwindow` (BEFORE INSERT/UPDATE/DELETE). When the election is activated, it locks the election row `FOR UPDATE` and then:
    - DELETE → raise;
    - INSERT needs `NEW.starts_at > clock_timestamp() AND NEW.starts_at >= e.opens_at`;
    - UPDATE needs `NEW.starts_at = OLD.starts_at AND NEW.ends_at > OLD.ends_at AND OLD.ends_at > clock_timestamp()`.
  - `uninstall` restores the 0002 bodies of `gea_vote_guard`/`gea_election_guard` and drops the window trigger and function.

- [ ] **Step 7: Update `conftest.make_election` and `seed_demo`**
  - `make_election` creates the window with `VotingWindow.objects.create` + `sync_envelope` before activating.
  - `seed_demo` creates its 72 h demo window the same way.

- [ ] **Step 8: Run** `uv run pytest -q`. Expected: all PASS, including the existing `test_voting.py`.

- [ ] **Step 9: Commit** `git commit -m "Replace the fixed 72-hour window with admin-defined voting windows"`

---

### Task 2: Committee window management

**Files:**
- Modify: `elections/forms.py` (`WindowForm`)
- Modify: `elections/views.py` (`committee`, `committee_action`)
- Modify: `templates/committee/index.html`
- Modify: `elections/admin.py` (`ElectionAdmin.fields = ["name"]`, read-only `VotingWindowInline`)
- Test: `tests/test_windows.py`

**Interfaces:**
- Consumes: `add_window`, `extend_window`, `remove_window` (Task 1).
- Produces:
  - `WindowForm` with fields `starts_at`, `ends_at` (`DateTimeInput(attrs={"type": "datetime-local"})`, interpreted in the current timezone, WIB) and optional `window_id`.
  - `committee_action` handles `action` values `add_window`, `extend_window`, `remove_window`.

- [ ] **Step 1: Write failing tests**

```python
def test_committee_adds_and_removes_windows_before_activation(make_election):
    # make_election(active=False); OTP staff client with operate_election (reuse staff_client() helper
    # from tests/test_accounts_and_operations.py — move it to conftest.py as a fixture)
    # POST committee_action action=add_window starts_at="2026-12-01T09:00" ends_at="2026-12-01T15:00"
    # -> 302; VotingWindow with starts_at == 2026-12-01 02:00 UTC exists
    # POST action=remove_window window_id=<id> -> window gone

def test_committee_window_errors_are_messages_not_500(make_election):
    # overlapping add -> 302 and messages contain the ElectionError text; window count unchanged

def test_committee_window_actions_need_operate_permission(make_election):
    # staff without operate_election -> 403
```

- [ ] **Step 2: Run** `uv run pytest tests/test_windows.py -q -k committee`. Expected: FAIL.

- [ ] **Step 3: Implement the views and template**
  - **Views:** pass `windows` and `window_form` to the committee page. An invalid form becomes `messages.error` plus a redirect, like the other actions.
  - **Template:** add a "Voting sessions" panel listing each window as a WIB date with `HH:MM–HH:MM`.
    - Before activation: an add form and a Remove button per window.
    - After activation and before close: the add form, plus an "Extend end" datetime input + button on windows that haven't ended.
  - **Admin:** `ElectionAdmin` shows `name` only. `opens_at`/`closes_at` are read-only. `VotingWindowInline` (Tabular) has every permission method returning `False` except view.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** `git commit -m "Let operators manage voting sessions from the committee page"`

---

### Task 3: Show sessions to voters and in credential emails

**Files:**
- Modify: `templates/elections/schedule.html`, `templates/elections/home.html`, `templates/elections/voting_stage.html`, `templates/elections/results.html`
- Modify: `static/js/panem.js` (treat `"between"` like `"paused"` visually)
- Modify: `elections/services.py` (credential email body in `issue_credentials`)
- Test: `tests/test_pages.py`, `tests/test_accounts_and_operations.py`

**Interfaces:**
- Consumes: `Election.state`, `current_window`, `next_window`, `election.windows`.

- [ ] **Step 1: Write failing tests**

```python
def test_schedule_lists_sessions_and_next_opening(client, make_election):
    # two windows, now between them: home page contains both "HH:MM–HH:MM" ranges in WIB
    # and the translated "Next session" label with the second window's start

def test_credentials_email_lists_every_session(make_election):
    # election with two windows; issue_credentials(None); decrypted outbox payload body contains both ranges
    # and does not contain "Selesai / Closes"
```

  Find how the existing outbox tests decrypt payloads in `test_accounts_and_operations.py` and reuse that helper.

- [ ] **Step 2: Run** them. Expected: FAIL.

- [ ] **Step 3: Implement `schedule.html`**
  - Keep the state badge, adding the label "Between sessions" for `between`.
  - Replace Opens/Closes with a session list.
  - Countdown targets:
    - `current_window.ends_at` when open ("Session ends in");
    - `next_window.starts_at` when scheduled/between ("Next session in").
  - Templates that pass `data-opens`/`data-closes` to the arena keep doing so; the envelope still exists.

- [ ] **Step 4: Implement the credentials email.** Replace its two Opens/Closes lines with:
  - one line per window: `• {d M Y}, {H:i}–{H:i} WIB` (an end on another day shows the full date);
  - the bilingual sentence `"Pemungutan suara dilakukan langsung di laptop panitia. / Voting is in person on the committee laptops."`

- [ ] **Step 5: Run** `uv run pytest -q`, then check the home page in the browser preview in the `between` state (`seed_demo` plus a second window). Expected: PASS, sessions visible.

- [ ] **Step 6: Commit** `git commit -m "Show voting sessions on public pages and in credential emails"`

---

### Task 4: Whole-ballot review, atomic finalize, auto-logout

**Files:**
- Modify: `elections/services.py` (add `submit_ballot`, delete `submit_vote`)
- Modify: `elections/forms.py` (`BallotForm` becomes multi-office)
- Modify: `elections/views.py` (`ballot`, new `done`, `dashboard`; delete `receipt`)
- Modify: `config/urls.py`: `vote/` → `ballot`, `done/` → `done`. Remove `vote/<slug>/` and `receipt/<uuid>/`.
- Create: `templates/elections/review.html`, `templates/elections/done.html`
- Modify: `templates/elections/ballot.html`, `templates/elections/dashboard.html`
- Delete: `templates/elections/confirm.html`, `templates/elections/receipt.html`
- Modify: `templates/500.html` (copy no longer mentions "retrying a submission" per office)
- Test: `tests/test_voting.py` (rewrite per-office tests), `tests/test_pages.py`, `tests/test_accounts_and_operations.py` (CSRF and language tests use `/vote/`)

**Interfaces:**
- Consumes: `is_voting_open` (Task 1).
- Produces:
  - `services.submit_ballot(user, choices: dict[str, int], request_id: uuid.UUID) -> tuple[list[Vote], bool]` (`choices` maps office slug → candidate pk; the bool is "created").
  - `BallotForm(data=None, *, election)`, with one required `RadioSelect` `ModelChoiceField` per office named `office_<slug>`, plus hidden `request_id`.
  - `BallotForm.choices() -> dict[str, int]`.
  - URL names: `ballot` (`/vote/`, no args) and `done` (`/done/`).

- [ ] **Step 1: Write failing service tests** (replace `cast`-based tests in `tests/test_voting.py`)

```python
def ballot(voter, c, k=0, s=0, request_id=None):
    return submit_ballot(voter.user, {"kahim": c["kahim"][k].pk, "senator": c["senator"][s].pk},
                         request_id or uuid.uuid4())

def test_finalize_writes_every_office_atomically(make_election):
    # votes, created = ballot(...); created and len(votes) == 2; both share request_id

def test_second_finalize_returns_existing_and_changes_nothing(make_election):
    # first = ballot(v, c, 0, 0); again, created = ballot(v, c, 1, 1)
    # not created; {x.pk for x in again} == {x.pk for x in first}; candidates unchanged; Vote count == 2

def test_incomplete_or_mismatched_choices_rejected(make_election):
    # missing "senator"; extra unknown slug; senator candidate under "kahim" -> ElectionError, Vote count 0

def test_failure_on_second_office_saves_nothing(make_election):
    # patch Vote.objects.create to raise on its second call -> exception propagates and Vote count == 0

def test_finalize_refused_outside_window_paused_revoked_closed_draft(make_election, state):
    # parametrize like the existing test_closed_inactive_and_ineligible_rejected, plus "between"
    # and "at_ends_at" (database_now patched to window.ends_at) -> ElectionError, Vote count 0
```

  Keep `test_database_uniqueness`, `test_votes_immutable_model_and_database`, the results/finalization tests and the Postgres guard test. Point their setup at `ballot(...)`.

- [ ] **Step 2: Write failing HTTP tests**

```python
def test_review_then_finalize_logs_out_and_hides_choice(client, make_election):
    # force_login; GET /vote/ -> 200, no "checked"; POST action=review with both offices -> 200, both
    # candidate names shown, Vote count 0; POST action=finalize -> 302 to reverse("done"); Vote count 2
    # GET done -> 200; candidate names, NIM and receipt ids absent from content; '<meta http-equiv="refresh"' present
    # GET /vote/ -> 302 to login (session ended); GET /dashboard/ -> 302 to login

def test_change_returns_to_ballot_with_selection(client, make_election):
    # POST action=change with both offices -> 200 ballot page with chosen radios checked, Vote count 0

def test_window_closes_during_review_keeps_voter_logged_in(client, make_election):
    # POST review; set_paused(None, True); POST finalize -> 302 to dashboard; Vote count 0;
    # dashboard GET -> 200 (still logged in) with the "Voting is not open." message

def test_already_voted_voter_sees_done_state_not_ballot(client, make_election):
    # ballot(v, c); force_login; GET /vote/ -> 302 to dashboard; dashboard shows "You have voted"
    # and no candidate name

def test_voter_pages_are_no_store(client, make_election):
    # for /vote/, /dashboard/, review response, /done/: "no-store" in response["Cache-Control"]
```

- [ ] **Step 3: Run** `uv run pytest tests/test_voting.py -q`. Expected: FAIL.

- [ ] **Step 4: Implement `submit_ballot` in `services.py`**
  - Same locking as today's `submit_vote`: `lock_election()`, then the voter `select_for_update`.
  - Order of checks:
    1. eligibility;
    2. existing votes → return `(list(existing), False)`;
    3. `is_voting_open(election, database_now())`, else `_("Voting is not open.")`;
    4. `set(choices) == set(office slugs)`, else `_("Choose a candidate for every office.")`;
    5. each candidate belongs to its office, else `_("Invalid candidate for this office.")`;
    6. create all rows with the same `request_id` and `submitted_at=now`.
  - Delete `submit_vote` once its callers are gone.

- [ ] **Step 5: Implement the views and templates**
  - **`ballot` view** (`@login_required @never_cache`, GET/POST):
    - a voter with votes → redirect to `dashboard`;
    - state not `open` → message + redirect to `dashboard`;
    - POST `review` with a valid form → render `review.html`;
    - POST `change` → re-render `ballot.html` with the bound form;
    - POST `finalize` → `submit_ballot`, then `django.contrib.auth.logout(request)`, then redirect to `done`. On `ElectionError`: message + redirect to `dashboard` (no logout).
  - **`done`:** `@never_cache @require_GET`, no login required.
    - Renders `done.html`: "Your vote is recorded", a "Selesai / Next voter" button to `login`, and `<meta http-equiv="refresh" content="10;url={% url 'login' %}">`.
  - **`dashboard`:** one "Start voting" button when open and not voted. "You have voted · HH:MM" when voted. The schedule otherwise. Per-office receipt links are removed.
  - **`review.html`:** reuses the `verdict` block styling once per office. Hidden inputs carry the choices and `request_id`. "Change" and "Finalize" buttons. Keep the existing "This vote is final." notice.

- [ ] **Step 6: Update the other tests and nav**
  - Update `test_pages.py`, `test_csrf_required_for_votes` and `test_language_switch_and_profiles` to `/vote/` (profiles render on the single ballot page).
  - Update `tests/test_archive_and_launch.py:75` if it asserts on the removed receipt route; keep its log-redaction intent.
  - `base.html` nav: "My ballot" stays, pointing to `dashboard`.

- [ ] **Step 7: Run** `uv run pytest -q`. Then walk the whole flow in the browser preview (`seed_demo`, log in, ballot, review, finalize, done → login after 10 s, Back button shows login). Expected: PASS. Screenshot the done page.

- [ ] **Step 8: Commit** `git commit -m "Review the whole ballot, finalize it atomically, and log the voter out"`

---

### Task 5: Idle timeout and one active session per voter

**Files:**
- Create: `elections/kiosk.py` (`KioskMiddleware`, `kiosk_ping` view)
- Create: `static/js/kiosk.js`
- Modify: `elections/models.py` (`Voter.session_key = CharField(max_length=40, blank=True, editable=False)`), migration `0009_voter_session_key`
- Modify: `elections/views.py` (`VoterLoginView.form_valid` records the session key)
- Modify: `config/settings.py` (settings + middleware after `MessageMiddleware`), `config/urls.py` (`kiosk/ping/` → `kiosk_ping`)
- Modify: `templates/base.html`:
  - for authenticated non-staff users, `<body data-idle-seconds="{{ kiosk_idle_seconds }}">` plus `kiosk.js`;
  - the logout form gets `id="logout-form"`.
- Modify: `elections/context.py` (`kiosk_idle_seconds`)
- Test: `tests/test_kiosk.py`

**Interfaces:**
- Produces:
  - `KioskMiddleware(get_response)`: acts only when `request.user.is_authenticated and not request.user.is_staff`. Task 6 extends it with the device check.
  - `kiosk_ping(request)`: POST, login required, returns 204.
  - Session key `"kiosk_seen"`: epoch seconds as `int`.

- [ ] **Step 1: Write failing tests in `tests/test_kiosk.py`**

```python
def test_idle_session_expires_on_server(client, make_election, settings):
    # settings.KIOSK_IDLE_SECONDS = 180; force_login; GET dashboard at t (patch time.time) -> 200;
    # GET at t+181 -> 302 to login with "inactivity" message; voter is anonymous afterwards

def test_ping_keeps_session_alive(client, make_election, settings):
    # GET at t; POST kiosk_ping at t+150 -> 204; GET dashboard at t+300 -> 200; GET at t+481 -> 302

def test_new_login_ends_older_session(make_election):
    # two Clients log in via POST reverse("login") with the conftest password; first client GET dashboard
    # -> 302 to login with "signed in on another laptop" message; second -> 200

def test_force_login_without_recorded_key_is_not_kicked(client, make_election):
    # voter.session_key == "" -> dashboard 200 (keeps the existing test suite valid)

def test_staff_sessions_ignore_kiosk_rules(make_election, settings):
    # OTP staff client idle 10 minutes -> committee page still 200
```

- [ ] **Step 2: Run** `uv run pytest tests/test_kiosk.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement `KioskMiddleware`**
  - **Idle:** if `now - session["kiosk_seen"] > KIOSK_IDLE_SECONDS` → `logout`, then `messages.info` with `_("Your session ended after a period of inactivity.")`, then redirect to `login`. Otherwise store `now`.
  - **Single session:** `voter.session_key` non-empty and different from `request.session.session_key` → `logout`, then `_("You signed in on another laptop, so this session ended.")`, then redirect to `login`.
  - Use `time.time()` so tests can patch it.

- [ ] **Step 4: Implement `VoterLoginView.form_valid`.** Call `super()`, then `Voter.objects.filter(user=self.request.user).update(session_key=self.request.session.session_key)`. The roster trigger allows this column.

- [ ] **Step 5: Implement `kiosk.js`**
  - Read `document.body.dataset.idleSeconds`.
  - Reset the countdown on `pointerdown`, `keydown`, `touchstart` and `scroll`.
  - On activity, POST to `/kiosk/ping/` with the CSRF token from the logout form, at most every 30 s.
  - In the last 30 s, show a fixed banner: "Sesi berakhir dalam N detik — sentuh layar untuk melanjutkan" / English via a `data-` attribute rendered with `{% translate %}`.
  - At 0, submit `#logout-form`.
  - Style the banner in `static/app.css` using existing tokens.

- [ ] **Step 6: Run** `uv run pytest -q`. In the browser preview with `KIOSK_IDLE_SECONDS=40`, check that the banner appears and logout happens. Expected: PASS.

- [ ] **Step 7: Commit** `git commit -m "End idle voter sessions and keep one active session per voter"`

---

### Task 6: Kiosk enrollment and kiosk-only voter login

**Files:**
- Modify: `elections/models.py` (`KioskDevice`), migration `0010_kioskdevice`
- Modify: `elections/kiosk.py`:
  - `sign_device`, `current_device`;
  - `kiosks` view;
  - a device check in `KioskMiddleware`.
- Modify: `elections/forms.py` (`VoterLoginForm.confirm_login_allowed` checks the device)
- Create: `templates/committee/kiosks.html`
- Modify: `templates/committee/index.html` (link), `templates/registration/login.html` (notice on unenrolled devices), `config/urls.py` (`committee/kiosks/` → `kiosks`)
- Modify: `elections/admin.py` (`KioskDevice` read-only in `ReadOnlyAdmin`)
- Test: `tests/test_kiosk.py`

**Interfaces:**
- Consumes: `KioskMiddleware` (Task 5), `committee_required`, `audit`.
- Produces:
  - `KioskDevice(id UUIDField pk default uuid4, label CharField(50), enrolled_at DateTimeField(default=timezone.now), revoked_at DateTimeField(null=True, blank=True))`
  - `kiosk.COOKIE = "gea_kiosk"`
  - `kiosk.sign_device(device) -> str` (`signing.dumps(str(device.pk), salt="gea.kiosk")`)
  - `kiosk.current_device(request) -> KioskDevice | None`: `signing.loads(..., max_age=KIOSK_COOKIE_AGE)`, unrevoked rows only.

- [ ] **Step 1: Write failing tests**

```python
def test_voter_login_refused_on_unenrolled_device(client, make_election, settings):
    # KIOSK_REQUIRED = True; POST login with valid credentials -> 200, not logged in, error text
    # "This device is not a registered voting laptop."; GET login shows the same notice

def test_operator_enrolls_device_and_voter_can_log_in(make_election, settings):
    # KIOSK_REQUIRED = True; OTP staff client with operate_election POSTs kiosks action=enroll label="Laptop 1"
    # -> response sets "gea_kiosk" cookie (httponly, samesite Lax); KioskDevice exists; audit "kiosk.enrolled"
    # copy cookie into a fresh Client; voter login -> 302 to dashboard

def test_revoked_device_ends_voter_sessions(make_election, settings):
    # enrolled client logged in as voter; staff POSTs action=revoke device_id -> audit "kiosk.revoked";
    # voter GET dashboard -> 302 to login

def test_tampered_or_expired_cookie_rejected(make_election, settings):
    # cookie "x"; and a valid cookie with time patched past KIOSK_COOKIE_AGE -> login refused

def test_staff_login_and_password_reset_work_without_kiosk(client, make_election, settings):
    # KIOSK_REQUIRED = True; committee admin login page 200; password_reset POST -> 302 to password_reset_done
```

- [ ] **Step 2: Run** `uv run pytest tests/test_kiosk.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement the device check**
  - **Login form:** `confirm_login_allowed` raises `ValidationError(_("This device is not a registered voting laptop."), code="kiosk")` when `settings.KIOSK_REQUIRED and not current_device(self.request)`.
  - **Middleware:** also logs out an authenticated voter whose device check fails.

- [ ] **Step 4: Implement the `kiosks` view**
  - Decorated `@committee_required("elections.operate_election") @never_cache`; serves GET and POST.
  - Lists devices and shows "This browser is enrolled as …".
  - **`enroll`:** create the device, then set the cookie with:
    - `max_age=KIOSK_COOKIE_AGE`;
    - `secure=settings.SESSION_COOKIE_SECURE`;
    - `httponly=True`;
    - `samesite="Lax"`.
  - **`revoke`:** set `revoked_at`.
  - Both actions audit.
  - The page reminds the operator to log out of the committee after enrolling.

- [ ] **Step 5: Run** `uv run pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit** `git commit -m "Allow voter login only on enrolled kiosk laptops"`

---

### Task 7: Operations, load test and docs

**Files:**
- Modify: `docs/OPERATIONS.md` (new "Kiosk laptops" section), `README.md` (voting flow, routes, settings)
- Modify: `loadtest/voting.js`, `loadtest/README.md`, `elections/management/commands/verify_rehearsal.py`
- Modify: `.env.example` (`KIOSK_REQUIRED`, `KIOSK_IDLE_SECONDS`, `KIOSK_COOKIE_AGE`)
- Test: `tests/test_archive_and_launch.py` (if `verify_rehearsal` is tested there; otherwise add `test_verify_rehearsal_counts_ballots`)

- [ ] **Step 1: Rewrite the `verify_rehearsal` test first**
  - k6 now logs `{"type": "ballot", "nim": ...}` once per voter after `/done/`.
  - The command fails unless:
    - every logged NIM has exactly one vote per office;
    - total votes == `expected_voters * office_count`;
    - there are no duplicate NIMs.
  - Run it. Expected: FAIL.

- [ ] **Step 2: Implement `verify_rehearsal` and `voting.js`**
  - `voting.js` follows the new flow: login, `/vote/`, POST review, POST finalize, expect `/done/`, then a retry finalize after re-login redirects to dashboard.
  - The load test README says to run with `KIOSK_REQUIRED=false` on the isolated rehearsal host only.

- [ ] **Step 3: Write the OPERATIONS.md kiosk checklist**
  - Dedicated Chrome profile (`--user-data-dir=C:\gea-kiosk --kiosk <URL>`), not incognito.
  - In that profile: password saving, autofill and translate prompts off.
  - Enroll each laptop at `/committee/kiosks/`, then log out of the committee.
  - Booth placement and privacy screens.
  - Laptop failure: revoke the device, enroll a spare, and pause voting if more than one fails.
  - Venue internet plus a mobile-hotspot backup on a different carrier.
  - If the connection drops mid-finalize, the voter logs in again. The dashboard shows whether the ballot was saved.
  - Gmail setup (replaces the Resend domain steps; keep those as the fallback):
    - create the committee account and turn on 2-Step Verification;
    - create an app password and store it only in Render;
    - run `send_test_email` to two ITB inboxes and check that it lands in Inbox, not Junk;
    - send credentials over at least two days;
    - never change the Gmail password during the election, because that cancels the app password.

- [ ] **Step 3b: Downsize hosting in `render.yaml` and `docs/OPERATIONS.md`**
  - Web `plan: starter`, with `WEB_WORKERS=2` and `WEB_THREADS=2`.
  - Database `plan: basic-256mb`.
  - Keep `numInstances: 1` and drop the "two instances before opening" capacity gate.
  - Validate with `render blueprints validate render.yaml` if the CLI is available.

- [ ] **Step 4: Run** the three Global Constraints commands. Expected: all PASS.

- [ ] **Step 5: Commit** `git commit -m "Document kiosk operations and move the load test to single finalize"`
