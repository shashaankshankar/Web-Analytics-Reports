CREATE TABLE IF NOT EXISTS app.data_retention_policies (
  organization_id uuid PRIMARY KEY REFERENCES app.organizations(id),
  aggregate_days integer NOT NULL DEFAULT 760 CHECK (aggregate_days BETWEEN 30 AND 3650),
  operations_days integer NOT NULL DEFAULT 180 CHECK (operations_days BETWEEN 30 AND 3650),
  audit_days integer NOT NULL DEFAULT 2555 CHECK (audit_days BETWEEN 365 AND 3650),
  deletion_grace_days integer NOT NULL DEFAULT 30 CHECK (deletion_grace_days BETWEEN 7 AND 90),
  updated_by uuid REFERENCES app.users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.deletion_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES app.organizations(id),
  website_id uuid REFERENCES app.websites(id) ON DELETE SET NULL,
  requested_by uuid NOT NULL REFERENCES app.users(id),
  status text NOT NULL CHECK (status IN ('scheduled','cancelled','executing','completed','failed')),
  execute_after timestamptz NOT NULL,
  confirmation_public_id text NOT NULL,
  preview_json jsonb NOT NULL DEFAULT '{}',
  result_json jsonb NOT NULL DEFAULT '{}',
  requested_at timestamptz NOT NULL DEFAULT now(),
  cancelled_at timestamptz,
  completed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS deletion_request_active_idx ON app.deletion_requests(website_id) WHERE status='scheduled';
CREATE INDEX IF NOT EXISTS deletion_requests_due_idx ON app.deletion_requests(status,execute_after);

INSERT INTO app.data_retention_policies(organization_id)
SELECT id FROM app.organizations ON CONFLICT(organization_id) DO NOTHING;

GRANT SELECT,INSERT,UPDATE ON app.data_retention_policies,app.deletion_requests TO measurement_tenant;

ALTER TABLE app.data_retention_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.data_retention_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY retention_policy_tenant_isolation ON app.data_retention_policies
USING (app.organization_allowed(organization_id)) WITH CHECK (app.organization_allowed(organization_id));

ALTER TABLE app.deletion_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.deletion_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY deletion_request_tenant_isolation ON app.deletion_requests
USING (app.organization_allowed(organization_id)) WITH CHECK (app.organization_allowed(organization_id));

INSERT INTO app.schema_migrations(version) VALUES('005_retention_offboarding') ON CONFLICT(version) DO NOTHING;
