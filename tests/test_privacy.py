from app.privacy import validate_event
def test_privacy_manifest_remains_fail_closed():
    assert not validate_event("/services","phone_click",{"location":"hero"},False)["allowed"]
    assert not validate_event("/contact?email=john@example.com","generate_lead",{},True)["allowed"]
    assert not validate_event("/contact","generate_lead",{"email":"john@example.com"},True)["allowed"]
    assert not validate_event("/patient/12345","phone_click",{},True)["allowed"]
