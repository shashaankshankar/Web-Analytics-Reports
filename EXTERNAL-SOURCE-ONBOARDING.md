# External source activation

Updated August 13, 2026. The production platform is prepared for these connections and keeps each one disabled until its owner supplies and approves the required configuration. Search Console is the first approved exception and is active through runtime ADC. Secrets belong in Google Secret Manager, never Git, tickets, screenshots, or chat.

## Recurring email reports

Owner-supplied requirements:

- an approved recipient address and non-sensitive alias;
- a verified sending domain and sender address;
- an owned transactional-email API key;
- approved weekly or monthly cadence, period, timezone, and first run time.

Production variables are `REPORT_EMAIL_API_KEY`, `REPORT_EMAIL_FROM`, and `REPORT_RECIPIENTS_JSON`. Postgres stores only the recipient alias. Provider acceptance and office-inbox delivery remain separate evidence gates.

## Google Analytics self-service OAuth

The Google web OAuth client is active in restricted Testing mode with this exact redirect URI:

`https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app/oauth/google/callback`

The client ID, client secret, and state-signing secret are supplied to both Cloud Run services through pinned Secret Manager versions. The callback service is intentionally public but returns 404 for everything except `/health`, `/healthz`, and `/oauth/google/callback`; the reporting service remains private. `GOOGLE_OAUTH_ENABLED=true` permits only the approved Testing-mode user, while `GOOGLE_OAUTH_PRODUCTION_APPROVED=false` truthfully records that the app is not publicly published or verified. The app requests only `https://www.googleapis.com/auth/analytics.readonly`, uses offline access and PKCE, and stores verifiers and refresh tokens under Cloud KMS. The operator grant and property discovery are proven, but the returned property does not match the approved House of Dental property and remains unassigned. Before setting the production-approval flag, publish approved platform homepage/privacy/terms pages on an owned authorized domain, complete Google verification, confirm access to the intended client property, and record the explicit production decision. The official requirements are documented in [Google's web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server), [production-readiness policy](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance), and [OAuth security practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices).

## Marketing and business sources

Activation requires explicit ownership and scope for each source:

| Source | Required owner-supplied configuration | Approval boundary |
| --- | --- | --- |
| Google Ads | Developer token, customer ID, optional manager ID, read-only OAuth grant, currency confirmation | Cost/click reconciliation and campaign identifier review |
| Search Console | Verified property matching `https://thehouseofdentalwp.com`, read access for the runtime identity or approved OAuth connection | Connected: the URL-prefix property is verified, the operator and runtime service account are owners, and the pinned ADC connection is approved. Query text retention remains disabled. |
| Call tracking | Selected vendor, account/source identifier, credential or signed webhook secret, approved outcome mapping | No recordings, transcripts, phone numbers, or caller identifiers enter analytics storage |
| CRM/booking | Selected first-party system, credential, source-record identifiers, lifecycle mapping, identity-policy reference | Healthcare/privacy approval for matching outside GA4; only one-way keyed identifiers enter reporting |

The production API reports Search Console as `partial_data` because the newly verified property has no finalized approved rows yet and the Search Analytics API exposes partial top-row coverage. Google Ads, call tracking, and CRM/booking remain `not_configured`. Do not activate another source until its access validates, reconciliation passes, and the documented approval boundary is signed off.

### Secret and activation contract

Every connection references an exact Secret Manager version such as `projects/PROJECT/secrets/SOURCE/versions/7`; `/latest` and raw credentials are rejected. Grant the Cloud Run runtime identity Secret Accessor only on the selected source secret.

The Google Ads secret contains this JSON shape:

```json
{
  "refreshToken": "OWNER_SUPPLIED",
  "clientId": "OWNER_SUPPLIED",
  "clientSecret": "OWNER_SUPPLIED",
  "developerToken": "OWNER_SUPPLIED"
}
```

Search Console uses the first three fields or `{"credentialMode":"adc"}` after its property owner grants the runtime identity read access. Call tracking and CRM/booking use `{"outcomeHashSecret":"OWNER_SUPPLIED_32_PLUS_CHARACTERS"}`. Never paste real values into the API request: registration contains only the version-pinned reference.

Activation sequence:

1. Register the source with `POST /api/websites/{websiteId}/external-sources`.
2. Validate access and record the owner/governance reference with `POST /api/websites/{websiteId}/external-sources/{sourceType}/approve`.
3. Google Ads and Search Console then enter the 5:00 AM Eastern isolated scheduler. Call/CRM systems send approved batches to `/outcomes` with a unique request ID.
4. Reconcile the returned execution totals against the source owner before relying on the data.

Search Console is labeled `partial_top_rows` even after successful pagination because the Search Analytics API does not guarantee every dimension row. Query text remains hashed unless its retention was separately approved.
