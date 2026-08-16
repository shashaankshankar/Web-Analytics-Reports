from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import TenantContext
from app.config import Settings, load_site
from app.main import create_app


TOKEN = "x" * 32


class OnboardingDatabase:
    configured = True

    def __init__(self, role="agency_owner"):
        self.role = role
        self.workflows = {}
        self.connections = {}
        self.members = []
        self.governance = {}
        self.first_sync = {}

    def close(self):
        pass

    def authorize_context(self, email, organization_id=None):
        if organization_id and organization_id != "org-1":
            raise PermissionError("membership_not_found")
        return TenantContext("org-1", "user-1", email, self.role)

    def create_onboarding_workflow(self, context, idempotency_key, company_id, company_name, website_id, canonical_domain, contract_slug):
        existing = next((item for item in self.workflows.values() if item["idempotencyKey"] == idempotency_key), None)
        if existing:
            if existing["request"] != (company_id, company_name, website_id, canonical_domain, contract_slug):
                raise ValueError("onboarding_idempotency_conflict")
            return {**existing["payload"], "idempotentReplay": True}
        workflow_id = f"workflow-{len(self.workflows) + 1}"
        payload = self.payload(workflow_id, company_id, company_name, website_id, canonical_domain, contract_slug, idempotency_key)
        self.workflows[workflow_id] = {"idempotencyKey": idempotency_key, "request": (company_id, company_name, website_id, canonical_domain, contract_slug), "payload": payload}
        return {**payload, "idempotentReplay": False}

    def payload(self, workflow_id, company_id, company_name, website_id, domain, contract_slug="local_service_v1", idempotency_key=None):
        governance = self.governance.get(workflow_id, {"governanceStatus": "pending_review", "consentStatus": "pending_client_consent"})
        connections = self.connections.get(workflow_id, [])
        first_sync = self.first_sync.get(workflow_id, {"status": "not_requested"})
        members = [item for item in self.members if item["workflowId"] == workflow_id]
        checklist = [
            {"key": "organization", "state": "completed", "complete": True, "detail": {}},
            {"key": "company", "state": "completed", "complete": True, "detail": {}},
            {"key": "website", "state": "completed", "complete": True, "detail": {}},
            {"key": "measurement_contract", "state": "completed", "complete": True, "detail": {}},
            {"key": "governance", "state": "completed" if governance["governanceStatus"] == "approved" else "in_progress", "complete": governance["governanceStatus"] == "approved", "detail": governance},
            {"key": "consent", "state": "completed" if governance["consentStatus"] == "approved" else "in_progress", "complete": governance["consentStatus"] == "approved", "detail": governance},
            {"key": "ga4_connection", "state": "completed" if any(item["kind"] == "ga4" and item["status"] == "approved" for item in connections) else "deferred" if any(item["kind"] == "ga4" and item["mode"] == "deferred" for item in connections) else "in_progress", "complete": any(item["kind"] == "ga4" and item["status"] == "approved" for item in connections), "detail": {}},
            {"key": "first_sync", "state": "completed" if first_sync["status"] == "ready" else "blocked" if first_sync["status"] == "blocked" else "in_progress", "complete": first_sync["status"] == "ready", "detail": first_sync},
            {"key": "client_access", "state": "completed" if members else "in_progress", "complete": bool(members), "detail": {"authorizedMembers": len(members)}},
        ]
        ready = all(item["complete"] for item in checklist)
        return {
            "workflowId": workflow_id,
            "organization": {"id": "org-1", "name": "Agency"},
            "status": "ready" if ready else "in_progress",
            "idempotencyKey": idempotency_key or next(item["idempotencyKey"] for item in self.workflows.values() if item["payload"]["workflowId"] == workflow_id),
            "company": {"id": company_id, "name": company_name},
            "website": {"id": website_id, "canonicalDomain": domain},
            "contract": {"slug": contract_slug, "version": 1, "approvalStatus": "approved"},
            "governance": governance,
            "consent": governance,
            "connections": connections,
            "firstSync": first_sync,
            "clientMembers": members,
            "checklist": checklist,
            "handoff": {"ready": ready, "status": "ready" if ready else "pending", "blockingGates": [item["key"] for item in checklist if not item["complete"]], "reportDispatch": {"enabled": False, "state": "not_requested", "realClientReportSent": False}},
        }

    def onboarding_workflow(self, context, workflow_id):
        if workflow_id not in self.workflows:
            raise ValueError("onboarding_workflow_not_found")
        return self.workflows[workflow_id]["payload"]

    def record_onboarding_governance(self, context, workflow_id, governance_status, consent_status, governance_reference, consent_reference):
        if governance_status != "pending_review" and not governance_reference:
            raise ValueError("governance_reference_required")
        if consent_status != "pending_client_consent" and not consent_reference:
            raise ValueError("consent_reference_required")
        value = {"governanceStatus": governance_status, "consentStatus": consent_status, "governanceReference": governance_reference, "consentReference": consent_reference}
        self.governance[workflow_id] = value
        base = self.workflows[workflow_id]["payload"]
        return self.payload(workflow_id, base["company"]["id"], base["company"]["name"], base["website"]["id"], base["website"]["canonicalDomain"], "local_service_v1")

    def register_onboarding_connection(self, context, workflow_id, connection_kind, mode, idempotency_key, credential_type, credential_reference, external_property_id, external_stream_id, external_account_id, configuration, defer_reason):
        if mode == "deferred" and (credential_reference or configuration):
            raise ValueError("deferred_connection_must_not_include_credentials")
        if mode == "deferred" and not defer_reason:
            raise ValueError("defer_reason_required")
        if mode == "registered" and not credential_reference:
            raise ValueError("ga4_connection_reference_required")
        value = {"kind": connection_kind, "mode": mode, "status": "deferred" if mode == "deferred" else "pending_approval", "idempotencyKey": idempotency_key, "externalPropertyId": external_property_id, "externalStreamId": external_stream_id, "externalAccountId": external_account_id, "configuration": configuration, "deferReason": defer_reason}
        self.connections.setdefault(workflow_id, []).append(value)
        base = self.workflows[workflow_id]["payload"]
        return self.payload(workflow_id, base["company"]["id"], base["company"]["name"], base["website"]["id"], base["website"]["canonicalDomain"], "local_service_v1")

    def onboarding_first_sync(self, context, workflow_id, action, idempotency_key=None):
        if action == "request":
            self.first_sync[workflow_id] = {"status": "blocked", "detail": {"code": "ga4_assignment_approval_required"}, "requestIdempotencyKey": idempotency_key}
        base = self.workflows[workflow_id]["payload"]
        return self.payload(workflow_id, base["company"]["id"], base["company"]["name"], base["website"]["id"], base["website"]["canonicalDomain"], "local_service_v1")

    def add_onboarding_client_membership(self, context, workflow_id, email, role, authorization_reference):
        value = {"workflowId": workflow_id, "userId": f"user-{len(self.members) + 2}", "email": email.strip().lower(), "role": role, "websiteId": "website-1", "authorizationReference": authorization_reference}
        self.members.append(value)
        base = self.workflows[workflow_id]["payload"]
        return self.payload(workflow_id, base["company"]["id"], base["company"]["name"], base["website"]["id"], base["website"]["canonicalDomain"], "local_service_v1")

    def onboarding_checklist(self, context, workflow_id):
        value = self.onboarding_workflow(context, workflow_id)
        return {"workflowId": workflow_id, "status": value["status"], "checklist": value["checklist"], "handoff": value["handoff"], "externalGates": {"credentials": "explicit_reference_or_deferred", "approvals": "caller_supplied_only", "reportDispatch": "not_requested"}}


