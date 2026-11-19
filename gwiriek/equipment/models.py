"""Data model for PPE inventory, following French regulation and the Copalme life record.

Business rules (validation across objects, status changes, end of life, assignments) live
in :mod:`gwiriek.equipment.services`. Models only hold structure and field-level checks.
"""

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class Family(models.TextChoices):
    """Equipment family, used for filtering and family-specific fields."""

    HARNESS = "harness", _("Harness")
    LANYARD = "lanyard", _("Lanyard")
    CONNECTOR = "connector", _("Connector")
    DESCENDER = "descender", _("Descender")
    ASCENDER = "ascender", _("Ascender / rope clamp")
    BACKUP = "backup", _("Mobile fall arrester")
    ROPE = "rope", _("Rope")
    HELMET = "helmet", _("Helmet")
    ABSORBER = "absorber", _("Energy absorber")
    OTHER = "other", _("Other")


class ItemStatus(models.TextChoices):
    """Lifecycle status of an item. ``retired`` is terminal."""

    IN_STOCK = "in_stock", _("In stock")
    IN_SERVICE = "in_service", _("In service")
    WATCH = "watch", _("Under watch")
    QUARANTINE = "quarantine", _("Quarantine")
    RETIRED = "retired", _("Retired")


class RopeType(models.TextChoices):
    """EN 1891 low-stretch kernmantle rope type."""

    A = "A", _("Type A")
    B = "B", _("Type B")


class AssignmentKind(models.TextChoices):
    """Who or where an item is assigned to."""

    PERSON = "person", _("Person")
    SITE = "site", _("Site")
    STOCK = "stock", _("Stock")


class InspectionDecision(models.TextChoices):
    """Outcome of a periodic inspection (Copalme, step 6 of the procedure)."""

    VALID = "valid", _("Fit for use")
    WATCH = "watch", _("Fit for use, under watch")
    QUARANTINE = "quarantine", _("Quarantine")
    RETIRE = "retire", _("Retire")


class CheckResult(models.TextChoices):
    """Result of one checklist point."""

    OK = "ok", _("Compliant")
    NOK = "nok", _("Not compliant")
    NA = "na", _("Not applicable")


class InspectorKind(models.TextChoices):
    """Whether a qualified person belongs to the company (R4323-100)."""

    INTERNAL = "internal", _("Internal")
    EXTERNAL = "external", _("External")


class Manufacturer(models.Model):
    """Equipment manufacturer (Copalme field 3: manufacturer and contact details)."""

    name = models.CharField(_("name"), max_length=100, unique=True)
    website = models.URLField(_("website"), blank=True)
    contact = models.TextField(_("contact details"), blank=True)

    class Meta:
        verbose_name = _("manufacturer")
        verbose_name_plural = _("manufacturers")
        ordering: ClassVar = ["name"]

    def __str__(self) -> str:
        return self.name


