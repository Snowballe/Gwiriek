import csv
import io

import pytest
from django.core.files.base import ContentFile

from gwiriek.equipment.models import Inspector
from gwiriek.equipment.services import inspectors, people
from gwiriek.equipment.services.errors import (
    InvalidDataError,
    NotFoundError,
    OperationNotAllowedError,
)


def test_internal_inspector(inspector, person, today):
    assert inspector.kind == "internal"
    assert inspector.name == "Yann Le Gall"
    assert inspector.person_id == person.id
    assert inspector.qualified is True
    assert inspector.issues == []
    assert inspector.as_of == today


def test_external_inspector(external_inspector):
    assert external_inspector.name == "Erwan Morvan (Bureau)"
    assert external_inspector.families == ["harness", "rope"]


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"kind": "internal"}, "person"),
        ({"kind": "external"}, "name"),
        ({"kind": "external", "name": "X", "authorized_on": "today"}, "authorization_reference"),
        (
            {
                "kind": "external",
                "name": "X",
                "authorization_valid_until": "today",
            },
            "authorization_valid_until",
        ),
        ({"kind": "external", "name": "X", "authorized_on": "tomorrow"}, "authorized_on"),
    ],
)
def test_validation(db, today, fields, field):
    from datetime import timedelta

    days = {"today": today, "tomorrow": today + timedelta(days=1)}
    fields = {k: days.get(v, v) if isinstance(v, str) else v for k, v in fields.items()}
    if fields.get("authorized_on") == days["tomorrow"]:
        fields["authorization_reference"] = "R"
    with pytest.raises(InvalidDataError) as exc:
        inspectors.create_inspector(**fields)
    assert field in exc.value.details["fields"]


def test_validity_cannot_end_before_authorization(db, months_ago):
    with pytest.raises(InvalidDataError) as exc:
        inspectors.create_inspector(
            kind="external",
            name="X",
            authorized_on=months_ago(2),
            authorization_reference="R",
            authorization_valid_until=months_ago(3),
        )
    assert "authorization_valid_until" in exc.value.details["fields"]


def test_unknown_kind_and_family(db):
    with pytest.raises(InvalidDataError):
        inspectors.create_inspector(kind="friend", name="X")
    with pytest.raises(InvalidDataError):
        inspectors.create_inspector(kind="external", name="X", families=["boat"])


def test_document_replaces_reference(db, today):
    view = inspectors.create_inspector(
        kind="external",
        name="X",
        authorized_on=today,
        authorization_document=ContentFile(b"%PDF", name="habilitation.pdf"),
        families=["rope"],
    )
    assert view.authorization_document.startswith("inspectors/habilitation")
    assert view.qualified is True


def test_person_is_inspector_once(inspector, person):
    with pytest.raises(InvalidDataError):
        inspectors.create_inspector(kind="internal", person_id=person.id)


def test_unknown_person_and_inspector(db):
    with pytest.raises(NotFoundError):
        inspectors.create_inspector(kind="internal", person_id=999)
    with pytest.raises(NotFoundError):
        inspectors.get_inspector(999)


def test_issues(db, months_ago, today):
    expired = inspectors.create_inspector(
        kind="external",
        name="Old",
        authorized_on=months_ago(24),
        authorization_reference="R",
        authorization_valid_until=months_ago(1),
        families=["rope"],
    )
    assert expired.issues == ["expired"]
    unauthorized = inspectors.create_inspector(kind="external", name="None")
    assert unauthorized.issues == ["not_authorized", "no_scope"]
    assert inspectors.get_inspector(expired.id, on=months_ago(2)).qualified is True


def check(view, family, on):
    inspectors.check_can_inspect(
        Inspector.objects.select_related("person").get(pk=view.id), family, on
    )


def test_check_can_inspect(external_inspector, months_ago, today):
    check(external_inspector, "rope", today)
    with pytest.raises(OperationNotAllowedError, match=r"scope|périmètre"):
        check(external_inspector, "helmet", today)
    with pytest.raises(OperationNotAllowedError) as exc:
        check(external_inspector, "rope", months_ago(25))
    assert exc.value.details["issues"] == ["not_yet_valid"]


def test_inactive_inspector_or_person(inspector, person, today):
    obj = Inspector.objects.get(pk=inspector.id)
    person_obj = people.get_person(person.id)
    person_obj.active = False
    person_obj.save()
    with pytest.raises(OperationNotAllowedError) as exc:
        check(inspector, "rope", today)
    assert exc.value.details["issues"] == ["person_inactive"]
    obj.active = False
    inspectors.save_inspector(obj)
    assert inspectors.list_inspectors() == []
    assert [v.issues for v in inspectors.list_inspectors(active_only=False)] == [
        ["inactive", "person_inactive"]
    ]


def test_export_csv(inspector, external_inspector):
    rows = list(csv.reader(io.StringIO(inspectors.export_inspectors_csv()), delimiter=";"))
    assert rows[0][0] == "ID"
    assert rows[0][1] == "Nom"  # French interface
    assert [r[1] for r in rows[1:]] == ["Yann Le Gall", "Erwan Morvan (Bureau)"]
    assert rows[2][7] == "Harnais, Corde"
    only = inspectors.export_inspectors_csv(inspector_ids=[external_inspector.id])
    assert len(only.splitlines()) == 2
