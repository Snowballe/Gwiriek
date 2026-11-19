from datetime import timedelta

import pytest

from gwiriek.equipment.models import Assignment, Item, StatusChange
from gwiriek.equipment.services import assignments, items
from gwiriek.equipment.services.errors import (
    AmbiguousReferenceError,
    InvalidDataError,
    NotFoundError,
    OperationNotAllowedError,
)

# --- Registration and validation ------------------------------------------------------------


def test_create_item_records_registration(harness_model, make_item, months_ago):
    item = make_item(harness_model, label="H-1", manufactured_on=months_ago(12))
    assert item.status == "in_stock"
    assert item.end_of_life.end_date == months_ago(12 - 120)
    assert item.end_of_life.basis == "complete"
    assert [(c.from_status, c.to_status, c.actor) for c in item.status_history] == [
        ("", "in_stock", "test")
    ]


def test_serial_or_lot_required(harness_model):
    with pytest.raises(InvalidDataError) as exc:
        items.create_item(product_model_id=harness_model.id, actor="t")
    assert "serial_number" in exc.value.details["fields"]


def test_lot_only_is_enough(connector_model):
    item = items.create_item(product_model_id=connector_model.id, actor="t", lot_number="22K")
    assert item.lot_number == "22K"


def test_unknown_product_model(db):
    with pytest.raises(NotFoundError):
        items.create_item(product_model_id=1, actor="t", serial_number="x")


@pytest.mark.parametrize("field", ["manufactured_on", "purchased_on", "in_service_on"])
def test_future_dates_are_invalid(harness_model, make_item, today, field):
    with pytest.raises(InvalidDataError) as exc:
        make_item(harness_model, **{field: today + timedelta(days=1)})
    assert field in exc.value.details["fields"]


@pytest.mark.parametrize("field", ["purchased_on", "in_service_on"])
def test_dates_before_manufacture_are_invalid(harness_model, make_item, months_ago, field):
    with pytest.raises(InvalidDataError) as exc:
        make_item(harness_model, manufactured_on=months_ago(10), **{field: months_ago(11)})
    assert field in exc.value.details["fields"]


def test_rope_fields_only_for_ropes(harness_model, make_item):
    with pytest.raises(InvalidDataError) as exc:
        make_item(harness_model, rope_length_m=60)
    assert "rope_length_m" in exc.value.details["fields"]


def test_rope_fields(rope_model, make_item, months_ago):
    item = make_item(
        rope_model,
        manufactured_on=months_ago(1),
        rope_diameter_mm=12.5,
        rope_length_m=60,
        rope_type="A",
        rope_terminations="1 œil cousu",
    )
    assert item.rope_diameter_mm == 12.5
    assert item.rope_type == "A"


def test_invalid_rope_type(rope_model, make_item):
    with pytest.raises(InvalidDataError):
        make_item(rope_model, rope_type="C")


def test_in_service_requires_date(harness_model, make_item):
    with pytest.raises(InvalidDataError) as exc:
        make_item(harness_model, status="in_service")
    assert "in_service_on" in exc.value.details["fields"]


def test_cannot_register_retired(harness_model, make_item):
    with pytest.raises(OperationNotAllowedError):
        make_item(harness_model, status="retired")


def test_unknown_status(harness_model, make_item):
    with pytest.raises(InvalidDataError):
        make_item(harness_model, status="lost")


def test_label_is_unique(harness_model, make_item):
    make_item(harness_model, label="H-1")
    with pytest.raises(InvalidDataError):
        make_item(harness_model, label="H-1")


def test_serial_is_unique_per_model(harness_model, connector_model, make_item):
    make_item(harness_model, serial_number="ABC")
    make_item(connector_model, serial_number="ABC")
    with pytest.raises(InvalidDataError):
        make_item(harness_model, serial_number="ABC")


def test_cannot_register_expired_item_in_service(harness_model, make_item, months_ago):
    with pytest.raises(OperationNotAllowedError):
        make_item(
            harness_model,
            manufactured_on=months_ago(121),
            in_service_on=months_ago(100),
            status="in_service",
        )


def test_actor_is_required(harness_model):
    with pytest.raises(InvalidDataError):
        items.create_item(product_model_id=harness_model.id, actor=" ", serial_number="x")


# --- Lookup ---------------------------------------------------------------------------------


def test_resolve_by_id_label_and_serial(harness_model, make_item):
    item = make_item(harness_model, serial_number="21A123", label="H-7")
    assert items.get_item(item.id).id == item.id
    assert items.get_item(str(item.id)).id == item.id
    assert items.get_item("h-7").id == item.id
    assert items.get_item("21a123").id == item.id
    assert items.get_item("serial:21A123").id == item.id
    assert items.get_item("label:H-7").id == item.id
    assert items.get_item(f"id:{item.id}").id == item.id


def test_resolve_ambiguous(harness_model, make_item):
    first = make_item(harness_model, serial_number="X1")
    second = make_item(harness_model, serial_number=str(first.id + 1000), label=str(first.id))
    with pytest.raises(AmbiguousReferenceError) as exc:
        items.get_item(str(first.id))
    ids = {c["id"] for c in exc.value.details["candidates"]}
    assert ids == {first.id, second.id}
    assert items.get_item(f"id:{first.id}").id == first.id
    assert items.get_item(f"label:{first.id}").id == second.id


def test_resolve_not_found(db):
    with pytest.raises(NotFoundError):
        items.get_item("nothing")
    with pytest.raises(NotFoundError):
        items.get_item(42)
    with pytest.raises(InvalidDataError):
        items.get_item("id:abc")


