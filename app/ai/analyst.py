from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.config import Settings
from app.ai.privacy import sanitize_for_ai
from app.ai.structured_output import (
    PERFORMANCE_REPORT_SCHEMA,
    WEEKLY_DIGEST_SCHEMA,
    parse_response_json,
    response_format,
)
from app.analytics.contracts import (
    AIReportOutput,
    DataDiscovery,
    GrowthAnalysisInput,
    ReportMode,
    ReportType,
    WeeklyDigestOutput,
)

GROWTH_ANALYST_SYSTEM_PROMPT = """You are a warm, articulate Senior Growth Advisor writing executive performance briefings for small business owners (e.g. dentists, clinic directors, and local practice owners).

YOUR AUDIENCE & TONE:
1. Our clients are busy small business owners. They do not have technical SEO or analytics backgrounds.
2. Write in clear, natural, human-readable plain English. Be encouraging, transparent, and direct.
3. Avoid technical jargon (do NOT say 'slicing', 'dimension clusters', 'raw event counts', 'funnel contraction', 'data state final'). Translate metrics into real-world business outcomes: phone calls, consultation requests, appointment inquiries, and website visitors.
4. CRITICAL FORMATTING RULE: Do NOT use markdown syntax (NO asterisks like **bold**, NO em-dashes like —, NO markdown bullets, NO hashtags). Write natural, clean sentences.
5. CRITICAL CLIENT-FACING RULE: NEVER output raw technical variable names, database keys, or code identifiers with underscores (e.g., do NOT write 'contact_form_submit', 'phone_click', 'active_users', 'generate_lead', 'CALL_CLICKS', 'primary_leads', 'screen_page_views', 'engagement_rate', etc.). Business clients cannot understand raw code identifiers. The purpose of this analysis is to transform raw telemetry into clear, intuitive insights and actions grounded in the client's business, practice, and goals. Always translate technical event and metric keys into natural, client-friendly plain English (e.g., 'contact form submissions', 'phone calls', 'unique visitors', 'inquiry leads', 'phone calls').
6. Ground every observation in the provided data. Do not invent numbers or metrics.
7. If a source status is empty, unavailable, or error, say that the source is unavailable and do not infer a result from it.
8. If report_mode is initial_baseline, use current observation data only. Do not state or imply a prior value, comparison, trend, movement, increase, decrease, or growth change.
9. Treat ratings, review totals, and review excerpts as observed profile context. Do not turn them into claims of reputation strength, patient satisfaction, social proof, or future attraction unless the supplied data directly supports that wording.
10. With small samples, prefer neutral descriptions of the observed actions and qualify uncertainty instead of calling activity strong, encouraging, meaningful, or representative.
11. Return ONLY a valid JSON object matching the requested schema.
"""


_BASELINE_MOVEMENT_PATTERN = re.compile(
    r"\b(?:compared|versus|vs\.?|period[- ]over[- ]period|week[- ]over[- ]week|"
    r"month[- ]over[- ]month|increased|decreased|declined|rose|fell|grew|dropped|spiked|"
    r"higher|lower|trend|trending|gained|lost|"
    r"(?:change|changes|changed)\s+(?:in|from|to|by)\b)",
    re.IGNORECASE,
)

