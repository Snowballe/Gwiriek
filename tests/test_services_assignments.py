import pytest

from gwiriek.equipment.services import assignments, items, people
from gwiriek.equipment.services.errors import (
    InvalidDataError,
    NotFoundError,
    OperationNotAllowedError,
)


def test_issue_from_stock_puts_into_service(harness_model, make_item, person, months_ago):
    item = make_item(harness_model, manufactured_on=months_ago(10))
    view = assignments.assign_item(
        item.id, "person", actor="t", person_id=person.id, started_on=months_ago(3)
    )
    assert view.person == "Yann Le Gall"
    detail = items.get_item(item.id)
    assert detail.status == "in_service"
    assert detail.in_service_on == months_ago(3)
    assert detail.status_history[-1].reason == "Remis en dotation"


def test_issue_keeps_existing_service_date(harness_model, make_item, months_ago):
    item = make_item(harness_model, manufactured_on=months_ago(10), in_service_on=months_ago(8))
    assignments.assign_item(item.id, "site", actor="t", location="Pont", started_on=months_ago(2))
    assert items.get_item(item.id).in_service_on == months_ago(8)


def test_return_to_stock(harness_model, make_item, person, months_ago, today):
    item = make_item(harness_model)
    assignments.assign_item(
        item.id, "person", actor="t", person_id=person.id, started_on=months_ago(1)
    )
    assignments.assign_item(item.id, "stock", actor="t", location="Dépôt")
    detail = items.get_item(item.id)
    assert detail.status == "in_stock"
    assert detail.current_assignment is not None
    assert detail.current_assignment.kind == "stock"
    assert detail.current_assignment.location == "Dépôt"
    previous = next(a for a in detail.assignments if a.kind == "person")
    assert previous.ended_on == today


def test_watch_status_is_kept(harness_model, make_item, person, months_ago):
    item = make_item(harness_model, in_service_on=months_ago(2), status="watch")
    assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    assignments.assign_item(item.id, "stock", actor="t")
    assert items.get_item(item.id).status == "watch"


def test_new_assignment_cannot_start_before_current(harness_model, make_item, person, months_ago):
    item = make_item(harness_model)
    assignments.assign_item(
        item.id, "person", actor="t", person_id=person.id, started_on=months_ago(1)
    )
    with pytest.raises(InvalidDataError):
        assignments.assign_item(item.id, "stock", actor="t", started_on=months_ago(2))


def test_future_start_is_invalid(harness_model, make_item, months_ago):
    item = make_item(harness_model)
    with pytest.raises(InvalidDataError):
        assignments.assign_item(item.id, "stock", actor="t", started_on=months_ago(-1))


def test_quarantined_item_cannot_be_issued_but_can_be_stored(harness_model, make_item, person):
    item = make_item(harness_model, status="quarantine")
    with pytest.raises(OperationNotAllowedError):
        assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    with pytest.raises(OperationNotAllowedError):
        assignments.assign_item(item.id, "site", actor="t", location="Pont")
    assignments.assign_item(item.id, "stock", actor="t", location="Bac quarantaine")
    assert items.get_item(item.id).status == "quarantine"


def test_retired_item_cannot_be_assigned(harness_model, make_item):
    item = make_item(harness_model)
    items.change_status(item.id, "retired", actor="t", reason="Usure")
    with pytest.raises(OperationNotAllowedError):
        assignments.assign_item(item.id, "stock", actor="t")


def test_expired_item_cannot_be_issued(harness_model, make_item, person, months_ago):
    item = make_item(harness_model, manufactured_on=months_ago(121))
    with pytest.raises(OperationNotAllowedError):
        assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    # Back-dated issue, before the end of life, is allowed (history entry).
    assignments.assign_item(
        item.id, "person", actor="t", person_id=person.id, started_on=months_ago(3 + 118)
    )


def test_inactive_person(harness_model, make_item, person):
    from gwiriek.equipment.models import Person

    Person.objects.filter(pk=person.id).update(active=False)
    item = make_item(harness_model)
    with pytest.raises(OperationNotAllowedError):
        assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    assert people.list_people() == []
    assert len(people.list_people(active_only=False)) == 1


@pytest.mark.parametrize(
    ("kind", "kwargs", "error"),
    [
        ("person", {}, InvalidDataError),
        ("person", {"person_id": 999}, NotFoundError),
        ("site", {}, InvalidDataError),
        ("site", {"location": "  "}, InvalidDataError),
        ("stock", {"person_id": 1}, InvalidDataError),
        ("van", {}, InvalidDataError),
    ],
)
def test_target_must_match_kind(harness_model, make_item, kind, kwargs, error):
    item = make_item(harness_model)
    with pytest.raises(error):
        assignments.assign_item(item.id, kind, actor="t", **kwargs)
