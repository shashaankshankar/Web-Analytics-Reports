# Measurement Platform Goal Resume Handoff

Last updated: 2026-08-14 (America/New_York)

## Resume prompt

Copy this into a new Codex session from this repository root:

```text
$define-goal Continue and complete the existing Measurement and Reporting Platform production goal in /Users/shashaankshankar/Desktop/Creative/Google Analytics Dashboard. Fully implement every requirement in Measurement and Reporting Platform.md using ARCHITECTURE-PLAN.md as the reference, including a genuinely usable end-to-end agency UI, client login/portal, and generic resumable client onboarding. The reusable application, contracts, APIs, UI, jobs, and deployment must remain fully tenant/client agnostic; House of Dental may appear only in explicit first-client configuration, seed data, tests/evidence, and currently necessary production configuration. Use the main gpt-5.6-sol model only for planning and review. Every grunt-work subagent must explicitly use model gpt-5.6-luna with reasoning_effort=max. Test everything and keep infrastructure enabled, but keep every real-client recurring report row disabled and never send a real report to a client. Start by reading GOAL-RESUME-HANDOFF.md, Measurement and Reporting Platform.md, ARCHITECTURE-PLAN.md, REQUIREMENTS-AUDIT.md, and the current git diff. Resume security scan 9886c466-0912-4f63-ac47-c12f0d99dbc5 or replace it with a current-snapshot standard scan if the saved snapshot is stale. Do not mark the goal complete until tests, migrations, security, guarded deployment, live agency/client flows, browser/accessibility checks, operations, and REQUIREMENTS-AUDIT.md all pass with zero unresolved implementation/deployment requirements; stop only for genuinely external credentials, client identities, legal/privacy/domain approval, or material product decisions.
```

## Non-negotiable boundaries

- Do not send a real report to any client.
- Keep the `measurement-report-dispatch` Scheduler job enabled, but keep every real-client recurring report database row disabled.
- Do not treat provider acceptance as inbox delivery.
- Do not fabricate analytics, Ads, call, CRM/booking, appointment, customer, or revenue facts.
- Keep unavailable data as unavailable, never a numeric zero.
- House of Dental is the first configured client, not an application-level special case.
- Keep secrets in Secret Manager/runtime configuration and out of Git, docs, logs, screenshots, and chat.
- Preserve existing unrelated user changes in this dirty working tree.

## Verified stopping state

### Local repository

- Branch: `qa`
- HEAD before this working tree: `1a5bc5c`
- Working tree is intentionally dirty and contains the implementation in progress.
- Full suite: **132 passed**, one Starlette/httpx deprecation warning.
- `python -m compileall`: passed.
- `git diff --check`: passed.
- Privacy fixture runner: previously verified 18 fixtures passing.
- No commit or push was performed in this run.

Major local additions and changes include:

- Generic site selection through `MEASUREMENT_SITE_CONFIG` / `MEASUREMENT_DEFAULT_SITE_CONFIG`.
- Client identifiers removed from the generic `local_service_v1` contract and environment example.
- Privacy fixture runner and corrected route eligibility semantics.
- Live GA4 access-health verification and honest unavailable-versus-zero outcomes.
- Report/fact provenance migration and sync persistence.
- Generic agency/client product UI, access page, client portal, and six-step onboarding UI.
- Resumable tenant-scoped onboarding backend and migration.
- IAP identity verification foundation and portal route integration.
- Genericized legacy dashboard URLs/content.
- Dispatcher now reports failed delivery as failure instead of a false HTTP 200.
- Ordered, serialized migrations through `011` with structural safety tests.

### Production

- Project: `web-analytics-agency-prod`
- Region: `us-central1`
- Reporting service: `measurement-reporting-platform-00051-cwx`
- OAuth callback service: `measurement-oauth-callback-00010-jwh`
- The new local UI/onboarding/IAP/migration work is **not deployed**.
- Enabled schedulers:
  - `measurement-daily-sync`
  - `measurement-retention`
  - `measurement-external-source-sync`
  - `measurement-report-dispatch`
- Both first-client recurring report rows are disabled.
- The prior attempted dispatch failed before provider acceptance: no provider message ID and no report email sent.
- Cloud IAP API is now enabled.
- An IAP/OAuth brand exists with application title `Measurement & Reporting Platform`.
- No `measurement-portal` Cloud Run service exists yet.
- Migrations `010_onboarding_workflows` and `011_fact_provenance` have not been applied to production.

