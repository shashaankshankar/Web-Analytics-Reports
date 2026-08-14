# Measurement and Reporting Platform
## Step-by-Step Implementation Plan

## Current implementation status — August 14, 2026

House of Dental is the first live website. The reporting service is deployed privately on Cloud Run and uses the official Python GA4 Data API client with read-only Application Default Credentials (ADC).

- Runtime: `app/main.py` served by Uvicorn; no Node.js runtime remains.
- Production views: tenant-scoped `GET /dashboard` and agency-only `GET /agency`; interactive endpoint documentation: `GET /docs`.
- Service liveness: `GET /health` and `GET /healthz`; neither proves GA4 access.
- Production access is private and enforced by Cloud Run IAM; token authentication remains available for local operation.
- Cloud Scheduler creates five fixed-period jobs through Cloud Tasks. Workers query GA4, preserve execution metadata and quota state, and persist versioned facts/snapshots in Cloud SQL Postgres.
- The GA4 Admin API verifies the property, active web stream, Measurement ID, key-event inventory, and property timezone. Fixed periods are calculated in that property timezone.
- Dashboard requests read stored snapshots. Correct period comparisons, retries, freshness, and data-quality states are active; exact job retries are idempotent.
- The reporting infrastructure is live. Shashaank Shankar approved the healthcare/privacy policy, consent configuration, explicit public routes, contract, mappings, and request semantics on August 12, 2026. On August 13, 2026, the GA4 Realtime Data API observed exactly one each of all six applicable first-site events, including the three post-success events after an authorized consented Resend handoff.
- The Phase 4 gate passes: HTTP and service tenant context, three database roles, and adversarial two-organization tests deny cross-tenant reads and writes. The initial 28 RLS policies have expanded to 39 as later tenant data was added. Client roles, the simplified portal, approved effective-dated goals, and PDF reports from stored snapshots are live.
- Phase 5 control-plane implementation is deployed: per-user Google identity resolution, memberships, recurring-report scheduling/delivery state, KMS-backed OAuth, revocation/offboarding, configurable retention, and tested delayed deletion. The email relay is configured but dispatch is deliberately paused by operator instruction. Google Auth Platform is live in External/Testing mode with one approved operator test user, exactly `analytics.readonly`, a Secret Manager-backed web client, a private-console connection control, and a separately deployed public callback-only service. The user grant, signed state, PKCE, token exchange, database persistence, KMS encryption, and read-only property discovery all passed. The discovered property was not House of Dental's approved property, so it was never assigned and the mismatched connection was revoked with its encrypted token deleted while the existing ADC assignment stays active. The OAuth client secret was rotated without downtime, with version 2 active and version 1 disabled. Public production publishing remains gated on approved legal pages, an owned authorized domain, Google verification, and an explicit production decision.
- Phase 6/7 storage, approval, secret-resolution, scheduling, ingestion, provenance, and truthful reporting APIs are deployed. Google Ads and Search Console have an isolated daily worker path; call tracking and CRM/booking have privacy-validated, idempotent outcome ingestion. The House of Dental URL-prefix property is verified for Search Console, the version-pinned ADC connection is approved, and the first signed production sync completed on August 13, 2026. Search Console truthfully reports `partial_data` with no approved historical rows yet; Google Ads, call tracking, and CRM/booking remain `not_configured`, and the product returns unavailable values rather than inventing cost, outcome, appointment, customer, or revenue data.
- Google-managed production controls are active: regional Cloud SQL HA with 30 backups, seven-day PITR, a passed isolated restore drill, connector-only encrypted access, a stable maintenance window, separated runtime/invoker/builder identities, no user-managed service-account keys, all-service Data Access audit logs, 180-day platform logs, privacy-safe OpenTelemetry traces in Cloud Trace, a three-region public-callback uptime probe, all seven named application alert conditions, monitoring and alerting, a billing budget, KMS rotation, and container scanning/cleanup. Post-deploy readiness and all five reporting worker periods pass; report dispatch remains paused.
- `REQUIREMENTS-AUDIT.md` records the live evidence and the hard-gated work that cannot proceed without authorized approvals and source-system configuration.

Start locally with:

```sh
.venv/bin/python -m uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:3000/dashboard`. Production operators use the authenticated Cloud Run proxy documented in `README.md`.

### Objective

Build the smallest production-ready version of the measurement and reporting platform around the first live website before expanding it into a broader agency platform.

The first milestone is reached when one client website has:

