"""Periodic inspection deadlines. Pure functions, no database access.

French regulation (R4323-99, arrêté of 19 March 1993) requires fall protection equipment,
in service or in stock, to have been inspected less than 12 months before it is used. The
rules applied here:

* after an inspection, the next one is due at the latest ``inspected_on`` + the product
  model's interval (itself at most 12 months), earlier if the inspector asks for it, and
  never after the item's end of life;
* an item never inspected counts from the day it entered the company: the earliest known
  of its date put into service and its date of purchase. With neither date, the inspection
  is due at once, whatever the date of manufacture: nothing proves it was ever checked.
"""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from gwiriek.equipment.services.end_of_life import add_months

#: Longest interval allowed between two inspections, in months.
MAX_INTERVAL_MONTHS = 12


class DeadlineReference(StrEnum):
    """What an inspection deadline is counted from."""

    LAST_INSPECTION = "last_inspection"
    IN_SERVICE = "in_service"
    PURCHASE = "purchase"
    NONE = "none"
    """No inspection and no date of entry: due at once."""


@dataclass(frozen=True)
class InspectionDeadline:
    """When an item's next periodic inspection is due."""

    due_on: dt.date | None
    reference: DeadlineReference
    last_inspected_on: dt.date | None = None

    def days_remaining(self, today: dt.date) -> int | None:
        """Days from ``today`` to the deadline (negative once passed)."""
        return None if self.due_on is None else (self.due_on - today).days

    def is_overdue(self, today: dt.date) -> bool:
        """Whether the item may not be used on ``today`` without a new inspection."""
        if self.reference == DeadlineReference.NONE:
            return True
        return self.due_on is not None and self.due_on <= today


def interval_months(model_interval: int | None) -> int:
    """Interval to apply: the model's, capped at the regulatory maximum."""
    if not model_interval or model_interval > MAX_INTERVAL_MONTHS:
        return MAX_INTERVAL_MONTHS
    return model_interval


def latest_next_due(inspected_on: dt.date, model_interval: int | None) -> dt.date:
    """Latest date the next inspection may be set to, before the end-of-life cap."""
    return add_months(inspected_on, interval_months(model_interval))


def compute_next_due(
    inspected_on: dt.date,
    model_interval: int | None,
    end_of_life: dt.date | None,
    requested: dt.date | None = None,
) -> dt.date | None:
    """Compute the next due date of an inspection.

    Args:
        inspected_on: Date of the inspection.
        model_interval: Inspection interval of the product model, in months.
        end_of_life: Computed end of life of the item, if known.
        requested: Earlier date asked for by the inspector; validated by the caller.

    Returns:
        The earliest of the interval deadline, the requested date and the end of life, or
        ``None`` if the end of life is not after the inspection (no later use possible).
    """
    due = latest_next_due(inspected_on, model_interval)
    if requested is not None:
        due = min(due, requested)
    if end_of_life is not None:
        if end_of_life <= inspected_on:
            return None
        due = min(due, end_of_life)
    return due


def inspection_deadline(
    *,
    last_inspected_on: dt.date | None,
    last_next_due: dt.date | None,
    in_service_on: dt.date | None,
    purchased_on: dt.date | None,
    model_interval: int | None,
) -> InspectionDeadline:
    """Compute when an item's next periodic inspection is due.

    Args:
        last_inspected_on: Date of the latest inspection, if any.
        last_next_due: Next due date recorded by that inspection.
        in_service_on: Date the item was put into service.
        purchased_on: Date of purchase, taken as the date it entered the stock.
        model_interval: Inspection interval of the product model, in months.

    Returns:
        The deadline and what it is counted from.
    """
    if last_inspected_on is not None:
        return InspectionDeadline(
            due_on=last_next_due,
            reference=DeadlineReference.LAST_INSPECTION,
            last_inspected_on=last_inspected_on,
        )
    entries = [
        (day, ref)
        for day, ref in (
            (in_service_on, DeadlineReference.IN_SERVICE),
            (purchased_on, DeadlineReference.PURCHASE),
        )
        if day is not None
    ]
    if not entries:
        return InspectionDeadline(due_on=None, reference=DeadlineReference.NONE)
    day, ref = min(entries, key=lambda entry: entry[0])
    return InspectionDeadline(
        due_on=add_months(day, interval_months(model_interval)), reference=ref
    )
