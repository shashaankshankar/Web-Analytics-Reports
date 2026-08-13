# Backlog

## Agency console and tenant isolation

Status: the first-site production milestone and all applicable Phase 1 event receipts pass. Phase 4 work is now authorized by the architecture gate.

- Replace the single shared API token model with authenticated organization, company, website, membership, and role context.
- Enforce tenant context in the service layer and Postgres RLS using separate ingestion, administration, and tenant application roles.
- Add adversarial API, service, and database isolation tests before enabling the client portal.
- Complete portfolio, operator-alert, and annotation workflows with incompatible-contract warnings.

Inbox placement for the synthetic request remains optional office-side evidence; Resend acceptance and GA4 receipt are complete.
