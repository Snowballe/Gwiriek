from datetime import timedelta

import pytest

from gwiriek.equipment.services import due, items
from gwiriek.equipment.services.errors import InvalidDataError


@pytest.fixture
def fleet(harness_model, rope_model, connector_model, undefined_model, make_item, months_ago):
    return {
        "expired": make_item(harness_model, label="EXP", manufactured_on=months_ago(121)),
        "soon": make_item(harness_model, label="SOON", manufactured_on=months_ago(118)),
        "later": make_item(harness_model, label="LATER", manufactured_on=months_ago(60)),
        "no_date": make_item(harness_model, label="NODATE"),
        "unlimited": make_item(connector_model, label="UNLIM", manufactured_on=months_ago(300)),
        "undefined": make_item(undefined_model, label="UNDEF"),
        "rope_stock": make_item(rope_model, label="ROPE", manufactured_on=months_ago(58)),
        "rope_partial": make_item(
            rope_model, label="PARTIAL", manufactured_on=months_ago(12), status="quarantine"
        ),
    }


def labels(entries):
    return [e.item.label for e in entries]


def test_due_report(fleet, today):
    report = due.due_items(within_days=90)
    assert report.as_of == today
    assert labels(report.overdue) == ["EXP"]
    assert set(labels(report.upcoming)) == {"SOON", "ROPE"}
    assert set(labels(report.undetermined)) == {"NODATE", "UNDEF", "PARTIAL"}
    assert (report.overdue[0].days_remaining or 0) < 0
    assert "notice" in report.disclaimer


def test_horizon(fleet):
    assert labels(due.due_items(within_days=0).upcoming) == []
    assert "LATER" in labels(due.due_items(within_days=3650).upcoming)


def test_upcoming_sorted_by_date(fleet):
    upcoming = due.due_items(within_days=3650).upcoming
    dates = [e.end_of_life for e in upcoming if e.end_of_life]
    assert len(dates) == len(upcoming)
    assert dates == sorted(dates)


def test_retired_items_are_excluded(fleet):
    items.change_status(fleet["expired"].id, "retired", actor="t", reason="Fin de vie")
    assert labels(due.due_items().overdue) == []


def test_end_of_life_today_is_overdue(harness_model, make_item, months_ago, today):
    make_item(harness_model, label="TODAY", manufactured_on=months_ago(120))
    report = due.due_items(within_days=30)
    assert labels(report.overdue) == ["TODAY"]
    assert report.overdue[0].days_remaining == 0


def test_explicit_reference_day(fleet, today):
    report = due.due_items(within_days=10, today=today - timedelta(days=3650 * 2))
    assert report.overdue == []


def test_negative_horizon(db):
    with pytest.raises(InvalidDataError):
        due.due_items(within_days=-1)
