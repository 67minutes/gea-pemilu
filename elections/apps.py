from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ElectionsConfig(AppConfig):
    name = "elections"
    verbose_name = _("Election operations")

    def ready(self):
        from . import checks  # noqa: F401
