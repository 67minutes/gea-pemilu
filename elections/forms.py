import uuid
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .kiosk import current_device
from .mail import enqueue, reset_allowed
from .models import Voter


class VoterLoginForm(AuthenticationForm):
    username = forms.CharField(label=_("NIM"), max_length=8, widget=forms.TextInput(attrs={
        "inputmode": "numeric", "autocomplete": "username", "pattern": "[0-9]{8}",
    }))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.is_staff or not hasattr(user, "voter"):
            raise forms.ValidationError(self.error_messages["invalid_login"], code="invalid_login",
                                        params={"username": "NIM"})
        if settings.KIOSK_REQUIRED and not current_device(self.request):
            raise forms.ValidationError(_("This device is not a registered voting laptop."), code="kiosk")


class BallotForm(forms.Form):
    """One required choice per office, reviewed together and finalized as one ballot."""

    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, data=None, *, election):
        super().__init__(data)
        self.offices = list(election.offices.all())
        for office in self.offices:
            self.fields[f"office_{office.slug}"] = forms.ModelChoiceField(
                queryset=office.candidates.all(), label=str(office), widget=forms.RadioSelect, empty_label=None,
                error_messages={"required": _("Choose a candidate for this office.")})

    def sections(self):
        return [{"office": office, "field": self[f"office_{office.slug}"]} for office in self.offices]

    def choices(self):
        return {office.slug: self.cleaned_data[f"office_{office.slug}"].pk for office in self.offices}


class KioskForm(forms.Form):
    label = forms.CharField(label=_("Laptop label"), max_length=50,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. Laptop 1")}))


class RosterForm(forms.Form):
    file = forms.FileField(label=_("Approved voter CSV"), widget=forms.ClearableFileInput(attrs={"accept": ".csv"}))

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > 1_000_000:
            raise forms.ValidationError(_("CSV must be smaller than 1 MB."))
        return file


class ApprovedPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(label=_("Approved ITB email"))

    def get_users(self, email):
        return (user for user in super().get_users(email.strip().lower())
                if not user.is_staff and hasattr(user, "voter"))

    def save(self, **kwargs):
        url = urlparse(settings.PUBLIC_BASE_URL)
        kwargs["domain_override"] = url.netloc
        kwargs["use_https"] = url.scheme == "https"
        return super().save(**kwargs)

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from django.template.loader import render_to_string

        user = context["user"]
        with transaction.atomic():
            Voter.objects.select_for_update().get(user=user)  # serializes the cooldown check per voter
            if reset_allowed(user):
                enqueue(user, "reset", "GEA ITB — Reset password / Password reset",
                        render_to_string(email_template_name, context))


class WindowForm(forms.Form):
    """A voting session in WIB; datetime-local values are read in the current (Asia/Jakarta) timezone."""

    window_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    starts_at = forms.DateTimeField(label=_("Starts"), required=False, input_formats=["%Y-%m-%dT%H:%M"],
                                    widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    ends_at = forms.DateTimeField(label=_("Ends"), input_formats=["%Y-%m-%dT%H:%M"],
                                  widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
