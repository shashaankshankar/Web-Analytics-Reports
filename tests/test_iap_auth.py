import base64
import json

import pytest

from app.auth import (
    IAP_CERTS_URL,
    IAP_ISSUER,
    PortalIdentityError,
    iap_token_claims,
    resolve_portal_identity_email,
)
from app.config import Settings, load_site


NOW = 1_800_000_000
AUDIENCE = "/projects/123456789/global/backendServices/987654321"


def claims(**overrides):
    value = {
        "iss": IAP_ISSUER,
        "aud": AUDIENCE,
        "email": " Client@Example.com ",
        "iat": NOW - 30,
        "exp": NOW + 300,
    }
    value.update(overrides)
    return value


def removed_signature_token(value):
    payload = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f"header.{payload}.SIGNATURE_REMOVED_BY_GOOGLE"


def signed_iap_assertion(value=None):
    return "signed-iap-assertion"


def test_signed_iap_identity_requires_and_normalizes_verified_claims():
    assert resolve_portal_identity_email(
        x_goog_iap_jwt_assertion=signed_iap_assertion(),
        expected_audience=AUDIENCE,
        portal_iap_mode="direct",
        now=NOW,
        iap_verifier=lambda token: claims(),
    ) == "client@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://accounts.google.com.evil.example"},
        {"email_verified": False},
        {"aud": "wrong-audience"},
        {"iat": NOW + 61},
        {"exp": NOW},
        {"email": "not-an-email"},
    ],
)
def test_signed_iap_identity_rejects_untrusted_or_stale_claims(overrides):
    with pytest.raises(PortalIdentityError, match="invalid_portal_identity"):
        resolve_portal_identity_email(
            x_goog_iap_jwt_assertion=signed_iap_assertion(),
            expected_audience=AUDIENCE,
            portal_iap_mode=True,
            now=NOW,
            iap_verifier=lambda token: claims(**overrides),
        )


def test_signed_iap_assertion_uses_iap_issuer_and_matches_compatibility_header():
    assertion = claims()
    assert resolve_portal_identity_email(
        x_goog_iap_jwt_assertion="signed-iap-assertion",
        x_goog_authenticated_user_email="accounts.google.com:CLIENT@example.com",
        expected_audience=AUDIENCE,
        portal_iap_mode="iap",
        now=NOW,
        iap_verifier=lambda token: assertion,
    ) == "client@example.com"


def test_iap_jwt_verification_uses_the_iap_jwks_and_configured_audience(monkeypatch):
    calls = []

    def verify(token, request, *, audience, certs_url):
        calls.append((token, audience, certs_url))
        return claims()

    monkeypatch.setattr("app.auth.id_token.verify_token", verify)

    assert iap_token_claims("signed-iap-assertion", AUDIENCE) == claims()
    assert calls == [("signed-iap-assertion", AUDIENCE, IAP_CERTS_URL)]


def test_transport_auth_may_coexist_but_never_becomes_portal_identity():
    calls = []

    def verify(token):
        calls.append(token)
        return claims()

    assert resolve_portal_identity_email(
        authorization="Bearer transport-authorization",
        x_serverless_authorization="Bearer transport-service-token",
        x_goog_iap_jwt_assertion=signed_iap_assertion(),
        expected_audience=AUDIENCE,
        portal_iap_mode="direct",
        now=NOW,
        iap_verifier=verify,
    ) == "client@example.com"
    assert calls == ["signed-iap-assertion"]


@pytest.mark.parametrize("transport_header", ["authorization", "x_serverless_authorization"])
def test_transport_auth_without_iap_assertion_is_not_a_portal_identity(transport_header):
    with pytest.raises(PortalIdentityError, match="identity_assertion_required"):
        resolve_portal_identity_email(
            **{transport_header: "Bearer transport-token"},
            expected_audience=AUDIENCE,
            portal_iap_mode=True,
            google_verifier=lambda token: claims(),
        )


def test_signature_removed_token_is_never_a_portal_identity():
    token = removed_signature_token(claims())
    with pytest.raises(PortalIdentityError, match="portal_iap_disabled"):
        resolve_portal_identity_email(
            x_serverless_authorization=f"Bearer {token}",
            expected_audience=AUDIENCE,
            portal_iap_mode=False,
            now=NOW,
        )

    with pytest.raises(PortalIdentityError, match="identity_assertion_required"):
        resolve_portal_identity_email(
            x_serverless_authorization=f"Bearer {token}",
            expected_audience=AUDIENCE,
            portal_iap_mode="direct",
            live=True,
            now=NOW,
        )

    with pytest.raises(PortalIdentityError, match="invalid_portal_identity"):
        resolve_portal_identity_email(
            x_goog_iap_jwt_assertion=token,
            expected_audience=AUDIENCE,
            portal_iap_mode="direct",
            now=NOW,
        )


def test_signature_removed_cloud_run_assertion_still_requires_claim_validation():
    for invalid in (
        claims(aud="wrong-audience"),
        claims(iss="https://accounts.google.com"),
        claims(exp=NOW),
        claims(iat=NOW + 61),
    ):
        with pytest.raises(PortalIdentityError, match="invalid_portal_identity"):
            resolve_portal_identity_email(
                x_goog_iap_jwt_assertion=removed_signature_token(invalid),
                expected_audience=AUDIENCE,
                portal_iap_mode=True,
                now=NOW,
            )


def test_compatibility_header_must_match_signed_iap_identity():
    with pytest.raises(PortalIdentityError, match="authenticated_email_mismatch"):
        resolve_portal_identity_email(
            x_goog_iap_jwt_assertion=signed_iap_assertion(),
            x_goog_authenticated_user_email="accounts.google.com:other@example.com",
            expected_audience=AUDIENCE,
            portal_iap_mode=True,
            now=NOW,
            iap_verifier=lambda token: claims(),
        )
    with pytest.raises(PortalIdentityError, match="identity_assertion_required"):
        resolve_portal_identity_email(
            x_goog_authenticated_user_email="accounts.google.com:client@example.com",
            expected_audience=AUDIENCE,
            portal_iap_mode=True,
        )


def test_live_portal_mode_fails_closed_without_expected_audience():
    with pytest.raises(PortalIdentityError, match="portal_iap_audience_required"):
        resolve_portal_identity_email(
            x_goog_iap_jwt_assertion=signed_iap_assertion(),
            portal_iap_mode=True,
            live=True,
        )


def test_settings_load_explicit_portal_iap_mode_and_audience():
    settings = Settings.from_environment(
        {
            "PLATFORM_MODE": "live",
            "PORTAL_IAP_MODE": "direct",
            "PORTAL_IAP_EXPECTED_AUDIENCE": AUDIENCE,
        }
    )
    assert settings.portal_iap_enabled is True
    assert settings.portal_expected_audience == AUDIENCE
    settings.validate(load_site())


def test_live_portal_settings_require_audience_and_reject_alias_drift():
    missing = Settings.from_environment({"PLATFORM_MODE": "live", "PORTAL_IAP_MODE": "direct"})
    with pytest.raises(RuntimeError, match="portal_iap_audience_required"):
        missing.validate(load_site())

    drift = Settings.from_environment(
        {
            "PLATFORM_MODE": "live",
            "PORTAL_IAP_MODE": "direct",
            "PORTAL_IAP_AUDIENCE": "one",
            "PORTAL_IAP_EXPECTED_AUDIENCE": "two",
        }
    )
    with pytest.raises(RuntimeError, match="portal_iap_audience_mismatch"):
        drift.validate(load_site())
