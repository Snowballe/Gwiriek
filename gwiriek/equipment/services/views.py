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
from gwiriek.equipment.services._common import today as current_date
from gwiriek.equipment.services.end_of_life import (
    DISCLAIMER,
    EndOfLife,
    LifespanRule,
    compute_end_of_life,
)
from gwiriek.equipment.services.periodicity import InspectionDeadline, inspection_deadline


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
    next_inspection_due_on: date | None
    inspection_overdue: bool


@dataclass(frozen=True)
class InspectorView:
    """A qualified person allowed to carry out periodic inspections (R4323-100).

    ``qualified`` and ``issues`` describe the authorization as of ``as_of``: issue codes are
    ``inactive``, ``not_authorized``, ``not_yet_valid``, ``expired``, ``no_scope`` and
    ``person_inactive``.
    """

    id: int
    kind: str
    name: str
    person_id: int | None
    organisation: str
    authorized_on: date | None
    authorization_reference: str
    authorization_document: str
    authorization_valid_until: date | None
    families: list[str]
    active: bool
    notes: str
    as_of: date
    qualified: bool
    issues: list[str]


@dataclass(frozen=True)
class ChecklistResultView:
    """One checklist point as it was when the inspection was recorded."""

    point: str
    result: str


@dataclass(frozen=True)
class InspectionView:
    """A recorded periodic inspection; immutable."""

    id: int
    item_id: int
    item: str
    inspected_on: date
    inspector_id: int
    inspector: str
    inspector_kind: str
    inspector_snapshot: dict[str, Any]
    decision: str
    checklist: list[ChecklistResultView]
    comment: str
    next_due_on: date | None
    report: str
    report_received_on: date | None
    report_archive_location: str
    resulting_status: str
    actor: str
    created_at: datetime


@dataclass(frozen=True)
class InspectionDeadlineView:
    """When an item's next periodic inspection is due, and what it is counted from."""

    due_on: date | None
    reference: str
    last_inspected_on: date | None
    days_remaining: int | None
    overdue: bool


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
    next_inspection: InspectionDeadlineView
    inspections: list[InspectionView]


@dataclass(frozen=True)
class DueItem:
    """An item with a deadline passed or near, or an end of life to determine by hand.

    ``end_of_life``, ``days_remaining``, ``basis`` and ``missing`` describe the end of life;
    ``inspection_*`` the periodic inspection; ``deadline`` is the nearest of both and
    ``deadline_kind`` says which (``end_of_life`` or ``inspection``). ``reasons`` lists
    what makes the item due: ``end_of_life_passed``, ``end_of_life_near``,
    ``inspection_overdue``, ``inspection_near``, ``end_of_life_undetermined``.
    """

    item: ItemSummary
    end_of_life: date | None
    days_remaining: int | None
    basis: str
    missing: list[str]
    inspection_due_on: date | None
    inspection_days_remaining: int | None
    inspection_reference: str
    last_inspected_on: date | None
    deadline: date | None
    deadline_kind: str | None
    deadline_days_remaining: int | None
    reasons: list[str]


@dataclass(frozen=True)
class DueReport:
    """Items to act on, as of a given day.

    ``overdue`` and ``upcoming`` are exclusive and sorted by nearest deadline;
    ``undetermined`` lists items whose end of life must be checked by hand, whatever their
    inspection deadline, so an item can also appear in one of the other two lists.
    """

    as_of: date
    within_days: int
    overdue: list[DueItem]
    upcoming: list[DueItem]
    undetermined: list[DueItem]
    disclaimer: str


@dataclass(frozen=True)
class DigestResult:
    """Outcome of an e-mail digest run.

    ``status`` is ``sent``, ``already_sent`` (same digest already sent today) or
    ``nothing_due``.
    """

    as_of: date
    status: str
    recipients: list[str]
    subject: str
    item_count: int
    fingerprint: str


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
        # Only a known service date or an in-use status means the item left storage: a
        # quarantined or retired item without a service date keeps its storage limit.
        commissioned=item.in_service_on is not None
        or item.status in (models.ItemStatus.IN_SERVICE, models.ItemStatus.WATCH),
    )