## Security scan handoff

- Scan ID: `9886c466-0912-4f63-ac47-c12f0d99dbc5`
- Status at stop: running, discovery phase, 0/1 review receipts.
- Preflight: 3/3 passed.
- Saved scan directory:
  `/private/var/folders/51/v5s8cpd579ngsv5kjb6rllh40000gn/T/codex-security-scans-BFb3bV/Google-Analytics-Dashboard/1a5bc5c78fee6856f2f9f2b99c109351e80d2ba2_20260815T025556Z_k15godjy`
- The baseline and focused review workers were stopped before producing results.
- The scan snapshot predates some final IAP edits. On resume, either update/complete it with an explicit coverage limitation and run a current working-tree security review, or start a replacement current-snapshot standard scan. Do not claim this scan complete as-is.

## Remaining implementation work

### 1. Re-review the final IAP patch

The last Luna-max agent made a partial patch that subsequently passed the full suite. Review it rather than trusting test count alone.

- Portal direct-IAP mode must require the signed `X-Goog-IAP-JWT-Assertion` for user identity.
- Verify IAP JWKS signature, issuer `https://cloud.google.com/iap`, time claims, and exact audience:
  `/projects/495946300014/locations/us-central1/services/measurement-portal`
- `X-Serverless-Authorization` is transport authentication and may coexist; never decode its signature-stripped payload as user identity.
- Plain `Authorization` must not become portal user identity.
- Compatibility email must never be trusted alone and must match the signed assertion when present.
- Preserve existing reporting-service Cloud Run IAM and Scheduler/Tasks authentication.
- Add/retain tests for coexistence, forged/missing/expired/wrong audience/issuer/signature assertions, compatibility mismatch, and reporting auth regression.

### 2. Remove remaining singleton-site behavior

The product is not fully client-agnostic while these paths still use the boot `Site` object:

- Measurement-health response currently derives assignment, governance, collection, and returned `websiteId` from the boot site.
- PDF generation uses the boot site's company/domain metadata.
- Recurring-report dispatch builds PDFs with boot-site metadata.
- Some GA4 access-health checks use the boot site's global reporter/settings instead of the requested website assignment.
- Sync construction still falls back to boot-site timezone/measurement ID in places.

Implement a tenant-scoped storage method that resolves the requested website's company, domain, timezone, approved contract/governance state, approved GA4 assignment, property/stream, credential type/reference, and collection state. Use it for measurement health, PDF generation, dispatch, and per-client access checks. Add two unrelated synthetic-client tests proving no metadata or facts cross between tenants.

### 3. Add a reproducible migration command/job

- Add a small safe migration entry point (for example `app.migrate`) that runs `Database.migrate()` without printing secrets.
- Add a Cloud Run Job or equivalent guarded release step using the same Cloud SQL attachment and application DB secret.
- Execute and wait for migrations before deploying portal code that depends on tables `010`/`011`.
- Take/verify a backup/PITR restore point before production migration.
- The migration review used structural/mocked tests only because local Docker/Postgres was unavailable.
- Validate the SQL against an ephemeral Postgres or a safe staging database before production.
- Historical `NOT VALID` constraints should be validated in a separately planned low-risk maintenance step.

### 4. Add the separate browser-facing portal service

Create a third Cloud Run service, recommended name `measurement-portal`:

- Same immutable image, separate service configuration.
- Direct Cloud Run IAP with `--iap` and `--no-allow-unauthenticated`.
- Grant `roles/run.invoker` to the IAP service agent.
- Grant `roles/iap.httpsResourceAccessor` initially only to the approved agency operator.
- Use the existing least-privilege runtime service account and Cloud SQL attachment.
- Set portal mode and the exact IAP resource audience above.
- Keep existing private reporting and callback services unchanged.
- Ensure health probes remain reachable by Cloud Run while product routes fail closed.
- Make Cloud Build/release configuration declarative without embedding client-specific IDs or sender/domain values. Use deployment substitutions or separately managed environment configuration.
- Add release evidence that the portal image/revision/probes/IAP policy are correct.

Google's current direct-IAP documentation says the IAP service agent also needs Cloud Run Invoker. External/out-of-organization client access may require a custom external OAuth client/consent configuration in Google Auth Platform.

### 5. Wire and validate real end-to-end product flows

Agency flow:

