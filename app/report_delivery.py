from __future__ import annotations

import base64
import calendar
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr


def advance_schedule(value: datetime, cadence: str) -> datetime:
    if cadence == "weekly":
        return value + timedelta(days=7)
    if cadence == "monthly":
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)
    raise ValueError("unsupported_report_cadence")


def valid_email(value: str) -> bool:
    _, address = parseaddr(value)
    return bool(address and "@" in address and address == value.strip())


@dataclass(frozen=True)
class ReportEmailSender:
    api_key: str
    sender: str
    recipients: dict[str, str]
    endpoint: str = "https://api.resend.com/emails"

    @property
    def configured(self) -> bool:
        return self.api_key.startswith("re_") and valid_email(self.sender) and bool(self.recipients)

    def resolve_recipient(self, reference: str) -> str:
        recipient = self.recipients.get(reference, "")
        if not valid_email(recipient):
            raise RuntimeError("report_recipient_not_configured")
        return recipient

    def send_pdf(self, recipient_reference: str, subject: str, html: str, filename: str,
                 pdf: bytes, idempotency_key: str) -> str:
        if not self.configured:
            raise RuntimeError("report_email_not_configured")
        payload = json.dumps({
            "from": self.sender,
            "to": [self.resolve_recipient(recipient_reference)],
            "subject": subject,
            "html": html,
            "attachments": [{"filename": filename, "content": base64.b64encode(pdf).decode("ascii")}],
        }).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Idempotency-Key": idempotency_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"report_email_provider_{error.code}") from error
        message_id = result.get("id", "")
        if not message_id:
            raise RuntimeError("report_email_provider_missing_id")
        return message_id


def delivery_html(company: str, period: str) -> str:
    return (
        f"<p>Your approved {period} analytics report for {company} is attached.</p>"
        "<p>Appointment requests are requests for office follow-up, not booked or confirmed appointments. "
        "The report excludes the current incomplete local day.</p>"
    )
