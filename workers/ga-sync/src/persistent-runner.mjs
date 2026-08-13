import { deterministicJobKey } from './index.mjs';

export async function syncFixedReport({ store, dataAdapter, job, assignment, reportDefinition }) {
  const idempotencyKey = job.idempotencyKey || deterministicJobKey(job);
  const persistedJob = await store.createSyncJob({ ...job, idempotencyKey });
  try {
    const result = await dataAdapter.runFixedReport({ assignment, reportDefinition, period: job.period });
    const execution = await store.recordReportExecution({
      assignmentId: job.assignmentId,
      reportDefinitionVersionId: job.reportDefinitionVersionId,
      syncJobId: persistedJob.id,
      startDate: job.startDate,
      endDate: job.endDate,
      request: result.request,
      response: result.rows,
      metadata: result.metadata
    });
    await store.markSyncJob(persistedJob.id, 'succeeded');
    return { job: persistedJob, execution, report: result };
  } catch (error) {
    await store.markSyncJob(persistedJob.id, 'failed', error.message, error.stack || null);
    throw error;
  }
}