_TECHNICAL_TERM_MAPPINGS: dict[str, str] = {
    # GA4 conversion & lead events
    "contact_form_submits": "contact form submissions",
    "contact_form_submit": "contact form submission",
    "contact_form_submissions": "contact form submissions",
    "contact_form_submission": "contact form submission",
    "contact_forms": "contact forms",
    "contact_form": "contact form",
    "phone_call_clicks": "phone call button clicks",
    "phone_call_click": "phone call button click",
    "phone_clicks": "phone call clicks",
    "phone_click": "phone call click",
    "phone_button_clicks": "phone call button clicks",
    "phone_button_click": "phone call button click",
    "call_clicks": "phone calls",
    "call_click": "phone call",
    "generate_leads": "inquiry leads",
    "generate_lead": "inquiry lead",
    "lead_submissions": "lead submissions",
    "lead_submission": "lead submission",
    "inquiry_submits": "inquiry submissions",
    "inquiry_submit": "inquiry submission",
    "inquiry_submissions": "inquiry submissions",
    "inquiry_submission": "inquiry submission",
    "appointment_requests": "appointment requests",
    "appointment_request": "appointment request",
    "book_appointments": "appointment bookings",
    "book_appointment": "appointment booking",
    "booking_submits": "appointment bookings",
    "booking_submit": "appointment booking",
    "schedule_consultations": "consultation requests",
    "schedule_consultation": "consultation request",
    "consultation_requests": "consultation requests",
    "consultation_request": "consultation request",
    "email_clicks": "email clicks",
    "email_click": "email click",
    "directions_clicks": "direction clicks",
    "directions_click": "direction click",
    "direction_requests": "direction requests",
    "direction_request": "direction request",
    "direction_actions": "direction requests",
    "direction_action": "direction request",
    "form_submits": "form submissions",
    "form_submit": "form submission",
    "form_starts": "form starts",
    "form_start": "form start",
    "form_steps": "form steps",
    "form_step": "form step",
    "form_step_1": "form step 1",
    "form_step_2": "form step 2",
    "form_step_3": "form step 3",
    "form_step1": "form step 1",
    "form_step2": "form step 2",
    "form_step3": "form step 3",
    "cta_clicks": "call to action clicks",
    "cta_click": "call to action click",
    "file_downloads": "file downloads",
    "file_download": "file download",
    "first_visits": "first visits",
    "first_visit": "first visit",
    "session_starts": "website visits",
    "session_start": "website visit",
    "page_views": "page views",
    "page_view": "page view",
    "screen_page_views": "page views",
    "screen_page_view": "page view",
    "user_engagement": "visitor engagement",
    "video_starts": "video plays",
    "video_start": "video play",

    # GA4 & Traffic Metrics
    "active_users": "unique visitors",
    "active_user": "unique visitor",
    "total_users": "total visitors",
    "total_user": "total visitor",
    "new_users": "new visitors",
    "new_user": "new visitor",
    "engagement_rate": "engagement rate",
    "bounce_rate": "bounce rate",
    "average_session_duration": "average session duration",
    "event_count": "event count",
    "key_conversions": "key conversions",
    "key_conversion": "key conversion",
    "primary_leads": "primary leads",
    "primary_lead": "primary lead",
    "prior_sessions": "prior sessions",

    # Google Business Profile / Local Metrics
    "website_clicks": "website visits",
    "website_click": "website visit",
    "booking_clicks": "booking clicks",
    "booking_click": "booking click",
    "messaging_clicks": "messages",
    "messaging_click": "message",
    "food_orders": "online orders",
    "food_order": "online order",
    "food_order_clicks": "online orders",
    "business_impressions_desktop_maps": "Google Maps desktop views",
    "business_impressions_desktop_search": "Google Search desktop views",
    "business_impressions_mobile_maps": "Google Maps mobile views",
    "business_impressions_mobile_search": "Google Search mobile views",
    "business_direction_requests": "direction requests",
    "business_conversations": "conversations",
    "business_bookings": "bookings",
    "business_food_orders": "food orders",
    "business_food_menu_clicks": "menu clicks",
    "gbp_call_clicks": "Google Business Profile phone calls",
    "gbp_direction_requests": "Google Business Profile direction requests",
    "gbp_website_clicks": "Google Business Profile website visits",
}

_CAMEL_CASE_REPLACEMENTS: dict[str, str] = {
    "activeUsers": "unique visitors",
    "totalUsers": "total visitors",
    "newUsers": "new visitors",
    "engagementRate": "engagement rate",
    "screenPageViews": "page views",
    "eventCount": "event count",
    "keyConversions": "key conversions",
    "primaryLeads": "primary leads",
    "priorSessions": "prior sessions",
    "bounceRate": "bounce rate",
}

_URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"\x27\)]+?(?=[.,;:!?]?(\s|$|[<>\"\x27\)]))")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9._-]+\.[A-Za-z]{2,}\b")
_SNAKE_CASE_PATTERN = re.compile(r"\b_?[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+_?\b")