- An approved measurement contract
- Privacy-safe GA4 instrumentation
- A managed read-only GA4 connection
- Reliable synchronization into Postgres
- Correct fixed-period reporting for additive and non-additive metrics
- A working dashboard
- Visible measurement and sync health
- A repeatable onboarding process for client #2

This follows the architecture's stated first production milestone.

---

# Stage 1: Lock the First-Site Scope

## Step 1. Select the first website

Choose exactly one real website to use for the entire first implementation.

Record:

- Company
- Website/domain
- GA4 property
- GA4 web stream
- Whether the business is HIPAA-regulated
- Existing analytics implementation
- Existing forms
- Phone CTAs
- Email CTAs
- Appointment/request flows
- Existing consent mechanism
- Existing GTM or gtag installation

### Deliverable

`first-site.md`

This becomes the implementation reference for Phases 0 through 3.

### Gate

Do not start building the generalized backend against multiple clients.

The architecture explicitly requires one fully verified first website before platform expansion.

---

# Stage 2: Define the Measurement Contract

This is the most important part of the implementation because every later report depends on these definitions.

## Step 2. Create `local_service_v1`

Create the first versioned measurement contract.

Recommended structure:

```text
measurement/
  contracts/
    local_service_v1/
      contract.json
      events.json
      parameters.json
      mappings.json
      validation.json
```

The contract should define:

- Event names
- Exact firing conditions
- Allowed parameters
- Parameter types
- Allowed parameter values
- Prohibited parameters
- Event collection owner
- Consent requirements
- Business interpretation
- Validation method
- Industry restrictions

The contract itself should be treated as executable configuration, not merely documentation.

---

## Step 3. Define the V1 event taxonomy

Implement these initial events:

```text
form_start
form_submit
generate_lead
phone_click
email_click
appointment_request
cta_click
```

Keep the meanings strict.

### `form_start`

Fire when the user first interacts with an eligible form.

Do not fire repeatedly for every field.

### `form_submit`

Fire when the website accepts the submission.

This means:

> technical submission succeeded

It does not automatically mean:

> business lead generated

### `generate_lead`

Fire only when a valid lead has actually been successfully received.

### `phone_click`

Fire when the user activates a tracked telephone CTA.

Do not automatically count this as a lead.

### `email_click`

Fire when a tracked email CTA is activated.

### `appointment_request`

Fire when an approved appointment-request action successfully completes.

### `cta_click`

Use for approved non-contact CTAs.

Keep later business stages outside GA4 where appropriate:

```text
qualified_lead
appointment_booked
customer
revenue
```

Those belong primarily to CRM, booking, payment, or other approved first-party systems.

---

# Stage 3: Define Privacy and Analytics Eligibility

## Step 4. Create route-level analytics eligibility

Every page or template must receive one of:

```text
approved
prohibited
requires_review
```

Create something similar to:

```text
measurement/
  eligibility/
    routes.json
```

Example:

```json
{
  "/": "approved",
  "/about": "approved",
  "/services/*": "requires_review",
  "/contact": "requires_review",
  "/appointment/*": "requires_review",
  "/patient/*": "prohibited",
  "/portal/*": "prohibited"
}
```

For healthcare websites, unknown routes must behave as prohibited.

The system should fail closed.

```text
unknown route
    ↓
no analytics
```

Not:

```text
unknown route
    ↓
analytics allowed
```

This behavior is a core release requirement.

---

## Step 5. Build the prohibited-data rules

Create a denylist covering at minimum:

```text
name
email
personal phone number
form contents
patient identifier
appointment identifier
patient-specific data
treatment/condition details where prohibited
```

Also inspect:

```text
URLs
query parameters
page titles
event parameters
custom dimensions
campaign parameters
site-search terms
```

The absence of an obvious `email` parameter is not enough. Information can leak through URLs and metadata.

---

## Step 6. Create privacy validation fixtures

Create automated examples that should pass and fail.

Example:

```text
PASS
/implants
event = phone_click
location = hero

FAIL
/contact?email=john@example.com

FAIL
event = generate_lead
email = john@example.com

FAIL
/patient/12345

FAIL
unclassified route loads GA4
```

These fixtures will later become automated tests.

---

# Stage 4: Implement the Website Tracking Layer

## Step 7. Standardize analytics attributes

Replace page-specific analytics code with semantic HTML attributes.

Example:

```html
<a
  href="tel:+14075551234"
  data-analytics-event="phone_click"
  data-analytics-location="hero"
>
  Call now
</a>
```

Use consistent attributes across the entire site.

Avoid custom JavaScript such as:

