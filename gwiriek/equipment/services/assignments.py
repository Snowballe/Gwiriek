"""Assignment services: issuing items to people or sites and returning them to stock."""

from datetime import date

from django.db import transaction
from django.utils.translation import gettext as _

from gwiriek.equipment.models import Assignment, AssignmentKind, Item, ItemStatus, Person
from gwiriek.equipment.services import items as item_services
from gwiriek.equipment.services._common import today, validated_save
from gwiriek.equipment.services.errors import InvalidDataError, OperationNotAllowedError
from gwiriek.equipment.services.people import get_person
from gwiriek.equipment.services.views import AssignmentView, assignment_view, item_end_of_life


def check_assignment(
    item: Item, kind: str, person_id: int | None, location: str, on: date | None = None
) -> Person | None:
    """Check that ``item``, in its current state, may be assigned to a target.

    Args:
        item: The item.
        kind: An :class:`~gwiriek.equipment.models.AssignmentKind` value.
        person_id: Person to assign to, for a person assignment.
        location: Site name, or optional storage place.
        on: Day of the assignment, defaults to today.

    Returns:
        The person to assign the item to, if any.

    Raises:
        NotFoundError: If the person does not exist.
        InvalidDataError: If the target does not match ``kind``.
        OperationNotAllowedError: If the item is retired, or is issued while quarantined or
            past its end of life.
    """
    if kind not in AssignmentKind.values:
        raise InvalidDataError(
            _("Unknown assignment target %(kind)s.") % {"kind": kind},
            {"allowed": list(AssignmentKind.values)},
        )
    if item.status == ItemStatus.RETIRED:
        raise OperationNotAllowedError(_("A retired item cannot be assigned."))
    if kind != AssignmentKind.STOCK and item.status == ItemStatus.QUARANTINE:
        raise OperationNotAllowedError(_("A quarantined item cannot be issued."))
    if kind != AssignmentKind.STOCK and item_end_of_life(item).is_expired(on or today()):
        raise OperationNotAllowedError(
            _("This item has passed its end of life and cannot be issued.")
        )
    if kind == AssignmentKind.PERSON:
        if person_id is None:
            raise InvalidDataError(_("Choose the person to assign the item to."))
        person = get_person(person_id)
        if not person.active:
            raise OperationNotAllowedError(_("This person is no longer active."))
        return person
    if person_id is not None:
        raise InvalidDataError(_("A person can only be given for a person assignment."))
    if kind == AssignmentKind.SITE and not location.strip():
        raise InvalidDataError(_("Enter the site name."))
    return None


@transaction.atomic
def assign_item(
    ref: int | str,
    kind: str,
    *,
    actor: str,
    person_id: int | None = None,
    location: str = "",
    started_on: date | None = None,
    notes: str = "",
) -> AssignmentView:
    """Assign an item to a person, a site or the stock, closing its open assignment.

    Issuing an item in stock to a person or a site puts it into service (and sets its date
    put into service if it has none). Returning an item in service to stock sets it back to
    ``in_stock``. Other statuses are kept.

    Args:
        ref: Item reference, see :func:`~gwiriek.equipment.services.items.resolve_item`.
        kind: An :class:`~gwiriek.equipment.models.AssignmentKind` value.
        actor: Who makes the change, recorded with the assignment.
        person_id: Person to assign to, for a person assignment.
        location: Site name, or optional storage place.
        started_on: Start date, defaults to today.
        notes: Free notes.

    Returns:
        The new assignment.

    Raises:
        NotFoundError: If the item or the person does not exist.
        InvalidDataError: If the target does not match ``kind`` or the date is invalid.
        OperationNotAllowedError: If the item cannot be assigned in its current state.
    """
    started_on = started_on or today()
    if started_on > today():
        raise InvalidDataError(_("An assignment cannot start in the future."))
    item = item_services.resolve_item(ref)
    person = check_assignment(item, kind, person_id, location, started_on)
    current = Assignment.objects.select_for_update().filter(item=item, ended_on=None).first()
    if current is not None:
        if started_on < current.started_on:
            raise InvalidDataError(
                _("The new assignment cannot start before the current one (%(date)s).")
                % {"date": current.started_on.isoformat()}
            )
        current.ended_on = started_on
        current.save(update_fields=["ended_on"])

    issued = kind != AssignmentKind.STOCK
    assignment = Assignment(
        item=item,
        kind=kind,
        person=person,
        location=location.strip(),
        started_on=started_on,
        actor=actor,
        notes=notes,
    )
    validated_save(assignment)

    if issued and item.status == ItemStatus.IN_STOCK:
        item.status = ItemStatus.IN_SERVICE
        if item.in_service_on is None:
            item.in_service_on = started_on
        item_services.save_item(item, actor=actor, reason=_("Issued"), on=started_on)
    elif not issued and item.status == ItemStatus.IN_SERVICE:
        item.status = ItemStatus.IN_STOCK
        item_services.save_item(item, actor=actor, reason=_("Returned to stock"), on=started_on)
    return assignment_view(assignment)
