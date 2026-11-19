"""Admin smoke tests: pages render, writes go through the services."""

import pytest
from django.urls import reverse

from gwiriek.equipment.models import Item, StatusChange


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