def settings():
    site = load_site()
    return Settings("live", True, True, site.property_id, site.stream_id, TOKEN, "127.0.0.1", 3000, database_url="postgresql://configured", operator_email="operator@example.com")


def headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def create_workflow(client):
    return client.post("/api/onboarding/workflows", headers=headers(), json={"idempotencyKey": "client-one-001", "companyId": "client_one", "companyName": "Client One Dental", "websiteId": "client_one_site", "canonicalDomain": "clientone.example.com", "contractSlug": "local_service_v1"})


def test_onboarding_workflow_is_idempotent_and_resumable():
    database = OnboardingDatabase()
    with TestClient(create_app(settings(), database=database)) as client:
        first = create_workflow(client)
        replay = create_workflow(client)
        fetched = client.get(f"/api/onboarding/workflows/{first.json()['workflowId']}", headers=headers())
    assert first.status_code == 201
    assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
    assert fetched.status_code == 200 and fetched.json()["website"]["canonicalDomain"] == "clientone.example.com"


def test_onboarding_rejects_noncanonical_domains_and_idempotency_conflicts():
    database = OnboardingDatabase()
    with TestClient(create_app(settings(), database=database)) as client:
        invalid = client.post("/api/onboarding/workflows", headers=headers(), json={"idempotencyKey": "client-two-001", "companyId": "client_two", "companyName": "Client Two", "websiteId": "client_two_site", "canonicalDomain": "https://clienttwo.example.com"})
        assert invalid.status_code == 422
        assert create_workflow(client).status_code == 201
        conflict = client.post("/api/onboarding/workflows", headers=headers(), json={"idempotencyKey": "client-one-001", "companyId": "client_one", "companyName": "Changed Name", "websiteId": "client_one_site", "canonicalDomain": "clientone.example.com"})
    assert conflict.status_code == 409


