# Client onboarding runbook

1. Record the company, website, business timezone, industry, and authorized owner.
2. Assign a versioned measurement contract; do not mutate historical definitions.
3. Complete route, URL, payload, consent, and privacy review. Healthcare remains default-deny.
4. Instrument the website through one collection owner and validate consent, duplicate suppression, prohibited routes, and backend-success semantics.
5. Obtain read-only GA4 access through a managed identity; do not download a long-lived key.
6. Discover and record the GA4 property and stream. One property represents one managed website unless a reviewed stream filter exists.
7. Create an effective-dated `website_analytics_assignment` and approve its reporting scope.
8. Seed versioned event, metric, mapping, and report definitions.
9. Run all five fixed windows, then verify provenance, quotas, completeness, thresholding, `(other)` loss, and freshness.
10. Re-run the same scheduled job key and verify no duplicate executions or facts.
11. Compare dashboard values to their snapshots and source executions.
12. Enable the dashboard only after measurement, governance, reporting, and sync health are separately visible.

Client #2 must use the same connector, sync, storage, response, and dashboard contracts. Add a new configuration/seed path; do not add client-specific query logic.
