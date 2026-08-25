from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.gbp_oauth import (
    GBPAuthError,
    GBPAuthFlow,
    GBP_MANAGE_SCOPE,
    OAuthSettings,
    SecretManagerOAuthStore,
    exchange_authorization_code,
)


def oauth_settings() -> OAuthSettings:
    return OAuthSettings(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://callback.example.test/oauth/google/callback",
        secret_id="GBP_OAUTH_CREDENTIALS_JSON",
        expected_place_id="ChIJtarget",
        project_id="project-id",
    )


def test_start_redirects_to_google_with_business_manage_and_state_cookie():
    response = GBPAuthFlow(settings_factory=oauth_settings).start()

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://callback.example.test/oauth/google/callback"]
    assert query["scope"] == [GBP_MANAGE_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"]
    assert "gbp_oauth_state=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]


def test_callback_validates_state_before_exchanging_code():
    called = False

    def exchange(*args):
        nonlocal called
        called = True
        raise AssertionError("code exchange must not run for a mismatched state")

    response = GBPAuthFlow(
        settings_factory=oauth_settings,
        exchange=exchange,
    ).callback(
        code="one-time-code",
        state="query-state",
        cookie_state="different-state",
        error=None,
    )

    assert response.status_code == 400
    assert called is False
    assert "authorization session expired" in response.body.decode().lower()


def test_callback_stores_bundle_without_returning_secrets():
    stored: list[dict[str, str]] = []

    class FakeStore:
        def add_version(self, bundle):
            stored.append(dict(bundle))

    def exchange(settings, code):
        assert settings.client_id == "client-id"
        assert code == "one-time-code"
        return {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    response = GBPAuthFlow(
        settings_factory=oauth_settings,
        exchange=exchange,
        verify=lambda settings, bundle: None,
        store_factory=lambda settings: FakeStore(),
    ).callback(
        code="one-time-code",
        state="same-state",
        cookie_state="same-state",
        error=None,
    )

    assert response.status_code == 200
    assert "authorization complete" in response.body.decode().lower()
    assert "refresh-token" not in response.body.decode()
    assert "client-secret" not in response.body.decode()
    assert stored[0]["refresh_token"] == "refresh-token"


def test_callback_does_not_store_bundle_when_location_verification_fails():
    stored = False

    class FakeStore:
        def add_version(self, bundle):
            nonlocal stored
            stored = True

    def verify(settings, bundle):
        raise GBPAuthError("The authorized Google account cannot access the configured GBP location.")

    response = GBPAuthFlow(
        settings_factory=oauth_settings,
        exchange=lambda settings, code: {"refresh_token": "refresh-token"},
        verify=verify,
        store_factory=lambda settings: FakeStore(),
    ).callback(
        code="one-time-code",
        state="same-state",
        cookie_state="same-state",
        error=None,
    )

    assert response.status_code == 502
    assert stored is False
    assert "cannot access" in response.body.decode().lower()


def test_exchange_requires_a_refresh_token():
    with pytest.raises(GBPAuthError, match="refresh token"):
        exchange_authorization_code(
            oauth_settings(),
            "one-time-code",
            poster=lambda url, form: {"access_token": "short-lived"},
        )


def test_secret_manager_store_adds_encoded_bundle_without_printing_it():
    class FakeCredentials:
        def refresh(self, request):
            return None

    class FakeResponse:
        status_code = 200

    class FakeSession:
        def __init__(self):
            self.payload = None

        def post(self, url, *, json, timeout):
            self.payload = json
            return FakeResponse()

    credentials = FakeCredentials()
    session = FakeSession()
    store = SecretManagerOAuthStore(
        oauth_settings(),
        auth_default=lambda scopes: (credentials, "project-id"),
        session_factory=lambda received: session,
    )
    bundle = {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    store.add_version(bundle)

    assert session.payload is not None
    encoded = session.payload["payload"]["data"]
    assert json.loads(base64.b64decode(encoded)) == bundle
