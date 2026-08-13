from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import logging
import base64
import json
import time

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

LOGGER=logging.getLogger(__name__)


AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
ALL_ROLES = AGENCY_ROLES | CLIENT_ROLES
WRITE_ROLES = frozenset({"agency_owner", "agency_admin", "client_admin"})


def google_token_claims(token: str) -> dict:
    return id_token.verify_oauth2_token(token,GoogleRequest(),audience=None)


def _removed_signature_claims(token: str) -> dict:
    segments=token.split(".")
    if len(segments)!=3 or segments[2]!="SIGNATURE_REMOVED_BY_GOOGLE": raise ValueError
    payload=segments[1]+"="*(-len(segments[1])%4)
    claims=json.loads(base64.urlsafe_b64decode(payload))
    now=int(time.time())
    if int(claims.get("exp",0))<=now or int(claims.get("iat",now+1))>now+60: raise ValueError
    return claims


def cloud_identity_email(token: str, fallback: str, verifier: Callable[[str],dict] = google_token_claims,
                         allow_cloud_run_signature_removed: bool = False) -> str:
    if not token: return fallback
    try:
        claims=_removed_signature_claims(token) if allow_cloud_run_signature_removed and token.endswith(".SIGNATURE_REMOVED_BY_GOOGLE") else verifier(token)
        email=str(claims.get("email","")).strip().lower()
        if claims.get("email_verified") is not True or claims.get("iss") not in {"https://accounts.google.com","accounts.google.com"} or "@" not in email:
            raise ValueError
        return email
    except Exception as error:
        if isinstance(error,PermissionError): raise
        LOGGER.warning("cloud_identity_verification_failed error=%s",type(error).__name__)
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
