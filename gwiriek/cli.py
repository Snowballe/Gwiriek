"""The ``gwiriek`` command: drive Gwiriek from a shell or a script.

Every subcommand calls :mod:`gwiriek.equipment.services`; this module only parses
arguments and formats output. ``--json`` prints machine-readable output on stdout.

Exit codes: 0 success, 1 business error (not found, invalid data, forbidden operation),
2 usage error, 3 ``due --check`` found items to act on.
"""

import argparse
import getpass
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DUE = 3

Handler = Callable[[argparse.Namespace], int]


def _setup_django() -> None:
    """Configure Django and activate the configured language."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gwiriek.settings")
    import django
    from django.conf import settings
    from django.utils import translation

    django.setup()
    translation.activate(settings.LANGUAGE_CODE)


# --- Argument types -------------------------------------------------------------------------


def _iso_date(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` date."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def _decimal(value: str) -> Decimal:
    """Parse a decimal number, accepting a comma as separator."""
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid number {value!r}") from exc


def _non_negative(value: str) -> int:
    """Parse a non-negative integer."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or more")
    return number


# --- Output ---------------------------------------------------------------------------------


def _print_json(value: Any) -> None:
    from gwiriek.equipment.services.views import to_jsonable

    print(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2))


def _table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    """Render rows as an aligned plain-text table."""
    cells = [[str(h) for h in headers], *[["" if c is None else str(c) for c in r] for r in rows]]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    lines = [
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in cells
    ]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _label(choices: Any, value: str) -> str:
    """Translated label of a choice value, or the value itself."""
    try:
        return str(choices(value).label)
    except ValueError:
        return value


