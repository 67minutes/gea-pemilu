from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_otp.admin import OTPAdminAuthenticationForm, OTPAdminSite

from .models import AuditEvent, Candidate, Election, EmailDelivery, Office, Voter
from .services import audit

site = OTPAdminSite(name="committee_admin")
site.site_header = _("HMTG “GEA” ITB — Committee")
site.site_title = "GEA Pemilu"
site.index_title = _("Election configuration")
site.site_url = "/committee/"


class CommitteeAuthenticationForm(OTPAdminAuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["otp_device"].label = _("Authenticator device")
        self.fields["otp_token"].label = _("Authentication code")
        self.fields["otp_token"].widget.attrs["autocomplete"] = "one-time-code"


site.login_form = CommitteeAuthenticationForm


class ConfigurationAdmin(admin.ModelAdmin):
    """Every configuration mutation serializes against activation and voting."""

    @transaction.atomic
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        election = Election.objects.select_for_update().first()
        if request.method == "POST" and election and election.activated_at:
            raise PermissionDenied("Activated election configuration is frozen.")
        return super().changeform_view(request, object_id, form_url, extra_context)

    @transaction.atomic
    def delete_view(self, request, object_id, extra_context=None):
        election = Election.objects.select_for_update().first()
        if election and election.activated_at:
            raise PermissionDenied
        return super().delete_view(request, object_id, extra_context)

    def has_add_permission(self, request):
        return not Election.objects.filter(activated_at__isnull=False).exists() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return not Election.objects.filter(activated_at__isnull=False).exists() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def log_addition(self, request, obj, message):
        audit(request.user, "configuration.created", model=obj._meta.label, object_id=str(obj.pk))
        return super().log_addition(request, obj, message)

    def log_change(self, request, obj, message):
        audit(request.user, "configuration.changed", model=obj._meta.label, object_id=str(obj.pk))
        return super().log_change(request, obj, message)


@admin.register(Election, site=site)
class ElectionAdmin(ConfigurationAdmin):
    fields = ["name", "opens_at", "closes_at"]
    list_display = ["name", "opens_at", "closes_at", "activated_at", "paused"]


@admin.register(Office, site=site)
class OfficeAdmin(ConfigurationAdmin):
    list_display = ["slug", "election"]


@admin.register(Candidate, site=site)
class CandidateAdmin(ConfigurationAdmin):
    list_display = ["name", "office", "number"]
    list_filter = ["office"]


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Voter, site=site)
class VoterAdmin(ReadOnlyAdmin):
    fields = ["user", "display_name", "credentials_issued_at"]
    list_display = ["user", "display_name", "credentials_issued_at"]
    search_fields = ["user__username", "user__email"]


@admin.register(EmailDelivery, site=site)
class EmailDeliveryAdmin(ReadOnlyAdmin):
    fields = ["user", "kind", "status", "attempts", "last_error", "provider_id", "created_at", "delivered_at", "bounced_at"]
    list_display = ["user", "kind", "status", "attempts", "last_error", "delivered_at", "bounced_at"]
    list_filter = ["status", "kind"]


@admin.register(AuditEvent, site=site)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "actor", "action"]
    fields = ["created_at", "actor", "action", "details"]
    list_filter = ["action"]
