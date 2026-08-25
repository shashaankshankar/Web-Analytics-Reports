from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest
from fastapi import APIRouter
from fastapi import Request as FastAPIRequest
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)

GBP_MANAGE_SCOPE = "https://www.googleapis.com/auth/business.manage"
GOOGLE_AUTHORIZATION_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
STATE_COOKIE_NAME = "gbp_oauth_state"
STATE_MAX_AGE_SECONDS = 600
DEFAULT_REDIRECT_URI = (
    "https://measurement-oauth-callback-ptlwmdunva-uc.a.run.app"
    "/oauth/google/callback"
)


class GBPAuthError(RuntimeError):
    """Safe, user-facing GBP OAuth error without provider response content."""


@dataclass(frozen=True)
class OAuthSettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    secret_id: str
    token_uri: str = GOOGLE_TOKEN_URI
    project_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "OAuthSettings":
        return cls(
            client_id=os.getenv("GBP_OAUTH_CLIENT_ID", "").strip(),
            client_secret=os.getenv("GBP_OAUTH_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("GBP_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
            secret_id=os.getenv("GBP_OAUTH_SECRET_ID", "GBP_OAUTH_CREDENTIALS_JSON").strip(),
            token_uri=os.getenv("GBP_OAUTH_TOKEN_URI", GOOGLE_TOKEN_URI).strip(),
            project_id=(
                os.getenv("GCP_PROJECT_ID", "").strip()
                or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
                or None
            ),
        )

    def validate(self) -> None:
        if not self.client_id or not self.client_secret:
            raise GBPAuthError("GBP OAuth client credentials are not configured.")
        if not self.redirect_uri.startswith("https://"):
            raise GBPAuthError("GBP OAuth redirect URI must use HTTPS.")
        if not self.secret_id:
            raise GBPAuthError("GBP OAuth Secret Manager target is not configured.")


def _post_form(url: str, form: Mapping[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Provider response bodies can contain sensitive account or token
        # material. Keep them out of logs and user-facing diagnostics.
        raise GBPAuthError("Google authorization-code exchange failed.") from exc
    if not isinstance(payload, dict):
        raise GBPAuthError("Google authorization-code exchange returned an invalid response.")
    return payload


def exchange_authorization_code(
    settings: OAuthSettings,
    code: str,
    poster: Callable[[str, Mapping[str, str]], dict[str, Any]] = _post_form,
) -> dict[str, str]:
    settings.validate()
    if not code.strip():
        raise GBPAuthError("Google did not return an authorization code.")

    response = poster(
        settings.token_uri,
        {
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": settings.redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    refresh_token = str(response.get("refresh_token") or "").strip()
    if not refresh_token:
        raise GBPAuthError(
            "Google did not return a refresh token. Start authorization again with consent."
        )

    returned_scope = str(response.get("scope") or "").split()
    if returned_scope and GBP_MANAGE_SCOPE not in returned_scope:
        raise GBPAuthError("The Google authorization did not include business.manage access.")

    # Store only the long-lived bundle needed by the reporting worker. The
    # short-lived access token is intentionally discarded.
    return {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh_token,
        "token_uri": settings.token_uri,
    }


class SecretManagerOAuthStore:
    """Append an OAuth credential bundle as a Secret Manager version."""

    def __init__(
        self,
        settings: OAuthSettings,
        auth_default: Callable[..., tuple[Any, Optional[str]]] = google.auth.default,
        session_factory: Callable[[Any], AuthorizedSession] = AuthorizedSession,
    ):
        self.settings = settings
        self.auth_default = auth_default
        self.session_factory = session_factory

    def add_version(self, bundle: Mapping[str, str]) -> None:
        credentials, inferred_project_id = self.auth_default(
            scopes=[GOOGLE_CLOUD_PLATFORM_SCOPE]
        )
        project_id = self.settings.project_id or inferred_project_id
        if not project_id:
            raise GBPAuthError("Google Cloud project is not configured for Secret Manager.")

        try:
            credentials.refresh(GoogleAuthRequest())
            session = self.session_factory(credentials)
            secret_name = (
                f"projects/{urllib.parse.quote(project_id, safe='')}/secrets/"
                f"{urllib.parse.quote(self.settings.secret_id, safe='')}"
            )
            encoded_payload = base64.b64encode(
                json.dumps(dict(bundle), sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
            response = session.post(
                f"https://secretmanager.googleapis.com/v1/{secret_name}:addVersion",
                json={"payload": {"data": encoded_payload}},
                timeout=30,
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise GBPAuthError("Secret Manager credential storage failed.") from exc
        except Exception as exc:
            # Keep provider and auth-library details out of the public callback.
            raise GBPAuthError("Secret Manager credential storage failed.") from exc

        if not 200 <= response.status_code < 300:
            raise GBPAuthError("Secret Manager credential storage failed.")


class GBPAuthFlow:
    """Stateless browser flow with an HTTP-only, short-lived state cookie."""

    def __init__(
        self,
        settings_factory: Callable[[], OAuthSettings] = OAuthSettings.from_env,
        exchange: Callable[[OAuthSettings, str], dict[str, str]] = exchange_authorization_code,
        store_factory: Callable[[OAuthSettings], SecretManagerOAuthStore] = SecretManagerOAuthStore,
    ):
        self.settings_factory = settings_factory
        self.exchange = exchange
        self.store_factory = store_factory

    def start(self) -> RedirectResponse:
        settings = self.settings_factory()
        settings.validate()
        state = secrets.token_urlsafe(32)
        query = urllib.parse.urlencode(
            {
                "client_id": settings.client_id,
                "redirect_uri": settings.redirect_uri,
                "response_type": "code",
                "scope": GBP_MANAGE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        response = RedirectResponse(
            f"{GOOGLE_AUTHORIZATION_URI}?{query}",
            status_code=302,
        )
        response.set_cookie(
            STATE_COOKIE_NAME,
            state,
            max_age=STATE_MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/oauth/google",
        )
        return response

    def callback(
        self,
        *,
        code: Optional[str],
        state: Optional[str],
        cookie_state: Optional[str],
        error: Optional[str],
    ) -> HTMLResponse:
        if error:
            return _html_result(
                "Google authorization was cancelled",
                "No GBP credential was stored. You may close this window.",
                status_code=400,
            )
        if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
            return _html_result(
                "GBP authorization could not be verified",
                "The authorization session expired or was not started from this service. Start again.",
                status_code=400,
            )

        try:
            settings = self.settings_factory()
            bundle = self.exchange(settings, code or "")
            self.store_factory(settings).add_version(bundle)
        except GBPAuthError as exc:
            logger.warning("GBP OAuth callback failed: %s", exc)
            return _html_result("GBP authorization failed", str(exc), status_code=502)
        except Exception:
            logger.exception("GBP OAuth callback failed unexpectedly")
            return _html_result(
                "GBP authorization failed",
                "The authorization could not be completed. Check the protected service logs.",
                status_code=500,
            )

        return _html_result(
            "GBP authorization complete",
            "The refresh credential was stored securely. You may close this window.",
            status_code=200,
        )


def _html_result(title: str, message: str, status_code: int) -> HTMLResponse:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title></head><body>"
        f"<h1>{safe_title}</h1><p>{safe_message}</p></body></html>",
        status_code=status_code,
    )


def create_oauth_router() -> APIRouter:
    router = APIRouter()

    @router.get("/oauth/google/start")
    def start_google_oauth() -> RedirectResponse | HTMLResponse:
        try:
            return GBPAuthFlow().start()
        except GBPAuthError as exc:
            return _html_result("GBP authorization is not configured", str(exc), 503)

    @router.get("/oauth/google/callback")
    def complete_google_oauth(
        request: FastAPIRequest,
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        response = GBPAuthFlow().callback(
            code=code,
            state=state,
            cookie_state=request.cookies.get(STATE_COOKIE_NAME),
            error=error,
        )
        response.delete_cookie(STATE_COOKIE_NAME, path="/oauth/google")
        return response

    return router
