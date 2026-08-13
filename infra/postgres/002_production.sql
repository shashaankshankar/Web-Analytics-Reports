CREATE TABLE IF NOT EXISTS app.schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.platform_identifiers (
  key text PRIMARY KEY,
  value_uuid uuid,
  value_text text
);

CREATE TABLE IF NOT EXISTS app.client_goals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  metric_definition_version_id uuid NOT NULL REFERENCES app.metric_definition_versions(id),
  target_numeric numeric NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS app.annotations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  annotation_date date NOT NULL,
  annotation_type text NOT NULL,
  note text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.report_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL REFERENCES analytics.report_executions(id),
  assignment_id uuid NOT NULL REFERENCES app.website_analytics_assignments(id),
  view_slug text NOT NULL,
  period_key text NOT NULL CHECK (period_key IN ('7d','28d','this_month','last_month','90d')),
  freshness analytics.freshness_state NOT NULL,
  quality_status text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(execution_id, view_slug, period_key)
);

CREATE TABLE IF NOT EXISTS analytics.operator_alerts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  assignment_id uuid NOT NULL REFERENCES app.website_analytics_assignments(id),
  alert_key text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  state text NOT NULL CHECK (state IN ('open','resolved')),
  detail_json jsonb NOT NULL DEFAULT '{}',
  opened_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  UNIQUE(assignment_id, alert_key, state)
);

CREATE TABLE IF NOT EXISTS analytics.measurement_health_checks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  assignment_id uuid NOT NULL REFERENCES app.website_analytics_assignments(id),
  checked_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL,
  details_json jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS analytics.daily_canonical_metrics (
  report_execution_id uuid NOT NULL REFERENCES analytics.report_executions(id),
  assignment_id uuid NOT NULL REFERENCES app.website_analytics_assignments(id),
  event_mapping_version_id uuid NOT NULL,
  metric_definition_version_id uuid NOT NULL REFERENCES app.metric_definition_versions(id),
  semantic_version_id text NOT NULL,
  metric_date date NOT NULL,
  value_numeric numeric NOT NULL,
  PRIMARY KEY(report_execution_id,event_mapping_version_id,metric_definition_version_id,metric_date)
);

ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS period_key text;
ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS scheduled_for timestamptz;
ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS payload_json jsonb NOT NULL DEFAULT '{}';
ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;
ALTER TABLE analytics.sync_jobs ADD COLUMN IF NOT EXISTS replay_count integer NOT NULL DEFAULT 0;
ALTER TABLE analytics.data_quality_status ADD COLUMN IF NOT EXISTS last_successful_sync_at timestamptz;
ALTER TABLE analytics.report_executions ADD COLUMN IF NOT EXISTS execution_key text;
ALTER TABLE analytics.report_executions ADD COLUMN IF NOT EXISTS source_requests_json jsonb NOT NULL DEFAULT '[]';
ALTER TABLE analytics.report_executions ADD COLUMN IF NOT EXISTS source_metadata_json jsonb NOT NULL DEFAULT '[]';
ALTER TABLE analytics.daily_channel_metrics ADD COLUMN IF NOT EXISTS metric_date date;
ALTER TABLE analytics.daily_page_metrics ADD COLUMN IF NOT EXISTS metric_date date;

UPDATE analytics.daily_channel_metrics SET metric_date=current_date WHERE metric_date IS NULL;
UPDATE analytics.daily_page_metrics SET metric_date=current_date WHERE metric_date IS NULL;
ALTER TABLE analytics.daily_channel_metrics ALTER COLUMN metric_date SET NOT NULL;
ALTER TABLE analytics.daily_page_metrics ALTER COLUMN metric_date SET NOT NULL;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='daily_channel_metrics_pkey' AND conrelid='analytics.daily_channel_metrics'::regclass) THEN
    ALTER TABLE analytics.daily_channel_metrics DROP CONSTRAINT daily_channel_metrics_pkey;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='daily_page_metrics_pkey' AND conrelid='analytics.daily_page_metrics'::regclass) THEN
    ALTER TABLE analytics.daily_page_metrics DROP CONSTRAINT daily_page_metrics_pkey;
  END IF;
END $$;
ALTER TABLE analytics.daily_channel_metrics ADD CONSTRAINT daily_channel_metrics_pkey PRIMARY KEY(report_execution_id,channel,metric_date);
ALTER TABLE analytics.daily_page_metrics ADD CONSTRAINT daily_page_metrics_pkey PRIMARY KEY(report_execution_id,landing_page,metric_date);

DO $$
DECLARE constraint_name text;
BEGIN
  SELECT conname INTO constraint_name
    FROM pg_constraint
   WHERE conrelid='analytics.report_executions'::regclass
     AND contype='u'
     AND pg_get_constraintdef(oid) LIKE '%request_hash%'
   LIMIT 1;
  IF constraint_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE analytics.report_executions DROP CONSTRAINT %I', constraint_name);
  END IF;
END $$;
DROP INDEX IF EXISTS analytics.report_executions_execution_key_idx;
CREATE UNIQUE INDEX report_executions_execution_key_idx ON analytics.report_executions(execution_key);

CREATE INDEX IF NOT EXISTS sync_jobs_status_next_attempt_idx ON analytics.sync_jobs(status,next_attempt_at);
CREATE INDEX IF NOT EXISTS report_snapshots_lookup_idx ON analytics.report_snapshots(assignment_id,view_slug,period_key,created_at DESC);
CREATE INDEX IF NOT EXISTS report_executions_assignment_completed_idx ON analytics.report_executions(assignment_id,completed_at DESC);
CREATE INDEX IF NOT EXISTS measurement_health_assignment_checked_idx ON analytics.measurement_health_checks(assignment_id,checked_at DESC);

CREATE OR REPLACE VIEW analytics.current_report_snapshots AS
SELECT DISTINCT ON (assignment_id,view_slug,period_key)
       assignment_id,view_slug,period_key,payload,created_at,execution_id,freshness,quality_status
  FROM analytics.report_snapshots
 ORDER BY assignment_id,view_slug,period_key,created_at DESC,execution_id DESC;

INSERT INTO app.schema_migrations(version) VALUES('002_production') ON CONFLICT(version) DO NOTHING;