class Standard(models.Model):
    """A European standard an equipment model complies with (e.g. EN 361)."""

    code = models.CharField(_("code"), max_length=40, unique=True)
    title = models.CharField(_("title"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("standard")
        verbose_name_plural = _("standards")
        ordering: ClassVar = ["code"]

    def __str__(self) -> str:
        return self.code


def _validate_checklist(value: Any) -> None:
    """Ensure a checklist is a list of non-empty strings."""
    if not isinstance(value, list) or not all(
        isinstance(point, str) and point.strip() for point in value
    ):
        raise ValidationError(_("The checklist must be a list of non-empty points."))


class ProductModel(models.Model):
    """A catalogue entry: one commercial model from one manufacturer.

    Lifespan durations are in months and come from the manufacturer's instructions. A null
    duration means "not specified"; ``lifetime_unlimited`` states explicitly that the
    manufacturer gives no limit (e.g. Petzl metal products).
    """

    manufacturer = models.ForeignKey(
        Manufacturer,
        on_delete=models.PROTECT,
        related_name="product_models",
        verbose_name=_("manufacturer"),
    )
    name = models.CharField(_("model name"), max_length=150)
    reference = models.CharField(_("manufacturer reference"), max_length=60, blank=True)
    family = models.CharField(_("family"), max_length=20, choices=Family.choices)
    standards = models.ManyToManyField(
        Standard, blank=True, related_name="product_models", verbose_name=_("standards")
    )
    lifetime_unlimited = models.BooleanField(
        _("unlimited lifetime"),
        default=False,
        help_text=_("The manufacturer states no lifetime limit (e.g. some metal products)."),
    )
    max_lifetime_months = models.PositiveSmallIntegerField(
        _("maximum lifetime (months)"),
        null=True,
        blank=True,
        help_text=_("Maximum lifetime counted from the date of manufacture."),
    )
    max_storage_months = models.PositiveSmallIntegerField(
        _("maximum storage (months)"),
        null=True,
        blank=True,
        help_text=_("Maximum time between manufacture and first use."),
    )
    max_service_months = models.PositiveSmallIntegerField(
        _("maximum service life (months)"),
        null=True,
        blank=True,
        help_text=_("Maximum time in use, counted from the date put into service."),
    )
    lifetime_source = models.CharField(
        _("lifetime source"),
        max_length=300,
        blank=True,
        help_text=_("Where the durations come from (notice, URL, version)."),
    )
    inspection_interval_months = models.PositiveSmallIntegerField(
        _("inspection interval (months)"),
        default=12,
        help_text=_("Periodic inspection interval; French regulation requires at most 12."),
    )
    notice_url = models.URLField(_("instructions for use URL"), blank=True)
    inspection_procedure_url = models.URLField(_("inspection procedure URL"), blank=True)
    checklist = models.JSONField(
        _("inspection checklist"),
        default=list,
        blank=True,
        validators=[_validate_checklist],
        help_text=_("Ordered list of points to check during an inspection."),
    )
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("product model")
        verbose_name_plural = _("product models")
        ordering: ClassVar = ["manufacturer__name", "name"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["manufacturer", "name"], name="unique_product_model_name"
            ),
            models.CheckConstraint(
                condition=Q(inspection_interval_months__gte=1),
                name="product_model_inspection_interval_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.manufacturer} {self.name}"

    def clean(self) -> None:
        """Reject contradictory lifespan data and an inspection interval above 12 months."""
        durations = (self.max_lifetime_months, self.max_storage_months, self.max_service_months)
        if self.lifetime_unlimited and any(d is not None for d in durations):
            raise ValidationError(
                _("An unlimited lifetime cannot be combined with maximum durations.")
            )
        if any(d == 0 for d in durations):
            raise ValidationError(_("Durations must be at least one month."))
        if self.inspection_interval_months and self.inspection_interval_months > 12:
            raise ValidationError(
                {
                    "inspection_interval_months": _(
                        "French regulation requires an inspection at least every 12 months."
                    )
                }
            )


class Person(models.Model):
    """Someone equipment can be assigned to. Not a login account."""

    first_name = models.CharField(_("first name"), max_length=100)
    last_name = models.CharField(_("last name"), max_length=100)
    email = models.EmailField(_("email"), blank=True)
    active = models.BooleanField(_("active"), default=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("person")
        verbose_name_plural = _("people")
        ordering: ClassVar = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Item(models.Model):
    """One physical piece of equipment, with its life record data (Copalme fields 1-7)."""

    product_model = models.ForeignKey(
        ProductModel, on_delete=models.PROTECT, related_name="items", verbose_name=_("model")
    )
    serial_number = models.CharField(_("serial number"), max_length=60, blank=True)
    lot_number = models.CharField(_("lot number"), max_length=60, blank=True)
    label = models.CharField(
        _("internal label"),
        max_length=40,
        blank=True,
        help_text=_("Company marking, e.g. C-012. Unique when set."),
    )
    manufactured_on = models.DateField(
        _("date of manufacture"),
        null=True,
        blank=True,
        help_text=_("If only the month is known, enter the first day of that month."),
    )
    purchased_on = models.DateField(_("date of purchase"), null=True, blank=True)
    in_service_on = models.DateField(
        _("date put into service"),
        null=True,
        blank=True,
        help_text=_("First use; starts the service life clock."),
    )
    status = models.CharField(
        _("status"), max_length=20, choices=ItemStatus.choices, default=ItemStatus.IN_STOCK
    )
    retired_on = models.DateField(_("date retired"), null=True, blank=True, editable=False)
    rope_diameter_mm = models.DecimalField(
        _("rope diameter (mm)"), max_digits=4, decimal_places=1, null=True, blank=True
    )
    rope_length_m = models.DecimalField(
        _("rope length (m)"), max_digits=6, decimal_places=1, null=True, blank=True
    )
    rope_type = models.CharField(_("rope type"), max_length=1, choices=RopeType.choices, blank=True)
    rope_terminations = models.CharField(
        _("rope terminations"),
        max_length=200,
        blank=True,
        help_text=_("Number and kind, e.g. two sewn eyes."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("item")
        verbose_name_plural = _("items")
        ordering: ClassVar = ["product_model__manufacturer__name", "product_model__name", "id"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["product_model", "serial_number"],
                condition=~Q(serial_number=""),
                name="unique_serial_per_model",
            ),
            models.UniqueConstraint(
                fields=["label"], condition=~Q(label=""), name="unique_item_label"
            ),
            models.CheckConstraint(
                condition=~Q(serial_number="", lot_number=""),
                name="item_has_serial_or_lot",
            ),
        ]

    def __str__(self) -> str:
        ident = self.label or self.serial_number or self.lot_number
        return f"{self.product_model} ({ident})"


class StatusChangeQuerySet(models.QuerySet["StatusChange"]):
    """QuerySet that refuses bulk updates and deletions of audit records."""

    def update(self, **kwargs: Any) -> int:
        """Refuse: audit records are immutable."""
        raise PermissionError("Status changes are append-only.")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: audit records are immutable."""
        raise PermissionError("Status changes are append-only.")


class StatusChange(models.Model):
    """Append-only audit record of an item's status change.

    Created only by :func:`gwiriek.equipment.services.items.save_item`.
    """

    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="status_changes", verbose_name=_("item")
    )
    from_status = models.CharField(
        _("previous status"), max_length=20, choices=ItemStatus.choices, blank=True
    )
    to_status = models.CharField(_("new status"), max_length=20, choices=ItemStatus.choices)
    changed_at = models.DateTimeField(_("changed at"), auto_now_add=True)
    actor = models.CharField(_("by"), max_length=150)
    reason = models.TextField(_("reason"), blank=True)

    objects = StatusChangeQuerySet.as_manager()

    class Meta:
        verbose_name = _("status change")
        verbose_name_plural = _("status changes")
        ordering: ClassVar = ["item", "changed_at", "id"]

    def __str__(self) -> str:
        return f"{self.item_id}: {self.from_status or '-'} -> {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Insert only; an existing record cannot be modified."""
        if self.pk is not None:
            raise PermissionError("Status changes are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse: audit records are immutable."""
        raise PermissionError("Status changes are append-only.")


class Assignment(models.Model):
    """Dated assignment of an item to a person, a site or the stock.

    An item has at most one open assignment (``ended_on`` is null); closed ones form its
    history. Created only by :func:`gwiriek.equipment.services.assignments.assign_item`.
    """

    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="assignments", verbose_name=_("item")
    )
    kind = models.CharField(_("assigned to"), max_length=10, choices=AssignmentKind.choices)
    person = models.ForeignKey(
        Person,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assignments",
        verbose_name=_("person"),
    )
    location = models.CharField(
        _("location"), max_length=150, blank=True, help_text=_("Site or storage name.")
    )
    started_on = models.DateField(_("from"))
    ended_on = models.DateField(_("until"), null=True, blank=True)
    actor = models.CharField(_("by"), max_length=150)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("assignment")
        verbose_name_plural = _("assignments")
        ordering: ClassVar = ["item", "-started_on", "-id"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["item"], condition=Q(ended_on__isnull=True), name="one_open_assignment"
            ),
            models.CheckConstraint(
                condition=(Q(kind="person") & Q(person__isnull=False))
                | (~Q(kind="person") & Q(person__isnull=True)),
                name="assignment_person_matches_kind",
            ),
            models.CheckConstraint(
                condition=~Q(kind="site", location=""), name="assignment_site_has_location"
            ),
            models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gte=models.F("started_on")),
                name="assignment_dates_ordered",
            ),
        ]

    def __str__(self) -> str:
        target = self.person if self.kind == AssignmentKind.PERSON else self.location
        return f"{self.get_kind_display()} {target or ''}".strip()


def attachment_path(instance: "Attachment", filename: str) -> str:
    """Store attachments under a directory per item."""
    return f"items/{instance.item_id}/{filename}"


class Attachment(models.Model):
    """A file attached to an item: invoice, instructions, external inspection report."""

    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="attachments", verbose_name=_("item")
    )
    file = models.FileField(_("file"), upload_to=attachment_path)
    description = models.CharField(_("description"), max_length=200, blank=True)
    uploaded_at = models.DateTimeField(_("uploaded at"), auto_now_add=True)

    class Meta:
        verbose_name = _("attachment")
        verbose_name_plural = _("attachments")
        ordering: ClassVar = ["item", "-uploaded_at"]

    def __str__(self) -> str:
        return self.description or self.file.name or ""


def _validate_families(value: Any) -> None:
    """Ensure a scope is a list of known, distinct families."""
    if (
        not isinstance(value, list)
        or not all(isinstance(f, str) and f in Family.values for f in value)
        or len(set(value)) != len(value)
    ):
        raise ValidationError(_("The scope must be a list of distinct, known families."))


def authorization_path(instance: "Inspector", filename: str) -> str:
    """Store written authorizations under a directory per inspector."""
    return f"inspectors/{filename}"


class Inspector(models.Model):
    """A qualified person allowed to carry out periodic inspections (R4323-100).

    An internal inspector is one of the company's people; an external one is named with
    their organisation. The written authorization (date, reference or document, optional
    end of validity) and the scope of families decide what they may inspect; the rules are
    in :mod:`gwiriek.equipment.services.inspectors`.
    """

    kind = models.CharField(_("type"), max_length=10, choices=InspectorKind.choices)
    person = models.OneToOneField(
        Person,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="inspector",
        verbose_name=_("person"),
        help_text=_("Internal inspectors only."),
    )
    name = models.CharField(
        _("name"), max_length=150, blank=True, help_text=_("External inspectors only.")
    )
    organisation = models.CharField(
        _("organisation"), max_length=150, blank=True, help_text=_("External inspectors only.")
    )
    authorized_on = models.DateField(
        _("authorization date"),
        null=True,
        blank=True,
        help_text=_("Date of the written authorization; none means not authorized."),
    )
    authorization_reference = models.CharField(
        _("authorization reference"),
        max_length=200,
        blank=True,
        help_text=_("Reference of the written authorization, training or certificate."),
    )
    authorization_document = models.FileField(
        _("authorization document"), upload_to=authorization_path, blank=True
    )
    authorization_valid_until = models.DateField(
        _("authorization valid until"),
        null=True,
        blank=True,
        help_text=_("Last day of validity, if the authorization expires."),
    )
    families = models.JSONField(
        _("scope (families)"),
        default=list,
        blank=True,
        validators=[_validate_families],
        help_text=_("Families of equipment this person may inspect."),
    )
    active = models.BooleanField(_("active"), default=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("inspector")
        verbose_name_plural = _("inspectors")
        ordering: ClassVar = ["id"]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=(Q(kind="internal") & Q(person__isnull=False))
                | (Q(kind="external") & Q(person__isnull=True) & ~Q(name="")),
                name="inspector_identity_matches_kind",
            ),
            models.CheckConstraint(
                condition=Q(authorization_valid_until__isnull=True)
                | Q(authorized_on__isnull=False),
                name="inspector_validity_needs_authorization",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """Name shown in lists and frozen into inspection records."""
        if self.kind == InspectorKind.INTERNAL and self.person_id is not None:
            return str(self.person)
        if self.organisation:
            return f"{self.name} ({self.organisation})"
        return self.name


class InspectionQuerySet(models.QuerySet["Inspection"]):
    """QuerySet that refuses bulk updates and deletions of inspection records."""

    def update(self, **kwargs: Any) -> int:
        """Refuse: inspection records are immutable."""
        raise PermissionError("Inspections cannot be modified.")

    def delete(self) -> tuple[int, dict[str, int]]:
        """Refuse: inspection records are immutable."""
        raise PermissionError("Inspections cannot be deleted.")


def inspection_report_path(instance: "Inspection", filename: str) -> str:
    """Store inspection reports under a directory per item."""
    return f"inspections/{instance.item_id}/{filename}"


class Inspection(models.Model):
    """Immutable periodic inspection record (Copalme fields 8-13, R4323-100 to 102).

    The checklist and the inspector's identity are frozen at recording time, so later
    changes to the catalogue or to the inspector do not rewrite history. A mistake is
    corrected by recording a new inspection. Created only by
    :func:`gwiriek.equipment.services.inspections.record_inspection`.
    """

    item = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name="inspections", verbose_name=_("item")
    )
    inspected_on = models.DateField(_("inspection date"))
    inspector = models.ForeignKey(
        Inspector,
        on_delete=models.PROTECT,
        related_name="inspections",
        verbose_name=_("inspector"),
    )
    inspector_snapshot = models.JSONField(
        _("inspector at the time"),
        default=dict,
        help_text=_("Identity and authorization of the inspector when the record was made."),
    )
    decision = models.CharField(_("decision"), max_length=20, choices=InspectionDecision.choices)
    checklist_results = models.JSONField(
        _("checklist results"),
        default=list,
        blank=True,
        help_text=_("Points of the model's checklist, frozen, with their result."),
    )
    comment = models.TextField(_("comment"), blank=True)
    next_due_on = models.DateField(_("next inspection due"), null=True, blank=True)
    report = models.FileField(_("report"), upload_to=inspection_report_path, blank=True)
    report_received_on = models.DateField(
        _("report received on"),
        null=True,
        blank=True,
        help_text=_("Date an external report was handed over, when it is not attached."),
    )
    report_archive_location = models.CharField(
        _("report archive location"),
        max_length=200,
        blank=True,
        help_text=_("Where an external report is archived, when it is not attached."),
    )
    resulting_status = models.CharField(
        _("resulting status"), max_length=20, choices=ItemStatus.choices
    )
    actor = models.CharField(_("recorded by"), max_length=150)
    created_at = models.DateTimeField(_("recorded at"), auto_now_add=True)

    objects = InspectionQuerySet.as_manager()

    class Meta:
        verbose_name = _("inspection")
        verbose_name_plural = _("inspections")
        ordering: ClassVar = ["item", "-inspected_on", "-id"]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(next_due_on__isnull=True) | Q(next_due_on__gt=models.F("inspected_on")),
                name="inspection_next_due_after_date",
            ),
            models.CheckConstraint(
                condition=~Q(decision="retire") | Q(next_due_on__isnull=True),
                name="inspection_retired_has_no_next_due",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item} {self.inspected_on}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Insert only; an existing record cannot be modified."""
        if self.pk is not None:
            raise PermissionError("Inspections cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse: inspection records are immutable."""
        raise PermissionError("Inspections cannot be deleted.")


class DigestLog(models.Model):
    """Record of a due-items digest sent by e-mail, to avoid sending it twice a day."""

    sent_on = models.DateField(_("day"))
    fingerprint = models.CharField(_("fingerprint"), max_length=64)
    subject = models.CharField(_("subject"), max_length=200)
    recipients = models.TextField(_("recipients"))
    item_count = models.PositiveIntegerField(_("items"))
    forced = models.BooleanField(_("forced"), default=False)
    sent_at = models.DateTimeField(_("sent at"), auto_now_add=True)

    class Meta:
        verbose_name = _("e-mail digest")
        verbose_name_plural = _("e-mail digests")
        ordering: ClassVar = ["-sent_at"]
        indexes: ClassVar = [models.Index(fields=["sent_on", "fingerprint"])]

    def __str__(self) -> str:
        return f"{self.sent_on} {self.subject}"
