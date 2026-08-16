import json
from contextlib import contextmanager
from pathlib import Path

from app.sync import SOURCE_SYSTEM, SyncEngine, authoritative_sampling_counts


ROOT = Path(__file__).resolve().parents[1]
FACT_TABLES = (
    "daily_property_metrics",
    "daily_event_metrics",
    "daily_channel_metrics",
    "daily_page_metrics",
    "daily_canonical_metrics",
)
PROVENANCE_COLUMNS = (
    "report_definition_version_id",
    "samples_read_count",
    "sampling_space_size",
    "source_system",
    "measurement_contract_version",
    "data_status",
    "last_synced_at",
)


def test_fact_provenance_migration_is_additive_idempotent_and_preserves_security():
    sql = (ROOT / "infra/postgres/011_fact_provenance.sql").read_text()

    assert "011_fact_provenance" in sql
    assert "ON CONFLICT(version) DO NOTHING" in sql
    assert "last_synced_at = completed_at" in sql
    assert "WHERE fact.report_execution_id = execution.id" in sql
    assert "DROP POLICY" not in sql
    assert "REVOKE" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql
    assert "SET LOCAL lock_timeout" in sql
    assert "SET LOCAL statement_timeout" in sql
    assert "every fact table's existing primary key starts" in sql
    assert sql.count("fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL") == len(FACT_TABLES)
    assert sql.count("fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL") == len(FACT_TABLES)

    for column in PROVENANCE_COLUMNS[1:]:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
    for table in ("report_executions", *FACT_TABLES):
        assert f"analytics.{table}" in sql
    for table in FACT_TABLES:
        for column in PROVENANCE_COLUMNS:
            assert f"ALTER TABLE analytics.{table}" in sql
            assert f"ADD COLUMN IF NOT EXISTS {column}" in sql


def test_sampling_counts_require_one_agreeing_authoritative_pair():
    provenance = [
        {"sampling_metadata": {"sampling_metadatas": [{"samples_read_count": "12", "sampling_space_size": "34"}]}},
        {"sampling_metadata": {"samplingMetadatas": [{"samplesReadCount": "12", "samplingSpaceSize": "34"}]}},
    ]
    assert authoritative_sampling_counts(provenance) == (12, 34)

    conflicting = [{"sampling_metadata": [{"samples_read_count": "12", "sampling_space_size": "34"}, {"samples_read_count": "13", "sampling_space_size": "34"}]}]
    assert authoritative_sampling_counts(conflicting) == (None, None)

    malformed = [{"sampling_metadata": {"sampling_metadatas": [{"samples_read_count": "unknown"}]}}]
    assert authoritative_sampling_counts(malformed) == (None, None)
    oversized = [{"sampling_metadata": {"sampling_metadatas": [{"samples_read_count": "9223372036854775808", "sampling_space_size": "34"}]}}]
    assert authoritative_sampling_counts(oversized) == (None, None)
    assert authoritative_sampling_counts([]) == (None, None)


class _Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        if "SELECT mc.slug AS contract_slug" in statement:
            return _Result({"contract_slug": "local_service_v1", "contract_version": 1})
        if "FROM app.event_mappings" in statement:
            return _Result(rows=[])
        return _Result()


class _Database:
    def __init__(self, connection):
        self._connection = connection

    @contextmanager
    def connection(self):
        yield self._connection


def _provenance_item(sampling_metadata):
    return {
        "row_count": 1,
        "property_timezone": "America/New_York",
        "currency_code": "USD",
        "data_loss_from_other_row": False,
        "empty_reason": None,
        "schema_restriction": {},
        "subject_to_thresholding": False,
        "sampling_metadata": sampling_metadata,
        "property_quota": {},
        "date_range": {"start": "2026-08-01", "end": "2026-08-07"},
        "request": {"property": "properties/123", "metrics": ["sessions"]},
    }


def test_sync_round_trip_carries_execution_and_daily_fact_provenance():
    connection = _Connection()
    bundle = {
        "period": "7d",
        "dateRange": {"start": "2026-08-01", "end": "2026-08-07"},
        "quality": {"status": "ok", "freshness": "reconciling"},
        "provenance": [
            _provenance_item({"sampling_metadatas": [{"samples_read_count": "12", "sampling_space_size": "34"}]}),
            _provenance_item({"sampling_metadatas": [{"samples_read_count": "12", "sampling_space_size": "34"}]}),
        ],
        "views": {"overview": {"metrics": []}},
        "daily": {
            "property": {"rows": [{"dimensions": ["20260807"], "metrics": ["3", "4"]}]},
            "events": {"rows": [{"dimensions": ["20260807", "phone_click"], "metrics": ["2"]}]},
            "channels": {"rows": [{"dimensions": ["20260807", "Organic Search"], "metrics": ["5", "6"]}]},
            "pages": {"rows": [{"dimensions": ["20260807", "/"], "metrics": ["5", "6", "7"]}]},
        },
    }

    result = SyncEngine(_Database(connection), None)._persist(
        "job-1", "run-1", "assignment-1", "report-version-1", "execution-key", "scheduled", bundle
    )

    assert result["status"] == "succeeded"
    execution_call = next(call for call in connection.calls if "INSERT INTO analytics.report_executions" in call[0])
    execution_params = execution_call[1]
    assert execution_params[18:23] == (SOURCE_SYSTEM, "local_service_v1@1", "reconciling", 12, 34)
    assert json.loads(execution_params[13])[0]["sampling_metadatas"][0]["samples_read_count"] == "12"

    daily_calls = [call for call in connection.calls if "INSERT INTO analytics.daily_" in call[0]]
    assert len(daily_calls) == 5
    for statement, params in daily_calls:
        assert all(column in statement for column in PROVENANCE_COLUMNS)
        assert "last_synced_at" in statement and "now()" in statement
        assert params[5:] == ("report-version-1", SOURCE_SYSTEM, "local_service_v1@1", "reconciling", 12, 34)
