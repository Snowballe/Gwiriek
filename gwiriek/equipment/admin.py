"""Django admin, in French, for entering and browsing a PPE inventory.

Item writes go through :mod:`gwiriek.equipment.services`: forms call the service checks
to report errors on the form, then ``save_model`` calls the services.
"""

from datetime import timedelta
from typing import Any

from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from gwiriek.equipment import models
from gwiriek.equipment.services import assignments as assignment_services
from gwiriek.equipment.services import items as item_services
from gwiriek.equipment.services._common import today
from gwiriek.equipment.services.end_of_life import DISCLAIMER, Basis
from gwiriek.equipment.services.errors import ServiceError
from gwiriek.equipment.services.views import item_end_of_life, open_assignment

admin.site.site_header = _("Gwiriek: PPE management")
admin.site.site_title = "Gwiriek"
admin.site.index_title = _("Inventory")


def actor_for(request: HttpRequest) -> str:
    """Name the admin user in audit records."""
    return f"admin:{request.user.get_username()}"


def add_service_error(form: forms.BaseForm, exc: ServiceError, default_field: str | None) -> None:
    """Report a service error on a form, on the fields it names when possible."""
    fields = exc.details.get("fields")
    if not fields:
        form.add_error(default_field, exc.message)
        return
    for field, messages in fields.items():
        target = field if field in form.fields else None
        for message in messages:
            form.add_error(target, message)


# --- Catalogue ------------------------------------------------------------------------------


@admin.register(models.Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin[models.Manufacturer]):
    """Manufacturers."""

    list_display = ("name", "website")
    search_fields = ("name",)


@admin.register(models.Standard)
class StandardAdmin(admin.ModelAdmin[models.Standard]):
    """Standards."""

    list_display = ("code", "title")
    search_fields = ("code", "title")


class ChecklistField(forms.CharField):
    """Edit a list of strings as one point per line."""

    widget = forms.Textarea(attrs={"rows": 8, "cols": 80})

    def prepare_value(self, value: Any) -> Any:
        """Show a list as lines."""
        if isinstance(value, list):
            return "\n".join(value)
        return value

    def to_python(self, value: Any) -> list[str]:  # type: ignore[override]
        """Turn lines into a list, dropping blank ones."""
        text = super().to_python(value) or ""
        return [line.strip() for line in text.splitlines() if line.strip()]


class ProductModelForm(forms.ModelForm[models.ProductModel]):
    """Product model form with a line-based checklist."""

    checklist = ChecklistField(
        label=_("inspection checklist"),
        required=False,
        help_text=_("One point to check per line, in order."),
    )

    class Meta:
        model = models.ProductModel
        fields = "__all__"  # noqa: DJ007 - the admin fieldsets choose the fields


def _months(value: int | None) -> str:
    if value is None:
        return "-"
    years, months = divmod(value, 12)
    if months == 0:
        return gettext("%(n)d y") % {"n": years}
    return gettext("%(n)d mo") % {"n": value}


@admin.register(models.ProductModel)
class ProductModelAdmin(admin.ModelAdmin[models.ProductModel]):
    """Catalogue entries."""

    form = ProductModelForm
    list_display = ("name", "manufacturer", "reference", "family", "lifespan")
    list_filter = ("family", "manufacturer", "lifetime_unlimited")
    search_fields = ("name", "reference", "manufacturer__name")
    list_select_related = ("manufacturer",)
    filter_horizontal = ("standards",)
    fieldsets = (
        (None, {"fields": ("manufacturer", "name", "reference", "family", "standards")}),
        (
            _("Lifespan (manufacturer data)"),
            {
                "fields": (
                    "lifetime_unlimited",
                    "max_lifetime_months",
                    "max_storage_months",
                    "max_service_months",
                    "lifetime_source",
                ),
                "description": DISCLAIMER,
            },
        ),
        (
            _("Inspection"),
            {
                "fields": (
                    "inspection_interval_months",
                    "notice_url",
                    "inspection_procedure_url",
                    "checklist",
                )
            },
        ),
        (None, {"fields": ("notes",)}),
    )

    @admin.display(description=_("lifespan"))
    def lifespan(self, obj: models.ProductModel) -> str:
        """Summarise lifespan durations."""
        if obj.lifetime_unlimited:
            return gettext("unlimited")
        return " / ".join(
            _months(v)
            for v in (obj.max_lifetime_months, obj.max_storage_months, obj.max_service_months)
        )


# --- People ---------------------------------------------------------------------------------


@admin.register(models.Person)
class PersonAdmin(admin.ModelAdmin[models.Person]):
    """People."""

    list_display = ("last_name", "first_name", "email", "active")
    list_filter = ("active",)
    search_fields = ("last_name", "first_name", "email")


# --- Items ----------------------------------------------------------------------------------