```javascript
trackHeroButton()
trackHeaderPhoneNumber()
trackFooterPhone()
```

Each should resolve through the same analytics system.

The architecture explicitly calls for one central adapter instead of page-specific tracking implementations.

---

## Step 8. Build the central analytics adapter

Create one module responsible for every website analytics event.

For example:

```text
analytics/
  index.ts
  consent.ts
  eligibility.ts
  events.ts
  validation.ts
  sanitize.ts
  deduplicate.ts
  transport.ts
  debug.ts
```

### Processing pipeline

Every analytics event should follow:

```text
User interaction
      ↓
Analytics adapter
      ↓
Is this route analytics-eligible?
      ↓
Is required consent present?
      ↓
Is event allowed?
      ↓
Are parameters allowed?
      ↓
Sanitize URL/data
      ↓
Duplicate check
      ↓
Dispatch
      ↓
gtag / GTM
```

If any validation step fails:

```text
DO NOT SEND
```

---

## Step 9. Establish collection ownership

Audit GA4 Enhanced Measurement, GTM, and website-side collection.

For each event, record exactly one owner.

Example:

| Event | Owner |
|---|---|
| page_view | GA4 Enhanced Measurement |
| phone_click | website adapter |
| form_start | website adapter |
| form_submit | website adapter |
| generate_lead | website adapter |

Do not allow:

```text
website adapter → phone_click
GTM rule → phone_click
GA4 automatic tracking → equivalent phone event
```

unless there is a deliberate reason and documented reconciliation behavior.

Duplicate logical events are explicitly prohibited by the architecture.

---

# Stage 5: Validate the First-Site Instrumentation

## Step 10. Test every event manually

Test:

```text
form_start
form_submit
generate_lead
phone_click
email_click
appointment_request
cta_click
```

For each event verify:

- Exactly one event fires
- Correct event name
- Correct parameters
- No unexpected parameters
- Correct success condition
- No event fires prematurely

Use GA4 DebugView and Tag Assistant as validation tools.

---

## Step 11. Test consent states

Test every supported consent state.

At minimum verify:

```text
No consent
Consent granted
Consent denied
Consent changed
```

Confirm that behavior matches the approved policy.

---

## Step 12. Test prohibited routes

Automate checks proving analytics cannot load on:

```text
prohibited routes
requires_review routes
unknown routes
unclassified routes
```

unless explicitly approved.

---

## Step 13. Test URL leakage

Exercise:

```text
forms
search
booking
campaign links
query parameters
redirects
error states
confirmation states
```

Inspect what GA4 actually receives.

Do not rely only on reading the source code.

---

## Step 14. Establish measurement-health checks

Create the first health-check service.

Track:

```text
GA4 stream exists
recent events received
required events present
unexpected events absent
prohibited events absent
contract version current
lead event activity
consent configuration recorded
property timezone matches
last successful validation
```

The platform is intended to monitor measurement health, not just whether the GA4 API responds.

---

# Phase 1 Gate

Stop here and validate the implementation.

Proceed only when all of the following are true:

- Expected events appear
- Parameters are correct
- No duplicates exist
- No prohibited data appears
- Consent behaves correctly
- Routes fail closed
- Lead semantics match the site's actual success condition

These are the architecture's Phase 1 release conditions.

---

# Stage 6: Create the Backend Project

Only begin this after the measurement implementation passes.

## Step 15. Establish the application structure

Recommended architecture:

```text
app/
  main.py
  ga4.py
  config.py
  privacy.py

measurement/
  contracts/
  eligibility/
  privacy/
  sites/

infra/
tests/
```

Use the architecture's production stack:

```text
FastAPI
Python
Postgres
Python workers
Managed queue
Managed scheduler
Secret management
Central observability
```

A Google Cloud deployment may use:

```text
Cloud Run
Cloud SQL
Cloud Scheduler
Cloud Tasks / Pub/Sub
Secret Manager / KMS
```

while keeping application interfaces portable.

---

# Stage 7: Implement the Core Database

## Step 16. Create logical Postgres schemas

Start with:

```sql
app
analytics
audit
```

### `app`

Application/control data.

### `analytics`

Aggregate analytics facts and data-quality state.

### `audit`

Administrative and security history.

Dashboard queries should eventually read Postgres rather than synchronously requesting Google.

---

## Step 17. Implement tenant entities

Create:

```text
organizations
companies
websites
users
memberships
```

Relationship:

```text
organization
  ↓
company
  ↓
website
```

Do not treat a GA4 property as equivalent to a website.

---

