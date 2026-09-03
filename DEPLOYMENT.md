# Scheduled report deployment

This repository is prepared for a private Google Cloud Run service invoked by
authenticated Cloud Scheduler jobs. Deployment and external account changes
remain operator-controlled; this document does not contain secrets.

## Deployment gate

Production Cloud Run deployments must use the repository wrapper:

```bash
./scripts/deploy_cloud_run.sh callback
./scripts/deploy_cloud_run.sh reports
```

The wrapper invokes `scripts/assert_main_branch.sh` before it calls Google
Cloud. A local deployment is rejected unless the checkout is on `main`, the
worktree is clean, and `HEAD` is the exact commit currently published as
`origin/main`. In Cloud Build, the provider-supplied `BRANCH_NAME` must be
`main` and tag builds are rejected. In GitHub Actions, the ref must be exactly
`refs/heads/main`.

The wrapper is fixed to the production project and the two production services.
Do not use a raw `gcloud run deploy` command for production; it cannot verify
which Git branch supplied the source directory.

This repository guard is not an IAM boundary. A project owner or another
principal with direct Cloud Run deployment permission can still bypass any
repository script. For an enforceable hosted policy, use a dedicated deployer
service account behind a Cloud Build trigger restricted to `^main$`, and remove
direct Cloud Run deployment permissions from human identities. Keep that IAM
change separate from the repository guard so it can be reviewed independently.

## Runtime settings

Set these through Secret Manager or Cloud Run environment configuration:

- `OPENROUTER_API_KEY`
- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- `GBP_OAUTH_CREDENTIALS_JSON` (Secret Manager-backed JSON containing the
  authorized GBP user's OAuth `client_id`, `client_secret`, `refresh_token`,
  and optional `token_uri`)
- `GBP_OAUTH_CLIENT_ID`, `GBP_OAUTH_REDIRECT_URI`, `GBP_OAUTH_SECRET_ID`, and
  `GBP_OAUTH_EXPECTED_PLACE_ID` configure the callback-only OAuth service. Inject
  `GBP_OAUTH_CLIENT_SECRET` from Secret Manager; never place the client secret
  in the image, repository, browser, or logs.
- `GBP_ACCOUNT_ID` is optional. Prefer `gbp_account_id` in the client JSON when
  known; otherwise the adapter resolves the managed `accounts/<id>` and
  `locations/<id>` resources by matching the configured public Place ID after
  OAuth access is available.
- `REPORT_DELIVERY_ENABLED=true`
- `REPORT_ALLOWED_CLIENTS=thehouseofdental`

Use the Cloud Run service account's Application Default Credentials for GA4 and
Search Console. Private GBP profile, Performance, managed Reviews, and optional
Business Calls data use the authorized user's OAuth bundle. Do not upload a
service-account key file to the image or repository.

