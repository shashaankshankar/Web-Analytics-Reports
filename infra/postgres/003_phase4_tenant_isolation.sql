CREATE OR REPLACE FUNCTION app.current_organization_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.organization_id', true), '')::uuid
$$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='measurement_tenant') THEN
    CREATE ROLE measurement_tenant NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='measurement_ingestion') THEN
    CREATE ROLE measurement_ingestion NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='measurement_admin') THEN
    CREATE ROLE measurement_admin NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  EXECUTE format('GRANT measurement_tenant,measurement_ingestion,measurement_admin TO %I', current_user);
END $$;

CREATE TABLE IF NOT EXISTS app.resource_identifiers (
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  resource_type text NOT NULL CHECK (resource_type IN ('company','website')),
  resource_id uuid NOT NULL,
  public_id text NOT NULL,
  PRIMARY KEY (organization_id,resource_type,public_id),
  UNIQUE (resource_type,resource_id)
);

GRANT USAGE ON SCHEMA app,analytics,audit TO measurement_tenant;
GRANT SELECT ON ALL TABLES IN SCHEMA app,analytics,audit TO measurement_tenant;
REVOKE ALL ON app.platform_identifiers FROM measurement_tenant;
GRANT INSERT,UPDATE,DELETE ON app.annotations,app.client_goals TO measurement_tenant;
GRANT INSERT ON audit.events TO measurement_tenant;

CREATE OR REPLACE FUNCTION app.database_role_is_privileged() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT pg_has_role(current_user, 'measurement_ingestion', 'member')
      OR pg_has_role(current_user, 'measurement_admin', 'member')
$$;

CREATE OR REPLACE FUNCTION app.organization_allowed(candidate uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT app.database_role_is_privileged() OR candidate=app.current_organization_id()
$$;

DROP POLICY IF EXISTS company_tenant_isolation ON app.companies;
DROP POLICY IF EXISTS website_tenant_isolation ON app.websites;

ALTER TABLE app.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY organization_tenant_isolation ON app.organizations USING (app.organization_allowed(id));

ALTER TABLE app.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.companies FORCE ROW LEVEL SECURITY;
CREATE POLICY company_tenant_isolation ON app.companies USING (app.organization_allowed(organization_id));

ALTER TABLE app.websites ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.websites FORCE ROW LEVEL SECURITY;
CREATE POLICY website_tenant_isolation ON app.websites USING (
  app.database_role_is_privileged() OR company_id IN (SELECT id FROM app.companies)
);

ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_tenant_isolation ON app.memberships USING (app.organization_allowed(organization_id));

ALTER TABLE app.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.users FORCE ROW LEVEL SECURITY;
CREATE POLICY user_tenant_isolation ON app.users USING (
  app.database_role_is_privileged() OR id IN (SELECT user_id FROM app.memberships)
);

ALTER TABLE app.analytics_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.analytics_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY connection_tenant_isolation ON app.analytics_connections USING (app.organization_allowed(organization_id));

ALTER TABLE app.ga_properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ga_properties FORCE ROW LEVEL SECURITY;
CREATE POLICY property_tenant_isolation ON app.ga_properties USING (
  app.database_role_is_privileged() OR analytics_connection_id IN (SELECT id FROM app.analytics_connections)
);

ALTER TABLE app.ga_data_streams ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ga_data_streams FORCE ROW LEVEL SECURITY;
CREATE POLICY stream_tenant_isolation ON app.ga_data_streams USING (
  app.database_role_is_privileged() OR ga_property_id IN (SELECT id FROM app.ga_properties)
);

ALTER TABLE app.website_analytics_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.website_analytics_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY assignment_tenant_isolation ON app.website_analytics_assignments USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
);

ALTER TABLE app.website_measurement_contract_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.website_measurement_contract_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY contract_assignment_tenant_isolation ON app.website_measurement_contract_assignments USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
);

ALTER TABLE app.event_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.event_mappings FORCE ROW LEVEL SECURITY;
CREATE POLICY event_mapping_tenant_isolation ON app.event_mappings USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
);

ALTER TABLE app.client_goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.client_goals FORCE ROW LEVEL SECURITY;
CREATE POLICY goal_tenant_isolation ON app.client_goals USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
) WITH CHECK (app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites));

ALTER TABLE app.annotations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.annotations FORCE ROW LEVEL SECURITY;
CREATE POLICY annotation_tenant_isolation ON app.annotations USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
) WITH CHECK (app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites));

ALTER TABLE app.resource_identifiers ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.resource_identifiers FORCE ROW LEVEL SECURITY;
CREATE POLICY resource_identifier_tenant_isolation ON app.resource_identifiers
USING (app.organization_allowed(organization_id));

ALTER TABLE audit.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.events FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_tenant_isolation ON audit.events USING (app.organization_allowed(organization_id))
WITH CHECK (app.organization_allowed(organization_id));

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'sync_runs','sync_jobs','report_executions','daily_property_metrics',
    'daily_channel_metrics','daily_page_metrics','daily_event_metrics',
    'period_metric_snapshots','data_quality_status','report_snapshots',
    'operator_alerts','measurement_health_checks','daily_canonical_metrics'
  ] LOOP
    EXECUTE format('ALTER TABLE analytics.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE analytics.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS %I ON analytics.%I', table_name || '_tenant_isolation', table_name);
    EXECUTE format(
      'CREATE POLICY %I ON analytics.%I USING (app.database_role_is_privileged() OR assignment_id IN (SELECT id FROM app.website_analytics_assignments))',
      table_name || '_tenant_isolation', table_name
    );
  END LOOP;
END $$;

CREATE OR REPLACE VIEW analytics.current_report_snapshots
WITH (security_invoker=true) AS
SELECT DISTINCT ON (assignment_id,view_slug,period_key)
       assignment_id,view_slug,period_key,payload,created_at,execution_id,freshness,quality_status
  FROM analytics.report_snapshots
 ORDER BY assignment_id,view_slug,period_key,created_at DESC,execution_id DESC;

INSERT INTO app.schema_migrations(version) VALUES('003_phase4_tenant_isolation') ON CONFLICT(version) DO NOTHING;