class EndOfLifeFilter(admin.SimpleListFilter):
    """Filter items by computed end of life."""

    title = _("end of life")
    parameter_name = "eol"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        """Offer the filter choices."""
        return [
            ("expired", _("Passed")),
            ("90", _("Within 90 days")),
            ("365", _("Within a year")),
            ("undetermined", _("Undetermined")),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        """Filter in Python: the end of life is computed, not stored."""
        value = self.value()
        if value is None:
            return queryset
        current = today()
        keep = []
        for item in queryset.select_related("product_model"):
            eol = item_end_of_life(item)
            if value == "undetermined":
                match = eol.basis in (Basis.UNKNOWN, Basis.UNDEFINED, Basis.PARTIAL)
            elif value == "expired":
                match = eol.is_expired(current)
            else:
                horizon = current + timedelta(days=int(value))
                match = eol.date is not None and eol.date <= horizon
            if match:
                keep.append(item.pk)
        return queryset.filter(pk__in=keep)


class ReadOnlyInline(admin.TabularInline):  # type: ignore[type-arg]
    """Inline showing history that cannot be edited here."""

    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """History is written by services only."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """History is written by services only."""
        return False


class AssignmentInline(ReadOnlyInline):
    """Assignment history of an item."""

    model = models.Assignment
    fields = ("kind", "person", "location", "started_on", "ended_on", "actor", "notes")
    readonly_fields = fields


class StatusChangeInline(ReadOnlyInline):
    """Status audit log of an item."""

    model = models.StatusChange
    fields = ("changed_at", "from_status", "to_status", "actor", "reason")
    readonly_fields = fields


class AttachmentInline(admin.TabularInline):  # type: ignore[type-arg]
    """Files attached to an item."""

    model = models.Attachment
    extra = 0
    fields = ("file", "description", "uploaded_at")
    readonly_fields = ("uploaded_at",)


class ItemForm(forms.ModelForm[models.Item]):
    """Item form: adds the reason for a status change and an assignment step."""

    status_reason = forms.CharField(
        label=_("reason for the status change"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Required for quarantine and retirement. Recorded in the audit log."),
    )
    assign_kind = forms.ChoiceField(
        label=_("assign to"),
        required=False,
        choices=[("", "---------"), *models.AssignmentKind.choices],
    )
    assign_person = forms.ModelChoiceField(
        label=_("person"), required=False, queryset=models.Person.objects.filter(active=True)
    )
    assign_location = forms.CharField(label=_("site or storage"), required=False)
    assign_date = forms.DateField(
        label=_("from"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Defaults to today."),
    )

    class Meta:
        model = models.Item
        fields = "__all__"  # noqa: DJ007 - the admin fieldsets choose the fields

    def _post_clean(self) -> None:
        """Run the service checks on the updated instance."""
        super()._post_clean()  # type: ignore[misc]  # Django hook missing from the stubs
        if self.errors:
            return
        item = self.instance
        previous = None
        if item.pk is not None:
            previous = models.Item.objects.values_list("status", flat=True).get(pk=item.pk)
        try:
            item_services.check_status_change(
                previous, item.status, self.cleaned_data.get("status_reason", "")
            )
            item_services.validate_item(item)
        except ServiceError as exc:
            add_service_error(self, exc, "status")
            return
        kind = self.cleaned_data.get("assign_kind")
        if kind:
            person = self.cleaned_data.get("assign_person")
            try:
                assignment_services.check_assignment(
                    item,
                    kind,
                    person.pk if person else None,
                    self.cleaned_data.get("assign_location", ""),
                    self.cleaned_data.get("assign_date"),
                )
            except ServiceError as exc:
                add_service_error(self, exc, "assign_kind")


@admin.register(models.Item)
class ItemAdmin(admin.ModelAdmin[models.Item]):
    """Items, with computed end of life and history."""

    form = ItemForm
    list_display = (
        "__str__",
        "label",
        "serial_number",
        "family",
        "status",
        "end_of_life",
        "assigned_to",
    )
    list_filter = (
        "status",
        EndOfLifeFilter,
        "product_model__family",
        "product_model__manufacturer",
    )
    search_fields = (
        "serial_number",
        "lot_number",
        "label",
        "product_model__name",
        "product_model__reference",
        "product_model__manufacturer__name",
    )
    list_select_related = ("product_model__manufacturer",)
    autocomplete_fields = ("product_model",)
    readonly_fields = ("end_of_life_detail", "retired_on", "created_at", "updated_at")
    inlines = (AttachmentInline, AssignmentInline, StatusChangeInline)
    fieldsets = (
        (
            None,
            {"fields": ("product_model", "serial_number", "lot_number", "label", "notes")},
        ),
        (
            _("Dates and end of life"),
            {
                "fields": (
                    "manufactured_on",
                    "purchased_on",
                    "in_service_on",
                    "end_of_life_detail",
                    "retired_on",
                )
            },
        ),
        (_("Status"), {"fields": ("status", "status_reason")}),
        (
            _("Assignment"),
            {"fields": ("assign_kind", "assign_person", "assign_location", "assign_date")},
        ),
        (
            _("Rope"),
            {
                "fields": (
                    "rope_diameter_mm",
                    "rope_length_m",
                    "rope_type",
                    "rope_terminations",
                ),
                "classes": ("collapse",),
            },
        ),
        (_("Record"), {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[models.Item]:
        """Prefetch what list columns need."""
        return super().get_queryset(request).prefetch_related("assignments__person")

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Life records are kept: retire an item instead of deleting it."""
        return False

    @admin.display(description=_("family"), ordering="product_model__family")
    def family(self, obj: models.Item) -> str:
        """Product family."""
        return obj.product_model.get_family_display()

    @admin.display(description=_("end of life"))
    def end_of_life(self, obj: models.Item) -> str:
        """End of life date, or why there is none."""
        eol = item_end_of_life(obj)
        if eol.date is None:
            return str(_basis_label(eol.basis))
        text = eol.date.strftime("%d/%m/%Y")
        if eol.basis == Basis.PARTIAL:
            text += " " + gettext("(at most)")
        if eol.is_expired(today()):
            return format_html('<strong style="color:#ba2121">{}</strong>', text)
        return text

    @admin.display(description=_("assigned to"))
    def assigned_to(self, obj: models.Item) -> str:
        """Current assignment."""
        current = open_assignment(obj)
        return str(current) if current else "-"

    @admin.display(description=_("computed end of life"))
    def end_of_life_detail(self, obj: models.Item) -> str:
        """Explain the end-of-life computation."""
        if obj.pk is None:
            return "-"
        eol = item_end_of_life(obj)
        lines = [str(_basis_label(eol.basis))]
        if eol.date:
            lines.append(gettext("Date: %(date)s") % {"date": eol.date.strftime("%d/%m/%Y")})
        for bound in eol.bounds:
            lines.append(
                gettext("Limit %(limit)s: %(date)s")
                % {"limit": _limit_label(bound.limit), "date": bound.date.strftime("%d/%m/%Y")}
            )
        if eol.missing:
            names = ", ".join(_field_label(models.Item, f) for f in eol.missing)
            lines.append(gettext("Missing: %(fields)s") % {"fields": names})
        if eol.storage_exceeded:
            lines.append(gettext("Put into service after the maximum storage time."))
        lines.append(str(DISCLAIMER))
        return format_html_join("", "<div>{}</div>", ((line,) for line in lines))

    def save_model(self, request: HttpRequest, obj: models.Item, form: Any, change: bool) -> None:
        """Save through the services, then apply the requested assignment."""
        actor = actor_for(request)
        reason = form.cleaned_data.get("status_reason", "")
        if not change and not reason:
            reason = gettext("Registered")
        item_services.save_item(obj, actor=actor, reason=reason)
        kind = form.cleaned_data.get("assign_kind")
        if kind:
            person = form.cleaned_data.get("assign_person")
            assignment_services.assign_item(
                f"id:{obj.pk}",
                kind,
                actor=actor,
                person_id=person.pk if person else None,
                location=form.cleaned_data.get("assign_location", ""),
                started_on=form.cleaned_data.get("assign_date"),
            )
            obj.refresh_from_db()


def _field_label(model: type[models.Item], name: str) -> str:
    field = model._meta.get_field(name)
    return str(getattr(field, "verbose_name", name))


def _basis_label(basis: Basis) -> Any:
    return {
        Basis.COMPLETE: _("Computed from all manufacturer limits."),
        Basis.PARTIAL: _("Partial: data is missing, the real end of life may be earlier."),
        Basis.PENDING: _("Starts when the item is put into service."),
        Basis.UNKNOWN: _("Cannot be computed: dates are missing."),
        Basis.UNLIMITED: _("No lifetime limit stated by the manufacturer."),
        Basis.UNDEFINED: _("No lifespan data in the catalogue."),
    }[basis]


def _limit_label(limit: str) -> Any:
    return {
        "manufacture": _("from manufacture"),
        "storage": _("maximum storage"),
        "service": _("service life"),
    }[limit]


# --- Audit and history (read-only) ----------------------------------------------------------


class ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Admin that only lists and shows records."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Records are written by services only."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Records are written by services only."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Records are kept."""
        return False


@admin.register(models.StatusChange)
class StatusChangeAdmin(ReadOnlyAdmin):
    """Status audit log."""

    list_display = ("changed_at", "item", "from_status", "to_status", "actor", "reason")
    list_filter = ("to_status", "from_status")
    search_fields = ("item__serial_number", "item__label", "actor", "reason")
    list_select_related = ("item__product_model__manufacturer",)
    date_hierarchy = "changed_at"


@admin.register(models.Assignment)
class AssignmentAdmin(ReadOnlyAdmin):
    """Assignment history (assign from the item page)."""

    list_display = ("item", "kind", "person", "location", "started_on", "ended_on", "actor")
    list_filter = ("kind", "person")
    search_fields = ("item__serial_number", "item__label", "location")
    list_select_related = ("item__product_model__manufacturer", "person")
