import pytest

from app.credentials import ANALYTICS_READONLY, AnalyticsCredential, OAuthCredential


class FakeOauthCredentials:
    scopes=[ANALYTICS_READONLY]


def test_oauth_credential_implements_same_read_only_interface_and_disables():
    value=OAuthCredential("refresh","client","secret",credentials=FakeOauthCredentials())
    assert isinstance(value,AnalyticsCredential)
    value.disable()
    with pytest.raises(RuntimeError,match="connection_disabled"): value.get_authorized_client()


def test_oauth_credential_rejects_scope_expansion():
    credentials=FakeOauthCredentials(); credentials.scopes=[ANALYTICS_READONLY,"https://www.googleapis.com/auth/analytics.edit"]
    with pytest.raises(RuntimeError,match="scope_violation"): OAuthCredential("refresh","client","secret",credentials=credentials)
