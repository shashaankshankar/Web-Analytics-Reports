from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel

from app.analytics.contracts import (
    EvidenceBundle,
    EvidenceFact,
    EvidencePeriod,
    EvidenceRecord,
    ExplorationCandidate,
    ReportMode,
    SourceAvailability,
)
from app.config import ClientConfig
from app.sources.ga4 import GA4Extractor
from app.sources.gbp import GoogleBusinessProfileExtractor
from app.sources.gsc import SearchConsoleExtractor, filter_search_rows


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return text[:60] or "value"


def _numeric(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _status(value: Any, rows: Sequence[Any] | None = None) -> SourceAvailability:
    if isinstance(value, SourceAvailability):
        return value
    try:
        return SourceAvailability(str(value))
    except ValueError:
        return SourceAvailability.AVAILABLE if rows else SourceAvailability.EMPTY


class MultiSourceAnalyticsToolkit:
    """Read-only exploratory tools that retain every response as evidence."""

    def __init__(
        self,
        client: ClientConfig,
        start_date: str,
        end_date: str,
        prior_start_date: str,
        prior_end_date: str,
        report_mode: ReportMode = ReportMode.COMPARISON,
        measurement_start_date: Optional[str] = None,
        requested_period_start: Optional[str] = None,
        requested_period_end: Optional[str] = None,
        requested_comparison_start: Optional[str] = None,
        requested_comparison_end: Optional[str] = None,
        comparison_suppression_reason: Optional[str] = None,
        expected_totals: Optional[Dict[str, Any]] = None,
        ga4_extractor: Optional[GA4Extractor] = None,
        gsc_extractor: Optional[SearchConsoleExtractor] = None,
        gbp_extractor: Optional[GoogleBusinessProfileExtractor] = None,
    ):
        self.client_config = client
        self.start_date = start_date
        self.end_date = end_date
        self.prior_start_date = prior_start_date
        self.prior_end_date = prior_end_date
        self.report_mode = report_mode
        self.measurement_start_date = measurement_start_date
        self.requested_period_start = requested_period_start or start_date
        self.requested_period_end = requested_period_end or end_date
        self.requested_comparison_start = requested_comparison_start or prior_start_date
        self.requested_comparison_end = requested_comparison_end or prior_end_date
        self.comparison_suppression_reason = comparison_suppression_reason
        self.expected_totals = dict(expected_totals or {})
        self.ga4_extractor = ga4_extractor or GA4Extractor(client.ga4_property_id)
        self.gsc_extractor = gsc_extractor or SearchConsoleExtractor(client.gsc_site_url)
        self.gbp_extractor = gbp_extractor or GoogleBusinessProfileExtractor(
            client.gbp_location_id,
            account_id=client.gbp_account_id,
        )
        self._records: list[EvidenceRecord] = []
        self._diagnostics: list[str] = []
        self._next_evidence_number = 1

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return OpenRouter function-calling schemas for source-backed exploration."""
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "query_ga4_dimensions",
                            "description": (
                                "Query current observation GA4 dimension rows. This initial baseline has no prior "
                                "period; do not make movement claims. Use the returned evidence_id and fact_ids in every finding."
                                if self.report_mode == ReportMode.INITIAL_BASELINE
                                else "Query current and prior GA4 dimension rows. Use the returned evidence_id and fact_ids in every finding. Empty or unavailable responses are not evidence."
                            ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "dimensions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Use GA4 API names in camelCase, such as deviceCategory, sessionSource, sessionMedium, landingPagePlusQueryString, or eventName.",
                            },
                            "metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Use GA4 API names, such as sessions, activeUsers, conversions, eventCount, engagementRate, or bounceRate.",
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        "required": ["dimensions", "metrics"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_gsc_search_queries",
                    "description": (
                        "Query current observation Search Console queries. This initial baseline has no prior period; cite exact query fact_ids for current-only claims."
                        if self.report_mode == ReportMode.INITIAL_BASELINE
                        else "Query current and prior Search Console queries. Cite exact query fact_ids for any ranking, impression, click, or CTR claim."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query_regex": {"type": "string"},
                            "min_impressions": {"type": "integer", "minimum": 0},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_gbp_local_reputation",
                    "description": "Inspect available GBP profile NAP, hours, categories, services, Performance metrics, monthly search keywords, managed reviews and reply status, plus Business Calls insights when enabled. Use only returned fields and preserve unavailable or threshold states.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_device_conversion_breakdown",
                    "description": (
                        "Query current observation GA4 device rows and deterministic conversion shares. This initial baseline has no prior period; cite the returned fact_ids for current-only claims."
                        if self.report_mode == ReportMode.INITIAL_BASELINE
                        else "Query current and prior GA4 device rows and deterministic conversion shares. Cite the returned fact_ids for device claims."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_top_referrers_and_landing_pages",
                    "description": (
                        "Query current observation GA4 source/medium and landing-page rows. This initial baseline has no prior period; cite exact fact_ids for current-only claims."
                        if self.report_mode == ReportMode.INITIAL_BASELINE
                        else "Query current and prior GA4 source/medium and landing-page rows. Cite exact fact_ids for referral or landing-page claims."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    },
                },
            },
        ]
        for definition in definitions:
            function = definition["function"]
            parameters = function["parameters"]
            function["strict"] = True
            parameters["additionalProperties"] = False
            parameters["required"] = list(parameters.get("properties", {}))
        return definitions

    def evidence_bundle(self) -> EvidenceBundle:
        candidates = [candidate for record in self._records for candidate in record.candidates]
        return EvidenceBundle(
            client_id=self.client_config.client_id,
            period_start=self.start_date,
            period_end=self.end_date,
            prior_start=self.prior_start_date,
            prior_end=self.prior_end_date,
            report_mode=self.report_mode,
            measurement_start_date=self.measurement_start_date,
            requested_period_start=self.requested_period_start,
            requested_period_end=self.requested_period_end,
            requested_comparison_start=self.requested_comparison_start,
            requested_comparison_end=self.requested_comparison_end,
            comparison_suppressed=self.report_mode == ReportMode.INITIAL_BASELINE,
            comparison_suppression_reason=self.comparison_suppression_reason,
            records=list(self._records),
            candidates=candidates,
            diagnostics=list(self._diagnostics),
        )

    @property
    def has_errors(self) -> bool:
        return any(
            record.current.status == SourceAvailability.ERROR
            or (record.prior is not None and record.prior.status == SourceAvailability.ERROR)
            for record in self._records
        )

    @property
    def comparison_enabled(self) -> bool:
        return self.report_mode == ReportMode.COMPARISON

    def _new_evidence_id(self) -> str:
        evidence_id = f"ev-{self._next_evidence_number:03d}"
        self._next_evidence_number += 1
        return evidence_id

    @staticmethod
    def _candidate_category(source: str, metric_name: str) -> str:
        source_lower = source.lower()
        metric_lower = metric_name.lower()
        if source_lower == "gsc":
            return "seo"
        if source_lower == "gbp":
            return "local"
        if "conversion" in metric_lower or "lead" in metric_lower:
            return "conversion"
        if metric_lower in {"sessions", "activeusers"}:
            return "traffic"
        if source_lower == "ga4":
            return "acquisition"
        return "engagement"

    @staticmethod
    def _candidate_claim_type(fact: EvidenceFact) -> str:
        if fact.operation in {"change", "percent_change"}:
            return "comparison"
        if fact.operation == "ratio":
            return "ratio"
        if fact.operation == "share":
            return "share"
        return "observation"

    @staticmethod
    def _candidate_relation(fact: EvidenceFact) -> str:
        if fact.operation in {"change", "percent_change"}:
            numeric = _numeric(fact.value)
            if numeric is not None:
                if numeric > 0:
                    return "increase"
                if numeric < 0:
                    return "decrease"
                return "flat"
        if fact.operation == "ratio":
            return "ratio"
        if fact.operation == "share":
            return "share"
        return "observed"

    def _candidate_for(self, record: EvidenceRecord, fact: EvidenceFact) -> ExplorationCandidate:
        facts_by_id = {item.fact_id: item for item in record.facts}
        dependency_facts = [facts_by_id[item] for item in fact.depends_on_fact_ids if item in facts_by_id]
        source_fact_ids = list(dict.fromkeys([fact.fact_id, *fact.depends_on_fact_ids]))
        period = "both" if fact.operation in {"change", "percent_change"} else "current"
        eligibility_reasons: list[str] = []

        if record.candidate_eligibility_blocked:
            eligibility_reasons.append(
                "This evidence response was withheld from candidate selection because its aggregate integrity check failed."
            )

        if record.current.status != SourceAvailability.AVAILABLE:
            eligibility_reasons.append(f"Current evidence status is {record.current.status.value}.")
        if record.current.truncated:
            eligibility_reasons.append("Current evidence response is truncated.")
        if fact.operation != "value" and not fact.formula:
            eligibility_reasons.append("Calculated evidence fact is missing its formula.")
        if fact.operation != "value" and fact.calculation == "source_value":
            eligibility_reasons.append("Calculated evidence fact is missing its calculation type.")
        for dependency_id in fact.depends_on_fact_ids:
            if dependency_id not in facts_by_id:
                eligibility_reasons.append(f"Evidence dependency {dependency_id} is missing.")

        if period == "both":
            if self.report_mode != ReportMode.COMPARISON:
                eligibility_reasons.append("Comparison candidates are not eligible in an initial baseline.")
            if record.prior is None:
                eligibility_reasons.append("Comparison candidate has no prior response.")
            else:
                if record.prior.status != SourceAvailability.AVAILABLE:
                    eligibility_reasons.append(f"Prior evidence status is {record.prior.status.value}.")
                if record.prior.truncated:
                    eligibility_reasons.append("Prior evidence response is truncated.")
            if not dependency_facts or any(item.prior_value is None for item in dependency_facts):
                eligibility_reasons.append("Comparison candidate is missing a prior source value.")

        current_value = fact.value
        prior_value = fact.prior_value
        if dependency_facts and fact.operation in {"change", "percent_change"}:
            current_value = dependency_facts[0].value
            prior_value = dependency_facts[0].prior_value

        return ExplorationCandidate(
            candidate_id=f"cand-{record.evidence_id}-{_slug(fact.fact_id)}",
            claim_type=self._candidate_claim_type(fact),
            category=self._candidate_category(record.source, fact.metric_name),
            label=fact.label,
            source=record.source,
            evidence_id=record.evidence_id,
            fact_ids=source_fact_ids,
            source_fact_ids=source_fact_ids,
            metric_name=fact.metric_name,
            unit=fact.unit,
            period=period,
            operation=fact.operation,
            formula=fact.formula,
            calculation=fact.calculation,
            value=fact.value,
            current_value=current_value,
            prior_value=prior_value,
            relation=self._candidate_relation(fact),
            eligible=not eligibility_reasons,
            eligibility_reasons=eligibility_reasons,
        )

    def _build_candidates(self, record: EvidenceRecord) -> list[ExplorationCandidate]:
        return [self._candidate_for(record, fact) for fact in record.facts]

    def _period(
        self,
        result: Dict[str, Any],
        start_date: str,
        end_date: str,
        rows: list[dict[str, Any]],
        payload: Optional[dict[str, Any]] = None,
    ) -> EvidencePeriod:
        status = _status(result.get("status"), rows)
        reason = result.get("reason")
        return EvidencePeriod(
            start_date=start_date,
            end_date=end_date,
            status=status,
            row_count=int(result.get("row_count", len(rows)) or 0),
            rows=rows,
            payload=payload or {},
            truncated=bool(result.get("truncated", False)),
            reason=reason,
        )

    def _record(
        self,
        source: str,
        query: dict[str, Any],
        current_result: Dict[str, Any],
        current_rows: list[dict[str, Any]],
        facts: list[EvidenceFact],
        prior_result: Optional[Dict[str, Any]] = None,
        prior_rows: Optional[list[dict[str, Any]]] = None,
        limitations: Optional[list[str]] = None,
        current_payload: Optional[dict[str, Any]] = None,
        prior_payload: Optional[dict[str, Any]] = None,
        candidate_eligibility_blocked: bool = False,
    ) -> tuple[EvidenceRecord, dict[str, Any]]:
        record = EvidenceRecord(
            evidence_id=self._new_evidence_id(),
            source=source,
            query=query,
            current=self._period(current_result, self.start_date, self.end_date, current_rows, current_payload),
            prior=(
                self._period(prior_result, self.prior_start_date, self.prior_end_date, prior_rows or [], prior_payload)
                if prior_result is not None
                else None
            ),
            facts=facts,
            limitations=limitations or [],
            candidate_eligibility_blocked=candidate_eligibility_blocked,
        )
        record.candidates = self._build_candidates(record)
        self._records.append(record)
        if record.current.status in {SourceAvailability.UNAVAILABLE, SourceAvailability.EMPTY, SourceAvailability.ERROR}:
            self._diagnostics.append(
                f"{record.evidence_id} {source} current status={record.current.status.value}: {record.current.reason or 'no additional reason'}"
            )
        if record.current.status == SourceAvailability.ERROR:
            self._diagnostics.append(f"{record.evidence_id} tool execution failed; no discovery may use this run.")
        if record.prior and record.prior.status in {
            SourceAvailability.UNAVAILABLE,
            SourceAvailability.EMPTY,
            SourceAvailability.ERROR,
        }:
            self._diagnostics.append(
                f"{record.evidence_id} {source} prior status={record.prior.status.value}: "
                f"{record.prior.reason or 'no additional reason'}"
            )
        if record.prior and record.prior.status == SourceAvailability.ERROR:
            self._diagnostics.append(f"{record.evidence_id} prior tool execution failed; current-only candidates may still use healthy evidence.")
        limitation_prefix = "aggregate integrity limitation" if record.candidate_eligibility_blocked else "limitation"
        for limitation in record.limitations:
            self._diagnostics.append(f"{record.evidence_id} {source} {limitation_prefix}: {limitation}")
        response = {
            "evidence_id": record.evidence_id,
            "source": source,
            "status": record.current.status.value,
            "current": record.current.model_dump(mode="json"),
            "prior": record.prior.model_dump(mode="json") if record.prior else None,
            "facts": [fact.model_dump(mode="json") for fact in facts],
            "candidates": [candidate.model_dump(mode="json") for candidate in record.candidates],
            "eligible_candidates": [
                candidate.model_dump(mode="json") for candidate in record.candidates if candidate.eligible
            ],
            "data": current_rows,
            "prior_data": prior_rows or [],
            "limitations": record.limitations,
            "candidate_eligibility_blocked": record.candidate_eligibility_blocked,
        }
        return record, response

    def _device_integrity_limitations(
        self,
        current_rows: list[dict[str, Any]],
        prior_rows: list[dict[str, Any]],
    ) -> list[str]:
        """Withhold device findings when their aggregate does not reconcile to GA4 totals."""

        limitations: list[str] = []
        expected_current_sessions = _numeric(self.expected_totals.get("sessions"))
        observed_current_sessions = (
            sum(float(row.get("sessions") or 0) for row in current_rows) if current_rows else None
        )
        if (
            expected_current_sessions is not None
            and observed_current_sessions is not None
            and abs(observed_current_sessions - float(expected_current_sessions)) > 0.01
        ):
            limitations.append(
                "Device session rows do not reconcile to the headline GA4 session total; device findings were withheld."
            )

        expected_current_conversions = _numeric(self.expected_totals.get("conversions"))
        observed_current_conversions = (
            sum(float(row.get("conversions") or 0) for row in current_rows) if current_rows else None
        )
        if (
            expected_current_conversions is not None
            and observed_current_conversions is not None
            and abs(observed_current_conversions - float(expected_current_conversions)) > 0.01
        ):
            limitations.append(
                "Device conversion rows do not reconcile to the headline GA4 conversion total; device findings were withheld."
            )
        return limitations

    def _error_response(self, tool_name: str, reason: str) -> str:
        result = {
            "source": tool_name,
            "status": SourceAvailability.ERROR.value,
            "reason": reason,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "rows": [],
            "row_count": 0,
        }
        _, response = self._record(
            source=tool_name,
            query={"tool": tool_name},
            current_result=result,
            current_rows=[],
            facts=[],
            limitations=[reason],
        )
        return json.dumps(response)

    @staticmethod
    def _structured_ga4_rows(result: Dict[str, Any], dimensions: list[str], metrics: list[str]) -> list[dict[str, Any]]:
        structured: list[dict[str, Any]] = []
        for row in result.get("rows", []):
            dimension_values = row.get("dimensions", [])
            metric_values = row.get("metrics", [])
            entry: dict[str, Any] = {}
            for index, name in enumerate(dimensions):
                entry[name] = dimension_values[index] if index < len(dimension_values) else ""
            for index, name in enumerate(metrics):
                raw_value = metric_values[index] if index < len(metric_values) else None
                numeric_value = _numeric(raw_value)
                entry[name] = numeric_value if numeric_value is not None else raw_value
            structured.append(entry)
        return structured

    @staticmethod
    def _row_key(row: dict[str, Any], dimensions: list[str], index: int) -> str:
        values = [str(row.get(name, "")) for name in dimensions]
        return "|".join(values) if any(values) else f"row-{index}"

    def _row_facts(
        self,
        prefix: str,
        current_rows: list[dict[str, Any]],
        prior_rows: list[dict[str, Any]],
        dimensions: list[str],
        metrics: list[str],
    ) -> list[EvidenceFact]:
        prior_by_key = {
            self._row_key(row, dimensions, index): row
            for index, row in enumerate(prior_rows)
        }
        facts: list[EvidenceFact] = []
        for index, row in enumerate(current_rows):
            key = self._row_key(row, dimensions, index)
            prior_row = prior_by_key.get(key, {})
            key_slug = _slug(key)
            for metric in metrics:
                current_value = row.get(metric)
                prior_value = prior_row.get(metric)
                if current_value is None and prior_value is None:
                    continue
                unit = "count" if metric in {"sessions", "activeUsers", "conversions", "clicks", "impressions"} else metric
                if metric == "ctr":
                    current_value = round(float(current_value) * 100, 2) if current_value is not None else None
                    prior_value = round(float(prior_value) * 100, 2) if prior_value is not None else None
                    unit = "percent"
                base_fact_id = f"{prefix}-{key_slug}-{_slug(metric)}"
                facts.append(EvidenceFact(
                    fact_id=base_fact_id,
                    label=f"{key} {metric}",
                    value=current_value,
                    prior_value=prior_value,
                    metric_name=metric,
                    unit=unit,
                    operation="value",
                    source_path=f"rows[{index}].{metric}",
                    calculation="source_value",
                ))
                if current_value is not None and prior_value is not None:
                    current_number = _numeric(current_value)
                    prior_number = _numeric(prior_value)
                    if current_number is not None and prior_number is not None:
                        facts.append(EvidenceFact(
                            fact_id=f"{prefix}-{key_slug}-{_slug(metric)}-change",
                            label=f"{key} {metric} change",
                            value=round(float(current_number) - float(prior_number), 2),
                            metric_name=metric,
                            unit=unit,
                            operation="change",
                            source_path=f"rows[{index}].{metric}",
                            formula="current value minus prior value",
                            calculation="difference",
                            depends_on_fact_ids=[base_fact_id],
                        ))
                        if prior_number != 0:
                            facts.append(EvidenceFact(
                                fact_id=f"{prefix}-{key_slug}-{_slug(metric)}-percent-change",
                                label=f"{key} {metric} percent change",
                                value=round(((float(current_number) - float(prior_number)) / abs(float(prior_number))) * 100, 2),
                                metric_name=metric,
                                unit="percent",
                                operation="percent_change",
                                source_path=f"rows[{index}].{metric}",
                                formula="(current minus prior) divided by absolute prior, times 100",
                                calculation="percent_change",
                                depends_on_fact_ids=[base_fact_id],
                            ))
        return facts

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute one tool and return a JSON response containing retained evidence."""
        try:
            if tool_name == "query_ga4_dimensions":
                return self._exec_ga4(arguments)
            if tool_name == "query_gsc_search_queries":
                return self._exec_gsc(arguments)
            if tool_name == "query_gbp_local_reputation":
                return self._exec_gbp(arguments)
            if tool_name == "query_device_conversion_breakdown":
                return self._exec_device_breakdown(arguments)
            if tool_name == "query_top_referrers_and_landing_pages":
                return self._exec_referrers(arguments)
            return self._error_response(tool_name, f"Unknown exploratory tool: {tool_name}.")
        except Exception as exc:
            return self._error_response(tool_name, f"Exploratory tool failed: {type(exc).__name__}.")

    def _exec_ga4(self, args: Dict[str, Any]) -> str:
        dimensions = args.get("dimensions")
        metrics = args.get("metrics")
        limit = int(args.get("limit", 10))
        if not isinstance(dimensions, list) or not dimensions or not all(isinstance(item, str) for item in dimensions):
            return self._error_response("query_ga4_dimensions", "GA4 dimensions must be a non-empty string array.")
        if not isinstance(metrics, list) or not metrics or not all(isinstance(item, str) for item in metrics):
            return self._error_response("query_ga4_dimensions", "GA4 metrics must be a non-empty string array.")
        if limit < 1 or limit > 100:
            return self._error_response("query_ga4_dimensions", "GA4 limit must be between 1 and 100.")

        current_result = self.ga4_extractor.run_report(self.start_date, self.end_date, dimensions, metrics, limit=limit)
        prior_result = (
            self.ga4_extractor.run_report(
                self.prior_start_date,
                self.prior_end_date,
                dimensions,
                metrics,
                limit=limit,
            )
            if self.comparison_enabled
            else None
        )
        current_rows = self._structured_ga4_rows(current_result, dimensions, metrics)
        prior_rows = self._structured_ga4_rows(prior_result or {}, dimensions, metrics)
        facts = self._row_facts("ga4", current_rows, prior_rows, dimensions, metrics)
        integrity_limitations = (
            self._device_integrity_limitations(current_rows, prior_rows)
            if dimensions == ["deviceCategory"]
            else []
        )
        _, response = self._record(
            source="ga4",
            query={"dimensions": dimensions, "metrics": metrics, "limit": limit},
            current_result=current_result,
            current_rows=current_rows,
            prior_result=prior_result,
            prior_rows=prior_rows,
            facts=facts,
            limitations=integrity_limitations,
            candidate_eligibility_blocked=bool(integrity_limitations),
        )
        return json.dumps(response)

    def _exec_gsc(self, args: Dict[str, Any]) -> str:
        query_regex = str(args.get("query_regex", ""))
        min_impressions = int(args.get("min_impressions", 10))
        limit = int(args.get("limit", 15))
        if min_impressions < 0 or limit < 1 or limit > 100:
            return self._error_response("query_gsc_search_queries", "Search Console thresholds are outside the supported range.")
        try:
            current_result = self.gsc_extractor.fetch_search_analytics(
                self.start_date, self.end_date, row_limit=min(limit * 3, 300)
            )
            prior_result = (
                self.gsc_extractor.fetch_search_analytics(
                    self.prior_start_date, self.prior_end_date, row_limit=min(limit * 3, 300)
                )
                if self.comparison_enabled
                else None
            )
            current_rows = filter_search_rows(current_result, query_regex, min_impressions, limit)
            prior_rows = filter_search_rows(prior_result or {}, query_regex, min_impressions, limit)
        except re.error:
            return self._error_response("query_gsc_search_queries", "Search Console query_regex is invalid.")
        facts = self._row_facts(
            "gsc",
            current_rows,
            prior_rows,
            ["query"],
            ["clicks", "impressions", "ctr", "position"],
        )
        _, response = self._record(
            source="gsc",
            query={"query_regex": query_regex, "min_impressions": min_impressions, "limit": limit},
            current_result=current_result,
            current_rows=current_rows,
            prior_result=prior_result,
            prior_rows=prior_rows,
            facts=facts,
        )
        return json.dumps({**response, "queries": current_rows, "prior_queries": prior_rows})

    def _exec_gbp(self, args: Dict[str, Any]) -> str:
        result = self.gbp_extractor.fetch_local_insights(self.start_date, self.end_date)
        prior_result = None
        if self.comparison_enabled:
            fetch_periodic = getattr(self.gbp_extractor, "fetch_periodic_insights", None)
            prior_result = (
                fetch_periodic(self.prior_start_date, self.prior_end_date)
                if callable(fetch_periodic)
                else {
                    "status": SourceAvailability.UNAVAILABLE.value,
                    "reason": "GBP connector does not expose a prior-period method.",
                }
            )

        reviews = list(result.get("reviews", []) or [])
        keywords = list(result.get("monthly_search_keywords", []) or [])
        payload = {
            "private_location_id": result.get("private_location_id"),
            "account_id": result.get("account_id"),
            "profile_status": result.get("profile_status"),
            "profile_summary": result.get("profile_summary", {}),
            "performance_status": result.get("performance_status"),
            "performance_metrics": result.get("performance_metrics", {}),
            "available_performance_metrics": result.get("available_performance_metrics", []),
            "search_keywords_status": result.get("search_keywords_status"),
            "monthly_search_keywords": keywords[:50],
            "monthly_search_keywords_total": len(keywords),
            "reviews_status": result.get("reviews_status"),
            "review_inventory_complete": result.get("review_inventory_complete", False),
            "review_response_summary": result.get("review_response_summary", {}),
            "reviews": reviews[:10],
            "reviews_total": len(reviews),
            "business_calls_status": result.get("business_calls_status"),
            "business_calls": result.get("business_calls", {}),
            "answered_calls": result.get("answered_calls"),
            "missed_calls": result.get("missed_calls"),
            "phone_calls": result.get("phone_calls"),
            "direction_requests": result.get("direction_requests"),
            "website_clicks": result.get("website_clicks"),
        }
        prior_payload = {
            "performance_status": (prior_result or {}).get("performance_status"),
            "performance_metrics": (prior_result or {}).get("performance_metrics", {}),
            "search_keywords_status": (prior_result or {}).get("search_keywords_status"),
            "monthly_search_keywords": list((prior_result or {}).get("monthly_search_keywords", []) or [])[:50],
            "business_calls_status": (prior_result or {}).get("business_calls_status"),
            "business_calls": (prior_result or {}).get("business_calls", {}),
            "answered_calls": (prior_result or {}).get("answered_calls"),
            "missed_calls": (prior_result or {}).get("missed_calls"),
        } if prior_result is not None else None

        payload = {
            key: value for key, value in payload.items() if value not in (None, {}, [])
        }
        for action_key in ("phone_calls", "direction_requests", "website_clicks"):
            payload[action_key] = result.get(action_key)
        facts: list[EvidenceFact] = []
        for key in ("average_rating", "total_reviews_count"):
            value = _numeric(result.get(key))
            if value is not None:
                facts.append(EvidenceFact(
                    fact_id=f"gbp-{_slug(key)}",
                    label=key,
                    value=value,
                    metric_name=key,
                    unit="rating" if key == "average_rating" else "count",
                    operation="value",
                    source_path=key,
                ))

        def add_period_fact(
            metric_name: str,
            label: str,
            current_value: Any,
            prior_value: Any,
            source_path: str,
        ) -> None:
            current_number = _numeric(current_value)
            if current_number is None:
                return
            prior_number = _numeric(prior_value)
            base_fact_id = f"gbp-{_slug(metric_name)}"
            facts.append(EvidenceFact(
                fact_id=base_fact_id,
                label=label,
                value=current_number,
                prior_value=prior_number,
                metric_name=metric_name,
                unit="count",
                operation="value",
                source_path=source_path,
            ))
            if prior_number is not None:
                facts.append(EvidenceFact(
                    fact_id=f"{base_fact_id}-change",
                    label=f"{label} change",
                    value=current_number - prior_number,
                    prior_value=prior_number,
                    metric_name=metric_name,
                    unit="count",
                    operation="change",
                    source_path=source_path,
                    formula="current value minus prior value",
                    calculation="difference",
                    depends_on_fact_ids=[base_fact_id],
                ))

        prior_metrics = (prior_result or {}).get("performance_metrics", {}) or {}
        for metric_name, metric_payload in (result.get("performance_metrics", {}) or {}).items():
            prior_metric_payload = prior_metrics.get(metric_name, {}) or {}
            add_period_fact(
                metric_name=f"gbp_{metric_name.lower()}",
                label=metric_name.replace("_", " ").title(),
                current_value=(metric_payload or {}).get("total") if isinstance(metric_payload, dict) else None,
                prior_value=(prior_metric_payload or {}).get("total") if isinstance(prior_metric_payload, dict) else None,
                source_path=f"performance_metrics.{metric_name}.total",
            )
        for key, label in {
            "answered_calls": "Answered Calls",
            "missed_calls": "Missed Calls",
            "phone_calls": "Call Button Clicks",
            "direction_requests": "Direction Requests",
            "website_clicks": "Website Clicks",
        }.items():
            add_period_fact(
                metric_name=f"gbp_{key}",
                label=label,
                current_value=result.get(key),
                prior_value=(prior_result or {}).get(key),
                source_path=key,
            )
        reply_coverage = _numeric((result.get("review_response_summary") or {}).get("reply_coverage_percent"))
        if reply_coverage is not None:
            facts.append(EvidenceFact(
                fact_id="gbp-review-reply-coverage",
                label="Review reply coverage",
                value=reply_coverage,
                metric_name="review_reply_coverage",
                unit="percent",
                operation="value",
                source_path="review_response_summary.reply_coverage_percent",
            ))
        _, response = self._record(
            source="gbp",
            query={
                "location_id": self.client_config.gbp_location_id,
                "account_id": self.client_config.gbp_account_id,
                "requested": "profile_performance_keywords_reviews_calls",
            },
            current_result=result,
            current_rows=[],
            facts=facts,
            limitations=list(result.get("limitations", [])),
            current_payload=payload,
            prior_result=prior_result,
            prior_payload=prior_payload,
        )
        return json.dumps(response)

    def _exec_device_breakdown(self, args: Dict[str, Any]) -> str:
        dimensions = ["deviceCategory"]
        metrics = ["sessions", "conversions"]
        current_result = self.ga4_extractor.run_report(self.start_date, self.end_date, dimensions, metrics, limit=100)
        prior_result = (
            self.ga4_extractor.run_report(
                self.prior_start_date,
                self.prior_end_date,
                dimensions,
                metrics,
                limit=100,
            )
            if self.comparison_enabled
            else None
        )
        current_rows = self._structured_ga4_rows(current_result, dimensions, metrics)
        prior_rows = self._structured_ga4_rows(prior_result or {}, dimensions, metrics)
        current_total_sessions = sum(float(row.get("sessions") or 0) for row in current_rows) if current_rows else None
        prior_total_sessions = sum(float(row.get("sessions") or 0) for row in prior_rows) if prior_rows else None
        current_total_conversions = sum(float(row.get("conversions") or 0) for row in current_rows) if current_rows else None
        prior_total_conversions = sum(float(row.get("conversions") or 0) for row in prior_rows) if prior_rows else None
        for row in current_rows:
            sessions = float(row.get("sessions") or 0)
            conversions = float(row.get("conversions") or 0)
            row["conversion_rate"] = round((conversions / sessions) * 100, 2) if sessions else None
            row["session_share"] = round((sessions / current_total_sessions) * 100, 2) if current_total_sessions else None
            row["conversion_share"] = round((conversions / current_total_conversions) * 100, 2) if current_total_conversions else None
        facts = self._row_facts("device", current_rows, prior_rows, dimensions, metrics)
        integrity_limitations = self._device_integrity_limitations(current_rows, prior_rows)
        fact_ids = {fact.fact_id for fact in facts}
        if current_total_sessions is not None:
            facts.append(EvidenceFact(
                fact_id="device-total-sessions",
                label="total sessions",
                value=current_total_sessions,
                prior_value=prior_total_sessions,
                metric_name="sessions",
                unit="count",
                operation="value",
                source_path="totals.sessions",
                calculation="source_value",
            ))
            fact_ids.add("device-total-sessions")
        if current_total_conversions is not None:
            facts.append(EvidenceFact(
                fact_id="device-total-conversions",
                label="total conversions",
                value=current_total_conversions,
                prior_value=prior_total_conversions,
                metric_name="conversions",
                unit="count",
                operation="value",
                source_path="totals.conversions",
                calculation="source_value",
            ))
            fact_ids.add("device-total-conversions")
        for index, row in enumerate(current_rows):
            device = _slug(row.get("deviceCategory", f"device-{index}"))
            row_session_id = f"device-{device}-sessions"
            row_conversion_id = f"device-{device}-conversions"
            dependencies = {
                "conversion_rate": [row_conversion_id, row_session_id],
                "session_share": [row_session_id, "device-total-sessions"],
                "conversion_share": [row_conversion_id, "device-total-conversions"],
            }
            for key, unit in (("conversion_rate", "percent"), ("session_share", "percent"), ("conversion_share", "percent")):
                if row.get(key) is not None:
                    facts.append(EvidenceFact(
                        fact_id=f"device-{device}-{_slug(key)}",
                        label=f"{row.get('deviceCategory')} {key}",
                        value=row[key],
                        metric_name=key,
                        unit=unit,
                        operation="ratio" if key == "conversion_rate" else "share",
                        source_path=f"data[{index}].{key}",
                        formula=f"derived from sessions/conversions; current totals sessions={current_total_sessions:g}, conversions={current_total_conversions:g}",
                        calculation="ratio" if key == "conversion_rate" else "share",
                        depends_on_fact_ids=[item for item in dependencies[key] if item in fact_ids],
                    ))
        _, response = self._record(
            source="ga4",
            query={"dimensions": dimensions, "metrics": metrics, "analysis": "device conversion breakdown"},
            current_result=current_result,
            current_rows=current_rows,
            prior_result=prior_result,
            prior_rows=prior_rows,
            facts=facts,
            limitations=integrity_limitations,
            candidate_eligibility_blocked=bool(integrity_limitations),
        )
        return json.dumps({**response, "devices": current_rows, "prior_devices": prior_rows})

    def _exec_referrers(self, args: Dict[str, Any]) -> str:
        limit = int(args.get("limit", 10))
        if limit < 1 or limit > 100:
            return self._error_response("query_top_referrers_and_landing_pages", "Referral limit must be between 1 and 100.")
        dimensions = ["sessionSourceMedium", "landingPagePlusQueryString"]
        metrics = ["sessions", "conversions"]
        current_result = self.ga4_extractor.run_report(self.start_date, self.end_date, dimensions, metrics, limit=limit)
        prior_result = (
            self.ga4_extractor.run_report(
                self.prior_start_date,
                self.prior_end_date,
                dimensions,
                metrics,
                limit=limit,
            )
            if self.comparison_enabled
            else None
        )
        current_rows = self._structured_ga4_rows(current_result, dimensions, metrics)
        prior_rows = self._structured_ga4_rows(prior_result or {}, dimensions, metrics)
        facts = self._row_facts("referrer", current_rows, prior_rows, dimensions, metrics)
        _, response = self._record(
            source="ga4",
            query={"dimensions": dimensions, "metrics": metrics, "limit": limit},
            current_result=current_result,
            current_rows=current_rows,
            prior_result=prior_result,
            prior_rows=prior_rows,
            facts=facts,
        )
        return json.dumps({**response, "referrers": current_rows, "prior_referrers": prior_rows})
