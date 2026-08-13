from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from urllib.parse import urlsplit


SOURCES=("google_ads","search_console","call_tracking","crm_booking")
DIRECT_IDENTIFIER_KEYS=frozenset({"name","email","phone","telephone","address","patient","dob","date_of_birth","recording","transcript"})
SAFE_ID=re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class SourceConnector(Protocol):
    source_type: str
    def validate_access(self) -> dict: ...
    def sync(self, start_date: date, end_date: date) -> list[dict]: ...
    def disable(self) -> None: ...


def assert_no_direct_identifiers(value: dict) -> None:
    found=DIRECT_IDENTIFIER_KEYS & {key.lower() for key in value}
    if found: raise ValueError("prohibited_direct_identifier_fields")


def safe_source_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value): raise ValueError("unsafe_source_identifier")
    return value


def safe_page_path(value: str) -> str:
    parsed=urlsplit(value)
    path=parsed.path or "/"
    if parsed.query or parsed.fragment or not path.startswith("/"): raise ValueError("unsafe_page_path")
    return path


def opaque_record_hash(source_id: str, secret: str) -> str:
    if len(secret)<32: raise ValueError("outcome_hash_secret_required")
    safe_source_id(source_id)
    return hmac.new(secret.encode(),source_id.encode(),hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class ExternalSourceState:
    source: str
    status: str
    approval_status: str | None
    last_validated_at: datetime | None
    last_sync_at: datetime | None
    blocker: str | None

    def as_dict(self) -> dict:
        return {"source":self.source,"status":self.status,"approvalStatus":self.approval_status,
                "lastValidatedAt":self.last_validated_at.isoformat() if self.last_validated_at else None,
                "lastSyncAt":self.last_sync_at.isoformat() if self.last_sync_at else None,"blocker":self.blocker}