Before the first live GBP call, the Google Cloud project must have approved
Business Profile API access. Enabling the individual APIs is not sufficient:
Google can leave the Account Management or Performance quota at zero until the
Basic API Access application is approved. Check the project's GBP quota before
repeating OAuth authorization; a 429 with no effective quota is an access gate,
not a transient rate spike. Submit the application through Google's
[Business Profile API access form](https://support.google.com/business/contact/api_default).

## GBP OAuth callback service

The OAuth callback is a separate, callback-only Cloud Run service. Keep the
reporting service private; do not make `/reports/generate` publicly reachable
just to receive the Google redirect.

The Google OAuth web client must contain this exact redirect URI:

```text
https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app/oauth/google/callback
```

Provision these secrets before deploying the callback service:

- `GBP_OAUTH_CLIENT_SECRET`: the current secret for the Google OAuth web
  client. Add it without printing it or placing it in source control.
- `GBP_OAUTH_CREDENTIALS_JSON`: create the secret before the first callback;
  the callback service adds the first version after Google returns a refresh
  token. The reporting service reads this secret after that version exists.

The callback verifies that the authorized user can enumerate the configured
`GBP_OAUTH_EXPECTED_PLACE_ID` through the private Business Profile APIs before
adding a credential version. This prevents an unrelated Google account from
rotating the shared reporting credential through the public redirect endpoint.

The callback service account needs only `roles/secretmanager.secretAccessor`
on `GBP_OAUTH_CLIENT_SECRET` and
`roles/secretmanager.secretVersionAdder` on
`GBP_OAUTH_CREDENTIALS_JSON`. The reporting service account needs
`roles/secretmanager.secretAccessor` on `GBP_OAUTH_CREDENTIALS_JSON`.

Set the non-secret OAuth identifiers in the deployment shell, then deploy the
callback-only service through the guarded wrapper. The wrapper supplies the
project-specific service account and secret bindings:

```bash
export GCP_PROJECT_ID=web-analytics-agency-prod
export GBP_OAUTH_CLIENT_ID=OAUTH_CLIENT_ID
export GBP_OAUTH_EXPECTED_PLACE_ID=PUBLIC_PLACE_ID
./scripts/deploy_cloud_run.sh callback
```

Start a fresh authorization at:

```text
https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app/oauth/google/start
```

After the callback displays its success page and an enabled version exists,
grant the reporting service access to `GBP_OAUTH_CREDENTIALS_JSON` and deploy
the private reporting service through the guarded wrapper:

```bash
./scripts/deploy_cloud_run.sh reports --with-gbp-credentials
```

The callback rejects missing or mismatched state cookies, verifies access to
the expected location, does not log Google response bodies, discards the
short-lived access token, and stores only the refreshable OAuth bundle. The
authorization code from the failed 404 attempt must not be reused; start a
new flow after deploying the callback service.

## Build and deploy

```bash
./scripts/deploy_cloud_run.sh reports
```

Use `./scripts/deploy_cloud_run.sh reports --with-gbp-credentials` only after
`GBP_OAUTH_CREDENTIALS_JSON` has an enabled Secret Manager version. The wrapper
checks that condition before creating a new revision.

Grant the runtime service account only the Google API access required by the
enabled client configurations. Grant a separate Scheduler service account
`roles/run.invoker` on this service.

```bash
gcloud run services add-iam-policy-binding client-growth-reports \
  --region us-east1 \
  --member serviceAccount=scheduler@PROJECT_ID.iam.gserviceaccount.com \
  --role roles/run.invoker
```

## Scheduler payloads

The service accepts `POST /reports/generate` with JSON such as:

```json
{
  "client_slug": "thehouseofdental",
  "report_type": "weekly",
  "send_email": true
}
```

Create authenticated OIDC Scheduler jobs with the Cloud Run URL as both the
target and token audience:

- Weekly: Monday at 09:00 in `America/New_York`
- Performance: day 1 of each month at 09:00 in `America/New_York`

```bash
gcloud scheduler jobs create http thehouseofdental-weekly \
  --location us-east1 \
  --schedule="0 9 * * 1" \
  --time-zone="America/New_York" \
  --uri="https://SERVICE_URL/reports/generate" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"client_slug":"thehouseofdental","report_type":"weekly","send_email":true}' \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com \
  --oidc-token-audience="https://SERVICE_URL"

gcloud scheduler jobs create http thehouseofdental-performance \
  --location us-east1 \
  --schedule="0 9 1 * *" \
  --time-zone="America/New_York" \
  --uri="https://SERVICE_URL/reports/generate" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"client_slug":"thehouseofdental","report_type":"performance","send_email":true}' \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com \
  --oidc-token-audience="https://SERVICE_URL"
```

Only add a client to `REPORT_ALLOWED_CLIENTS` after its Google permissions,
sender-domain authorization, configured client and `agency_audit` recipients,
and controlled inbox tests are independently verified. Deep Insights should
remain disabled in a client config until its source coverage is ready; a
per-run API or CLI override is available for controlled validation.

### Measurement start and initial baselines

`site_launch_date` and `measurement_start_date` are separate client fields.
The first is optional launch context supplied by the client. The second is the
first date the analytics property is trusted to provide reportable data and is
the field used by the report selector. If a performance report's prior window
predates or overlaps that date, the run becomes an Initial Measurement Baseline:
it queries from measurement start through the report end, suppresses all prior
values and movement deltas, and records the requested comparison as suppressed
metadata in the internal audit. A normal comparison requires both full windows
to be on or after measurement start and covered by the source.

## Release evidence

Provider acceptance is not inbox delivery. Record the Cloud Run revision,
Scheduler execution, Resend message ID, and confirmed inbox receipt
separately. Do not enable a client job based only on local tests or a local
render. A generated audit artifact is evidence of the run, not proof of inbox
placement.
