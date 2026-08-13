# Backlog

## Remaining external production gates

Status: the first-site production milestone, all applicable Phase 1 receipts, and the Phase 4 tenant-isolation gate pass. The client dashboard, approved goals, and PDF report foundation are live.

- Supply approved client users and real goal targets before creating either.
- Keep recurring report dispatch paused until the operator explicitly asks to enable it. The secure relay and operator recipient are configured, but the synthetic schedule is disabled and no report was sent.
- Create and approve a Google OAuth production client and consent screen before self-service connection can be enabled.
- Authorize and configure Google Ads, call tracking, CRM/booking, and revenue sources before those later-stage integrations can go live. Search Console is connected; monitor the new property until Google supplies finalized rows, while preserving the required partial-coverage label.

The corresponding application flows, tenant schemas, rotating KMS key, four schedulers, regional Cloud SQL, monitoring policies/dashboard, retention policy, external-source workers, and fail-closed status surfaces are deployed. `EXTERNAL-SOURCE-ONBOARDING.md` lists the exact remaining owner-supplied values and approvals.

Inbox placement for the synthetic request remains optional office-side evidence; Resend acceptance and GA4 receipt are complete.
