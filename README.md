# Measurement and Reporting Platform

Production FastAPI service for privacy-aware, stored GA4 reporting. House of Dental is the first website. The service is deployed privately on Cloud Run, uses read-only Application Default Credentials, synchronizes fixed reporting windows through Cloud Scheduler and Cloud Tasks, and stores versioned facts and report provenance in Cloud SQL Postgres.

## Production

- Cloud Run service: `measurement-reporting-platform`, `us-central1`
- Canonical private URL: `https://measurement-reporting-platform-ptlwmdunva-uc.a.run.app`
- Cloud SQL: `web-analytics-agency-prod:us-central1:measurement-db`
- Queue: `measurement-sync`, one concurrent dispatch, five attempts
- Scheduler: `measurement-daily-sync`, daily at 3:15 AM `America/New_York`
- Runtime identity: `analytics-reporting-reader@web-analytics-agency-prod.iam.gserviceaccount.com`

Cloud Run IAM protects the entire production service. Authorized operators can open it through a local authenticated proxy:

```sh
gcloud run services proxy measurement-reporting-platform \
  --region us-central1 \
  --project web-analytics-agency-prod
```

Then open `http://127.0.0.1:8080/dashboard`.

## Local development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn app.main:app --reload
```

The reporting APIs read stored snapshots, not live GA4 requests. Sync workers are the only path that queries GA4. Failed or incomplete queries do not become zeros; a successful complete query with no matching rows is represented as `empty_complete`.

## Production operations

- `/health` is process liveness only.
- `/ready` verifies live configuration and database migration state.
- `/dashboard` is the single-client dashboard.
- `/api/websites/website_house_of_dental/sync-status` exposes freshness, failures, and quality.
- `/api/websites/website_house_of_dental/measurement-health` keeps collection, persistence, assignment, and governance separate.
- Internal `/internal/schedule` and `/internal/sync` routes require Cloud Run IAM and a rotated Secret Manager trigger.

Database migrations are in `infra/postgres/`. Reapplying them is safe. Do not place database, trigger, Google, or API credentials in the repository.
