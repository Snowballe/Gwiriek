"""Admin smoke tests: pages render, writes go through the services."""

import pytest
from django.urls import reverse

from gwiriek.equipment.models import Inspection, Inspector, Item, StatusChange


def form_data(response):
    """Rebuild the POST data of an admin change form from its context."""
    data = {}
    form = response.context["adminform"].form
    for name, _field in form.fields.items():
        value = form[name].value()
        if value is None:
            continue
        if isinstance(value, list | tuple):
            data[name] = list(value)
        else:
            data[name] = value
    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        for name, _field in formset.management_form.fields.items():
            data[f"{formset.prefix}-{name}"] = formset.management_form[name].value()
        for subform in formset.forms:
            for name in subform.fields:
                value = subform[name].value()
                if value is not None:
                    data[subform.add_prefix(name)] = value
    return {k: v for k, v in data.items() if v is not None}


@pytest.fixture
def item(harness_model, make_item, months_ago):
    return make_item(harness_model, label="H-1", manufactured_on=months_ago(121))


def test_changelists_render(admin_client, item, rope_model):
    for model in (
        "item",
        "productmodel",
        "manufacturer",
        "person",
        "statuschange",
        "assignment",
        "standard",
    ):
        response = admin_client.get(reverse(f"admin:equipment_{model}_changelist"))
        assert response.status_code == 200, model
    response = admin_client.get(reverse("admin:equipment_item_changelist"))
    content = response.content.decode()
    assert "H-1" in content
    assert "Fin de vie" in content or "fin de vie" in content


@pytest.mark.parametrize(
    ("value", "expected"), [("expired", True), ("90", True), ("undetermined", False)]
)
def test_end_of_life_filter(admin_client, item, value, expected):
    url = reverse("admin:equipment_item_changelist") + f"?eol={value}"
    response = admin_client.get(url)
    assert response.status_code == 200
    assert ("H-1" in response.content.decode()) is expected


def test_search_by_serial(admin_client, item):
    response = admin_client.get(reverse("admin:equipment_item_changelist") + "?q=SN-0001")
    assert "H-1" in response.content.decode()


def test_change_form_shows_end_of_life(admin_client, item):
    response = admin_client.get(reverse("admin:equipment_item_change", args=[item.id]))
    content = response.content.decode()
    assert response.status_code == 200
    assert "À titre indicatif" in content


def test_status_change_requires_reason(admin_client, item):
    url = reverse("admin:equipment_item_change", args=[item.id])
    data = form_data(admin_client.get(url))
    data["status"] = "quarantine"
    response = admin_client.post(url, data)
    assert response.status_code == 200  # form redisplayed with errors
    assert Item.objects.get(pk=item.id).status == "in_stock"

    data["status_reason"] = "Chute"
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    change = StatusChange.objects.filter(item_id=item.id).latest("id")
    assert (change.to_status, change.actor, change.reason) == ("quarantine", "admin:admin", "Chute")


def test_assign_from_admin(admin_client, harness_model, make_item, person, months_ago):
    fresh = make_item(harness_model, label="H-2", manufactured_on=months_ago(2))
    url = reverse("admin:equipment_item_change", args=[fresh.id])
    data = form_data(admin_client.get(url))
    data.update({"assign_kind": "person", "assign_person": person.id})
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    obj = Item.objects.get(pk=fresh.id)
    assert obj.status == "in_service"
    assert obj.assignments.get(ended_on=None).person_id == person.id


def test_expired_item_cannot_be_issued_from_admin(admin_client, item, person):
    url = reverse("admin:equipment_item_change", args=[item.id])
    data = form_data(admin_client.get(url))
    data.update({"assign_kind": "person", "assign_person": person.id})
    response = admin_client.post(url, data)
    assert response.status_code == 200
    assert not Item.objects.get(pk=item.id).assignments.exists()


def test_add_item_from_admin(admin_client, harness_model, months_ago):
    url = reverse("admin:equipment_item_add")
    data = form_data(admin_client.get(url))
    data.update(
        {
            "product_model": harness_model.id,
            "serial_number": "NEW-1",
            "manufactured_on": months_ago(1).isoformat(),
            "status": "in_stock",
        }
    )
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    created = Item.objects.get(serial_number="NEW-1")
    assert created.status_changes.get().actor == "admin:admin"


def test_no_deletion_and_read_only_history(admin_client, item):
    assert (
        admin_client.get(reverse("admin:equipment_item_delete", args=[item.id])).status_code == 403
    )
    assert admin_client.get(reverse("admin:equipment_statuschange_add")).status_code == 403
    change = StatusChange.objects.filter(item_id=item.id).earliest("id")
    response = admin_client.post(
        reverse("admin:equipment_statuschange_change", args=[change.pk]), {"reason": "x"}
    )
    assert response.status_code == 403


def test_product_model_checklist_lines(admin_client, petzl):
    url = reverse("admin:equipment_productmodel_add")
    data = form_data(admin_client.get(url))
    data.update(
        {
            "manufacturer": petzl.id,
            "name": "NEWTON",
            "family": "harness",
            "max_lifetime_months": 120,
            "inspection_interval_months": 12,
            "checklist": "Sangles\n\n  Coutures  \n",
        }
    )
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    from gwiriek.equipment.models import ProductModel

    assert ProductModel.objects.get(name="NEWTON").checklist == ["Sangles", "Coutures"]


