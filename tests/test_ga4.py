from datetime import date

from app.ga4 import freshness_for, period_dates, previous_dates, safe_landing_page


def test_fixed_periods_and_comparisons_do_not_overlap():
    assert period_dates("7d",date(2026,8,12)) == ("2026-08-05","2026-08-11")
    assert previous_dates("7d",date(2026,8,12)) == ("2026-07-29","2026-08-04")
    assert period_dates("last_month",date(2026,8,12)) == ("2026-07-01","2026-07-31")


def test_landing_pages_drop_queries_and_identifier_paths():
    assert safe_landing_page("/services?email=person@example.com") == "/services"
    assert safe_landing_page("/patient/123456") == "[redacted_identifier_path]"
    assert safe_landing_page("https://example.com/about#staff") == "/about"


def test_freshness_is_explicit():
    assert freshness_for("2026-08-11",date(2026,8,12)) == "provisional"
    assert freshness_for("2026-08-01",date(2026,8,12)) == "reconciling"
    assert freshness_for("2026-07-01",date(2026,8,12)) == "stable"
