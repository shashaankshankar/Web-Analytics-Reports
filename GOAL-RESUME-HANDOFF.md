# Measurement Platform Goal Resume Handoff

Last updated: 2026-08-18 (America/New_York)

## Resume prompt

Copy this into a new implementation session from this repository root:

```text
Continue and complete the existing Measurement and Reporting Platform production goal in /Users/shashaankshankar/Desktop/Creative/Google Analytics Dashboard. The reusable application, contracts, APIs, UI, jobs, and deployment must remain fully tenant/client agnostic; House of Dental may appear only in explicit first-client configuration, seed data, tests/evidence, and currently necessary production configuration. Keep every real-client recurring report row disabled and never send a real report to a client. Start by reading GOAL-RESUME-HANDOFF.md, Measurement and Reporting Platform.md, ARCHITECTURE-PLAN.md, and REQUIREMENTS-AUDIT.md. Do not mark the goal complete until all remaining implementation, browser, deployment, and audit work passes with zero unresolved requirements; stop only for genuinely external credentials, client identities, legal/privacy/domain approval, or material product decisions.
```

## Non-negotiable boundaries

- Do not send a real report to any client.
- Keep the `measurement-report-dispatch` Scheduler job enabled, but keep every real-client recurring report database row disabled.
- Do not treat provider acceptance as inbox delivery.
- Do not fabricate analytics, Ads, call, CRM/booking, appointment, customer, or revenue facts.
- Keep unavailable data as unavailable, never a numeric zero.
- House of Dental is the first configured client, not an application-level special case.
- Keep secrets in Secret Manager/runtime configuration and out of Git, docs, logs, screenshots, and chat.

## Verified stopping state

### Local repository

- **Branch:** `dev`
- **HEAD:** `807f55e` (Add comprehensive tests for onboarding, portal integration, and privacy features)
- Working tree is intentionally dirty with the singleton→database migration changes.
- **140 passed**, including `test_react_portal_static_assets_serving` in `tests/test_portal_integration.py`, with one Starlette/httpx deprecation warning.
- `python -m compileall`: passed.
- `git diff --check`: passed.
- Privacy fixture runner: 18 fixtures passing, zero failures.
- No commit or push was performed in this run.

### What was completed in the current implementation

1. **Singleton-site behavior eliminated.** Every per-website endpoint now resolves metadata (company, domain, governance, timezone, assignment, Measurement ID) from Postgres under RLS. The boot `Site` singleton is no longer consulted for measurement health, PDF generation, or report dispatch.

2. **Measurement ID canonicalized.** Migration `012_measurement_id_on_streams.sql` adds a nullable `measurement_id` column to `app.ga_data_streams`. `seed_first_site` and `assign_oauth_property` write it; `sync_target` and `website_site_context` read it with `reporting_scope->>'measurementId'` as transitional fallback only.

3. **Governance resolved from canonical source.** `website_site_context` uses `COALESCE(latest_workflow.governance_status, websites.healthcare_eligibility)`. The five-state workflow governance is authoritative; the legacy three-state healthcare_eligibility projection is used only when no workflow exists and is documented as a lossy fallback.

4. **Fabricated fields removed.** `deploymentStatus` and `publicCollectionStatus` are no longer returned by the per-website measurement-health endpoint. Those fields were boot-JSON presentation values with no persisted runtime source. The collection check is now data-driven from `sync.quality.empty`.

5. **Per-website GA4 access verifier cache.** Verifiers are keyed by property ID on `app.state.ga4_access_verifiers`, not a global singleton. The injected reporter seam is preserved for tests.

6. **Migration entrypoint added.** `app.main:migrate()` applies pending migrations through the application DB connection, intended for a Cloud Run Job or deployment preamble.

7. **React portal SPA and service declaratively configured.** The React Single Page Application is implemented in `frontend/`; its compiled static distribution is in `app/static/`, with assets mounted at `/assets` and the SPA served at `/portal`. `cloudbuild.yaml` gained a `deploy-portal` step with `--no-allow-unauthenticated`, a portal-env file reference, and evidence collection. The checked-in portal release config is `infra/gcp/portal-env.yaml`. The service is not yet deployed.

8. **Security scan completed.** Scan `28ea4399-1268-4c62-afb2-28eb694031d1`: 3/3 preflight, 7 threat surfaces, **0 findings**. Report sealed at `807f55e`. Covers IAP identity, Postgres RLS, OAuth PKCE/KMS, internal trigger tokens, XSS, Secret Manager references, and migration sequencing.

### Production state (unchanged since 2026-08-15)

