from pathlib import Path
from contextlib import contextmanager

import pytest

from app.storage import Database, MIGRATION_ORDER


def test_all_incremental_migrations_record_their_version():
    root=Path(__file__).resolve().parents[1]/"infra"/"postgres"
    for version in MIGRATION_ORDER:
        sql=(root/f"{version}.sql").read_text()
        assert f"VALUES('{version}')" in sql


def test_phase5_sensitive_values_are_references_or_ciphertext():
    sql=(Path(__file__).resolve().parents[1]/"infra"/"postgres"/"004_phase5_reporting_oauth.sql").read_text()
    assert "recipient_secret_reference" in sql
    assert "encrypted_refresh_token bytea" in sql
    assert "recipient_email" not in sql
    assert "refresh_token text" not in sql


class _Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _MigrationConnection:
    def __init__(self, *, organizations=True, ledger=True, applied=()):
        self.organizations = organizations
        self.ledger = ledger
        self.applied = set(applied)
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(statement)
        if "to_regclass('app.organizations')" in statement:
            return _Result({"exists": self.organizations})
        if "to_regclass('app.schema_migrations')" in statement:
            return _Result({"exists": self.ledger})
        if "SELECT version FROM app.schema_migrations" in statement:
            return _Result(rows=[{"version": version} for version in self.applied])
        return _Result()


class _MigrationDatabase(Database):
    def __init__(self, connection):
        self._test_connection = connection

    @contextmanager
    def connection(self):
        yield self._test_connection


def test_migration_runner_applies_current_schema_in_strict_order():
    connection = _MigrationConnection(applied=MIGRATION_ORDER[:-1])
    result = _MigrationDatabase(connection).migrate()

    assert result == {"status": "ok", "migration": "011_fact_provenance"}
    executed_sql = [statement for statement in connection.statements if statement.lstrip().startswith("--")]
    assert executed_sql == [(Path(__file__).resolve().parents[1] / "infra/postgres/011_fact_provenance.sql").read_text()]


def test_migration_runner_fails_closed_for_an_existing_schema_without_a_ledger():
    with pytest.raises(RuntimeError, match="migration_ledger_missing"):
        _MigrationDatabase(_MigrationConnection(organizations=True, ledger=False)).migrate()


def test_migration_runner_rejects_out_of_order_ledger_entries():
    with pytest.raises(RuntimeError, match="migration_ledger_out_of_order"):
        _MigrationDatabase(_MigrationConnection(applied=("002_production", "004_phase5_reporting_oauth"))).migrate()


def test_current_migrations_are_additive_and_bound_waits():
    root=Path(__file__).resolve().parents[1]/"infra"/"postgres"
    for version in ("010_onboarding_workflows", "011_fact_provenance"):
        sql=(root/f"{version}.sql").read_text()
        assert "SET LOCAL lock_timeout" in sql
        assert "SET LOCAL statement_timeout" in sql
        assert "DROP TABLE" not in sql.upper()
        assert "TRUNCATE" not in sql.upper()
        assert "DELETE FROM" not in sql.upper()
