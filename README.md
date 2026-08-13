# Measurement and Reporting Platform

Production-oriented implementation scaffold for the agency GA4 reporting platform. House of Dental is the first live website. The application keeps server-side connectors disabled by default, never substitutes demo values for unavailable live data, and includes the Postgres schema needed for managed infrastructure.

## Run locally

```sh
npm run check
npm run dev
```

Open `http://localhost:3000`. In the default setup mode, the JSON endpoints expose House of Dental's real configuration and explicit unavailable-data states without making GA4 requests.

## Live GA4 reporting

The deployed House of Dental site uses property `549721844`, web stream `15408312790`, and Measurement ID `G-TC66MQQ0T7`. These values match the canonical website repository. Live reporting mode is explicitly gated. Provision `PLATFORM_API_TOKEN` as a secret with at least 32 characters before using this configuration:

```sh
PLATFORM_MODE=live \
HOST=0.0.0.0 \
GA4_DATA_API_ENABLED=true \
GA4_LIVE_APPROVED=true \
GA4_PROPERTY_ID=549721844 \
GA4_STREAM_ID=15408312790 \
npm run dev
```

The connector uses read-only ADC credentials. The overview endpoint makes live GA4 Data API requests for summary metrics and event counts; previous-period comparisons and database persistence are not yet included.

## Live-connection gate

Set the credential-related environment values only after the site's privacy, route, consent, property, stream, and assignment evidence is recorded. The connector rejects live work unless explicitly enabled, an approved assignment is present, and the configured property and stream exactly match the first-site record.

The public website's GA4 collection and this platform's read-only reporting connection are separate states. Public browser verification confirms a consent-controlled request to the configured Measurement ID; it does not establish Viewer access, property receipt, historical sync, or database persistence.

The database migration is at `infra/postgres/001_core.sql`; it is intentionally not applied by this repository.
