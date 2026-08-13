import { assertSupportedPeriod } from '../../../packages/analytics-types/src/index.mjs';

const periodDates = (period, today = new Date()) => {
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - 1));
  if (period === 'this_month') return { startDate: new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1)).toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
  if (period === 'last_month') {
    const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 1, 1));
    const last = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 0));
    return { startDate: start.toISOString().slice(0, 10), endDate: last.toISOString().slice(0, 10) };
  }
  const days = Number(period.replace('d', ''));
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - days + 1);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
};

const defaultReport = { dimensions: [{ name: 'date' }], metrics: [{ name: 'activeUsers' }, { name: 'sessions' }, { name: 'eventCount' }] };

export class Ga4AdminAdapter {
  constructor(credential, { enabled = false } = {}) { this.credential = credential; this.enabled = enabled; }
  async discover() {
    if (!this.enabled) return { status: 'disabled', properties: [], reason: 'admin_api_not_configured' };
    const properties = await this.credential.listAccessibleProperties();
    return { status: 'ready', properties };
  }
}

export class Ga4DataAdapter {
  constructor(credential, { enabled = false, fetchImpl = globalThis.fetch, now = () => new Date() } = {}) { this.credential = credential; this.enabled = enabled; this.fetchImpl = fetchImpl; this.now = now; }
  async runFixedReport({ assignment, reportDefinition, period }) {
    assertSupportedPeriod(period);
    if (!assignment || assignment.status !== 'approved') throw new Error('assignment_not_approved');
    if (!assignment.gaPropertyId || !assignment.gaStreamId) throw new Error('assignment_scope_incomplete');
    if (!this.enabled) throw new Error('data_api_not_configured');
    const client = await this.credential.getAuthorizedClient();
    if (!this.fetchImpl) throw new Error('fetch_unavailable');
    const dates = periodDates(period, this.now());
    const request = { dateRanges: [{ startDate: dates.startDate, endDate: dates.endDate }], dimensions: reportDefinition.dimensions || defaultReport.dimensions, metrics: reportDefinition.metrics || defaultReport.metrics, returnPropertyQuota: true };
    const response = await this.fetchImpl(`https://analyticsdata.googleapis.com/v1beta/properties/${encodeURIComponent(assignment.gaPropertyId)}:runReport`, { method: 'POST', headers: { authorization: `Bearer ${client.accessToken}`, 'content-type': 'application/json' }, body: JSON.stringify(request) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.status || 'data_api_request_failed');
    return { request: { property: assignment.gaPropertyId, report: reportDefinition.slug, period, ...request }, rows: body.rows || [], metadata: { propertyQuota: body.propertyQuota || null, rowCount: body.rowCount || 0, metadata: body.metadata || {}, propertyTimeZone: body.metadata?.timeZone || null, currencyCode: body.metadata?.currencyCode || null, subjectToThresholding: Boolean(body.metadata?.subjectToThresholding), dataLossFromOtherRow: Boolean(body.metadata?.dataLossFromOtherRow) } };
  }
}

export { periodDates };
