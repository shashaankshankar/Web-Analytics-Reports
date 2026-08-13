import pytest

from app.auth import cloud_identity_email


def test_verified_cloud_identity_email_is_used_per_request():
    verifier=lambda _: {"email":"Client@Example.com","email_verified":True,"iss":"https://accounts.google.com"}
    assert cloud_identity_email("signed-token","operator@example.com",verifier) == "client@example.com"
    assert cloud_identity_email("","operator@example.com") == "operator@example.com"


def test_unverified_or_untrusted_identity_is_rejected():
    with pytest.raises(PermissionError): cloud_identity_email("token","operator@example.com",lambda _: {"email":"client@example.com","email_verified":False,"iss":"https://accounts.google.com"})
    with pytest.raises(PermissionError): cloud_identity_email("token","operator@example.com",lambda _: {"email":"client@example.com","email_verified":True,"iss":"https://attacker.invalid"})
    with pytest.raises(PermissionError): cloud_identity_email("forged","operator@example.com",lambda _: (_ for _ in ()).throw(ValueError("bad signature")))
