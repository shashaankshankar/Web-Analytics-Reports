from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token


AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
ALL_ROLES = AGENCY_ROLES | CLIENT_ROLES
WRITE_ROLES = frozenset({"agency_owner", "agency_admin", "client_admin"})


def google_token_claims(token: str) -> dict:
    return id_token.verify_oauth2_token(token,GoogleRequest(),audience=None)


def cloud_identity_email(token: str, fallback: str, verifier: Callable[[str],dict] = google_token_claims) -> str:
    if not token: return fallback
    try:
        claims=verifier(token)
        email=str(claims.get("email","")).strip().lower()
        if claims.get("email_verified") is not True or claims.get("iss") not in {"https://accounts.google.com","accounts.google.com"} or "@" not in email:
            raise ValueError
        return email
    except Exception as error:
        if isinstance(error,PermissionError): raise
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