1. IAP login.
2. Agency portfolio.
3. Start/resume generic onboarding.
4. Create company/site boundary.
5. Record explicit privacy/governance and consent references.
6. Register or explicitly defer each connection.
7. Request/check first sync.
8. Add an authorized client portal membership.
9. Review handoff checklist without creating/enabling a report recipient.

Client flow:

1. IAP login.
2. Resolve only authorized website scope.
3. View stored overview, source caveats, measurement health, freshness, goals, and PDFs.
4. Client admin may manage supported goals; viewer remains read-only.
5. No client can access agency routes or another client's site by changing IDs/query parameters.

Use real browser testing against the deployed IAP URL. Test desktop/mobile, keyboard navigation, focus, screen-reader landmarks, empty/loading/error states, XSS escaping, authorization failures, and a second synthetic client. Previous local browser visual QA was blocked by the in-app/Brave extension blocking localhost; do not claim visual QA from code inspection alone.

### 6. Complete security validation and remediation

Run a current-snapshot standard security scan covering:

- IAP/header trust and audience confusion.
- Tenant authorization/IDOR and Postgres RLS.
- Agency/client role transitions and membership grants.
- OAuth state/PKCE/KMS token handling and callback isolation.
- Secret Manager references and source connectors.
- Internal Scheduler/Tasks token endpoints.
- Report recipient references and dispatch idempotency.
- Stored/reflected XSS in server-rendered and JS-rendered UI.
- Migration privileges and deployment IAM.

Validate and fix every real finding, then complete/seal the scan and link its generated report. The stopped scan has no findings and no coverage receipts; that means incomplete, not clean.

### 7. Guarded production deployment and smoke tests

- Re-run all tests, privacy fixtures, compile, lint/diff checks, dependency/image vulnerability scan, and migration tests.
- Back up Cloud SQL and verify PITR.
- Apply migrations `010` and `011` through the guarded job.
- Submit the guarded Cloud Build and verify immutable revisions.
- Verify reporting, callback, and portal health/readiness.
- Force daily sync, retention, external-source sync, and report-dispatch Scheduler jobs.
- Confirm Cloud Tasks queues drain with no failures.
- Verify 7d, 28d, 90d, this-month, and last-month stored reporting periods.
- Verify Search Console/other source status remains honest.
- Exercise PDF generation/download, but do not create or enable a real-client report schedule.
- Confirm all recurring report rows remain disabled after every test.
- Verify dispatcher with a fake/stub recipient/provider path only; never a client address.

### 8. External or owner-controlled gates

These cannot be fabricated and may remain explicit blockers:

- Approved agency and client login email identities.
- External/out-of-organization IAP OAuth client/consent configuration if clients are outside the Google organization.
- Google OAuth production approval, authorized domains, branding, privacy policy, and terms.
- Client authorization for GA4 property/stream assignment.
- Google Ads credentials/account approval.
- Call-tracking provider credentials and identity policy.
- CRM/booking credentials and actual outcome mapping.
- Client-approved privacy/legal/healthcare route decisions.
- Client-approved KPI definitions and effective-dated targets.
- A generic agency-owned report sender/domain/provider if real recurring delivery is later approved.
- Independent inbox-delivery proof after a future explicitly authorized send.

### 9. Documentation and release audit

- Update `Measurement and Reporting Platform.md` implementation status.
- Update `PRODUCTION-READINESS.md` with evidence boundaries.
- Rewrite `REQUIREMENTS-AUDIT.md` against the final implementation and production state.
- `REQUIREMENTS-AUDIT.md` must show zero unresolved implementation/deployment requirements before goal completion; external owner gates must be clearly separated and never described as implemented.
- Document client-agnostic onboarding, deployment substitutions, identity roles, secrets ownership, incident/runbook steps, backup/PITR, rollback, retention/deletion, and how to onboard client #2 without code changes.

## Safe first commands on resume

```sh
cd "/Users/shashaankshankar/Desktop/Creative/Google Analytics Dashboard"
git status --short --untracked-files=all
git diff --check
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app measurement
rg -n "House of Dental|house-of-dental|company_house_of_dental|thehouseofdentalwp|549268305|G-TC66MQQ0T7" app measurement .env.example README.md \
  --glob '!measurement/sites/house-of-dental.json' \
  --glob '!**/evidence/**' \
  --glob '!**/first-site.md'
```

Do not start by deploying. First review the final IAP diff, close singleton-site behavior, complete current-snapshot security review, and make migrations/deployment reproducible.
