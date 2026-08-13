import fs from 'node:fs';
import { AGGREGATION, FRESHNESS, PERIODS, QUALITY } from '../../../packages/analytics-types/src/index.mjs';
import { periodDates } from '../../analytics-connectors/src/ga4.mjs';

const siteRecord = JSON.parse(fs.readFileSync(new URL('../../../measurement/sites/house-of-dental.json', import.meta.url), 'utf8'));
export const firstSite = Object.freeze({
  organizationId: 'org_agency',
  companyId: siteRecord.companyId,
  websiteId: siteRecord.siteId,
  assignmentId: siteRecord.assignmentId,
  company: siteRecord.company,
  canonicalDomain: siteRecord.canonicalDomain,
  deploymentStatus: siteRecord.deployment.status,
  businessTimezone: siteRecord.businessTimezone,
  propertyTimezone: siteRecord.ga4.propertyTimezone,
  gaPropertyId: siteRecord.ga4.propertyId,
  gaStreamId: siteRecord.ga4.webStreamId,
  measurementId: siteRecord.ga4.measurementId,
  collectionStatus: siteRecord.ga4.collectionStatus,
  governanceStatus: siteRecord.governance.status
});

export const assignments = new Map([[firstSite.assignmentId, {
  id: firstSite.assignmentId,
  websiteId: firstSite.websiteId,
  status: 'awaiting_client_grant',
  gaPropertyId: firstSite.gaPropertyId,
  gaStreamId: firstSite.gaStreamId,
  reportingScope: 'single_website'
}]]);
export const definitions = Object.freeze({
  generated_leads: { version: 'generated_leads_v1', aggregation: AGGREGATION.SUM, source: 'ga4_reporting_api' },
  appointment_requests: { version: 'appointment_requests_v1', aggregation: AGGREGATION.SUM, source: 'ga4_reporting_api' },
  active_users: { version: 'active_users_v1', aggregation: AGGREGATION.WINDOWED_UNIQUE, source: 'ga4_reporting_api' },
  sessions: { version: 'sessions_v1', aggregation: AGGREGATION.WINDOWED_UNIQUE, source: 'ga4_reporting_api' },
  appointment_request_rate: { version: 'appointment_request_rate_v1', aggregation: AGGREGATION.RATIO, source: 'ga4_reporting_api' }
});
const previousRange = ({ startDate, endDate }) => {
  const start = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  const days = Math.round((end - start) / 86_400_000) + 1;
  const previousEnd = new Date(start); previousEnd.setUTCDate(previousEnd.getUTCDate() - 1);
  const previousStart = new Date(previousEnd); previousStart.setUTCDate(previousStart.getUTCDate() - days + 1);
  return { start: previousStart.toISOString().slice(0, 10), end: previousEnd.toISOString().slice(0, 10) };
};
const unavailableSnapshot = (period) => {
  const dates = periodDates(period);
  return {
    period: { start: dates.startDate, end: dates.endDate },
    previous: previousRange(dates),
    executionId: `exec_unavailable_${period}`,
    freshness: FRESHNESS.PROVISIONAL,
    quality: QUALITY.DISABLED,
    metrics: { generated_leads: [null, null], appointment_requests: [null, null], active_users: [null, null], sessions: [null, null], appointment_request_rate: [null, null] }
  };
};
export const snapshots = new Map(PERIODS.map((period) => [period, unavailableSnapshot(period)]));
export const syncState = Object.freeze({ status: 'awaiting_access', lastSuccessfulSync: null, lastCompleteDate: null, queuedJobs: 0, failedJobs: 0, reason: 'awaiting_ga4_viewer_grant_and_governance_confirmation' });
