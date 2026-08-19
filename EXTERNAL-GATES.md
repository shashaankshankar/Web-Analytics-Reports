# External gates

This runbook consolidates the external approvals, credentials, ownership decisions, and evidence required before this privacy-aware measurement platform is treated as ready for a client. It complements README.md, ONBOARDING.md, EXTERNAL-SOURCE-ONBOARDING.md, and PRODUCTION-READINESS.md.

The platform is fail-closed. A local build, passing test, provider API response, or configured secret is not proof that an external gate is complete. Record each gate with its owner, scope, evidence, date, and remaining risk. Never put credentials, refresh tokens, API keys, private client data, or recipient addresses in Git, tickets, screenshots, or chat.

## Gate 1: User Identity & Cloud Run IAP Access

Purpose: ensure that only approved human identities reach the browser-facing portal and that each identity resolves to the correct organization and website membership. Cloud Run/IAP is a transport and identity gate; application tenant authorization and roles remain separate.

Required setup and evidence:

1. Create or confirm the portal Cloud Run service and IAP configuration. The checked-in portal configuration uses the measurement-portal service and an audience shaped like /projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME.
2. Enable IAP and grant least-privilege access to named agency/client users or groups. Do not make the reporting portal broadly public.
3. Make PORTAL_IAP_AUDIENCE and PORTAL_IAP_EXPECTED_AUDIENCE exactly match the configured audience. Verify Cloud Run forwards the signed X-Goog-IAP-JWT-Assertion header.
4. Establish application memberships using the smallest approved role: agency_owner, agency_admin, agency_analyst, client_admin, or client_viewer. Memberships must remain organization- and website-scoped.
5. Test an approved user for every role, a denied user, an identity without a membership, an ambiguous membership, and conflicting identity headers.
6. Retain the IAP policy/audience record, successful role-scoped requests, denied requests, and membership audit records. /health is process liveness only; readiness and reporting routes remain protected.

Completion means approved users see only their assigned companies/websites and denial cases pass. IAP/Cloud Run Invoker access does not grant application membership, source access, or reporting authorization.

## Gate 2: Google OAuth App Verification & Production Publishing

The integration is currently designed for restricted Testing mode. Keep GOOGLE_OAUTH_PRODUCTION_APPROVED=false until the external work is complete. The approved scope is only https://www.googleapis.com/auth/analytics.readonly.

The documented callback is https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app/oauth/google/callback.

Required setup and evidence:

1. In Google Auth Platform, confirm app type, audience, branding, authorized domain, homepage, privacy policy, and terms URLs.
2. Publish accurate legal pages on an owned authorized domain. They must explain data access, use, sharing, retention, deletion, and support contact.
3. Verify the exact HTTPS redirect URI, complete Google verification, and answer any branding, security, or data-use review.
4. Keep scopes minimal and obtain privacy/security approval before adding one.
5. Record an explicit production-publishing decision with approver, date, intended users, and approved client property.
6. Re-authorize with the intended client account; confirm property and stream match the reviewed assignment. Revoke and delete incorrect test connections.

Client IDs, secrets, state secrets, refresh tokens, and KMS references belong in pinned Secret Manager versions and encrypted storage, never in source or browser fields. Testing success or a working callback is not verification or production publishing.

## Gate 3: Connecting Client Data Sources

Each source needs a named owner, purpose, least-privilege access, version-pinned credential reference, exact source-to-website match, reconciliation, and approval. Unavailable, partial, unauthorized, or stale data must remain visibly unavailable, not become zero.

### GA4 service account / managed identity

1. The property owner confirms the intended GA4 property and web stream.
2. Grant the runtime managed identity read-only property access, or use an approved read-only OAuth connection. Do not download a long-lived service-account key.
3. Discover property and stream through the Admin API and compare IDs, measurement ID, and timezone with the reviewed site configuration.
4. Persist an effective-dated website assignment and approve reporting scope. One property represents one managed website unless a reviewed stream filter exists.
5. Run fixed windows, then verify provenance, completeness, quotas, thresholding, (other) loss, freshness, and idempotency before enabling the dashboard.

### Search Console

The client verifies the exact URL-prefix property and grants read access to the runtime identity or approved OAuth connection. Register only a pinned Secret Manager reference such as projects/PROJECT/secrets/SOURCE/versions/7; /latest and raw credentials are rejected. Validate, record owner/governance approval, run the isolated sync, and reconcile coverage. Search Analytics may have partial top-row coverage; query-text retention remains disabled unless separately approved.

The documented first exception is the verified House of Dental URL-prefix property through runtime ADC; it remains partial until approved rows and coverage exist.

