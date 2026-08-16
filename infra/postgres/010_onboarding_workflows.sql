-- Tenant-safe, resumable onboarding state. This migration stores references and
-- explicit gates only; it never stores provider credentials or invents approval.
-- Keep startup migration failure bounded while avoiding partial policy changes.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10min';

CREATE TABLE IF NOT EXISTS app.onboarding_workflows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$'),
  request_hash text NOT NULL,
  status text NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','blocked','ready','completed','cancelled')),
  company_id uuid REFERENCES app.companies(id),
  website_id uuid REFERENCES app.websites(id),
  contract_version_id uuid REFERENCES app.measurement_contract_versions(id),
  governance_status text NOT NULL DEFAULT 'not_recorded' CHECK (governance_status IN ('not_recorded','pending_review','requires_review','approved','prohibited')),
  consent_status text NOT NULL DEFAULT 'not_recorded' CHECK (consent_status IN ('not_recorded','pending_client_consent','approved','rejected')),
  governance_reference text,
  consent_reference text,
  created_by uuid NOT NULL REFERENCES app.users(id),
  updated_by uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, idempotency_key),
  CONSTRAINT onboarding_workflows_org_id_key UNIQUE (organization_id, id),
  CHECK (governance_status <> 'approved' OR governance_reference IS NOT NULL),
  CHECK (consent_status <> 'approved' OR consent_reference IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS app.onboarding_workflow_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL,
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  step_key text NOT NULL CHECK (step_key IN ('organization','company','website','measurement_contract','governance','consent','ga4_connection','source_connections','first_sync','client_access','handoff')),
  status text NOT NULL CHECK (status IN ('pending','in_progress','completed','blocked','deferred')),
  detail_json jsonb NOT NULL DEFAULT '{}',
  updated_by uuid NOT NULL REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, step_key),
  CONSTRAINT onboarding_steps_workflow_org_fk FOREIGN KEY (organization_id, workflow_id)
    REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app.onboarding_connection_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL,
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  connection_kind text NOT NULL CHECK (connection_kind IN ('ga4','google_ads','search_console','call_tracking','crm_booking')),
  mode text NOT NULL CHECK (mode IN ('registered','deferred')),
  status text NOT NULL CHECK (status IN ('deferred','pending_approval','approved','blocked')),
  idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$'),
  request_hash text NOT NULL,
  analytics_connection_id uuid REFERENCES app.analytics_connections(id),
  source_connection_id uuid REFERENCES app.source_connections(id),
  external_property_id text CHECK (external_property_id IS NULL OR external_property_id ~ '^[0-9]+$'),
  external_stream_id text CHECK (external_stream_id IS NULL OR external_stream_id ~ '^[0-9]+$'),
  external_account_id text,
  configuration_json jsonb NOT NULL DEFAULT '{}',
  defer_reason text,
  approval_reference text,
  created_by uuid NOT NULL REFERENCES app.users(id),
  updated_by uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, connection_kind),
  CHECK ((mode = 'deferred' AND defer_reason IS NOT NULL) OR mode = 'registered'),
  CHECK ((mode = 'registered' AND (analytics_connection_id IS NOT NULL OR source_connection_id IS NOT NULL)) OR mode = 'deferred'),
  CHECK (connection_kind = 'ga4' OR (external_property_id IS NULL AND external_stream_id IS NULL)),
  CHECK (external_property_id IS NULL OR external_stream_id IS NOT NULL),
  CHECK (external_stream_id IS NULL OR external_property_id IS NOT NULL),
  CONSTRAINT onboarding_connections_workflow_org_fk FOREIGN KEY (organization_id, workflow_id)
    REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app.onboarding_sync_readiness (
  workflow_id uuid PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  website_id uuid REFERENCES app.websites(id),
  assignment_id uuid REFERENCES app.website_analytics_assignments(id),
  status text NOT NULL DEFAULT 'not_requested' CHECK (status IN ('not_requested','blocked','requested','running','ready','failed')),
  request_idempotency_key text CHECK (request_idempotency_key IS NULL OR request_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$'),
  detail_json jsonb NOT NULL DEFAULT '{}',
  requested_by uuid REFERENCES app.users(id),
  requested_at timestamptz,
  checked_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT onboarding_sync_workflow_org_fk FOREIGN KEY (organization_id, workflow_id)
    REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app.client_membership_scopes (
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  workflow_id uuid NOT NULL,
  user_id uuid NOT NULL REFERENCES app.users(id),
  website_id uuid NOT NULL REFERENCES app.websites(id),
  role text NOT NULL CHECK (role IN ('client_admin','client_viewer')),
  authorization_reference text NOT NULL,
  created_by uuid NOT NULL REFERENCES app.users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workflow_id, user_id, website_id),
  UNIQUE (organization_id, user_id, website_id),
  CONSTRAINT client_scope_workflow_org_fk FOREIGN KEY (organization_id, workflow_id)
    REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE,
  CONSTRAINT client_scope_membership_fk FOREIGN KEY (organization_id, user_id)
    REFERENCES app.memberships(organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS onboarding_workflows_org_updated_idx
  ON app.onboarding_workflows(organization_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS onboarding_steps_workflow_idx
  ON app.onboarding_workflow_steps(workflow_id, step_key);
CREATE INDEX IF NOT EXISTS onboarding_connections_workflow_idx
  ON app.onboarding_connection_requests(workflow_id, connection_kind);
CREATE INDEX IF NOT EXISTS onboarding_sync_org_status_idx
  ON app.onboarding_sync_readiness(organization_id, status, checked_at DESC);
CREATE INDEX IF NOT EXISTS client_membership_scopes_user_idx
  ON app.client_membership_scopes(organization_id, user_id, website_id);

-- The table definitions above cover a new install. These guarded additions
-- also make a replay safe if an earlier 010 execution created the tables before
-- the organization-keyed constraints were present. NOT VALID avoids scanning
-- historical rows during startup; new writes are still checked immediately.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.onboarding_workflows'::regclass
       AND conname = 'onboarding_workflows_org_id_key'
  ) THEN
    ALTER TABLE app.onboarding_workflows
      ADD CONSTRAINT onboarding_workflows_org_id_key UNIQUE (organization_id, id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.onboarding_workflow_steps'::regclass
       AND conname = 'onboarding_steps_workflow_org_fk'
  ) THEN
    ALTER TABLE app.onboarding_workflow_steps
      ADD CONSTRAINT onboarding_steps_workflow_org_fk
      FOREIGN KEY (organization_id, workflow_id)
      REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.onboarding_connection_requests'::regclass
       AND conname = 'onboarding_connections_workflow_org_fk'
  ) THEN
    ALTER TABLE app.onboarding_connection_requests
      ADD CONSTRAINT onboarding_connections_workflow_org_fk
      FOREIGN KEY (organization_id, workflow_id)
      REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.onboarding_sync_readiness'::regclass
       AND conname = 'onboarding_sync_workflow_org_fk'
  ) THEN
    ALTER TABLE app.onboarding_sync_readiness
      ADD CONSTRAINT onboarding_sync_workflow_org_fk
      FOREIGN KEY (organization_id, workflow_id)
      REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.client_membership_scopes'::regclass
       AND conname = 'client_scope_workflow_org_fk'
  ) THEN
    ALTER TABLE app.client_membership_scopes
      ADD CONSTRAINT client_scope_workflow_org_fk
      FOREIGN KEY (organization_id, workflow_id)
      REFERENCES app.onboarding_workflows(organization_id, id) ON DELETE CASCADE NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'app.client_membership_scopes'::regclass
       AND conname = 'client_scope_membership_fk'
  ) THEN
    ALTER TABLE app.client_membership_scopes
      ADD CONSTRAINT client_scope_membership_fk
      FOREIGN KEY (organization_id, user_id)
      REFERENCES app.memberships(organization_id, user_id) NOT VALID;
  END IF;
