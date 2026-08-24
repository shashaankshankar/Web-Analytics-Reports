"""OpenAI-compatible strict structured-output contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def _nullable_string() -> dict[str, Any]:
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _nullable_integer() -> dict[str, Any]:
    return {"anyOf": [{"type": "integer"}, {"type": "null"}]}


ACTION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "impact_area": {"type": "string"},
        "priority": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["title", "description", "impact_area", "priority", "evidence"],
}


WEEKLY_DIGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "biggest_win": {"type": "string"},
        "needs_attention": _nullable_string(),
        "acquisition_insight": {"type": "string"},
        "conversion_insight": {"type": "string"},
        "search_opportunity": _nullable_string(),
        "local_insight": _nullable_string(),
        "next_actions": {"type": "array", "items": ACTION_ITEM_SCHEMA},
        "overall_sentiment": {"type": "string"},
    },
    "required": [
        "biggest_win",
        "needs_attention",
        "acquisition_insight",
        "conversion_insight",
        "search_opportunity",
        "local_insight",
        "next_actions",
        "overall_sentiment",
    ],
}


PERFORMANCE_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "executive_summary": {"type": "array", "items": {"type": "string"}},
        "biggest_win": {"type": "string"},
        "watch_item": _nullable_string(),
        "traffic_and_inflow_insights": {"type": "string"},
        "conversion_insights": {"type": "string"},
        "seo_and_content_opportunities": {"type": "string"},
        "local_seo_insights": {"type": "string"},
        "agency_action_plan": {"type": "array", "items": ACTION_ITEM_SCHEMA},
        "overall_sentiment": {"type": "string"},
    },
    "required": [
        "executive_summary",
        "biggest_win",
        "watch_item",
        "traffic_and_inflow_insights",
        "conversion_insights",
        "seo_and_content_opportunities",
        "local_seo_insights",
        "agency_action_plan",
        "overall_sentiment",
    ],
}


NUMERIC_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_id": {"type": "string"},
        "field": {"type": "string", "enum": ["title", "what_we_noticed", "recommended_next_step"]},
        "claim_text": {"type": "string"},
        "span_start": _nullable_integer(),
        "span_end": _nullable_integer(),
        "value": {"type": "number"},
        "metric_name": {"type": "string"},
        "unit": {"type": "string"},
        "operation": {
            "type": "string",
            "enum": ["value", "change", "percent_change", "ratio", "share"],
        },
        "evidence_id": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "string"}},
        "period": {"type": "string", "enum": ["current", "prior", "both"]},
        "formula": _nullable_string(),
    },
    "required": [
        "claim_id",
        "field",
        "claim_text",
        "span_start",
        "span_end",
        "value",
        "metric_name",
        "unit",
        "operation",
        "evidence_id",
        "fact_ids",
        "period",
        "formula",
    ],
}


EXPLORATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "discoveries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposal_id": {"type": "string"},
                    "candidate_id": {"type": "string"},
                    "title": {"type": "string"},
                    "what_we_noticed": {"type": "string"},
                    "recommended_next_step": {"type": "string"},
                    "numeric_claims": {"type": "array", "items": NUMERIC_CLAIM_SCHEMA},
                    "rank": _nullable_integer(),
                },
                "required": [
                    "proposal_id",
                    "candidate_id",
                    "title",
                    "what_we_noticed",
                    "recommended_next_step",
                    "numeric_claims",
                    "rank",
                ],
            },
        }
    },
    "required": ["discoveries"],
}


VERIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposal_id": {"type": "string"},
                    "candidate_id": {"type": "string"},
                    "approved": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["proposal_id", "candidate_id", "approved", "reason"],
            },
        }
    },
    "required": ["decisions"],
}


def response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Build the OpenAI Chat Completions strict JSON-schema response format."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": deepcopy(schema),
        },
    }


def parse_response_json(body: dict[str, Any], context: str) -> dict[str, Any]:
    """Extract a strict structured response and fail with a bounded provider error."""

    try:
        message = body["choices"][0]["message"]
        if message.get("refusal"):
            raise ValueError(f"{context} was refused by the model.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{context} did not contain structured content.")
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{context} did not return valid strict structured output.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{context} did not return a JSON object.")
    return parsed
