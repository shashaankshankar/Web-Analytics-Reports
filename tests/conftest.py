import pytest
from pathlib import Path

@pytest.fixture
def sample_client_config_path(tmp_path):
    config_content = """{
        "client_id": "test-client",
        "company_name": "Test Company",
        "domain": "https://test.example.com",
        "industry": "ecommerce",
        "ga4_property_id": "111222333",
        "gsc_site_url": "https://test.example.com/",
        "gbp_location_id": "loc_123",
        "branding": {
            "primary_color": "#112233",
            "secondary_color": "#445566",
            "accent_color": "#778899",
            "logo_url": "https://test.example.com/logo.png"
        },
        "recipients": {
            "client": "client@test.example.com",
            "agency_cc": "agency@growth.example.com"
        },
        "timezone": "America/New_York",
        "monthly_retainer_focus": "Organic product rankings and conversion rate optimization"
    }"""
    cfg_file = tmp_path / "test-client.json"
    cfg_file.write_text(config_content, encoding="utf-8")
    return cfg_file