# --- Inspectors and inspections ----------------------------------------------------------


def test_inspection_changelists_render(admin_client, inspector, item):
    for model in ("inspector", "inspection", "digestlog"):
        response = admin_client.get(reverse(f"admin:equipment_{model}_changelist"))
        assert response.status_code == 200, model


def test_item_page_links_to_inspection_entry(admin_client, item):
    response = admin_client.get(reverse("admin:equipment_item_change", args=[item.id]))
    content = response.content.decode()
    assert "Enregistrer une vérification" in content
    assert reverse("admin:equipment_inspection_add") + f"?item={item.id}" in content
    assert "Due maintenant" in content  # never inspected, no date of entry


@pytest.fixture
def fresh(harness_model, make_item, months_ago):
    return make_item(harness_model, label="H-3", manufactured_on=months_ago(6))


def inspection_data(item, inspector, **fields):
    data = {
        "item": item.id,
        "inspector": inspector.id,
        "inspected_on": fields.pop("inspected_on", ""),
        "decision": "valid",
        "comment": "",
        "next_due_on": "",
        "report_received_on": "",
        "report_archive_location": "",
        "point_1": "ok",
        "point_2": "ok",
    }
    data.update(fields)
    return data


def test_inspection_form_has_checklist(admin_client, fresh):
    url = reverse("admin:equipment_inspection_add") + f"?item={fresh.id}"
    response = admin_client.get(url)
    content = response.content.decode()
    assert response.status_code == 200
    assert "Sangles" in content
    assert "Coutures" in content
    assert 'name="point_1"' in content
    assert "Non conforme" in content
    response = admin_client.get(reverse("admin:equipment_inspection_add"))
    assert 'name="point_1"' not in response.content.decode()


def test_record_inspection_from_admin(admin_client, fresh, inspector, today):
    url = reverse("admin:equipment_inspection_add") + f"?item={fresh.id}"
    data = inspection_data(fresh, inspector, inspected_on=today.isoformat(), point_2="na")
    response = admin_client.post(url, data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    assert response["Location"] == reverse("admin:equipment_item_change", args=[fresh.id])
    inspection = Inspection.objects.get()
    assert inspection.actor == "admin:admin"
    assert inspection.checklist_results == [
        {"point": "Sangles", "result": "ok"},
        {"point": "Coutures", "result": "na"},
    ]

    view_url = reverse("admin:equipment_inspection_change", args=[inspection.pk])
    response = admin_client.get(view_url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "[Sans objet] Coutures" in content
    assert "HAB-1" in content
    assert admin_client.post(view_url, {"comment": "x"}).status_code == 403
    delete_url = reverse("admin:equipment_inspection_delete", args=[inspection.pk])
    assert admin_client.get(delete_url).status_code == 403


def test_inspection_rules_from_admin(admin_client, fresh, inspector, today):
    url = reverse("admin:equipment_inspection_add") + f"?item={fresh.id}"
    data = inspection_data(fresh, inspector, inspected_on=today.isoformat(), point_1="nok")
    response = admin_client.post(url, data)
    assert response.status_code == 200
    assert "quarantaine" in str(response.context["adminform"].form.errors)
    data["decision"] = "quarantine"
    response = admin_client.post(url, data)
    assert response.status_code == 200
    assert "comment" in response.context["adminform"].form.errors
    data["comment"] = "Coupure"
    response = admin_client.post(url, data)
    assert response.status_code == 302
    assert Item.objects.get(pk=fresh.id).status == "quarantine"
    change = StatusChange.objects.filter(item_id=fresh.id).latest("id")
    assert change.actor == "admin:admin"
    assert "Coupure" in change.reason


def test_inspection_missing_point_from_admin(admin_client, fresh, inspector, today):
    url = reverse("admin:equipment_inspection_add") + f"?item={fresh.id}"
    data = inspection_data(fresh, inspector, inspected_on=today.isoformat())
    del data["point_2"]
    response = admin_client.post(url, data)
    assert response.status_code == 200
    assert not Inspection.objects.exists()


def test_inspector_admin(admin_client, person, months_ago):
    url = reverse("admin:equipment_inspector_add")
    base = {
        "kind": "internal",
        "name": "",
        "organisation": "",
        "authorized_on": months_ago(1).isoformat(),
        "authorization_reference": "HAB-9",
        "authorization_valid_until": "",
        "families": ["harness", "rope"],
        "active": "on",
        "notes": "",
    }
    response = admin_client.post(url, base)
    assert response.status_code == 200
    assert "person" in response.context["adminform"].form.errors
    response = admin_client.post(url, {**base, "person": person.id})
    assert response.status_code == 302, response.context["adminform"].form.errors
    created = Inspector.objects.get()
    assert created.families == ["harness", "rope"]
    response = admin_client.get(reverse("admin:equipment_inspector_changelist"))
    assert "Yann Le Gall" in response.content.decode()
    delete_url = reverse("admin:equipment_inspector_delete", args=[created.pk])
    assert admin_client.get(delete_url).status_code == 403

    response = admin_client.post(
        reverse("admin:equipment_inspector_changelist"),
        {"action": "export_csv", "_selected_action": [created.pk]},
    )
    assert response["Content-Type"].startswith("text/csv")
    assert "HAB-9" in response.content.decode()
