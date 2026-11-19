"""Shared fixtures: a small catalogue built through the services."""

from datetime import date

import pytest
from django.utils import timezone

from gwiriek.equipment.services import catalog, items, people
from gwiriek.equipment.services.end_of_life import add_months


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


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


@pytest.fixture
def inspector(person, months_ago):
    from gwiriek.equipment.models import Family
    from gwiriek.equipment.services import inspectors

    return inspectors.create_inspector(
        kind="internal",
        person_id=person.id,
        authorized_on=months_ago(24),
        authorization_reference="HAB-1",
        families=Family.values,
    )


@pytest.fixture
def external_inspector(db, months_ago):
    from gwiriek.equipment.services import inspectors

    return inspectors.create_inspector(
        kind="external",
        name="Erwan Morvan",
        organisation="Bureau",
        authorized_on=months_ago(24),
        authorization_reference="Contrat",
        families=["harness", "rope"],
    )


@pytest.fixture
def all_ok():
    def _all_ok(model):
        return dict.fromkeys(model.checklist, "ok")

    return _all_ok


@pytest.fixture(autouse=True)
def _reset_language():
    """Keep the default language between tests: a request may activate another one."""
    from django.conf import settings
    from django.utils import translation

    yield
    translation.activate(settings.LANGUAGE_CODE)
