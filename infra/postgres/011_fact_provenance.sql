-- Additive warehouse provenance for immutable report executions and daily facts.
-- Existing RLS policies and table grants are intentionally left untouched: adding
-- columns and copying values through existing foreign keys preserves both.
-- Bound DDL wait and the historical backfill. A timeout aborts the transaction
-- instead of leaving a partially applied provenance migration.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

-- No new provenance index is required for this backfill: report_executions.id
-- is already its primary key and every fact table's existing primary key starts
-- with report_execution_id. Avoid an unnecessary lock-heavy index build.

ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.report_executions
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS report_definition_version_id uuid REFERENCES app.report_definition_versions(id);
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.daily_property_metrics
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS report_definition_version_id uuid REFERENCES app.report_definition_versions(id);
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.daily_event_metrics
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS report_definition_version_id uuid REFERENCES app.report_definition_versions(id);
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.daily_channel_metrics
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS report_definition_version_id uuid REFERENCES app.report_definition_versions(id);
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.daily_page_metrics
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS report_definition_version_id uuid REFERENCES app.report_definition_versions(id);
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS samples_read_count bigint;
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS sampling_space_size bigint;
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS source_system text;
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS measurement_contract_version text;
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS data_status analytics.freshness_state;
ALTER TABLE analytics.daily_canonical_metrics
  ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;

-- This timestamp is directly derivable from an already successful execution.
-- Source and contract values remain NULL for historical rows because the old
-- schema did not retain enough evidence to recover them truthfully.
UPDATE analytics.report_executions
   SET last_synced_at = completed_at
 WHERE last_synced_at IS NULL
   AND status = 'succeeded'
   AND completed_at IS NOT NULL;

-- Recover singular GA4 sample counts only when every retained raw entry is a
-- numeric, agreeing pair. Ambiguous or malformed metadata remains NULL while
-- sampling_metadata_json stays the authoritative raw record.
WITH normalized AS (
  SELECT e.id,
         CASE
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'array'
             THEN e.sampling_metadata_json
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'object'
                AND e.sampling_metadata_json ? 'sampling_metadatas'
                AND jsonb_typeof(e.sampling_metadata_json->'sampling_metadatas') = 'array'
             THEN e.sampling_metadata_json->'sampling_metadatas'
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'object'
                AND e.sampling_metadata_json ? 'sampling_metadatas'
                AND jsonb_typeof(e.sampling_metadata_json->'sampling_metadatas') = 'object'
             THEN jsonb_build_array(e.sampling_metadata_json->'sampling_metadatas')
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'object'
                AND e.sampling_metadata_json ? 'samplingMetadatas'
                AND jsonb_typeof(e.sampling_metadata_json->'samplingMetadatas') = 'array'
             THEN e.sampling_metadata_json->'samplingMetadatas'
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'object'
                AND e.sampling_metadata_json ? 'samplingMetadatas'
                AND jsonb_typeof(e.sampling_metadata_json->'samplingMetadatas') = 'object'
             THEN jsonb_build_array(e.sampling_metadata_json->'samplingMetadatas')
           WHEN jsonb_typeof(e.sampling_metadata_json) = 'object'
                AND (e.sampling_metadata_json ? 'samples_read_count'
                     OR e.sampling_metadata_json ? 'samplesReadCount')
             THEN jsonb_build_array(e.sampling_metadata_json)
           ELSE '[]'::jsonb
         END AS entries
    FROM analytics.report_executions AS e
), entries AS (
  SELECT n.id, entry.value
    FROM normalized AS n
    CROSS JOIN LATERAL jsonb_array_elements(n.entries) AS entry(value)
), fields AS (
  SELECT id,
         CASE
           WHEN COALESCE(value->>'samples_read_count', value->>'samplesReadCount') ~ '^[0-9]+$'
             THEN CASE
               WHEN (COALESCE(value->>'samples_read_count', value->>'samplesReadCount'))::numeric <= 9223372036854775807
                 THEN (COALESCE(value->>'samples_read_count', value->>'samplesReadCount'))::bigint
             END
         END AS samples_read_count,
         CASE
           WHEN COALESCE(value->>'sampling_space_size', value->>'samplingSpaceSize') ~ '^[0-9]+$'
             THEN CASE
               WHEN (COALESCE(value->>'sampling_space_size', value->>'samplingSpaceSize'))::numeric <= 9223372036854775807
                 THEN (COALESCE(value->>'sampling_space_size', value->>'samplingSpaceSize'))::bigint
             END
         END AS sampling_space_size
    FROM entries
), candidates AS (
  SELECT id,
         count(*) AS entry_count,
         count(*) FILTER (WHERE samples_read_count IS NOT NULL AND sampling_space_size IS NOT NULL) AS valid_count,
         count(DISTINCT CASE
           WHEN samples_read_count IS NOT NULL AND sampling_space_size IS NOT NULL
             THEN samples_read_count::text || ':' || sampling_space_size::text
         END) AS distinct_pairs,
         min(samples_read_count) AS samples_read_count,
         min(sampling_space_size) AS sampling_space_size
    FROM fields
   GROUP BY id
)
UPDATE analytics.report_executions AS execution
   SET samples_read_count = candidate.samples_read_count,
       sampling_space_size = candidate.sampling_space_size
  FROM candidates AS candidate
 WHERE execution.id = candidate.id
   AND execution.samples_read_count IS NULL
   AND execution.sampling_space_size IS NULL
   AND candidate.entry_count = candidate.valid_count
   AND candidate.valid_count > 0
   AND candidate.distinct_pairs = 1;

