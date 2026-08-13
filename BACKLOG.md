# Backlog

## First-site reporting activation

Status: direct GA4 reporting is active locally. The remaining items are governance and persistence work, not blockers for local live reads.

- Obtain approval for healthcare/privacy status.
- Approve route-level analytics eligibility.
- Approve consent behavior and production wording.
- Reconcile property `549721844`, web stream `15408312790`, Measurement ID `G-TC66MQQ0T7`, and the property timezone in the client-owned GA4 account.
- Verify the appointment backend success semantics.
- Record the named privacy/legal approval for the already-live consent and route policy.
- Record the authorized reporting principal and retain its Viewer-level access for the FastAPI runtime.

The public website is already collecting consent-controlled GA4 traffic. The FastAPI dashboard reads GA4 directly on request and returns zero only when GA4 returns no matching event rows; persistence and scheduled synchronization remain separate future work.
