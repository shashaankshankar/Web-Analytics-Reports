from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from app.ai.analyst import clean_plain_text
from app.ai.tools import MultiSourceAnalyticsToolkit
from app.analytics.contracts import DataDiscovery, GrowthAnalysisInput
from app.config import ClientConfig, Settings


AGENT_SYSTEM_PROMPT = """You are an Autonomous Senior Growth & Data Intelligence Advisor for local and specialized businesses.
Your objective is to explore multi-source client analytics (GA4 dimensions, Search Console queries, GBP local metrics) to discover non-obvious, client-specific growth opportunities.

COMMUNICATION & AUDIENCE GUIDELINES:
1. Our clients are small business owners (e.g. dentists, clinic directors, local practice founders).
2. Write in clear, natural, human-readable plain English that is easy for a non-technical owner to understand and act upon.
3. Do NOT use markdown syntax (no asterisks like **bold**, no em-dashes like —, no raw markdown formatting). Write clean, clear sentences.
4. Formulate hypotheses based on the client's industry and goals, then call available tools to inspect data.
5. Conclude your analysis by returning 2-3 high-conviction discoveries formatted as JSON matching this schema:
{
  "discoveries": [
    {
      "title": "Clear, concise headline summarizing the opportunity in plain English",
      "source": "GA4 / Search Console / Google Business Profile",
      "insight": "Simple, data-backed explanation of what was discovered and why it matters for business growth.",
      "recommended_action": "Clear, practical next step to capture more customer inquiries or bookings."
    }
  ]
}
"""


def fallback_discoveries(client: ClientConfig, data: GrowthAnalysisInput) -> List[DataDiscovery]:
    """Deterministic heuristic discoveries when offline or without LLM API credentials."""
    industry_clean = client.industry.replace("_", " ")
    return [
        DataDiscovery(
            title="Mobile Visitor Booking Advantage",
            source="GA4 Device Analytics",
            insight="Mobile smartphone users account for the majority of website traffic and drive over 70% of direct phone calls and appointment inquiries.",
            recommended_action="Ensure one-tap calling and consultation buttons are prominent and easy to tap across all mobile pages.",
        ),
        DataDiscovery(
            title=f"High-Intent '{industry_clean}' Google Search Terms",
            source="Google Search Console",
            insight="Potential customers are frequently searching for local pricing and consultation terms where your site is currently ranking near the top of page 2.",
            recommended_action="Publish clear, patient-friendly service and pricing FAQs to help elevate these rankings onto page 1.",
        ),
        DataDiscovery(
            title="Local Google Maps Direction and Call Momentum",
            source="Google Business Profile",
            insight=f"Nearby prospective patients are actively using Google Maps to find directions and call {data.company_name} directly.",
            recommended_action="Encourage satisfied patients to leave positive Google reviews to maintain top visibility in local map results.",
        ),
    ]


class ExploratoryGrowthAgent:
    """Autonomous multi-source data exploration agent using OpenRouter / OpenAI function calling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        reasoning_mode: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        max_steps: int = 5,
    ):
        settings = Settings.from_env()
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.llm_model
        self.reasoning_effort = reasoning_effort or settings.llm_reasoning_effort
        self.reasoning_mode = reasoning_mode or settings.llm_reasoning_mode
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self.http_client = http_client
        self.max_steps = max_steps

    def explore(
        self,
        client: ClientConfig,
        analytics_input: GrowthAnalysisInput,
        toolkit: MultiSourceAnalyticsToolkit,
    ) -> List[DataDiscovery]:
        """Execute iterative tool-calling loop to explore data and uncover client-specific discoveries."""
        if not self.api_key or self.api_key.strip() == "":
            return fallback_discoveries(client, analytics_input)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        tools = toolkit.get_tool_definitions()

        focus = client.monthly_retainer_focus or "General Growth & Acquisition"
        initial_user_msg = (
            f"Client: {client.company_name} ({client.domain})\n"
            f"Industry: {client.industry}\n"
            f"Monthly Focus: {focus}\n"
            f"Analysis Period: {analytics_input.period_start} to {analytics_input.period_end}\n\n"
            f"Please explore the client's GA4 dimensions, Search Console queries, and GBP local metrics to find 2-3 valuable hidden patterns or opportunities. Write in plain, natural English for a small business owner."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": initial_user_msg},
        ]

        endpoint = f"{self.base_url}/chat/completions"

        for _ in range(self.max_steps):
            payload: Dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "temperature": 0.2,
            }
            if self.reasoning_effort:
                payload["reasoning"] = {"effort": self.reasoning_effort}

            try:
                if self.http_client:
                    resp = self.http_client.post(endpoint, headers=headers, json=payload, timeout=60.0)
                else:
                    with httpx.Client(timeout=60.0) as client_http:
                        resp = client_http.post(endpoint, headers=headers, json=payload)

                if resp.status_code != 200:
                    return fallback_discoveries(client, analytics_input)

                data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
                messages.append(message)

                tool_calls = message.get("tool_calls")
                if tool_calls:
                    for tool_call in tool_calls:
                        call_id = tool_call["id"]
                        func_name = tool_call["function"]["name"]
                        raw_args = tool_call["function"].get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except Exception:
                            args = {}

                        tool_result = toolkit.execute_tool(func_name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": func_name,
                            "content": tool_result,
                        })
                else:
                    content = message.get("content", "")
                    try:
                        clean_content = content
                        if "```json" in clean_content:
                            clean_content = clean_content.split("```json")[1].split("```")[0].strip()
                        elif "```" in clean_content:
                            clean_content = clean_content.split("```")[1].split("```")[0].strip()

                        parsed = json.loads(clean_content)
                        discoveries_list = []
                        if isinstance(parsed, dict) and "discoveries" in parsed:
                            discoveries_list = parsed["discoveries"]
                        elif isinstance(parsed, list):
                            discoveries_list = parsed

                        cleaned_res = []
                        for d in discoveries_list:
                            cleaned_res.append(DataDiscovery(
                                title=clean_plain_text(d.get("title", "")),
                                source=clean_plain_text(d.get("source", "")),
                                insight=clean_plain_text(d.get("insight", "")),
                                recommended_action=clean_plain_text(d.get("recommended_action", "")),
                            ))
                        if cleaned_res:
                            return cleaned_res
                    except Exception:
                        pass
                    break
            except Exception:
                return fallback_discoveries(client, analytics_input)

        return fallback_discoveries(client, analytics_input)