## Step 18. Implement analytics connections

Create:

```text
analytics_connections
ga_properties
ga_data_streams
```

An `analytics_connection` represents authentication only.

It must not determine website ownership.

---

## Step 19. Implement website analytics assignments

Create:

```text
website_analytics_assignments
```

Required fields:

```text
id
website_id
analytics_connection_id
ga_property_id
ga_stream_id
reporting_scope
effective_from
effective_to
status
```

This entity becomes the reporting boundary.

Facts should reference:

```text
assignment_id
```

rather than inferring ownership from:

```text
property_id
```

This distinction is central to the architecture.

---

# Stage 8: Implement Versioned Semantics

## Step 20. Create measurement-definition tables

Implement:

```text
measurement_contracts
measurement_contract_versions
website_measurement_contract_assignments
event_definitions
event_mappings
metric_definitions
metric_definition_versions
report_definitions
report_definition_versions
client_goals
annotations
```

Definitions must be versioned.

Do not mutate historical meanings in place.

---

## Step 21. Implement client-specific event mappings

Example:

```text
Website A
generate_lead
    ↓
leads
```

Another approved client might use:

```text
appointment_request
    ↓
leads
```

Mappings need:

```text
effective date
version
approval state
```

This allows historical reports to retain the semantic definition used at the time.

---

# Stage 9: Implement Metric Aggregation Rules

## Step 22. Encode aggregation behavior

Every metric definition receives one behavior:

```text
SUM
RATIO
WINDOWED_UNIQUE
SNAPSHOT
FUNNEL
EXTERNAL
```

Examples:

### SUM

```text
event count
lead count
compatible revenue
```

### RATIO

```text
engagement rate
conversion rate
```

Recompute using numerator and denominator.

Never average daily percentages.

### WINDOWED_UNIQUE

```text
users
active users
period sessions where treated as period totals
```

Request the complete reporting period from GA4.

Never sum daily unique counts.

### FUNNEL

Use only approved funnel definitions.

### EXTERNAL

Use CRM, booking, payment, or another owning external system.

The aggregation contract is necessary to prevent mathematically invalid dashboard metrics.

---

# Stage 10: Build the GA4 Connector

## Step 23. Define the internal credential interface

Implement a Python protocol or concrete credential provider with equivalent responsibilities:

```python
class AnalyticsCredential(Protocol):
    def get_authorized_client(self): ...
    def validate_access(self) -> bool: ...
    def list_accessible_properties(self) -> list[dict]: ...
```

The rest of the system should not know whether authentication came through OAuth or a service account.

---

## Step 24. Implement read-only authentication

Start with Application Default Credentials (ADC) for agency-managed deployments. Use workload identity or a managed runtime identity in production; only support a service-account JSON key when there is no safer approved option.

Requirements:

- Read-only GA4 access
- No credentials committed to source control
- Prefer managed runtime identity or ADC
- Avoid downloadable long-lived key files

OAuth comes later.

The MVP authentication mechanism is read-only ADC; OAuth is a later client-facing capability.

---

## Step 25. Implement the GA4 Admin API adapter

Responsibilities:

```text
discover properties
discover streams
inspect property metadata
inspect key events
inspect custom definitions
run configuration health checks
```

Do not add configuration-changing Admin API operations to MVP.

---

## Step 26. Implement the GA4 Data API adapter

Responsibilities:

```text
fixed reports
period comparisons
metadata
compatibility checking
optional realtime status
```

Set:

```text
returnPropertyQuota = true
```

when supported so quota data can be persisted with executions.

---

# Stage 11: Create Report Definitions

## Step 27. Define V1 reports

Create fixed report-definition versions for:

```text
property summary
channel acquisition
landing pages
events
lead events
measurement validation
```

Do not expose arbitrary GA4 query construction.

Each definition should specify:

```text
metrics
dimensions
filters
ordering
storage mode
aggregation rules
report version
```

---

## Step 28. Define dimension storage modes

Each dimensioned report must declare:

```text
ALL_RETURNED_ROWS
```

or:

```text
TOP_N_SNAPSHOT
```

Default small-site reports to `ALL_RETURNED_ROWS`.

The dashboard can display only the top 10 while retaining the complete returned dataset.

If a safety limit truncates a report:

```text
execution.status = incomplete
```

and expose the condition in the UI.

---

# Stage 12: Implement Report Execution Provenance

## Step 29. Create `report_executions`

Store:

