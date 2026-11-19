"""Shared fixtures: a small catalogue built through the services."""

from datetime import date

import pytest
from django.utils import timezone

from gwiriek.equipment.services import catalog, items, people
from gwiriek.equipment.services.end_of_life import add_months


@pytest.fixture
def today() -> date:
    return timezone.localdate()


@pytest.fixture
def months_ago(today):
    def _months_ago(n: int) -> date:
        return add_months(today, -n)

    return _months_ago


@pytest.fixture
def petzl(db):
    return catalog.create_manufacturer("Petzl", website="https://www.petzl.com")


@pytest.fixture
def beal(db):
    return catalog.create_manufacturer("Beal")


@pytest.fixture
def harness_model(petzl):
    return catalog.create_product_model(
        manufacturer="Petzl",
        name="AVAO BOD FAST",
        family="harness",
        standards=["EN 361", "en358"],
        max_lifetime_months=120,
        checklist=["Sangles", "Coutures"],
    )


@pytest.fixture
def connector_model(petzl):
    return catalog.create_product_model(
        manufacturer=petzl.id, name="OK", family="connector", lifetime_unlimited=True
    )


@pytest.fixture
def rope_model(beal):
    return catalog.create_product_model(
        manufacturer="Beal",
        name="ERGO 12.5 mm",
        family="rope",
        max_lifetime_months=180,
        max_storage_months=60,
        max_service_months=120,
    )


@pytest.fixture
def undefined_model(petzl):
    return catalog.create_product_model(manufacturer="Petzl", name="I'D S", family="descender")


@pytest.fixture
def person(db):
    return people.create_person("Yann", "Le Gall")


@pytest.fixture
def make_item(db):
    counter = iter(range(1, 10_000))

    def _make_item(model, **fields):
        fields.setdefault("serial_number", f"SN-{next(counter):04d}")
        return items.create_item(product_model_id=model.id, actor="test", **fields)

    return _make_item
