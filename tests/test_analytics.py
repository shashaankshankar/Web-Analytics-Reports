from datetime import date
import pytest
from app.analytics.contracts import (
    GrowthAnalysisInput,
    MetricDelta,
    StrikingDistanceKeyword,
)
from app.analytics.metrics import (
    calculate_percentage_change,
    determine_direction,
    calculate_date_ranges,
    filter_striking_distance_keywords,
    aggregate_growth_metrics,
)
from app.config import ClientConfig

def test_calculate_percentage_change():
    assert calculate_percentage_change(120, 100) == 20.0
    assert calculate_percentage_change(80, 100) == -20.0
    assert calculate_percentage_change(100, 100) == 0.0
    assert calculate_percentage_change(100, 0) is None  # Zero prior edge case

def test_determine_direction():
    assert determine_direction(105, 100) == "up"
    assert determine_direction(95, 100) == "down"
    assert determine_direction(100, 100) == "flat"

def test_calculate_date_ranges():
    fixed_today = date(2026, 8, 19)
    start, end, prior_start, prior_end = calculate_date_ranges(days=28, today=fixed_today)
    assert end == "2026-08-18"
    assert start == "2026-07-22"
    assert prior_end == "2026-07-21"
    assert prior_start == "2026-06-24"

def test_filter_striking_distance_keywords():
    raw_queries = [
        {"query": "page 1 top", "position": 2.1, "impressions": 1000, "clicks": 200, "ctr": 0.2},
        {"query": "striking keyword high imp", "position": 11.5, "impressions": 1200, "clicks": 20, "ctr": 0.016},
        {"query": "striking keyword low imp", "position": 14.0, "impressions": 5, "clicks": 0, "ctr": 0.0},  # filtered by imp < 10
        {"query": "page 3 deep", "position": 25.0, "impressions": 800, "clicks": 0, "ctr": 0.0},  # filtered by pos > 20
        {"query": "striking keyword medium imp", "position": 9.2, "impressions": 600, "clicks": 15, "ctr": 0.025},
    ]
    results = filter_striking_distance_keywords(raw_queries, min_position=8.0, max_position=20.0, min_impressions=10)
    assert len(results) == 2
    assert results[0].query == "striking keyword high imp"
    assert results[0].opportunity_score > results[1].opportunity_score

def test_aggregate_growth_metrics():
    client = ClientConfig(
        client_id="acme",
        company_name="Acme Corp",
        domain="https://acme.example.com",
        industry="b2b_saas",
    )
    ga4_data = {
        "summary": {"activeUsers": 500, "sessions": 700, "engagementRate": 0.65, "bounceRate": 0.35, "conversions": 25},
        "prior_summary": {"activeUsers": 400, "sessions": 550, "engagementRate": 0.60, "bounceRate": 0.40, "conversions": 20},
        "channels": [{"channel": "Organic Search", "sessions": 450, "activeUsers": 350, "conversions": 18, "priorSessions": 350, "sessionChange": 100}],
        "pages": [{"pagePath": "/signup", "sessions": 120, "activeUsers": 100, "priorSessions": 90, "sessionChange": 30}],
        "events": {"generate_lead": 25},
        "prior_events": {"generate_lead": 20},
    }
    gsc_queries = [
        {"query": "b2b saas tool", "position": 12.0, "impressions": 800, "clicks": 10, "ctr": 0.012},
    ]
    gbp_data = {
        "phone_calls": 5, "prior_phone_calls": 3,
        "direction_requests": 0, "prior_direction_requests": 0,
        "website_clicks": 20, "prior_website_clicks": 15,
    }
    growth_input = aggregate_growth_metrics(
        client=client,
        start_date="2026-07-22",
        end_date="2026-08-18",
        prior_start_date="2026-06-24",
        prior_end_date="2026-07-21",
        ga4_data=ga4_data,
        gsc_queries=gsc_queries,
        gbp_data=gbp_data,
    )
    assert isinstance(growth_input, GrowthAnalysisInput)
    assert len(growth_input.core_metrics) == 4
    sess_metric = next(m for m in growth_input.core_metrics if m.metric_name == "sessions")
    assert sess_metric.current_value == 700
    assert sess_metric.prior_value == 550
    assert sess_metric.direction == "up"
    assert growth_input.top_pages[0].is_high_intent is True  # '/signup' is high intent
