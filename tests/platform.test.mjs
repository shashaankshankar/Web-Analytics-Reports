import test from 'node:test';
import assert from 'node:assert/strict';
import { AGGREGATION, calculateMetric, freshnessForAgeDays } from '../packages/analytics-types/src/index.mjs';
import { createTenantContext, assertWebsiteAccess } from '../packages/authorization/src/index.mjs';
import { AdcCredential, DisabledCredential, ServiceAccountCredential, credentialFromEnvironment } from '../services/analytics-connectors/src/credentials.mjs';
import { Ga4AdminAdapter, Ga4DataAdapter, periodDates } from '../services/analytics-connectors/src/ga4.mjs';
import { deterministicJobKey, InMemoryQueue } from '../workers/ga-sync/src/index.mjs';
import { overview, health } from '../services/reporting/src/index.mjs';
import { firstSite } from '../services/reporting/src/site-store.mjs';
import { assertProductionSecurity, requestAuthorized } from '../apps/web/security.mjs';
import { createLiveReporter } from '../services/reporting/src/live.mjs';

const context = createTenantContext({ organizationId: firstSite.organizationId, websiteIds: [firstSite.websiteId], role: 'agency_admin' });
test('ratios are recomputed and zero denominators remain unknown', () => { assert.equal(calculateMetric({ aggregation: AGGREGATION.RATIO, numerator: 2, denominator: 8 }), .25); assert.equal(calculateMetric({ aggregation: AGGREGATION.RATIO, numerator: 2, denominator: 0 }), null); });
test('freshness states follow reconciliation policy', () => { assert.equal(freshnessForAgeDays(0), 'realtime'); assert.equal(freshnessForAgeDays(1), 'provisional'); assert.equal(freshnessForAgeDays(14), 'reconciling'); assert.equal(freshnessForAgeDays(15), 'stable'); });
test('tenant access fails closed', () => { assert.throws(() => assertWebsiteAccess(context, 'other_website'), /forbidden_website/); });
test('live HTTP mode requires a strong API token', () => {
  assert.throws(() => assertProductionSecurity({ PLATFORM_MODE: 'live' }), /platform_api_token_required/);
  assert.doesNotThrow(() => assertProductionSecurity({ PLATFORM_MODE: 'live', PLATFORM_API_TOKEN: 'a'.repeat(32) }));
  assert.equal(requestAuthorized({ headers: { authorization: `Bearer ${'a'.repeat(32)}` } }, { PLATFORM_MODE: 'live', PLATFORM_API_TOKEN: 'a'.repeat(32) }), true);
  assert.equal(requestAuthorized({ headers: { authorization: 'Bearer wrong' } }, { PLATFORM_MODE: 'live', PLATFORM_API_TOKEN: 'a'.repeat(32) }), false);
});
test('live GA4 adapter rejects unapproved or incomplete assignment', async () => { const adapter = new Ga4DataAdapter(new DisabledCredential(), { enabled: true }); await assert.rejects(adapter.runFixedReport({ assignment: { status: 'pending_approval' }, reportDefinition: { slug: 'property_summary' }, period: '28d' }), /assignment_not_approved/); });
test('service accounts are disabled until explicitly configured', async () => { const credential = new ServiceAccountCredential({ enabled: false }); assert.deepEqual(await credential.validateAccess(), { valid: false, reason: 'credential_not_configured' }); });
test('ADC credentials obtain an access token from the configured auth client', async () => {
  const credential = new AdcCredential({ enabled: true, authFactory: () => ({ async getClient() { return { async getAccessToken() { return { token: 'adc-token' }; } }; } }) });
  assert.deepEqual(await credential.getAuthorizedClient(), { authType: 'adc', scopes: ['https://www.googleapis.com/auth/analytics.readonly'], accessToken: 'adc-token' });
});
test('environment credentials use ADC when no JSON key is supplied', () => {
  assert.equal(credentialFromEnvironment({ GA4_DATA_API_ENABLED: 'true' }).constructor.name, 'AdcCredential');
  assert.equal(credentialFromEnvironment({ GA4_ADMIN_API_ENABLED: 'true' }).constructor.name, 'AdcCredential');
});
test('equivalent queue jobs are idempotent', () => { const job = { assignmentId: 'a', reportDefinitionVersionId: 'r', startDate: '2026-08-01', endDate: '2026-08-08' }; const queue = new InMemoryQueue(); assert.equal(queue.enqueue(job), queue.enqueue(job)); assert.equal(deterministicJobKey(job), deterministicJobKey({ ...job })); });
test('reporting response exposes missing server data and provenance', () => { const result = overview(context, firstSite.websiteId, '28d'); assert.equal(result.dataStatus, 'disabled'); assert.ok(result.metrics.every((item) => item.reportExecutionId && item.warnings.includes('reporting_connection_unavailable'))); });
test('all advertised periods preserve the requested window in setup mode', () => {
  for (const period of ['7d', '28d', 'this_month', 'last_month', '90d']) assert.equal(overview(context, firstSite.websiteId, period).period, period);
});
test('measurement health exposes hard blockers', () => { assert.equal(health(context, firstSite.websiteId).state, 'blocked'); });
test('live reporter rejects property or stream drift from the first site record', () => {
  const base = { PLATFORM_MODE: 'live', GA4_DATA_API_ENABLED: 'true', GA4_LIVE_APPROVED: 'true' };
  assert.throws(() => createLiveReporter({ ...base, GA4_PROPERTY_ID: '1', GA4_STREAM_ID: firstSite.gaStreamId }), /live_ga4_property_mismatch/);
  assert.throws(() => createLiveReporter({ ...base, GA4_PROPERTY_ID: firstSite.gaPropertyId, GA4_STREAM_ID: '2' }), /live_ga4_stream_mismatch/);
});
test('GA4 fixed periods produce complete date ranges', () => { assert.deepEqual(periodDates('28d', new Date('2026-08-09T12:00:00Z')), { startDate: '2026-07-12', endDate: '2026-08-08' }); });
test('GA4 Data API adapter builds a real report request and preserves metadata', async () => {
  const calls = [];
  const credential = { async getAuthorizedClient() { return { accessToken: 'test-token' }; } };
  const adapter = new Ga4DataAdapter(credential, { enabled: true, now: () => new Date('2026-08-09T12:00:00Z'), fetchImpl: async (url, options) => { calls.push({ url, options }); return { ok: true, async json() { return { rows: [{ dimensionValues: [{ value: '20260808' }], metricValues: [{ value: '3' }] }], rowCount: 1, propertyQuota: { tokensPerHour: { remaining: '90' } }, metadata: { dataLossFromOtherRow: true, timeZone: 'America/New_York', currencyCode: 'USD' } }; } }; } });
  const result = await adapter.runFixedReport({ assignment: { status: 'approved', gaPropertyId: '123', gaStreamId: '456' }, reportDefinition: { slug: 'property_summary', metrics: [{ name: 'activeUsers' }] }, period: '28d' });
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /properties\/123:runReport$/);
  assert.equal(JSON.parse(calls[0].options.body).dateRanges[0].startDate, '2026-07-12');
  assert.equal(result.rows.length, 1);
  assert.equal(result.metadata.dataLossFromOtherRow, true);
  assert.equal(result.metadata.propertyTimeZone, 'America/New_York');
  assert.equal(result.metadata.currencyCode, 'USD');
  assert.equal(result.request.returnPropertyQuota, true);
});
test('GA4 Admin adapter uses the credential property discovery path', async () => {
  const credential = { async listAccessibleProperties() { return [{ property: 'properties/123', displayName: 'The House of Dental' }]; } };
  assert.deepEqual(await new Ga4AdminAdapter(credential, { enabled: true }).discover(), { status: 'ready', properties: [{ property: 'properties/123', displayName: 'The House of Dental' }] });
});
