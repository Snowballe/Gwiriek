from datetime import timedelta

import pytest

from gwiriek.equipment.services import due, items
from gwiriek.equipment.services.errors import InvalidDataError


@pytest.fixture
def fleet(harness_model, rope_model, connector_model, undefined_model, make_item, months_ago):
    # Bought a month ago: the first inspection is due in 11 months, beyond the horizons
    # below, so these tests isolate the end-of-life deadlines.
    def add(model, label, **fields):
        return make_item(model, label=label, purchased_on=months_ago(1), **fields)

    return {
        "expired": add(harness_model, "EXP", manufactured_on=months_ago(121)),
        "soon": add(harness_model, "SOON", manufactured_on=months_ago(118)),
        "later": add(harness_model, "LATER", manufactured_on=months_ago(60)),
        "no_date": add(harness_model, "NODATE"),
        "unlimited": add(connector_model, "UNLIM", manufactured_on=months_ago(300)),
        "undefined": add(undefined_model, "UNDEF"),
        "rope_stock": add(rope_model, "ROPE", manufactured_on=months_ago(58)),
        "rope_partial": add(
            rope_model, "PARTIAL", manufactured_on=months_ago(12), status="quarantine"
        ),
    }


def labels(entries):
    return [e.item.label for e in entries]


def test_due_report(fleet, today):
    report = due.due_items(within_days=90)
    assert report.as_of == today
    assert labels(report.overdue) == ["EXP"]
    assert set(labels(report.upcoming)) == {"SOON", "ROPE"}
    # A quarantined item without a service date keeps its storage limit: its end of life is
    # known (manufacture + storage), far beyond the horizon, so it is in no list.
    assert set(labels(report.undetermined)) == {"NODATE", "UNDEF"}
    assert (report.overdue[0].days_remaining or 0) < 0
    assert "notice" in report.disclaimer


def test_horizon(fleet):
    assert labels(due.due_items(within_days=0).upcoming) == []
    assert "LATER" in labels(due.due_items(within_days=3650).upcoming)


def test_upcoming_sorted_by_date(fleet):
    upcoming = due.due_items(within_days=3650).upcoming
    dates = [e.deadline for e in upcoming if e.deadline]
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
    assert report.upcoming == []


def test_negative_horizon(db):
    with pytest.raises(InvalidDataError):
        due.due_items(within_days=-1)


# --- Periodic inspections ---------------------------------------------------------------


def reasons(report, label):
    entries = [e for e in report.overdue + report.upcoming if e.item.label == label]
    return entries[0].reasons if entries else []


def test_never_inspected_without_entry_date_is_overdue(harness_model, make_item, today):
    make_item(harness_model, label="NEW", manufactured_on=today)  # stock, no purchase date
    report = due.due_items(within_days=0)
    [entry] = report.overdue
    assert entry.inspection_reference == "none"
    assert entry.inspection_due_on is None
    assert entry.deadline == today
    assert entry.deadline_kind == "inspection"
    assert entry.reasons == ["inspection_overdue"]


def test_never_inspected_counts_from_earliest_entry(harness_model, make_item, months_ago):
    make_item(
        harness_model,
        label="OLD",
        manufactured_on=months_ago(30),
        purchased_on=months_ago(13),
        in_service_on=months_ago(12),
    )
    make_item(
        harness_model, label="RECENT", manufactured_on=months_ago(12), purchased_on=months_ago(11)
    )
    report = due.due_items(within_days=40)
    [old] = report.overdue
    assert old.item.label == "OLD"
    assert old.inspection_reference == "purchase"
    assert old.inspection_due_on == months_ago(1)
    [recent] = report.upcoming
    assert recent.inspection_reference == "purchase"
    assert recent.reasons == ["inspection_near"]


def test_stock_is_included(harness_model, make_item, months_ago):
    make_item(harness_model, label="STOCK", purchased_on=months_ago(14))
    entry = due.due_items(within_days=0).overdue[0]
    assert entry.item.status == "in_stock"
    assert "inspection_overdue" in entry.reasons


def test_inspection_deadline_after_inspection(
    harness_model, make_item, inspector, all_ok, months_ago, today
):
    from gwiriek.equipment.services import inspections

    item = make_item(harness_model, label="H", purchased_on=months_ago(20))
    assert due.due_items(within_days=0).overdue[0].item.label == "H"
    inspections.record_inspection(
        item.id,
        inspector_id=inspector.id,
        decision="valid",
        results=all_ok(harness_model),
        actor="t",
        inspected_on=months_ago(11),
    )
    report = due.due_items(within_days=60)
    assert report.overdue == []
    [entry] = report.upcoming
    assert entry.inspection_reference == "last_inspection"
    assert entry.last_inspected_on == months_ago(11)
    assert entry.inspection_due_on == months_ago(-1)
    assert entry.deadline_kind == "inspection"
    assert due.due_items(within_days=10).upcoming == []


def test_nearest_deadline_is_end_of_life(harness_model, make_item, months_ago):
    make_item(harness_model, label="H", manufactured_on=months_ago(119), purchased_on=months_ago(2))
    [entry] = due.due_items(within_days=60).upcoming
    assert entry.deadline == months_ago(-1)
    assert entry.deadline_kind == "end_of_life"
    assert entry.reasons == ["end_of_life_near"]


def test_inspections_only(harness_model, make_item, months_ago):
    make_item(
        harness_model, label="EXP", manufactured_on=months_ago(121), purchased_on=months_ago(1)
    )
    make_item(harness_model, label="NODATE", purchased_on=months_ago(1))
    report = due.due_items(within_days=30, inspections_only=True)
    assert report.overdue == report.upcoming == report.undetermined == []
    assert labels(due.due_items(within_days=30).overdue) == ["EXP"]
