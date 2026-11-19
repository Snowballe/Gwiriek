"""Tests for the pure end-of-life computation (no database)."""

from datetime import date

import pytest

from gwiriek.equipment.services.end_of_life import (
    Basis,
    Bound,
    EndOfLife,
    LifespanRule,
    Limit,
    add_months,
    compute_end_of_life,
)

D = date


# --- add_months -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (D(2020, 1, 15), 0, D(2020, 1, 15)),
        (D(2020, 1, 15), 1, D(2020, 2, 15)),
        (D(2020, 11, 30), 3, D(2021, 2, 28)),  # year wrap and clamp
        (D(2019, 1, 31), 1, D(2019, 2, 28)),  # clamp, common year
        (D(2020, 1, 31), 1, D(2020, 2, 29)),  # clamp, leap year
        (D(2020, 2, 29), 12, D(2021, 2, 28)),  # leap day + 1 year
        (D(2020, 2, 29), 48, D(2024, 2, 29)),  # leap day + 4 years
        (D(2016, 3, 31), 120, D(2026, 3, 31)),  # 10 years
        (D(2020, 3, 31), -1, D(2020, 2, 29)),  # negative
        (D(2020, 1, 1), -1, D(2019, 12, 1)),  # negative across a year
        (D(2020, 12, 31), 12, D(2021, 12, 31)),
    ],
)
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


# --- LifespanRule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_lifetime_months": 0},
        {"max_storage_months": -1},
        {"max_service_months": 0},
        {"unlimited": True, "max_lifetime_months": 120},
        {"unlimited": True, "max_service_months": 12},
    ],
)
def test_rule_rejects_invalid_durations(kwargs):
    with pytest.raises(ValueError, match=r"Durations|unlimited"):
        LifespanRule(**kwargs)


def test_rule_has_durations():
    assert not LifespanRule().has_durations
    assert not LifespanRule(unlimited=True).has_durations
    assert LifespanRule(max_storage_months=60).has_durations


# --- Cases without a computable bound --------------------------------------------------------


def test_unlimited_rule():
    result = compute_end_of_life(LifespanRule(unlimited=True), D(2000, 1, 1), D(2001, 1, 1))
    assert result == EndOfLife(basis=Basis.UNLIMITED)
    assert not result.is_expired(D(2100, 1, 1))
    assert result.days_remaining(D(2100, 1, 1)) is None


def test_undefined_rule():
    result = compute_end_of_life(LifespanRule(), D(2000, 1, 1), None)
    assert result.basis == Basis.UNDEFINED
    assert result.date is None


def test_lifetime_without_manufacture_date_is_unknown():
    result = compute_end_of_life(LifespanRule(max_lifetime_months=120), None, D(2020, 1, 1))
    assert result.basis == Basis.UNKNOWN
    assert result.date is None
    assert result.missing == ("manufactured_on",)


def test_purchase_date_is_never_used_as_manufacture_date():
    # compute_end_of_life has no purchase parameter on purpose: nothing to substitute.
    result = compute_end_of_life(LifespanRule(max_lifetime_months=120), None, None)
    assert result.basis == Basis.UNKNOWN


def test_service_only_not_started_is_pending():
    result = compute_end_of_life(LifespanRule(max_service_months=120), D(2020, 1, 1), None)
    assert result.basis == Basis.PENDING
    assert result.service_not_started
    assert result.missing == ()


def test_service_only_commissioned_without_date_is_unknown():
    result = compute_end_of_life(
        LifespanRule(max_service_months=120), D(2020, 1, 1), None, commissioned=True
    )
    assert result.basis == Basis.UNKNOWN
    assert result.missing == ("in_service_on",)
    assert not result.service_not_started


# --- Single bounds --------------------------------------------------------------------------


def test_lifetime_from_manufacture():
    result = compute_end_of_life(LifespanRule(max_lifetime_months=120), D(2016, 5, 10), None)
    assert result.basis == Basis.COMPLETE
    assert result.date == D(2026, 5, 10)
    assert result.limited_by == (Limit.MANUFACTURE,)
    assert result.bounds == (Bound(Limit.MANUFACTURE, D(2026, 5, 10)),)


def test_lifetime_ignores_service_date():
    result = compute_end_of_life(
        LifespanRule(max_lifetime_months=120), D(2016, 5, 10), D(2020, 1, 1)
    )
    assert result.date == D(2026, 5, 10)


def test_service_life_from_commissioning():
    result = compute_end_of_life(LifespanRule(max_service_months=60), None, D(2022, 3, 1))
    assert result.basis == Basis.COMPLETE
    assert result.date == D(2027, 3, 1)
    assert result.limited_by == (Limit.SERVICE,)


# --- Storage --------------------------------------------------------------------------------

BEAL = LifespanRule(max_lifetime_months=180, max_storage_months=60, max_service_months=120)


