"""Inspector services: the list of qualified persons (R4323-100) and their authorization.

An inspector may record an inspection only if, on the day of the inspection and on the day
it is recorded, they are active, hold a written authorization that has started and not
expired, and the item's family is within their scope.
"""

import csv
import io
from collections.abc import Iterable
from datetime import date

from django.core.files import File
from django.db import transaction
from django.utils.translation import gettext as _

from gwiriek.equipment.models import Family, Inspector, InspectorKind, Person
from gwiriek.equipment.services._common import get_or_not_found, today, validated_save
from gwiriek.equipment.services.errors import InvalidDataError, OperationNotAllowedError
from gwiriek.equipment.services.views import InspectorView, inspector_issues, inspector_view


def resolve_inspector(inspector_id: int) -> Inspector:
    """Return an inspector.

    Args:
        inspector_id: Id of the inspector.

    Returns:
        The inspector, with its person loaded.

    Raises:
        NotFoundError: If it does not exist.
    """
    return get_or_not_found(
        lambda: Inspector.objects.select_related("person").get(pk=inspector_id),
        _("No inspector with id %(id)s.") % {"id": inspector_id},
    )


def list_inspectors(active_only: bool = True, on: date | None = None) -> list[InspectorView]:
    """List inspectors with their qualification as of a day.

    Args:
        active_only: Leave out inactive inspectors.
        on: Reference day for the qualification, defaults to today.

    Returns:
        Inspectors, by id.
    """
    on = on or today()
    qs = Inspector.objects.select_related("person")
    if active_only:
        qs = qs.filter(active=True)
    return [inspector_view(i, on) for i in qs]


def get_inspector(inspector_id: int, on: date | None = None) -> InspectorView:
    """Return an inspector with its qualification (see :func:`resolve_inspector`)."""
    return inspector_view(resolve_inspector(inspector_id), on or today())


def validate_inspector(inspector: Inspector, on: date | None = None) -> None:
    """Check rules spanning several fields of an inspector.

    Args:
        inspector: The inspector to check, saved or not.
        on: Reference day for dates in the future, defaults to today.

    Raises:
        InvalidDataError: Listing every broken rule by field.
    """
    on = on or today()
    errors: dict[str, list[str]] = {}

    def add(field: str, message: str) -> None:
        errors.setdefault(field, []).append(message)

    if inspector.kind == InspectorKind.INTERNAL:
        if inspector.person_id is None:
            add("person", _("An internal inspector is one of the company's people."))
        if inspector.name.strip() or inspector.organisation.strip():
            add("name", _("Name and organisation are for external inspectors only."))
    elif inspector.kind == InspectorKind.EXTERNAL:
        if inspector.person_id is not None:
            add("person", _("An external inspector is not one of the company's people."))
        if not inspector.name.strip():
            add("name", _("Enter the name of the external inspector."))
    else:
        add("kind", _("Choose internal or external."))
    if inspector.authorized_on is not None:
        if inspector.authorized_on > on:
            add("authorized_on", _("This date is in the future."))
        if not inspector.authorization_reference.strip() and not inspector.authorization_document:
            add(
                "authorization_reference",
                _("A written authorization needs a reference or an attached document."),
            )
    if inspector.authorization_valid_until is not None:
        if inspector.authorized_on is None:
            add("authorization_valid_until", _("Enter the authorization date first."))
        elif inspector.authorization_valid_until < inspector.authorized_on:
            add("authorization_valid_until", _("The validity ends before the authorization."))
    unknown = [f for f in inspector.families if f not in Family.values]
    if unknown:
        add("families", _("Unknown family: %(values)s.") % {"values": ", ".join(unknown)})
    if errors:
        message = "; ".join(f"{f}: {m}" for f, msgs in errors.items() for m in msgs)
        raise InvalidDataError(message, {"fields": errors})


@transaction.atomic
def save_inspector(inspector: Inspector) -> Inspector:
    """Validate and save an inspector (used by the admin).

    Args:
        inspector: A new or modified inspector.

    Returns:
        The saved inspector.

    Raises:
        InvalidDataError: If the inspector breaks a validation rule.
    """
    inspector.name = inspector.name.strip()
    inspector.organisation = inspector.organisation.strip()
    inspector.authorization_reference = inspector.authorization_reference.strip()
    validate_inspector(inspector)
    validated_save(inspector)
    return inspector


