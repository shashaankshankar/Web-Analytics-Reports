import pytest

from app.external_sources import assert_no_direct_identifiers, opaque_record_hash, safe_page_path, safe_source_id


def test_external_source_normalization_rejects_identifiers_and_unsafe_urls():
    assert_no_direct_identifiers({"campaign_id":"123","clicks":5})
    with pytest.raises(ValueError,match="prohibited_direct_identifier"): assert_no_direct_identifiers({"email":"person@example.com"})
    assert safe_page_path("/contact") == "/contact"
    with pytest.raises(ValueError,match="unsafe_page_path"): safe_page_path("/contact?patient=1")
    with pytest.raises(ValueError,match="unsafe_source_identifier"): safe_source_id("not allowed / identifier")


def test_first_party_record_ids_are_one_way_hmac_values():
    value=opaque_record_hash("crm-record-123","s"*32)
    assert len(value)==64 and "crm-record-123" not in value
    with pytest.raises(ValueError,match="secret_required"): opaque_record_hash("crm-record-123","short")