def humanize_technical_tokens(text: str) -> str:
    """Replace raw variable names, database keys, and snake_case tokens with client-friendly English.

    Preserves URLs and email addresses that legitimately contain underscores.
    """
    if not text:
        return ""

    urls: list[str] = []

    def save_url(m: re.Match[str]) -> str:
        urls.append(m.group(0))
        return f"\x02URLPROTECTED{len(urls)-1}\x03"

    emails: list[str] = []

    def save_email(m: re.Match[str]) -> str:
        emails.append(m.group(0))
        return f"\x02EMAILPROTECTED{len(emails)-1}\x03"

    t = _URL_PATTERN.sub(save_url, text)
    t = _EMAIL_PATTERN.sub(save_email, t)

    for camel_key, replacement in _CAMEL_CASE_REPLACEMENTS.items():
        t = re.sub(rf"\b{re.escape(camel_key)}\b", replacement, t)

    def replace_token(m: re.Match[str]) -> str:
        tok = m.group(0)
        clean_tok = tok.strip("_")
        if not clean_tok:
            return " "
        lower_tok = clean_tok.lower()
        if lower_tok in _TECHNICAL_TERM_MAPPINGS:
            repl = _TECHNICAL_TERM_MAPPINGS[lower_tok]
        elif clean_tok.isupper():
            repl = clean_tok.lower().replace("_", " ")
        else:
            repl = clean_tok.replace("_", " ")

        prefix = m.string[:m.start()]
        is_sentence_start = bool(re.search(r"(?:^|[.!?]\s+)$", prefix))
        if is_sentence_start or (clean_tok[0].isupper() and not clean_tok.isupper()):
            repl = repl[:1].upper() + repl[1:]
        return repl

    t = _SNAKE_CASE_PATTERN.sub(replace_token, t)

    for i, email in enumerate(emails):
        t = t.replace(f"\x02EMAILPROTECTED{i}\x03", email)
    for i, url in enumerate(urls):
        t = t.replace(f"\x02URLPROTECTED{i}\x03", url)

    return t


class AnalysisUnavailableError(RuntimeError):
    """Raised when a real OpenRouter synthesis cannot be completed."""


