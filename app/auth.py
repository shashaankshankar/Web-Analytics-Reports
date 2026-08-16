from __future__ import annotations

import base64
import json
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token


LOGGER = logging.getLogger(__name__)

AGENCY_ROLES = frozenset({"agency_owner", "agency_admin", "agency_analyst"})
CLIENT_ROLES = frozenset({"client_admin", "client_viewer"})
ALL_ROLES = AGENCY_ROLES | CLIENT_ROLES
WRITE_ROLES = frozenset({"agency_owner", "agency_admin", "client_admin"})

GOOGLE_ISSUER = "https://accounts.google.com"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
SIGNATURE_REMOVED_BY_GOOGLE = "SIGNATURE_REMOVED_BY_GOOGLE"
CLOCK_SKEW_SECONDS = 60
MAX_UNSIGNED_TOKEN_AGE_SECONDS = 3600

Claims = Mapping[str, Any]
ClaimsVerifier = Callable[[str], Claims]


class PortalIdentityError(PermissionError):
    """Raised when a portal identity cannot be established safely."""


def _normalized_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("email_missing")
    email = value.strip().casefold()
    if (
        not email
        or len(email) > 320
        or any(character.isspace() or ord(character) < 32 for character in email)
        or email.count("@") != 1
    ):
        raise ValueError("email_invalid")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("email_invalid")
    return email


def _header_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortalIdentityError("invalid_identity_header")
    value = value.strip()
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PortalIdentityError("invalid_identity_header")
    return value


def _bearer_token(value: Any) -> str | None:
    value = _header_text(value)
    if value is None:
        return None
    parts = value.split(" ")
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
        raise PortalIdentityError("invalid_bearer_header")
    if any(not part for part in parts) or any(character.isspace() for character in parts[1]):
        raise PortalIdentityError("invalid_bearer_header")
    return parts[1]


def _claims_mapping(claims: Any) -> dict[str, Any]:
    if not isinstance(claims, Mapping):
        raise ValueError("claims_invalid")
    return dict(claims)


def _timestamp(claims: Claims, name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}_invalid")
    return float(value)


def _validate_timestamps(
    claims: Claims,
    *,
    now: float,
    max_age: float | None = None,
) -> None:
    issued_at = _timestamp(claims, "iat")
    expires_at = _timestamp(claims, "exp")
    if issued_at > now + CLOCK_SKEW_SECONDS:
        raise ValueError("iat_in_future")
    if expires_at <= now:
        raise ValueError("token_expired")
    if expires_at < issued_at:
        raise ValueError("timestamp_order_invalid")
    if max_age is not None and now - issued_at > max_age + CLOCK_SKEW_SECONDS:
        raise ValueError("token_too_old")

    not_before = claims.get("nbf")
    if not_before is not None and _timestamp(claims, "nbf") > now + CLOCK_SKEW_SECONDS:
        raise ValueError("token_not_yet_valid")


def _validate_google_claims(
    claims: Claims,
    *,
    expected_audience: str,
    now: float,
    require_email_verified: bool,
    max_age: float | None = None,
) -> str:
    if claims.get("iss") != GOOGLE_ISSUER:
        raise ValueError("issuer_invalid")
    if not isinstance(expected_audience, str) or not expected_audience.strip():
        raise ValueError("audience_required")
    if claims.get("aud") != expected_audience:
        raise ValueError("audience_invalid")
    if require_email_verified and claims.get("email_verified") is not True:
        raise ValueError("email_unverified")
    if not require_email_verified and claims.get("email_verified") is False:
        raise ValueError("email_unverified")
    _validate_timestamps(claims, now=now, max_age=max_age)
    return _normalized_email(claims.get("email"))


def _validate_iap_claims(claims: Claims, *, expected_audience: str, now: float) -> str:
    if claims.get("iss") != IAP_ISSUER:
        raise ValueError("issuer_invalid")
    if not isinstance(expected_audience, str) or not expected_audience.strip():
        raise ValueError("audience_required")
    if claims.get("aud") != expected_audience:
        raise ValueError("audience_invalid")
    _validate_timestamps(claims, now=now)
    # IAP assertions are signed by the IAP JWKS and the current assertion
    # schema does not include email_verified. If present, a false value must
    # still fail closed.
    if claims.get("email_verified") is False:
        raise ValueError("email_unverified")
    return _normalized_email(claims.get("email"))


def _removed_signature_claims(token: str, *, now: float | None = None) -> dict[str, Any]:
    segments = token.split(".")
    if len(segments) != 3 or segments[2] != SIGNATURE_REMOVED_BY_GOOGLE:
        raise ValueError("signature_required")
    if not segments[1] or len(segments[1]) > 16384:
        raise ValueError("claims_invalid")
    encoded_payload = segments[1] + "=" * (-len(segments[1]) % 4)
    try:
        payload = base64.b64decode(encoded_payload, altchars=b"-_", validate=True)
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("claims_invalid") from error
    claims = _claims_mapping(claims)
    _validate_timestamps(
        claims,
        now=time.time() if now is None else float(now),
        max_age=MAX_UNSIGNED_TOKEN_AGE_SECONDS,
    )
    return claims


def google_token_claims(token: str, audience: str | None = None) -> dict[str, Any]:
    """Verify a Google-signed ID token using Google's public keys."""

    return id_token.verify_oauth2_token(token, GoogleRequest(), audience=audience)


