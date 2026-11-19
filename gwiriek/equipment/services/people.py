"""People services."""

from django.db import transaction
from django.utils.translation import gettext as _

from gwiriek.equipment.models import Person
from gwiriek.equipment.services._common import get_or_not_found, validated_save
from gwiriek.equipment.services.views import PersonView, person_view


def list_people(active_only: bool = True) -> list[PersonView]:
    """List people by name, only active ones by default."""
    qs = Person.objects.all()
    if active_only:
        qs = qs.filter(active=True)
    return [person_view(p) for p in qs]


def get_person(person_id: int) -> Person:
    """Return a person.

    Args:
        person_id: Id of the person.

    Returns:
        The person.

    Raises:
        NotFoundError: If it does not exist.
    """
    return get_or_not_found(
        lambda: Person.objects.get(pk=person_id),
        _("No person with id %(id)s.") % {"id": person_id},
    )


@transaction.atomic
def create_person(first_name: str, last_name: str, email: str = "", notes: str = "") -> PersonView:
    """Create a person.

    Args:
        first_name: First name.
        last_name: Last name.
        email: Email address, optional.
        notes: Free notes.

    Returns:
        The created person.

    Raises:
        InvalidDataError: If the data breaks a validation rule.
    """
    person = Person(
        first_name=first_name.strip(), last_name=last_name.strip(), email=email, notes=notes
    )
    validated_save(person)
    return person_view(person)
