from pathlib import Path
import pytest
from pydantic import ValidationError
from app.config import ClientConfig, BrandingConfig, load_client_config, list_available_clients, Settings

def test_valid_client_config(sample_client_config_path):
    config = load_client_config(sample_client_config_path)
    assert config.client_id == "test-client"
    assert config.company_name == "Test Company"
    assert config.domain == "https://test.example.com"
    assert config.industry == "ecommerce"
    assert config.branding.primary_color == "#112233"
    assert config.recipients["client"] == "client@test.example.com"

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