END
$$;

CREATE OR REPLACE FUNCTION app.current_actor_role() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT current_setting('app.role', true)
$$;

CREATE OR REPLACE FUNCTION app.company_allowed(candidate uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = app, pg_temp AS $$
  SELECT app.database_role_is_privileged()
      OR EXISTS (
        SELECT 1
          FROM app.companies c
         WHERE c.id = candidate
           AND c.organization_id = app.current_organization_id()
           AND (
             app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst')
             OR EXISTS (
               SELECT 1
                 FROM app.client_membership_scopes s
                 JOIN app.websites w ON w.id = s.website_id
                WHERE s.organization_id = app.current_organization_id()
                  AND s.user_id = nullif(current_setting('app.user_id', true), '')::uuid
                  AND w.company_id = c.id
             )
           )
      )
$$;

CREATE OR REPLACE FUNCTION app.website_allowed(candidate uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = app, pg_temp AS $$
  SELECT app.database_role_is_privileged()
      OR EXISTS (
        SELECT 1
          FROM app.websites w
          JOIN app.companies c ON c.id = w.company_id
         WHERE w.id = candidate
           AND c.organization_id = app.current_organization_id()
           AND (
             app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst')
             OR EXISTS (
               SELECT 1
                 FROM app.client_membership_scopes s
                WHERE s.organization_id = app.current_organization_id()
                  AND s.user_id = nullif(current_setting('app.user_id', true), '')::uuid
                  AND s.website_id = w.id
             )
           )
      )
$$;

ALTER TABLE app.onboarding_workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.onboarding_workflows FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_workflow_tenant_isolation ON app.onboarding_workflows;
CREATE POLICY onboarding_workflow_tenant_isolation ON app.onboarding_workflows
USING (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (company_id IS NULL OR app.company_allowed(company_id)) AND (website_id IS NULL OR app.website_allowed(website_id))))
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (company_id IS NULL OR app.company_allowed(company_id)) AND (website_id IS NULL OR app.website_allowed(website_id))));

