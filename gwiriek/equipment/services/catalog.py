"""Catalogue services: manufacturers, standards and product models."""

from collections.abc import Sequence

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from gwiriek.equipment.models import Family, Manufacturer, ProductModel, Standard
from gwiriek.equipment.services._common import get_or_not_found, validated_save
from gwiriek.equipment.services.errors import InvalidDataError
from gwiriek.equipment.services.views import (
    ManufacturerView,
    ProductModelDetail,
    ProductModelSummary,
    manufacturer_view,
    product_model_detail,
    product_model_summary,
)


def list_manufacturers() -> list[ManufacturerView]:
    """List all manufacturers by name."""
    return [manufacturer_view(m) for m in Manufacturer.objects.all()]


@transaction.atomic
def create_manufacturer(name: str, website: str = "", contact: str = "") -> ManufacturerView:
    """Create a manufacturer.

    Args:
        name: Unique name.
        website: Website URL.
        contact: Contact details.

    Returns:
        The created manufacturer.

    Raises:
        InvalidDataError: If the name is empty or already used.
    """
    manufacturer = Manufacturer(name=name.strip(), website=website, contact=contact)
    validated_save(manufacturer)
    return manufacturer_view(manufacturer)


def _resolve_manufacturer(ref: int | str) -> Manufacturer:
    if isinstance(ref, int) or str(ref).isdigit():
        return get_or_not_found(
            lambda: Manufacturer.objects.get(pk=int(ref)),
            _("No manufacturer with id %(ref)s.") % {"ref": ref},
        )
    return get_or_not_found(
        lambda: Manufacturer.objects.get(name__iexact=str(ref).strip()),
        _("No manufacturer named %(ref)s.") % {"ref": ref},
    )


def list_product_models(
    query: str | None = None,
    family: str | None = None,
    manufacturer: int | str | None = None,
) -> list[ProductModelSummary]:
    """List catalogue entries, optionally filtered.

    Args:
        query: Case-insensitive text matched against model name, reference and manufacturer.
        family: A :class:`~gwiriek.equipment.models.Family` value.
        manufacturer: Manufacturer id or name.

    Returns:
        Matching product models, by manufacturer then name.

    Raises:
        InvalidDataError: If ``family`` is not a known family.
        NotFoundError: If ``manufacturer`` does not exist.
    """
    qs = ProductModel.objects.select_related("manufacturer").prefetch_related("standards")
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(reference__icontains=query)
            | Q(manufacturer__name__icontains=query)
        )
    if family:
        if family not in Family.values:
            raise InvalidDataError(
                _("Unknown family %(family)s.") % {"family": family},
                {"allowed": list(Family.values)},
            )
        qs = qs.filter(family=family)
    if manufacturer is not None:
        qs = qs.filter(manufacturer=_resolve_manufacturer(manufacturer))
    return [product_model_summary(pm) for pm in qs]


def get_product_model(product_model_id: int) -> ProductModelDetail:
    """Return one catalogue entry.

    Args:
        product_model_id: Id of the product model.

    Returns:
        The product model with its lifespan rules, documentation and checklist.

    Raises:
        NotFoundError: If it does not exist.
    """
    pm = get_or_not_found(
        lambda: ProductModel.objects.select_related("manufacturer").get(pk=product_model_id),
        _("No product model with id %(id)s.") % {"id": product_model_id},
    )
    return product_model_detail(pm)


def normalise_standard_code(code: str) -> str:
    """Normalise a standard code: ``en361`` and ``EN  361`` both become ``EN 361``."""
    code = " ".join(code.upper().split())
    for prefix in ("EN", "NF EN", "ISO"):
        if code.startswith(prefix) and code[len(prefix) : len(prefix) + 1].isdigit():
            return f"{prefix} {code[len(prefix) :]}"
    return code


@transaction.atomic
def create_product_model(
    *,
    manufacturer: int | str,
    name: str,
    family: str,
    reference: str = "",
    standards: Sequence[str] = (),
    lifetime_unlimited: bool = False,
    max_lifetime_months: int | None = None,
    max_storage_months: int | None = None,
    max_service_months: int | None = None,
    lifetime_source: str = "",
    inspection_interval_months: int = 12,
    notice_url: str = "",
    inspection_procedure_url: str = "",
    checklist: Sequence[str] = (),
    notes: str = "",
) -> ProductModelDetail:
    """Create a catalogue entry; unknown standards are added to the standards list.

    Args:
        manufacturer: Manufacturer id or name.
        name: Commercial model name, unique per manufacturer.
        family: A :class:`~gwiriek.equipment.models.Family` value.
        reference: Manufacturer reference.
        standards: Standard codes, e.g. ``EN 361``.
        lifetime_unlimited: The manufacturer states no lifetime limit.
        max_lifetime_months: Maximum lifetime from manufacture.
        max_storage_months: Maximum storage between manufacture and first use.
        max_service_months: Maximum service life from the date put into service.
        lifetime_source: Where the durations come from.
        inspection_interval_months: Periodic inspection interval, 12 at most.
        notice_url: Instructions for use.
        inspection_procedure_url: Manufacturer inspection procedure.
        checklist: Ordered points to check during an inspection.
        notes: Free notes.

    Returns:
        The created product model.

    Raises:
        InvalidDataError: If the data breaks a validation rule.
        NotFoundError: If the manufacturer does not exist.
    """
    pm = ProductModel(
        manufacturer=_resolve_manufacturer(manufacturer),
        name=name.strip(),
        reference=reference.strip(),
        family=family,
        lifetime_unlimited=lifetime_unlimited,
        max_lifetime_months=max_lifetime_months,
        max_storage_months=max_storage_months,
        max_service_months=max_service_months,
        lifetime_source=lifetime_source,
        inspection_interval_months=inspection_interval_months,
        notice_url=notice_url,
        inspection_procedure_url=inspection_procedure_url,
        checklist=[point.strip() for point in checklist],
        notes=notes,
    )
    validated_save(pm)
    codes = {normalise_standard_code(c) for c in standards if c.strip()}
    pm.standards.set([Standard.objects.get_or_create(code=code)[0] for code in sorted(codes)])
    return product_model_detail(pm)
