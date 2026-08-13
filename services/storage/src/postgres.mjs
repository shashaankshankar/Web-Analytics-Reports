import crypto from 'node:crypto';

const digest = (value) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

export class PostgresStore {
  constructor(pool) { this.pool = pool; }

  async createSyncJob({ assignmentId, reportDefinitionVersionId, startDate, endDate, idempotencyKey }) {
    const result = await this.pool.query(`
      INSERT INTO analytics.sync_jobs
        (assignment_id, report_definition_version_id, requested_start_date, requested_end_date, idempotency_key)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
      RETURNING id, assignment_id, report_definition_version_id, requested_start_date, requested_end_date, idempotency_key, status
    `, [assignmentId, reportDefinitionVersionId, startDate, endDate, idempotencyKey]);
    return result.rows[0];
  }

  async recordReportExecution({ assignmentId, reportDefinitionVersionId, syncJobId, startDate, endDate, request, response, metadata = {}, status = 'succeeded' }) {
    const result = await this.pool.query(`
      INSERT INTO analytics.report_executions
        (assignment_id, report_definition_version_id, sync_job_id, requested_start_date, requested_end_date,
         request_hash, response_hash, property_time_zone, currency_code, subject_to_thresholding,
         data_loss_from_other_row, sampling_metadata_json, property_quota_json, status, completed_at)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, now())
      ON CONFLICT (assignment_id, report_definition_version_id, requested_start_date, requested_end_date, request_hash)
      DO UPDATE SET response_hash = EXCLUDED.response_hash, property_quota_json = EXCLUDED.property_quota_json,
        status = EXCLUDED.status, completed_at = now()
      RETURNING id, status, request_hash, response_hash
    `, [assignmentId, reportDefinitionVersionId, syncJobId, startDate, endDate, digest(request), digest(response), metadata.propertyTimeZone ?? null, metadata.currencyCode ?? null, Boolean(metadata.subjectToThresholding), Boolean(metadata.dataLossFromOtherRow), metadata.sampling ?? {}, metadata.propertyQuota ?? {}, status]);
    return result.rows[0];
  }

  async markSyncJob(jobId, status, errorCode = null, errorDetail = null) {
    const result = await this.pool.query('UPDATE analytics.sync_jobs SET status = $2, error_code = $3, error_detail = $4 WHERE id = $1 RETURNING id, status, error_code', [jobId, status, errorCode, errorDetail]);
    return result.rows[0];
  }
}

export async function createPostgresStore(connectionString = process.env.DATABASE_URL) {
  if (!connectionString) throw new Error('database_not_configured');
  const { Pool } = await import('pg');
  return new PostgresStore(new Pool({ connectionString }));
}