- Project: `web-analytics-agency-prod`, `us-central1`
- Reporting service: `measurement-reporting-platform-00051-cwx`
- OAuth callback service: `measurement-oauth-callback-00010-jwh`
- **No `measurement-portal` service exists yet.**
- **Migrations `010`, `011`, and `012` have not been applied to production; migration 012 is complete in the working tree only.**
- All four schedulers enabled; both first-client recurring report rows disabled.
- The local working-tree changes (singleton→database migration, portal config) are **not deployed**.

## Remaining work

### 1. Apply migrations to production

- Back up Cloud SQL and verify PITR.
- Run `python -c "from app.main import migrate; migrate()"` through a guarded Cloud Run Job or `gcloud run jobs execute` against production with the same Cloud SQL attachment.
- Migrations to apply: `010_onboarding_workflows`, `011_fact_provenance`, `012_measurement_id_on_streams`.
- After migration, re-run `seed_first_site` to backfill the House of Dental ADC stream's `measurement_id` column.
- Validate `NOT VALID` constraints in a separate low-risk maintenance window.

### 2. Deploy the updated code

- Submit the guarded `cloudbuild.yaml` (tests → build → scan → deploy-reporting → deploy-portal → deploy-callback → verify).
- The immutable image must include the `app.main:migrate()` entrypoint for portal service startup.
- After deployment, force daily sync, retention, external-source sync, and verify all five reporting periods.
- Confirm recurring report rows remain disabled; verify dispatcher with a stub path only.

### 3. Launch the portal service

- `gcloud run deploy measurement-portal` with `--iap` and `--no-allow-unauthenticated`.
- Grant `roles/run.invoker` to the IAP service agent and `roles/iap.httpsResourceAccessor` to approved operators.
- Set `PORTAL_IAP_MODE=direct`, `PORTAL_IAP_EXPECTED_AUDIENCE=/projects/495946300014/locations/us-central1/services/measurement-portal`, and `OAUTH_CALLBACK_ONLY=false` on the portal service only.
- Verify health probes remain reachable by Cloud Run; product routes fail closed for unauthenticated requests.

### 4. Browser and live flow validation

Both flows require the deployed portal service and approved IAP identities:

**Agency flow:** IAP login → portfolio → start/resume onboarding → create company/site → record governance/consent → register connections → request first sync → add client membership → review handoff.

**Client flow:** IAP login → resolve authorized website → view overview, health, freshness, goals, PDF → client admin manages goals; viewer is read-only.

Test desktop/mobile, keyboard navigation, focus, screen-reader landmarks, empty/loading/error states, XSS escaping, authorization rejections, and a second synthetic client. Do not claim visual QA from code inspection alone.

### 5. External or owner-controlled gates (not implemented and cannot be fabricated)

- Approved agency and client IAP identity emails.
- External OAuth client/consent configuration for clients outside the Google organization.
- Google OAuth production approval, authorized domains, branding, privacy policy, and terms.
- Client authorization for GA4 property/stream assignment.
- Google Ads, call-tracking, CRM/booking credentials and account approval.
- Client-approved privacy/legal/healthcare route decisions and KPI definitions.
- A generic agency-owned report sender/domain if real recurring delivery is later approved.
- Independent inbox-delivery proof after a future authorized send.

The current local verification baseline remains 140 tests, including `test_react_portal_static_assets_serving` in `tests/test_portal_integration.py`, 18 privacy fixtures, Python `compileall`, and `git diff --check`, all passing. These checks validate the working tree only; they do not establish client IAP access, external Google OAuth production verification, client Ads/CRM credentials, portal deployment, migration application in production, real recurring report dispatch, or inbox delivery.

### 6. Documentation and release audit

- Documentation updates in `Measurement and Reporting Platform.md`, `PRODUCTION-READINESS.md`, `REQUIREMENTS-AUDIT.md`, and this handoff record the completed implementation changes and preserve the external/deployment boundaries.
- Document how to onboard client #2 without code changes: create a site config in `measurement/sites/`, set `MEASUREMENT_SITE_CONFIG`, run seed, or use the six-step onboarding workflow.

## Safe first commands on resume

```sh
cd "/Users/shashaankshankar/Desktop/Creative/Google Analytics Dashboard"
git status --short --untracked-files=all
git diff --check
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app measurement
.venv/bin/python measurement/privacy/run_fixtures.py
rg -n "House of Dental|house-of-dental|company_house_of_dental|thehouseofdentalwp|549268305|G-TC66MQQ0T7" app measurement .env.example README.md \
  --glob '!measurement/sites/house-of-dental.json' \
  --glob '!**/evidence/**' \
  --glob '!**/first-site.md'
```

Do not start by deploying. First review the working-tree diff, verify the security scan report, and make deployment reproducible.
