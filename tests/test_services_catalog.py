import pytest

from gwiriek.equipment.services import catalog
from gwiriek.equipment.services.errors import InvalidDataError, NotFoundError


def test_create_product_model_normalises_standards(harness_model):
    assert harness_model.standards == ["EN 358", "EN 361"]
    assert harness_model.checklist == ["Sangles", "Coutures"]
    assert harness_model.inspection_interval_months == 12


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("en361", "EN 361"),
        ("EN  12841", "EN 12841"),
        ("nf en 397", "NF EN 397"),
        ("EN 1891", "EN 1891"),
        ("iso9001", "ISO 9001"),
        ("ANSI Z359", "ANSI Z359"),
    ],
)
def test_normalise_standard_code(code, expected):
    assert catalog.normalise_standard_code(code) == expected


def test_standards_are_shared(harness_model, petzl):
    other = catalog.create_product_model(
        manufacturer="Petzl", name="NEWTON", family="harness", standards=["EN 361"]
    )
    assert other.standards == ["EN 361"]
    from gwiriek.equipment.models import Standard

    assert Standard.objects.filter(code="EN 361").count() == 1


def test_duplicate_manufacturer_is_invalid(petzl):
    with pytest.raises(InvalidDataError):
        catalog.create_manufacturer("Petzl")


def test_duplicate_model_name_is_invalid(harness_model):
    with pytest.raises(InvalidDataError):
        catalog.create_product_model(manufacturer="Petzl", name="AVAO BOD FAST", family="harness")


def test_unlimited_with_durations_is_invalid(petzl):
    with pytest.raises(InvalidDataError, match=r"illimitée|unlimited"):
        catalog.create_product_model(
            manufacturer="Petzl",
            name="X",
            family="connector",
            lifetime_unlimited=True,
            max_lifetime_months=120,
        )


def test_zero_duration_is_invalid(petzl):
    with pytest.raises(InvalidDataError):
        catalog.create_product_model(
            manufacturer="Petzl", name="X", family="harness", max_service_months=0
        )


def test_inspection_interval_above_twelve_months_is_invalid(petzl):
    with pytest.raises(InvalidDataError) as exc:
        catalog.create_product_model(
            manufacturer="Petzl", name="X", family="harness", inspection_interval_months=13
        )
    assert "inspection_interval_months" in exc.value.details["fields"]


def test_unknown_family_is_invalid(petzl):
    with pytest.raises(InvalidDataError):
        catalog.create_product_model(manufacturer="Petzl", name="X", family="boat")


def test_unknown_manufacturer(db):
    with pytest.raises(NotFoundError):
        catalog.create_product_model(manufacturer="Nobody", name="X", family="harness")
    with pytest.raises(NotFoundError):
        catalog.list_product_models(manufacturer=999)


def test_list_and_filter_models(harness_model, rope_model, connector_model):
    assert {m.name for m in catalog.list_product_models()} == {
        "AVAO BOD FAST",
        "ERGO 12.5 mm",
        "OK",
    }
    assert [m.name for m in catalog.list_product_models(family="rope")] == ["ERGO 12.5 mm"]
    assert [m.name for m in catalog.list_product_models(query="beal")] == ["ERGO 12.5 mm"]
    assert {m.name for m in catalog.list_product_models(manufacturer="petzl")} == {
        "AVAO BOD FAST",
        "OK",
    }
    with pytest.raises(InvalidDataError):
        catalog.list_product_models(family="boat")


def test_get_product_model(rope_model):
    detail = catalog.get_product_model(rope_model.id)
    assert detail.max_storage_months == 60
    with pytest.raises(NotFoundError):
        catalog.get_product_model(rope_model.id + 100)


def test_list_manufacturers(petzl, beal):
    assert [m.name for m in catalog.list_manufacturers()] == ["Beal", "Petzl"]