def test_list_items_filters(harness_model, rope_model, make_item, person, months_ago):
    h = make_item(harness_model, label="H-1", manufactured_on=months_ago(2))
    make_item(rope_model, label="R-1")
    assignments.assign_item(h.id, "person", actor="t", person_id=person.id)
    assert {i.label for i in items.list_items()} == {"H-1", "R-1"}
    assert [i.label for i in items.list_items(statuses=["in_service"])] == ["H-1"]
    assert [i.label for i in items.list_items(family="rope")] == ["R-1"]
    assert [i.label for i in items.list_items(query="ergo")] == ["R-1"]
    assert [i.label for i in items.list_items(person_id=person.id)] == ["H-1"]
    assert len(items.list_items(limit=1)) == 1
    assert items.list_items(statuses=["in_service"])[0].assigned_to == "Personne Yann Le Gall"
    with pytest.raises(InvalidDataError):
        items.list_items(statuses=["lost"])
    with pytest.raises(InvalidDataError):
        items.list_items(family="boat")


# --- Status changes and audit log -----------------------------------------------------------


def test_change_status_records_audit(harness_model, make_item, months_ago, today):
    item = make_item(harness_model, manufactured_on=months_ago(5))
    detail = items.change_status(item.id, "in_service", actor="cli:me")
    assert detail.status == "in_service"
    assert detail.in_service_on == today  # first put into service
    detail = items.change_status(item.id, "quarantine", actor="cli:me", reason="Chute")
    assert [(c.from_status, c.to_status, c.reason) for c in detail.status_history][1:] == [
        ("in_stock", "in_service", ""),
        ("in_service", "quarantine", "Chute"),
    ]


@pytest.mark.parametrize("status", ["quarantine", "retired"])
def test_reason_required(harness_model, make_item, status):
    item = make_item(harness_model)
    with pytest.raises(InvalidDataError):
        items.change_status(item.id, status, actor="t", reason="  ")


def test_same_status_is_refused(harness_model, make_item):
    item = make_item(harness_model)
    with pytest.raises(OperationNotAllowedError):
        items.change_status(item.id, "in_stock", actor="t")


def test_unknown_target_status(harness_model, make_item):
    item = make_item(harness_model)
    with pytest.raises(InvalidDataError):
        items.change_status(item.id, "lost", actor="t")


def test_retire_is_terminal_and_closes_assignment(harness_model, make_item, person, today):
    item = make_item(harness_model)
    assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    detail = items.change_status(item.id, "retired", actor="t", reason="Usure")
    assert detail.retired_on == today
    assert detail.current_assignment is None
    assert detail.assignments[0].ended_on == today
    with pytest.raises(OperationNotAllowedError):
        items.change_status(item.id, "in_stock", actor="t")


def test_expired_item_cannot_be_put_into_service(harness_model, make_item, months_ago):
    item = make_item(harness_model, manufactured_on=months_ago(121))
    assert item.end_of_life.expired
    with pytest.raises(OperationNotAllowedError):
        items.change_status(item.id, "in_service", actor="t")
    with pytest.raises(OperationNotAllowedError):
        items.change_status(item.id, "watch", actor="t")
    # Taking it out of use stays possible.
    items.change_status(item.id, "retired", actor="t", reason="Fin de vie")


def test_save_item_is_atomic(harness_model, make_item):
    item = make_item(harness_model)
    obj = Item.objects.get(pk=item.id)
    obj.status = "quarantine"
    with pytest.raises(InvalidDataError):
        items.save_item(obj, actor="t")  # no reason
    assert Item.objects.get(pk=item.id).status == "in_stock"
    assert StatusChange.objects.filter(item_id=item.id).count() == 1


def test_audit_log_is_append_only(harness_model, make_item):
    item = make_item(harness_model)
    change = StatusChange.objects.get(item_id=item.id)
    change.reason = "tampered"
    with pytest.raises(PermissionError):
        change.save()
    with pytest.raises(PermissionError):
        change.delete()
    with pytest.raises(PermissionError):
        StatusChange.objects.filter(item_id=item.id).update(reason="x")
    with pytest.raises(PermissionError):
        StatusChange.objects.filter(item_id=item.id).delete()
    assert StatusChange.objects.get(item_id=item.id).reason != "tampered"


def test_item_detail_end_of_life_disclaimer(harness_model, make_item):
    item = make_item(harness_model)
    assert item.end_of_life.basis == "unknown"
    assert item.end_of_life.missing == ["manufactured_on"]
    assert "notice" in item.end_of_life.disclaimer


def test_assignment_constraint_one_open(harness_model, make_item, person):
    item = make_item(harness_model)
    assignments.assign_item(item.id, "person", actor="t", person_id=person.id)
    assignments.assign_item(item.id, "stock", actor="t")
    assert Assignment.objects.filter(item_id=item.id, ended_on__isnull=True).count() == 1


def test_quarantine_without_service_date_keeps_storage_limit(rope_model, make_item, months_ago):
    """A stock item sent to quarantine has not been put into use: storage still binds."""
    from gwiriek.equipment.models import Item
    from gwiriek.equipment.services.views import item_end_of_life

    created = make_item(rope_model, manufactured_on=months_ago(50))
    item = Item.objects.get(pk=created.id)
    before = item_end_of_life(item).date
    items.change_status(item.id, "quarantine", actor="test", reason="damaged sheath")
    item.refresh_from_db()

    assert item.in_service_on is None
    assert before is not None
    assert item_end_of_life(item).date == before
