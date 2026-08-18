-- Canonical GA4 web-stream measurement ID.
--
-- Completes the singleton-to-database migration: runtime code must read a
-- stream's Measurement ID from Postgres, not from the boot site JSON. The
-- column is nullable because streams discovered before this migration may not
-- yet carry a known ID; a later assignment or a re-run seed backfills it.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

ALTER TABLE app.ga_data_streams
  ADD COLUMN IF NOT EXISTS measurement_id text;

-- Compatibility bridge only. Populate from assignment JSON where an OAuth
-- assignment already recorded the value. Subsequent runtime writes use the
-- column as the canonical source; the JSON key is preserved solely for
-- transitional reads and existing consumers.
UPDATE app.ga_data_streams AS stream
   SET measurement_id = assignment.reporting_scope ->> 'measurementId'
  FROM app.website_analytics_assignments AS assignment
 WHERE assignment.ga_stream_id = stream.id
   AND assignment.reporting_scope ->> 'measurementId' IS NOT NULL
   AND stream.measurement_id IS NULL;

INSERT INTO app.schema_migrations(version)
VALUES('012_measurement_id_on_streams')
ON CONFLICT(version) DO NOTHING;
