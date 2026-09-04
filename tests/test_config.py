from pathlib import Path
import pytest
from pydantic import ValidationError
from app.config import ClientConfig, BrandingConfig, load_client_config, load_client_config_by_slug, list_available_clients, Settings

def test_valid_client_config(sample_client_config_path):
    config = load_client_config(sample_client_config_path)
    assert config.client_id == "test-client"
    assert config.company_name == "Test Company"
    assert config.domain == "https://test.example.com"
    assert config.industry == "ecommerce"
    assert config.branding.primary_color == "#112233"
    assert config.recipients["client"] == "client@test.example.com"
    assert config.goals == ["Organic product rankings", "Conversion rate optimization"]


def test_measurement_and_launch_dates_use_strict_iso_dates():
    config = ClientConfig(
        client_id="dated-client",
        company_name="Dated Client",
        domain="https://example.com",
        site_launch_date="2026-08-10",
        measurement_start_date="2026-08-12",
    )
    assert config.site_launch_date.isoformat() == "2026-08-10"
    assert config.measurement_start_date.isoformat() == "2026-08-12"


@pytest.mark.parametrize("field", ["site_launch_date", "measurement_start_date"])
def test_client_config_rejects_non_iso_or_invalid_dates(field):
    with pytest.raises(ValidationError, match="ISO date"):
        ClientConfig(
            client_id="invalid-date-client",
            company_name="Invalid Date Client",
            domain="https://example.com",
            **{field: "08/12/2026"},
        )

def test_client_goals_default_to_empty_list():
    config = ClientConfig(
        client_id="default-goals",
        company_name="Default Goals Co",
        domain="https://example.com",
    )
    assert config.goals == []

def test_client_config_rejects_removed_monthly_focus():
    with pytest.raises(ValidationError, match="monthly_retainer_focus"):
        ClientConfig(
            client_id="legacy-goals",
            company_name="Legacy Goals Co",
            domain="https://example.com",
            monthly_retainer_focus="Legacy focus",
        )

def test_client_config_still_ignores_unrelated_extra_fields():
    config = ClientConfig(
        client_id="extra-fields",
        company_name="Extra Fields Co",
        domain="https://example.com",
        unrelated_field="ignored",
    )
    assert not hasattr(config, "unrelated_field")

def test_invalid_client_id():
    with pytest.raises(ValidationError):
        ClientConfig(
            client_id="invalid slug with spaces!",
            company_name="Invalid Slug Co",
            domain="https://example.com",
        )

def test_domain_normalization():
    config = ClientConfig(
        client_id="norm-test",
        company_name="Norm Co",
        domain="example.com",
    )
    assert config.domain == "https://example.com"

def test_branding_hex_fallback():
    branding = BrandingConfig(primary_color="not-a-color")
    assert branding.primary_color == "#1E3A8A"

def test_list_available_clients(tmp_path):
    (tmp_path / "client-a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "client-b.json").write_text("{}", encoding="utf-8")
    clients = list_available_clients(config_dir=tmp_path)
    assert clients == ["client-a", "client-b"]

def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key-123")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_98765432101234567890")
    settings = Settings.from_env()
    assert settings.openrouter_api_key == "sk-or-v1-test-key-123"
    assert settings.resend_api_key == "re_test_98765432101234567890"
    assert settings.llm_model == "openai/gpt-5.6-luna"
    assert settings.llm_reasoning_effort == "medium"
    assert settings.llm_reasoning_mode == "standard"

def test_slug_loader_rejects_paths(tmp_path):
    with pytest.raises(ValueError):
        load_client_config_by_slug("../thehouseofdental", config_dir=tmp_path)

def test_delivery_requires_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("REPORT_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("REPORT_ALLOWED_CLIENTS", "thehouseofdental")
    settings = Settings.from_env()
    assert settings.report_delivery_enabled is True
    assert settings.report_allowed_clients == ("thehouseofdental",)


def test_gbp_configuration_keeps_private_candidates_and_public_place_id_separate():
    config = ClientConfig(
        client_id="dental-client",
        company_name="Dental Client",
        domain="https://example.com",
        gbp_location_id="locations/16486178344866374391",
        gbp_candidate_location_ids=["locations/16486178344866374391"],
        gbp_public_place_id="ChIJM7fB_p1v54gR35t3HRaGH_Q",
    )
    assert config.gbp_location_id == "locations/16486178344866374391"
    assert config.gbp_candidate_location_ids == ["locations/16486178344866374391"]
    assert config.gbp_public_place_id == "ChIJM7fB_p1v54gR35t3HRaGH_Q"


def test_website_inquiry_config_accepts_reference_only_and_rejects_raw_secret():
    config = ClientConfig(
        client_id="scoped-client",
        company_name="Scoped Client",
        domain="https://example.com",
        website_inquiry_metrics={
            "enabled": True,
            "provider": "secret_manager",
            "secret_manager_ref": "projects/example-project/secrets/scoped-inquiries/versions/latest",
            "expected_client_id": "scoped-client",
        },
    )
    assert config.website_inquiry_metrics.secret_manager_ref.startswith("projects/")
    with pytest.raises(ValidationError, match="Secret Manager resource reference"):
        ClientConfig(
            client_id="raw-secret",
            company_name="Raw Secret",
            domain="https://example.com",
            website_inquiry_metrics={
                "provider": "secret_manager",
                "secret_manager_ref": "re_test_123456789",
            },
        )
    with pytest.raises(ValidationError, match="website_api_key"):
        ClientConfig(
            client_id="nested-raw-secret",
            company_name="Nested Raw Secret",
            domain="https://example.com",
            website_inquiry_metrics={"website_api_key": "must-not-be-configured"},
        )
