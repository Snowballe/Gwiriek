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

from gwiriek.equipment.services import catalog, due, items, people
from gwiriek.equipment.services.errors import ServiceError
from gwiriek.equipment.services.views import (
    DueReport,
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
- This server cannot delete anything.
"""

Status = Literal["in_stock", "in_service", "watch", "quarantine", "retired"]
InitialStatus = Literal["in_stock", "in_service", "watch", "quarantine"]
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
        description="PPE inventory, life records and end-of-life deadlines.",
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
        """List items (not retired) whose end of life is passed or within the horizon.

        Also lists items whose end of life cannot be determined and needs checking by hand.
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

    return server


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gwiriek")
    except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
        return "0"