def iap_token_claims(token: str, audience: str) -> dict[str, Any]:
    """Verify a signed IAP assertion using the IAP public-key set."""

    return id_token.verify_token(
        token,
        GoogleRequest(),
        audience=audience,
        certs_url=IAP_CERTS_URL,
    )


def _invoke_verifier(verifier: Callable[..., Claims], token: str, audience: str) -> dict[str, Any]:
    """Call test-injectable verifiers without weakening the default verifier."""

    if verifier is google_token_claims:
        return _claims_mapping(verifier(token, audience=audience))
    if verifier is iap_token_claims:
        return _claims_mapping(verifier(token, audience))
    return _claims_mapping(verifier(token))


def _compatibility_email(value: Any) -> str:
    value = _header_text(value)
    if value is None or not value.startswith("accounts.google.com:"):
        raise ValueError("authenticated_email_invalid")
    return _normalized_email(value.split(":", 1)[1])


def _portal_mode_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return False
    return value.strip().casefold() in {"1", "true", "on", "yes", "direct", "iap", "enabled"}


def resolve_portal_identity_email(
    *,
    authorization: str | None = None,
    x_serverless_authorization: str | None = None,
    x_goog_iap_jwt_assertion: str | None = None,
    x_goog_authenticated_user_email: str | None = None,
    expected_audience: str = "",
    portal_iap_mode: bool | str = False,
    live: bool = False,
    google_verifier: Callable[..., Claims] | None = None,
    iap_verifier: Callable[..., Claims] | None = None,
    now: float | None = None,
) -> str:
    """Return the only identity value the membership layer should receive.

    Only ``x-goog-iap-jwt-assertion`` can establish portal identity. The
    ``Authorization`` and ``X-Serverless-Authorization`` values are transport
    credentials and may coexist with the IAP assertion; they are deliberately
    never decoded or used as user identity here.
    """

    iap_value = _header_text(x_goog_iap_jwt_assertion)
    compatibility_value = _header_text(x_goog_authenticated_user_email)

    mode_enabled = _portal_mode_enabled(portal_iap_mode)
    if live and not mode_enabled:
        raise PortalIdentityError("portal_iap_required")
    if not mode_enabled:
        raise PortalIdentityError("portal_iap_disabled")
    if not isinstance(expected_audience, str) or not expected_audience.strip():
        raise PortalIdentityError("portal_iap_audience_required")
    if iap_value is None:
        raise PortalIdentityError("identity_assertion_required")

    request_now = time.time() if now is None else float(now)
    token = iap_value

    try:
        if token.endswith(f".{SIGNATURE_REMOVED_BY_GOOGLE}"):
            raise ValueError("signed_iap_assertion_required")
        verifier = iap_verifier or iap_token_claims
        claims = _invoke_verifier(verifier, token, expected_audience)
        email = _validate_iap_claims(claims, expected_audience=expected_audience, now=request_now)
        if compatibility_value is not None:
            try:
                compatibility_email = _compatibility_email(compatibility_value)
            except ValueError as error:
                raise PortalIdentityError("authenticated_email_invalid") from error
            if compatibility_email != email:
                raise PortalIdentityError("authenticated_email_mismatch")
        return email
    except PortalIdentityError:
        raise
    except Exception as error:
        LOGGER.warning("portal_identity_verification_failed error=%s", type(error).__name__)
        raise PortalIdentityError("invalid_portal_identity") from error


def portal_identity_email(**kwargs: Any) -> str:
    """Short alias for the portal identity resolver."""

    return resolve_portal_identity_email(**kwargs)


def cloud_identity_email(
    token: str,
    fallback: str,
    verifier: Callable[..., Claims] = google_token_claims,
    allow_cloud_run_signature_removed: bool = False,
    expected_audience: str | None = None,
    now: float | None = None,
) -> str:
    """Preserve the existing Cloud Run identity helper for the current app.

    Existing token/IAM callers intentionally retain their legacy fallback and
    verifier contract. Supplying ``expected_audience`` opts this helper into
    the stricter Google ID-token claim validation used by the portal path.
    New portal code should call :func:`resolve_portal_identity_email` so it
    cannot accidentally accept a missing identity or an ambiguous header set.
    """

    if not token:
        return fallback
    request_now = time.time() if now is None else float(now)
    try:
        if token.endswith(f".{SIGNATURE_REMOVED_BY_GOOGLE}"):
            if not allow_cloud_run_signature_removed:
                raise ValueError("signature_removed_not_allowed")
            claims = _removed_signature_claims(token, now=request_now)
        else:
            claims = _invoke_verifier(verifier, token, expected_audience or "") if expected_audience else _claims_mapping(verifier(token))
        if expected_audience:
            return _validate_google_claims(
                claims,
                expected_audience=expected_audience,
                now=request_now,
                require_email_verified=True,
            )
        email = _normalized_email(claims.get("email"))
        if claims.get("email_verified") is not True or claims.get("iss") not in {GOOGLE_ISSUER, "accounts.google.com"}:
            raise ValueError("identity_claims_invalid")
        return email
    except Exception as error:
        if isinstance(error, PermissionError):
            raise
        LOGGER.warning("cloud_identity_verification_failed error=%s", type(error).__name__)
        raise PermissionError("invalid_cloud_identity") from error


@dataclass(frozen=True)
class TenantContext:
    organization_id: str
    user_id: str
    email: str
    role: str

    def require_role(self, allowed: frozenset[str]) -> None:
        if self.role not in allowed:
            raise PermissionError("role_not_authorized")
