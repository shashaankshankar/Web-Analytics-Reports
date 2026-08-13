import { assertWebsiteAccess, createTenantContext } from '../../../packages/authorization/src/index.mjs';
import { Ga4DataAdapter } from '../../analytics-connectors/src/ga4.mjs';
import { credentialFromEnvironment } from '../../analytics-connectors/src/credentials.mjs';
import { firstSite } from './site-store.mjs';

const propertyId = (env) => String(env.GA4_PROPERTY_ID || '').replace(/^properties\//, '');
const streamId = (env) => String(env.GA4_STREAM_ID || '').replace(/^properties\/\d+\/dataStreams\//, '');

const valueFor = (row, index) => {
  const value = row.metricValues?.[index]?.value;
  return value === undefined || value === null || value === '' ? null : Number(value);
};
const eventCountByName = (rows, name) => rows.filter((row) => row.dimensionValues?.[0]?.value === name).reduce((sum, row) => sum + (valueFor(row, 0) ?? 0), 0);

export const liveEnabled = (env = process.env) => env.PLATFORM_MODE === 'live' && env.GA4_DATA_API_ENABLED === 'true' && env.GA4_LIVE_APPROVED === 'true';

export function createLiveReporter(env = process.env) {
  const assignment = {
    id: firstSite.assignmentId,
    websiteId: firstSite.websiteId,
    status: 'approved',
    gaPropertyId: propertyId(env),
    gaStreamId: streamId(env),
    reportingScope: 'single_website_required'
  };
  if (!assignment.gaPropertyId || !assignment.gaStreamId) throw new Error('live_ga4_assignment_incomplete');
  if (assignment.gaPropertyId !== firstSite.gaPropertyId) throw new Error('live_ga4_property_mismatch');
  if (assignment.gaStreamId !== firstSite.gaStreamId) throw new Error('live_ga4_stream_mismatch');

  const credential = credentialFromEnvironment(env);
  const adapter = new Ga4DataAdapter(credential, { enabled: true });
  return { assignment, adapter };
}

export async function liveOverview(context, websiteId, period = '28d', env = process.env) {
  assertWebsiteAccess(context, websiteId);
  if (websiteId !== firstSite.websiteId) throw new Error('forbidden_website');
  const { assignment, adapter } = createLiveReporter(env);
  const [summary, events] = await Promise.all([
    adapter.runFixedReport({ assignment, period, reportDefinition: { slug: 'live_summary', dimensions: [], metrics: [{ name: 'activeUsers' }, { name: 'sessions' }, { name: 'eventCount' }] } }),
    adapter.runFixedReport({ assignment, period, reportDefinition: { slug: 'live_events', dimensions: [{ name: 'eventName' }], metrics: [{ name: 'eventCount' }] } })
  ]);
  const summaryRow = summary.rows[0] || {};
  const activeUsers = valueFor(summaryRow, 0);
  const sessions = valueFor(summaryRow, 1);
  const appointmentRequests = eventCountByName(events.rows, 'appointment_request');
  return {
    websiteId,
    period,
    property: `properties/${assignment.gaPropertyId}`,
    stream: `properties/${assignment.gaPropertyId}/dataStreams/${assignment.gaStreamId}`,
    measurementId: firstSite.measurementId,
    propertyTimezone: summary.metadata.propertyTimeZone,
    dataStatus: 'live',
    metrics: [
      { metric: 'generated_leads', value: null, previousValue: null, source: 'ga4_reporting_api', warnings: ['event_not_implemented', 'previous_period_not_loaded'] },
      { metric: 'appointment_requests', value: appointmentRequests, previousValue: null, source: 'ga4_reporting_api', warnings: ['previous_period_not_loaded'] },
      { metric: 'active_users', value: activeUsers, previousValue: null, source: 'ga4_reporting_api', warnings: ['previous_period_not_loaded'] },
      { metric: 'sessions', value: sessions, previousValue: null, source: 'ga4_reporting_api', warnings: ['previous_period_not_loaded'] },
      { metric: 'appointment_request_rate', value: sessions ? appointmentRequests / sessions : null, previousValue: null, source: 'ga4_reporting_api', warnings: ['previous_period_not_loaded'] }
    ],
    api: { summaryRows: summary.rows.length, eventRows: events.rows.length, rowCount: summary.metadata.rowCount + events.metadata.rowCount, propertyQuota: summary.metadata.propertyQuota }
  };
}

export async function liveAcquisition(context, websiteId, period = '28d', env = process.env) {
  assertWebsiteAccess(context, websiteId);
  if (websiteId !== firstSite.websiteId) throw new Error('forbidden_website');
  const { assignment, adapter } = createLiveReporter(env);
  const report = await adapter.runFixedReport({
    assignment,
    period,
    reportDefinition: {
      slug: 'live_acquisition',
      dimensions: [{ name: 'sessionDefaultChannelGroup' }],
      metrics: [{ name: 'sessions' }, { name: 'activeUsers' }]
    }
  });
  return {
    websiteId,
    period,
    dataStatus: 'live',
    propertyTimezone: report.metadata.propertyTimeZone,
    rows: report.rows.map((row) => ({
      channel: row.dimensionValues?.[0]?.value || '(not set)',
      sessions: valueFor(row, 0) ?? 0,
      activeUsers: valueFor(row, 1) ?? 0
    })),
    warnings: ['previous_period_not_loaded'],
    api: { rowCount: report.metadata.rowCount, propertyQuota: report.metadata.propertyQuota }
  };
}

export function liveContext() {
  return createTenantContext({ organizationId: firstSite.organizationId, companyIds: [firstSite.companyId], websiteIds: [firstSite.websiteId], role: 'agency_admin' });
}
