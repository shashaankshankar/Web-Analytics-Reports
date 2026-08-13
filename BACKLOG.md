# Backlog

## First-site production milestone

Status: governance, persisted reporting, scheduling, the private dashboard, and the production request handoff are live. One operational measurement check remains before Phase 1 can be closed.

- Run one explicitly authorized test after granting analytics consent.
- Verify exactly one each of `form_submit`, `generate_lead`, and `appointment_request` in the GA4 Realtime API.
- If office-inbox access becomes available, record inbox placement separately from Resend acceptance.
- Preserve the Admin API-verified assignment: property `549721844`, web stream `15427015396`, Measurement ID `G-TC66MQQ0T7`, and timezone `America/New_York`.

The public website collects only consent-controlled GA4 traffic. Cloud Scheduler and Cloud Tasks synchronize fixed-period GA4 reports into Cloud SQL, and dashboard reads use stored snapshots rather than live Google requests.
