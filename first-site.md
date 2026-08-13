# First Site: The House of Dental

Status: Live website and active managed reporting connection
Recorded: August 8, 2026
Production status updated: August 13, 2026
Site count: 1

## Decision

The first website on the platform is **The House of Dental**, a dental and facial-aesthetics website serving Winter Park, Florida. It is live on Cloudflare at `https://thehouseofdentalwp.com`.

This is the first client website in scope. The platform should preserve its reusable multi-client boundaries, but production activation must be completed and verified for this site before onboarding another one.

## Site identity

| Field | Current value | Evidence / status |
|---|---|---|
| Company | The House of Dental | `src/data/site.json`; site README |
| Website/domain | `https://thehouseofdentalwp.com` | Live HTTP 200 and canonical URL verified August 12, 2026 |
| Location | 6504 University Blvd, Winter Park, FL 32792 | `src/data/site.json`; site footer/contact page |
| Primary phone | (407) 678-1400 | `src/data/site.json`; telephone CTAs throughout the site |
| GA4 property | `549721844` | Verified through GA4 Admin and Data APIs on August 12-13, 2026 |
| GA4 web stream | `15427015396` | Verified through the GA4 Admin API on 2026-08-12 |
| GA4 Measurement ID | `G-TC66MQQ0T7` | Enabled in the live website bundle |
| Public GA4 collection | Active after consent | Browser observed a GA4 collection response with HTTP 204 on August 12, 2026 |
| Reporting connection | Active | Managed runtime ADC has read-only access; fixed-period sync is live |
| Privacy/governance approval | Approved | Shashaank Shankar approved the healthcare/default-deny policy, routes, consent, contract, mappings, and request semantics on August 12, 2026 |

The canonical domain, GA4 property, stream, Measurement ID, and property timezone are verified and stored as the approved website assignment.

## Healthcare and privacy classification

The first site is a healthcare website because it advertises dental care, facial-aesthetics services, appointment requests, and patient-facing care information.

Whether the business is legally HIPAA-regulated is not asserted by this platform. The approved technical policy is **healthcare/default-deny**: form values, direct identifiers, patient-specific URLs, and sensitive free text are prohibited from analytics. This is an implementation/governance approval, not legal advice.

The privacy and terms pages are live, but repository publication is not the same as documented legal approval. The contact form warns users not to include sensitive medical details.

## Existing analytics implementation

- The website source includes a direct `gtag.js` integration in `src/scripts/80-analytics.js`.
- GA4 is enabled by `measurement/site.json` with Measurement ID `G-TC66MQQ0T7`; the public bundle was verified on August 12, 2026.
- The integration uses Consent Mode v2 advanced defaults: analytics and advertising storage default to denied, with analytics storage granted only after the site's first-party choice.
- The integration creates a first-party privacy-choice banner and stores the choice under `thod-analytics-consent`.
- The integration implements the `local_service_v1` event taxonomy through a central allowlisted adapter. Business events do not send form values or arbitrary metadata.
- No Google Tag Manager container or GTM-specific implementation was found in the source.
- The authenticated production dashboard and current website QA complete without browser-console errors. Cloudflare Web Analytics is not part of the approved measurement owner; the repository Content Security Policy does not permit an additional beacon.

## Existing forms

The site has one appointment-request form on `/contact#book`:

- Form action: `POST /api/contact`
- Form handler: Cloudflare Worker `worker/index.mjs`
- User fields: full name, phone number, email, new-patient choice, and free-text message
- Honeypot field: `company`
- Client behavior: submits with `fetch`, then displays success or failure status
- Current business meaning: an appointment request that the front desk will call to confirm; it is not itself a booked appointment
- Backend state: the Worker validates the origin and payload and forwards through Resend only when the server-side secret and sender configuration are available
- Privacy boundary: the form explicitly asks users not to include sensitive medical details

The form fields contain direct identifiers and potentially sensitive free text. They must never be copied into GA4 event parameters, URLs, page titles, custom dimensions, logs, or dashboard payloads.

## Existing calls, email CTAs, and appointment flows

### Phone CTAs

Telephone links using `tel:+14076781400` are generated with semantic `phone_click` attributes. The GA4 Realtime Data API observed one authorized production receipt.

### Email CTAs

No `mailto:` CTA was found in the current source. `email_click` therefore has no current website trigger and must remain unimplemented until an approved email CTA exists.

### Appointment/request flow

The current flow is:

```text
Book / Contact CTA
      -> /contact#book
      -> appointment-request form
      -> POST /api/contact
      -> Resend acceptance, when configured
      -> front desk calls to confirm
```

The website does not establish `appointment_booked`. A successful Worker response triggers `form_submit`, `generate_lead`, and `appointment_request`, meaning an approved request was accepted for office follow-up; it is not a booked or confirmed appointment. An authorized consented production test produced exactly one of each event.

## Consent mechanism

The source includes an active first-party consent mechanism, conditional on the valid GA4 configuration and approved route policy.

Current behavior in source:

- Consent Mode v2 advanced configuration
- Default `analytics_storage: denied`
- Default advertising storage and advertising personalization signals: denied
- User choices: allow analytics or continue without analytics
- Choice persisted in local storage under `thod-analytics-consent`
- A settings control allows the visitor to change the choice
- The GA4 library loads with denied storage under advanced Consent Mode; business events require granted analytics storage

The consent copy, storage behavior, explicit route scope, and production request semantics are approved and validated. Consent Mode does not replace healthcare/privacy or legal review.

## Existing GTM or gtag installation

| System | State | Notes |
|---|---|---|
| Direct gtag.js | Active on the live site | `src/scripts/80-analytics.js`; Measurement ID `G-TC66MQQ0T7` |
| Google Tag Manager | Not found | No GTM container or GTM bootstrap was found in the source audit |
| Cloudflare Web Analytics | Not an approved collection owner | Keep it disabled/unavailable unless separately reviewed; do not loosen CSP as a shortcut |

## Initial route inventory

The current website export contains these page routes:

```text
/
/about
/accessibility
/contact
/facial-aesthetics
/new-patients
/pre-post-op
/privacy
/reviews
/services
/terms
```

The deployment also defines legacy redirects for `/home`, `/about-us`, `/dental-services`, `/new-patient`, and `/contact-us`. All declared clean routes are enabled in the website's route manifest; unknown and unclassified routes remain prohibited.

## Remaining external evidence and optional expansion

The trustworthy first-site reporting launch is complete. Remaining items are separate external evidence or later-stage expansion:

1. Dental-office inbox placement can be checked independently; Resend acceptance is proven.
2. DebugView/Tag Assistant is optional additional UI evidence; live Realtime API receipts plus code and privacy fixtures cover the applicable event gate.
3. Search Console is verified and connected with truthful partial/top-row coverage. Google Ads, call tracking, and CRM/booking remain unconfigured until their owners provide access and approve the source-specific policies in `EXTERNAL-SOURCE-ONBOARDING.md`.
4. Recurring email infrastructure and its secure relay are deployed, but the dispatcher is deliberately paused and the synthetic schedule disabled per operator direction.
5. Self-service OAuth remains inactive until a production Google OAuth client and consent screen are approved.

## First-site gate

The website and first-site reporting gates are complete. The canonical Cloudflare deployment, consented live event receipts, read-only GA4 assignment, property timezone, private Cloud Run service, Cloud SQL persistence, scheduled per-assignment synchronization, dashboard/PDF output, and privacy governance are verified.

Do not broaden this proof to unconfigured external sources or claim appointment, inbox, customer, or revenue outcomes that the approved owning systems have not supplied.
