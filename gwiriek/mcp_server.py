"""MCP server exposing Gwiriek's services as tools, built on the official ``mcp`` SDK (v2).

Started by ``gwiriek mcp`` on the stdio transport. Each tool is a thin wrapper around a
service; business errors come back as tool errors (``is_error=True``) the model can read.
There is deliberately no deletion tool.

The stdio transport gives full access to the database to whoever launches the process.
"""

import functools
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

from django.db import close_old_connections
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from gwiriek.equipment.services import (
    assignments,
    catalog,
    due,
    inspections,
    inspectors,
    items,
    people,
)
from gwiriek.equipment.services.errors import ServiceError
from gwiriek.equipment.services.views import (
    AssignmentView,
    DueReport,
    InspectionView,
    InspectorView,
    ItemDetail,
    ItemSummary,
    ManufacturerView,
    PersonView,
    ProductModelDetail,
    ProductModelSummary,
)

MCP_ACTOR = "mcp"

INSTRUCTIONS = """\
Gwiriek manages the personal protective equipment (PPE) of a rope access company: a
catalogue of product models, items identified by serial or lot number, their status,
assignments and computed end of life.

- An item reference is an id, an internal label or a serial number; prefix it with
  "id:", "label:" or "serial:" when a lookup is ambiguous.
- End-of-life dates are indicative and must be confirmed against the manufacturer's
  instructions. Say so when you report them.
- Statuses: in_stock, in_service, watch, quarantine, retired.
- Periodic inspections (at least every 12 months, stock included) are recorded with
  record_inspection by a qualified inspector (list_inspectors) whose written authorization
  covers the item's family on the inspection day. Every checklist point of the product
  model needs a result (ok, nok, na); a "nok" point only allows quarantine or retire.
  Recorded inspections cannot be modified: correct one by recording a new inspection.
- Write tools record the actor "mcp" in the audit log.
- This server cannot delete anything.
"""

