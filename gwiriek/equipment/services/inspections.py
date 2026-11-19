"""Periodic inspection services: recording an inspection and reading the history.

Rules applied by :func:`record_inspection`:

* a retired item is not inspected;
* the inspector must be qualified for the item's family on the inspection day and on the
  recording day (see :mod:`gwiriek.equipment.services.inspectors`);
* every point of the product model's checklist gets a result (``ok``, ``nok``, ``na``),
  frozen into the record with the point's text;
* a non-compliant point allows only the quarantine or retire decisions: "fit for use,
  under watch" keeps the item in use, which a failed check does not allow;
* an item past its end of life can only be quarantined or retired;
* a decision other than "fit for use" needs a comment, and so does an inspection with no
  checklist point;
* an external inspector's report is attached, or its date of receipt and archive location
  are given (R4323-102);
* the next inspection is due at the latest after the model's interval (at most 12 months),
  earlier on request, never after the end of life, and not at all after a retirement;
* the decision updates the item's status through ``save_item``, so the audit log records
  it;
* records cannot be modified: a mistake is corrected by a new inspection, dated the same
  day or later.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.core.files import File
from django.db import transaction
from django.utils.translation import gettext as _

from gwiriek.equipment.models import (
    AssignmentKind,
    CheckResult,
    Inspection,
    InspectionDecision,
    Inspector,
    InspectorKind,
    Item,
    ItemStatus,
)
from gwiriek.equipment.services import items as item_services
from gwiriek.equipment.services._common import get_or_not_found, today, validated_save
from gwiriek.equipment.services.errors import InvalidDataError, OperationNotAllowedError
from gwiriek.equipment.services.inspectors import check_can_inspect, resolve_inspector
from gwiriek.equipment.services.periodicity import compute_next_due, latest_next_due
from gwiriek.equipment.services.views import (
    InspectionView,
    inspection_view,
    item_end_of_life,
    latest_inspection,
    open_assignment,
)

#: Decisions allowed when a point is not compliant or the end of life is passed.
WITHDRAWAL_DECISIONS = frozenset({InspectionDecision.QUARANTINE, InspectionDecision.RETIRE})
#: Checklist results: a mapping, or (point, result) pairs to detect points given twice.
Results = Mapping[str, str] | Sequence[tuple[str, str]]
#: Decisions that need a comment.
DECISIONS_REQUIRING_COMMENT = WITHDRAWAL_DECISIONS | {InspectionDecision.WATCH}


@dataclass(frozen=True)
class InspectionPlan:
    """What recording an inspection will write, once every rule has been checked."""

    checklist: list[dict[str, str]]
    next_due_on: date | None
    resulting_status: str


def normalize_results(checklist: list[str], results: Results) -> list[dict[str, str]]:
    """Match checklist results to the product model's points and freeze them.

    Args:
        checklist: Points of the product model's checklist, in order.
        results: Result per point, keyed by the point's text (case-insensitive) or its
            1-based number. When the model has no checklist, the keys are recorded as
            free points.

    Returns:
        One ``{"point", "result"}`` entry per point, in checklist order.

    Raises:
        InvalidDataError: If a result is unknown, a point is unknown, given twice or
            missing.
    """
    allowed = set(CheckResult.values)
    pairs = list(results.items()) if isinstance(results, Mapping) else list(results)
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        result = str(value).strip().lower()
        if result not in allowed:
            raise InvalidDataError(
                _("Unknown result %(result)s for %(point)s.") % {"result": value, "point": key},
                {"allowed": sorted(allowed)},
            )
        cleaned.append((str(key).strip(), result))

    if not checklist:
        points = [(key, result) for key, result in cleaned if key]
        if len({key.lower() for key, _r in points}) != len(points):
            raise InvalidDataError(_("A checklist point is given twice."))
        return [{"point": key, "result": result} for key, result in points]

    by_text = {point.strip().lower(): index for index, point in enumerate(checklist)}
    found: dict[int, str] = {}
    for key, result in cleaned:
        if key.isdigit() and 1 <= int(key) <= len(checklist):
            index = int(key) - 1
        elif key.lower() in by_text:
            index = by_text[key.lower()]
        else:
            raise InvalidDataError(
                _("%(point)s is not a point of this model's checklist.") % {"point": key},
                {"checklist": checklist},
            )
        if index in found:
            raise InvalidDataError(
                _("The point %(point)s is given twice.") % {"point": checklist[index]}
            )
        found[index] = result
    missing = [point for index, point in enumerate(checklist) if index not in found]
    if missing:
        raise InvalidDataError(
            _("Every checklist point needs a result; missing: %(points)s.")
            % {"points": "; ".join(missing)},
            {"missing": missing, "checklist": checklist},
        )
    return [{"point": point, "result": found[i]} for i, point in enumerate(checklist)]


def resulting_status(item: Item, decision: str) -> str:
    """Status an item takes after an inspection decision.

    "Fit for use" keeps an item in stock or in service as it is, puts back into service an
    item under watch, and returns a quarantined item to service if it is still issued to a
    person or a site with a date put into service, to stock otherwise.

    Args:
        item: The inspected item.
        decision: An :class:`~gwiriek.equipment.models.InspectionDecision` value.

    Returns:
        The new status, possibly the current one.
    """
    if decision == InspectionDecision.WATCH:
        return ItemStatus.WATCH
    if decision == InspectionDecision.QUARANTINE:
        return ItemStatus.QUARANTINE
    if decision == InspectionDecision.RETIRE:
        return ItemStatus.RETIRED
    if item.status in (ItemStatus.IN_STOCK, ItemStatus.IN_SERVICE):
        return item.status
    if item.status == ItemStatus.WATCH:
        return ItemStatus.IN_SERVICE
    current = open_assignment(item)
    issued = current is not None and current.kind != AssignmentKind.STOCK
    return ItemStatus.IN_SERVICE if issued and item.in_service_on else ItemStatus.IN_STOCK


def prepare_inspection(
    item: Item,
    inspector: Inspector,
    *,
    decision: str,
    results: Results,
    inspected_on: date,
    next_due_on: date | None = None,
    comment: str = "",
    has_report: bool = False,
    report_received_on: date | None = None,
    report_archive_location: str = "",
    on: date | None = None,
) -> InspectionPlan:
    """Check every rule for recording an inspection, without writing anything.

    Args:
        item: The inspected item.
        inspector: Who inspected it.
        decision: An :class:`~gwiriek.equipment.models.InspectionDecision` value.
        results: Checklist results, see :func:`normalize_results`.
        inspected_on: Date of the inspection.
        next_due_on: Earlier next due date requested by the inspector.
        comment: Observations; required for decisions other than "fit for use".
        has_report: Whether a report file is attached.
        report_received_on: Date an external report was received, when not attached.
        report_archive_location: Where an external report is archived, when not attached.
        on: Recording day, defaults to today.

    Returns:
        The frozen checklist, the next due date and the status the item will take.

    Raises:
        InvalidDataError: If the data breaks a rule.
        OperationNotAllowedError: If the item or the inspector does not allow the
            inspection or the decision.
    """
    on = on or today()
    if decision not in InspectionDecision.values:
        raise InvalidDataError(
            _("Unknown decision %(decision)s.") % {"decision": decision},
            {"allowed": list(InspectionDecision.values)},
        )
    if item.status == ItemStatus.RETIRED:
        raise OperationNotAllowedError(_("A retired item cannot be inspected."))
    if inspected_on > on:
        raise InvalidDataError(
            _("An inspection cannot be dated in the future."),
            {"fields": {"inspected_on": [_("This date is in the future.")]}},
        )
    if item.manufactured_on and inspected_on < item.manufactured_on:
        raise InvalidDataError(
            _("An inspection cannot be dated before the date of manufacture."),
            {"fields": {"inspected_on": [_("This date is before the date of manufacture.")]}},
        )
    last = latest_inspection(item)
    if last is not None and inspected_on < last.inspected_on:
        raise InvalidDataError(
            _("The item's latest inspection is dated %(date)s; a new one cannot be earlier.")
            % {"date": last.inspected_on.strftime("%d/%m/%Y")},
            {"fields": {"inspected_on": [_("Earlier than the latest inspection.")]}},
        )

    family = item.product_model.family
    check_can_inspect(inspector, family, inspected_on)
    if on != inspected_on:
        check_can_inspect(inspector, family, on)

    checklist = normalize_results(list(item.product_model.checklist), results)
    failed = [r["point"] for r in checklist if r["result"] == CheckResult.NOK]
    if failed and decision not in WITHDRAWAL_DECISIONS:
        raise OperationNotAllowedError(
            _("A point is not compliant (%(points)s): the item can only be quarantined or retired.")
            % {"points": "; ".join(failed)},
            {"failed_points": failed, "allowed": sorted(d.value for d in WITHDRAWAL_DECISIONS)},
        )
    eol = item_end_of_life(item)
    if eol.is_expired(on) and decision not in WITHDRAWAL_DECISIONS:
        raise OperationNotAllowedError(
            _("This item has passed its end of life: it can only be quarantined or retired."),
            {"end_of_life": eol.date.isoformat() if eol.date else None},
        )
    if not comment.strip():
        if decision in DECISIONS_REQUIRING_COMMENT:
            raise InvalidDataError(
                _("A comment is required for the decision %(decision)s.")
                % {"decision": InspectionDecision(decision).label},
                {"fields": {"comment": [_("Explain the decision.")]}},
            )
        if not checklist:
            raise InvalidDataError(
                _("This model has no checklist: describe what was checked in the comment."),
                {"fields": {"comment": [_("Describe what was checked.")]}},
            )

    if report_received_on is not None and not (inspected_on <= report_received_on <= on):
        raise InvalidDataError(
            _("The report cannot be received before the inspection or in the future."),
            {"fields": {"report_received_on": [_("Invalid date.")]}},
        )
    if (
        inspector.kind == InspectorKind.EXTERNAL
        and not has_report
        and (report_received_on is None or not report_archive_location.strip())
    ):
        raise InvalidDataError(
            _(
                "For an external inspector, attach the report or give the date it was "
                "received and where it is archived."
            ),
            {"fields": {"report": [_("Attach the report or give its archive details.")]}},
        )

    if decision == InspectionDecision.RETIRE:
        if next_due_on is not None:
            raise InvalidDataError(_("A retired item has no next inspection."))
        due = None
    else:
        latest = latest_next_due(inspected_on, item.product_model.inspection_interval_months)
        if next_due_on is not None and not (inspected_on < next_due_on <= latest):
            raise InvalidDataError(
                _("The next inspection must be after the inspection and no later than %(date)s.")
                % {"date": latest.strftime("%d/%m/%Y")},
                {"fields": {"next_due_on": [_("Invalid date.")]}, "latest": latest.isoformat()},
            )
        due = compute_next_due(
            inspected_on,
            item.product_model.inspection_interval_months,
            eol.date,
            next_due_on,
        )
    return InspectionPlan(
        checklist=checklist, next_due_on=due, resulting_status=resulting_status(item, decision)
    )


def _snapshot(inspector: Inspector) -> dict[str, Any]:
    return {
        "name": inspector.display_name,
        "kind": inspector.kind,
        "organisation": inspector.organisation,
        "authorized_on": inspector.authorized_on.isoformat() if inspector.authorized_on else None,
        "authorization_reference": inspector.authorization_reference,
        "authorization_valid_until": (
            inspector.authorization_valid_until.isoformat()
            if inspector.authorization_valid_until
            else None
        ),
        "families": list(inspector.families),
    }


@transaction.atomic
def record_inspection(
    ref: int | str,
    *,
    inspector_id: int,
    decision: str,
    results: Results,
    actor: str,
    inspected_on: date | None = None,
    next_due_on: date | None = None,
    comment: str = "",
    report: File | None = None,  # type: ignore[type-arg]
    report_received_on: date | None = None,
    report_archive_location: str = "",
) -> InspectionView:
    """Record a periodic inspection and apply its decision to the item's status.

    See the module docstring for the rules.

    Args:
        ref: Item reference, see :func:`~gwiriek.equipment.services.items.resolve_item`.
        inspector_id: Id of the inspector.
        decision: ``valid``, ``watch``, ``quarantine`` or ``retire``.
        results: Checklist results keyed by point text or 1-based number, valued ``ok``,
            ``nok`` or ``na``; a mapping or a sequence of pairs.
        actor: Who records it, also recorded in the status audit log.
        inspected_on: Date of the inspection, defaults to today.
        next_due_on: Earlier next due date than the model's interval.
        comment: Observations.
        report: Report file.
        report_received_on: Date an external report was received, when not attached.
        report_archive_location: Where an external report is archived, when not attached.

    Returns:
        The recorded inspection.

    Raises:
        NotFoundError: If the item or the inspector does not exist.
        InvalidDataError: If the data breaks a rule.
        OperationNotAllowedError: If the item, the inspector or the decision is not allowed.
    """
    if not actor.strip():
        raise InvalidDataError(_("The actor of a change is required."))
    on = today()
    inspected_on = inspected_on or on
    pk = item_services.resolve_item(ref).pk
    Item.objects.select_for_update().filter(pk=pk).first()  # serialise records per item
    item = item_services.resolve_item(f"id:{pk}")
    inspector = resolve_inspector(inspector_id)
    plan = prepare_inspection(
        item,
        inspector,
        decision=decision,
        results=results,
        inspected_on=inspected_on,
        next_due_on=next_due_on,
        comment=comment,
        has_report=report is not None,
        report_received_on=report_received_on,
        report_archive_location=report_archive_location,
        on=on,
    )
    inspection = Inspection(
        item=item,
        inspected_on=inspected_on,
        inspector=inspector,
        inspector_snapshot=_snapshot(inspector),
        decision=decision,
        checklist_results=plan.checklist,
        comment=comment.strip(),
        next_due_on=plan.next_due_on,
        report_received_on=report_received_on,
        report_archive_location=report_archive_location.strip(),
        resulting_status=plan.resulting_status,
        actor=actor,
    )
    if report is not None:
        inspection.report.save(report.name or "report", report, save=False)
    validated_save(inspection)

    if plan.resulting_status != item.status:
        item.status = plan.resulting_status
        if item.status in item_services.STATUSES_IN_USE and item.in_service_on is None:
            item.in_service_on = inspected_on
        reason = _("Inspection of %(date)s: %(decision)s.") % {
            "date": inspected_on.strftime("%d/%m/%Y"),
            "decision": InspectionDecision(decision).label,
        }
        if inspection.comment:
            reason = f"{reason} {inspection.comment}"
        item_services.save_item(item, actor=actor, reason=reason, on=on)
    return inspection_view(_inspections().get(pk=inspection.pk))


def _inspections() -> Any:
    return Inspection.objects.select_related("item__product_model__manufacturer", "inspector")


def get_inspection(inspection_id: int) -> InspectionView:
    """Return a recorded inspection.

    Args:
        inspection_id: Id of the inspection.

    Returns:
        The inspection.

    Raises:
        NotFoundError: If it does not exist.
    """
    return inspection_view(
        get_or_not_found(
            lambda: _inspections().get(pk=inspection_id),
            _("No inspection with id %(id)s.") % {"id": inspection_id},
        )
    )


def list_inspections(
    *,
    item_ref: int | str | None = None,
    since: date | None = None,
    inspector_id: int | None = None,
    limit: int | None = None,
) -> list[InspectionView]:
    """List recorded inspections, latest first.

    Args:
        item_ref: Keep only this item's inspections (see ``resolve_item``).
        since: Keep only inspections on or after this day.
        inspector_id: Keep only this inspector's inspections.
        limit: Maximum number of results.

    Returns:
        Inspections, by date then id, latest first.

    Raises:
        NotFoundError: If the item does not exist.
        AmbiguousReferenceError: If the item reference matches several items.
    """
    qs = _inspections().order_by("-inspected_on", "-id")
    if item_ref is not None:
        qs = qs.filter(item=item_services.resolve_item(item_ref))
    if since is not None:
        qs = qs.filter(inspected_on__gte=since)
    if inspector_id is not None:
        qs = qs.filter(inspector_id=inspector_id)
    if limit is not None:
        qs = qs[: max(limit, 0)]
    return [inspection_view(i) for i in qs]
