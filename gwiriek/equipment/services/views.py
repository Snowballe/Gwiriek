"""Read models returned by services: plain dataclasses, safe to serialise to JSON.

The CLI prints them, the MCP server returns them as structured content. Field names are
part of the public interface of both.
"""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from gwiriek.equipment import models
from gwiriek.equipment.services.end_of_life import (
    DISCLAIMER,
    EndOfLife,
    LifespanRule,
    compute_end_of_life,
)


@dataclass(frozen=True)
class ManufacturerView:
    """A manufacturer."""

    id: int
    name: str
    website: str
    contact: str


@dataclass(frozen=True)
class ProductModelSummary:
    """A catalogue entry, short form."""

    id: int
    manufacturer: str
    name: str
    reference: str
    family: str
    standards: list[str]
    lifetime_unlimited: bool
    max_lifetime_months: int | None
    max_storage_months: int | None
    max_service_months: int | None


@dataclass(frozen=True)
class ProductModelDetail:
    """A catalogue entry with its documentation and checklist."""

    id: int
    manufacturer: str
    manufacturer_id: int
    name: str
    reference: str
    family: str
    standards: list[str]
    lifetime_unlimited: bool
    max_lifetime_months: int | None
    max_storage_months: int | None
    max_service_months: int | None
    lifetime_source: str
    inspection_interval_months: int
    notice_url: str
    inspection_procedure_url: str
    checklist: list[str]
    notes: str


@dataclass(frozen=True)
class PersonView:
    """A person equipment can be assigned to."""

    id: int
    first_name: str
    last_name: str
    email: str
    active: bool


@dataclass(frozen=True)
class EndOfLifeView:
    """Computed end of life of an item, always to be confirmed against the notice."""

    end_date: date | None
    basis: str
    limited_by: list[str]
    missing: list[str]
    storage_exceeded: bool
    service_not_started: bool
    days_remaining: int | None
    expired: bool
    disclaimer: str


@dataclass(frozen=True)
class AssignmentView:
    """One assignment of an item."""

    id: int
    item_id: int
    kind: str
    person_id: int | None
    person: str | None
    location: str
    started_on: date
    ended_on: date | None
    actor: str
    notes: str


@dataclass(frozen=True)
class StatusChangeView:
    """One entry of an item's status audit log."""

    from_status: str
    to_status: str
    changed_at: datetime
    actor: str
    reason: str


@dataclass(frozen=True)
class ItemSummary:
    """An item, short form."""

    id: int
    label: str
    serial_number: str
    lot_number: str
    manufacturer: str
    product_model: str
    product_model_id: int
    family: str
    status: str
    end_of_life: date | None
    end_of_life_basis: str
    assigned_to: str | None


@dataclass(frozen=True)
class ItemDetail:
    """An item with its life record: dates, end of life, assignments, status history."""

    id: int
    label: str
    serial_number: str
    lot_number: str
    manufacturer: str
    product_model: str
    product_model_id: int
    family: str
    status: str
    manufactured_on: date | None
    purchased_on: date | None
    in_service_on: date | None
    retired_on: date | None
    rope_diameter_mm: float | None
    rope_length_m: float | None
    rope_type: str
    rope_terminations: str
    notes: str
    end_of_life: EndOfLifeView
    current_assignment: AssignmentView | None
    assignments: list[AssignmentView]
    status_history: list[StatusChangeView]


@dataclass(frozen=True)
class DueItem:
    """An item whose end of life is passed, near, or cannot be determined."""

    item: ItemSummary
    end_of_life: date | None
    days_remaining: int | None
    basis: str
    missing: list[str]


@dataclass(frozen=True)
class DueReport:
    """Items to act on, as of a given day."""

    as_of: date
    within_days: int
    overdue: list[DueItem]
    upcoming: list[DueItem]
    undetermined: list[DueItem]
    disclaimer: str


# --- Builders -------------------------------------------------------------------------------


def lifespan_rule(product_model: models.ProductModel) -> LifespanRule:
    """Build the lifespan rule of a product model."""
    return LifespanRule(
        max_lifetime_months=product_model.max_lifetime_months,
        max_storage_months=product_model.max_storage_months,
        max_service_months=product_model.max_service_months,
        unlimited=product_model.lifetime_unlimited,
    )


def item_end_of_life(item: models.Item) -> EndOfLife:
    """Compute the end of life of an item from its product model's rule."""
    return compute_end_of_life(
        lifespan_rule(item.product_model),
        item.manufactured_on,
        item.in_service_on,
        commissioned=item.in_service_on is not None or item.status != models.ItemStatus.IN_STOCK,
    )


def end_of_life_view(eol: EndOfLife, today: date) -> EndOfLifeView:
    """Present an end-of-life result relative to ``today``."""
    return EndOfLifeView(
        end_date=eol.date,
        basis=eol.basis.value,
        limited_by=[limit.value for limit in eol.limited_by],
        missing=list(eol.missing),
        storage_exceeded=eol.storage_exceeded,
        service_not_started=eol.service_not_started,
        days_remaining=eol.days_remaining(today),
        expired=eol.is_expired(today),
        disclaimer=str(DISCLAIMER),
    )


