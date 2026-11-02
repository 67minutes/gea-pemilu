import csv
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import BallotForm, KioskForm, RosterForm, VoterLoginForm, WindowForm
from .kiosk import COOKIE, current_device, sign_device
from .models import AuditEvent, Election, EmailDelivery, KioskDevice, Office, Vote, Voter
from .services import (
    ElectionError,
    activate,
    add_window,
    audit,
    delivery_summary,
    extend_window,
    finalize_results,
    import_roster,
    parse_roster,
    published_results,
    remove_window,
    request_credentials,
    revoke_access,
    set_paused,
    submit_ballot,
)

# Election committee (panitia), shown on the About page. Division titles are proper names, not translated.
COMMITTEE_CHAIR = ("Ketua", "Wafda Agung Mulkinegara", "12024106")
COMMITTEE = [
    ("IT", "Sahara Nada Maheswari", "12024107"),
    ("Pencalonan", "Fernanda Rheza Adiyasa", "12024048"),
    ("Hearing", "Rayhan Pratama", "12024081"),
    ("Uji Panelis dan Debat", "Abdallah Yazid Ma’ruf", "12024034"),
    ("Pemungutan dan Rekapitulasi Suara", "Yedija Giovanni Wistoro", "12024068"),
    ("Grafis", "Masarra Amarantine", "12024101"),
    ("Videografi", "Muhammad Rafie Putra", "12024096"),
    ("Dokumentasi", "Muhammad Iqbal", "12024089"),
    ("Publikasi", "Theo Jonathan Prakarsa Ginting", "12024005"),
    ("Pensuasanaan", "Muhammad Naufal", "12024059"),
    ("Relasi", "Vira Hana Syifa", "12024018"),
    ("Operasional", "Daniel Sitopu", "12024079"),
    ("Sekretaris dan Bendahara", "Rayna Amelia", "12024097"),
    ("Disiplin", "Muhammad Iqbal Rifa’i", "12024076"),
]
HELPDESK = {"name": "Sahara Nada Maheswari", "role": "IT", "phone": "0851-2804-7459", "e164": "6285128047459"}


class VoterLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = VoterLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        # Only the newest login stays signed in: KioskMiddleware ends any older session.
        Voter.objects.filter(user=self.request.user).update(session_key=self.request.session.session_key)
        return response

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs),
                "kiosk_unenrolled": settings.KIOSK_REQUIRED and not current_device(self.request)}


def committee_required(permission=None):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.is_staff or not request.user.is_verified():
                return redirect(f"{reverse('committee_admin:login')}?next={request.path}")
            if permission and not request.user.has_perm(permission):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


@require_GET
def home(request):
    election = Election.objects.first()
    return render(request, "elections/home.html", {"election": election})


@login_required
@never_cache
@require_GET
def dashboard(request):
    voter = get_object_or_404(Voter, user=request.user, user__is_staff=False)
    return render(request, "elections/dashboard.html", {"election": voter.election,
        "voted_at": voter.votes.order_by("submitted_at").values_list("submitted_at", flat=True).first()})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def ballot(request):
    voter = get_object_or_404(Voter, user=request.user, user__is_staff=False)
    if voter.votes.exists():
        return redirect("dashboard")
    election = voter.election
    if election.state != "open":
        messages.error(request, _("Voting is not open."))
        return redirect("dashboard")
    form = BallotForm(request.POST or None, election=election)
    action = request.POST.get("action")
    if request.method == "POST" and action in {"review", "finalize"} and form.is_valid():
        if action == "finalize":
            try:
                submit_ballot(request.user, form.choices(), form.cleaned_data["request_id"])
            except ElectionError as exc:
                messages.error(request, str(exc))
                return redirect("dashboard")
            # The kiosk is shared: end the session so the next voter starts at the login page.
            logout(request)
            return redirect("done")
        return render(request, "elections/review.html", {"form": form, "election": election,
            "picks": [{"office": office, "candidate": form.cleaned_data[f"office_{office.slug}"]}
                      for office in form.offices]})
    if action == "change":
        form.errors.clear()  # returning to edit is not a failed submission
    return render(request, "elections/ballot.html", {"form": form, "election": election})


@never_cache
@require_GET
def done(request):
    return render(request, "elections/done.html")


@never_cache
@require_GET
def results(request):
    election = Election.objects.first()
    snapshot = published_results() if election else None
    labels = dict(Office.Slug.choices)
    offices = [{**office, "label": labels.get(office["slug"], office["slug"])} for office in snapshot["offices"]] if snapshot else []
    return render(request, "elections/results.html", {"election": election, "snapshot": snapshot, "offices": offices})


@require_GET
def about(request):
    return render(request, "elections/about.html", {"chair": COMMITTEE_CHAIR, "committee": COMMITTEE})


@require_GET
def helpdesk(request):
    return render(request, "elections/helpdesk.html", {"contact": HELPDESK})


@committee_required()
@never_cache
@require_GET
def committee(request):
    election = Election.objects.first()
    turnout = []
    if election:
        turnout = [{"office": office, "votes": Vote.objects.filter(office=office).count()}
                   for office in election.offices.all()]
    return render(request, "committee/index.html", {"election": election, "turnout": turnout,
        "eligible": election.voters.count() if election else 0, "deliveries": delivery_summary(),
        "failed_deliveries": EmailDelivery.objects.filter(status="failed").select_related("user")[:100],
        "can_operate": request.user.has_perm("elections.operate_election"),
        "can_review": request.user.has_perm("elections.review_ballots"),
        "voters": Voter.objects.select_related("user").order_by("user__username"),
        "windows": election.windows.all() if election else [], "now": timezone.now(),
        "window_form": WindowForm()})