```text
id
assignment_id
report_definition_version_id
requested_start_date
requested_end_date
started_at
completed_at
request_hash
response_hash
property_time_zone
currency_code
empty_reason
subject_to_thresholding
data_loss_from_other_row
sampling_metadata_json
samples_read_count
sampling_space_size
schema_restrictions_json
property_quota_json
status
error_code
error_detail
```

Every stored analytical fact must point back to:

```text
report_execution_id
```

This is how dashboard values remain reproducible and auditable.

---

# Stage 13: Build Analytics Fact Tables

## Step 30. Create daily fact tables

Implement:

```text
daily_property_metrics
daily_channel_metrics
daily_page_metrics
daily_event_metrics
daily_canonical_metrics
```

---

## Step 31. Create period snapshots

Implement:

```text
period_metric_snapshots
```

Initially support only:

```text
Trailing 7 days
Trailing 28 days
This month
Last month
Trailing 90 days
```

For non-additive metrics, these values must come from matching GA4 period executions.

Do not derive:

```text
28-day users
```

by doing:

```text
SUM(daily users)
```

Likewise, do not average daily conversion rates to manufacture a period conversion rate.

The architecture explicitly defers arbitrary date windows for these metrics.

---

# Stage 14: Build the Sync System

## Step 32. Create sync control tables

Implement:

```text
sync_runs
sync_jobs
report_executions
data_quality_status
```

---

## Step 33. Implement the scheduler

The scheduler should generate jobs based on:

```text
assignment
report definition
date / period
```

Result:

```text
Scheduler
    ↓
Queue
    ↓
Worker
    ↓
GA4
    ↓
Postgres
```

---

## Step 34. Implement the queue

The queue should provide:

- Property isolation
- Rate limiting
- Retry handling
- Concurrency controls
- Dead-letter handling

One broken client must not stop the rest of the portfolio.

---

## Step 35. Implement workers

Each worker should:

1. Load job
2. Load assignment
3. Load credential
4. Authenticate
5. Validate access
6. Validate report compatibility
7. Execute GA4 report
8. Capture metadata
9. Capture quota state
10. Normalize response
11. Persist `report_execution`
12. Upsert facts
13. Update sync health
14. Mark job successful

On failure:

1. Categorize error
2. Record error code
3. Retry where appropriate
4. Apply exponential backoff
5. Send unrecoverable jobs to a dead-letter state

This follows the defined synchronization topology.

---

# Stage 15: Guarantee Idempotency

## Step 36. Create deterministic uniqueness rules

Each report job must be safe to retry.

A retry should reuse its:

```text
report_execution_id
```

A retry therefore produces the same stored state.

A later reconciliation generates a new execution.

Never silently overwrite history.

Current-result views should deterministically select the latest valid execution.

The architecture specifically requires immutable execution history and deterministic current views.

---

## Step 37. Test idempotency

Automated test:

```text
run sync
↓
record row counts
↓
run exact same sync again
↓
verify no duplicate facts
```

Also test:

```text
initial execution
↓
later reconciliation
↓
new execution created
↓
current view selects latest eligible execution
```

---

# Stage 16: Implement Reconciliation

## Step 38. Encode freshness states

Implement:

```text
realtime
provisional
reconciling
stable
```

Default reconciliation:

```text
Today
→ realtime/provisional

Yesterday
→ provisional
→ resync daily

2-14 days old
→ reconciling
→ resync daily

15-90 days old
→ mostly stable
→ resync weekly

90+ days old
→ stable
→ repair/backfill only when needed
```

These are default application policies, not guarantees about GA4's underlying data stability.

---

# Stage 17: Implement Data Quality

## Step 39. Convert GA4 metadata into product states

Track:

```text
sampling
thresholding
(other) row loss
schema restrictions
empty results
provisional data
incomplete dimension retrieval
quota information
```

Translate technical metadata into visible statuses such as:

```text
Data still reconciling
Thresholding applies
Partial high-cardinality result
Revenue metrics unavailable
Cost metrics restricted
Sync failed
Tracking stale
Measurement contract outdated
```

Never silently convert incomplete or restricted data into `0`.

---

# Phase 2 Gate

Before building the real dashboard verify:

### Data correctness

- Retrying jobs creates no duplicates
- Source queries can be reproduced
- Period snapshots work correctly
- Users are not summed across daily rows
- Rates are not averaged across daily rows
- Funnel calculations respect approved definitions

### Reliability

- Revoked GA4 access produces a visible state
- Quota errors produce visible states
- Server errors are retryable
- Failed jobs can be replayed
- One property's failure does not affect another

### Data quality

