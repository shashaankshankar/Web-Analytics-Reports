# Google Cloud production controls

Project: `web-analytics-agency-prod`  
Region: `us-central1`

This directory contains non-secret, reviewable configuration for the deployed measurement platform. It intentionally excludes OAuth client secrets, database credentials, trigger tokens, source credentials, and recipient addresses.

## Runtime and identities

- Cloud Run service: `measurement-reporting-platform`
- Runtime/data identity: `analytics-reporting-reader@web-analytics-agency-prod.iam.gserviceaccount.com`
- Scheduler and Cloud Tasks invoker: `measurement-scheduler@web-analytics-agency-prod.iam.gserviceaccount.com`
- Build identity: `measurement-builder@web-analytics-agency-prod.iam.gserviceaccount.com`
- Cloud Tasks queue: `measurement-sync`, one concurrent dispatch, five attempts, exponential backoff
- Report dispatcher: deliberately paused; do not resume without explicit operator direction

The runtime image is multi-stage, digest-pinned, and distroless. Production images must pass an authenticated `/ready` check, reject unauthenticated requests, and undergo On-Demand Scanning before the release is recorded as hardened. Source builds use the dedicated builder identity and a regional user-owned staging bucket; the default compute identity is not granted build access.

## Database

Cloud SQL instance `measurement-db` is regional HA, deletion-protected, encrypted-only, and requires the Cloud SQL Auth Proxy or a supported Cloud SQL connector. It retains 30 automated backups and seven days of point-in-time recovery. Query Insights is enabled without recording client addresses. Maintenance uses the stable production channel on Sunday at 06:00 UTC.

`RESTORE-RUNBOOK.md` defines the isolated recovery drill. The latest automated backup was restored to a temporary connector-only instance, validated against migrations, RLS policies, database roles, counts, and deterministic report-data hashes, then deleted after all three managed operations completed without errors.

## Monitoring

The deployed five-panel dashboard is defined by `monitoring-dashboard.json`. The callback's three-region HTTPS/JSON probe is defined by `oauth-callback-uptime.json`, and its multi-region outage policy is defined by `oauth-callback-unavailable-alert.json`. Active alert policies cover:

- Private Cloud Run and public OAuth callback HTTP 5xx responses
- Public OAuth callback external availability from Iowa, Oregon, and Virginia
- Cloud Run error-severity runtime logs
- Cloud Run p95 latency above five seconds
- Cloud Tasks non-OK attempts
- Cloud Scheduler error-severity attempts
- Cloud SQL availability
- Cloud SQL disk utilization above 80 percent
- Cloud SQL CPU utilization above 80 percent

Alerts notify the project-owned operator channel. Log-based metrics `measurement_scheduler_failures` and `measurement_runtime_errors` back the two control-plane policies. The default log bucket retains 180 days, with `_Default` sink exclusion `oauth_callback_query_privacy` (defined in `oauth-callback-log-exclusion.json`) preventing OAuth callback query strings from being retained. Both Cloud Run services send privacy-safe OpenTelemetry traces to the Google Telemetry API at a 100% sampling rate because traffic is low. Synchronous export keeps delivery inside request CPU allocation. Spans allow only method, route template, response status, service, revision, environment, project, and SDK metadata; do not add raw URLs, query strings, headers, bodies, identity values, or exception messages.

Project IAM enables Admin Read, Data Read, and Data Write audit logs for `allServices` with no exempt principals. Organization policies prevent user-managed service-account key creation and upload; the project currently has no user-managed service-account keys.

The billing account contains only this production project and has a $25 monthly budget with notifications at 50%, 90%, 100%, and 150% of current spend.

## Secrets and encryption

- Internal trigger secret versions are pinned; disabled versions must never be re-enabled.
- The OAuth refresh-token KMS key rotates every 90 days.
- OAuth uses dedicated Secret Manager resources `measurement-google-oauth-client-id`, `measurement-google-oauth-client-secret`, and `measurement-google-oauth-state-secret`. OAuth client-secret version 2 is pinned into private revision `measurement-reporting-platform-00047-ftn` and callback-only revision `measurement-oauth-callback-00006-kz9`; version 1 is disabled in Google Auth Platform and Secret Manager. Values are never stored in source or printed during configuration audits.
- Internal trigger versions 1 through 7 are disabled. The private service and all four Scheduler jobs use newline-free version 8; configuration verification compares values without printing them.
- Scheduler and task invocation use `measurement-scheduler`, the only direct Cloud Run service-account invoker; runtime data access and task enqueueing use `analytics-reporting-reader`.
- Secret values must not be printed by configuration audits; inspect only secret names, pinned versions, and state.

## Artifact lifecycle

Apply `artifact-cleanup-policy.json` to the `cloud-run-source-deploy` repository. It preserves the ten newest versions and deletes untagged versions older than 30 days.

## OAuth boundary

Google Auth Platform branding, audience, data-access declaration, verification, and web-client creation are console-managed. The app is External/Testing, has one approved operator test user, declares only `analytics.readonly`, and uses the exact callback documented in `EXTERNAL-SOURCE-ONBOARDING.md`. The `measurement-oauth-callback` service is public with `OAUTH_CALLBACK_ONLY=true` and a three-route application allowlist; every reporting and control-plane route remains private on `measurement-reporting-platform`. `GOOGLE_OAUTH_ENABLED=true` is separate from `GOOGLE_OAUTH_PRODUCTION_APPROVED=false`; do not set the latter true until approved public legal pages, an owned authorized domain, Google verification, access to the intended client property, and an explicit production decision all exist.
