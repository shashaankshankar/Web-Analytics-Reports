from datetime import datetime, timezone

import pytest

from app.config import Settings, load_site
from app.oauth import ANALYTICS_READONLY_SCOPE, KmsCipher, OAuthManager


class FakeKms:
    def encrypt(self,request):
        class Result: ciphertext=b"ciphertext"
        assert request["additional_authenticated_data"]
        return Result()
    def decrypt(self,request):
        class Result: plaintext=b"plaintext"
        assert request["additional_authenticated_data"]
        return Result()


def manager():
    return OAuthManager("client-id","client-secret","https://example.com/oauth/google/callback","s"*32,KmsCipher("projects/p/locations/global/keyRings/r/cryptoKeys/k",FakeKms()),True)


def test_oauth_authorization_is_signed_scoped_offline_and_pkce_protected():
    value=manager().create_authorization("org-1","user-1")
    assert "access_type=offline" in value["url"]
    assert "code_challenge_method=S256" in value["url"]
    assert "analytics.readonly" in value["url"]
    verified=manager().verify_state(value["state"])
    assert verified["organizationId"] == "org-1" and verified["userId"] == "user-1"
    with pytest.raises(PermissionError): manager().verify_state(value["state"]+"changed")


def test_kms_cipher_uses_authenticated_encryption_context():
    cipher=manager().cipher
    assert cipher.encrypt("secret","context") == b"ciphertext"
    assert cipher.decrypt(b"ciphertext","context") == "plaintext"


def test_oauth_is_disabled_without_explicit_enablement():
    value=OAuthManager("client","secret","https://example.com/callback","s"*32,KmsCipher(""),False)
    assert value.configured is False
    with pytest.raises(RuntimeError,match="not_configured"): value.create_authorization("org","user")


def test_testing_oauth_can_be_enabled_without_public_production_approval():
    settings=Settings.from_environment({
        "GOOGLE_OAUTH_ENABLED":"true",
        "GOOGLE_OAUTH_PRODUCTION_APPROVED":"false",
    })
    assert settings.google_oauth_enabled is True
    assert settings.google_oauth_production_approved is False


def test_oauth_production_approval_requires_explicit_enablement():
    settings=Settings.from_environment({"GOOGLE_OAUTH_PRODUCTION_APPROVED":"true"})
    with pytest.raises(RuntimeError,match="oauth_production_approval_requires_enablement"):
        settings.validate(load_site())