def test_governance_connections_sync_and_client_access_keep_gates_explicit():
    database = OnboardingDatabase()
    with TestClient(create_app(settings(), database=database)) as client:
        workflow = create_workflow(client).json()["workflowId"]
        governance = client.post(f"/api/onboarding/workflows/{workflow}/governance", headers=headers(), json={"governanceStatus": "approved", "consentStatus": "approved", "governanceReference": "legal-approval-1", "consentReference": "consent-1"})
        deferred = client.post(f"/api/onboarding/workflows/{workflow}/connections", headers=headers(), json={"idempotencyKey": "ga4-deferred-1", "connectionKind": "ga4", "mode": "deferred", "deferReason": "Client has not supplied an approved GA4 connection yet"})
        sync = client.post(f"/api/onboarding/workflows/{workflow}/first-sync", headers=headers(), json={"action": "request", "idempotencyKey": "sync-request-1"})
        membership = client.post(f"/api/onboarding/workflows/{workflow}/memberships", headers=headers(), json={"email": "client@example.com", "role": "client_viewer", "authorizationReference": "client-auth-1"})
        checklist = client.get(f"/api/onboarding/workflows/{workflow}/checklist", headers=headers())
    assert governance.status_code == 200
    assert deferred.status_code == 200 and deferred.json()["connections"][0]["mode"] == "deferred"
    assert sync.status_code == 200 and sync.json()["handoff"]["reportDispatch"]["realClientReportSent"] is False
    assert membership.status_code == 201
    assert checklist.status_code == 200 and checklist.json()["handoff"]["ready"] is False
    assert "ga4_connection" in checklist.json()["handoff"]["blockingGates"]


def test_onboarding_requires_agency_owner_or_admin_and_explicit_membership_authorization():
    viewer = OnboardingDatabase(role="client_viewer")
    with TestClient(create_app(settings(), database=viewer)) as client:
        assert create_workflow(client).status_code == 403

    database = OnboardingDatabase()
    with TestClient(create_app(settings(), database=database)) as client:
        workflow = create_workflow(client).json()["workflowId"]
        response = client.post(f"/api/onboarding/workflows/{workflow}/memberships", headers=headers(), json={"email": "client@example.com", "role": "client_viewer"})
    assert response.status_code == 422


def test_onboarding_migration_is_rls_protected_and_does_not_store_credentials_or_report_recipients():
    sql = (Path(__file__).resolve().parents[1] / "infra" / "postgres" / "010_onboarding_workflows.sql").read_text()
    for table in ("onboarding_workflows", "onboarding_workflow_steps", "onboarding_connection_requests", "onboarding_sync_readiness", "client_membership_scopes"):
        assert f"CREATE TABLE IF NOT EXISTS app.{table}" in sql
        assert f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY" in sql
    assert "VALUES('010_onboarding_workflows')" in sql
    assert "recipient_email" not in sql and "refresh_token text" not in sql
    assert "current_actor_role() IN ('agency_owner','agency_admin')" in sql
    assert "app.website_allowed(website_id)" in sql
    assert "CONSTRAINT client_scope_membership_fk" in sql
    assert "CONSTRAINT onboarding_workflows_org_id_key" in sql
    assert "SET LOCAL lock_timeout" in sql
    assert "SET LOCAL statement_timeout" in sql


def test_onboarding_rls_keeps_agency_users_inside_the_current_organization():
    sql = (Path(__file__).resolve().parents[1] / "infra" / "postgres" / "010_onboarding_workflows.sql").read_text()
    policy = sql[sql.index("CREATE POLICY user_tenant_isolation"):sql.index("CREATE POLICY audit_tenant_isolation")]
    assert "EXISTS (" in policy
    assert "FROM app.memberships m" in policy
    assert "app.organization_allowed(m.organization_id)" in policy
    assert "OR app.current_actor_role() IN ('agency_owner','agency_admin','agency_analyst')" in policy
