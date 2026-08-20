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

GROWTH_ANALYST_SYSTEM_PROMPT = """You are a warm, articulate Senior Growth Advisor writing executive performance briefings for small business owners (e.g. dentists, clinic directors, and local practice owners).

YOUR AUDIENCE & TONE:
1. Our clients are busy small business owners. They do not have technical SEO or analytics backgrounds.
2. Write in clear, natural, human-readable plain English. Be encouraging, transparent, and direct.
3. Avoid technical jargon (do NOT say 'slicing', 'dimension clusters', 'raw event counts', 'funnel contraction', 'data state final'). Translate metrics into real-world business outcomes: phone calls, consultation requests, appointment inquiries, and website visitors.
4. CRITICAL FORMATTING RULE: Do NOT use markdown syntax (NO asterisks like **bold**, NO em-dashes like —, NO markdown bullets, NO hashtags). Write natural, clean sentences.
5. Ground every observation in the provided data. Do not invent numbers or metrics.
6. Return ONLY a valid JSON object matching the requested schema.
"""


def clean_plain_text(text: Optional[str]) -> str:
    """Sanitize LLM text to ensure clean, human-readable plain English without markdown artifacts."""
    if not text:
        return ""
    t = text.strip()
    # Convert markdown bold labels like '**Title** — description' or '**Title:** desc' to clean 'Title: desc'
    t = re.sub(r"\*\*(.*?)\*\*\s*[—–\-:]*\s*", r"\1: ", t)
    # Remove remaining markdown asterisks, underscores used for italics, and backticks
    t = t.replace("**", "").replace("*", "").replace("`", "")
    # Replace raw em-dashes with clean commas or dashes
    t = t.replace(" — ", ", ").replace(" —", ", ").replace("— ", ", ").replace("—", ", ")
    # Clean up double colons or excessive spaces
    t = re.sub(r":\s*:", ":", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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
    return f"""Analyze this 7-day weekly growth dataset for {data.company_name} ({data.domain}) and produce a concise, warm, executive weekly digest for the business owner.

Dataset:
{schema_str}

Instructions:
- Write in natural, easy-to-read sentences without technical jargon or markdown formatting (no asterisks, no em-dashes).
- Highlight key wins in patient/customer activity, website visitors, and local search visibility.

Respond in JSON with the exact following schema:
{{
  "biggest_win": "One clear, encouraging sentence highlighting the single best positive achievement this week.",
  "needs_attention": "One constructive sentence on an area to improve or monitor, or null if everything is performing smoothly.",
  "acquisition_insight": "1-2 plain-English sentences summarizing how new and returning visitors found the business this week.",
  "search_opportunity": "1-2 sentences in simple terms about a high-value Google search topic we are close to ranking for, or null if not applicable.",
  "local_insight": "1-2 sentences on local Google Maps activity, phone calls, or directions, or null if unconfigured.",
  "next_actions": [
    {{
      "title": "Clear, professional action title",
      "description": "Simple explanation of what our team is doing this week to help grow the practice.",
      "impact_area": "SEO / Conversion / Content / Local / Technical",
      "priority": "High / Medium",
      "evidence": "Specific metric or observation supporting this step"
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
    return f"""Analyze this 28-day performance dataset for {data.company_name} ({data.domain}) and produce an executive growth briefing for the practice owner.

Dataset:
{schema_str}

Important Guidelines:
- Write in natural, warm, professional English tailored for a busy business owner.
- Do NOT use markdown formatting (no asterisks like **bold**, no em-dashes like —, no raw markdown symbols). Write clean sentences.
- Clearly translate data into business value: patient inquiries, phone calls, website traffic, and Google search rankings.

Respond in JSON with the exact following schema:
{{
  "executive_summary": [
    "First key takeaway in one clear, natural sentence covering overall traffic and audience growth.",
    "Second key takeaway explaining the primary channel bringing visitors to the practice.",
    "Third key takeaway highlighting customer actions, inquiries, or the top upcoming growth opportunity."
  ],
  "biggest_win": "One encouraging sentence celebrating the single biggest positive result achieved during this cycle.",
  "watch_item": "One clear, practical observation on an area we are actively refining or monitoring to maximize results.",
  "traffic_and_inflow_insights": "2 short, conversational paragraphs explaining where visitors came from, how traffic trended, and which service pages were most popular.",
  "conversion_insights": "1-2 short paragraphs detailing inquiries, phone calls, and appointment requests in clear terms.",
  "seo_and_content_opportunities": "2 short paragraphs highlighting valuable Google search terms that potential customers are using and where adding helpful service content will attract more inquiries.",
  "local_seo_insights": "1-2 short paragraphs on Google Maps visibility, local reviews, and direct patient phone calls.",
  "agency_action_plan": [
    {{
      "title": "Clear action title",
      "description": "Specific tactical initiative the agency is delivering this cycle to capture more business.",
      "impact_area": "SEO / Conversion / Content / Local / Technical",
      "priority": "High / Medium",
      "evidence": "Specific metric or observation supporting this priority"
    }}
  ],
  "overall_sentiment": "Growth / Moderate / Critical"
}}
"""


def sanitize_weekly_output(output: WeeklyDigestOutput) -> WeeklyDigestOutput:
    """Clean all text fields in WeeklyDigestOutput of markdown artifacts."""
    output.biggest_win = clean_plain_text(output.biggest_win)
    if output.needs_attention:
        output.needs_attention = clean_plain_text(output.needs_attention)
    output.acquisition_insight = clean_plain_text(output.acquisition_insight)
    if output.search_opportunity:
        output.search_opportunity = clean_plain_text(output.search_opportunity)
    if output.local_insight:
        output.local_insight = clean_plain_text(output.local_insight)
    for act in output.next_actions:
        act.title = clean_plain_text(act.title)
        act.description = clean_plain_text(act.description)
        if act.evidence:
            act.evidence = clean_plain_text(act.evidence)
    return output


def sanitize_report_output(output: AIReportOutput) -> AIReportOutput:
    """Clean all text fields in AIReportOutput of markdown artifacts."""
    output.executive_summary = [clean_plain_text(item) for item in output.executive_summary]
    output.biggest_win = clean_plain_text(output.biggest_win)
    if output.watch_item:
        output.watch_item = clean_plain_text(output.watch_item)
    output.traffic_and_inflow_insights = clean_plain_text(output.traffic_and_inflow_insights)
    output.conversion_insights = clean_plain_text(output.conversion_insights)
    output.seo_and_content_opportunities = clean_plain_text(output.seo_and_content_opportunities)
    output.local_seo_insights = clean_plain_text(output.local_seo_insights)
    for act in output.agency_action_plan:
        act.title = clean_plain_text(act.title)
        act.description = clean_plain_text(act.description)
        if act.evidence:
            act.evidence = clean_plain_text(act.evidence)
    for disc in output.deep_discoveries:
        disc.title = clean_plain_text(disc.title)
        disc.insight = clean_plain_text(disc.insight)
        disc.recommended_action = clean_plain_text(disc.recommended_action)
    return output


def fallback_weekly_briefing(data: GrowthAnalysisInput) -> WeeklyDigestOutput:
    """Deterministic rule-based fallback for 7-day weekly digest."""
    sessions_metric = next((m for m in data.core_metrics if m.metric_name == "sessions"), None)
    conv_metric = next((m for m in data.core_metrics if m.metric_name == "conversions"), None)
    cr_metric = data.conversion_rate or next((m for m in data.core_metrics if m.metric_name == "conversion_rate"), None)

    sess_pct = f"{sessions_metric.percentage_change:+.1f}%" if (sessions_metric and sessions_metric.percentage_change is not None) else "stable"
    top_channel_name = data.top_channels[0].channel if data.top_channels else "Organic Search"
    top_kw = data.striking_distance_keywords[0].query if data.striking_distance_keywords else None

    if sessions_metric and (sessions_metric.percentage_change or 0) > 0:
        biggest_win = f"Weekly sessions reached {int(sessions_metric.current_value):,} ({sess_pct} compared to the prior 7 days), driven by steady {top_channel_name} interest."
    elif conv_metric and conv_metric.current_value > 0:
        biggest_win = f"Patient engagement was strong, generating {int(conv_metric.current_value)} direct inquiries this week."
    else:
        biggest_win = f"Maintained consistent baseline visibility with {int(sessions_metric.current_value if sessions_metric else 0):,} total website visits."

    needs_attention = None
    if sessions_metric and (sessions_metric.percentage_change or 0) < -10.0:
        needs_attention = f"Total visits saw a temporary dip of {sess_pct} compared to the previous week, which we are addressing through channel adjustments."
    elif cr_metric and (cr_metric.percentage_points_change or 0) < -0.5:
        needs_attention = f"The conversion rate shifted {cr_metric.percentage_points_change:+.1f} percentage points, so we are fine-tuning mobile buttons and form ease."

    acq_insight = f"{top_channel_name} was the leading channel bringing prospective patients to the website this week."

    search_opp = None
    if top_kw:
        search_opp = f"The search term '{top_kw}' is currently ranking on page 2 of Google, and a few focused content improvements can help move it to page 1."

    local_insight = None
    if data.local_seo.phone_calls > 0 or data.local_seo.direction_requests > 0:
        local_insight = f"Your Google Business Profile generated {data.local_seo.phone_calls} direct phone calls and {data.local_seo.direction_requests} map direction requests."

    actions = []
    if top_kw:
        actions.append(
            ActionItem(
                title=f"Improve Search Rankings for '{top_kw}'",
                description=f"Enhance service page headers and content for '{top_kw}' to attract more local search traffic.",
                impact_area="SEO",
                priority="High",
                evidence="High search volume on page 2",
            )
        )
    actions.append(
        ActionItem(
            title="Streamline Mobile Booking Touchpoints",
            description="Audit phone and form buttons on key service pages so visitors can easily book consultations from their smartphones.",
            impact_area="Conversion",
            priority="Medium",
            evidence=f"Current conversion rate at {cr_metric.current_value:.1f}%" if cr_metric else "Mobile engagement review",
        )
    )

    out = WeeklyDigestOutput(
        biggest_win=biggest_win,
        needs_attention=needs_attention,
        acquisition_insight=acq_insight,
        search_opportunity=search_opp,
        local_insight=local_insight,
        next_actions=actions[:2],
        overall_sentiment="Growth",
    )
    return sanitize_weekly_output(out)


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
    top_kw = data.striking_distance_keywords[0].query if data.striking_distance_keywords else "brand search"

    period_str = f"{data.period_days}-day"
    conv_rate_label = f" with a {cr_metric.current_value:.1f}% inquiry rate" if cr_metric else ""
    exec_summary = [
        f"Website traffic reached {int(sessions_metric.current_value if sessions_metric else 0):,} visits ({sess_pct} vs prior period) from {int(users_metric.current_value if users_metric else 0):,} active visitors.",
        f"{top_channel_name} was your leading source of visitors, bringing the largest share of prospective clients to the site.",
        f"Visitors completed {conv_val:,} key inquiry and contact actions across appointment forms and phone clicks{conv_rate_label}.",
    ]

    biggest_win = f"{top_channel_name} attracted steady interest, supporting total website traffic of {int(sessions_metric.current_value if sessions_metric else 0):,} visits."
    watch_item = "We are continually refining mobile page speed and contact buttons to ensure every visitor has a smooth experience."

    traffic_insights = (
        f"Over the past {period_str} cycle, {data.company_name} welcomed {int(sessions_metric.current_value if sessions_metric else 0):,} website visits. "
        f"Most visitors arrived through {top_channel_name}, showing healthy and consistent community interest in your services. "
        f"Visitors spent quality time exploring core service pages and contact options across the website."
    )

    conv_rate_str = f"{cr_metric.current_value:.1f}%" if cr_metric else "steady"
    conversion_insights = (
        f"A total of {conv_val:,} inquiries and key actions were recorded this period, representing an overall inquiry rate of {conv_rate_str}. "
        f"Direct phone calls and online consultation forms continue to be the primary ways new patients get in touch."
    )

    seo_insights = (
        f"Google Search Console data highlights great growth potential for valuable searches like '{top_kw}'. "
        f"Because your site already ranks near page 1 for these terms, targeted content updates will help attract even more local searches."
    )

    local_insights = ""
    if data.local_seo.phone_calls > 0 or data.local_seo.direction_requests > 0:
        local_insights = (
            f"Your Google Business Profile drove {data.local_seo.phone_calls} direct phone calls and {data.local_seo.direction_requests} map direction requests. "
            f"Continuing to gather positive patient reviews will keep your practice prominent in local Google Maps searches."
        )

    action_plan = [
        ActionItem(
            title=f"Enhance Search Rankings for '{top_kw}'",
            description=f"Update service page headings and helpful answers for '{top_kw}' to move rankings higher in Google search results.",
            impact_area="SEO",
            priority="High",
            evidence=f"High-potential query '{top_kw}'",
        ),
        ActionItem(
            title="Optimize Mobile Consultation Booking",
            description="Make phone and contact buttons even easier to tap on smartphones to convert more website visitors into scheduled appointments.",
            impact_area="Conversion",
            priority="High",
            evidence=f"Supporting {conv_val} key consultation inquiries",
        ),
    ]
    if data.local_seo.phone_calls > 0 or data.local_seo.direction_requests > 0 or data.local_seo.total_reviews_count is not None:
        action_plan.append(
            ActionItem(
                title="Strengthen Local Google Maps Profile",
                description="Ensure local practice details remain consistent and support regular patient review collection to maintain map prominence.",
                impact_area="Local",
                priority="Medium",
                evidence=f"Verified local profile signals; calls ({data.local_seo.phone_calls}) and directions ({data.local_seo.direction_requests}) where available",
            )
        )

    out = AIReportOutput(
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
    return sanitize_report_output(out)


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
        self.used_fallback = False

    def analyze_weekly(self, data: GrowthAnalysisInput) -> WeeklyDigestOutput:
        """Synthesize 7-day metrics into a concise WeeklyDigestOutput."""
        if not self.api_key or self.api_key.strip() == "":
            self.used_fallback = True
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
                self.used_fallback = True
                return fallback_weekly_briefing(data)

            res_json = response.json()
            content_str = res_json["choices"][0]["message"]["content"]
            clean_str = content_str.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_str)
            raw_output = WeeklyDigestOutput(**parsed)
            return sanitize_weekly_output(raw_output)
        except Exception:
            self.used_fallback = True
            return fallback_weekly_briefing(data)

    def analyze(self, data: GrowthAnalysisInput) -> AIReportOutput:
        """Synthesize structured growth metrics into executive AI briefing."""
        if not self.api_key or self.api_key.strip() == "":
            self.used_fallback = True
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
                self.used_fallback = True
                return fallback_growth_briefing(data)

            res_json = response.json()
            content_str = res_json["choices"][0]["message"]["content"]
            clean_str = content_str.strip()
            if "```json" in clean_str:
                clean_str = clean_str.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_str:
                clean_str = clean_str.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean_str)
            raw_output = AIReportOutput(**parsed)
            return sanitize_report_output(raw_output)
        except Exception:
            self.used_fallback = True
            return fallback_growth_briefing(data)

# Backward compatibility alias
build_user_prompt = build_performance_user_prompt
