import uuid
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.utils.translation import gettext_lazy as _

from .mail import enqueue
from .models import Candidate


class VoterLoginForm(AuthenticationForm):
    username = forms.CharField(label=_("NIM"), max_length=8, widget=forms.TextInput(attrs={
        "inputmode": "numeric", "autocomplete": "username", "pattern": "[0-9]{8}",
    }))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.is_staff or not hasattr(user, "voter"):
            raise forms.ValidationError(self.error_messages["invalid_login"], code="invalid_login",
                                        params={"username": "NIM"})


class BallotForm(forms.Form):
    candidate = forms.ModelChoiceField(queryset=Candidate.objects.none(), label=_("Your choice"),
                                       widget=forms.RadioSelect, empty_label=None)
    request_id = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, office, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["candidate"].queryset = office.candidates.all()


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

        enqueue(context["user"], "reset", "GEA ITB — Reset password / Password reset",
                render_to_string(email_template_name, context))
