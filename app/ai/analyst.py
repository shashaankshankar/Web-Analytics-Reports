from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.config import Settings
from app.analytics.contracts import (
    ActionItem,
    AIReportOutput,
    DataDiscovery,
    GrowthAnalysisInput,
    ReportType,
    WeeklyDigestOutput,
)

GROWTH_ANALYST_SYSTEM_PROMPT = """You are an elite Senior Growth Analyst & Technical SEO Director at a premier digital growth agency.
Your job is to transform pre-computed client analytics data into a clear, factual, and actionable performance report or weekly digest.

CORE RULES:
1. All numbers and statistics cited must match the input dataset exactly. Do not invent metrics or metrics deltas.
2. Keep explanations concise, practical, and grounded in the metrics provided.
3. Tailor vocabulary to the client's industry context.
4. Provide clear, realistic recommended next steps based on the data.
5. Return valid JSON matching the requested schema.
"""

def build_weekly_user_prompt(data: GrowthAnalysisInput) -> str:
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
            "days": data.period_days,
        },
        "core_metrics": [m.model_dump() for m in data.core_metrics],
        "top_channels": [c.model_dump() for c in data.top_channels[:4]],
        "top_pages": [p.model_dump() for p in data.top_pages[:4]],
        "striking_distance_keywords": [k.model_dump() for k in data.striking_distance_keywords[:3]],
        "search_movers": [m.model_dump() for m in data.search_movers[:3]],
        "local_seo": data.local_seo.model_dump(),
        "conversion_events": [e.model_dump() for e in data.conversion_events[:4]],
    }
    schema_str = json.dumps(payload, indent=2)
    return f"""Analyze this 7-day weekly growth dataset for {data.company_name} ({data.domain}) and produce a concise executive weekly digest.

Dataset:
{schema_str}

Respond in JSON with the exact following schema:
{{
  "biggest_win": "One punchy sentence highlighting the single strongest positive movement supported by data.",
  "needs_attention": "One sentence explaining a meaningful decline or gap, or null if healthy.",
  "acquisition_insight": "1-2 sentences summarizing top traffic channel performance.",
  "search_opportunity": "1-2 sentences identifying the top striking-distance search query or rank mover, or null if no search data.",
  "local_insight": "1-2 sentences on GBP calls/directions if active, or null if unconfigured.",
  "next_actions": [
    {{
      "title": "Concise weekly action title",
      "description": "Tactical growth task for this week.",
      "impact_area": "SEO / Conversion / Content / Local / Technical",
      "priority": "High / Medium",
      "evidence": "Citing exact metric from data"
    }}
  ],
  "overall_sentiment": "Growth / Moderate / Critical"
}}
"""

def build_performance_user_prompt(data: GrowthAnalysisInput) -> str:
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
            "days": data.period_days,
        },
        "core_metrics": [m.model_dump() for m in data.core_metrics],
        "conversion_rate": data.conversion_rate.model_dump() if data.conversion_rate else None,
        "conversion_events": [e.model_dump() for e in data.conversion_events],
        "top_channels": [c.model_dump() for c in data.top_channels],
        "top_pages": [p.model_dump() for p in data.top_pages],
        "page_gainers": [p.model_dump() for p in data.page_gainers],
        "page_decliners": [p.model_dump() for p in data.page_decliners],
        "striking_distance_keywords": [k.model_dump() for k in data.striking_distance_keywords],
        "search_movers": [m.model_dump() for m in data.search_movers],
        "local_seo": data.local_seo.model_dump(),
        "raw_summary_stats": data.raw_summary_stats,
    }
    schema_str = json.dumps(payload, indent=2)
    return f"""Analyze this normalized 28-day growth dataset for {data.company_name} ({data.domain}) and produce an executive growth briefing.

Dataset:
{schema_str}

Respond in JSON with the exact following schema:
{{
  "executive_summary": [
    "First high-impact takeaway (bold headline + 1 sentence on key win/movement)",
    "Second takeaway (acquisition channel or traffic driver)",
    "Third takeaway (conversion, local intent, or primary opportunity area)"
  ],
  "biggest_win": "One punchy takeaway on the single strongest positive achievement during this period.",
  "watch_item": "One clear observation on a key risk, traffic decline, or conversion optimization gap to monitor.",
  "traffic_and_inflow_insights": "2-3 paragraphs analyzing top performing channels, traffic momentum, high-intent landing page engagement, and customer acquisition flow.",
  "conversion_insights": "1-2 paragraphs detailing total conversions, conversion rate movements, and key lead actions.",
  "seo_and_content_opportunities": "2-3 paragraphs identifying specific striking-distance search queries (positions 8-20), ranking opportunity scores, content gaps, and recommended new landing or service pages.",
  "local_seo_insights": "1-2 paragraphs evaluating Google Business Profile visibility, direction requests, direct calls, review velocity, and local map pack capture.",
  "agency_action_plan": [
    {{
      "title": "Clear action title",
      "description": "Specific tactical action the agency is executing this cycle to capture growth.",
      "impact_area": "SEO / Conversion / Content / Local / Technical",
      "priority": "High / Medium",
      "evidence": "Data point justifying action"
    }}
  ],
  "overall_sentiment": "Growth / Moderate / Critical"
}}
"""

