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
       • Local interaction stats
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

Create client configuration files under `config/clients/<client-slug>.json` (see `config/clients/example-dental.json` or `config/clients/example-saas.json`).

### 3. Generate Reports via CLI

```bash
# List available client configurations
python -m app.cli list-clients

# Generate a 28-day growth briefing (HTML + Executive PDF)
python -m app.cli generate --client example-dental --mock

# Generate and send directly via Resend to client & agency
python -m app.cli generate --client example-dental --send
```

### 4. Run Test Suite

```bash
pytest
```
