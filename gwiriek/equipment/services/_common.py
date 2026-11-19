"""Helpers shared by services."""

from collections.abc import Callable
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, models
from django.utils import timezone

from gwiriek.equipment.services.errors import InvalidDataError, NotFoundError


def today() -> date:
    """Return the current date in the configured time zone."""
    return timezone.localdate()


def validated_save(instance: models.Model, exclude: list[str] | None = None) -> None:
    """Run model validation then save, converting errors to ``InvalidDataError``.

    Args:
        instance: The model instance to validate and save.
        exclude: Fields to leave out of validation.

    Raises:
        InvalidDataError: If field validation or a database constraint fails.
    """
    try:
        instance.full_clean(exclude=exclude)
        instance.save()
    except DjangoValidationError as exc:
        raise InvalidDataError.from_django(exc) from exc
    except IntegrityError as exc:  # pragma: no cover - full_clean checks constraints first
        raise InvalidDataError(str(exc)) from exc


def get_or_not_found[M: models.Model](getter: Callable[[], M], message: str) -> M:
    """Call ``getter`` and turn ``DoesNotExist`` into ``NotFoundError``.

    Args:
        getter: Callable returning the object.
        message: Error message if the object does not exist.

    Returns:
        The object returned by ``getter``.

    Raises:
        NotFoundError: If the object does not exist.
    """
    try:
        return getter()
    except models.ObjectDoesNotExist as exc:
        raise NotFoundError(message) from exc
