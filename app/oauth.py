from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.cloud import kms_v1


ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class KmsCipher:
    key_name: str
    client: object | None = None

    @property
    def configured(self) -> bool:
        return self.key_name.startswith("projects/") and "/cryptoKeys/" in self.key_name

    def encrypt(self, plaintext: str, associated_data: str) -> bytes:
        if not self.configured: raise RuntimeError("oauth_kms_not_configured")
        client = self.client or kms_v1.KeyManagementServiceClient()
        response = client.encrypt(request={"name":self.key_name,"plaintext":plaintext.encode(),"additional_authenticated_data":associated_data.encode()})
        return response.ciphertext

    def decrypt(self, ciphertext: bytes, associated_data: str) -> str:
        if not self.configured: raise RuntimeError("oauth_kms_not_configured")
        client = self.client or kms_v1.KeyManagementServiceClient()
        response = client.decrypt(request={"name":self.key_name,"ciphertext":ciphertext,"additional_authenticated_data":associated_data.encode()})
        return response.plaintext.decode()


@dataclass(frozen=True)
class OAuthManager:
    client_id: str
    client_secret: str
    redirect_uri: str
    state_secret: str
    cipher: KmsCipher
    production_approved: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.production_approved and self.client_id and self.client_secret and self.redirect_uri.startswith("https://") and len(self.state_secret)>=32 and self.cipher.configured)

    def create_authorization(self, organization_id: str, user_id: str) -> dict:
        if not self.configured: raise RuntimeError("google_oauth_not_configured")
        verifier=_b64(secrets.token_bytes(48)); nonce=_b64(secrets.token_bytes(24))
        expires=datetime.now(timezone.utc)+timedelta(minutes=10)
        payload=_b64(json.dumps({"organizationId":organization_id,"userId":user_id,"nonce":nonce,"exp":int(expires.timestamp())},separators=(",",":")).encode())
        signature=_b64(hmac.new(self.state_secret.encode(),payload.encode(),hashlib.sha256).digest())
        state=f"{payload}.{signature}"
        challenge=_b64(hashlib.sha256(verifier.encode()).digest())
        query=urllib.parse.urlencode({"client_id":self.client_id,"redirect_uri":self.redirect_uri,"response_type":"code","scope":ANALYTICS_READONLY_SCOPE,"access_type":"offline","include_granted_scopes":"true","state":state,"code_challenge":challenge,"code_challenge_method":"S256","prompt":"consent"})
        return {"url":f"{AUTHORIZATION_ENDPOINT}?{query}","state":state,"stateHash":hashlib.sha256(state.encode()).hexdigest(),"verifier":verifier,"expiresAt":expires}

    def verify_state(self, state: str) -> dict:
        try:
            payload,signature=state.split(".",1)
            expected=_b64(hmac.new(self.state_secret.encode(),payload.encode(),hashlib.sha256).digest())
            if not hmac.compare_digest(signature,expected): raise ValueError
            value=json.loads(_unb64(payload))
            if int(value["exp"]) < int(datetime.now(timezone.utc).timestamp()): raise ValueError
            return value
        except (ValueError,KeyError,json.JSONDecodeError) as error:
            raise PermissionError("invalid_or_expired_oauth_state") from error

    def exchange_code(self, code: str, verifier: str) -> dict:
        if not self.configured: raise RuntimeError("google_oauth_not_configured")
        body=urllib.parse.urlencode({"code":code,"client_id":self.client_id,"client_secret":self.client_secret,"redirect_uri":self.redirect_uri,"grant_type":"authorization_code","code_verifier":verifier}).encode()
        request=urllib.request.Request(TOKEN_ENDPOINT,data=body,method="POST",headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request,timeout=30) as response: value=json.loads(response.read())
        scopes=set(value.get("scope","").split())
        if ANALYTICS_READONLY_SCOPE not in scopes: raise RuntimeError("oauth_required_scope_not_granted")
        if not value.get("refresh_token"): raise RuntimeError("oauth_refresh_token_missing_reconsent_required")
        return value

    def revoke(self, refresh_token: str) -> None:
        body=urllib.parse.urlencode({"token":refresh_token}).encode()
        request=urllib.request.Request(REVOCATION_ENDPOINT,data=body,method="POST",headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request,timeout=30): pass
