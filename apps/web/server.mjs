import http from 'node:http';
import { createTenantContext } from '../../packages/authorization/src/index.mjs';
import { overview, health, syncStatus, acquisition, portfolio } from '../../services/reporting/src/index.mjs';
import { firstSite } from '../../services/reporting/src/site-store.mjs';
import { liveEnabled, liveOverview, liveAcquisition, liveContext } from '../../services/reporting/src/live.mjs';
import { assertProductionSecurity, requestAuthorized } from './security.mjs';

assertProductionSecurity(process.env);
const context = createTenantContext({ organizationId: firstSite.organizationId, companyIds: [firstSite.companyId], websiteIds: [firstSite.websiteId], role: 'agency_admin' });
const securityHeaders = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer' };
const json = (response, status, body) => { response.writeHead(status, { ...securityHeaders, 'content-type': 'application/json; charset=utf-8' }); response.end(JSON.stringify(body, null, 2)); };
const html = `<!doctype html><meta charset="utf-8"><title>Measurement Platform</title><style>body{font:16px system-ui;margin:3rem;max-width:72rem;color:#172033}h1{margin-bottom:.25rem}.notice{padding:1rem;background:#fff5db;border-radius:.5rem}code{background:#eef2f7;padding:.15rem .3rem}</style><h1>Measurement & Reporting Platform</h1><p>Agency operations console — ${liveEnabled() ? 'live GA4 reporting' : 'first-site setup mode'}</p><p><strong>${firstSite.company}</strong> is the first live website on the platform: <a href="${firstSite.canonicalDomain}">${firstSite.canonicalDomain}</a>.</p><p class="notice">${liveEnabled() ? 'Live GA4 reporting is enabled for the approved production assignment.' : 'Public GA4 collection is live, but server-side reporting remains disabled until the read-only connection and governance gates are complete. Unavailable data is never represented as zero.'}</p><h2>Available endpoints</h2><ul><li><code>/api/companies/${firstSite.companyId}/overview?period=28d</code></li><li><code>/api/websites/${firstSite.websiteId}/acquisition?period=28d</code></li><li><code>/api/websites/${firstSite.websiteId}/measurement-health</code></li><li><code>/api/websites/${firstSite.websiteId}/sync-status</code></li><li><code>/api/portfolio/summary</code></li></ul>`;
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost'); const period = url.searchParams.get('period') ?? '28d';
  try {
    if (!requestAuthorized(request, process.env)) return json(response, 401, { error: 'unauthorized' });
    if (url.pathname === '/') { response.writeHead(200, { ...securityHeaders, 'content-type': 'text/html; charset=utf-8' }); return response.end(html); }
    if (url.pathname === '/api/portfolio/summary') return json(response, 200, portfolio(context));
    if (url.pathname === `/api/companies/${firstSite.companyId}/overview`) return json(response, 200, liveEnabled() ? await liveOverview(liveContext(), firstSite.websiteId, period) : overview(context, firstSite.websiteId, period));
    if (url.pathname === `/api/websites/${firstSite.websiteId}/acquisition`) return json(response, 200, liveEnabled() ? await liveAcquisition(liveContext(), firstSite.websiteId, period) : acquisition(context, firstSite.websiteId, period));
    if (url.pathname === `/api/websites/${firstSite.websiteId}/measurement-health`) return json(response, 200, health(context, firstSite.websiteId));
    if (url.pathname === `/api/websites/${firstSite.websiteId}/sync-status`) return json(response, 200, syncStatus(context, firstSite.websiteId));
    return json(response, 404, { error: 'not_found' });
  } catch (error) { return json(response, error.message.startsWith('forbidden') ? 403 : 400, { error: error.message }); }
});
const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST || '127.0.0.1';
server.listen(port, host, () => console.log(`Measurement platform listening on http://${host}:${port}`));