def fallback_weekly_briefing(data: GrowthAnalysisInput) -> WeeklyDigestOutput:
    """Deterministic rule-based fallback for 7-day weekly digest."""
    sessions_metric = next((m for m in data.core_metrics if m.metric_name == "sessions"), None)
    conv_metric = next((m for m in data.core_metrics if m.metric_name == "conversions"), None)
    cr_metric = data.conversion_rate or next((m for m in data.core_metrics if m.metric_name == "conversion_rate"), None)

    sess_pct = f"{sessions_metric.percentage_change:+.1f}%" if (sessions_metric and sessions_metric.percentage_change is not None) else "stable"
    top_channel_name = data.top_channels[0].channel if data.top_channels else "Organic Search"
    top_kw = data.striking_distance_keywords[0].query if data.striking_distance_keywords else None

    if sessions_metric and (sessions_metric.percentage_change or 0) > 0:
        biggest_win = f"Weekly sessions reached {int(sessions_metric.current_value):,} ({sess_pct} vs prior 7 days), led by strong {top_channel_name} traffic."
    elif conv_metric and conv_metric.current_value > 0:
        biggest_win = f"High-intent conversion activity generated {int(conv_metric.current_value)} qualified inquiries this week."
    else:
        biggest_win = f"Maintained steady baseline visibility with {int(sessions_metric.current_value if sessions_metric else 0):,} total site sessions."

    needs_attention = None
    if sessions_metric and (sessions_metric.percentage_change or 0) < -10.0:
        needs_attention = f"Total sessions dipped {sess_pct} compared to the previous 7 days, primarily due to channel fluctuations."
    elif cr_metric and (cr_metric.percentage_points_change or 0) < -0.5:
        needs_attention = f"Conversion rate contracted {cr_metric.percentage_points_change:+.1f} percentage points, warranting mobile CTA review."

    acq_insight = f"{top_channel_name} was the primary acquisition driver, generating the majority of qualified visitor volume."

    search_opp = None
    if top_kw:
        search_opp = f"'{top_kw}' is ranking in striking distance on page 2—targeted on-page refinement can push it to page 1."

    local_insight = None
    if data.local_seo.phone_calls > 0 or data.local_seo.direction_requests > 0:
        local_insight = f"Local profile actions produced {data.local_seo.phone_calls} direct calls and {data.local_seo.direction_requests} directions."

    actions = []
    if top_kw:
        actions.append(
            ActionItem(
                title=f"Optimize On-Page SEO for '{top_kw}'",
                description=f"Refine header hierarchy and body content for '{top_kw}' to capture top-5 rankings.",
                impact_area="SEO",
                priority="High",
                evidence="Striking-distance rank with high impression volume",
            )
        )
    actions.append(
        ActionItem(
            title="Mobile CTA & Form Conversion Polish",
            description="Audit landing page conversion touchpoints to maximize inquiry yield from current weekly traffic.",
            impact_area="Conversion",
            priority="Medium",
            evidence=f"Current conversion rate at {cr_metric.current_value:.1f}%" if cr_metric else "Weekly engagement audit",
        )
    )

    return WeeklyDigestOutput(
        biggest_win=biggest_win,
        needs_attention=needs_attention,
        acquisition_insight=acq_insight,
        search_opportunity=search_opp,
        local_insight=local_insight,
        next_actions=actions[:2],
        overall_sentiment="Growth",
    )

