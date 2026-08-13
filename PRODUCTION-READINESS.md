# Production readiness: House of Dental analytics platform

Updated August 12, 2026.

House of Dental is the first live website on the platform. The website launch and the reporting-platform launch are separate states.

| Area | Current state | Evidence boundary |
| --- | --- | --- |
| Website | Live | `https://thehouseofdentalwp.com` and all 11 declared routes returned HTTP 200. |
| Hosting | Cloudflare Worker Static Assets | Public Cloudflare headers and the website repository configuration match. Account ownership and secret state were not changed. |
| GA4 browser collection | Live after consent | A public browser received HTTP 204 for Measurement ID `G-TC66MQQ0T7`. This does not prove receipt in the intended property. |
| GA4 identifiers | Reconciled in source | Property `549721844`, stream `15408312790`, Measurement ID `G-TC66MQQ0T7`. The former dashboard stream value was stale and has been removed. |
| GA4 reporting connection | Active locally | The FastAPI runtime uses external Application Default Credentials and the official Python GA4 Data client to read property `549721844` directly. Live requests return known overview and acquisition fields; empty GA4 results remain empty rather than becoming fabricated values. Deployment-specific credentials still require separate verification. |
| Privacy/governance | Open | The route and consent implementation is fail-closed for unknown routes and excludes prohibited fields, but a named healthcare/privacy approval record was not found. |
| Contact delivery | Partially verified | The production endpoint boundary is live; no production form was submitted, so Resend acceptance and inbox placement are unverified. |
| Cloudflare Web Analytics | Cleanup required | Cloudflare injects a beacon that the repository CSP blocks. Disable it unless separately approved. |
| Dashboard HTTP security | Implemented locally | Live mode now requires a strong bearer token and defaults to loopback binding. External identity, TLS termination, and secret provisioning remain deployment responsibilities. |
| Persistence and sync | Direct-query mode | The dashboard refreshes from GA4 on request. The Postgres schema and sync runner are not active, so there is no persisted historical sync, scheduled refresh, or freshness monitor yet. |

## Launch sequence for the reporting platform

1. Record the client/authorized owner, privacy/legal decision, consent approval, and approved reporting scope.
2. Confirm the GA4 property, stream, Measurement ID, and property timezone in the client-owned GA4 account.
3. Grant the agency reporting principal Viewer access with `analytics.readonly`; keep credentials outside the repository.
4. If persistent historical reporting is required, provision managed Postgres, apply `infra/postgres/001_core.sql`, and create the House of Dental assignment with explicit effective dates. Direct-query reporting is already available without this step.
5. Provision a strong `PLATFORM_API_TOKEN`, set the live-mode variables, and expose the service only behind managed TLS and access controls.
6. Before enabling persistence, run a read-only GA4 reconciliation, persist report executions, and verify fixed periods, non-additive metrics, provenance, quotas, and tenant isolation.
7. Verify every implemented event in DebugView and confirm that no prohibited data, query values, or form contents arrive.
8. Add sync scheduling, failure alerts, freshness monitoring, backup/restore evidence, and a rollback procedure before declaring the reporting platform live.

Do not represent the platform as live reporting until steps 1–8 have evidence. The public website can remain live while these server-side gates are completed.
