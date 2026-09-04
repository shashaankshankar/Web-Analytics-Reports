"""Small local store for internal report-delivery provenance.

The reporting service does not persist report bodies or recipient data.  It
only needs the provider IDs returned after a successful internal report send
so a later read-only metrics query can scope itself to this service's reports.
SQLite is used from the standard library because the repository has no existing
persistence dependency or repository abstraction.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_SAFE_METADATA_KEYS = frozenset(
    {
        "delivery_kind",
        "has_attachment",
        "provider_status",
        "provider_status_code",
        "idempotency_key_hash",
    }
)


def _enum_value(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value).strip()


def _validate_slug(value: Any, field_name: str) -> str:
    normalized = _enum_value(value).lower()
    if not _SLUG_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase client/report slug.")
    return normalized


def _parse_temporal(value: Any, field_name: str) -> datetime:
    """Parse an ISO date/datetime for validation without changing its stored text."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        try:
            if "T" in raw or " " in raw:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                parsed = datetime.combine(date.fromisoformat(raw), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO date or datetime.") from exc
    else:
        raise ValueError(f"{field_name} must be an ISO date or datetime.")

    if parsed.tzinfo is None:
        # Report windows are dates, while timestamps supplied by callers must
        # be explicit.  A date-only value was already made UTC-aware above.
        if isinstance(value, datetime) or (isinstance(value, str) and ("T" in value or " " in value)):
            raise ValueError(f"{field_name} datetime must include a timezone offset.")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _preserve_iso(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{field_name} datetime must include a timezone offset.")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO date or datetime.")
    raw = value.strip()
    _parse_temporal(raw, field_name)
    return raw


def _normalize_sent_at(value: Any) -> str:
    parsed = _parse_temporal(value, "sent_at")
    return parsed.isoformat().replace("+00:00", "Z")


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise ValueError("technical_metadata must be a mapping.")

    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key = str(key)
        if key not in _SAFE_METADATA_KEYS:
            raise ValueError("technical_metadata contains an unsupported field.")
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError("technical_metadata contains a non-finite value.")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError("technical_metadata values must be scalar and non-sensitive.")
        if isinstance(value, str) and len(value) > 512:
            raise ValueError("technical_metadata values are too long.")
        safe[key] = value
    return safe


@dataclass(frozen=True)
class SentReportRecord:
    """Safe provenance for one accepted internal report email."""

    resend_email_id: str
    client_id: str
    report_type: str
    reporting_window_start: str
    reporting_window_end: str
    timezone: str
    sent_at: str
    recipient_role: str = "client"
    cloud_run_revision: str = ""
    idempotency_key: str = ""
    technical_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SentReportQuery:
    """Retained IDs and the number excluded by the configured retention rule."""

    records: tuple[SentReportRecord, ...]
    expired_count: int = 0

    @property
    def email_ids(self) -> list[str]:
        return [record.resend_email_id for record in self.records]


class SentReportStore:
    """SQLite-backed store for report email IDs.

    ``None`` or an empty path disables persistence.  ``:memory:`` is supported
    for isolated tests, but a file path is required for persistence across
    process restarts.
    """

    def __init__(self, path: str | Path | None):
        raw_path = str(path).strip() if path is not None else ""
        self.path: str | Path | None = ":memory:" if raw_path == ":memory:" else (Path(raw_path) if raw_path else None)
        self._memory_connection: sqlite3.Connection | None = None

    @property
    def is_configured(self) -> bool:
        return self.path is not None

    def _connect(self) -> tuple[sqlite3.Connection, bool]:
        if not self.is_configured:
            raise RuntimeError("Report-delivery persistence is not configured.")
        if self.path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._initialize(self._memory_connection)
            return self._memory_connection, False

        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=30)
        self._initialize(connection)
        try:
            path.chmod(0o600)
        except OSError:
            # Permission hardening is best effort on platforms/filesystems that
            # do not expose POSIX modes; the store still remains usable.
            pass
        return connection, True

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_report_emails (
                resend_email_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                reporting_window_start TEXT NOT NULL,
                reporting_window_end TEXT NOT NULL,
                timezone TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                recipient_role TEXT NOT NULL DEFAULT 'client',
                cloud_run_revision TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL DEFAULT '',
                technical_metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        # Migrate existing table if columns are missing
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sent_report_emails)").fetchall()}
        if "recipient_role" not in columns:
            connection.execute("ALTER TABLE sent_report_emails ADD COLUMN recipient_role TEXT NOT NULL DEFAULT 'client'")
        if "cloud_run_revision" not in columns:
            connection.execute("ALTER TABLE sent_report_emails ADD COLUMN cloud_run_revision TEXT NOT NULL DEFAULT ''")
        if "idempotency_key" not in columns:
            connection.execute("ALTER TABLE sent_report_emails ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sent_report_window
            ON sent_report_emails (
                client_id,
                report_type,
                reporting_window_start,
                reporting_window_end,
                timezone
            )
            """
        )
        connection.commit()

    @staticmethod
    def _record_from_row(row: sqlite3.Row | tuple[Any, ...]) -> SentReportRecord:
        if isinstance(row, sqlite3.Row):
            keys = row.keys()
            return SentReportRecord(
                resend_email_id=str(row["resend_email_id"]),
                client_id=str(row["client_id"]),
                report_type=str(row["report_type"]),
                reporting_window_start=str(row["reporting_window_start"]),
                reporting_window_end=str(row["reporting_window_end"]),
                timezone=str(row["timezone"]),
                sent_at=str(row["sent_at"]),
                recipient_role=str(row["recipient_role"]) if "recipient_role" in keys else "client",
                cloud_run_revision=str(row["cloud_run_revision"]) if "cloud_run_revision" in keys else "",
                idempotency_key=str(row["idempotency_key"]) if "idempotency_key" in keys else "",
                technical_metadata=json.loads(row["technical_metadata_json"]) if "technical_metadata_json" in keys and row["technical_metadata_json"] else {},
            )
        return SentReportRecord(
            resend_email_id=str(row[0]),
            client_id=str(row[1]),
            report_type=str(row[2]),
            reporting_window_start=str(row[3]),
            reporting_window_end=str(row[4]),
            timezone=str(row[5]),
            sent_at=str(row[6]),
            recipient_role=str(row[7]) if len(row) > 8 else "client",
            cloud_run_revision=str(row[8]) if len(row) > 9 else "",
            idempotency_key=str(row[9]) if len(row) > 10 else "",
            technical_metadata=json.loads(row[-1]) if row[-1] else {},
        )

    def record_sent_report(
        self,
        *,
        resend_email_id: str,
        client_id: str,
        report_type: Any,
        reporting_window_start: Any,
        reporting_window_end: Any,
        timezone_name: str,
        sent_at: Any,
        recipient_role: str = "client",
        cloud_run_revision: str = "",
        idempotency_key: str = "",
        technical_metadata: Mapping[str, Any] | None = None,
    ) -> SentReportRecord:
        """Record an accepted provider ID without storing recipients or content."""
        email_id = str(resend_email_id).strip()
        if not _ID_RE.fullmatch(email_id):
            raise ValueError("resend_email_id must be a provider-safe identifier.")
        client_slug = _validate_slug(client_id, "client_id")
        report_slug = _validate_slug(report_type, "report_type")
        start = _preserve_iso(reporting_window_start, "reporting_window_start")
        end = _preserve_iso(reporting_window_end, "reporting_window_end")
        if _parse_temporal(start, "reporting_window_start") > _parse_temporal(end, "reporting_window_end"):
            raise ValueError("reporting_window_start must not be after reporting_window_end.")
        timezone_name = str(timezone_name).strip()
        if not timezone_name:
            raise ValueError("timezone is required for report provenance.")
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone.") from exc
        sent_timestamp = _normalize_sent_at(sent_at)
        safe_metadata = _safe_metadata(technical_metadata)
        record = SentReportRecord(
            resend_email_id=email_id,
            client_id=client_slug,
            report_type=report_slug,
            reporting_window_start=start,
            reporting_window_end=end,
            timezone=timezone_name,
            sent_at=sent_timestamp,
            recipient_role=str(recipient_role).strip() if recipient_role else "client",
            cloud_run_revision=str(cloud_run_revision).strip() if cloud_run_revision else "",
            idempotency_key=str(idempotency_key).strip() if idempotency_key else "",
            technical_metadata=safe_metadata,
        )

        connection, close_after = self._connect()
        try:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                "SELECT resend_email_id, client_id, report_type, reporting_window_start, "
                "reporting_window_end, timezone, sent_at, recipient_role, "
                "cloud_run_revision, idempotency_key, technical_metadata_json "
                "FROM sent_report_emails WHERE resend_email_id = ?",
                (record.resend_email_id,),
            ).fetchone()
            if existing is not None:
                existing_record = self._record_from_row(existing)
                same_provenance = (
                    existing_record.resend_email_id == record.resend_email_id
                    and existing_record.client_id == record.client_id
                    and existing_record.report_type == record.report_type
                    and existing_record.reporting_window_start == record.reporting_window_start
                    and existing_record.reporting_window_end == record.reporting_window_end
                    and existing_record.timezone == record.timezone
                )
                if not same_provenance:
                    raise ValueError("resend_email_id is already bound to different report provenance.")
                return existing_record

            connection.execute(
                "INSERT INTO sent_report_emails ("
                "resend_email_id, client_id, report_type, reporting_window_start, "
                "reporting_window_end, timezone, sent_at, recipient_role, "
                "cloud_run_revision, idempotency_key, technical_metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.resend_email_id,
                    record.client_id,
                    record.report_type,
                    record.reporting_window_start,
                    record.reporting_window_end,
                    record.timezone,
                    record.sent_at,
                    record.recipient_role,
                    record.cloud_run_revision,
                    record.idempotency_key,
                    json.dumps(record.technical_metadata, sort_keys=True, separators=(",", ":")),
                ),
            )
            connection.commit()
            return record
        finally:
            if close_after:
                connection.close()

    def find_sent_reports(
        self,
        *,
        client_id: str,
        report_type: Any,
        reporting_window_start: Any,
        reporting_window_end: Any,
        timezone_name: str,
        retention_cutoff: Any | None = None,
        recipient_role: str | None = None,
    ) -> SentReportQuery:
        """Find IDs for one exact client/report/window/timezone tuple.

        When a retention cutoff is supplied, records older than it are not
        returned, but are counted so callers can expose a truthful partial or
        unavailable state rather than treating missing history as zero.
        """
        client_slug = _validate_slug(client_id, "client_id")
        report_slug = _validate_slug(report_type, "report_type")
        start = _preserve_iso(reporting_window_start, "reporting_window_start")
        end = _preserve_iso(reporting_window_end, "reporting_window_end")
        if _parse_temporal(start, "reporting_window_start") > _parse_temporal(end, "reporting_window_end"):
            raise ValueError("reporting_window_start must not be after reporting_window_end.")
        timezone_name = str(timezone_name).strip()
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone.") from exc

        cutoff: str | None = None
        if retention_cutoff is not None:
            cutoff = _normalize_sent_at(retention_cutoff)

        connection, close_after = self._connect()
        try:
            connection.row_factory = sqlite3.Row
            where_clauses = [
                "client_id = ?",
                "report_type = ?",
                "reporting_window_start = ?",
                "reporting_window_end = ?",
                "timezone = ?",
            ]
            params: list[Any] = [client_slug, report_slug, start, end, timezone_name]
            if recipient_role is not None:
                where_clauses.append("recipient_role = ?")
                params.append(str(recipient_role).strip())

            expired_count = 0
            if cutoff is not None:
                count_where = " AND ".join(where_clauses + ["sent_at < ?"])
                expired_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM sent_report_emails WHERE {count_where}",
                        (*params, cutoff),
                    ).fetchone()[0]
                )
                query_where = " AND ".join(where_clauses + ["sent_at >= ?"])
                rows = connection.execute(
                    f"SELECT resend_email_id, client_id, report_type, reporting_window_start, "
                    f"reporting_window_end, timezone, sent_at, recipient_role, "
                    f"cloud_run_revision, idempotency_key, technical_metadata_json "
                    f"FROM sent_report_emails WHERE {query_where} ORDER BY sent_at, resend_email_id",
                    (*params, cutoff),
                ).fetchall()
            else:
                query_where = " AND ".join(where_clauses)
                rows = connection.execute(
                    f"SELECT resend_email_id, client_id, report_type, reporting_window_start, "
                    f"reporting_window_end, timezone, sent_at, recipient_role, "
                    f"cloud_run_revision, idempotency_key, technical_metadata_json "
                    f"FROM sent_report_emails WHERE {query_where} ORDER BY sent_at, resend_email_id",
                    tuple(params),
                ).fetchall()
            return SentReportQuery(
                records=tuple(self._record_from_row(row) for row in rows),
                expired_count=expired_count,
            )
        finally:
            if close_after:
                connection.close()

    def list_report_email_ids(self, **filters: Any) -> list[str]:
        """Convenience wrapper returning only provider IDs for the exact filter."""
        return self.find_sent_reports(**filters).email_ids

    def prune_before(self, cutoff: Any) -> int:
        """Delete records older than a caller-selected retention cutoff."""
        cutoff_value = _normalize_sent_at(cutoff)
        connection, close_after = self._connect()
        try:
            cursor = connection.execute("DELETE FROM sent_report_emails WHERE sent_at < ?", (cutoff_value,))
            connection.commit()
            return int(cursor.rowcount)
        finally:
            if close_after:
                connection.close()
