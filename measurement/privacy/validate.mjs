import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateEvent, validatePageView, normalizeUrl } from './validator.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(fs.readFileSync(path.join(here, 'fixtures.json'), 'utf8'));
const read = (relative) => JSON.parse(fs.readFileSync(path.join(here, relative), 'utf8'));
const routes = read('../eligibility/routes.json');
const prohibited = read('./prohibited-data.json');
const contract = read('../contracts/local_service_v1/contract.json');
const events = read('../contracts/local_service_v1/events.json');

assert.equal(routes.default_behavior, 'prohibited', 'unknown routes must default to prohibited');
assert.equal(routes.unknown_routes, 'prohibited', 'unknown route policy must be explicit');
assert.equal(routes.unclassified_routes, 'prohibited', 'unclassified route policy must be explicit');
assert.equal(routes.status, 'pending_privacy_review', 'reporting platform collection must not be enabled without recorded approval');
assert.equal(prohibited.behavior_on_match, 'reject_event_and_do_not_load_analytics');
assert.ok(prohibited.prohibited_sources.includes('query_parameters'));
assert.ok(prohibited.prohibited_sources.includes('page_title'));
assert.ok(prohibited.prohibited_sources.includes('custom_dimensions'));
assert.ok(prohibited.url_rules.reject_identifier_like_path_segments);
assert.deepEqual(
  events.events.map(({ name }) => name).sort(),
  ['appointment_request', 'cta_click', 'email_click', 'form_start', 'form_submit', 'generate_lead', 'phone_click'].sort(),
  'contract must contain exactly the seven Stage 3 taxonomy events'
);
assert.equal(contract.privacy.healthcare_default_deny, true);
assert.equal(contract.privacy.consent_required, true);

// The manifest is intentionally pending approval: tests prove fail-closed behavior
// without silently turning live analytics on.
for (const fixture of fixtures.pass) {
  if (fixture.event) {
    const result = validateEvent({ route: fixture.route, event: fixture.event, parameters: fixture.parameters, consentGranted: false });
    assert.equal(result.allowed, false, `${fixture.name}: consent must still block a pending manifest`);
  } else {
    const result = validatePageView({ route: fixture.route, consentGranted: false });
    assert.equal(result.allowed, false, `${fixture.name}: consent must still block a pending manifest`);
  }
}

for (const fixture of fixtures.fail) {
  const result = fixture.event
    ? validateEvent({ route: fixture.route, event: fixture.event, parameters: fixture.parameters, consentGranted: true })
    : validatePageView({ route: fixture.route, consentGranted: true });
  assert.equal(result.allowed, false, `${fixture.name}: expected rejection`);
}

assert.equal(validatePageView({ route: '/services', consentGranted: true }).allowed, false, 'pending manifest must fail closed');
assert.equal(validatePageView({ route: '/not-in-manifest', consentGranted: true }).allowed, false, 'unknown routes must fail closed');
assert.equal(validateEvent({ route: '/services', event: 'phone_click', parameters: { location: 'hero', link_type: 'tel' }, consentGranted: true }).allowed, false, 'requires_review routes must fail closed');
assert.equal(normalizeUrl('/services?utm_source=google&utm_campaign=summer').url, '/services?utm_source=google&utm_campaign=summer');
assert.equal(normalizeUrl('/services?email=john@example.com').valid, false);
assert.equal(validateEvent({ route: '/services', event: 'generate_lead', parameters: { email: 'john@example.com' }, consentGranted: true }).allowed, false);

console.log(`Privacy validation passed: ${fixtures.pass.length} pass fixtures and ${fixtures.fail.length} fail fixtures; manifest remains fail-closed.`);
