"""Item services: registration, lookup, validation and status changes with audit trail."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet
from django.utils.translation import gettext as _

from gwiriek.equipment.models import (
    Assignment,
    Family,
    Inspection,
    Item,
    ItemStatus,
    ProductModel,
    StatusChange,
)
from gwiriek.equipment.services._common import get_or_not_found, today, validated_save
from gwiriek.equipment.services.errors import (
    AmbiguousReferenceError,
    InvalidDataError,
    NotFoundError,
    OperationNotAllowedError,
)
from gwiriek.equipment.services.views import (
    ItemDetail,
    ItemSummary,
    item_detail,
    item_end_of_life,
    item_summary,
)

#: Statuses that require a date put into service.
STATUSES_IN_USE = frozenset({ItemStatus.IN_SERVICE, ItemStatus.WATCH})
#: Statuses that require a reason when entered.
STATUSES_REQUIRING_REASON = frozenset({ItemStatus.QUARANTINE, ItemStatus.RETIRED})
#: Statuses an item can be registered with.
INITIAL_STATUSES = frozenset(ItemStatus) - {ItemStatus.RETIRED}

_ROPE_FIELDS = ("rope_diameter_mm", "rope_length_m", "rope_type", "rope_terminations")


def items_queryset() -> QuerySet[Item]:
    """Items with what summaries need, loaded in a fixed number of queries."""
    return Item.objects.select_related("product_model__manufacturer").prefetch_related(
        Prefetch("assignments", queryset=Assignment.objects.select_related("person")),
        Prefetch("inspections", queryset=Inspection.objects.order_by("-inspected_on", "-id")),
    )


# --- Lookup ---------------------------------------------------------------------------------


def resolve_item(ref: int | str) -> Item:
    """Find an item by id, internal label or serial number.

    A bare reference is matched against all three. Prefix it with ``id:``, ``label:`` or
    ``serial:`` to remove ambiguity.

    Args:
        ref: The reference.

    Returns:
        The item, with its product model, manufacturer and assignments loaded.

    Raises:
        InvalidDataError: If an ``id:`` reference is not a number.
        NotFoundError: If nothing matches.
        AmbiguousReferenceError: If several items match.
    """
    text = str(ref).strip()
    kind, _sep, value = text.partition(":")
    if _sep and kind in {"id", "label", "serial"}:
        lookups = {kind: value.strip()}
    else:
        lookups = {"id": text, "label": text, "serial": text}

    query = Q()
    if "id" in lookups:
        if lookups["id"].isdigit():
            query |= Q(pk=int(lookups["id"]))
        elif len(lookups) == 1:
            raise InvalidDataError(_("An item id must be a number."))
    if lookups.get("label"):
        query |= Q(label__iexact=lookups["label"])
    if lookups.get("serial"):
        query |= Q(serial_number__iexact=lookups["serial"])

    matches = list(items_queryset().filter(query)[:10]) if query else []
    if not matches:
        raise NotFoundError(_("No item matches %(ref)s.") % {"ref": text})
    if len(matches) > 1:
        raise AmbiguousReferenceError(
            _("%(ref)s matches several items; prefix it with id:, label: or serial:.")
            % {"ref": text},
            {
                "candidates": [
                    {"id": i.pk, "label": i.label, "serial": i.serial_number} for i in matches
                ]
            },
        )
    return matches[0]


def list_items(
    *,
    statuses: Iterable[str] = (),
    family: str | None = None,
    query: str | None = None,
    person_id: int | None = None,
    limit: int | None = None,
) -> list[ItemSummary]:
    """List items, optionally filtered.

    Args:
        statuses: Keep only these statuses (all when empty).
        family: Keep only this product family.
        query: Text matched against label, serial, lot, model name, reference, manufacturer.
        person_id: Keep only items currently assigned to this person.
        limit: Maximum number of results.

    Returns:
        Matching items, by manufacturer, model and id.

    Raises:
        InvalidDataError: If a status or the family is unknown.
    """
    qs = items_queryset()
    statuses = list(statuses)
    unknown = [s for s in statuses if s not in ItemStatus.values]
    if unknown:
        raise InvalidDataError(
            _("Unknown status: %(values)s.") % {"values": ", ".join(unknown)},
            {"allowed": list(ItemStatus.values)},
        )
    if statuses:
        qs = qs.filter(status__in=statuses)
    if family:
        if family not in Family.values:
            raise InvalidDataError(
                _("Unknown family %(family)s.") % {"family": family},
                {"allowed": list(Family.values)},
            )
        qs = qs.filter(product_model__family=family)
    if query:
        qs = qs.filter(
            Q(label__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(lot_number__icontains=query)
            | Q(product_model__name__icontains=query)
            | Q(product_model__reference__icontains=query)
            | Q(product_model__manufacturer__name__icontains=query)
        )
    if person_id is not None:
        qs = qs.filter(assignments__person_id=person_id, assignments__ended_on__isnull=True)
    if limit is not None:
        qs = qs[: max(limit, 0)]
    return [item_summary(item) for item in qs]


def get_item(ref: int | str) -> ItemDetail:
    """Return the full life record of an item (see :func:`resolve_item` for ``ref``)."""
    return item_detail(resolve_item(ref), today())


# --- Validation and saving ------------------------------------------------------------------


def validate_item(item: Item, on: date | None = None) -> None:
    """Check rules spanning several fields of an item.

    Args:
        item: The item to check, saved or not.
        on: Reference day for dates in the future, defaults to today.

    Raises:
        InvalidDataError: Listing every broken rule by field.
    """
    on = on or today()
    errors: dict[str, list[str]] = {}

    def add(field: str, message: str) -> None:
        errors.setdefault(field, []).append(message)

    if not item.serial_number.strip() and not item.lot_number.strip():
        add("serial_number", _("Enter a serial number or a lot number."))
    for field in ("manufactured_on", "purchased_on", "in_service_on"):
        value = getattr(item, field)
        if value and value > on:
            add(field, _("This date is in the future."))
    if item.manufactured_on:
        for field in ("purchased_on", "in_service_on"):
            value = getattr(item, field)
            if value and value < item.manufactured_on:
                add(field, _("This date is before the date of manufacture."))
    if item.status in STATUSES_IN_USE and item.in_service_on is None:
        add("in_service_on", _("An item in use needs the date it was put into service."))
    if item.product_model_id and item.product_model.family != Family.ROPE:
        for field in _ROPE_FIELDS:
            if getattr(item, field) not in (None, ""):
                add(field, _("Rope fields only apply to ropes."))
    if errors:
        message = "; ".join(f"{f}: {m}" for f, msgs in errors.items() for m in msgs)
        raise InvalidDataError(message, {"fields": errors})


def check_status_change(previous: str | None, new: str, reason: str) -> None:
    """Check that an item may go from one status to another.

    Args:
        previous: Current status, ``None`` for a new item.
        new: Requested status.
        reason: Reason given for the change.

    Raises:
        OperationNotAllowedError: If the transition is forbidden.
        InvalidDataError: If a required reason is missing.
    """
    if previous == new:
        return
    if previous == ItemStatus.RETIRED:
        raise OperationNotAllowedError(_("A retired item cannot change status."))
    if previous is None and new not in INITIAL_STATUSES:
        raise OperationNotAllowedError(_("An item cannot be registered as retired."))
    if new in STATUSES_REQUIRING_REASON and not reason.strip():
        raise InvalidDataError(
            _("A reason is required to set the status to %(status)s.")
            % {"status": ItemStatus(new).label},
            {"fields": {"status": [_("A reason is required for this status.")]}},
        )


@transaction.atomic
def save_item(item: Item, *, actor: str, reason: str = "", on: date | None = None) -> Item:
    """Validate and save an item, recording any status change in the audit log.

    This is the only code path that writes items; the admin, the CLI and the MCP server
    all go through it.

    Args:
        item: A new or modified item.
        actor: Who makes the change (e.g. ``admin:alice``, ``cli:bob``, ``mcp``).
        reason: Why the status changed; required for quarantine and retirement.
        on: Date of the change, defaults to today.

    Returns:
        The saved item.

    Raises:
        InvalidDataError: If the item breaks a validation rule.
        OperationNotAllowedError: If the status change is forbidden, including putting
            into use an item past its end of life.
    """
    on = on or today()
    if not actor.strip():
        raise InvalidDataError(_("The actor of a change is required."))
    previous = None
    if item.pk is not None:
        previous = (
            Item.objects.select_for_update()
            .filter(pk=item.pk)
            .values_list("status", flat=True)
            .first()
        )
    status_changed = previous != item.status

    if status_changed:
        check_status_change(previous, item.status, reason)
        if item.status in STATUSES_IN_USE and item_end_of_life(item).is_expired(on):
            raise OperationNotAllowedError(
                _("This item has passed its end of life and cannot be put into use.")
            )
        if item.status == ItemStatus.RETIRED:
            item.retired_on = on

    validate_item(item)
    validated_save(item)

    if status_changed:
        StatusChange.objects.create(
            item=item,
            from_status=previous or "",
            to_status=item.status,
            actor=actor,
            reason=reason.strip(),
        )
        if item.status == ItemStatus.RETIRED:
            Assignment.objects.filter(item=item, ended_on__isnull=True).update(ended_on=on)
    return item


@transaction.atomic
def create_item(
    *,
    product_model_id: int,
    actor: str,
    serial_number: str = "",
    lot_number: str = "",
    label: str = "",
    manufactured_on: date | None = None,
    purchased_on: date | None = None,
    in_service_on: date | None = None,
    status: str = ItemStatus.IN_STOCK,
    rope_diameter_mm: Decimal | float | None = None,
    rope_length_m: Decimal | float | None = None,
    rope_type: str = "",
    rope_terminations: str = "",
    notes: str = "",
) -> ItemDetail:
    """Register a new item.

    Args:
        product_model_id: Catalogue entry of the item.
        actor: Who registers it, recorded in the audit log.
        serial_number: Serial number; this or ``lot_number`` is required.
        lot_number: Lot number.
        label: Internal company label, unique when set.
        manufactured_on: Date of manufacture.
        purchased_on: Date of purchase.
        in_service_on: Date first put into service.
        status: Initial status, anything but ``retired``.
        rope_diameter_mm: Ropes only: diameter.
        rope_length_m: Ropes only: length.
        rope_type: Ropes only: EN 1891 type, ``A`` or ``B``.
        rope_terminations: Ropes only: terminations.
        notes: Free notes.

    Returns:
        The life record of the new item.

    Raises:
        NotFoundError: If the product model does not exist.
        InvalidDataError: If the data breaks a validation rule.
        OperationNotAllowedError: If the initial status is not allowed.
    """
    product_model = get_or_not_found(
        lambda: ProductModel.objects.select_related("manufacturer").get(pk=product_model_id),
        _("No product model with id %(id)s.") % {"id": product_model_id},
    )
    if status not in ItemStatus.values:
        raise InvalidDataError(
            _("Unknown status: %(values)s.") % {"values": status},
            {"allowed": list(ItemStatus.values)},
        )
    item = Item(
        product_model=product_model,
        serial_number=serial_number.strip(),
        lot_number=lot_number.strip(),
        label=label.strip(),
        manufactured_on=manufactured_on,
        purchased_on=purchased_on,
        in_service_on=in_service_on,
        status=status,
        rope_diameter_mm=_decimal(rope_diameter_mm),
        rope_length_m=_decimal(rope_length_m),
        rope_type=rope_type,
        rope_terminations=rope_terminations.strip(),
        notes=notes,
    )
    save_item(item, actor=actor, reason=_("Registered"))
    return get_item(f"id:{item.pk}")


@transaction.atomic
def change_status(
    ref: int | str,
    status: str,
    *,
    actor: str,
    reason: str = "",
    on: date | None = None,
) -> ItemDetail:
    """Change the status of an item.

    Putting an item into use for the first time sets its date put into service.

    Args:
        ref: Item reference, see :func:`resolve_item`.
        status: New status.
        actor: Who makes the change, recorded in the audit log.
        reason: Why; required for quarantine and retirement.
        on: Date of the change, defaults to today.

    Returns:
        The updated life record.

    Raises:
        NotFoundError: If the item does not exist.
        InvalidDataError: If the status is unknown or a required reason is missing.
        OperationNotAllowedError: If the item is retired, already has this status, or is
            past its end of life and being put into use.
    """
    if status not in ItemStatus.values:
        raise InvalidDataError(
            _("Unknown status: %(values)s.") % {"values": status},
            {"allowed": list(ItemStatus.values)},
        )
    item = resolve_item(ref)
    if item.status == status:
        raise OperationNotAllowedError(
            _("The item already has the status %(status)s.") % {"status": ItemStatus(status).label}
        )
    on = on or today()
    item.status = status
    if status in STATUSES_IN_USE and item.in_service_on is None:
        item.in_service_on = on
    save_item(item, actor=actor, reason=reason, on=on)
    return get_item(f"id:{item.pk}")


def _decimal(value: Decimal | float | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))
