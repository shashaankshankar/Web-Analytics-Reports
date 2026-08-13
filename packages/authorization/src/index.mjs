const ROLES = new Set(['agency_owner', 'agency_admin', 'agency_analyst', 'client_admin', 'client_viewer']);

export function createTenantContext({ organizationId, companyIds = [], websiteIds = [], role }) {
  if (!organizationId || !ROLES.has(role)) throw new Error('invalid_tenant_context');
  return Object.freeze({ organizationId, companyIds: new Set(companyIds), websiteIds: new Set(websiteIds), role });
}

export function assertWebsiteAccess(context, websiteId) {
  if (!context.websiteIds.has(websiteId)) throw new Error('forbidden_website');
  return true;
}

export function assertAgencyRole(context) {
  if (!context.role.startsWith('agency_')) throw new Error('forbidden_agency_role');
  return true;
}
