CREATE TABLE IF NOT EXISTS app.recurring_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  name text NOT NULL,
  period_key text NOT NULL CHECK (period_key IN ('7d','28d','this_month','last_month','90d')),
  cadence text NOT NULL CHECK (cadence IN ('weekly','monthly')),
  timezone text NOT NULL,
  recipient_secret_reference text NOT NULL,
  enabled boolean NOT NULL DEFAULT false,
  next_run_at timestamptz NOT NULL,
  created_by uuid REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  disabled_at timestamptz
);

CREATE TABLE IF NOT EXISTS analytics.report_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recurring_report_id uuid NOT NULL REFERENCES app.recurring_reports(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  scheduled_for timestamptz NOT NULL,
  status text NOT NULL CHECK (status IN ('queued','generating','sent','failed','blocked_configuration')),
  provider_message_id text,
  report_hash text,
  error_code text,
  attempt_count integer NOT NULL DEFAULT 1,
  detail_json jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE(recurring_report_id,scheduled_for)
);

CREATE TABLE IF NOT EXISTS app.oauth_authorization_states (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  actor_user_id uuid NOT NULL REFERENCES app.users(id),
  state_hash text NOT NULL UNIQUE,
  pkce_verifier_ciphertext bytea NOT NULL,
  requested_scopes text[] NOT NULL,
  redirect_uri text NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.oauth_credentials (
  analytics_connection_id uuid PRIMARY KEY REFERENCES app.analytics_connections(id),
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  encrypted_refresh_token bytea NOT NULL,
  granted_scopes text[] NOT NULL,
  token_endpoint text NOT NULL,
  provider_subject text,
  connected_by uuid REFERENCES app.users(id),
  connected_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  last_validated_at timestamptz
);

CREATE INDEX IF NOT EXISTS recurring_reports_due_idx ON app.recurring_reports(enabled,next_run_at);
CREATE INDEX IF NOT EXISTS report_deliveries_status_idx ON analytics.report_deliveries(status,created_at);
CREATE INDEX IF NOT EXISTS oauth_state_expiry_idx ON app.oauth_authorization_states(expires_at);

GRANT INSERT,UPDATE ON app.analytics_connections TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE ON app.recurring_reports,app.oauth_authorization_states TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE,DELETE ON app.oauth_credentials TO measurement_tenant;
GRANT SELECT ON analytics.report_deliveries TO measurement_tenant;

ALTER TABLE app.recurring_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.recurring_reports FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS recurring_report_tenant_isolation ON app.recurring_reports;
CREATE POLICY recurring_report_tenant_isolation ON app.recurring_reports USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
) WITH CHECK (app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites));

ALTER TABLE analytics.report_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.report_deliveries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS report_delivery_tenant_isolation ON analytics.report_deliveries;
CREATE POLICY report_delivery_tenant_isolation ON analytics.report_deliveries USING (
  app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites)
);

ALTER TABLE app.oauth_authorization_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.oauth_authorization_states FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS oauth_state_tenant_isolation ON app.oauth_authorization_states;
CREATE POLICY oauth_state_tenant_isolation ON app.oauth_authorization_states USING (
  app.organization_allowed(organization_id)
) WITH CHECK (app.organization_allowed(organization_id));

ALTER TABLE app.oauth_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.oauth_credentials FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS oauth_credential_tenant_isolation ON app.oauth_credentials;
CREATE POLICY oauth_credential_tenant_isolation ON app.oauth_credentials USING (
  app.organization_allowed(organization_id)
) WITH CHECK (app.organization_allowed(organization_id));

INSERT INTO app.schema_migrations(version) VALUES('004_phase5_reporting_oauth') ON CONFLICT(version) DO NOTHING;
