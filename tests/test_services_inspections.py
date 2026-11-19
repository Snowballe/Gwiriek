from datetime import timedelta

import pytest
from django.core.files.base import ContentFile

from gwiriek.equipment.models import Inspection, Item, ProductModel, StatusChange
from gwiriek.equipment.services import assignments, inspections, inspectors, items
from gwiriek.equipment.services.errors import (
    InvalidDataError,
    NotFoundError,
    OperationNotAllowedError,
)


@pytest.fixture
def harness(harness_model, make_item, months_ago):
    return make_item(
        harness_model, label="H-1", manufactured_on=months_ago(24), purchased_on=months_ago(23)
    )


@pytest.fixture
def record(inspector):
    def _record(item, **fields):
        fields.setdefault("inspector_id", inspector.id)
        fields.setdefault("decision", "valid")
        fields.setdefault("results", {"Sangles": "ok", "Coutures": "ok"})
        fields.setdefault("actor", "test")
        return inspections.record_inspection(item.id, **fields)

    return _record


def status(item):
    return Item.objects.get(pk=item.id).status


# --- Recording and freezing ---------------------------------------------------------------


def test_record_valid_inspection(harness, record, inspector, today, months_ago):
    view = record(harness, comment="RAS")
    assert view.inspected_on == today
    assert view.inspector == "Yann Le Gall"
    assert view.inspector_snapshot["authorization_reference"] == "HAB-1"
    assert [(c.point, c.result) for c in view.checklist] == [
        ("Sangles", "ok"),
        ("Coutures", "ok"),
    ]
    assert view.next_due_on == months_ago(-12)
    assert view.resulting_status == "in_stock"
    assert view.actor == "test"
    assert status(harness) == "in_stock"
    assert not StatusChange.objects.filter(item_id=harness.id, reason__contains="Inspection")


def test_checklist_is_frozen(harness, harness_model, record):
    view = record(harness, results={"1": "ok", "coutures": "na"})
    ProductModel.objects.filter(pk=harness_model.id).update(checklist=["Autre chose"])
    [frozen] = inspections.list_inspections(item_ref=harness.id)
    assert [(c.point, c.result) for c in frozen.checklist] == [
        ("Sangles", "ok"),
        ("Coutures", "na"),
    ]
    assert frozen.id == view.id


def test_inspector_identity_is_frozen(harness, record, person):
    from gwiriek.equipment.models import Person

    record(harness)
    Person.objects.filter(pk=person.id).update(last_name="Changed")
    assert inspections.list_inspections()[0].inspector == "Yann Le Gall"


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ({"Sangles": "ok"}, "Coutures"),
        ({"Sangles": "ok", "Coutures": "maybe"}, "maybe"),
        ({"Sangles": "ok", "Coutures": "ok", "Boucles": "ok"}, "Boucles"),
        ([("Sangles", "ok"), ("1", "ok"), ("Coutures", "ok")], "Sangles"),
        ({"Sangles": "ok", "Coutures": "ok", "3": "ok"}, "3"),
    ],
)
def test_checklist_must_match_the_model(harness, record, results, message):
    with pytest.raises(InvalidDataError, match=message):
        record(harness, results=results)
    assert not Inspection.objects.exists()


def test_model_without_checklist_needs_a_comment(undefined_model, make_item, record, today):
    item = make_item(undefined_model, label="D", purchased_on=today)
    with pytest.raises(InvalidDataError, match="comment"):
        record(item, results={})
    view = record(item, results={"Came": "ok"}, comment="Contrôle visuel et fonctionnel")
    assert [c.point for c in view.checklist] == ["Came"]
    with pytest.raises(InvalidDataError):
        record(item, results=[("Came", "ok"), ("came", "ok")], comment="x")


def test_inspection_is_immutable(harness, record):
    view = record(harness)
    inspection = Inspection.objects.get(pk=view.id)
    inspection.comment = "rewritten"
    with pytest.raises(PermissionError):
        inspection.save()
    with pytest.raises(PermissionError):
        inspection.delete()
    with pytest.raises(PermissionError):
        Inspection.objects.filter(pk=view.id).update(comment="x")
    with pytest.raises(PermissionError):
        Inspection.objects.all().delete()


def test_correction_by_a_new_inspection(harness, record, months_ago, today):
    record(harness, inspected_on=months_ago(1))
    with pytest.raises(InvalidDataError, match=r"latest|dernière"):
        record(harness, inspected_on=months_ago(2))
    record(harness, inspected_on=months_ago(1), decision="watch", comment="Correction")
    history = inspections.list_inspections(item_ref=harness.id)
    assert [i.decision for i in history] == ["watch", "valid"]


# --- Inspector rules ----------------------------------------------------------------------


