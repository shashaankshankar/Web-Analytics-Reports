from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import json


AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
ALL_ROLES = AGENCY_ROLES | CLIENT_ROLES
WRITE_ROLES = frozenset({"agency_owner", "agency_admin", "client_admin"})


def cloud_identity_email(token: str, fallback: str) -> str:
    if not token or token.count(".") != 2: return fallback
    try:
        payload=token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims=json.loads(base64.urlsafe_b64decode(payload))
        email=str(claims.get("email","")).strip().lower()
        if claims.get("email_verified") is not True or claims.get("iss") not in {"https://accounts.google.com","accounts.google.com"} or "@" not in email:
            raise ValueError
        return email
    except (ValueError,json.JSONDecodeError,UnicodeDecodeError,binascii.Error):
        raise PermissionError("invalid_cloud_identity")


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    user_id: str
    email: str
    role: str

    def require_role(self, allowed: frozenset[str]) -> None:
        if self.role not in allowed:
            raise PermissionError("role_not_authorized")
