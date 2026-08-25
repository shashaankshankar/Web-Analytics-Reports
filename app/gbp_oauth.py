from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import os
import secrets
import time
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
from fastapi.responses import HTMLResponse, RedirectResponse, Response

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
    expected_place_id: str = ""

    @classmethod
    def from_env(cls) -> "OAuthSettings":
        return cls(
            client_id=os.getenv("GBP_OAUTH_CLIENT_ID", "").strip(),
            client_secret=os.getenv("GBP_OAUTH_CLIENT_SECRET", "").strip(),
            redirect_uri=os.getenv("GBP_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
            secret_id=os.getenv("GBP_OAUTH_SECRET_ID", "GBP_OAUTH_CREDENTIALS_JSON").strip(),
            expected_place_id=os.getenv("GBP_OAUTH_EXPECTED_PLACE_ID", "").strip(),
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
        if not self.expected_place_id:
            raise GBPAuthError("GBP OAuth expected GBP Place ID is not configured.")


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
    except urllib.error.HTTPError as exc:
        logger.warning(
            "GBP OAuth provider request failed grant_type=%s status=%s",
            form.get("grant_type", "unknown"),
            exc.code,
        )
        # Provider response bodies can contain sensitive account or token
        # material. Keep them out of logs and user-facing diagnostics.
        raise GBPAuthError("Google authorization-code exchange failed.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning(
            "GBP OAuth provider request failed grant_type=%s error=%s",
            form.get("grant_type", "unknown"),
            type(exc).__name__,
        )
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


def _get_json(url: str, access_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2**attempt)
                continue
            logger.warning(
                "GBP access verification API request failed api=%s status=%s",
                urllib.parse.urlsplit(url).hostname or "unknown",
                exc.code,
            )
            # Keep account and location details out of the callback response/logs.
            raise GBPAuthError("Google Business Profile access verification failed.") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning(
                "GBP access verification API request failed api=%s error=%s",
                urllib.parse.urlsplit(url).hostname or "unknown",
                type(exc).__name__,
            )
            # Keep account and location details out of the callback response/logs.
            raise GBPAuthError("Google Business Profile access verification failed.") from exc
        break
    if not isinstance(payload, dict):
        raise GBPAuthError("Google Business Profile access verification returned an invalid response.")
    return payload


def verify_gbp_access(settings: OAuthSettings, bundle: Mapping[str, str]) -> None:
    """Verify the refresh credential can access the configured client location.

    The callback is intentionally public so Google can redirect to it. A valid
    OAuth state cookie alone must not be enough to rotate the shared worker
    credential, so the credential is accepted only after the authorized user
    can enumerate the configured public Place ID through the private GBP APIs.
    """
    settings.validate()
    token_response = _post_form(
        settings.token_uri,
        {
            "client_id": str(bundle.get("client_id") or settings.client_id),
            "client_secret": str(bundle.get("client_secret") or settings.client_secret),
            "refresh_token": str(bundle.get("refresh_token") or ""),
            "grant_type": "refresh_token",
        },
    )
    access_token = str(token_response.get("access_token") or "").strip()
    if not access_token:
        raise GBPAuthError("Google did not return an access token for GBP verification.")

    expected_place_id = settings.expected_place_id.removeprefix("places/")
    account_page_token: Optional[str] = None
    seen_account_tokens: set[str] = set()
    accounts_url = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
    while True:
        query: list[tuple[str, str]] = [("pageSize", "20")]
        if account_page_token:
            if account_page_token in seen_account_tokens:
                raise GBPAuthError("Google returned invalid GBP account pagination.")
            seen_account_tokens.add(account_page_token)
            query.append(("pageToken", account_page_token))
        accounts = _get_json(
            f"{accounts_url}?{urllib.parse.urlencode(query)}",
            access_token,
        )
        for account in accounts.get("accounts", []) or []:
            account_name = str(account.get("name") or "").strip()
            if not account_name:
                continue
            location_page_token: Optional[str] = None
            seen_location_tokens: set[str] = set()
            locations_url = (
                "https://mybusinessbusinessinformation.googleapis.com/v1/"
                f"{account_name}/locations"
            )
            while True:
                location_query: list[tuple[str, str]] = [
                    ("readMask", "name,metadata"),
                    ("pageSize", "100"),
                ]
                if location_page_token:
                    if location_page_token in seen_location_tokens:
                        raise GBPAuthError("Google returned invalid GBP location pagination.")
                    seen_location_tokens.add(location_page_token)
                    location_query.append(("pageToken", location_page_token))
                locations = _get_json(
                    f"{locations_url}?{urllib.parse.urlencode(location_query)}",
                    access_token,
                )
                for location in locations.get("locations", []) or []:
                    metadata = location.get("metadata") or {}
                    place_id = str(
                        metadata.get("placeId") or metadata.get("place_id") or ""
                    ).strip()
                    if place_id == expected_place_id:
                        return
                location_page_token = str(
                    locations.get("nextPageToken")
                    or locations.get("next_page_token")
                    or ""
                ).strip() or None
                if not location_page_token:
                    break
        account_page_token = str(
            accounts.get("nextPageToken") or accounts.get("next_page_token") or ""
        ).strip() or None
        if not account_page_token:
            break

    raise GBPAuthError(
        "The authorized Google account cannot access the configured GBP location."
    )


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
        verify: Callable[[OAuthSettings, Mapping[str, str]], None] = verify_gbp_access,
        store_factory: Callable[[OAuthSettings], SecretManagerOAuthStore] = SecretManagerOAuthStore,
    ):
        self.settings_factory = settings_factory
        self.exchange = exchange
        self.verify = verify
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
            self.verify(settings, bundle)
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
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title></head><body>"
        f"<h1>{safe_title}</h1><p>{safe_message}</p>"
        f"<script nonce='{nonce}'>history.replaceState(null, '', '/oauth/google/callback');</script>"
        "</body></html>",
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


def create_oauth_router() -> APIRouter:
    router = APIRouter()

    @router.get("/oauth/google/start")
    def start_google_oauth() -> Response:
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