ALTER TABLE app.onboarding_workflow_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.onboarding_workflow_steps FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_step_tenant_isolation ON app.onboarding_workflow_steps;
CREATE POLICY onboarding_step_tenant_isolation ON app.onboarding_workflow_steps
USING (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin')))
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin')));

ALTER TABLE app.onboarding_connection_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.onboarding_connection_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_connection_tenant_isolation ON app.onboarding_connection_requests;
CREATE POLICY onboarding_connection_tenant_isolation ON app.onboarding_connection_requests
USING (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (analytics_connection_id IS NULL OR EXISTS (SELECT 1 FROM app.analytics_connections c WHERE c.id = app.onboarding_connection_requests.analytics_connection_id AND c.organization_id = app.onboarding_connection_requests.organization_id)) AND (source_connection_id IS NULL OR EXISTS (SELECT 1 FROM app.source_connections s WHERE s.id = app.onboarding_connection_requests.source_connection_id AND s.organization_id = app.onboarding_connection_requests.organization_id))))
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (analytics_connection_id IS NULL OR EXISTS (SELECT 1 FROM app.analytics_connections c WHERE c.id = app.onboarding_connection_requests.analytics_connection_id AND c.organization_id = app.onboarding_connection_requests.organization_id)) AND (source_connection_id IS NULL OR EXISTS (SELECT 1 FROM app.source_connections s WHERE s.id = app.onboarding_connection_requests.source_connection_id AND s.organization_id = app.onboarding_connection_requests.organization_id))));

ALTER TABLE app.onboarding_sync_readiness ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.onboarding_sync_readiness FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_sync_tenant_isolation ON app.onboarding_sync_readiness;
CREATE POLICY onboarding_sync_tenant_isolation ON app.onboarding_sync_readiness
USING (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (website_id IS NULL OR app.website_allowed(website_id)) AND (assignment_id IS NULL OR EXISTS (SELECT 1 FROM app.website_analytics_assignments a WHERE a.id = app.onboarding_sync_readiness.assignment_id AND a.website_id = app.onboarding_sync_readiness.website_id))))
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin') AND (website_id IS NULL OR app.website_allowed(website_id)) AND (assignment_id IS NULL OR EXISTS (SELECT 1 FROM app.website_analytics_assignments a WHERE a.id = app.onboarding_sync_readiness.assignment_id AND a.website_id = app.onboarding_sync_readiness.website_id))));

