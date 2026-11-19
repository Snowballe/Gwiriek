"""End-of-life computation from manufacturer lifespan rules.

Pure functions, no database access. The rule, for each duration the manufacturer gives:

* **manufacture**: date of manufacture + maximum lifetime;
* **storage**: date of manufacture + maximum storage, which binds if the item has not been
  put into service yet, or was put into service after that deadline;
* **service**: date put into service + maximum service life.

The end of life is the earliest bound. It is the first day the item must no longer be used.
Missing dates are reported explicitly instead of being guessed: a purchase date is never a
substitute for a manufacture date, because it would give a later, unsafe end of life.

Every result is indicative and must be confirmed against the manufacturer's instructions.
"""

import calendar
import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from django.utils.translation import gettext_lazy as _

DISCLAIMER = _("Indicative: to be confirmed against the manufacturer's instructions.")


class Basis(StrEnum):
    """How reliable the computed end of life is."""

    COMPLETE = "complete"
    """Every applicable bound was computed."""
    PARTIAL = "partial"
    """Some bounds lack data: the real end of life may be earlier than ``date``."""
    PENDING = "pending"
    """Only the service bound applies and the item has not been put into service."""
    UNKNOWN = "unknown"
    """Bounds apply but none can be computed for lack of data."""
    UNLIMITED = "unlimited"
    """The manufacturer states no lifetime limit."""
    UNDEFINED = "undefined"
    """The catalogue holds no lifespan data for this model."""


class Limit(StrEnum):
    """Which rule produced a bound."""

    MANUFACTURE = "manufacture"
    STORAGE = "storage"
    SERVICE = "service"


@dataclass(frozen=True)
class LifespanRule:
    """Lifespan data of a product model, durations in months (``None`` = not given)."""

    max_lifetime_months: int | None = None
    max_storage_months: int | None = None
    max_service_months: int | None = None
    unlimited: bool = False

    def __post_init__(self) -> None:
        durations = (self.max_lifetime_months, self.max_storage_months, self.max_service_months)
        if any(d is not None and d <= 0 for d in durations):
            raise ValueError("Durations must be positive.")
        if self.unlimited and any(d is not None for d in durations):
            raise ValueError("An unlimited rule cannot have durations.")

    @property
    def has_durations(self) -> bool:
        """Whether at least one duration is given."""
        return any(
            d is not None
            for d in (self.max_lifetime_months, self.max_storage_months, self.max_service_months)
        )


@dataclass(frozen=True)
class Bound:
    """One computed limit date."""

    limit: Limit
    date: dt.date


@dataclass(frozen=True)
class EndOfLife:
    """Result of :func:`compute_end_of_life`."""

    basis: Basis
    date: dt.date | None = None
    limited_by: tuple[Limit, ...] = ()
    bounds: tuple[Bound, ...] = ()
    missing: tuple[str, ...] = ()
    storage_exceeded: bool = False
    service_not_started: bool = False

    def days_remaining(self, today: dt.date) -> int | None:
        """Days from ``today`` to the end of life (negative once passed)."""
        return None if self.date is None else (self.date - today).days

    def is_expired(self, today: dt.date) -> bool:
        """Whether the item must no longer be used on ``today``."""
        return self.date is not None and self.date <= today


def add_months(start: dt.date, months: int) -> dt.date:
    """Add calendar months, clamping to the last day of the month (31 Jan + 1 = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def compute_end_of_life(
    rule: LifespanRule,
    manufactured_on: dt.date | None,
    in_service_on: dt.date | None,
    *,
    commissioned: bool | None = None,
) -> EndOfLife:
    """Compute the end of life of one item.

    Args:
        rule: Lifespan data of the item's product model.
        manufactured_on: Date of manufacture, if known.
        in_service_on: Date the item was first put into service, if known.
        commissioned: Whether the item has been put into service. Defaults to
            ``in_service_on is not None``; pass ``True`` for an item in use whose
            commissioning date is unknown, so the gap is reported as missing data.

    Returns:
        The end of life, with its basis and the data that would be needed to complete it.

    Raises:
        ValueError: If the item was put into service before it was manufactured.
    """
    if manufactured_on and in_service_on and in_service_on < manufactured_on:
        raise ValueError("An item cannot be put into service before it is manufactured.")
    if commissioned is None:
        commissioned = in_service_on is not None
    if rule.unlimited:
        return EndOfLife(basis=Basis.UNLIMITED)
    if not rule.has_durations:
        return EndOfLife(basis=Basis.UNDEFINED)

    bounds: list[Bound] = []
    missing: set[str] = set()
    storage_exceeded = False
    service_not_started = False

    if rule.max_lifetime_months is not None:
        if manufactured_on is None:
            missing.add("manufactured_on")
        else:
            bounds.append(
                Bound(Limit.MANUFACTURE, add_months(manufactured_on, rule.max_lifetime_months))
            )

    if rule.max_storage_months is not None:
        if manufactured_on is None:
            missing.add("manufactured_on")
        else:
            deadline = add_months(manufactured_on, rule.max_storage_months)
            if not commissioned:
                bounds.append(Bound(Limit.STORAGE, deadline))
            elif in_service_on is None:
                missing.add("in_service_on")
            elif in_service_on > deadline:
                bounds.append(Bound(Limit.STORAGE, deadline))
                storage_exceeded = True

    if rule.max_service_months is not None:
        if in_service_on is not None:
            bounds.append(Bound(Limit.SERVICE, add_months(in_service_on, rule.max_service_months)))
        elif commissioned:
            missing.add("in_service_on")
        else:
            service_not_started = True

    missing_fields = tuple(sorted(missing))
    if not bounds:
        basis = Basis.UNKNOWN if missing else Basis.PENDING
        return EndOfLife(
            basis=basis, missing=missing_fields, service_not_started=service_not_started
        )

    end = min(b.date for b in bounds)
    return EndOfLife(
        basis=Basis.PARTIAL if missing else Basis.COMPLETE,
        date=end,
        limited_by=tuple(b.limit for b in bounds if b.date == end),
        bounds=tuple(bounds),
        missing=missing_fields,
        storage_exceeded=storage_exceeded,
        service_not_started=service_not_started,
    )