def manufacturer_view(manufacturer: models.Manufacturer) -> ManufacturerView:
    """Build a manufacturer view."""
    return ManufacturerView(
        id=manufacturer.pk,
        name=manufacturer.name,
        website=manufacturer.website,
        contact=manufacturer.contact,
    )


def _standard_codes(product_model: models.ProductModel) -> list[str]:
    return sorted(s.code for s in product_model.standards.all())


def product_model_summary(pm: models.ProductModel) -> ProductModelSummary:
    """Build a product model summary (prefetch ``standards`` for lists)."""
    return ProductModelSummary(
        id=pm.pk,
        manufacturer=pm.manufacturer.name,
        name=pm.name,
        reference=pm.reference,
        family=pm.family,
        standards=_standard_codes(pm),
        lifetime_unlimited=pm.lifetime_unlimited,
        max_lifetime_months=pm.max_lifetime_months,
        max_storage_months=pm.max_storage_months,
        max_service_months=pm.max_service_months,
    )


def product_model_detail(pm: models.ProductModel) -> ProductModelDetail:
    """Build a full product model view."""
    return ProductModelDetail(
        id=pm.pk,
        manufacturer=pm.manufacturer.name,
        manufacturer_id=pm.manufacturer_id,
        name=pm.name,
        reference=pm.reference,
        family=pm.family,
        standards=_standard_codes(pm),
        lifetime_unlimited=pm.lifetime_unlimited,
        max_lifetime_months=pm.max_lifetime_months,
        max_storage_months=pm.max_storage_months,
        max_service_months=pm.max_service_months,
        lifetime_source=pm.lifetime_source,
        inspection_interval_months=pm.inspection_interval_months,
        notice_url=pm.notice_url,
        inspection_procedure_url=pm.inspection_procedure_url,
        checklist=list(pm.checklist),
        notes=pm.notes,
    )


def person_view(person: models.Person) -> PersonView:
    """Build a person view."""
    return PersonView(
        id=person.pk,
        first_name=person.first_name,
        last_name=person.last_name,
        email=person.email,
        active=person.active,
    )


def assignment_view(assignment: models.Assignment) -> AssignmentView:
    """Build an assignment view."""
    return AssignmentView(
        id=assignment.pk,
        item_id=assignment.item_id,
        kind=assignment.kind,
        person_id=assignment.person_id,
        person=str(assignment.person) if assignment.person else None,
        location=assignment.location,
        started_on=assignment.started_on,
        ended_on=assignment.ended_on,
        actor=assignment.actor,
        notes=assignment.notes,
    )


def _assignment_label(assignment: models.Assignment | None) -> str | None:
    if assignment is None:
        return None
    return str(assignment)


def open_assignment(item: models.Item) -> models.Assignment | None:
    """Return the item's open assignment, using prefetched data when available."""
    return next((a for a in item.assignments.all() if a.ended_on is None), None)


def item_summary(item: models.Item) -> ItemSummary:
    """Build an item summary (select ``product_model__manufacturer``, prefetch assignments)."""
    eol = item_end_of_life(item)
    pm = item.product_model
    return ItemSummary(
        id=item.pk,
        label=item.label,
        serial_number=item.serial_number,
        lot_number=item.lot_number,
        manufacturer=pm.manufacturer.name,
        product_model=pm.name,
        product_model_id=pm.pk,
        family=pm.family,
        status=item.status,
        end_of_life=eol.date,
        end_of_life_basis=eol.basis.value,
        assigned_to=_assignment_label(open_assignment(item)),
    )


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def item_detail(item: models.Item, today: date) -> ItemDetail:
    """Build the full life record view of an item."""
    pm = item.product_model
    assignments = list(item.assignments.select_related("person").all())
    current = next((a for a in assignments if a.ended_on is None), None)
    return ItemDetail(
        id=item.pk,
        label=item.label,
        serial_number=item.serial_number,
        lot_number=item.lot_number,
        manufacturer=pm.manufacturer.name,
        product_model=pm.name,
        product_model_id=pm.pk,
        family=pm.family,
        status=item.status,
        manufactured_on=item.manufactured_on,
        purchased_on=item.purchased_on,
        in_service_on=item.in_service_on,
        retired_on=item.retired_on,
        rope_diameter_mm=_float(item.rope_diameter_mm),
        rope_length_m=_float(item.rope_length_m),
        rope_type=item.rope_type,
        rope_terminations=item.rope_terminations,
        notes=item.notes,
        end_of_life=end_of_life_view(item_end_of_life(item), today),
        current_assignment=assignment_view(current) if current else None,
        assignments=[assignment_view(a) for a in assignments],
        status_history=[
            StatusChangeView(
                from_status=c.from_status,
                to_status=c.to_status,
                changed_at=c.changed_at,
                actor=c.actor,
                reason=c.reason,
            )
            for c in item.status_changes.all()
        ],
    )


def to_jsonable(value: Any) -> Any:
    """Convert views (and lists of views) to JSON-compatible structures."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value