### Google Ads

The client supplies and approves the developer token, customer ID, optional manager ID, read-only OAuth grant, currency, account scope, and campaign mapping. Validate access and reconcile cost, clicks, conversions, date/timezone, and currency before activating paid-performance reporting. Registration carries only a pinned secret reference, never the secret payload.

### CRM, booking, and first-party outcomes

Identify the system, credential owner, source-record IDs, lifecycle mapping, and identity policy. Privacy/legal approval is required before matching outside GA4. Accept only approved, keyed, privacy-validated batches with unique request IDs. Never ingest names, emails, phones, addresses, patient data, recordings, transcripts, or other direct identifiers. Gate status is per source: not_configured, unavailable, partial, failed, or approved; a credential check alone is not approval or freshness.

## Gate 4: Transactional Email & Recurring Reports

Required setup and evidence:

1. The client approves sending domain, sender, recipients, period, weekly/monthly cadence, timezone, first run, attachment policy, and support owner.
2. Create an owned Resend account/API key with least privilege. Store it as REPORT_EMAIL_API_KEY in Secret Manager.
3. Add the exact provider-supplied SPF, DKIM, and DMARC DNS records; verify the sending domain in Resend and retain DNS/provider evidence. Values are domain-specific and must not be invented here.
4. Configure REPORT_EMAIL_FROM, REPORT_RECIPIENTS_JSON, and https://api.resend.com/emails outside source control. Postgres stores only a non-sensitive recipient alias; the address stays protected.
5. Create schedules only when emailDeliveryConfigured is true. Verify dispatcher, scheduler, idempotency key, delivery ledger, retries, and failure handling.
6. Send a controlled test and separately prove provider acceptance/message ID, inbox placement, attachment integrity, and wording that appointment requests are not booked or confirmed appointments.

Provider HTTP 200 is not inbox proof. Current platform documentation says dispatch is deliberately paused and no client report has been sent; keep it paused until sender, DNS, recipient, approval, and independent delivery evidence are complete.

## Gate 5: Client Governance, Legal Compliance & Real KPI Targets

The client must name the data owner, website owner, agency operator, privacy/legal reviewer, and support/incident contacts; confirm company, canonical domain, timezone, industry, organization boundary, and retention/deletion expectations; and approve the versioned contract covering routes, events, parameters, consent, prohibited data, attribution, and reporting windows.

Before collection, complete privacy/legal review for healthcare or other sensitive contexts. Healthcare eligibility is default-deny. Approve processing, third-party sharing, disclosures, retention, deletion/offboarding, access requests, incident response, and required contract terms.

Define real KPI targets with effective date, owner, unit, source, denominator, period, and review cadence. Targets come from the client or an approved business source; never invent benchmarks or treat placeholder goals as client truth. Reconcile values to stored snapshots and source executions, and review partial states, attribution limits, thresholding, (other) loss, and incomplete local-day handling. Obtain written preview acceptance, production handoff approval, and documented offboarding scope. Technical health is not consent, legal approval, or business validation.

## Local Development & Viewing the React UI

FastAPI is in app/; React/Vite is in frontend/. Vite uses port 5173 and has no backend proxy. For a same-origin, API-backed portal, build into FastAPI's app/static/ and open /portal.

### Recommended: compiled React portal served by FastAPI

~~~sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
export MEASUREMENT_SITE_CONFIG=measurement/sites/house-of-dental.json
.venv/bin/python -m pytest
cd frontend && npm ci && npm run build && cd ..
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000/portal, /agency, or /health. Keep PLATFORM_MODE=demo and live GA4 flags disabled unless database, identity, assignment, and governance approvals are real. Local startup does not create IAP, connect sources, send email, or publish.

### Vite development shell

Terminal 1:

~~~sh
export MEASUREMENT_SITE_CONFIG=measurement/sites/house-of-dental.json
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

Terminal 2:

~~~sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
~~~

Open http://127.0.0.1:5173. Since no Vite API proxy is configured, API calls from that origin are not automatically sent to port 8000; use the compiled FastAPI path for interactive API-backed viewing, or add a reviewed local-only proxy.

Frontend verification:

~~~sh
cd frontend && npm run build && cd ..
git diff --check
~~~

The build writes generated assets to app/static/; do not hand-edit them. Review the diff after building and keep generated output synchronized with source.

## Gate record template

| Field | Value |
| --- | --- |
| Gate/source |  |
| Client/website/organization |  |
| Owner and approver |  |
| State (pending, blocked, partial, complete) |  |
| Evidence and date |  |
| Scope, expiry, and remaining risk |  |
| Next review date |  |
