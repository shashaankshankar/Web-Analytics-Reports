CREATE TABLE IF NOT EXISTS app.source_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  source_type text NOT NULL CHECK (source_type IN ('google_ads','search_console','call_tracking','crm_booking')),
  credential_secret_reference text NOT NULL,
  external_account_id text,
  configuration_json jsonb NOT NULL DEFAULT '{}',
  approval_status app.approval_status NOT NULL DEFAULT 'pending_approval',
  last_validated_at timestamptz,
  disabled_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(website_id,source_type)
);

CREATE TABLE IF NOT EXISTS analytics.google_ads_daily (
  source_connection_id uuid NOT NULL REFERENCES app.source_connections(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  metric_date date NOT NULL,
  campaign_id text NOT NULL,
  campaign_name text NOT NULL,
  ad_group_id text NOT NULL DEFAULT '',
  cost_micros bigint NOT NULL CHECK (cost_micros>=0),
  clicks bigint NOT NULL CHECK (clicks>=0),
  impressions bigint NOT NULL CHECK (impressions>=0),
  currency_code text NOT NULL,
  source_sync_at timestamptz NOT NULL,
  PRIMARY KEY(source_connection_id,metric_date,campaign_id,ad_group_id)
);

CREATE TABLE IF NOT EXISTS analytics.search_console_daily (
  source_connection_id uuid NOT NULL REFERENCES app.source_connections(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  metric_date date NOT NULL,
  query_hash text NOT NULL,
  query_text text,
  page_path text NOT NULL,
  clicks bigint NOT NULL CHECK (clicks>=0),
  impressions bigint NOT NULL CHECK (impressions>=0),
  ctr numeric NOT NULL CHECK (ctr>=0 AND ctr<=1),
  position numeric NOT NULL CHECK (position>=0),
  privacy_approved boolean NOT NULL DEFAULT false,
  source_sync_at timestamptz NOT NULL,
  CHECK (privacy_approved OR query_text IS NULL),
  PRIMARY KEY(source_connection_id,metric_date,query_hash,page_path)
);

CREATE TABLE IF NOT EXISTS analytics.first_party_outcomes (
  source_connection_id uuid NOT NULL REFERENCES app.source_connections(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  source_record_hash text NOT NULL,
  subject_key text,
  outcome_type text NOT NULL CHECK (outcome_type IN ('generated_lead','qualified_lead','booked_appointment','customer','call_answered','call_qualified','revenue')),
  outcome_date date NOT NULL,
  revenue_minor_units bigint,
  currency_code text,
  attribution_json jsonb NOT NULL DEFAULT '{}',
  identity_policy_reference text NOT NULL,
  source_sync_at timestamptz NOT NULL,
  CHECK ((outcome_type='revenue')=(revenue_minor_units IS NOT NULL)),
  PRIMARY KEY(source_connection_id,source_record_hash,outcome_type)
);

GRANT SELECT ON app.source_connections,analytics.google_ads_daily,analytics.search_console_daily,analytics.first_party_outcomes TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE,DELETE ON app.source_connections,analytics.google_ads_daily,analytics.search_console_daily,analytics.first_party_outcomes TO measurement_ingestion;

ALTER TABLE app.source_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.source_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY source_connection_tenant_isolation ON app.source_connections
USING (app.organization_allowed(organization_id));

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['google_ads_daily','search_console_daily','first_party_outcomes'] LOOP
    EXECUTE format('ALTER TABLE analytics.%I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('ALTER TABLE analytics.%I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('CREATE POLICY %I ON analytics.%I USING (app.database_role_is_privileged() OR website_id IN (SELECT id FROM app.websites))',table_name||'_tenant_isolation',table_name);
  END LOOP;
END $$;

INSERT INTO app.schema_migrations(version) VALUES('006_external_sources') ON CONFLICT(version) DO NOTHING;
