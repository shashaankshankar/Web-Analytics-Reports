from pathlib import Path


def test_all_incremental_migrations_record_their_version():
    root=Path(__file__).resolve().parents[1]/"infra"/"postgres"
    for version in ("002_production","003_phase4_tenant_isolation","004_phase5_reporting_oauth","005_retention_offboarding","006_external_sources","007_external_sync_provenance","008_source_connection_management"):
        sql=(root/f"{version.split('_',1)[0]}_{version.split('_',1)[1]}.sql").read_text()
        assert f"VALUES('{version}')" in sql


def test_phase5_sensitive_values_are_references_or_ciphertext():
    sql=(Path(__file__).resolve().parents[1]/"infra"/"postgres"/"004_phase5_reporting_oauth.sql").read_text()
    assert "recipient_secret_reference" in sql
    assert "encrypted_refresh_token bytea" in sql
    assert "recipient_email" not in sql
    assert "refresh_token text" not in sql
