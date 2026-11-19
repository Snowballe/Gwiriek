from datetime import date

import pytest

from gwiriek.equipment.services.periodicity import (
    DeadlineReference,
    compute_next_due,
    inspection_deadline,
    interval_months,
    latest_next_due,
)

D = date(2026, 1, 31)


@pytest.mark.parametrize(("model", "expected"), [(None, 12), (0, 12), (6, 6), (12, 12), (24, 12)])
def test_interval_is_capped_at_twelve_months(model, expected):
    assert interval_months(model) == expected


def test_latest_next_due_clamps_month_end():
    assert latest_next_due(D, 1) == date(2026, 2, 28)
    assert latest_next_due(D, 12) == date(2027, 1, 31)


def test_compute_next_due():
    assert compute_next_due(D, 12, None) == date(2027, 1, 31)
    assert compute_next_due(D, 6, None) == date(2026, 7, 31)
    assert compute_next_due(D, 12, None, requested=date(2026, 5, 1)) == date(2026, 5, 1)


def test_next_due_capped_by_end_of_life():
    assert compute_next_due(D, 12, date(2026, 6, 1)) == date(2026, 6, 1)
    assert compute_next_due(D, 12, date(2030, 1, 1)) == date(2027, 1, 31)
    assert compute_next_due(D, 12, D) is None
    assert compute_next_due(D, 12, date(2025, 1, 1)) is None


def test_deadline_from_last_inspection():
    deadline = inspection_deadline(
        last_inspected_on=D,
        last_next_due=date(2026, 7, 31),
        in_service_on=date(2020, 1, 1),
        purchased_on=None,
        model_interval=12,
    )
    assert deadline.reference == DeadlineReference.LAST_INSPECTION
    assert deadline.due_on == date(2026, 7, 31)
    assert deadline.days_remaining(date(2026, 7, 30)) == 1
    assert not deadline.is_overdue(date(2026, 7, 30))
    assert deadline.is_overdue(date(2026, 7, 31))


def test_never_inspected_uses_earliest_entry_date():
    deadline = inspection_deadline(
        last_inspected_on=None,
        last_next_due=None,
        in_service_on=date(2025, 6, 1),
        purchased_on=date(2025, 3, 1),
        model_interval=12,
    )
    assert deadline.reference == DeadlineReference.PURCHASE
    assert deadline.due_on == date(2026, 3, 1)
    deadline = inspection_deadline(
        last_inspected_on=None,
        last_next_due=None,
        in_service_on=date(2025, 6, 1),
        purchased_on=None,
        model_interval=6,
    )
    assert deadline.reference == DeadlineReference.IN_SERVICE
    assert deadline.due_on == date(2025, 12, 1)


def test_never_inspected_without_entry_is_due_at_once():
    deadline = inspection_deadline(
        last_inspected_on=None,
        last_next_due=None,
        in_service_on=None,
        purchased_on=None,
        model_interval=12,
    )
    assert deadline.reference == DeadlineReference.NONE
    assert deadline.due_on is None
    assert deadline.days_remaining(D) is None
    assert deadline.is_overdue(D)


def test_last_inspection_without_next_due_is_not_overdue():
    deadline = inspection_deadline(
        last_inspected_on=D,
        last_next_due=None,
        in_service_on=None,
        purchased_on=None,
        model_interval=12,
    )
    assert not deadline.is_overdue(date(2040, 1, 1))
