# Backlog

## First-site reporting activation

Status: pending client/business action; intentionally not blocking local development.

- Obtain approval for healthcare/privacy status.
- Approve route-level analytics eligibility.
- Approve consent behavior and production wording.
- Reconcile property `549721844`, web stream `15408312790`, Measurement ID `G-TC66MQQ0T7`, and the property timezone in the client-owned GA4 account.
- Verify the appointment backend success semantics.
- Record the named privacy/legal approval for the already-live consent and route policy.
- Grant the reporting service account Viewer access and promote the assignment from `awaiting_client_grant` to `approved` only after a live read succeeds.

The public website is already collecting consent-controlled GA4 traffic. Until this item is closed, the reporting connector and synchronization remain disabled and the dashboard must show unavailable data rather than zero.
