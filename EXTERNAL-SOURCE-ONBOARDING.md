# External source activation

Updated August 13, 2026. The production platform is prepared for these connections but keeps each one disabled until its owner supplies and approves the required configuration. Secrets belong in Google Secret Manager, never Git, tickets, screenshots, or chat.

## Recurring email reports

Owner-supplied requirements:

- an approved recipient address and non-sensitive alias;
- a verified sending domain and sender address;
- an owned transactional-email API key;
- approved weekly or monthly cadence, period, timezone, and first run time.

Production variables are `REPORT_EMAIL_API_KEY`, `REPORT_EMAIL_FROM`, and `REPORT_RECIPIENTS_JSON`. Postgres stores only the recipient alias. Provider acceptance and office-inbox delivery remain separate evidence gates.

## Google Analytics self-service OAuth

Create a production Google web OAuth client with this exact redirect URI:

`https://measurement-reporting-platform-ptlwmdunva-uc.a.run.app/oauth/google/callback`

Supply the client ID and secret through Secret Manager, complete the Google consent-screen/verification requirements, then set `GOOGLE_OAUTH_PRODUCTION_APPROVED=true`. The app requests only `https://www.googleapis.com/auth/analytics.readonly`, uses offline access and PKCE, and stores refresh tokens under Cloud KMS. The official requirements are documented in [Google's web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server), [production-readiness policy](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance), and [OAuth security practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices).

## Marketing and business sources

Activation requires explicit ownership and scope for each source:

| Source | Required owner-supplied configuration | Approval boundary |
| --- | --- | --- |
| Google Ads | Developer token, customer ID, optional manager ID, read-only OAuth grant, currency confirmation | Cost/click reconciliation and campaign identifier review |
| Search Console | Verified property matching `https://thehouseofdentalwp.com`, read access for the runtime identity or approved OAuth connection | Query retention/privacy review; the API is enabled, but current operator and runtime credentials list zero accessible sites |
| Call tracking | Selected vendor, account/source identifier, credential or signed webhook secret, approved outcome mapping | No recordings, transcripts, phone numbers, or caller identifiers enter analytics storage |
| CRM/booking | Selected first-party system, credential, source-record identifiers, lifecycle mapping, identity-policy reference | Healthcare/privacy approval for matching outside GA4; only one-way keyed identifiers enter reporting |

The production API currently reports every source as `not_configured`. Do not change that state until source access validates, reconciliation passes, and the documented approval boundary is signed off.

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