def window_action(request, action):
    form = WindowForm(request.POST)
    if not form.is_valid():
        raise ElectionError(_("Enter a valid date and time for the voting session."))
    data = form.cleaned_data
    if action == "add_window":
        if not data["starts_at"]:
            raise ElectionError(_("Enter a valid date and time for the voting session."))
        add_window(request.user, data["starts_at"], data["ends_at"])
    elif action == "extend_window":
        extend_window(request.user, data["window_id"], data["ends_at"])


@committee_required("elections.operate_election")
@never_cache
@require_http_methods(["GET", "POST"])
def kiosks(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "enroll":
            form = KioskForm(request.POST)
            if not form.is_valid():
                messages.error(request, _("Enter a label for this laptop."))
                return redirect("kiosks")
            device = KioskDevice.objects.create(label=form.cleaned_data["label"])
            audit(request.user, "kiosk.enrolled", device=str(device.pk), label=device.label)
            messages.success(request, _("This browser is now enrolled. Log out of the committee before voters use it."))
            response = redirect("kiosks")
            response.set_cookie(COOKIE, sign_device(device), max_age=settings.KIOSK_COOKIE_AGE,
                                secure=settings.SESSION_COOKIE_SECURE, httponly=True, samesite="Lax")
            return response
        if action == "revoke":
            try:
                device_id = uuid.UUID(request.POST.get("device_id", ""))
            except ValueError:
                device_id = None
            device = device_id and KioskDevice.objects.filter(pk=device_id, revoked_at__isnull=True).first()
            if not device:
                messages.error(request, _("Invalid kiosk laptop."))
                return redirect("kiosks")
            device.revoked_at = timezone.now()
            device.save(update_fields=["revoked_at"])
            audit(request.user, "kiosk.revoked", device=str(device.pk), label=device.label)
            messages.success(request, _("Kiosk laptop revoked. Voters signed in on it are logged out."))
            return redirect("kiosks")
        raise PermissionDenied
    return render(request, "committee/kiosks.html", {"devices": KioskDevice.objects.all(),
        "current": current_device(request), "form": KioskForm()})


@committee_required("elections.operate_election")
@never_cache
@require_http_methods(["GET", "POST"])
def roster_import(request):
    form = RosterForm(request.POST or None, request.FILES or None)
    rows = None
    if request.method == "POST":
        try:
            if request.POST.get("action") == "confirm":
                rows = request.session.pop("roster_preview", None)
                if not rows:
                    raise ElectionError(_("Preview the CSV again before importing."))
                count = import_roster(request.user, rows)
                messages.success(request, _("Imported %(count)s voters.") % {"count": count})
                return redirect("committee")
            if form.is_valid():
                rows = parse_roster(form.cleaned_data["file"].read())
                request.session["roster_preview"] = rows
        except ElectionError as exc:
            messages.error(request, str(exc))
            rows = None
    return render(request, "committee/import.html", {"form": form, "rows": rows})


@committee_required("elections.operate_election")
@require_POST
def committee_action(request):
    action = request.POST.get("action")
    try:
        if action == "activate":
            activate(request.user)
        elif action in {"pause", "resume"}:
            set_paused(request.user, action == "pause")
        elif action in {"add_window", "extend_window"}:
            window_action(request, action)
        elif action == "remove_window":
            window_id = request.POST.get("window_id", "")
            if not window_id.isdigit():
                raise ElectionError(_("Invalid voting session."))
            remove_window(request.user, int(window_id))
        elif action == "credentials":
            request_credentials(request.user)
            messages.success(request, _("Credential generation is queued. Check delivery status shortly."))
        elif action == "revoke":
            voter_id = request.POST.get("voter_id", "")
            if not voter_id.isdigit() or not Voter.objects.filter(pk=voter_id).exists():
                raise ElectionError(_("Invalid voter."))
            revoke_access(request.user, voter_id)
        else:
            raise PermissionDenied
    except ElectionError as exc:
        messages.error(request, str(exc))
    return redirect("committee")


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store, private"
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        # Spreadsheet formula injection also applies to committee-authored names.
        writer.writerow(["'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
    return response


@committee_required()
@never_cache
@require_GET
def export_results(request, kind):
    if kind == "ballots" and not request.user.has_perm("elections.review_ballots"):
        raise PermissionDenied
    if kind == "audit" and not request.user.has_perm("elections.operate_election"):
        raise PermissionDenied
    snapshot = finalize_results()
    if not snapshot:
        raise PermissionDenied(_("Exports are available after closing."))
    if kind == "ballots":
        audit(request.user, "export.identity_ballots")
        rows = Vote.objects.select_related("voter__user", "office", "candidate").order_by("id")
        return csv_response("gea-ballots.csv", ["nim", "office", "candidate_number", "candidate_name", "submitted_at", "receipt_id"],
                            ((v.voter.user.username, v.office.slug, v.candidate.number, v.candidate.name,
                              v.submitted_at.isoformat(), v.receipt_id) for v in rows))
    if kind == "aggregate":
        audit(request.user, "export.aggregate")
        return csv_response("gea-results.csv", ["office", "candidate_number", "candidate_name", "votes", "eligible", "turnout_percent"],
                            ((o["slug"], c["number"], c["name"], c["votes"], snapshot["eligible"], o["turnout_percent"])
                             for o in snapshot["offices"] for c in o["candidates"]))
    if kind == "audit":
        audit(request.user, "export.audit")
        return csv_response("gea-audit.csv", ["timestamp", "actor", "action", "details"],
                            ((a.created_at.isoformat(), a.actor_id or "system", a.action, str(a.details))
                             for a in AuditEvent.objects.order_by("id")))
    raise PermissionDenied


@require_GET
def health_live(request):
    return JsonResponse({"status": "ok"})


@require_GET
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ready"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