-- Fact provenance is copied only through each fact's existing execution FK.
-- COALESCE makes the repair idempotent and never replaces known values with NULL.
UPDATE analytics.daily_property_metrics AS fact
   SET report_definition_version_id = COALESCE(fact.report_definition_version_id, execution.report_definition_version_id),
       samples_read_count = COALESCE(fact.samples_read_count, execution.samples_read_count),
       sampling_space_size = COALESCE(fact.sampling_space_size, execution.sampling_space_size),
       source_system = COALESCE(fact.source_system, execution.source_system),
       measurement_contract_version = COALESCE(fact.measurement_contract_version, execution.measurement_contract_version),
       data_status = COALESCE(fact.data_status, execution.data_status),
       last_synced_at = COALESCE(fact.last_synced_at, execution.last_synced_at)
  FROM analytics.report_executions AS execution
 WHERE fact.report_execution_id = execution.id
   AND (
     (fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL)
     OR (fact.samples_read_count IS NULL AND execution.samples_read_count IS NOT NULL)
     OR (fact.sampling_space_size IS NULL AND execution.sampling_space_size IS NOT NULL)
     OR (fact.source_system IS NULL AND execution.source_system IS NOT NULL)
     OR (fact.measurement_contract_version IS NULL AND execution.measurement_contract_version IS NOT NULL)
     OR (fact.data_status IS NULL AND execution.data_status IS NOT NULL)
     OR (fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL)
   );

UPDATE analytics.daily_event_metrics AS fact
   SET report_definition_version_id = COALESCE(fact.report_definition_version_id, execution.report_definition_version_id),
       samples_read_count = COALESCE(fact.samples_read_count, execution.samples_read_count),
       sampling_space_size = COALESCE(fact.sampling_space_size, execution.sampling_space_size),
       source_system = COALESCE(fact.source_system, execution.source_system),
       measurement_contract_version = COALESCE(fact.measurement_contract_version, execution.measurement_contract_version),
       data_status = COALESCE(fact.data_status, execution.data_status),
       last_synced_at = COALESCE(fact.last_synced_at, execution.last_synced_at)
  FROM analytics.report_executions AS execution
 WHERE fact.report_execution_id = execution.id
   AND (
     (fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL)
     OR (fact.samples_read_count IS NULL AND execution.samples_read_count IS NOT NULL)
     OR (fact.sampling_space_size IS NULL AND execution.sampling_space_size IS NOT NULL)
     OR (fact.source_system IS NULL AND execution.source_system IS NOT NULL)
     OR (fact.measurement_contract_version IS NULL AND execution.measurement_contract_version IS NOT NULL)
     OR (fact.data_status IS NULL AND execution.data_status IS NOT NULL)
     OR (fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL)
   );

