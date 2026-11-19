"""Deadline services: items whose end of life is passed, near, or undetermined."""

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
    item_summary,
)

#: Bases for which no end-of-life date can be given although limits apply.
UNDETERMINED_BASES = frozenset({Basis.UNKNOWN, Basis.UNDEFINED})


def due_items(within_days: int = 90, today: date | None = None) -> DueReport:
    """List items not retired whose end of life is passed or within ``within_days``.

    Also lists, as ``undetermined``, items whose end of life cannot be computed (missing
    dates or no lifespan data in the catalogue) or is only partially known beyond the
    horizon: they need checking by hand. Items with an unlimited lifetime, or only waiting
    to be put into service, are not listed.

    Args:
        within_days: Horizon in days.
        today: Reference day, defaults to today.

    Returns:
        The report, each list sorted by end of life.

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
        entry = DueItem(
            item=item_summary(item),
            end_of_life=eol.date,
            days_remaining=eol.days_remaining(today),
            basis=eol.basis.value,
            missing=list(eol.missing),
        )
        if eol.date is None:
            if eol.basis in UNDETERMINED_BASES:
                undetermined.append(entry)
        elif eol.date <= today:
            overdue.append(entry)
        elif eol.date <= horizon:
            upcoming.append(entry)
        elif eol.basis == Basis.PARTIAL:
            undetermined.append(entry)  # the real end of life may be earlier

    def by_date(entry: DueItem) -> tuple[date, int]:
        assert entry.end_of_life is not None
        return entry.end_of_life, entry.item.id

    overdue.sort(key=by_date)
    upcoming.sort(key=by_date)
    return DueReport(
        as_of=today,
        within_days=within_days,
        overdue=overdue,
        upcoming=upcoming,
        undetermined=undetermined,
        disclaimer=str(DISCLAIMER),
    )
