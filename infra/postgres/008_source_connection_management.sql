GRANT INSERT,UPDATE ON app.source_connections TO measurement_tenant;
DROP POLICY IF EXISTS source_connection_tenant_isolation ON app.source_connections;
CREATE POLICY source_connection_tenant_isolation ON app.source_connections
USING (app.organization_allowed(organization_id))
WITH CHECK (app.organization_allowed(organization_id));
INSERT INTO app.schema_migrations(version) VALUES('008_source_connection_management') ON CONFLICT(version) DO NOTHING;