UPDATE analytics.daily_channel_metrics AS fact
   SET report_definition_version_id = COALESCE(fact.report_definition_version_id, execution.report_definition_version_id),
       samples_read_count = COALESCE(fact.samples_read_count, execution.samples_read_count),
       sampling_space_size = COALESCE(fact.sampling_space_size, execution.sampling_space_size),
       source_system = COALESCE(fact.source_system, execution.source_system),
       measurement_contract_version = COALESCE(fact.measurement_contract_version, execution.measurement_contract_version),
       data_status = COALESCE(fact.data_status, execution.data_status),
       last_synced_at = COALESCE(fact.last_synced_at, execution.last_synced_at)
  FROM analytics.report_executions AS execution
 WHERE fact.report_execution_id = execution.id
   AND (
     (fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL)
     OR (fact.samples_read_count IS NULL AND execution.samples_read_count IS NOT NULL)
     OR (fact.sampling_space_size IS NULL AND execution.sampling_space_size IS NOT NULL)
     OR (fact.source_system IS NULL AND execution.source_system IS NOT NULL)
     OR (fact.measurement_contract_version IS NULL AND execution.measurement_contract_version IS NOT NULL)
     OR (fact.data_status IS NULL AND execution.data_status IS NOT NULL)
     OR (fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL)
   );

UPDATE analytics.daily_page_metrics AS fact
   SET report_definition_version_id = COALESCE(fact.report_definition_version_id, execution.report_definition_version_id),
       samples_read_count = COALESCE(fact.samples_read_count, execution.samples_read_count),
       sampling_space_size = COALESCE(fact.sampling_space_size, execution.sampling_space_size),
       source_system = COALESCE(fact.source_system, execution.source_system),
       measurement_contract_version = COALESCE(fact.measurement_contract_version, execution.measurement_contract_version),
       data_status = COALESCE(fact.data_status, execution.data_status),
       last_synced_at = COALESCE(fact.last_synced_at, execution.last_synced_at)
  FROM analytics.report_executions AS execution
 WHERE fact.report_execution_id = execution.id
   AND (
     (fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL)
     OR (fact.samples_read_count IS NULL AND execution.samples_read_count IS NOT NULL)
     OR (fact.sampling_space_size IS NULL AND execution.sampling_space_size IS NOT NULL)
     OR (fact.source_system IS NULL AND execution.source_system IS NOT NULL)
     OR (fact.measurement_contract_version IS NULL AND execution.measurement_contract_version IS NOT NULL)
     OR (fact.data_status IS NULL AND execution.data_status IS NOT NULL)
     OR (fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL)
   );

UPDATE analytics.daily_canonical_metrics AS fact
   SET report_definition_version_id = COALESCE(fact.report_definition_version_id, execution.report_definition_version_id),
       samples_read_count = COALESCE(fact.samples_read_count, execution.samples_read_count),
       sampling_space_size = COALESCE(fact.sampling_space_size, execution.sampling_space_size),
       source_system = COALESCE(fact.source_system, execution.source_system),
       measurement_contract_version = COALESCE(fact.measurement_contract_version, execution.measurement_contract_version),
       data_status = COALESCE(fact.data_status, execution.data_status),
       last_synced_at = COALESCE(fact.last_synced_at, execution.last_synced_at)
  FROM analytics.report_executions AS execution
 WHERE fact.report_execution_id = execution.id
   AND (
     (fact.report_definition_version_id IS NULL AND execution.report_definition_version_id IS NOT NULL)
     OR (fact.samples_read_count IS NULL AND execution.samples_read_count IS NOT NULL)
     OR (fact.sampling_space_size IS NULL AND execution.sampling_space_size IS NOT NULL)
     OR (fact.source_system IS NULL AND execution.source_system IS NOT NULL)
     OR (fact.measurement_contract_version IS NULL AND execution.measurement_contract_version IS NOT NULL)
     OR (fact.data_status IS NULL AND execution.data_status IS NOT NULL)
     OR (fact.last_synced_at IS NULL AND execution.last_synced_at IS NOT NULL)
   );

INSERT INTO app.schema_migrations(version)
VALUES('011_fact_provenance')
ON CONFLICT(version) DO NOTHING;
