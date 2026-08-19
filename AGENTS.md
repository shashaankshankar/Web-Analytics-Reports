# Repository Agent Instructions

## Working Principles

* Inspect before editing. Keep the code simple, clean, and directly aligned with real inputs.
* Prefer the smallest coherent change that fixes the root cause.
* Preserve existing behavior unless the task explicitly requires changing it.
* Do not invent synthetic metrics, fallbacks, or data fields that do not exist in the source APIs.
* Keep the project stateless and simple: client parameters are loaded from standard JSON configuration files.

## Architecture & Contracts

* **Client Configurations (`config/clients/<slug>.json`)**: Client details, branding colors, target recipients, and site IDs.
* **Data Ingestion (`app/sources/`)**: On-demand queries to GA4, Search Console, and Google Business Profile.
* **Metrics Pre-Processor (`app/analytics/`)**: Mathematical delta calculations (current vs. prior period) and keyword ranking analysis.
* **AI Insights (`app/ai/`)**: Structured summary generation from computed analytics.
* **Delivery (`app/delivery/`)**: HTML email and PDF generation via ReportLab.

## Verification

Verification is part of implementation.

After making changes:

1. Inspect the complete `git diff`.
2. Run `pytest` to ensure all tests pass.
3. Verify CLI runs cleanly: `python -m app.cli generate --client <slug> --mock`.

Do not claim success unless the relevant validation actually ran and passed.

Report:

* what changed
* what was verified
* exact validation commands and results
* assumptions or unresolved uncertainty

## Safety

* Do not commit, push, merge, deploy, alter production infrastructure, or modify credentials/secrets unless explicitly requested.
* Do not expose secrets in code, logs, diffs, or responses.
* Do not silently discard user changes.
* Stop and ask before making a destructive or difficult-to-reverse change.
* If correctness depends on an unverified architectural or domain assumption, investigate it before editing.

## Final Review

Before finishing, independently challenge the implementation:

* What assumption could still be wrong?
* Could this break an existing caller or workflow?
* Is every returned value backed by an authoritative source?
* Could this expose or mix data across tenants or websites?
* Are missing/error states represented honestly?
* Do the tests prove the intended behavior rather than merely mirror the implementation?

If a material uncertainty remains, report it instead of declaring the task complete.