- Restricted metrics are visible
- Incomplete dimension results are visible
- Every fact has provenance

The required Phase 2 gate is defined by these correctness and recoverability requirements.

---

# Stage 18: Build the Reporting Service

## Step 40. Create product-oriented endpoints

Implement:

```text
GET /portfolio/summary

GET /companies/:companyId/overview

GET /websites/:websiteId/acquisition

GET /websites/:websiteId/conversion

GET /websites/:websiteId/measurement-health

GET /websites/:websiteId/sync-status

GET /health

GET /healthz
```

The live FastAPI routes use literal IDs in the path and an optional fixed `period` query value (`7d`, `28d`, `90d`, `this_month`, or `last_month`). `GET /health` and `GET /healthz` are unauthenticated liveness checks only. `GET /docs` is the interactive OpenAPI test surface; reporting endpoints remain bearer-token protected.

Do not expose:

```text
GET /ga4/runReport?dimensions=...
```

The application API should expose product concepts rather than mirroring GA4.

---

## Step 41. Standardize the reporting response

Each KPI response should contain:

```text
metric definition
metric definition version
current value
comparison value
date window
property timezone
freshness state
provisional state
source system
quality warnings
calculation type
```

Example:

```json
{
  "metric": "generated_leads",
  "value": 24,
  "previousValue": 18,
  "period": {
    "start": "...",
    "end": "..."
  },
  "freshness": "reconciling",
  "source": "ga4_reporting_api",
  "aggregation": "SUM",
  "warnings": []
}
```

---

# Stage 19: Build the Single-Client Dashboard

## Step 42. Build the overview

Prioritize:

### 1. Business/lead outcomes

```text
Generated leads
Appointment requests
Other approved outcomes
```

### 2. Acquisition

```text
Organic
Paid
Direct
Referral
Social
```

### 3. Conversion

```text
Form starts
Form submits
Generated leads
Approved funnel conversion
```

### 4. Engagement

```text
Users
Sessions
Landing pages
Devices
```

### 5. Trust

```text
Data freshness
Measurement contract
Last sync
Tracking health
Warnings
```

The intended hierarchy places outcomes first and data trust visibly alongside performance data.

---

## Step 43. Add period selection

Only expose the supported predefined windows:

```text
7D
28D
This month
Last month
90D
```

Do not initially build arbitrary date-range selection for non-additive metrics.

---

## Step 44. Add period comparison

For each appropriate KPI:

```text
Current period
Previous comparable period
Absolute difference
Percentage difference
```

Only compare metrics whose semantic definitions are compatible.

---

## Step 45. Build acquisition view

Show:

```text
Channel
Sessions
Users where appropriate
Leads
Conversion metrics
Trend
```

Use the approved stored report definitions rather than generating arbitrary Data API queries.

---

## Step 46. Build landing-page view

Show:

```text
Landing page
Traffic
Engagement
Approved conversions
```

Clearly indicate incomplete/truncated data where relevant.

---

## Step 47. Build event view

Show:

```text
Expected events
Counts
Trend
Business interpretation
Measurement status
```

This can also help identify tracking regressions.

---

## Step 48. Build measurement-health view

Expose:

```text
Contract version
Last validation
GA4 stream status
Required-event health
Unexpected events
Prohibited-event detection
Lead-event activity
Consent configuration
Timezone alignment
```

This is a first-class product surface, not an internal debugging screen.

---

## Step 49. Build sync-status view

Expose:

```text
last successful sync
last complete date
current job status
failed jobs
error codes
reconciliation state
quota state where useful
```

---

# Phase 3 Gate

Validate the first-site dashboard against its underlying stored report executions.

Verify:

```text
Dashboard value
    ↓
Stored fact
    ↓
Report execution
    ↓
Report definition version
    ↓
GA4 source query
```

Every KPI should be traceable through that chain.

The dashboard passes when:

- Totals reconcile within documented GA4 limitations
- Every KPI has a known definition
- Every KPI has a known source
- Data-quality problems remain visible

These are the single-client dashboard acceptance criteria.

---

# Stage 20: Make Client #2 Possible

## Step 50. Document onboarding

Write a repeatable onboarding procedure:

```text
1. Create company
2. Create website
3. Assign measurement contract
4. Perform route/privacy review
5. Instrument website
6. Validate instrumentation
7. Create analytics connection
8. Discover GA4 property
9. Create website analytics assignment
10. Validate stream/reporting scope
11. Start synchronization
12. Verify data quality
13. Configure event mappings
14. Enable dashboard
```