def _fmt_date(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "-"


def _eol_text(end: date | None, basis: str) -> str:
    from django.utils.translation import gettext as _

    labels = {
        "partial": _("at most"),
        "pending": _("not started"),
        "unknown": _("unknown"),
        "unlimited": _("unlimited"),
        "undefined": _("no data"),
    }
    if end is None:
        return labels.get(basis, basis)
    suffix = f" ({labels['partial']})" if basis == "partial" else ""
    return _fmt_date(end) + suffix


def _item_ident(item: Any) -> str:
    return str(item.label or item.serial_number or item.lot_number)


def _print_items(items: Sequence[Any]) -> None:
    from django.utils.translation import gettext as _

    from gwiriek.equipment.models import Family, ItemStatus

    rows = [
        (
            i.id,
            _item_ident(i),
            f"{i.manufacturer} {i.product_model}",
            _label(Family, i.family),
            _label(ItemStatus, i.status),
            _eol_text(i.end_of_life, i.end_of_life_basis),
            i.assigned_to or "-",
        )
        for i in items
    ]
    headers = [
        _("ID"),
        _("Item"),
        _("Model"),
        _("Family"),
        _("Status"),
        _("End of life"),
        _("Assigned to"),
    ]
    print(_table(rows, headers))
    print(_("%(count)d item(s).") % {"count": len(items)})


def _print_item_detail(item: Any) -> None:
    from django.utils import timezone
    from django.utils.translation import gettext as _

    from gwiriek.equipment.models import AssignmentKind, Family, ItemStatus, RopeType

    eol = item.end_of_life
    fields: list[tuple[str, Any]] = [
        (_("ID"), item.id),
        (_("Model"), f"{item.manufacturer} {item.product_model} (#{item.product_model_id})"),
        (_("Family"), _label(Family, item.family)),
        (_("Internal label"), item.label or "-"),
        (_("Serial number"), item.serial_number or "-"),
        (_("Lot number"), item.lot_number or "-"),
        (_("Status"), _label(ItemStatus, item.status)),
        (_("Date of manufacture"), _fmt_date(item.manufactured_on)),
        (_("Date of purchase"), _fmt_date(item.purchased_on)),
        (_("Date put into service"), _fmt_date(item.in_service_on)),
    ]
    if item.retired_on:
        fields.append((_("Date retired"), _fmt_date(item.retired_on)))
    if item.family == Family.ROPE:
        fields += [
            (_("Rope diameter (mm)"), item.rope_diameter_mm or "-"),
            (_("Rope length (m)"), item.rope_length_m or "-"),
            (_("Rope type"), _label(RopeType, item.rope_type) if item.rope_type else "-"),
            (_("Rope terminations"), item.rope_terminations or "-"),
        ]
    eol_line = _eol_text(eol.end_date, eol.basis)
    if eol.days_remaining is not None:
        eol_line += " " + (
            _("(passed)") if eol.expired else _("(in %(days)d days)") % {"days": eol.days_remaining}
        )
    fields.append((_("End of life"), eol_line))
    if eol.missing:
        fields.append((_("Missing data"), ", ".join(eol.missing)))
    if eol.storage_exceeded:
        fields.append(("", _("Put into service after the maximum storage time.")))
    if item.current_assignment:
        a = item.current_assignment
        target = a.person if a.kind == AssignmentKind.PERSON else a.location
        fields.append(
            (
                _("Assigned to"),
                f"{_label(AssignmentKind, a.kind)} {target or ''} "
                f"({_('since')} {_fmt_date(a.started_on)})",
            )
        )
    if item.notes:
        fields.append((_("Notes"), item.notes))
    width = max(len(str(k)) for k, _v in fields)
    for key, value in fields:
        print(f"{str(key).ljust(width)}  {value}")
    print(f"\n{eol.disclaimer}")
    if item.status_history:
        print(f"\n{_('Status history')}")
        rows = [
            (
                timezone.localtime(c.changed_at).strftime("%d/%m/%Y %H:%M"),
                _label(ItemStatus, c.from_status) if c.from_status else "-",
                _label(ItemStatus, c.to_status),
                c.actor,
                c.reason,
            )
            for c in item.status_history
        ]
        print(_table(rows, [_("Date"), _("From"), _("To"), _("By"), _("Reason")]))
    if item.assignments:
        print(f"\n{_('Assignments')}")
        rows = [
            (
                _label(AssignmentKind, a.kind),
                a.person or a.location or "-",
                _fmt_date(a.started_on),
                _fmt_date(a.ended_on),
                a.actor,
            )
            for a in item.assignments
        ]
        print(_table(rows, [_("Target"), _("Who / where"), _("From"), _("Until"), _("By")]))


def _emit(args: argparse.Namespace, value: Any, human: Callable[[Any], None]) -> int:
    if args.json:
        _print_json(value)
    else:
        human(value)
    return EXIT_OK


# --- Handlers -------------------------------------------------------------------------------


def _items_list(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import items

    result = items.list_items(
        statuses=args.status or (),
        family=args.family,
        query=args.search,
        person_id=args.person,
        limit=args.limit,
    )
    return _emit(args, result, _print_items)


def _items_show(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import items

    return _emit(args, items.get_item(args.ref), _print_item_detail)


def _items_add(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import items

    detail = items.create_item(
        product_model_id=args.model,
        actor=args.actor,
        serial_number=args.serial or "",
        lot_number=args.lot or "",
        label=args.label or "",
        manufactured_on=args.manufactured,
        purchased_on=args.purchased,
        in_service_on=args.in_service,
        status=args.status,
        rope_diameter_mm=args.diameter,
        rope_length_m=args.length,
        rope_type=args.rope_type or "",
        rope_terminations=args.terminations or "",
        notes=args.notes or "",
    )
    return _emit(args, detail, _print_item_detail)


def _items_status(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import items

    detail = items.change_status(
        args.ref, args.new_status, actor=args.actor, reason=args.reason or "", on=args.date
    )
    return _emit(args, detail, _print_item_detail)


def _items_assign(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import assignments, items

    if args.person is not None:
        kind, location = "person", ""
    elif args.site is not None:
        kind, location = "site", args.site
    else:
        kind, location = "stock", args.stock or ""
    assignments.assign_item(
        args.ref,
        kind,
        actor=args.actor,
        person_id=args.person,
        location=location,
        started_on=args.date,
        notes=args.notes or "",
    )
    return _emit(args, items.get_item(args.ref), _print_item_detail)


def _print_due(report: Any) -> None:
    from django.utils.translation import gettext as _

    from gwiriek.equipment.models import ItemStatus

    def rows(entries: Sequence[Any]) -> list[tuple[Any, ...]]:
        return [
            (
                e.item.id,
                _item_ident(e.item),
                f"{e.item.manufacturer} {e.item.product_model}",
                _label(ItemStatus, e.item.status),
                _eol_text(e.end_of_life, e.basis),
                "-" if e.days_remaining is None else e.days_remaining,
                ", ".join(e.missing) or "-",
            )
            for e in entries
        ]

    headers = [
        _("ID"),
        _("Item"),
        _("Model"),
        _("Status"),
        _("End of life"),
        _("Days"),
        _("Missing data"),
    ]
    sections = [
        (_("End of life passed"), report.overdue),
        (_("End of life within %(days)d days") % {"days": report.within_days}, report.upcoming),
        (_("End of life to determine by hand"), report.undetermined),
    ]
    print(_("As of %(date)s") % {"date": _fmt_date(report.as_of)})
    for title, entries in sections:
        print(f"\n{title}: {len(entries)}")
        if entries:
            print(_table(rows(entries), headers))
    print(f"\n{report.disclaimer}")


def _due(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import due

    report = due.due_items(within_days=args.within)
    _emit(args, report, _print_due)
    if args.check and (report.overdue or report.upcoming):
        return EXIT_DUE
    return EXIT_OK


def _print_manufacturers(manufacturers: Sequence[Any]) -> None:
    from django.utils.translation import gettext as _

    print(
        _table(
            [(m.id, m.name, m.website) for m in manufacturers], [_("ID"), _("Name"), _("Website")]
        )
    )


def _catalog_manufacturers(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import catalog

    return _emit(args, catalog.list_manufacturers(), _print_manufacturers)


def _catalog_add_manufacturer(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import catalog

    result = catalog.create_manufacturer(
        args.name, website=args.website or "", contact=args.contact or ""
    )
    return _emit(args, result, lambda m: _print_manufacturers([m]))


def _months(value: int | None) -> str:
    return "-" if value is None else str(value)


def _print_models(models_: Sequence[Any]) -> None:
    from django.utils.translation import gettext as _

    from gwiriek.equipment.models import Family

    rows = [
        (
            m.id,
            m.manufacturer,
            m.name,
            m.reference or "-",
            _label(Family, m.family),
            ", ".join(m.standards) or "-",
            _("unlimited")
            if m.lifetime_unlimited
            else " / ".join(
                _months(v)
                for v in (m.max_lifetime_months, m.max_storage_months, m.max_service_months)
            ),
        )
        for m in models_
    ]
    headers = [
        _("ID"),
        _("Manufacturer"),
        _("Model"),
        _("Reference"),
        _("Family"),
        _("Standards"),
        _("Lifetime / storage / service (months)"),
    ]
    print(_table(rows, headers))


def _print_model_detail(m: Any) -> None:
    from django.utils.translation import gettext as _

    _print_models([m])
    print()
    print(f"{_('Lifetime source')}: {m.lifetime_source or '-'}")
    print(f"{_('Inspection interval (months)')}: {m.inspection_interval_months}")
    print(f"{_('Instructions for use')}: {m.notice_url or '-'}")
    print(f"{_('Inspection procedure')}: {m.inspection_procedure_url or '-'}")
    if m.checklist:
        print(_("Checklist:"))
        for number, point in enumerate(m.checklist, 1):
            print(f"  {number}. {point}")


def _catalog_models(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import catalog

    result = catalog.list_product_models(
        query=args.search, family=args.family, manufacturer=args.manufacturer
    )
    return _emit(args, result, _print_models)


def _catalog_show(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import catalog

    return _emit(args, catalog.get_product_model(args.id), _print_model_detail)


def _catalog_add_model(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import catalog

    result = catalog.create_product_model(
        manufacturer=args.manufacturer,
        name=args.name,
        family=args.family,
        reference=args.reference or "",
        standards=args.standard or (),
        lifetime_unlimited=args.unlimited,
        max_lifetime_months=args.lifetime_months,
        max_storage_months=args.storage_months,
        max_service_months=args.service_months,
        lifetime_source=args.source or "",
        inspection_interval_months=args.interval,
        notice_url=args.notice_url or "",
        inspection_procedure_url=args.procedure_url or "",
        checklist=args.check or (),
    )
    return _emit(args, result, _print_model_detail)


def _print_people(people: Sequence[Any]) -> None:
    from django.utils.translation import gettext as _

    rows = [
        (p.id, p.last_name, p.first_name, p.email or "-", _("yes") if p.active else _("no"))
        for p in people
    ]
    print(_table(rows, [_("ID"), _("Last name"), _("First name"), _("Email"), _("Active")]))


def _people_list(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import people

    return _emit(args, people.list_people(active_only=not args.all), _print_people)


def _people_add(args: argparse.Namespace) -> int:
    from gwiriek.equipment.services import people

    result = people.create_person(args.first_name, args.last_name, email=args.email or "")
    return _emit(args, result, lambda p: _print_people([p]))


def _mcp(args: argparse.Namespace) -> int:
    from gwiriek.mcp_server import build_server

    build_server().run()
    return EXIT_OK


# --- Parser ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (Django must be set up for translated help)."""
    from django.utils.translation import gettext as _

    from gwiriek.equipment.models import Family, ItemStatus, RopeType

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help=_("print JSON on stdout"))
    writer = argparse.ArgumentParser(add_help=False)
    writer.add_argument(
        "--actor",
        default=f"cli:{_current_user()}",
        help=_("name recorded in the audit log (default: cli:<user>)"),
    )

    parser = argparse.ArgumentParser(
        prog="gwiriek",
        description=_("Gwiriek: PPE management for rope access companies."),
        epilog=_(
            "Exit codes: 0 success, 1 business error, 2 usage error, 3 items due (due --check)."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # articles
    articles = commands.add_parser("articles", aliases=["items"], help=_("items (PPE)"))
    art = articles.add_subparsers(dest="action", required=True, metavar="ACTION")

    p = art.add_parser("list", parents=[common], help=_("list items"))
    p.add_argument(
        "--status",
        action="append",
        choices=ItemStatus.values,
        help=_("keep this status (repeatable)"),
    )
    p.add_argument("--family", choices=Family.values, help=_("keep this family"))
    p.add_argument("--search", help=_("text in label, serial, lot, model or manufacturer"))
    p.add_argument("--person", type=int, help=_("items currently assigned to this person id"))
    p.add_argument("--limit", type=_non_negative, help=_("maximum number of items"))
    p.set_defaults(handler=_items_list)

    p = art.add_parser("show", parents=[common], help=_("show an item's life record"))
    p.add_argument(
        "ref",
        help=_("id, internal label or serial number (prefix id:, label: or serial: if ambiguous)"),
    )
    p.set_defaults(handler=_items_show)

    p = art.add_parser("add", parents=[common, writer], help=_("register an item"))
    p.add_argument("--model", type=int, required=True, help=_("product model id"))
    p.add_argument("--serial", help=_("serial number"))
    p.add_argument("--lot", help=_("lot number"))
    p.add_argument("--label", help=_("internal label"))
    p.add_argument("--manufactured", type=_iso_date, help=_("date of manufacture (YYYY-MM-DD)"))
    p.add_argument("--purchased", type=_iso_date, help=_("date of purchase (YYYY-MM-DD)"))
    p.add_argument("--in-service", type=_iso_date, help=_("date put into service (YYYY-MM-DD)"))
    p.add_argument(
        "--status",
        default=ItemStatus.IN_STOCK.value,
        choices=[s for s in ItemStatus.values if s != ItemStatus.RETIRED],
        help=_("initial status (default: in_stock)"),
    )
    p.add_argument("--diameter", type=_decimal, help=_("rope diameter in mm"))
    p.add_argument("--length", type=_decimal, help=_("rope length in m"))
    p.add_argument("--rope-type", choices=RopeType.values, help=_("EN 1891 rope type"))
    p.add_argument("--terminations", help=_("rope terminations"))
    p.add_argument("--notes", help=_("notes"))
    p.set_defaults(handler=_items_add)

    p = art.add_parser("status", parents=[common, writer], help=_("change an item's status"))
    p.add_argument("ref", help=_("id, internal label or serial number"))
    p.add_argument("new_status", choices=ItemStatus.values, help=_("new status"))
    p.add_argument("--reason", help=_("reason (required for quarantine and retired)"))
    p.add_argument("--date", type=_iso_date, help=_("date of the change (default: today)"))
    p.set_defaults(handler=_items_status)

    p = art.add_parser(
        "assign",
        parents=[common, writer],
        help=_("assign an item to a person, a site or the stock"),
    )
    p.add_argument("ref", help=_("id, internal label or serial number"))
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--person", type=int, help=_("person id"))
    target.add_argument("--site", help=_("site name"))
    target.add_argument("--stock", nargs="?", const="", help=_("return to stock (optional place)"))
    p.add_argument("--date", type=_iso_date, help=_("start date (default: today)"))
    p.add_argument("--notes", help=_("notes"))
    p.set_defaults(handler=_items_assign)

    # due
    p = commands.add_parser(
        "due", parents=[common], help=_("items whose end of life is passed or near")
    )
    p.add_argument(
        "--within", type=_non_negative, default=90, help=_("horizon in days (default: 90)")
    )
    p.add_argument(
        "--check", action="store_true", help=_("exit with code 3 if any item is passed or due")
    )
    p.set_defaults(handler=_due)

    # catalog
    catalog = commands.add_parser("catalog", help=_("product catalogue"))
    cat = catalog.add_subparsers(dest="action", required=True, metavar="ACTION")

    p = cat.add_parser("manufacturers", parents=[common], help=_("list manufacturers"))
    p.set_defaults(handler=_catalog_manufacturers)

    p = cat.add_parser("add-manufacturer", parents=[common], help=_("add a manufacturer"))
    p.add_argument("name", help=_("name"))
    p.add_argument("--website", help=_("website URL"))
    p.add_argument("--contact", help=_("contact details"))
    p.set_defaults(handler=_catalog_add_manufacturer)

    p = cat.add_parser("models", parents=[common], help=_("list product models"))
    p.add_argument("--search", help=_("text in name, reference or manufacturer"))
    p.add_argument("--family", choices=Family.values, help=_("keep this family"))
    p.add_argument("--manufacturer", help=_("manufacturer id or name"))
    p.set_defaults(handler=_catalog_models)

    p = cat.add_parser("show", parents=[common], help=_("show a product model"))
    p.add_argument("id", type=int, help=_("product model id"))
    p.set_defaults(handler=_catalog_show)

    p = cat.add_parser("add-model", parents=[common], help=_("add a product model"))
    p.add_argument("--manufacturer", required=True, help=_("manufacturer id or name"))
    p.add_argument("--name", required=True, help=_("model name"))
    p.add_argument("--family", required=True, choices=Family.values, help=_("family"))
    p.add_argument("--reference", help=_("manufacturer reference"))
    p.add_argument("--standard", action="append", help=_("standard, e.g. 'EN 361' (repeatable)"))
    p.add_argument("--unlimited", action="store_true", help=_("no lifetime limit"))
    p.add_argument("--lifetime-months", type=int, help=_("maximum lifetime from manufacture"))
    p.add_argument("--storage-months", type=int, help=_("maximum storage before first use"))
    p.add_argument("--service-months", type=int, help=_("maximum service life"))
    p.add_argument("--source", help=_("where the durations come from"))
    p.add_argument(
        "--interval", type=int, default=12, help=_("inspection interval in months (default: 12)")
    )
    p.add_argument("--notice-url", help=_("instructions for use URL"))
    p.add_argument("--procedure-url", help=_("inspection procedure URL"))
    p.add_argument("--check", action="append", help=_("checklist point (repeatable, in order)"))
    p.set_defaults(handler=_catalog_add_model)

    # people
    people = commands.add_parser("people", help=_("people"))
    ppl = people.add_subparsers(dest="action", required=True, metavar="ACTION")
    p = ppl.add_parser("list", parents=[common], help=_("list people"))
    p.add_argument("--all", action="store_true", help=_("include inactive people"))
    p.set_defaults(handler=_people_list)
    p = ppl.add_parser("add", parents=[common], help=_("add a person"))
    p.add_argument("first_name", help=_("first name"))
    p.add_argument("last_name", help=_("last name"))
    p.add_argument("--email", help=_("email"))
    p.set_defaults(handler=_people_add)

    # mcp
    p = commands.add_parser("mcp", help=_("run the MCP server on stdio"))
    p.set_defaults(handler=_mcp, json=False)

    # manage
    # Listed for --help only: main() hands "manage ..." to Django before parsing.
    commands.add_parser("manage", help=_("run a Django management command (e.g. migrate)"))
    return parser


def _manage(args: argparse.Namespace) -> int:
    from django.core.management import execute_from_command_line

    execute_from_command_line(["gwiriek manage", *args.args])
    return EXIT_OK


def _current_user() -> str:
    try:
        return getpass.getuser()
    except OSError:  # pragma: no cover - no user name in the environment
        return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``gwiriek`` command and return its exit code."""
    from gwiriek.equipment.services.errors import ServiceError

    _setup_django()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["manage"]:
        # Hand everything over to Django, including --help.
        return _manage(argparse.Namespace(args=argv[1:]))
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code: int = args.handler(args)
    except ServiceError as exc:
        if args.json:
            print(
                json.dumps({"error": exc.as_dict()}, ensure_ascii=False, default=str),
                file=sys.stderr,
            )
        else:
            print(f"gwiriek: {exc.message}", file=sys.stderr)
        return EXIT_ERROR
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
