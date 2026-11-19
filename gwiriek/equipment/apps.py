"""Application configuration and system checks."""

from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning, register
from django.utils.translation import gettext_lazy as _


class EquipmentConfig(AppConfig):
    """PPE inventory application."""

    name = "gwiriek.equipment"
    verbose_name = _("PPE inventory")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Register system checks."""
        register(check_secret_key, Tags.security, deploy=True)


def check_secret_key(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Warn, in ``check --deploy``, when no persistent secret key is configured."""
    if getattr(settings, "SECRET_KEY_FROM_ENV", True):
        return []
    return [
        Warning(
            "GWIRIEK_SECRET_KEY is not set: an ephemeral key is used, so admin sessions "
            "do not survive a restart.",
            hint="Set GWIRIEK_SECRET_KEY before serving the admin.",
            id="gwiriek.W001",
        )
    ]