If onboarding client #2 still requires substantial custom engineering, the platform abstraction is not finished.

---

# First Production Milestone

At this point the platform should have:

```text
✓ Versioned measurement system
✓ Privacy-safe analytics
✓ One validated real website
✓ GA4 connector
✓ Service-account authentication
✓ Postgres reporting store
✓ Versioned report definitions
✓ Correct aggregation semantics
✓ Reliable synchronization
✓ Reconciliation
✓ Data-quality tracking
✓ Single-client reporting API
✓ Single-client dashboard
✓ Measurement health
✓ Repeatable onboarding
```

Stop and evaluate the architecture before adding the broader platform.

The source architecture specifically says not to continue broad platform expansion until these first-site assumptions have been tested.

---

# Stage 21: Build the Agency Console

Only proceed once the first production milestone is stable.

## Step 51. Add portfolio reporting

Create:

```text
/portfolio/summary
```

Show:

```text
company
website
approved primary outcome
period change
measurement health
last complete date
sync health
action required
```

---

## Step 52. Add operator alerts

Detect:

```text
tracking stopped
lead count unexpectedly dropped
contract outdated
sync failed
GA4 access revoked
data stale
measurement incompatibility
```

---

## Step 53. Add annotations

Allow agency operators to record:

```text
site launch
campaign launch
tracking change
website redesign
major outage
measurement change
```

These annotations provide context for performance changes.

---

# Stage 22: Implement Full Tenant Isolation

## Step 54. Implement HTTP authorization

Every request must validate:

```text
organization
company
website
membership
role
```

Never trust a browser-supplied GA4 property ID.

---

## Step 55. Implement service-layer tenant context

Service functions should receive authorized tenant context.

Bad:

```typescript
getAnalytics(propertyId)
```

Better conceptually:

```typescript
getWebsiteAnalytics(
  authorizedTenantContext,
  websiteId
)
```

---

## Step 56. Implement Postgres RLS

Apply row-level security to client-facing tenant data.

Use separate database roles for:

```text
tenant application access
ingestion
administration
```

---

## Step 57. Write adversarial tenant tests

Explicitly attempt:

```text
Company A user → Company B website
Company A user → Company B report
Company A user → Company B fact rows
Company A user → Company B API endpoint
```

Test through:

```text
API
service layer
database
```

not merely through the UI.

Three-layer tenant isolation is required before the client portal launches.

---

# Phase 4 Gate

Proceed only when:

```text
✓ cross-client leakage tests pass
✓ incompatible contract comparisons warn
✓ failed syncs can be repaired
✓ Postgres RLS is active
✓ database isolation tests pass
```



---

# Stage 23: Build the Client Portal

## Step 58. Implement client roles

Add:

```text
client_admin
client_viewer
```

Keep the existing agency roles:

```text
agency_owner
agency_admin
agency_analyst
```

---

## Step 59. Build the client dashboard

Simplify the agency dashboard.

Prioritize:

```text
Business outcomes
Period-over-period changes
Acquisition drivers
Landing pages
Simple approved funnel
Freshness explanation
```

Do not expose agency operational details unnecessarily.

---

## Step 60. Add goals

Allow clients to define effective-dated goals such as:

```text
Monthly leads = 50
Booked appointments = 30
Cost per qualified lead < $X
```

Only expose goals where the associated metric has a trustworthy definition.

---

## Step 61. Add recurring reports

Generate approved:

```text
PDF reports
email reports
```

from the same semantic layer used by the dashboard.

Do not create separate reporting calculations.

---

## Step 62. Add OAuth

Only now implement self-service Google authorization.

Requirements:

```text
analytics.readonly
web-server OAuth flow
encrypted refresh tokens
revocation
re-consent
offboarding
```

---

# Phase 5 Gate

Before public client use verify:

```text
✓ OAuth production requirements
✓ tenant authorization
✓ API isolation
✓ service-layer isolation
✓ database RLS
✓ revocation
✓ offboarding
✓ retention
✓ deletion
```



---

# Stage 24: Add Marketing Sources

Only after the core platform works.

Implement in this order:

## Step 63. Google Ads

Add:

```text
cost
clicks
campaign
ad group where required
source identifiers
```

---

## Step 64. Search Console

Add:

```text
queries
pages
clicks
impressions
CTR
position
```

---

## Step 65. Call tracking

Add real call outcomes rather than treating GA4 `phone_click` events as confirmed leads.

Priority is explicitly:

```text
1. Google Ads
2. Search Console
3. Call tracking
```



---

