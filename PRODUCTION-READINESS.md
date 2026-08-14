# Production readiness: House of Dental analytics platform

Updated August 14, 2026.

## Verified live

| Area | State | Current evidence |
| --- | --- | --- |
| Reporting service | Live, private, and hardened | Cloud Run revision `measurement-reporting-platform-00050-kw8` serves 100% of traffic; authenticated `/ready` returns 200 and unauthenticated requests are rejected. Startup, database-aware readiness, and liveness probes are active. Its distroless Debian 13 image passed the guarded release scan with zero critical/high findings. |
| Runtime readiness | Ready | Authenticated `/health` and `/ready` return 200; `/ready` confirms the `measurement` database and production migration. |
| GA4 connection | Active read-only | Managed ADC and the Admin API verify property `549721844`, stream `15427015396`, Measurement ID `G-TC66MQQ0T7`, and timezone `America/New_York`. |
| Persistence | Active | Cloud SQL contains immutable report executions, period snapshots, daily facts, quality status, and stored dashboard snapshots. |
| Scheduling | Active | `measurement-daily-sync` is enabled for 3:15 AM Eastern. A forced run returned 200. |
| Queue/workers | Active | `measurement-sync` dispatches one property job at a time with five attempts and exponential backoff; all five forced fixed-period tasks returned 200. |
| Idempotency | Passed | Repeating the same schedule/period key returned the original successful job and created no duplicate facts. |
| Fixed-period correctness | Passed | `7d`, `28d`, `this_month`, `last_month`, and `90d` are stored independently; active users and sessions are period snapshots, and rates use numerator/denominator. |
| Dashboard/API | Live | Authenticated agency and client views plus stored reporting, health, goals, annotations, and PDF endpoints return 200. Client reports use the same stored semantic layer and hide agency repair details. |
| Tenant isolation | Passed | Five roles, service-layer tenant context, 39 Postgres RLS policies, three database roles, and temporary second-organization read/write denial tests are active. |
| Per-property isolation | Passed | Scheduling enumerates each approved assignment. A synthetic inaccessible property dead-lettered without changing the healthy production assignment, then was removed. |
| End-user identity | Active | Google-signed Cloud Run identities resolve to organization memberships per request; ambiguous dual authentication headers are rejected. Membership changes are role-restricted and audited. |
| Recurring reporting | Deployed, deliberately paused | PDF generation, delivery ledger, idempotency, error handling, a bearer-protected recipient-locked relay, and the dispatcher are deployed. Per operator direction, the only synthetic schedule is disabled, the scheduler is paused, and the prior test attempt failed before provider acceptance with no message ID; no report was sent. |
| OAuth | Live in Google Testing mode | The external Google Auth Platform app, one approved operator test user, a web client, version-pinned Secret Manager credentials, KMS, PKCE, signed state, `analytics.readonly`, refresh-token encryption, revocation, re-consent, and offboarding are active. A separate public callback-only service at revision `measurement-oauth-callback-00009-hpm` exposes liveness, generic database-aware readiness, and the callback while returning 404 for root, docs, private readiness, agency, and API routes. Startup/readiness/liveness probes are active; one warm instance prevents scale-from-zero callback failures and service scale is capped at two. The reporting service remains private. The approved user grant completed and property discovery returned one read-only property. It was not the approved House of Dental property, so the connection was never assigned and was revoked with its encrypted token deleted. Both services use OAuth client-secret version 2; version 1 is disabled in Google Auth Platform and Secret Manager. Public publishing remains gated on approved platform legal pages, an owned authorized domain, Google verification, and an explicit production decision. |
| Retention/deletion | Active | 760-day aggregate, 180-day operations, seven-year audit, and 30-day deletion-grace defaults are active and configurable. A temporary production tenant passed full deletion and cleanup. |
| External sources | Search Console active with partial coverage | Version-pinned secret resolution, approval/audit, daily Ads/Search scheduling, idempotent provenance, privacy-validated call/CRM ingestion, and truthful paid/search/business APIs are live. The House of Dental URL-prefix property is verified, the runtime ADC connection is approved, and a signed production sync returned 200. The property is new and has no approved historical rows, so the API correctly reports unavailable totals and `partial_data`; Ads, call tracking, and CRM remain unconfigured. |
| Database resilience | Regional HA and restore-tested | Cloud SQL is regional, deletion-protected, encrypted-only, connector-only, retains 30 automated backups, and has seven-day point-in-time recovery. The latest successful automated backup was restored to an isolated temporary instance and validated before the target was deleted. Query Insights is enabled without client-address capture. Maintenance uses the stable production channel on Sunday at 06:00 UTC. |
| Secrets and invocation IAM | Active, separated | Database, source, relay, and internal trigger values are in Secret Manager. Trigger versions 1 through 7 are disabled and production plus all four schedulers are pinned to newline-free version 8. Scheduler and Cloud Tasks use the dedicated `measurement-scheduler` invoker; it is the sole direct Cloud Run service-account invoker. The data-reading runtime identity can enqueue work but cannot invoke the service. |
| Monitoring, audit, tracing, and cost | Active | An operator email channel and ten policies cover private-service and public-callback 5xx, multi-region callback unavailability, runtime errors/high latency, Cloud Tasks failures, Scheduler failures, and Cloud SQL availability/disk/CPU. A five-panel service dashboard covers traffic, latency, task attempts, database availability, and callback availability. Privacy-safe OpenTelemetry spans are exported to the Google Telemetry API and visible in Cloud Trace with route templates instead of URLs or query strings. Platform logs retain 180 days; a verified `_Default` sink exclusion prevents OAuth callback query strings from being retained. Admin Read, Data Read, and Data Write audit logging are active for every service with no exempt principals. The sole-project billing account has a $25 monthly budget with 50/90/100/150% thresholds. |
| Supply-chain security | Active | Container Analysis, Container Scanning, Error Reporting, and On-Demand Scanning APIs are enabled. Artifact Registry keeps the ten most recent versions and deletes older versions, whether tagged or untagged, after 30 days. |

