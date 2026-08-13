import base64
import json

import pytest

from app.auth import cloud_identity_email


def token(claims):
    payload=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_verified_cloud_identity_email_is_used_per_request():
    value=token({"email":"Client@Example.com","email_verified":True,"iss":"https://accounts.google.com"})
    assert cloud_identity_email(value,"operator@example.com") == "client@example.com"
    assert cloud_identity_email("","operator@example.com") == "operator@example.com"


def test_unverified_or_untrusted_identity_is_rejected():
    with pytest.raises(PermissionError): cloud_identity_email(token({"email":"client@example.com","email_verified":False,"iss":"https://accounts.google.com"}),"operator@example.com")
    with pytest.raises(PermissionError): cloud_identity_email(token({"email":"client@example.com","email_verified":True,"iss":"https://attacker.invalid"}),"operator@example.com")