# Stage 25: Add Revenue Attribution

## Step 66. Integrate CRM/booking

Introduce first-party entities such as:

```text
leads
appointments
customers
revenue_events
call_outcomes
```

---

## Step 67. Map website activity to actual outcomes

Build the progression:

```text
Website
↓
Generated lead
↓
Qualified lead
↓
Booked appointment
↓
Customer
↓
Revenue
```

Do not force prohibited identifiers into GA4 to accomplish this.

Matching should occur in approved first-party systems.

---

## Step 68. Add business KPIs

Examples:

```text
Cost per qualified lead
Lead-to-appointment rate
Appointment-to-customer rate
Revenue
Revenue by channel
```

This stage completes the highest-value portion of the metric hierarchy.

---

# Stage 26: Add Raw Event Analytics Only When Justified

BigQuery should remain optional.

Only onboard it when a client genuinely requires:

```text
event-level analysis
path analysis
advanced attribution
large joins
modeling
approved first-party data analysis
```

Maintain two separate source families:

```text
ga4_reporting_api
ga4_bigquery_export
```

Never combine them into a KPI without an explicit reconciliation rule.

The architecture intentionally keeps BigQuery outside the normal dashboard request path.

---

# Recommended Immediate Execution Order

If development starts now, work through these items in exactly this order:

```text
01  Select first website
02  Define local_service_v1
03  Define event schemas
04  Define prohibited data
05  Define route eligibility
06  Build validation fixtures
07  Add semantic analytics attributes
08  Build central tracking adapter
09  Resolve collection ownership
10  Test events
11  Test consent
12  Test prohibited routes
13  Test URL/data leakage
14  Build measurement-health checks

        PHASE 1 GATE

15  Create backend project
16  Create Postgres schemas
17  Implement organization/company/website
18  Implement connections
19  Implement analytics assignments
20  Implement measurement-definition tables
21  Implement event mappings
22  Implement aggregation behaviors
23  Implement credential abstraction
24  Implement service-account connection
25  Implement Admin API adapter
26  Implement Data API adapter
27  Create report definitions
28  Define dimension storage rules
29  Implement report executions
30  Implement daily facts
31  Implement period snapshots
32  Implement sync tables
33  Implement scheduler
34  Implement queue
35  Implement workers
36  Implement idempotency
37  Test retries
38  Implement reconciliation
39  Implement data-quality states

        PHASE 2 GATE

40  Build reporting service
41  Standardize API responses
42  Build dashboard overview
43  Add fixed periods
44  Add period comparison
45  Build acquisition view
46  Build landing-page view
47  Build event view
48  Build measurement-health view
49  Build sync-status view

        PHASE 3 GATE

50  Document client onboarding

        FIRST PRODUCTION MILESTONE

51  Build agency portfolio
52  Add operator alerts
53  Add annotations
54  Add HTTP tenant authorization
55  Add service tenant context
56  Add Postgres RLS
57  Run adversarial tenant tests

        PHASE 4 GATE

58  Add client roles
59  Build client portal
60  Add goals
61  Add recurring reports
62  Add OAuth

        PHASE 5 GATE

63  Integrate Google Ads
64  Integrate Search Console
65  Integrate call tracking
66  Integrate CRM/booking
67  Implement outcome matching
68  Add revenue KPIs

        OPTIONAL

69  Add BigQuery raw-event analytics
```

---

# What Not to Build Yet

Until the first production milestone is complete, deliberately avoid:

```text
Generic report builder
Arbitrary dashboard formulas
Arbitrary date ranges for non-additive metrics
Client self-service OAuth
Client portal
Google Ads
Search Console
CRM
BigQuery
Advanced attribution
Cross-client benchmarking
Realtime analytics as a major feature
GA4 Admin API write operations
```

These are either explicitly deferred or belong to later delivery phases.

---

# Development Rule

Treat each phase gate as a hard dependency.

Do not follow:

```text
measurement 40% done
backend 60% done
dashboard 50% done
OAuth 30% done
BigQuery experiment started
```

Follow:

```text
Measurement
    ↓ PASS
Backend/data platform
    ↓ PASS
Single-client dashboard
    ↓ PASS
Repeatable onboarding
    ↓
Agency console
    ↓ PASS
Tenant isolation
    ↓ PASS
Client portal
    ↓
Additional integrations
```

The architecture is deliberately designed to test the riskiest assumptions first: whether the platform can define trustworthy measurement, collect it safely, reproduce it correctly, and turn it into useful reporting for one real website.

Everything else should build on that proof.
