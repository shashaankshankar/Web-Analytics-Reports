export class DisabledCredential {
  constructor(reason = 'credential_not_configured') { this.reason = reason; this.disabled = true; }
  async getAuthorizedClient() { throw new Error(this.reason); }
  async validateAccess() { return { valid: false, reason: this.reason }; }
  async listAccessibleProperties() { return []; }
  async disable() { this.disabled = true; }
}

import crypto from 'node:crypto';
import { GoogleAuth } from 'google-auth-library';

const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const ANALYTICS_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly';
const encode = (value) => Buffer.from(value).toString('base64url');

function readServiceAccount(value) {
  if (!value) return null;
  if (typeof value === 'object') return value;
  try { return JSON.parse(value); } catch { throw new Error('credential_json_invalid'); }
}

export class ServiceAccountCredential {
  constructor({ serviceAccountJson, enabled = false, fetchImpl = globalThis.fetch, now = () => Date.now() }) {
    this.serviceAccountJson = readServiceAccount(serviceAccountJson);
    this.enabled = enabled;
    this.disabled = false;
    this.fetchImpl = fetchImpl;
    this.now = now;
    this.token = null;
  }
  assertEnabled() {
    if (this.disabled) throw new Error('credential_disabled');
    if (!this.enabled || !this.serviceAccountJson) throw new Error('credential_not_configured');
  }
  async getAuthorizedClient() {
    this.assertEnabled();
    if (!this.fetchImpl) throw new Error('fetch_unavailable');
    if (this.token && this.token.expiresAt > this.now() + 60_000) return this.token.client;
    const account = this.serviceAccountJson;
    if (!account.client_email || !account.private_key) throw new Error('credential_json_incomplete');
    const nowSeconds = Math.floor(this.now() / 1000);
    const header = encode(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const claim = encode(JSON.stringify({ iss: account.client_email, scope: ANALYTICS_SCOPE, aud: account.token_uri || TOKEN_URL, iat: nowSeconds, exp: nowSeconds + 3600 }));
    const signer = crypto.createSign('RSA-SHA256');
    signer.update(`${header}.${claim}`);
    const assertion = `${header}.${claim}.${encode(signer.sign(account.private_key))}`;
    const response = await this.fetchImpl(account.token_uri || TOKEN_URL, {
      method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer', assertion })
    });
    const body = await response.json();
    if (!response.ok || !body.access_token) throw new Error(body.error || 'credential_token_request_failed');
    const client = { authType: 'service_account', scopes: [ANALYTICS_SCOPE], accessToken: body.access_token };
    this.token = { client, expiresAt: this.now() + Number(body.expires_in || 3600) * 1000 };
    return client;
  }
  async validateAccess() { try { await this.getAuthorizedClient(); return { valid: true, scope: 'analytics.readonly' }; } catch (error) { return { valid: false, reason: error.message }; } }
  async listAccessibleProperties() {
    const client = await this.getAuthorizedClient();
    const properties = [];
    let pageToken = '';
    do {
      const query = pageToken ? `?pageToken=${encodeURIComponent(pageToken)}` : '';
      const response = await this.fetchImpl(`https://analyticsadmin.googleapis.com/v1beta/accountSummaries${query}`, { headers: { authorization: `Bearer ${client.accessToken}` } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.status || 'admin_api_request_failed');
      properties.push(...(body.accountSummaries || []).flatMap((account) => (account.propertySummaries || []).map((property) => ({ account: account.name, property: property.property, displayName: property.displayName }))));
      pageToken = body.nextPageToken || '';
    } while (pageToken);
    return properties;
  }
  async disable() { this.disabled = true; }
}

export class AdcCredential {
  constructor({ enabled = false, authFactory = () => new GoogleAuth({ scopes: [ANALYTICS_SCOPE] }), fetchImpl = globalThis.fetch } = {}) {
    this.enabled = enabled;
    this.disabled = false;
    this.authFactory = authFactory;
    this.fetchImpl = fetchImpl;
    this.auth = null;
    this.authClient = null;
  }
  assertEnabled() {
    if (this.disabled) throw new Error('credential_disabled');
    if (!this.enabled) throw new Error('credential_not_configured');
  }
  async getAuthorizedClient() {
    this.assertEnabled();
    this.auth ||= this.authFactory();
    this.authClient ||= await this.auth.getClient();
    const { token } = await this.authClient.getAccessToken();
    if (!token) throw new Error('adc_access_token_unavailable');
    return { authType: 'adc', scopes: [ANALYTICS_SCOPE], accessToken: token };
  }
  async validateAccess() {
    try { await this.getAuthorizedClient(); return { valid: true, scope: 'analytics.readonly', authType: 'adc' }; }
    catch (error) { return { valid: false, reason: error.message }; }
  }
  async listAccessibleProperties() {
    const client = await this.getAuthorizedClient();
    const properties = [];
    let pageToken = '';
    do {
      const query = pageToken ? `?pageToken=${encodeURIComponent(pageToken)}` : '';
      if (!this.fetchImpl) throw new Error('fetch_unavailable');
      const response = await this.fetchImpl(`https://analyticsadmin.googleapis.com/v1beta/accountSummaries${query}`, { headers: { authorization: `Bearer ${client.accessToken}` } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.status || 'admin_api_request_failed');
      properties.push(...(body.accountSummaries || []).flatMap((account) => (account.propertySummaries || []).map((property) => ({ account: account.name, property: property.property, displayName: property.displayName }))));
      pageToken = body.nextPageToken || '';
    } while (pageToken);
    return properties;
  }
  async disable() { this.disabled = true; }
}

export function credentialFromEnvironment(env = process.env) {
  const enabled = env.GA4_DATA_API_ENABLED === 'true' || env.GA4_ADMIN_API_ENABLED === 'true';
  if (env.GOOGLE_APPLICATION_CREDENTIALS_JSON) {
    return new ServiceAccountCredential({ serviceAccountJson: env.GOOGLE_APPLICATION_CREDENTIALS_JSON, enabled });
  }
  return new AdcCredential({ enabled });
}