def test_unauthorized_inspector_cannot_record(db, harness, record):
    nobody = inspectors.create_inspector(kind="external", name="Nobody", families=["harness"])
    with pytest.raises(OperationNotAllowedError, match=r"authorization|habilitation"):
        record(harness, inspector_id=nobody.id, report_archive_location="x")


def test_expired_inspector_cannot_record(db, harness, record, months_ago):
    expired = inspectors.create_inspector(
        kind="external",
        name="Old",
        authorized_on=months_ago(24),
        authorization_reference="R",
        authorization_valid_until=months_ago(1),
        families=["harness"],
    )
    fields = {"report_received_on": months_ago(2), "report_archive_location": "Classeur"}
    with pytest.raises(OperationNotAllowedError) as exc:
        record(harness, inspector_id=expired.id, **fields)
    assert exc.value.details["issues"] == ["expired"]
    # Valid on the inspection day, expired on the recording day: still refused.
    with pytest.raises(OperationNotAllowedError):
        record(harness, inspector_id=expired.id, inspected_on=months_ago(2), **fields)


def test_out_of_scope_inspector_cannot_record(helmet_item, external_inspector, record, today):
    with pytest.raises(OperationNotAllowedError, match=r"scope|périmètre"):
        record(
            helmet_item,
            inspector_id=external_inspector.id,
            results={},
            comment="x",
            report_received_on=today,
            report_archive_location="x",
        )


@pytest.fixture
def helmet_item(petzl, make_item, today):
    from gwiriek.equipment.services import catalog

    model = catalog.create_product_model(
        manufacturer="Petzl", name="VERTEX", family="helmet", max_lifetime_months=120
    )
    return make_item(ProductModel.objects.get(pk=model.id), label="K", purchased_on=today)


def test_inactive_inspector_cannot_record(harness, record, inspector):
    from gwiriek.equipment.models import Inspector

    obj = Inspector.objects.get(pk=inspector.id)
    obj.active = False
    inspectors.save_inspector(obj)
    with pytest.raises(OperationNotAllowedError):
        record(harness)


def test_external_inspector_needs_report_or_archive(harness, record, external_inspector, today):
    with pytest.raises(InvalidDataError, match=r"report|rapport"):
        record(harness, inspector_id=external_inspector.id)
    with pytest.raises(InvalidDataError):
        record(harness, inspector_id=external_inspector.id, report_archive_location="Classeur")
    with pytest.raises(InvalidDataError):
        record(
            harness,
            inspector_id=external_inspector.id,
            report_archive_location="Classeur",
            report_received_on=today + timedelta(days=1),
        )
    view = record(
        harness,
        inspector_id=external_inspector.id,
        report_archive_location="Classeur VGP",
        report_received_on=today,
    )
    assert view.report_archive_location == "Classeur VGP"
    attached = record(
        harness,
        inspector_id=external_inspector.id,
        report=ContentFile(b"%PDF", name="rapport.pdf"),
    )
    assert attached.report == f"inspections/{harness.id}/rapport.pdf"
    assert attached.inspector_kind == "external"


# --- Decision rules -----------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["valid", "watch"])
def test_non_compliant_point_forbids_keeping_in_use(harness, record, decision):
    with pytest.raises(OperationNotAllowedError) as exc:
        record(
            harness, decision=decision, results={"Sangles": "nok", "Coutures": "ok"}, comment="x"
        )
    assert exc.value.details["failed_points"] == ["Sangles"]
    assert exc.value.details["allowed"] == ["quarantine", "retire"]
    assert all(type(d) is str for d in exc.value.details["allowed"])


def test_non_compliant_point_allows_quarantine(harness, record):
    view = record(
        harness,
        decision="quarantine",
        results={"Sangles": "nok", "Coutures": "ok"},
        comment="Coupure",
    )
    assert view.resulting_status == "quarantine"
    change = StatusChange.objects.filter(item_id=harness.id).latest("id")
    assert (change.to_status, change.actor) == ("quarantine", "test")
    assert "Coupure" in change.reason


@pytest.mark.parametrize("decision", ["watch", "quarantine", "retire"])
def test_decision_other_than_valid_needs_comment(harness, record, decision):
    with pytest.raises(InvalidDataError, match="comment"):
        record(harness, decision=decision)


def test_unknown_decision(harness, record):
    with pytest.raises(InvalidDataError):
        record(harness, decision="maybe")


def test_expired_item_can_only_be_withdrawn(harness_model, make_item, record, months_ago):
    item = make_item(
        harness_model, label="OLD", manufactured_on=months_ago(121), purchased_on=months_ago(120)
    )
    with pytest.raises(OperationNotAllowedError, match=r"end of life|fin de vie"):
        record(item)
    view = record(item, decision="retire", comment="Fin de vie")
    assert view.next_due_on is None
    assert status(item) == "retired"


def test_retired_item_is_not_inspected(harness, record):
    items.change_status(harness.id, "retired", actor="t", reason="Perdu")
    with pytest.raises(OperationNotAllowedError, match=r"retired|rebuté"):
        record(harness)


