from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from email.utils import parseaddr
from typing import List, Optional


def is_valid_email(val: str) -> bool:
    _, addr = parseaddr(val)
    return bool(addr and "@" in addr and addr == val.strip())


class ResendEmailSender:
    def __init__(
        self,
        api_key: Optional[str] = None,
        from_email: Optional[str] = None,
        endpoint: str = "https://api.resend.com/emails",
    ):
        self.api_key = api_key or os.getenv("RESEND_API_KEY", os.getenv("REPORT_EMAIL_API_KEY", ""))
        self.from_email = from_email or os.getenv("RESEND_FROM_EMAIL", os.getenv("REPORT_EMAIL_FROM", "reports@growthagency.com"))
        self.endpoint = endpoint

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) >= 20 and is_valid_email(self.from_email))

    def send_briefing(
        self,
        to_recipients: List[str] | str,
        subject: str,
        html_content: str,
        pdf_attachment: Optional[bytes] = None,
        pdf_filename: str = "Executive_Growth_Briefing.pdf",
        cc_recipients: Optional[List[str] | str] = None,
    ) -> dict:
        """Dispatch growth briefing email via Resend API."""
        if isinstance(to_recipients, str):
            raw_to = [s.strip() for s in to_recipients.split(",") if s.strip()]
        else:
            raw_to = [s.strip() for s in to_recipients if s.strip()]
        valid_to = [r for r in raw_to if is_valid_email(r)]
        if not valid_to:
            raise ValueError("No valid recipient email addresses provided.")

        if not self.is_configured:
            # Return dry-run / unconfigured simulation dict
            return {
                "status": "simulated_unconfigured",
                "message": "Resend API key or sender email not configured; delivery skipped.",
                "to": valid_to,
                "subject": subject,
            }

        payload: dict = {
            "from": self.from_email,
            "to": valid_to,
            "subject": subject,
            "html": html_content,
        }
        if cc_recipients:
            if isinstance(cc_recipients, str):
                raw_cc = [s.strip() for s in cc_recipients.split(",") if s.strip()]
            else:
                raw_cc = [s.strip() for s in cc_recipients if s.strip()]
            valid_cc = [r for r in raw_cc if is_valid_email(r)]
            if valid_cc:
                payload["cc"] = valid_cc

        if pdf_attachment:
            payload["attachments"] = [
                {
                    "filename": pdf_filename,
                    "content": base64.b64encode(pdf_attachment).decode("ascii"),
                }
            ]

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                res_data = json.loads(response.read())
                return {
                    "status": "sent",
                    "id": res_data.get("id"),
                    "to": valid_to,
                    "subject": subject,
                }
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Resend API error {error.code}: {body}") from error