def fallback_growth_briefing(data: GrowthAnalysisInput) -> AIReportOutput:
    """Deterministic rule-based fallback if LLM API is unavailable or unconfigured."""
    sessions_metric = next((m for m in data.core_metrics if m.metric_name == "sessions"), None)
    users_metric = next((m for m in data.core_metrics if m.metric_name == "active_users"), None)
    conversions_metric = next((m for m in data.core_metrics if m.metric_name == "conversions"), None)
    cr_metric = data.conversion_rate or next((m for m in data.core_metrics if m.metric_name == "conversion_rate"), None)

    sess_pct = f"{sessions_metric.percentage_change:+.1f}%" if (sessions_metric and sessions_metric.percentage_change is not None) else "stable"
    users_pct = f"{users_metric.percentage_change:+.1f}%" if (users_metric and users_metric.percentage_change is not None) else "stable"
    conv_val = int(conversions_metric.current_value) if conversions_metric else 0

    top_channel_name = data.top_channels[0].channel if data.top_channels else "Direct"
    top_kw = data.striking_distance_keywords[0].query if data.striking_distance_keywords else "brand terms"

    period_str = f"{data.period_days}-day"
    conv_rate_label = f" ({cr_metric.current_value:.1f}% conversion rate)" if cr_metric else ""
    exec_summary = [
        f"Traffic reached {int(sessions_metric.current_value if sessions_metric else 0):,} sessions ({sess_pct} vs prior period) with {int(users_metric.current_value if users_metric else 0):,} active users.",
        f"{top_channel_name} remains the primary acquisition channel, driving the majority of qualified visitor volume.",
        f"High-intent conversion activity recorded {conv_val:,} key lead/engagement actions{conv_rate_label}.",
    ]

    biggest_win = f"{top_channel_name} generated substantial qualified demand, supporting total site traffic of {int(sessions_metric.current_value if sessions_metric else 0):,} sessions."
    watch_item = "Ensure mobile landing page performance and conversion CTAs remain optimized as search volume expands."

    traffic_insights = (
        f"During the latest {period_str} cycle, {data.company_name} observed {int(sessions_metric.current_value if sessions_metric else 0):,} total sessions. "
        f"The primary traffic driver was {top_channel_name}, demonstrating steady interest in core services. "
        f"Landing page analysis indicates sustained engagement across primary service and contact touchpoints."
    )

    conv_rate_str = f"{cr_metric.current_value:.1f}%" if cr_metric else "steady"
    conversion_insights = (
        f"Total key conversions totaled {conv_val:,} actions, representing an overall conversion rate of {conv_rate_str}. "
        f"Inquiries and lead forms continue to account for the highest proportion of high-value client intent."
    )

    seo_insights = (
        f"Search Console analysis revealed high-potential striking-distance keyword opportunities, led by \"{top_kw}\". "
        f"Targeting queries currently ranking on page 2 (positions 8–20) represents the highest-ROI opportunity to capture immediate organic growth."
    )

    local_insights = ""
    if data.local_seo.phone_calls > 0 or data.local_seo.direction_requests > 0:
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
            evidence=f"Striking distance query '{top_kw}'",
        ),
        ActionItem(
            title="High-Intent Conversion Path Optimization",
            description="Streamline mobile CTA buttons and form microcopy on top landing pages to maximize conversion yield from existing traffic.",
            impact_area="Conversion",
            priority="High",
            evidence=f"Conversion rate optimization for {conv_val} key inquiries",
        ),
        ActionItem(
            title="Local Map Pack Citation & Review Acceleration",
            description="Audit local NAP citations and deploy automated review request cadence to strengthen local authority.",
            impact_area="Local",
            priority="Medium",
            evidence=f"Direct calls ({data.local_seo.phone_calls}) and direction requests ({data.local_seo.direction_requests})",
        ),
    ]

    return AIReportOutput(
        executive_summary=exec_summary,
        biggest_win=biggest_win,
        watch_item=watch_item,
        traffic_and_inflow_insights=traffic_insights,
        conversion_insights=conversion_insights,
        seo_and_content_opportunities=seo_insights,
        local_seo_insights=local_insights,
        agency_action_plan=action_plan,
        deep_discoveries=[],
        overall_sentiment="Growth",
    )

class GrowthAnalyst:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        reasoning_mode: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        settings = Settings.from_env()
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.llm_model
        self.reasoning_effort = reasoning_effort or settings.llm_reasoning_effort
        self.reasoning_mode = reasoning_mode or settings.llm_reasoning_mode
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.http_client = http_client

    def analyze_weekly(self, data: GrowthAnalysisInput) -> WeeklyDigestOutput:
        """Synthesize 7-day metrics into a concise WeeklyDigestOutput."""
        if not self.api_key or self.api_key.strip() == "":
            return fallback_weekly_briefing(data)

        prompt = build_weekly_user_prompt(data)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GROWTH_ANALYST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        endpoint = f"{self.base_url}/chat/completions"
        try:
            if self.http_client:
                response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                return fallback_weekly_briefing(data)

            res_json = response.json()
            content_str = res_json["choices"][0]["message"]["content"]
            clean_str = content_str.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_str)
            return WeeklyDigestOutput(**parsed)
        except Exception:
            return fallback_weekly_briefing(data)

    def analyze(self, data: GrowthAnalysisInput) -> AIReportOutput:
        """Synthesize structured growth metrics into executive AI briefing."""
        if not self.api_key or self.api_key.strip() == "":
            return fallback_growth_briefing(data)

        prompt = build_performance_user_prompt(data)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GROWTH_ANALYST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        endpoint = f"{self.base_url}/chat/completions"
        try:
            if self.http_client:
                response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                return fallback_growth_briefing(data)

            res_json = response.json()
            content_str = res_json["choices"][0]["message"]["content"]
            clean_str = content_str.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_str)
            return AIReportOutput(**parsed)
        except Exception:
            return fallback_growth_briefing(data)

# Backward compatibility alias
build_user_prompt = build_performance_user_prompt
