CREATE TABLE IF NOT EXISTS analytics.source_sync_executions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_connection_id uuid NOT NULL REFERENCES app.source_connections(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  requested_start_date date NOT NULL,
  requested_end_date date NOT NULL,
  request_hash text NOT NULL,
  response_hash text,
  status text NOT NULL CHECK (status IN ('running','succeeded','failed','incomplete')),
  row_count integer,
  reconciliation_json jsonb NOT NULL DEFAULT '{}',
  error_code text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(source_connection_id,requested_start_date,requested_end_date,request_hash)
);
CREATE INDEX IF NOT EXISTS source_sync_execution_status_idx ON analytics.source_sync_executions(status,started_at);
GRANT SELECT ON analytics.source_sync_executions TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE ON analytics.source_sync_executions TO measurement_ingestion;
ALTER TABLE analytics.source_sync_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.source_sync_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY source_sync_execution_tenant_isolation ON analytics.source_sync_executions
USING (app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites));
INSERT INTO app.schema_migrations(version) VALUES('007_external_sync_provenance') ON CONFLICT(version) DO NOTHING;
