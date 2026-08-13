from __future__ import annotations

from dataclasses import dataclass


AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
ALL_ROLES = AGENCY_ROLES | CLIENT_ROLES
WRITE_ROLES = frozenset({"agency_owner", "agency_admin", "client_admin"})


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    user_id: str
    email: str
    role: str

    def require_role(self, allowed: frozenset[str]) -> None:
        if self.role not in allowed:
            raise PermissionError("role_not_authorized")

