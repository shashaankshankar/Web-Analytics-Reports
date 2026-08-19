# Automated AI Growth Briefings & Local SEO Intelligence Engine

A website-agnostic digital growth intelligence engine. It statelessly queries Google analytics/search APIs on demand, pre-computes deterministic metric deltas, synthesizes executive insights via AI, and delivers branded HTML briefings with attached executive PDF reports directly to clients.

## Architecture

```text
[ Trigger: CLI / Scheduled Cron / Webhook ]
                     │
         [ Client Site Config Loader ]
     (Reads config/clients/<client-slug>.json: GA4 ID, GSC URL, GBP ID, Recipients)
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   [ GA4 API ]  [ GSC API ]   [ GBP API ]   (Stateless, read-only on-demand queries)
       └─────────────┬─────────────┘
                     ▼
       [ Deterministic Aggregator ]
       • 28d vs prior 28d metric deltas & percentage changes
       • Top acquisition channels & high-intent landing pages
       • Striking-distance SEO queries (positions 8–20 with high impressions)
       • Local intent (calls, directions, reviews)
                     │
                     ▼
          [ LLM Growth Analyst ]
       • Industry-agnostic structured prompts
       • Executive Snapshot (30-second takeaway)
       • Acquisition, engagement, and conversion insights
       • Concrete monthly agency action items
                     │
                     ▼
         [ Multi-Format Delivery ]
       • Responsive HTML Email Briefing
       • Branded Executive PDF Attachment (ReportLab)
       • Email Dispatcher (Resend API)
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
