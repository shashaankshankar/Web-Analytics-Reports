# First Site: The House of Dental

Status: Live website; reporting connection pending  
Recorded: August 8, 2026  
Production status updated: August 12, 2026  
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
| GA4 property | `549721844` | Repository value; account-level access not yet verified |
| GA4 web stream | `15427015396` | Verified through the GA4 Admin API on 2026-08-12 |
| GA4 Measurement ID | `G-TC66MQQ0T7` | Enabled in the live website bundle |
| Public GA4 collection | Active after consent | Browser observed a GA4 collection response with HTTP 204 on August 12, 2026 |
| Reporting connection | Awaiting client grant | Service principal and read-only access are not configured |
| Privacy/legal approval record | Not found | Must be recorded separately from technical activation |

The domain is recorded from the website's canonical metadata and source configuration. Domain ownership, the GA4 property assignment, and the web-stream assignment still require confirmation from the client or authorized administrator before a managed connection is created.

## Healthcare and privacy classification

The first site is a healthcare website because it advertises dental care, facial-aesthetics services, appointment requests, and patient-facing care information.

Whether the business is legally HIPAA-regulated was not established by the repository. Treat the site as **healthcare/default-deny pending written client and legal/privacy confirmation**. The live implementation excludes form values and patient-specific data, but the missing approval record remains a governance gap.

The privacy and terms pages are live, but repository publication is not the same as documented legal approval. The contact form warns users not to include sensitive medical details.

## Existing analytics implementation

- The website source includes a direct `gtag.js` integration in `src/scripts/80-analytics.js`.
- GA4 is enabled by `measurement/site.json` with Measurement ID `G-TC66MQQ0T7`; the public bundle was verified on August 12, 2026.
- The integration uses Consent Mode v2 advanced defaults: analytics and advertising storage default to denied, with analytics storage granted only after the site's first-party choice.
- The integration creates a first-party privacy-choice banner and stores the choice under `thod-analytics-consent`.
- The integration implements the `local_service_v1` event taxonomy through a central allowlisted adapter. Business events do not send form values or arbitrary metadata.
- No Google Tag Manager container or GTM-specific implementation was found in the source.
- Cloudflare currently injects a Web Analytics beacon at the account level, but the repository's Content Security Policy blocks it. This produces a console error and should be disabled in Cloudflare unless separately approved.

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

Telephone links using `tel:+14076781400` are generated with semantic `phone_click` attributes. The implementation is locally verified; account-level GA4 receipt is still unverified.

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

The website does not establish `appointment_booked`. A successful Worker response triggers `form_submit` and `appointment_request`, meaning the message was accepted for notification; it is not a booked appointment or confirmed lead. `generate_lead` remains intentionally blocked.

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

The consent copy, privacy language, storage choice, route scope, and production approval are not yet validated for this measurement platform. Consent Mode does not replace healthcare/privacy review.

## Existing GTM or gtag installation

| System | State | Notes |
|---|---|---|
| Direct gtag.js | Active on the live site | `src/scripts/80-analytics.js`; Measurement ID `G-TC66MQQ0T7` |
| Google Tag Manager | Not found | No GTM container or GTM bootstrap was found in the source audit |
| Cloudflare Web Analytics | Account-injected and blocked by CSP | Disable unless separately approved; do not loosen CSP as a shortcut |

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

## Open production reporting items

These items do not block the already-live website, but they block a trustworthy production reporting launch:

1. Confirm client ownership of property `549721844`, web stream `15427015396`, and Measurement ID `G-TC66MQQ0T7` through an authorized GA4 account.
2. Confirm the GA4 property timezone and reporting scope.
3. Record the named healthcare/privacy decision and approval for the live consent and route policy.
4. Grant the reporting principal Viewer access and verify a read-only Data API request.
5. Complete production DebugView checks for each implemented event and confirm prohibited data is absent.
6. Verify Resend sender-domain status and appointment inbox delivery separately from HTTP acceptance.
7. Disable the unapproved Cloudflare Web Analytics injection or approve and integrate it intentionally.
8. Add production authentication, managed Postgres, migrations, sync scheduling, and operational monitoring before exposing this dashboard outside a trusted local environment.

## First-site gate

The website launch gate is complete: the canonical domain is publicly serving the Cloudflare build. The server-side reporting gate is not complete until the read-only GA4 assignment, property timezone, authentication, persistence, and live data reconciliation are verified.

The public site is already instrumented. Do not confuse that technical state with a verified managed reporting connection or a recorded healthcare/privacy approval.