def test_storage_binds_while_in_stock():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), None)
    assert result.basis == Basis.COMPLETE
    assert result.date == D(2025, 1, 1)
    assert result.limited_by == (Limit.STORAGE,)
    assert result.service_not_started
    assert {b.limit for b in result.bounds} == {Limit.MANUFACTURE, Limit.STORAGE}


def test_storage_respected_service_binds():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), D(2022, 1, 1))
    assert result.date == D(2032, 1, 1)
    assert result.limited_by == (Limit.SERVICE,)
    assert not result.storage_exceeded
    assert Limit.STORAGE not in {b.limit for b in result.bounds}


def test_put_into_service_on_storage_deadline_is_allowed():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), D(2025, 1, 1))
    assert not result.storage_exceeded
    assert result.date == D(2035, 1, 1)
    assert result.limited_by == (Limit.MANUFACTURE, Limit.SERVICE)


def test_put_into_service_after_storage_deadline():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), D(2025, 1, 2))
    assert result.storage_exceeded
    assert result.date == D(2025, 1, 1)
    assert result.limited_by == (Limit.STORAGE,)
    assert result.is_expired(D(2025, 1, 2))


def test_storage_without_manufacture_date():
    result = compute_end_of_life(LifespanRule(max_storage_months=60), None, None)
    assert result.basis == Basis.UNKNOWN
    assert result.missing == ("manufactured_on",)


def test_storage_commissioned_without_date_is_partial():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), None, commissioned=True)
    assert result.basis == Basis.PARTIAL
    assert result.date == D(2035, 1, 1)  # manufacture bound only
    assert result.missing == ("in_service_on",)


def test_storage_only_in_service_within_limit_is_pending_free():
    # Only a storage limit, respected: no bound applies any more.
    result = compute_end_of_life(LifespanRule(max_storage_months=60), D(2020, 1, 1), D(2021, 1, 1))
    assert result.basis == Basis.PENDING
    assert result.date is None
    assert not result.service_not_started


# --- Combinations ---------------------------------------------------------------------------


def test_manufacture_binds_before_service():
    rule = LifespanRule(max_lifetime_months=120, max_service_months=120)
    result = compute_end_of_life(rule, D(2016, 1, 1), D(2018, 6, 1))
    assert result.date == D(2026, 1, 1)
    assert result.limited_by == (Limit.MANUFACTURE,)


def test_service_binds_before_manufacture():
    rule = LifespanRule(max_lifetime_months=120, max_service_months=36)
    result = compute_end_of_life(rule, D(2016, 1, 1), D(2018, 6, 1))
    assert result.date == D(2021, 6, 1)
    assert result.limited_by == (Limit.SERVICE,)


def test_tie_reports_every_limit():
    rule = LifespanRule(max_lifetime_months=120, max_service_months=60)
    result = compute_end_of_life(rule, D(2016, 1, 1), D(2021, 1, 1))
    assert result.limited_by == (Limit.MANUFACTURE, Limit.SERVICE)


def test_partial_when_manufacture_date_missing():
    rule = LifespanRule(max_lifetime_months=120, max_service_months=60)
    result = compute_end_of_life(rule, None, D(2021, 1, 1))
    assert result.basis == Basis.PARTIAL
    assert result.date == D(2026, 1, 1)
    assert result.missing == ("manufactured_on",)


def test_missing_lists_each_field_once_sorted():
    result = compute_end_of_life(BEAL, None, None, commissioned=True)
    assert result.basis == Basis.UNKNOWN
    assert result.missing == ("in_service_on", "manufactured_on")


def test_commissioned_defaults_to_presence_of_service_date():
    implicit = compute_end_of_life(BEAL, D(2020, 1, 1), D(2021, 1, 1))
    explicit = compute_end_of_life(BEAL, D(2020, 1, 1), D(2021, 1, 1), commissioned=True)
    assert implicit == explicit


def test_service_before_manufacture_is_rejected():
    with pytest.raises(ValueError, match="before it is manufactured"):
        compute_end_of_life(BEAL, D(2020, 1, 2), D(2020, 1, 1))


def test_same_day_manufacture_and_service():
    result = compute_end_of_life(BEAL, D(2020, 1, 1), D(2020, 1, 1))
    assert result.date == D(2030, 1, 1)


# --- Relative to today ----------------------------------------------------------------------


def test_expiry_is_on_the_end_of_life_day():
    result = compute_end_of_life(LifespanRule(max_lifetime_months=12), D(2020, 1, 1), None)
    assert result.date == D(2021, 1, 1)
    assert not result.is_expired(D(2020, 12, 31))
    assert result.is_expired(D(2021, 1, 1))
    assert result.days_remaining(D(2020, 12, 31)) == 1
    assert result.days_remaining(D(2021, 1, 1)) == 0
    assert result.days_remaining(D(2021, 1, 11)) == -10
