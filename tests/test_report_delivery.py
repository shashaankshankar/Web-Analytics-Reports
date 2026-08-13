from datetime import datetime, timezone

import pytest

from app.report_delivery import ReportEmailSender, advance_schedule, valid_email


def test_schedule_advance_handles_weekly_and_month_end():
    value=datetime(2026,1,31,14,tzinfo=timezone.utc)
    assert advance_schedule(value,"weekly").day == 7
    assert advance_schedule(value,"monthly") == datetime(2026,2,28,14,tzinfo=timezone.utc)
    with pytest.raises(ValueError): advance_schedule(value,"daily")


def test_sender_configuration_and_recipient_validation_fail_closed():
    sender=ReportEmailSender("","reports@example.com",{})
    assert sender.configured is False
    assert valid_email("office@example.com")
    assert not valid_email("not-an-email")
    with pytest.raises(RuntimeError,match="recipient_not_configured"): sender.resolve_recipient("office")


def test_sender_supports_a_https_relay_without_weakening_configuration_checks():
    sender=ReportEmailSender("r"*48,"reports@example.com",{"operator":"operator@example.com"},"https://thehouseofdentalwp.com/api/report-email")
    assert sender.configured is True
    assert sender.resolve_recipient("operator") == "operator@example.com"
    assert ReportEmailSender("short","reports@example.com",{"operator":"operator@example.com"},sender.endpoint).configured is False
    assert ReportEmailSender("r"*48,"reports@example.com",{"operator":"operator@example.com"},"http://example.com").configured is False
