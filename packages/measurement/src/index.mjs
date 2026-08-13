export { eligibilityFor, routeDecision, validateParameters, normalizeUrl, validateEvent, validatePageView } from '../../../measurement/privacy/validator.mjs';

export function measurementHealth({ contractStatus, routeManifestStatus, streamStatus, lastValidationAt, requiredEvents = [], observedEvents = [] }) {
  const missingEvents = requiredEvents.filter((event) => !observedEvents.includes(event));
  const checks = [
    { key: 'contract', state: contractStatus === 'approved' ? 'ok' : 'blocked', detail: contractStatus },
    { key: 'route_manifest', state: routeManifestStatus === 'approved' ? 'ok' : 'blocked', detail: routeManifestStatus },
    { key: 'ga4_stream', state: streamStatus === 'verified' ? 'ok' : 'blocked', detail: streamStatus },
    { key: 'required_events', state: missingEvents.length ? 'warning' : 'ok', detail: missingEvents },
    { key: 'last_validation', state: lastValidationAt ? 'ok' : 'warning', detail: lastValidationAt ?? 'not_validated' }
  ];
  return { state: checks.some((check) => check.state === 'blocked') ? 'blocked' : checks.some((check) => check.state === 'warning') ? 'warning' : 'healthy', checks };
}
