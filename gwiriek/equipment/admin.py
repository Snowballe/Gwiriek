"""Django admin, in French, for entering and browsing a PPE inventory.

Item writes go through :mod:`gwiriek.equipment.services`: forms call the service checks
to report errors on the form, then ``save_model`` calls the services.
"""

from datetime import timedelta
from typing import Any, ClassVar

from django import forms
from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from gwiriek.equipment import models
from gwiriek.equipment.services import assignments as assignment_services
from gwiriek.equipment.services import inspections as inspection_services
from gwiriek.equipment.services import inspectors as inspector_services
from gwiriek.equipment.services import items as item_services
from gwiriek.equipment.services._common import today
from gwiriek.equipment.services.end_of_life import DISCLAIMER, Basis
from gwiriek.equipment.services.errors import ServiceError
from gwiriek.equipment.services.views import (
    inspector_issues,
    item_end_of_life,
    item_inspection_deadline,
    open_assignment,
)

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


class InspectionInline(ReadOnlyInline):
    """Inspection history of an item (record one with the link above)."""

    model = models.Inspection
    fields = (
        "inspected_on",
        "inspector",
        "decision",
        "resulting_status",
        "next_due_on",
        "comment",
    )
    readonly_fields = fields
    show_change_link = True


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
        "next_inspection",
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
    readonly_fields = (
        "end_of_life_detail",
        "inspection_detail",
        "retired_on",
        "created_at",
        "updated_at",
    )
    inlines = (AttachmentInline, InspectionInline, AssignmentInline, StatusChangeInline)
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
        (_("Periodic inspection"), {"fields": ("inspection_detail",)}),
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
        return super().get_queryset(request).prefetch_related("assignments__person", "inspections")

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

    @admin.display(description=_("next inspection"))
    def next_inspection(self, obj: models.Item) -> str:
        """Next periodic inspection, in red when overdue."""
        deadline = item_inspection_deadline(obj)
        text = deadline.due_on.strftime("%d/%m/%Y") if deadline.due_on else gettext("now")
        if obj.status == models.ItemStatus.RETIRED:
            return "-"
        if deadline.is_overdue(today()):
            return format_html('<strong style="color:#ba2121">{}</strong>', text)
        return text

    @admin.display(description=_("next inspection"))
    def inspection_detail(self, obj: models.Item) -> str:
        """Next inspection and a link to record one."""
        if obj.pk is None:
            return "-"
        if obj.status == models.ItemStatus.RETIRED:
            return gettext("Retired: no more inspections.")
        deadline = item_inspection_deadline(obj)
        if deadline.due_on is None:
            text = gettext("Due now: never inspected and no date of entry.")
        else:
            text = gettext("Due on %(date)s") % {"date": deadline.due_on.strftime("%d/%m/%Y")}
            if deadline.is_overdue(today()):
                text += " " + gettext("(overdue)")
        url = reverse("admin:equipment_inspection_add") + f"?item={obj.pk}"
        return format_html(
            '<div>{}</div><div><a class="button" href="{}">{}</a></div>',
            text,
            url,
            gettext("Record an inspection"),
        )

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


# --- Inspectors and inspections -------------------------------------------------------------


