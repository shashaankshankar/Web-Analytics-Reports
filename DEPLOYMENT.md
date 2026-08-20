# Scheduled report deployment

This repository is prepared for a private Google Cloud Run service invoked by
authenticated Cloud Scheduler jobs. Deployment and external account changes
remain operator-controlled; this document does not contain secrets or a
project ID.

## Runtime settings

Set these through Secret Manager or Cloud Run environment configuration:

- `OPENROUTER_API_KEY`
- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- `REPORT_DELIVERY_ENABLED=true`
- `REPORT_ALLOWED_CLIENTS=thehouseofdental`

Use the Cloud Run service account's Application Default Credentials for GA4,
Search Console, and any approved GBP access. Do not upload a service-account
key file to the image or repository.

## Build and deploy

```bash
gcloud run deploy client-growth-reports \
  --source . \
  --region us-east1 \
  --no-allow-unauthenticated \
  --service-account reports-runtime@PROJECT_ID.iam.gserviceaccount.com \
  --set-env-vars REPORT_DELIVERY_ENABLED=true,REPORT_ALLOWED_CLIENTS=thehouseofdental \
  --set-secrets OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest,RESEND_API_KEY=RESEND_API_KEY:latest,RESEND_FROM_EMAIL=RESEND_FROM_EMAIL:latest
```

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
  "send_email": true,
  "mock_data": false
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
  --message-body='{"client_slug":"thehouseofdental","report_type":"weekly","send_email":true,"mock_data":false}' \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com \
  --oidc-token-audience="https://SERVICE_URL"

gcloud scheduler jobs create http thehouseofdental-performance \
  --location us-east1 \
  --schedule="0 9 1 * *" \
  --time-zone="America/New_York" \
  --uri="https://SERVICE_URL/reports/generate" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"client_slug":"thehouseofdental","report_type":"performance","send_email":true,"mock_data":false}' \
  --oidc-service-account-email=scheduler@PROJECT_ID.iam.gserviceaccount.com \
  --oidc-token-audience="https://SERVICE_URL"
```

Only add a client to `REPORT_ALLOWED_CLIENTS` after its Google permissions,
sender-domain authorization, configured recipient, and controlled inbox test
are independently verified. Example clients remain disabled.

## Release evidence

Provider acceptance is not inbox delivery. Record the Cloud Run revision,
Scheduler execution, Resend message ID, and confirmed inbox receipt
separately. Do not enable a client job based only on local tests or a mock
render.