Status = Literal["in_stock", "in_service", "watch", "quarantine", "retired"]
InitialStatus = Literal["in_stock", "in_service", "watch", "quarantine"]
Decision = Literal["valid", "watch", "quarantine", "retire"]
CheckResultName = Literal["ok", "nok", "na"]
AssignmentTarget = Literal["person", "site", "stock"]
FamilyName = Literal[
    "harness",
    "lanyard",
    "connector",
    "descender",
    "ascender",
    "backup",
    "rope",
    "helmet",
    "absorber",
    "other",
]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CREATES = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
#: Updates that can be irreversible (retiring an item is final).
UPDATES = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def service_tool[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Run a tool body with fresh database connections and readable business errors."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        close_old_connections()
        try:
            return func(*args, **kwargs)
        except ServiceError as exc:
            detail = f" Details: {exc.details}" if exc.details else ""
            raise ToolError(f"[{exc.code}] {exc.message}{detail}") from exc
        finally:
            close_old_connections()

    return wrapper


def build_server() -> MCPServer:
    """Create the MCP server. Django must already be set up."""
    server = MCPServer(
        "gwiriek",
        title="Gwiriek",
        description="PPE inventory, life records, periodic inspections and deadlines.",
        instructions=INSTRUCTIONS,
        version=_version(),
        log_level="WARNING",
    )

    @server.tool(title="Search items", annotations=READ_ONLY)
    @service_tool
    def search_items(
        query: Annotated[
            str | None,
            Field(description="Text matched in label, serial, lot, model or manufacturer."),
        ] = None,
        status: Annotated[
            list[Status] | None, Field(description="Keep only these statuses.")
        ] = None,
        family: Annotated[FamilyName | None, Field(description="Keep only this family.")] = None,
        person_id: Annotated[
            int | None, Field(description="Keep items currently assigned to this person.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum results.")] = 50,
    ) -> list[ItemSummary]:
        """Search the PPE inventory. Returns short item records with their end of life."""
        return items.list_items(
            statuses=status or (), family=family, query=query, person_id=person_id, limit=limit
        )

    @server.tool(title="Get an item's life record", annotations=READ_ONLY)
    @service_tool
    def get_item(
        ref: Annotated[str, Field(description="Item id, internal label or serial number.")],
    ) -> ItemDetail:
        """Return an item's full life record: dates, end of life, assignments, status log."""
        return items.get_item(ref)

    @server.tool(title="List items due", annotations=READ_ONLY)
    @service_tool
    def list_due_items(
        within_days: Annotated[
            int, Field(ge=0, le=3650, description="Horizon in days from today.")
        ] = 90,
    ) -> DueReport:
        """List items (not retired) whose end of life or periodic inspection is passed or near.

        Each entry gives both deadlines and the nearest one. Also lists items whose end of
        life cannot be determined and needs checking by hand.
        """
        return due.due_items(within_days=within_days)

    @server.tool(title="List manufacturers", annotations=READ_ONLY)
    @service_tool
    def list_manufacturers() -> list[ManufacturerView]:
        """List the manufacturers in the catalogue."""
        return catalog.list_manufacturers()

    @server.tool(title="Search the catalogue", annotations=READ_ONLY)
    @service_tool
    def search_product_models(
        query: Annotated[
            str | None, Field(description="Text in model name, reference or manufacturer.")
        ] = None,
        family: Annotated[FamilyName | None, Field(description="Keep only this family.")] = None,
        manufacturer: Annotated[
            str | None, Field(description="Manufacturer id or exact name.")
        ] = None,
    ) -> list[ProductModelSummary]:
        """Search product models (catalogue entries) with their lifespan durations in months."""
        return catalog.list_product_models(query=query, family=family, manufacturer=manufacturer)

    @server.tool(title="Get a product model", annotations=READ_ONLY)
    @service_tool
    def get_product_model(
        product_model_id: Annotated[int, Field(description="Product model id.")],
    ) -> ProductModelDetail:
        """Return a catalogue entry: lifespan rules and source, notice URLs, checklist."""
        return catalog.get_product_model(product_model_id)

    @server.tool(title="List people", annotations=READ_ONLY)
    @service_tool
    def list_people(
        include_inactive: Annotated[bool, Field(description="Include inactive people.")] = False,
    ) -> list[PersonView]:
        """List the people equipment can be assigned to."""
        return people.list_people(active_only=not include_inactive)

    @server.tool(title="Register an item", annotations=CREATES)
    @service_tool
    def create_item(
        product_model_id: Annotated[int, Field(description="Catalogue entry of the item.")],
        serial_number: Annotated[str, Field(description="Serial number.")] = "",
        lot_number: Annotated[str, Field(description="Lot number.")] = "",
        label: Annotated[str, Field(description="Internal company label, unique.")] = "",
        manufactured_on: Annotated[
            date | None,
            Field(description="Date of manufacture; first day of the month if only it is known."),
        ] = None,
        purchased_on: Annotated[date | None, Field(description="Date of purchase.")] = None,
        in_service_on: Annotated[
            date | None, Field(description="Date first put into service.")
        ] = None,
        status: Annotated[InitialStatus, Field(description="Initial status.")] = "in_stock",
        rope_diameter_mm: Annotated[
            float | None, Field(gt=0, description="Ropes only: diameter in mm.")
        ] = None,
        rope_length_m: Annotated[
            float | None, Field(gt=0, description="Ropes only: length in m.")
        ] = None,
        rope_type: Annotated[
            Literal["A", "B"] | None, Field(description="Ropes only: EN 1891 type.")
        ] = None,
        rope_terminations: Annotated[str, Field(description="Ropes only: terminations.")] = "",
        notes: Annotated[str, Field(description="Free notes.")] = "",
    ) -> ItemDetail:
        """Register a new item. A serial number or a lot number is required.

        The creation is recorded in the audit log with the actor "mcp".
        """
        return items.create_item(
            product_model_id=product_model_id,
            actor=MCP_ACTOR,
            serial_number=serial_number,
            lot_number=lot_number,
            label=label,
            manufactured_on=manufactured_on,
            purchased_on=purchased_on,
            in_service_on=in_service_on,
            status=status,
            rope_diameter_mm=rope_diameter_mm,
            rope_length_m=rope_length_m,
            rope_type=rope_type or "",
            rope_terminations=rope_terminations,
            notes=notes,
        )

    @server.tool(title="List inspectors", annotations=READ_ONLY)
    @service_tool
    def list_inspectors(
        include_inactive: Annotated[
            bool, Field(description="Include inactive inspectors.")
        ] = False,
    ) -> list[InspectorView]:
        """List qualified inspectors (R4323-100) with their authorization and scope.

        ``qualified`` tells whether the inspector may record an inspection today;
        ``issues`` says why not.
        """
        return inspectors.list_inspectors(active_only=not include_inactive)

    @server.tool(title="Get an item's inspection history", annotations=READ_ONLY)
    @service_tool
    def get_inspection_history(
        ref: Annotated[str, Field(description="Item id, internal label or serial number.")],
    ) -> list[InspectionView]:
        """Return an item's periodic inspections, latest first, with frozen checklists."""
        return inspections.list_inspections(item_ref=ref)

    @server.tool(title="List inspections due", annotations=READ_ONLY)
    @service_tool
    def list_inspections_due(
        within_days: Annotated[
            int, Field(ge=0, le=3650, description="Horizon in days from today.")
        ] = 30,
    ) -> DueReport:
        """List items, in service or in stock, whose periodic inspection is overdue or near.

        An item never inspected counts from its date put into service or purchase,
        whichever is earlier; without either it is overdue.
        """
        return due.due_items(within_days=within_days, inspections_only=True)

    @server.tool(title="Record an inspection", annotations=CREATES)
    @service_tool
    def record_inspection(
        ref: Annotated[str, Field(description="Item id, internal label or serial number.")],
        inspector_id: Annotated[int, Field(description="Inspector id (see list_inspectors).")],
        decision: Annotated[
            Decision,
            Field(description="valid (fit for use), watch (fit, under watch), quarantine, retire."),
        ],
        points: Annotated[
            dict[str, CheckResultName],
            Field(
                description=(
                    "Result of every checklist point of the product model, keyed by the "
                    "point's text or its 1-based number (see get_product_model)."
                )
            ),
        ],
        inspected_on: Annotated[
            date | None, Field(description="Inspection date, defaults to today.")
        ] = None,
        next_due_on: Annotated[
            date | None,
            Field(description="Earlier next inspection date than the model's interval."),
        ] = None,
        comment: Annotated[
            str, Field(description="Observations; required unless the decision is valid.")
        ] = "",
        report_received_on: Annotated[
            date | None,
            Field(description="External inspector: date the report was received."),
        ] = None,
        report_archive_location: Annotated[
            str, Field(description="External inspector: where the report is archived.")
        ] = "",
    ) -> InspectionView:
        """Record a periodic inspection and apply its decision to the item's status.

        The record cannot be modified afterwards. A report file can only be attached from
        the admin; for an external inspector, give the report's date of receipt and archive
        location instead.
        """
        return inspections.record_inspection(
            ref,
            inspector_id=inspector_id,
            decision=decision,
            results=points,
            actor=MCP_ACTOR,
            inspected_on=inspected_on,
            next_due_on=next_due_on,
            comment=comment,
            report_received_on=report_received_on,
            report_archive_location=report_archive_location,
        )

    @server.tool(title="Change an item's status", annotations=UPDATES)
    @service_tool
    def change_item_status(
        ref: Annotated[str, Field(description="Item id, internal label or serial number.")],
        status: Annotated[Status, Field(description="New status; retired is final.")],
        reason: Annotated[
            str, Field(description="Reason, recorded in the audit log.", min_length=1)
        ],
    ) -> ItemDetail:
        """Change an item's status, recorded with its reason in the audit log.

        An item past its end of life cannot be put into use; a retired item cannot change.
        """
        return items.change_status(ref, status, actor=MCP_ACTOR, reason=reason)

    @server.tool(title="Assign an item", annotations=CREATES)
    @service_tool
    def assign_item(
        ref: Annotated[str, Field(description="Item id, internal label or serial number.")],
        target: Annotated[
            AssignmentTarget, Field(description="Assign to a person, site or stock.")
        ],
        person_id: Annotated[
            int | None, Field(description="Person id, for a person assignment.")
        ] = None,
        location: Annotated[
            str, Field(description="Site name (required for a site), or storage place.")
        ] = "",
        started_on: Annotated[
            date | None, Field(description="Start date, defaults to today.")
        ] = None,
        notes: Annotated[str, Field(description="Free notes.")] = "",
    ) -> AssignmentView:
        """Assign an item to a person, a site or the stock, closing its current assignment.

        Issuing an item in stock puts it into service. Quarantined, retired or expired items
        cannot be issued.
        """
        return assignments.assign_item(
            ref,
            target,
            actor=MCP_ACTOR,
            person_id=person_id,
            location=location,
            started_on=started_on,
            notes=notes,
        )

    return server


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gwiriek")
    except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
        return "0"
