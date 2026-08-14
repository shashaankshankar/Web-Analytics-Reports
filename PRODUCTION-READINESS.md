# Production readiness: House of Dental analytics platform

Updated August 13, 2026.

## Verified live

| Area | State | Current evidence |
| --- | --- | --- |
| Reporting service | Live, private, and hardened | Cloud Run revision `measurement-reporting-platform-00039-tl7` serves 100% of traffic; authenticated readiness returns 200 and unauthenticated requests return 403. Its distroless Debian 13 image was scanned after deployment and reports zero critical/high findings and zero fixable critical/high findings. |
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
| OAuth | Live in Google Testing mode | The external Google Auth Platform app, one approved operator test user, a web client, version-pinned Secret Manager credentials, KMS, PKCE, signed state, `analytics.readonly`, refresh-token encryption, revocation, re-consent, and offboarding are active. The live status reports `configured: true`, `enabled: true`, and `productionApproved: false`; public publishing remains gated on approved platform legal pages, an owned authorized domain, Google verification, and an explicit production decision. No user grant has been completed yet. |
| Retention/deletion | Active | 760-day aggregate, 180-day operations, seven-year audit, and 30-day deletion-grace defaults are active and configurable. A temporary production tenant passed full deletion and cleanup. |
| External sources | Search Console active with partial coverage | Version-pinned secret resolution, approval/audit, daily Ads/Search scheduling, idempotent provenance, privacy-validated call/CRM ingestion, and truthful paid/search/business APIs are live. The House of Dental URL-prefix property is verified, the runtime ADC connection is approved, and a signed production sync returned 200. The property is new and has no approved historical rows, so the API correctly reports unavailable totals and `partial_data`; Ads, call tracking, and CRM remain unconfigured. |
| Database resilience | Regional HA | Cloud SQL is regional, deletion-protected, encrypted-only, connector-only, retains 30 automated backups, and has seven-day point-in-time recovery. Query Insights is enabled without client-address capture. Maintenance uses the stable production channel on Sunday at 06:00 UTC. |
| Secrets and invocation IAM | Active, separated | Database, source, relay, and internal trigger values are in Secret Manager. Trigger versions 1 through 5 are disabled and production is pinned to version 6. Scheduler and Cloud Tasks use the dedicated `measurement-scheduler` invoker; it is the sole direct Cloud Run service-account invoker. The data-reading runtime identity can enqueue work but cannot invoke the service. |
| Monitoring, audit, and cost | Active | An operator email channel and eight policies cover Cloud Run 5xx/runtime errors/high latency, Cloud Tasks failures, Scheduler failures, Cloud SQL availability/disk/CPU. A four-panel service dashboard covers traffic, latency, task attempts, and database availability; platform logs retain 180 days. Admin Read, Data Read, and Data Write audit logging are active for every service with no exempt principals. The sole-project billing account has a $25 monthly budget with 50/90/100/150% thresholds. |
| Supply-chain security | Active | Container Analysis, Container Scanning, Error Reporting, and On-Demand Scanning APIs are enabled. Artifact Registry keeps the ten most recent versions and deletes untagged images after 30 days. |

## Live data state

The most recent production run completed all five fixed periods. The period boundary is calculated in the verified GA4 property timezone, and the latest complete date is August 12, 2026. The stored trailing-28-day view contains 2 generated leads, 2 appointment requests, 3 form starts, 2 technical submissions, 5 active users, and 10 sessions. These are small production counts, not inferred business outcomes. No thresholding, schema restrictions, or `(other)` data loss were reported.

The local suite passes 49 tests, including the enabled-Testing/false-public-approval OAuth boundary and encrypted authorization-state persistence. The authenticated production dashboard was rendered after the earlier external-source release with no browser-console errors; revision `00039-tl7` preserves those APIs and returns authenticated readiness with live reporting and database status `ok`.

After the OAuth deployment, forced runs of `measurement-daily-sync`, `measurement-retention`, and `measurement-external-source-sync` each completed with HTTP 200 and the Cloud Tasks queue drained to zero. `measurement-report-dispatch` remained paused and was not invoked. On-Demand Scanning result `aa2234d7-72e1-4953-9e69-9209c9ec0cea` for image tag `da35e67` found no critical or high vulnerabilities.

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

Rollback is performed by routing Cloud Run traffic to the prior ready revision. Database migrations are additive; restore uses Cloud SQL point-in-time recovery when data recovery is required.
