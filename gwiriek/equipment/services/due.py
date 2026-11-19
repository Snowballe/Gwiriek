"""Deadline services: end of life and periodic inspections, passed, near or undetermined."""

from datetime import date, timedelta

from django.utils.translation import gettext as _

from gwiriek.equipment.models import ItemStatus
from gwiriek.equipment.services._common import today as current_date
from gwiriek.equipment.services.end_of_life import DISCLAIMER, Basis
from gwiriek.equipment.services.errors import InvalidDataError
from gwiriek.equipment.services.items import items_queryset
from gwiriek.equipment.services.views import (
    DueItem,
    DueReport,
    item_end_of_life,
    item_inspection_deadline,
    item_summary,
)

#: Bases for which no end-of-life date can be given although limits apply.
UNDETERMINED_BASES = frozenset({Basis.UNKNOWN, Basis.UNDEFINED})

END_OF_LIFE = "end_of_life"
INSPECTION = "inspection"


def due_items(
    within_days: int = 90, today: date | None = None, *, inspections_only: bool = False
) -> DueReport:
    """List items, in service or in stock, with a deadline passed or within ``within_days``.

    Two deadlines are considered for every item not retired: its end of life and its next
    periodic inspection (see :mod:`gwiriek.equipment.services.periodicity`; an item never
    inspected and without a date of entry is overdue). An item is ``overdue`` if either is
    passed, ``upcoming`` if either falls within the horizon.

    ``undetermined`` lists items whose end of life cannot be computed (missing dates or no
    lifespan data in the catalogue) or is only partially known beyond the horizon: they need
    checking by hand. Items with an unlimited lifetime, or only waiting to be put into
    service, are not listed there.

    Args:
        within_days: Horizon in days.
        today: Reference day, defaults to today.
        inspections_only: Consider only inspection deadlines (``undetermined`` is empty).

    Returns:
        The report, ``overdue`` and ``upcoming`` sorted by nearest deadline.

    Raises:
        InvalidDataError: If ``within_days`` is negative.
    """
    if within_days < 0:
        raise InvalidDataError(_("The number of days cannot be negative."))
    today = today or current_date()
    horizon = today + timedelta(days=within_days)
    overdue: list[DueItem] = []
    upcoming: list[DueItem] = []
    undetermined: list[DueItem] = []

    for item in items_queryset().exclude(status=ItemStatus.RETIRED):
        eol = item_end_of_life(item)
        deadline = item_inspection_deadline(item)
        reasons: list[str] = []
        candidates: list[tuple[date, str]] = []
        if not inspections_only and eol.date is not None:
            candidates.append((eol.date, END_OF_LIFE))
            if eol.date <= today:
                reasons.append("end_of_life_passed")
            elif eol.date <= horizon:
                reasons.append("end_of_life_near")
        if deadline.due_on is not None:
            candidates.append((deadline.due_on, INSPECTION))
        if deadline.is_overdue(today):
            reasons.append("inspection_overdue")
        elif deadline.due_on is not None and deadline.due_on <= horizon:
            reasons.append("inspection_near")
        eol_undetermined = not inspections_only and (
            (eol.date is None and eol.basis in UNDETERMINED_BASES)
            or (eol.basis == Basis.PARTIAL and eol.date is not None and eol.date > horizon)
        )
        if eol_undetermined:
            reasons.append("end_of_life_undetermined")
        if not reasons:
            continue

        nearest = min(candidates) if candidates else None
        if deadline.due_on is None and deadline.is_overdue(today):
            nearest = (today, INSPECTION)  # never inspected, no date of entry: due now
        entry = DueItem(
            item=item_summary(item, today),
            end_of_life=eol.date,
            days_remaining=eol.days_remaining(today),
            basis=eol.basis.value,
            missing=list(eol.missing),
            inspection_due_on=deadline.due_on,
            inspection_days_remaining=deadline.days_remaining(today),
            inspection_reference=deadline.reference.value,
            last_inspected_on=deadline.last_inspected_on,
            deadline=nearest[0] if nearest else None,
            deadline_kind=nearest[1] if nearest else None,
            deadline_days_remaining=(nearest[0] - today).days if nearest else None,
            reasons=reasons,
        )
        if {"end_of_life_passed", "inspection_overdue"} & set(reasons):
            overdue.append(entry)
        elif {"end_of_life_near", "inspection_near"} & set(reasons):
            upcoming.append(entry)
        if eol_undetermined:
            undetermined.append(entry)

    def by_deadline(entry: DueItem) -> tuple[date, int]:
        return entry.deadline or today, entry.item.id

    overdue.sort(key=by_deadline)
    upcoming.sort(key=by_deadline)
    return DueReport(
        as_of=today,
        within_days=within_days,
        overdue=overdue,
        upcoming=upcoming,
        undetermined=undetermined,
        disclaimer=str(DISCLAIMER),
    )