@transaction.atomic
def create_inspector(
    *,
    kind: str,
    person_id: int | None = None,
    name: str = "",
    organisation: str = "",
    authorized_on: date | None = None,
    authorization_reference: str = "",
    authorization_document: File | None = None,  # type: ignore[type-arg]
    authorization_valid_until: date | None = None,
    families: Iterable[str] = (),
    notes: str = "",
) -> InspectorView:
    """Add a qualified person to the list of inspectors.

    Args:
        kind: ``internal`` or ``external``.
        person_id: For an internal inspector, the person.
        name: For an external inspector, their name.
        organisation: For an external inspector, their organisation.
        authorized_on: Date of the written authorization; without it the inspector cannot
            record inspections.
        authorization_reference: Reference of the authorization, training or certificate.
        authorization_document: Scan of the authorization.
        authorization_valid_until: Last day of validity, if the authorization expires.
        families: Families of equipment the inspector may inspect.
        notes: Free notes.

    Returns:
        The new inspector, with its qualification as of today.

    Raises:
        NotFoundError: If the person does not exist.
        InvalidDataError: If the data breaks a validation rule or the person is already an
            inspector.
    """
    if kind not in InspectorKind.values:
        raise InvalidDataError(
            _("Unknown inspector type %(kind)s.") % {"kind": kind},
            {"allowed": list(InspectorKind.values)},
        )
    person = None
    if person_id is not None:
        person = get_or_not_found(
            lambda: Person.objects.get(pk=person_id),
            _("No person with id %(id)s.") % {"id": person_id},
        )
        if Inspector.objects.filter(person=person).exists():
            raise InvalidDataError(_("This person is already an inspector."))
    inspector = Inspector(
        kind=kind,
        person=person,
        name=name,
        organisation=organisation,
        authorized_on=authorized_on,
        authorization_reference=authorization_reference,
        authorization_valid_until=authorization_valid_until,
        families=list(dict.fromkeys(families)),
        notes=notes,
    )
    if authorization_document is not None:
        inspector.authorization_document.save(
            authorization_document.name or "authorization", authorization_document, save=False
        )
    save_inspector(inspector)
    return inspector_view(inspector, today())


def check_can_inspect(inspector: Inspector, family: str, on: date) -> None:
    """Check that an inspector may inspect an item of ``family`` on a day.

    Args:
        inspector: The inspector.
        family: Family of the item's product model.
        on: The day to check.

    Raises:
        OperationNotAllowedError: Naming the first reason the inspector is not allowed.
    """
    messages = {
        "inactive": _("This inspector is inactive."),
        "person_inactive": _("This inspector is no longer one of the active people."),
        "not_authorized": _("This inspector has no written authorization."),
        "not_yet_valid": _("This inspector's authorization starts after %(date)s."),
        "expired": _("This inspector's authorization was no longer valid on %(date)s."),
        "no_scope": _("This inspector's scope covers no family of equipment."),
    }
    issues = inspector_issues(inspector, on)
    if issues:
        raise OperationNotAllowedError(
            messages[issues[0]] % {"date": on.strftime("%d/%m/%Y")},
            {"issues": issues, "inspector_id": inspector.pk},
        )
    if family not in inspector.families:
        raise OperationNotAllowedError(
            _("The family %(family)s is outside this inspector's scope.")
            % {"family": Family(family).label},
            {"family": family, "scope": list(inspector.families)},
        )


def export_inspectors_csv(
    include_inactive: bool = False,
    on: date | None = None,
    inspector_ids: Iterable[int] | None = None,
) -> str:
    """Export the list of qualified persons (R4323-100) as CSV.

    Args:
        include_inactive: Also list inactive inspectors.
        on: Reference day for the qualification, defaults to today.
        inspector_ids: Export only these inspectors.

    Returns:
        CSV text with a header row, separated by semicolons for spreadsheet software set to
        French.
    """
    views = list_inspectors(active_only=not include_inactive, on=on)
    if inspector_ids is not None:
        wanted = set(inspector_ids)
        views = [v for v in views if v.id in wanted]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            _("ID"),
            _("Name"),
            _("Type"),
            _("Organisation"),
            _("Authorization date"),
            _("Authorization reference"),
            _("Valid until"),
            _("Scope"),
            _("Active"),
            _("Qualified on %(date)s") % {"date": (on or today()).strftime("%d/%m/%Y")},
        ]
    )
    for v in views:
        writer.writerow(
            [
                v.id,
                v.name,
                InspectorKind(v.kind).label,
                v.organisation,
                v.authorized_on.isoformat() if v.authorized_on else "",
                v.authorization_reference or v.authorization_document,
                v.authorization_valid_until.isoformat() if v.authorization_valid_until else "",
                ", ".join(str(Family(f).label) for f in v.families),
                _("yes") if v.active else _("no"),
                _("yes") if v.qualified else _("no"),
            ]
        )
    return buffer.getvalue()
