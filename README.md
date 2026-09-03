# Client Growth Reports

A simple, website-agnostic performance reporting tool. It reads client site configs, fetches analytics on demand (GA4, Google Search Console, Google Business Profile), calculates metric deltas, generates structured AI insights, and produces email briefings and PDF reports.

## Architecture

```text
[ Trigger: CLI / Scheduled Cron / Webhook ]
                     │
         [ Client Site Config Loader ]
     (Reads config/clients/<slug>.json)
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   [ GA4 API ]  [ GSC API ]   [ GBP API ]   (Stateless, read-only on-demand queries)
       └─────────────┬─────────────┘
                     ▼
       [ Deterministic Aggregator ]
       • 28d vs prior 28d metric deltas
       • Acquisition channels & landing pages
       • Striking-distance SEO keywords (rank 8–20)
       • GBP profile NAP, hours, categories, and services
       • All returned GBP Performance daily metrics and period deltas
       • Monthly GBP search keywords, paginated managed reviews, and reply status
       • Optional answered/missed Business Calls insights
                     │
                     ▼
          [ AI Growth Analyst ]
       • Executive summary
       • Traffic & keyword opportunities
       • Recommended action items
                     │
                     ▼
         [ Multi-Format Delivery ]
       • Responsive HTML email
       • Branded PDF report (ReportLab)
       • Email dispatch (Resend API)
```

## Quick Start

### 1. Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure Clients

Create real client configuration files under `config/clients/<client-slug>.json`. Synthetic clients and source fixtures belong in tests only.

For Google Business Profile, set `gbp_location_id` to the private
`locations/<id>` resource when known, or to a public `places/<place-id>` value.
After the authorized user's OAuth bundle is available, the connector can match
the public Place ID to the managed account and private location. Set the
optional `gbp_account_id` when the managed `accounts/<id>` resource is already
known; it is required for the managed Reviews endpoint unless the connector
can resolve it. Private GBP source fields remain unavailable until OAuth and
manager access are independently verified.

The Google Cloud project also needs approved Google Business Profile Basic API
Access. Enabling the APIs alone can leave the project at quota zero, which
returns HTTP 429 when the connector tries to enumerate managed locations.

The OAuth connection uses a separate callback-only service. Start it at
`https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app/oauth/google/start`
after the callback service and Secret Manager bindings in
[DEPLOYMENT.md](DEPLOYMENT.md) are ready. Do not reuse an authorization code
from a failed callback or expose OAuth credentials in the browser, repository,
or logs. The callback verifies the manager account can access the configured
client Place ID before storing the refresh credential.

For each real client, keep two dates distinct when they are known:

- `site_launch_date` is optional client-confirmed context about when the public website launched. It does not decide whether analytics data is reportable.
- `measurement_start_date` is optional but should be set when the analytics property has a known first trustworthy reporting date. It controls report eligibility.

When a requested prior period is before, or overlaps, `measurement_start_date`, the performance command automatically creates an **Initial Measurement Baseline**. The observed window begins on `measurement_start_date`, current source-backed values are shown, and prior values, deltas, movement claims, and comparison findings are suppressed. The report is not labeled as a complete 28-day report. A normal comparison report requires a full current and prior source-covered window on or after the measurement start date.

### 3. Generate Reports via CLI

```bash
# List available client configurations
python -m app.cli list-clients

# Generate a 28-day growth briefing from configured live sources (HTML + Executive PDF)
python -m app.cli generate --client thehouseofdental --report performance

# Override the configured Deep Insights setting for one performance run
python -m app.cli generate --client thehouseofdental --report performance --explore
python -m app.cli generate --client thehouseofdental --report performance --no-explore

# Production delivery requires the explicit REPORT_DELIVERY_ENABLED and
# REPORT_ALLOWED_CLIENTS settings in DEPLOYMENT.md. OpenRouter and Google
# credentials must be available; there is no runtime mock mode.
```

### 4. Run Test Suite

```bash
pytest
```

## Scheduled production deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the private Cloud Run and authenticated
Cloud Scheduler setup. Local runs never enable delivery. Production
email requires `REPORT_DELIVERY_ENABLED=true` and an explicit
`REPORT_ALLOWED_CLIENTS` allowlist; the initial allowlist should contain only
`thehouseofdental`. Production Cloud Run deployment is gated to a clean,
published `main` checkout by `scripts/deploy_cloud_run.sh`.
