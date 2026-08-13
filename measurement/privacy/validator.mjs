import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const loadJson = (name) => JSON.parse(fs.readFileSync(path.join(here, name), 'utf8'));

const routes = loadJson('../eligibility/routes.json');
const privacy = loadJson('./prohibited-data.json');

const eventNames = new Set([
  'form_start', 'form_submit', 'generate_lead', 'phone_click',
  'email_click', 'appointment_request', 'cta_click'
]);

const caseInsensitive = (pattern) => pattern.replace(/^\(\?i\)/, '');
const prohibitedPatterns = privacy.prohibited_value_patterns.map(
  (pattern) => new RegExp(caseInsensitive(pattern), 'i')
);
const prohibitedNames = new Set(privacy.prohibited_parameter_names.map((name) => name.toLowerCase()));
const allowedQueryParameters = new Set(privacy.url_rules.allowed_campaign_parameters);

function routePath(input) {
  const value = new URL(input, 'https://measurement.invalid');
  return value.pathname || '/';
}

function matchesPattern(route, pattern) {
  if (!pattern.endsWith('*')) return route === pattern;
  return route.startsWith(pattern.slice(0, -1));
}

export function eligibilityFor(input) {
  const route = routePath(input);
  if (Object.hasOwn(routes.routes, route)) return routes.routes[route];
  const match = routes.patterns.find(({ pattern }) => matchesPattern(route, pattern));
  return match?.status ?? routes.default_behavior;
}

export function routeDecision(input, { consentGranted = false } = {}) {
  const status = eligibilityFor(input);
  const allowed = routes.status === 'approved' && status === 'approved' && consentGranted;
  return {
    allowed,
    status,
    reason: !consentGranted ? 'consent_required' : status !== 'approved' ? `route_${status}` : routes.status !== 'approved' ? 'manifest_not_approved' : 'allowed'
  };
}

function hasProhibitedValue(value) {
  if (value === undefined || value === null) return false;
  return prohibitedPatterns.some((pattern) => pattern.test(String(value)));
}

export function validateParameters(parameters = {}) {
  const violations = [];
  for (const [name, value] of Object.entries(parameters)) {
    if (prohibitedNames.has(name.toLowerCase())) violations.push(`prohibited_parameter:${name}`);
    else if (hasProhibitedValue(value)) violations.push(`prohibited_value:${name}`);
  }
  return { valid: violations.length === 0, violations };
}

export function normalizeUrl(input) {
  const url = new URL(input, 'https://measurement.invalid');
  if (url.hash) return { valid: false, url: null, reason: 'url_fragment' };
  const identifierLikeSegment = url.pathname.split('/').some((segment) =>
    /^\d{4,}$/.test(segment) || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(segment)
  );
  if (identifierLikeSegment || hasProhibitedValue(url.pathname)) {
    return { valid: false, url: null, reason: 'identifier_like_path' };
  }
  const kept = new URLSearchParams();
  for (const [name, value] of url.searchParams) {
    if (!allowedQueryParameters.has(name)) return { valid: false, url: null, reason: `query_parameter:${name}` };
    if (hasProhibitedValue(value)) return { valid: false, url: null, reason: `query_value:${name}` };
    kept.append(name, value);
  }
  url.search = kept.toString();
  return { valid: true, url: `${url.pathname}${url.search}` };
}

export function validateEvent({ route, event, parameters = {}, consentGranted = false }) {
  const routeResult = routeDecision(route, { consentGranted });
  const urlResult = normalizeUrl(route);
  const parameterResult = validateParameters(parameters);
  const violations = [];
  if (!routeResult.allowed) violations.push(routeResult.reason);
  if (!eventNames.has(event)) violations.push(`event_not_allowed:${event}`);
  if (!urlResult.valid) violations.push(urlResult.reason);
  if (!parameterResult.valid) violations.push(...parameterResult.violations);
  return {
    allowed: violations.length === 0,
    violations,
    route: routeResult,
    url: urlResult,
    parameters: parameterResult
  };
}

export function validatePageView({ route, consentGranted = false }) {
  const routeResult = routeDecision(route, { consentGranted });
  const urlResult = normalizeUrl(route);
  const allowed = routeResult.allowed && urlResult.valid;
  return { allowed, violations: allowed ? [] : [routeResult.reason, ...(urlResult.valid ? [] : [urlResult.reason])], route: routeResult, url: urlResult };
}
