from app.config import Settings, load_site
from app.telemetry import configure_tracing, safe_http_attributes


def test_tracing_is_disabled_without_cloud_credentials():
    settings = Settings("demo", False, False, "", "", "", "127.0.0.1", 3000)
    assert configure_tracing(settings) is None


def test_http_trace_attributes_are_a_strict_privacy_allowlist():
    attributes = safe_http_attributes("GET", "/oauth/google/callback", 302)
    assert attributes == {
        "http.request.method": "GET",
        "http.route": "/oauth/google/callback",
        "http.response.status_code": 302,
    }
    assert set(attributes) == {"http.request.method", "http.route", "http.response.status_code"}


def test_trace_sample_rate_is_validated():
    settings = Settings(
        "demo", False, False, "", "", "", "127.0.0.1", 3000,
        google_cloud_project="project", trace_enabled=True, trace_sample_rate=1.1,
    )
    try:
        settings.validate(load_site())
    except RuntimeError as error:
        assert str(error) == "invalid_trace_sample_rate"
    else:
        raise AssertionError("invalid trace sample rate was accepted")