def test_retire_closes_assignment(harness, record, person):
    assignments.assign_item(harness.id, "person", actor="t", person_id=person.id)
    record(harness, decision="retire", comment="Usure")
    obj = Item.objects.get(pk=harness.id)
    assert obj.status == "retired"
    assert obj.retired_on is not None
    assert not obj.assignments.filter(ended_on=None).exists()


def test_retire_has_no_next_due(harness, record, today):
    with pytest.raises(InvalidDataError):
        record(harness, decision="retire", comment="x", next_due_on=today + timedelta(days=5))


# --- Status updates -----------------------------------------------------------------------


def test_watch_decision(harness, record, person, months_ago, today):
    view = record(harness, decision="watch", comment="Usure légère")
    assert view.resulting_status == "watch"
    obj = Item.objects.get(pk=harness.id)
    assert obj.status == "watch"
    assert obj.in_service_on == today  # entering an in-use status, like change_status


def test_valid_lifts_watch(harness, record, person):
    assignments.assign_item(harness.id, "person", actor="t", person_id=person.id)
    record(harness, decision="watch", comment="A surveiller")
    record(harness)
    assert status(harness) == "in_service"


def test_valid_after_quarantine_returns_to_service_if_issued(harness, record, person):
    assignments.assign_item(harness.id, "person", actor="t", person_id=person.id)
    record(harness, decision="quarantine", comment="Doute", results={"1": "nok", "2": "ok"})
    record(harness)
    assert status(harness) == "in_service"


def test_valid_after_quarantine_returns_to_stock_otherwise(harness, record):
    record(harness, decision="quarantine", comment="Doute")
    view = record(harness)
    assert view.resulting_status == "in_stock"
    assert status(harness) == "in_stock"
    reasons = list(StatusChange.objects.filter(item_id=harness.id).values_list("reason", flat=True))
    assert any("Inspection" in r or "Vérification" in r for r in reasons)


def test_valid_keeps_service(harness, record, person):
    assignments.assign_item(harness.id, "person", actor="t", person_id=person.id)
    count = StatusChange.objects.count()
    record(harness)
    assert status(harness) == "in_service"
    assert StatusChange.objects.count() == count


# --- Next due date ------------------------------------------------------------------------


def test_next_due_follows_model_interval(harness, harness_model, record, today, months_ago):
    ProductModel.objects.filter(pk=harness_model.id).update(inspection_interval_months=6)
    assert record(harness).next_due_on == months_ago(-6)


def test_requested_next_due(harness, record, today, months_ago):
    assert record(harness, next_due_on=months_ago(-3)).next_due_on == months_ago(-3)
    with pytest.raises(InvalidDataError, match=r"no later|au plus tard"):
        record(harness, next_due_on=months_ago(-12) + timedelta(days=1))
    with pytest.raises(InvalidDataError):
        record(harness, next_due_on=today)


def test_next_due_capped_by_end_of_life(harness_model, make_item, record, months_ago):
    item = make_item(
        harness_model, label="SOON", manufactured_on=months_ago(116), purchased_on=months_ago(115)
    )
    assert record(item).next_due_on == months_ago(-4)


def test_late_recording(harness, record, months_ago):
    view = record(harness, inspected_on=months_ago(13))
    assert view.next_due_on == months_ago(1)


# --- Dates and lookups --------------------------------------------------------------------


def test_inspection_dates(harness, record, today, months_ago):
    with pytest.raises(InvalidDataError, match=r"future|futur"):
        record(harness, inspected_on=today + timedelta(days=1))
    with pytest.raises(InvalidDataError, match=r"manufacture|fabrication"):
        record(harness, inspected_on=months_ago(25))


def test_actor_required(harness, record):
    with pytest.raises(InvalidDataError):
        record(harness, actor=" ")


def test_lookups(harness, record, inspector, months_ago):
    first = record(harness, inspected_on=months_ago(6))
    second = record(harness)
    assert inspections.get_inspection(first.id).inspected_on == months_ago(6)
    assert [i.id for i in inspections.list_inspections()] == [second.id, first.id]
    assert [i.id for i in inspections.list_inspections(since=months_ago(1))] == [second.id]
    assert inspections.list_inspections(inspector_id=inspector.id + 1) == []
    assert len(inspections.list_inspections(limit=1)) == 1
    with pytest.raises(NotFoundError):
        inspections.get_inspection(999)
    with pytest.raises(NotFoundError):
        record(harness, inspector_id=999)
    detail = items.get_item(harness.id)
    assert [i.id for i in detail.inspections] == [second.id, first.id]
    assert detail.next_inspection.reference == "last_inspection"
    assert detail.next_inspection.due_on == second.next_due_on
    summary = items.list_items()[0]
    assert summary.next_inspection_due_on == second.next_due_on
    assert summary.inspection_overdue is False
