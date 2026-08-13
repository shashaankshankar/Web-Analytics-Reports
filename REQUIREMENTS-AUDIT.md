# Requirement audit

Updated August 13, 2026. This is the evidence ledger for `Measurement and Reporting Platform.md`. A later stage is not treated as authorization to bypass an earlier hard gate.

## Current release boundary

| Scope | Status | Evidence or blocker |
| --- | --- | --- |
| Steps 1-6 | Implemented and approved | Shashaank Shankar approved the healthcare/privacy policy, consent, eleven explicit public routes, contract, mappings, and request semantics on August 12, 2026. Unknown routes and unimplemented route patterns remain prohibited. |
| Steps 7-9 | Implemented and live | The live site has one collection owner, advanced Consent Mode v2, approved semantic events, and a validated Resend request handoff. |
| Steps 10-13 | Implemented and observed live for all applicable events | The 35-check site suite passes, including a runtime integration test proving a consented successful response emits exactly one each of `form_submit`, `generate_lead`, and `appointment_request` with only allowlisted parameters. Live unsafe-URL and unsafe-fragment checks initialized no analytics. The GA4 Realtime API observed exactly one each of `form_start`, `cta_click`, `phone_click`, `form_submit`, `generate_lead`, and `appointment_request`. No mailto CTA exists, so `email_click` is not applicable. |
| Step 14 | Live | Measurement health exposes contract, stream, measurement ID, timezone, event, consent/governance, persistence, and collection states. |
| Phase 1 gate | **Passed** | Governance, routes, consent, semantics, privacy fixtures, all six applicable event receipts, and the production Resend handoff pass. `email_click` is documented as not applicable. |
| Steps 15-39 | Live | FastAPI, Cloud SQL schemas, versioned semantics, ADC credential abstraction, Admin/Data API adapters, fixed reports, exact provenance, daily/period facts, Scheduler, Tasks, workers, retries, replay/dead-letter state, idempotency, reconciliation, and data-quality states are deployed. |
| Phase 2 gate | Passed for the first-site backend | A repeated production job returned `idempotentReplay: true`; all six report definitions pass GA4 compatibility; all facts link to executions; failed validation jobs replayed; quota, thresholding, restrictions, dimension loss, freshness, and errors are visible. Cross-property failure isolation remains a multi-client test. |
| Steps 40-49 | Live | Stored product APIs and a private dashboard expose overview, fixed periods/comparisons, acquisition, conversion, landing pages, expected events, measurement health, and sync status. |
| Phase 3 gate | Passed for available approved metrics | Dashboard values resolve to stored snapshots, report executions, report version 1, and exact GA4 requests. Outcome metrics remain zero/unapproved rather than being inferred. |
| Step 50 | Complete | `ONBOARDING.md` documents the repeatable client onboarding path and hard gates. |
| First production milestone | **Passed** | One real client has approved privacy-safe measurement, complete applicable live event evidence, managed GA4 access, idempotent persisted synchronization, fixed-period reporting, a private dashboard, visible health, and documented onboarding. |
| Steps 51-57 | In progress | Portfolio, alert, annotation, role, and RLS foundations exist. The milestone gate is now open; full HTTP/service/database tenant isolation and adversarial acceptance remain required. |
| Steps 58-62 | Gated | Client roles, portal, goals, recurring reports, and OAuth are prohibited before Phase 4 passes. |
| Steps 63-68 | Gated and dependency-blocked | No approved Google Ads, Search Console, call-tracking, CRM/booking, outcome-matching, or revenue-source authorization/configuration exists. The plan forbids inventing these integrations. |
| Step 69 | Not justified | BigQuery raw-event analytics is optional and the plan says to add it only when aggregate reporting is insufficient. |

## Live production evidence

- Cloud Run revision `measurement-reporting-platform-00010-vd5` serves 100% of traffic and rejects unauthenticated requests with HTTP 403.
- `/ready` reports the `measurement` Cloud SQL database migrated and ready.
- GA4 Admin API verifies property `549721844`, web stream `15427015396`, measurement ID `G-TC66MQQ0T7`, and timezone `America/New_York`.
- Cloud Scheduler and Cloud Tasks completed all five fixed-period jobs on revision `00010-vd5` with HTTP 200; queue and failed-job counts returned to zero.
- Period boundaries use the GA4 property timezone. At the verification time, the latest complete date was `2026-08-11`; current-local-day activity is excluded.
- Re-dispatching the exact `28d` schedule key returned `idempotentReplay: true` for job `dbb74a68-014c-57d7-b30f-a0679d7bc5c5`.
- Source requests and metadata are stored on every new execution. Daily property, channel, page, and event facts are populated when rows exist. All seven mappings are approved; canonical facts populate only from observed source events.
- Cloud SQL backups, point-in-time recovery, seven retained backups, and seven days of transaction logs are enabled.
- On August 12, 2026, the explicitly authorized production test returned the visible success state `Your message was sent. We'll get back to you soon.` after the Worker received an accepted Resend response. The message stated that it was a measurement test to disregard and did not create or confirm an appointment. Inbox placement was not independently verified.
- On August 13, 2026, an explicitly authorized consented production test returned the same success state. The GA4 Realtime Data API then returned exactly one each of `form_submit`, `generate_lead`, and `appointment_request` at `minutesAgo=00`.
- Cloudflare Worker version `aae99152-f821-4aec-bf3c-0db2ebb533ef` explicitly allows approved static fragments such as `/contact#book` but continues to fail closed for unknown fragments such as `#patient-12345`.

## Additional operational evidence

1. Inbox placement remains a separate office-inbox check; Resend acceptance is already proven.
2. DebugView/Tag Assistant parameter inspection is optional additional UI evidence because the live Data API receipt plus code and fixture proof cover the Phase 1 gate.

These items do not reopen the passed Phase 1 gate.
