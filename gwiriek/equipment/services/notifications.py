"""E-mail digest of due items, meant to run from cron or a systemd timer.

The digest goes to the addresses in ``GWIRIEK_ALERT_RECIPIENTS`` through Django's e-mail
backend. The same digest (same day, same content) is sent only once unless forced.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils.translation import gettext as _

from gwiriek.equipment.models import DigestLog, ItemStatus
from gwiriek.equipment.services._common import today as current_date
from gwiriek.equipment.services.due import due_items
from gwiriek.equipment.services.errors import InvalidDataError
from gwiriek.equipment.services.views import DigestResult, DueItem, DueReport, to_jsonable

SENT = "sent"
ALREADY_SENT = "already_sent"
NOTHING_DUE = "nothing_due"


def configured_recipients() -> list[str]:
    """Return the digest recipients configured in the settings."""
    return list(getattr(settings, "GWIRIEK_ALERT_RECIPIENTS", []))


def _fingerprint(report: DueReport) -> str:
    """Hash the content of a report, so the same digest is recognised."""
    data = to_jsonable(report)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _line(entry: DueItem) -> str:
    item = entry.item
    ident = item.label or item.serial_number or item.lot_number
    parts = [f"#{item.id} {ident} - {item.manufacturer} {item.product_model}"]
    parts.append(str(ItemStatus(item.status).label))
    if entry.end_of_life:
        parts.append(_("end of life %(date)s") % {"date": entry.end_of_life.strftime("%d/%m/%Y")})
    if entry.inspection_due_on:
        parts.append(
            _("inspection due %(date)s") % {"date": entry.inspection_due_on.strftime("%d/%m/%Y")}
        )
    elif "inspection_overdue" in entry.reasons:
        parts.append(_("never inspected, no date of entry"))
    if entry.item.assigned_to:
        parts.append(entry.item.assigned_to)
    return "- " + ", ".join(parts)


def render_digest(report: DueReport) -> tuple[str, str]:
    """Render a due report as an e-mail subject and plain-text body.

    Args:
        report: The report.

    Returns:
        The subject and the body.
    """
    subject = _("Gwiriek: %(overdue)d overdue, %(upcoming)d due within %(days)d days") % {
        "overdue": len(report.overdue),
        "upcoming": len(report.upcoming),
        "days": report.within_days,
    }
    lines = [_("PPE deadlines as of %(date)s.") % {"date": report.as_of.strftime("%d/%m/%Y")}]
    sections = [
        (_("Overdue: do not use before action"), report.overdue),
        (_("Due within %(days)d days") % {"days": report.within_days}, report.upcoming),
        (_("End of life to determine by hand"), report.undetermined),
    ]
    for title, entries in sections:
        if entries:
            lines += ["", f"{title} ({len(entries)})", *(_line(e) for e in entries)]
    lines += ["", report.disclaimer]
    return subject, "\n".join(lines) + "\n"


@transaction.atomic
def send_due_digest(
    within_days: int = 30,
    *,
    force: bool = False,
    recipients: Sequence[str] | None = None,
    today: date | None = None,
) -> DigestResult:
    """E-mail the digest of items with a deadline passed or near.

    Nothing is sent when no item is overdue or upcoming, nor when the same digest was
    already sent today, unless ``force`` is set.

    Args:
        within_days: Horizon in days.
        force: Send even if the same digest was already sent today.
        recipients: Addresses, defaults to ``GWIRIEK_ALERT_RECIPIENTS``.
        today: Reference day, defaults to today.

    Returns:
        What was done.

    Raises:
        InvalidDataError: If no recipient is configured or the horizon is negative.
    """
    addresses = list(recipients if recipients is not None else configured_recipients())
    if not addresses:
        raise InvalidDataError(
            _("No recipient: set GWIRIEK_ALERT_RECIPIENTS (comma-separated addresses).")
        )
    today = today or current_date()
    report = due_items(within_days, today)
    subject, body = render_digest(report)
    fingerprint = _fingerprint(report)
    count = len(report.overdue) + len(report.upcoming)

    def result(status: str) -> DigestResult:
        return DigestResult(
            as_of=today,
            status=status,
            recipients=addresses,
            subject=subject,
            item_count=count,
            fingerprint=fingerprint,
        )

    if count == 0:
        return result(NOTHING_DUE)
    if not force and DigestLog.objects.filter(sent_on=today, fingerprint=fingerprint).exists():
        return result(ALREADY_SENT)
    send_mail(subject, body, None, addresses)
    DigestLog.objects.create(
        sent_on=today,
        fingerprint=fingerprint,
        subject=subject[:200],
        recipients=", ".join(addresses),
        item_count=count,
        forced=force,
    )
    return result(SENT)
