import { assertSupportedPeriod, PERIODS } from '../../../packages/analytics-types/src/index.mjs';
import { assertWebsiteAccess } from '../../../packages/authorization/src/index.mjs';
import { measurementHealth } from '../../../packages/measurement/src/index.mjs';
import { firstSite, definitions, snapshots, syncState } from './site-store.mjs';

function metric(slug, values, snapshot) {
  const [value, previousValue] = values;
  const definition = definitions[slug];
  return { metric: slug, metricDefinitionVersion: definition.version, value, previousValue, period: snapshot.period, previousPeriod: snapshot.previous, propertyTimezone: firstSite.propertyTimezone, freshness: snapshot.freshness, provisional: snapshot.freshness !== 'stable', source: definition.source, aggregation: definition.aggregation, reportExecutionId: snapshot.executionId, warnings: value === null ? ['reporting_connection_unavailable'] : [] };
}

export function overview(context, websiteId, period = '28d') {
  assertWebsiteAccess(context, websiteId);
  assertSupportedPeriod(period);
  const snapshot = snapshots.get(period);
  return { websiteId, period, supportedPeriods: PERIODS, metrics: Object.entries(snapshot.metrics).map(([slug, values]) => metric(slug, values, snapshot)), dataStatus: snapshot.quality, sync: syncState };
}

export function health(context, websiteId) {
  assertWebsiteAccess(context, websiteId);
  return {
    websiteId,
    deploymentStatus: firstSite.deploymentStatus,
    publicCollectionStatus: firstSite.collectionStatus,
    governanceStatus: firstSite.governanceStatus,
    publicVerification: { measuredAt: '2026-08-12', measurementId: firstSite.measurementId, result: 'ga4_collection_http_204_after_consent' },
    ...measurementHealth({ contractStatus: 'pending_approval', routeManifestStatus: 'pending_privacy_review', streamStatus: 'configured_unverified', lastValidationAt: '2026-08-12', requiredEvents: ['form_start', 'form_submit', 'generate_lead', 'phone_click', 'email_click', 'appointment_request', 'cta_click'] }),
    sync: syncState
  };
}

export function syncStatus(context, websiteId) { assertWebsiteAccess(context, websiteId); return { websiteId, ...syncState }; }

export function acquisition(context, websiteId, period) { return { ...overview(context, websiteId, period), rows: [], warning: 'No source rows are shown until an approved GA4 assignment has completed a sync.' }; }

export function portfolio(context) { return { organizationId: context.organizationId, websites: [{ websiteId: firstSite.websiteId, company: firstSite.company, canonicalDomain: firstSite.canonicalDomain, deploymentState: firstSite.deploymentStatus, collectionState: firstSite.collectionStatus, governanceState: firstSite.governanceStatus, sync: syncState, measurementState: 'live_collection_reporting_pending' }] }; }
