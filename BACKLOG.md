# Backlog

## Remaining external production gates

Status: the first-site production milestone, all applicable Phase 1 receipts, and the Phase 4 tenant-isolation gate pass. The client dashboard, approved goals, and PDF report foundation are live.

- Supply approved client users and real goal targets before creating either.
- Supply an approved report recipient and a new owned transactional-email credential before enabling recurring email delivery. The ignored local House of Dental Resend key returns HTTP 401; the different deployed Worker secret is valid but intentionally cannot be read or exported from Cloudflare.
- Create and approve a Google OAuth production client and consent screen before self-service connection can be enabled.
- Authorize and configure Google Ads, Search Console, call tracking, CRM/booking, and revenue sources before their later-stage integrations can go live.

The corresponding application flows, tenant schemas, KMS key, four schedulers, retention policy, external-source workers, and fail-closed status surfaces are already deployed. `EXTERNAL-SOURCE-ONBOARDING.md` lists the exact remaining owner-supplied values and approvals.

Inbox placement for the synthetic request remains optional office-side evidence; Resend acceptance and GA4 receipt are complete.