class InspectorForm(forms.ModelForm[models.Inspector]):
    """Inspector form: scope as checkboxes, service checks on the instance."""

    families = forms.MultipleChoiceField(
        label=_("scope (families)"),
        required=False,
        choices=models.Family.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = models.Inspector
        fields = "__all__"  # noqa: DJ007 - the admin fieldsets choose the fields

    def validate_constraints(self) -> None:
        """Run the service checks first: their messages name the fields to fix."""
        try:
            inspector_services.validate_inspector(self.instance)
        except ServiceError as exc:
            add_service_error(self, exc, None)
            return
        super().validate_constraints()


@admin.register(models.Inspector)
class InspectorAdmin(admin.ModelAdmin[models.Inspector]):
    """Qualified persons (R4323-100): deactivate rather than delete."""

    form = InspectorForm
    list_display = ("__str__", "kind", "authorized_on", "authorization_valid_until", "qualified")
    list_filter = ("kind", "active")
    search_fields = ("name", "organisation", "person__first_name", "person__last_name")
    list_select_related = ("person",)
    autocomplete_fields = ("person",)
    actions = ("export_csv",)
    fieldsets = (
        (None, {"fields": ("kind", "person", "name", "organisation", "active")}),
        (
            _("Written authorization"),
            {
                "fields": (
                    "authorized_on",
                    "authorization_reference",
                    "authorization_document",
                    "authorization_valid_until",
                    "families",
                )
            },
        ),
        (None, {"fields": ("notes",)}),
    )

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """The list of qualified persons is kept: deactivate an inspector instead."""
        return False

    @admin.display(description=_("qualified today"), boolean=True)
    def qualified(self, obj: models.Inspector) -> bool:
        """Whether the inspector may record an inspection today."""
        return not inspector_issues(obj, today())

    def save_model(
        self, request: HttpRequest, obj: models.Inspector, form: Any, change: bool
    ) -> None:
        """Save through the services."""
        inspector_services.save_inspector(obj)

    @admin.action(description=_("Export the list of qualified persons (CSV)"))
    def export_csv(
        self, request: HttpRequest, queryset: QuerySet[models.Inspector]
    ) -> HttpResponse:
        """Download the selected inspectors as CSV."""
        content = inspector_services.export_inspectors_csv(
            include_inactive=True, inspector_ids=queryset.values_list("pk", flat=True)
        )
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="verificateurs.csv"'
        return response


POINT_PREFIX = "point_"


class InspectionForm(forms.ModelForm[models.Inspection]):
    """Inspection entry form; checklist fields are added per item by the admin."""

    class Meta:
        model = models.Inspection
        fields = (
            "item",
            "inspector",
            "inspected_on",
            "decision",
            "comment",
            "next_due_on",
            "report",
            "report_received_on",
            "report_archive_location",
        )
        widgets: ClassVar = {
            "inspected_on": forms.DateInput(attrs={"type": "date"}),
            "next_due_on": forms.DateInput(attrs={"type": "date"}),
            "report_received_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if "inspector" not in self.fields:  # read-only view of a recorded inspection
            return
        self.fields["inspected_on"].initial = today()
        self.fields["next_due_on"].help_text = gettext(
            "Leave empty for the model's interval (at most 12 months), capped by the end of life."
        )
        inspector_field = self.fields["inspector"]
        assert isinstance(inspector_field, forms.ModelChoiceField)
        inspector_field.queryset = models.Inspector.objects.filter(active=True).select_related(
            "person"
        )

    def results(self) -> list[tuple[str, str]]:
        """Checklist results entered in the form, by point text."""
        return [
            (str(field.label), self.cleaned_data[name])
            for name, field in self.fields.items()
            if name.startswith(POINT_PREFIX) and name in self.cleaned_data
        ]

    def _post_clean(self) -> None:
        """Run the service checks instead of the model's: records are built by services."""
        if self.errors:
            return
        data = self.cleaned_data
        try:
            inspection_services.prepare_inspection(
                item_services.resolve_item(f"id:{data['item'].pk}"),
                inspector_services.resolve_inspector(data["inspector"].pk),
                decision=data["decision"],
                results=self.results(),
                inspected_on=data["inspected_on"],
                next_due_on=data.get("next_due_on"),
                comment=data.get("comment", ""),
                has_report=bool(data.get("report")),
                report_received_on=data.get("report_received_on"),
                report_archive_location=data.get("report_archive_location", ""),
            )
        except ServiceError as exc:
            add_service_error(self, exc, None)


@admin.register(models.Inspection)
class InspectionAdmin(admin.ModelAdmin[models.Inspection]):
    """Periodic inspections: recorded once with the item's checklist, then read-only."""

    form = InspectionForm
    list_display = (
        "inspected_on",
        "item",
        "inspector",
        "decision",
        "resulting_status",
        "next_due_on",
    )
    list_filter = ("decision", "inspector")
    search_fields = ("item__serial_number", "item__label", "comment")
    list_select_related = ("item__product_model__manufacturer", "inspector__person")
    autocomplete_fields = ("item",)
    date_hierarchy = "inspected_on"
    view_fields = (
        "item",
        "inspected_on",
        "inspector_at_the_time",
        "decision",
        "checklist",
        "comment",
        "next_due_on",
        "resulting_status",
        "report",
        "report_received_on",
        "report_archive_location",
        "actor",
        "created_at",
    )

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Recorded inspections cannot be modified: record a new one to correct."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Inspection records are kept."""
        return False

    def _checklist(self, request: HttpRequest) -> list[str]:
        """Checklist of the item being inspected, from the submitted or initial data."""
        item_id = request.POST.get("item") or request.GET.get("item")
        if not item_id or not str(item_id).isdigit():
            return []
        product_model = models.ProductModel.objects.filter(items__pk=int(item_id)).first()
        return list(product_model.checklist) if product_model else []

    def get_fieldsets(self, request: HttpRequest, obj: Any = None) -> Any:
        """Read-only record for an existing inspection, entry form otherwise."""
        if obj is not None:
            return [(None, {"fields": self.view_fields})]
        points = [f"{POINT_PREFIX}{n}" for n in range(1, len(self._checklist(request)) + 1)]
        return [
            (None, {"fields": ("item", "inspector", "inspected_on")}),
            (
                _("Checklist"),
                {
                    "fields": points,
                    "description": gettext(
                        "Points of the model's checklist; they are frozen in the record. "
                        "A non-compliant point only allows quarantine or retirement."
                    )
                    if points
                    else gettext(
                        "Choose the item and save to load its checklist. Without a checklist, "
                        "describe what was checked in the comment."
                    ),
                },
            ),
            (_("Decision"), {"fields": ("decision", "comment", "next_due_on")}),
            (
                _("Report"),
                {
                    "fields": ("report", "report_received_on", "report_archive_location"),
                    "description": gettext(
                        "External inspector: attach the report, or give the date it was "
                        "received and where it is archived."
                    ),
                },
            ),
        ]

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> Any:
        """Everything is read-only once recorded."""
        return self.view_fields if obj is not None else ()

    def get_form(
        self, request: HttpRequest, obj: Any = None, change: bool = False, **kwargs: Any
    ) -> Any:
        """Add one result field per checklist point of the item."""
        if obj is None:
            attrs = {
                f"{POINT_PREFIX}{n}": forms.ChoiceField(
                    label=point,
                    choices=models.CheckResult.choices,
                    widget=forms.RadioSelect,
                )
                for n, point in enumerate(self._checklist(request), 1)
            }
            kwargs["form"] = type("InspectionEntryForm", (InspectionForm,), attrs)
        return super().get_form(request, obj, change=change, **kwargs)

    def save_model(
        self, request: HttpRequest, obj: models.Inspection, form: Any, change: bool
    ) -> None:
        """Record through the services, then load the created record."""
        data = form.cleaned_data
        view = inspection_services.record_inspection(
            f"id:{data['item'].pk}",
            inspector_id=data["inspector"].pk,
            decision=data["decision"],
            results=form.results(),
            actor=actor_for(request),
            inspected_on=data["inspected_on"],
            next_due_on=data.get("next_due_on"),
            comment=data.get("comment", ""),
            report=data.get("report") or None,
            report_received_on=data.get("report_received_on"),
            report_archive_location=data.get("report_archive_location", ""),
        )
        obj.pk = view.id
        obj.refresh_from_db()

    def response_post_save_add(self, request: HttpRequest, obj: models.Inspection) -> Any:
        """Go back to the item's page."""
        return HttpResponseRedirect(reverse("admin:equipment_item_change", args=[obj.item_id]))

    @admin.display(description=_("inspector at the time"))
    def inspector_at_the_time(self, obj: models.Inspection) -> str:
        """Inspector's identity and authorization frozen in the record."""
        snap = obj.inspector_snapshot
        parts = [str(snap.get("name", obj.inspector))]
        if snap.get("authorization_reference"):
            parts.append(str(snap["authorization_reference"]))
        if snap.get("authorization_valid_until"):
            parts.append(
                gettext("valid until %(date)s") % {"date": snap["authorization_valid_until"]}
            )
        return " - ".join(parts)

    @admin.display(description=_("checklist results"))
    def checklist(self, obj: models.Inspection) -> str:
        """Frozen checklist with results."""
        if not obj.checklist_results:
            return "-"
        return format_html_join(
            "",
            "<div>[{}] {}</div>",
            ((models.CheckResult(r["result"]).label, r["point"]) for r in obj.checklist_results),
        )


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


@admin.register(models.DigestLog)
class DigestLogAdmin(ReadOnlyAdmin):
    """E-mail digests sent by ``gwiriek notify``."""

    list_display = ("sent_at", "subject", "recipients", "item_count", "forced")
    date_hierarchy = "sent_at"