def latest_inspection(item: models.Item) -> models.Inspection | None:
    """Return the item's latest inspection, using prefetched data when available."""
    return next(iter(item.inspections.all()), None)


def item_inspection_deadline(item: models.Item) -> InspectionDeadline:
    """Compute when the item's next periodic inspection is due."""
    last = latest_inspection(item)
    return inspection_deadline(
        last_inspected_on=last.inspected_on if last else None,
        last_next_due=last.next_due_on if last else None,
        in_service_on=item.in_service_on,
        purchased_on=item.purchased_on,
        model_interval=item.product_model.inspection_interval_months,
    )


def inspection_deadline_view(deadline: InspectionDeadline, today: date) -> InspectionDeadlineView:
    """Present an inspection deadline relative to ``today``."""
    return InspectionDeadlineView(
        due_on=deadline.due_on,
        reference=deadline.reference.value,
        last_inspected_on=deadline.last_inspected_on,
        days_remaining=deadline.days_remaining(today),
        overdue=deadline.is_overdue(today),
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


def inspector_issues(inspector: models.Inspector, on: date) -> list[str]:
    """List why an inspector may not inspect on ``on`` (empty when qualified)."""
    issues = []
    if not inspector.active:
        issues.append("inactive")
    if inspector.person_id is not None and not inspector.person.active:  # type: ignore[union-attr]
        issues.append("person_inactive")
    if inspector.authorized_on is None:
        issues.append("not_authorized")
    elif inspector.authorized_on > on:
        issues.append("not_yet_valid")
    if inspector.authorization_valid_until is not None and inspector.authorization_valid_until < on:
        issues.append("expired")
    if not inspector.families:
        issues.append("no_scope")
    return issues


def inspector_view(inspector: models.Inspector, on: date) -> InspectorView:
    """Build an inspector view, with its qualification as of ``on``."""
    issues = inspector_issues(inspector, on)
    return InspectorView(
        id=inspector.pk,
        kind=inspector.kind,
        name=inspector.display_name,
        person_id=inspector.person_id,
        organisation=inspector.organisation,
        authorized_on=inspector.authorized_on,
        authorization_reference=inspector.authorization_reference,
        authorization_document=inspector.authorization_document.name or "",
        authorization_valid_until=inspector.authorization_valid_until,
        families=list(inspector.families),
        active=inspector.active,
        notes=inspector.notes,
        as_of=on,
        qualified=not issues,
        issues=issues,
    )


def inspection_view(inspection: models.Inspection) -> InspectionView:
    """Build an inspection view (select ``item`` and ``inspector`` for lists)."""
    snapshot = dict(inspection.inspector_snapshot)
    return InspectionView(
        id=inspection.pk,
        item_id=inspection.item_id,
        item=str(inspection.item),
        inspected_on=inspection.inspected_on,
        inspector_id=inspection.inspector_id,
        inspector=str(snapshot.get("name") or inspection.inspector),
        inspector_kind=str(snapshot.get("kind") or inspection.inspector.kind),
        inspector_snapshot=snapshot,
        decision=inspection.decision,
        checklist=[
            ChecklistResultView(point=str(r["point"]), result=str(r["result"]))
            for r in inspection.checklist_results
        ],
        comment=inspection.comment,
        next_due_on=inspection.next_due_on,
        report=inspection.report.name or "",
        report_received_on=inspection.report_received_on,
        report_archive_location=inspection.report_archive_location,
        resulting_status=inspection.resulting_status,
        actor=inspection.actor,
        created_at=inspection.created_at,
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


def item_summary(item: models.Item, today: date | None = None) -> ItemSummary:
    """Build an item summary (select the manufacturer, prefetch assignments, inspections).

    Args:
        item: The item.
        today: Reference day for ``inspection_overdue``, defaults to today.

    Returns:
        The summary.
    """
    eol = item_end_of_life(item)
    deadline = item_inspection_deadline(item)
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
        next_inspection_due_on=deadline.due_on,
        inspection_overdue=deadline.is_overdue(today or current_date()),
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
        next_inspection=inspection_deadline_view(item_inspection_deadline(item), today),
        inspections=[
            inspection_view(i)
            for i in item.inspections.select_related(
                "item__product_model__manufacturer", "inspector__person"
            )
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
