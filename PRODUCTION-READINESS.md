# Production readiness: House of Dental analytics platform

Updated August 13, 2026.

## Verified live

| Area | State | Current evidence |
| --- | --- | --- |
| Reporting service | Live and private | Cloud Run revision `measurement-reporting-platform-00013-rtj` serves 100% of traffic; unauthenticated requests return 403. |
| Runtime readiness | Ready | Authenticated `/health` and `/ready` return 200; `/ready` confirms the `measurement` database and production migration. |
| GA4 connection | Active read-only | Managed ADC and the Admin API verify property `549721844`, stream `15427015396`, Measurement ID `G-TC66MQQ0T7`, and timezone `America/New_York`. |
| Persistence | Active | Cloud SQL contains immutable report executions, period snapshots, daily facts, quality status, and stored dashboard snapshots. |
| Scheduling | Active | `measurement-daily-sync` is enabled for 3:15 AM Eastern. A forced run returned 200. |
| Queue/workers | Active | `measurement-sync` dispatches one property job at a time with five attempts and exponential backoff; all five forced fixed-period tasks returned 200. |
| Idempotency | Passed | Repeating the same schedule/period key returned the original successful job and created no duplicate facts. |
| Fixed-period correctness | Passed | `7d`, `28d`, `this_month`, `last_month`, and `90d` are stored independently; active users and sessions are period snapshots, and rates use numerator/denominator. |
| Dashboard/API | Live | Authenticated agency and client views plus stored reporting, health, goals, annotations, and PDF endpoints return 200. Client reports use the same stored semantic layer and hide agency repair details. |
| Tenant isolation | Passed | Five roles, service-layer tenant context, 28 Postgres RLS policies, three database roles, and temporary second-organization read/write denial tests are active. |
| Database resilience | Active | Cloud SQL has automated backups and point-in-time recovery enabled with seven-day retention. |
| Secrets | Active | Database and internal trigger values are in Secret Manager. Exposed trigger versions 1 and 2 are disabled; production is pinned to version 3. |

## Live data state

The most recent production run completed all five fixed periods. The period boundary is calculated in the verified GA4 property timezone, so the latest complete date is August 11, 2026. Those complete windows currently contain no rows. Current-day Realtime validation separately observed all six applicable measurement events; fixed-period snapshots correctly exclude that incomplete local day. No thresholding, schema restrictions, or `(other)` data loss were reported.

These are small live counts, not a claim that every measurement event has been manually validated.

## Approved governance and live evidence

Shashaank Shankar approved the healthcare/privacy policy, consent configuration, eleven public routes, measurement contract, mappings, and downstream request semantics on August 12, 2026. The live site now fails closed before analytics initialization when URL data is unsafe. The GA4 Realtime API observed exactly one each of `form_start`, `cta_click`, and `phone_click` during the approval run.

The Phase 1 live measurement gate is passed:

- an authorized consented production test was accepted by Resend and the live form displayed its success state;
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