## Live data state

The most recent production run completed all five fixed periods. The period boundary is calculated in the verified GA4 property timezone, and the latest complete date is August 13, 2026. The stored trailing-28-day view contains 1 generated lead, 1 appointment request, 4 form starts, 1 technical submission, 6 active users, and 12 sessions. These are small production counts, not inferred business outcomes. No thresholding, schema restrictions, or `(other)` data loss were reported.

The local and guarded Cloud Build suites pass 59 tests, including fail-closed HTTP readiness, the strict trace-label allowlist, all seven named operator-alert decisions, the enabled-Testing/false-public-approval OAuth boundary, encrypted authorization-state persistence, and callback-only route isolation. The authenticated production dashboard includes a read-only OAuth connection control; revision `00050-kw8` returns authenticated readiness with live reporting and database status `ok`, and OAuth status reports zero stored connections after the mismatched test grant was revoked.

Guarded Cloud Build release `a7627a03-6e1f-448a-bf49-85f29e351da4` ran tests, built and pushed one immutable image, performed On-Demand Scanning, blocked on any high/critical finding, deployed both services with service-specific probes, and verified 100% traffic, callback readiness, warm-instance bounds, and paused report dispatch. The scan found 50 low/medium/minimal/unknown findings and zero high/critical findings. A fresh signed five-period schedule then drained to zero; production now contains 64 successful executions and no queued or failed jobs. `measurement-report-dispatch` remained paused and was not invoked. Cloud Trace contains sanitized schedule, worker, readiness, health, and callback spans whose labels omit URLs, query strings, headers, bodies, and exception messages.

Step 52 now detects every named condition: stopped tracking from a nontrivial prior week, a guardrailed 28-day generated-lead drop, missing/unapproved/superseded contracts, failed syncs, GA4 authorization failures, more than 36 hours without a successful website sync, and GA4 report-definition incompatibility. Healthy evaluations resolve persistent conditions; temporal and contract conditions are also computed at read time so a stopped scheduler cannot hide staleness.

The callback uptime check validates HTTPS, HTTP 200, and JSON status `ok` every minute from Iowa, Oregon, and Virginia. All three regions returned passing samples after provisioning. Alert policy `7680340487027202507` opens when at least two regions fail for two minutes; the existing callback 5xx policy remains independently active.

The callback briefly returned three platform-generated 500 responses when no scale-from-zero instance was available. Service-level minimum scale is now one and maximum scale remains two; all three uptime regions returned passing samples after the correction.

Cloud SQL backup `1786662000000`, completed at `2026-08-14T01:09:58Z`, passed an isolated restore drill. The recovered PostgreSQL 18 database contained all eight migrations, 39 tenant RLS policies, the three separated database roles, one organization/company/website/assignment, 44 report executions, and 220 report snapshots. Deterministic hashes for the execution and snapshot tables matched production exactly. Creation, restore, and deletion operations all completed without errors, and temporary instance `measurement-db-restore-drill-20260813` no longer exists.

## Approved governance and live evidence

Shashaank Shankar approved the healthcare/privacy policy, consent configuration, eleven public routes, measurement contract, mappings, and downstream request semantics on August 12, 2026. The live site now fails closed before analytics initialization when URL data is unsafe. The GA4 Realtime API observed exactly one each of `form_start`, `cta_click`, and `phone_click` during the approval run.

The Phase 1 live measurement gate is passed:

- authorized consented production tests were accepted by Resend and the live form displayed its success state, including a fresh one-request test on August 13, 2026;
- the GA4 Realtime Data API observed exactly one each of `form_submit`, `generate_lead`, and `appointment_request` at `minutesAgo=00`;
- the deployed anchor allowlist permits the site's static `#book` fragment while unknown fragments still fail closed before analytics initialization;
- the rendered site has no mailto CTA, so `email_click` is not currently applicable;
- inbox placement remains unverified without access to the dental office inbox and is tracked separately from provider acceptance.

The dashboard reports governance as approved. Code, fixtures, runtime integration tests, and live Data API receipts jointly prove the applicable Phase 1 event path.

## Operator access

The service is not public. Start an authenticated local proxy:

```sh
gcloud run services proxy measurement-reporting-platform \
  --region us-central1 \
  --project web-analytics-agency-prod
```

Open `http://127.0.0.1:8080/agency` for operations or `/dashboard` for the simplified client view.

Rollback is performed by routing Cloud Run traffic to the prior ready revision. Database migrations are additive; restore uses Cloud SQL backup or point-in-time recovery when data recovery is required. The tested isolated process is documented in `infra/gcp/RESTORE-RUNBOOK.md`.
