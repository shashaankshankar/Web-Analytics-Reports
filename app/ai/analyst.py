from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.analytics.contracts import (
    ActionItem,
    AIReportOutput,
    GrowthAnalysisInput,
)


GROWTH_ANALYST_SYSTEM_PROMPT = """You are an elite Senior Growth Analyst & Technical SEO Director at a premier digital growth agency.
Your job is to transform pre-computed client analytics data into a clear, factual, and actionable monthly performance report.

CORE RULES:
1. All numbers and statistics cited must match the input dataset exactly. Do not invent metrics or metrics deltas.
2. Keep explanations concise, practical, and grounded in the metrics provided.
3. Tailor vocabulary to the client\'s industry context.
4. Provide clear, realistic recommended next steps based on the data.
5. Return valid JSON matching the requested schema.
"""


def build_user_prompt(data: GrowthAnalysisInput) -> str:
    payload = {
        "client_profile": {
            "company_name": data.company_name,
            "domain": data.domain,
            "industry": data.industry,
            "monthly_retainer_focus": data.monthly_retainer_focus,
        },
        "period": {
            "current": f"{data.period_start} to {data.period_end}",
            "prior": f"{data.comparison_start} to {data.comparison_end}",
        },
        "core_metrics": [m.model_dump() for m in data.core_metrics],
        "top_channels": [c.model_dump() for c in data.top_channels],
        "top_pages": [p.model_dump() for p in data.top_pages],
        "striking_distance_keywords": [k.model_dump() for k in data.striking_distance_keywords],
        "local_seo": data.local_seo.model_dump(),
        "raw_summary_stats": data.raw_summary_stats,
    }

    return f"""Analyze this normalized 28-day growth dataset for {data.company_name} ({data.domain}) and produce an executive growth briefing.

Dataset:
{json.dumps(payload, indent=2)}

Respond in JSON with the exact following schema:
{{
  "executive_summary": [
    "First high-impact takeaway (bold headline + 1 sentence on key win/movement)",
    "Second takeaway (acquisition channel or traffic driver)",
    "Third takeaway (conversion, local intent, or primary opportunity area)"
  ],
  "traffic_and_inflow_insights": "2-3 paragraphs analyzing top performing channels, traffic momentum, high-intent landing page engagement, and customer acquisition flow.",
  "seo_and_content_opportunities": "2-3 paragraphs identifying specific striking-distance search queries (positions 8-20), ranking opportunity scores, content gaps, and recommended new landing or service pages.",
  "local_seo_insights": "1-2 paragraphs evaluating Google Business Profile visibility, direction requests, direct calls, review velocity, and local map pack capture.",
  "agency_action_plan": [
    {{
      "title": "Clear action title",
      "description": "Specific tactical action the agency is executing this month to capture growth.",
      "impact_area": "SEO / Conversion / Content / Local / Technical",
      "priority": "High / Medium"
    }}
  ],
  "overall_sentiment": "Growth / Moderate / Critical"
}}
"""


def fallback_growth_briefing(data: GrowthAnalysisInput) -> AIReportOutput:
    """Deterministic rule-based fallback if LLM API is unavailable or unconfigured."""
    sessions_metric = next((m for m in data.core_metrics if m.metric_name == "sessions"), None)
    users_metric = next((m for m in data.core_metrics if m.metric_name == "active_users"), None)
    conversions_metric = next((m for m in data.core_metrics if m.metric_name == "conversions"), None)

    sess_pct = f"{sessions_metric.percentage_change:+.1f}%" if (sessions_metric and sessions_metric.percentage_change is not None) else "stable"
    users_pct = f"{users_metric.percentage_change:+.1f}%" if (users_metric and users_metric.percentage_change is not None) else "stable"
    conv_val = int(conversions_metric.current_value) if conversions_metric else 0

    top_channel_name = data.top_channels[0].channel if data.top_channels else "Direct"
    top_kw = data.striking_distance_keywords[0].query if data.striking_distance_keywords else "brand terms"

    exec_summary = [
        f"Traffic reached {int(sessions_metric.current_value if sessions_metric else 0):,} sessions ({sess_pct} vs prior period) with {int(users_metric.current_value if users_metric else 0):,} active users.",
        f"{top_channel_name} remains the primary acquisition channel, driving the majority of qualified visitor volume.",
        f"High-intent conversion activity recorded {conv_val:,} key lead/engagement actions during the last 28 days.",
    ]

    traffic_insights = (
        f"During the latest 28-day cycle, {data.company_name} observed {int(sessions_metric.current_value if sessions_metric else 0):,} total sessions. "
        f"The primary traffic driver was {top_channel_name}, demonstrating steady interest in core services. "
        f"Landing page analysis indicates sustained engagement across primary service and contact touchpoints."
    )

    seo_insights = (
        f"Search Console analysis revealed high-potential striking-distance keyword opportunities, led by \"{top_kw}\". "
        f"Targeting queries currently ranking on page 2 (positions 8–20) represents the highest-ROI opportunity to capture immediate organic growth."
    )

    local_insights = (
        f"Local visibility generated {data.local_seo.phone_calls} direct phone calls and {data.local_seo.direction_requests} direction requests. "
        f"Maintaining review velocity and optimizing Google Business Profile categories will reinforce local map pack rankings."
    )

    action_plan = [
        ActionItem(
            title=f"Optimize On-Page SEO for '{top_kw}'",
            description=f"Enhance H1/H2 header structure, internal linking, and content depth targeting '{top_kw}' to push rankings from striking distance into top 3.",
            impact_area="SEO",
            priority="High",
        ),
        ActionItem(
            title="High-Intent Conversion Path Optimization",
            description="Streamline mobile CTA buttons and form microcopy on top landing pages to maximize conversion yield from existing traffic.",
            impact_area="Conversion",
            priority="High",
        ),
        ActionItem(
            title="Local Map Pack Citation & Review Acceleration",
            description="Audit local NAP citations and deploy automated review request cadence to strengthen local authority.",
            impact_area="Local",
            priority="Medium",
        ),
    ]

    return AIReportOutput(
        executive_summary=exec_summary,
        traffic_and_inflow_insights=traffic_insights,
        seo_and_content_opportunities=seo_insights,
        local_seo_insights=local_insights,
        agency_action_plan=action_plan,
        overall_sentiment="Growth",
    )


class GrowthAnalyst:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        http_client: Optional[httpx.Client] = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.http_client = http_client

    def analyze(self, data: GrowthAnalysisInput) -> AIReportOutput:
        """Synthesize structured growth metrics into executive AI briefing."""
        if not self.api_key or self.api_key.strip() == "":
            return fallback_growth_briefing(data)

        prompt = build_user_prompt(data)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GROWTH_ANALYST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        try:
            if self.http_client:
                response = self.http_client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            
            if response.status_code != 200:
                return fallback_growth_briefing(data)

            res_json = response.json()
            content_str = res_json["choices"][0]["message"]["content"]
            parsed = json.loads(content_str)
            return AIReportOutput(**parsed)
        except Exception:
            return fallback_growth_briefing(data)
