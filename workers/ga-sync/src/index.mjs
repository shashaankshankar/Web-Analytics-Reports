import crypto from 'node:crypto';

export function deterministicJobKey({ assignmentId, reportDefinitionVersionId, startDate, endDate }) {
  return crypto.createHash('sha256').update(JSON.stringify({ assignmentId, reportDefinitionVersionId, startDate, endDate })).digest('hex');
}

export class InMemoryQueue {
  constructor() { this.jobs = new Map(); }
  enqueue(job) { const key = deterministicJobKey(job); if (!this.jobs.has(key)) this.jobs.set(key, { ...job, key, attempts: 0, status: 'queued' }); return this.jobs.get(key); }
  async process(key, handler) { const job = this.jobs.get(key); if (!job) throw new Error('job_not_found'); job.attempts += 1; try { const result = await handler(job); job.status = 'succeeded'; job.result = result; return job; } catch (error) { job.status = job.attempts >= 3 ? 'dead_letter' : 'retryable'; job.errorCode = error.message; throw error; } }
}

export async function runSyncJob({ job, dataAdapter, assignment, reportDefinition }) {
  return dataAdapter.runFixedReport({ assignment, reportDefinition, period: job.period });
}