def clean_plain_text(text: Optional[str]) -> str:
    """Sanitize LLM text to ensure clean, human-readable plain English without markdown artifacts or raw variable names."""
    if not text:
        return ""
    t = text.strip()
    # Convert markdown bold labels like '**Title** — description' or '**Title:** desc' to clean 'Title: desc'
    t = re.sub(r"\*\*(.*?)\*\*\s*[—–\-:]*\s*", r"\1: ", t)
    # Remove remaining markdown asterisks and backticks
    t = t.replace("**", "").replace("*", "").replace("`", "")
    # Remove markdown italics underscores around single words
    t = re.sub(r"(^|\s)_([A-Za-z0-9]+)_($|[\s.,;:!?])", r"\1\2\3", t)
    # Replace raw em-dashes with clean commas or dashes
    t = t.replace(" — ", ", ").replace(" —", ", ").replace("— ", ", ").replace("—", ", ")
    # Humanize raw variable names, database keys, and snake_case tokens
    t = humanize_technical_tokens(t)
    # Clean up double colons or excessive spaces
    t = re.sub(r":\s*:", ":", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _local_seo_prompt_payload(data: GrowthAnalysisInput) -> dict[str, Any]:
    """Keep the report prompt useful while retaining the complete source contract."""
    local = data.local_seo.model_dump()
    reviews = list(local.get("reviews") or [])
    local["reviews_total_in_contract"] = len(reviews)
    local["reviews_truncated_for_prompt"] = len(reviews) > 10
    local["reviews"] = reviews[:10]
    keywords = list(local.get("monthly_search_keywords") or [])
    local["monthly_search_keywords_total_in_contract"] = len(keywords)
    local["monthly_search_keywords"] = keywords[:50]
    prior_keywords = list(local.get("prior_monthly_search_keywords") or [])
    local["prior_monthly_search_keywords"] = prior_keywords[:50]
    return local


def build_weekly_user_prompt(data: GrowthAnalysisInput) -> str:
    payload = {
        "client_profile": {
            "company_name": data.company_name,
            "domain": data.domain,
            "industry": data.industry,
            "goals": data.goals,
        },
        "period": {
            "mode": data.report_mode.value,
            "current": f"{data.period_start} to {data.period_end}",
            "prior": (
                f"{data.comparison_start} to {data.comparison_end}"
                if data.report_mode == ReportMode.COMPARISON
                else None
            ),
            "days": data.period_days,
            "observed_days": data.observed_days,
            "measurement_start_date": data.measurement_start_date,
            "comparison_suppressed": data.comparison_suppressed,
            "comparison_suppression_reason": data.comparison_suppression_reason,
        },
        "core_metrics": [m.model_dump() for m in data.core_metrics],
        "top_channels": [c.model_dump() for c in data.top_channels[:4]],
        "top_pages": [p.model_dump() for p in data.top_pages[:4]],
        "striking_distance_keywords": [k.model_dump() for k in data.striking_distance_keywords[:3]],
        "search_movers": [m.model_dump() for m in data.search_movers[:3]],
        "local_seo": _local_seo_prompt_payload(data),
        "conversion_events": [e.model_dump() for e in data.conversion_events[:8]],
    }
    schema_str = json.dumps(sanitize_for_ai(payload), indent=2, ensure_ascii=False)
    return f"""Analyze this 7-day weekly growth dataset for {data.company_name} ({data.domain}) and produce a concise, warm, executive weekly digest for the business owner.

Dataset:
{schema_str}

Instructions:
- Write in natural, easy-to-read sentences without technical jargon or markdown formatting (no asterisks, no em-dashes).
- Do NOT output raw technical variable names, database keys, or tokens containing underscores (e.g., contact_form_submit, phone_click, active_users, generate_lead, CALL_CLICKS). Always translate raw telemetry, metric keys, and event names into clear, natural, client-friendly plain English (e.g., 'contact form submissions', 'phone calls', 'unique visitors', 'inquiry leads') in the context of the client, their business, and their goals.
- Highlight key wins in patient/customer activity, website visitors, and local search visibility.
- Treat Key Conversions as the subset of events currently configured as conversions in GA4. Recorded customer actions can be nonzero even when Key Conversions is zero; never describe zero key conversions as zero customer activity.
- Write conversion_insight as 1-2 plain-English sentences that name the recorded customer/contact actions and their counts when present, and explain the difference between recorded actions and configured key conversions when that distinction matters.
- Engagement rate is the share of sessions GA4 classifies as engaged. With a small sample or an initial baseline, call it an early signal rather than proof of meaningful interest or a trend.
- With a small initial sample, describe observed actions neutrally rather than calling them strong, encouraging, or representative. Treat ratings and review totals as profile context, not proof of reputation, satisfaction, or future patient growth.
- Treat the supplied GBP profile as observed NAP, hours, category, and service-listing data. Do not claim the listing is accurate, complete, or currently live unless the profile status says available.
- Use only supplied GBP Performance totals and daily series. Distinguish Maps/Search impressions, calls, directions, website clicks, bookings, food actions, and conversations; never turn an impression or click into a confirmed patient or appointment outcome.
- Monthly GBP search-keyword rows can contain an exact value or a privacy threshold. Never restate a threshold as an exact count. Managed review reply status is an operational observation, not proof of patient satisfaction.
- If striking_distance_keywords is empty, set search_opportunity to null. Do not invent a confirmed search opportunity or treat suggested topics as validated demand.
- This is a weekly monitoring digest. Do not provide recommended actions or next steps here; the monthly performance report contains the action plan.

Respond in JSON with the exact following schema:
{{
  "biggest_win": "One clear, encouraging sentence highlighting the single best positive achievement this week.",
  "needs_attention": "One constructive sentence on an area to improve or monitor, or null if everything is performing smoothly.",
  "acquisition_insight": "1-2 plain-English sentences summarizing how new and returning visitors found the business this week.",
  "conversion_insight": "1-2 plain-English sentences naming recorded customer/contact actions and their counts, while distinguishing those events from configured Key Conversions when needed.",
  "search_opportunity": "1-2 sentences in simple terms about a high-value Google search topic we are close to ranking for, or null if not applicable.",
  "local_insight": "1-2 sentences on local Google Maps activity, phone calls, or directions, or null if unconfigured.",
  "overall_sentiment": "Growth / Moderate / Critical"
}}
"""


def build_performance_user_prompt(data: GrowthAnalysisInput) -> str:
    payload = {
        "client_profile": {
            "company_name": data.company_name,
            "domain": data.domain,
            "industry": data.industry,
            "goals": data.goals,
        },
        "period": {
            "mode": data.report_mode.value,
            "current": f"{data.period_start} to {data.period_end}",
            "prior": (
                f"{data.comparison_start} to {data.comparison_end}"
                if data.report_mode == ReportMode.COMPARISON
                else None
            ),
            "days": data.period_days,
            "observed_days": data.observed_days,
            "measurement_start_date": data.measurement_start_date,
            "comparison_suppressed": data.comparison_suppressed,
            "comparison_suppression_reason": data.comparison_suppression_reason,
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
        "local_seo": _local_seo_prompt_payload(data),
        "raw_summary_stats": data.raw_summary_stats,
    }
    schema_str = json.dumps(sanitize_for_ai(payload), indent=2, ensure_ascii=False)
    dataset_name = (
        "initial measurement baseline dataset"
        if data.report_mode == ReportMode.INITIAL_BASELINE
        else "28-day performance dataset"
    )
    baseline_guidance = (
        "- This is an initial measurement baseline. Describe only the observed current window. Do not mention prior periods or make movement, trend, increase, decrease, or growth-change claims. Do not use comparison language such as trend, higher, lower, compared, versus, or movement.\n"
        if data.report_mode == ReportMode.INITIAL_BASELINE
        else ""
    )
    traffic_detail = (
        "2 short, conversational paragraphs explaining where visitors came from and which service pages were most popular."
        if data.report_mode == ReportMode.INITIAL_BASELINE
        else "2 short, conversational paragraphs explaining where visitors came from, how traffic trended, and which service pages were most popular."
    )
    seo_detail = (
        "2 short paragraphs explaining the exact supplied Google search terms, their current visibility, and the helpful content or page improvement that follows from those terms. Do not call a term high opportunity unless it appears in the supplied striking_distance_keywords list."
        if data.striking_distance_keywords
        else "1-2 short paragraphs stating that no confirmed ranking opportunity was supplied in the current Search Console snapshot. If possible content topics are mentioned, label them as hypotheses to validate rather than terms customers are already using."
    )
    return f"""Analyze this {dataset_name} for {data.company_name} ({data.domain}) and produce an executive growth briefing for the practice owner.

Dataset:
{schema_str}

Important Guidelines:
- Write in natural, warm, professional English tailored for a busy business owner.
- Do NOT use markdown formatting (no asterisks like **bold**, no em-dashes like —, no raw markdown symbols). Write clean sentences.
- Do NOT output raw technical variable names, database keys, or tokens containing underscores (e.g., contact_form_submit, phone_click, active_users, generate_lead, CALL_CLICKS). Always translate raw telemetry, metric keys, and event names into clear, natural, client-friendly business English (e.g., 'contact form submissions', 'phone calls', 'website visitors', 'inquiry leads') contextualized to the client's business, practice, and goals.
- Clearly translate data into business value: patient inquiries, phone calls, website traffic, and Google search rankings.
- Key Conversions are the subset of GA4 events currently marked as conversions. The conversion_events list contains recorded events and may be nonzero even when Key Conversions is zero. Do not call every recorded event a completed appointment, call, sale, or revenue outcome.
- Explain Engagement Rate in plain English as the share of sessions GA4 classifies as engaged. With a small sample or initial baseline, describe it as an early signal and avoid claiming it proves meaningful interest.
- Treat ratings, review totals, and review excerpts as observed profile context. Do not call them strong reputation or social proof, claim patient satisfaction, or imply they will attract inquiries unless the supplied data directly supports that wording.
- Treat the supplied GBP profile as observed NAP, hours, category, and service-listing data. Do not claim the listing is accurate, complete, or currently live unless the profile status says available.
- Use only supplied GBP Performance totals and daily series. Distinguish Maps/Search impressions, calls, directions, website clicks, bookings, food actions, and conversations; never turn an impression or click into a confirmed patient or appointment outcome.
- Monthly GBP search-keyword rows can contain an exact value or a privacy threshold. Never restate a threshold as an exact count. Managed review reply status is an operational observation, not proof of patient satisfaction.
- With a small initial sample, use neutral language for observed actions rather than calling activity strong, encouraging, meaningful, or representative.
- Only describe a search term as a confirmed opportunity when it appears in striking_distance_keywords. If that list is empty, say that no confirmed ranking opportunity was supplied and frame any possible content topics as hypotheses to validate, not as terms customers are already using.
- Keep recommendations evidence-backed and bounded. Do not imply that a content or tracking change will guarantee more inquiries.
- Return no more than three recommended actions, ordered from strongest to weakest by expected business impact and supporting evidence. Mark the strongest actions as High priority.
{baseline_guidance}- If a source is unavailable, state that limitation plainly and do not infer a value.

Respond in JSON with the exact following schema:
{{
  "executive_summary": [
    "First key takeaway in one clear, natural sentence covering overall traffic and audience growth.",
    "Second key takeaway explaining the primary channel bringing visitors to the practice.",
    "Third key takeaway highlighting customer actions, inquiries, or the top upcoming growth opportunity."
  ],
  "biggest_win": "One encouraging sentence celebrating the single biggest positive result achieved during this cycle.",
  "watch_item": "One clear, practical observation on an area we are actively refining or monitoring to maximize results.",
  "traffic_and_inflow_insights": "{traffic_detail}",
  "conversion_insights": "1-2 short paragraphs detailing inquiries, phone calls, and appointment requests in clear terms.",
  "seo_and_content_opportunities": "{seo_detail}",
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
    output.conversion_insight = clean_plain_text(output.conversion_insight)
    if output.search_opportunity:
        output.search_opportunity = clean_plain_text(output.search_opportunity)
    if output.local_insight:
        output.local_insight = clean_plain_text(output.local_insight)
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
    # Deep-insight cards are authored and normalized by the exploratory agent,
    # then independently verified. Do not rewrite their model-authored fields
    # in the general report sanitizer.
    return output


def validate_baseline_report_output(output: AIReportOutput) -> AIReportOutput:
    """Fail closed if the general analyst reintroduces comparison language in a baseline."""
    fields = [
        *output.executive_summary,
        output.biggest_win,
        output.watch_item or "",
        output.traffic_and_inflow_insights,
        output.conversion_insights,
        output.seo_and_content_opportunities,
        output.local_seo_insights,
        *[item.title for item in output.agency_action_plan],
        *[item.description for item in output.agency_action_plan],
        *[item.evidence for item in output.agency_action_plan],
    ]
    matched_terms = sorted(
        {
            match.group(0).lower()
            for text in fields
            for match in _BASELINE_MOVEMENT_PATTERN.finditer(text or "")
        }
    )
    if matched_terms:
        raise AnalysisUnavailableError(
            "OpenRouter baseline synthesis contained comparison or movement language: "
            + ", ".join(matched_terms)
        )
    return output


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
            raise AnalysisUnavailableError("OpenRouter credentials are unavailable.")

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
            "response_format": response_format("weekly_digest", WEEKLY_DIGEST_SCHEMA),
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        payload = sanitize_for_ai(payload)

        endpoint = f"{self.base_url}/chat/completions"
        try:
            if self.http_client:
                response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                raise AnalysisUnavailableError(f"OpenRouter returned HTTP {response.status_code}.")

            parsed = parse_response_json(response.json(), "OpenRouter weekly synthesis")
            raw_output = WeeklyDigestOutput(**parsed)
            return sanitize_weekly_output(raw_output)
        except AnalysisUnavailableError:
            raise
        except Exception as exc:
            raise AnalysisUnavailableError(
                f"OpenRouter weekly synthesis failed: {type(exc).__name__}."
            ) from exc

    def analyze(self, data: GrowthAnalysisInput) -> AIReportOutput:
        """Synthesize structured growth metrics into executive AI briefing."""
        if not self.api_key or self.api_key.strip() == "":
            raise AnalysisUnavailableError("OpenRouter credentials are unavailable.")

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
            "temperature": 0 if data.report_mode == ReportMode.INITIAL_BASELINE else 0.3,
            "response_format": response_format("performance_report", PERFORMANCE_REPORT_SCHEMA),
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        payload = sanitize_for_ai(payload)

        endpoint = f"{self.base_url}/chat/completions"
        try:
            if self.http_client:
                response = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
            else:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                raise AnalysisUnavailableError(f"OpenRouter returned HTTP {response.status_code}.")

            parsed = parse_response_json(response.json(), "OpenRouter performance synthesis")
            raw_output = AIReportOutput(**parsed)
            sanitized = sanitize_report_output(raw_output)
            return (
                validate_baseline_report_output(sanitized)
                if data.report_mode == ReportMode.INITIAL_BASELINE
                else sanitized
            )
        except AnalysisUnavailableError:
            raise
        except Exception as exc:
            raise AnalysisUnavailableError(
                f"OpenRouter performance synthesis failed: {type(exc).__name__}."
            ) from exc

# Backward compatibility alias
build_user_prompt = build_performance_user_prompt
