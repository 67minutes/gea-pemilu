from django.utils.translation import gettext as _


def site_context(request):
    return {"site_name": 'HMTG “GEA” ITB', "privacy_notice": _(
        "Your choices are linked to your NIM. Authorized committee reviewers can review them after closing. "
        "Keep your password private. Each submitted vote is final.")}
