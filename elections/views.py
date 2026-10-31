import csv
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import BallotForm, RosterForm, VoterLoginForm
from .models import AuditEvent, Election, EmailDelivery, Office, Vote, Voter
from .services import (
    ElectionError,
    activate,
    audit,
    delivery_summary,
    finalize_results,
    import_roster,
    parse_roster,
    published_results,
    request_credentials,
    revoke_access,
    set_paused,
    submit_vote,
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
    votes = {vote.office_id: vote for vote in voter.votes.select_related("office", "candidate")}
    return render(request, "elections/dashboard.html", {"election": voter.election,
        "offices": [{"office": office, "vote": votes.get(office.pk)} for office in voter.election.offices.all()]})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def ballot(request, slug):
    voter = get_object_or_404(Voter, user=request.user, user__is_staff=False)
    office = get_object_or_404(Office, election=voter.election, slug=slug)
    existing = Vote.objects.filter(voter=voter, office=office).first()
    if existing:
        return redirect("receipt", receipt_id=existing.receipt_id)
    if voter.election.state != "open":
        messages.error(request, _("Voting is not open."))
        return redirect("dashboard")
    form = BallotForm(request.POST or None, office=office)
    if request.method == "POST" and form.is_valid():
        if request.POST.get("action") == "submit":
            try:
                vote, _created = submit_vote(request.user, slug, form.cleaned_data["candidate"].pk,
                                             form.cleaned_data["request_id"])
            except ElectionError as exc:
                messages.error(request, str(exc))
                return redirect("dashboard")
            return redirect("receipt", receipt_id=vote.receipt_id)
        return render(request, "elections/confirm.html", {"form": form, "office": office,
            "candidate": form.cleaned_data["candidate"]})
    return render(request, "elections/ballot.html", {"form": form, "office": office,
        "candidates": office.candidates.all()})


@login_required
@never_cache
@require_GET
def receipt(request, receipt_id):
    vote = get_object_or_404(Vote.objects.select_related("office", "candidate"),
                             receipt_id=receipt_id, voter__user=request.user)
    remaining = Office.objects.filter(election=vote.election).exclude(
        pk__in=Vote.objects.filter(voter=vote.voter).values("office_id"))
    return render(request, "elections/receipt.html", {"vote": vote, "remaining": remaining})


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
        "voters": Voter.objects.select_related("user").order_by("user__username")})


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