ALTER TABLE app.client_membership_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.client_membership_scopes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS client_membership_scope_tenant_isolation ON app.client_membership_scopes;
CREATE POLICY client_membership_scope_tenant_isolation ON app.client_membership_scopes
USING (
  app.database_role_is_privileged()
  OR (
    app.organization_allowed(organization_id)
    AND app.website_allowed(website_id)
    AND (app.current_actor_role() IN ('agency_owner','agency_admin') OR user_id = nullif(current_setting('app.user_id', true), '')::uuid)
  )
)
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.website_allowed(website_id) AND app.current_actor_role() IN ('agency_owner','agency_admin')));

DROP POLICY IF EXISTS company_tenant_isolation ON app.companies;
CREATE POLICY company_tenant_isolation ON app.companies
USING (app.company_allowed(id))
WITH CHECK (app.company_allowed(id));

DROP POLICY IF EXISTS website_tenant_isolation ON app.websites;
CREATE POLICY website_tenant_isolation ON app.websites
USING (app.website_allowed(id))
WITH CHECK (app.website_allowed(id));

DROP POLICY IF EXISTS resource_identifier_tenant_isolation ON app.resource_identifiers;
CREATE POLICY resource_identifier_tenant_isolation ON app.resource_identifiers
USING (
  app.database_role_is_privileged()
  OR (resource_type = 'company' AND app.company_allowed(resource_id))
  OR (resource_type = 'website' AND app.website_allowed(resource_id))
)
WITH CHECK (app.organization_allowed(organization_id));

DROP POLICY IF EXISTS membership_tenant_isolation ON app.memberships;
CREATE POLICY membership_tenant_isolation ON app.memberships
USING (
  app.database_role_is_privileged()
  OR (
    app.organization_allowed(organization_id)
    AND (app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst') OR user_id = nullif(current_setting('app.user_id', true), '')::uuid)
  )
)
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin')));

DROP POLICY IF EXISTS user_tenant_isolation ON app.users;
CREATE POLICY user_tenant_isolation ON app.users
USING (
  app.database_role_is_privileged()
  OR (
    app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst')
    AND EXISTS (
      SELECT 1
        FROM app.memberships m
       WHERE m.user_id = app.users.id
         AND app.organization_allowed(m.organization_id)
    )
  )
  OR id = nullif(current_setting('app.user_id', true), '')::uuid
) WITH CHECK (
  app.database_role_is_privileged()
  OR app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst')
  OR id = nullif(current_setting('app.user_id', true), '')::uuid
);

DROP POLICY IF EXISTS audit_tenant_isolation ON audit.events;
CREATE POLICY audit_tenant_isolation ON audit.events
USING (
  app.database_role_is_privileged()
  OR (app.organization_allowed(organization_id) AND (app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst') OR actor_user_id = nullif(current_setting('app.user_id', true), '')::uuid))
)
WITH CHECK (app.database_role_is_privileged() OR (app.organization_allowed(organization_id) AND app.current_actor_role() IN ('agency_owner','agency_admin')));

GRANT SELECT,INSERT,UPDATE ON app.companies,app.websites,app.resource_identifiers,app.users,app.memberships TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE ON app.website_measurement_contract_assignments TO measurement_tenant;
GRANT SELECT,INSERT,UPDATE ON app.onboarding_workflows,app.onboarding_workflow_steps,
  app.onboarding_connection_requests,app.onboarding_sync_readiness,app.client_membership_scopes TO measurement_tenant;

INSERT INTO app.schema_migrations(version)
VALUES('010_onboarding_workflows')
ON CONFLICT(version) DO NOTHING;
