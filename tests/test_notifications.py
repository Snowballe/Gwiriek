from datetime import timedelta

import pytest
from django.core import mail

from gwiriek.equipment.models import DigestLog
from gwiriek.equipment.services import notifications
from gwiriek.equipment.services.errors import InvalidDataError


@pytest.fixture
def recipients(settings):
    settings.GWIRIEK_ALERT_RECIPIENTS = ["chef@example.org", "atelier@example.org"]
    return settings.GWIRIEK_ALERT_RECIPIENTS


@pytest.fixture
def overdue_item(harness_model, make_item, months_ago):
    return make_item(harness_model, label="H-1", purchased_on=months_ago(13))


def test_sends_digest(recipients, overdue_item, months_ago):
    result = notifications.send_due_digest()
    assert result.status == "sent"
    assert result.item_count == 1
    [message] = mail.outbox
    assert message.to == recipients
    assert "1" in message.subject
    assert "H-1" in message.body
    assert months_ago(1).strftime("%d/%m/%Y") in message.body
    assert "notice" in message.body  # disclaimer
    assert DigestLog.objects.get().fingerprint == result.fingerprint


def test_same_digest_not_sent_twice_a_day(recipients, overdue_item, today):
    assert notifications.send_due_digest().status == "sent"
    assert notifications.send_due_digest().status == "already_sent"
    assert len(mail.outbox) == 1
    forced = notifications.send_due_digest(force=True)
    assert forced.status == "sent"
    assert len(mail.outbox) == 2
    assert DigestLog.objects.filter(forced=True).count() == 1
    tomorrow = notifications.send_due_digest(today=today + timedelta(days=1))
    assert tomorrow.status == "sent"


def test_changed_digest_is_sent_again(recipients, overdue_item, harness_model, make_item):
    notifications.send_due_digest()
    make_item(harness_model, label="H-2")
    assert notifications.send_due_digest().status == "sent"
    assert len(mail.outbox) == 2


def test_nothing_due(recipients, db):
    assert notifications.send_due_digest().status == "nothing_due"
    assert mail.outbox == []


def test_no_recipient(settings, db):
    settings.GWIRIEK_ALERT_RECIPIENTS = []
    with pytest.raises(InvalidDataError, match="GWIRIEK_ALERT_RECIPIENTS"):
        notifications.send_due_digest()


def test_explicit_recipients(settings, overdue_item):
    settings.GWIRIEK_ALERT_RECIPIENTS = []
    result = notifications.send_due_digest(recipients=["x@example.org"])
    assert result.recipients == ["x@example.org"]
    assert mail.outbox[0].to == ["x@example.org"]


def test_never_inspected_line(recipients, harness_model, make_item):
    make_item(harness_model, label="NEW")
    notifications.send_due_digest()
    assert "jamais vérifié" in mail.outbox[0].body or "never inspected" in mail.outbox[0].body
