from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import google.auth
from google.auth.transport.grpc import AuthMetadataPlugin
from google.auth.transport.requests import Request as GoogleAuthRequest
import grpc
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings

OTLP_TRACE_ENDPOINT = "https://telemetry.googleapis.com:443/v1/traces"


def safe_http_attributes(method: str, route: str | None, status_code: int) -> dict[str, str | int]:
    """Return a strict allowlist. Raw URLs, queries, headers, and bodies never enter spans."""
    return {
        "http.request.method": method,
        "http.route": route or "unmatched",
        "http.response.status_code": status_code,
    }


@dataclass
class TracingRuntime:
    provider: TracerProvider

    def shutdown(self) -> None:
        self.provider.force_flush(timeout_millis=10_000)
        self.provider.shutdown()


def configure_tracing(settings: Settings) -> TracingRuntime | None:
    if not settings.trace_enabled or settings.trace_sample_rate == 0:
        return None

    credentials, project_id = google.auth.default(
        scopes=("https://www.googleapis.com/auth/cloud-platform",),
        quota_project_id=settings.google_cloud_project,
    )
    auth_plugin = AuthMetadataPlugin(credentials=credentials, request=GoogleAuthRequest())
    channel_credentials = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(), grpc.metadata_call_credentials(auth_plugin)
    )
    service_name = os.getenv("K_SERVICE") or (
        "measurement-oauth-callback" if settings.oauth_callback_only else "measurement-reporting-platform"
    )
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("K_REVISION", "local"),
            "deployment.environment.name": settings.mode,
            "gcp.project_id": project_id or settings.google_cloud_project,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_rate)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_TRACE_ENDPOINT, credentials=channel_credentials))
    )
    trace.set_tracer_provider(provider)
    return TracingRuntime(provider)


class PrivacySafeTracingMiddleware:
    def __init__(self, app: ASGIApp, tracer_name: str = "measurement-reporting-platform") -> None:
        self.app = app
        self.tracer = trace.get_tracer(tracer_name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "HTTP"))
        status_code = 500
        carrier: Mapping[str, str] = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", ())
            if key.lower() in {b"traceparent", b"tracestate"}
        }
        parent_context = propagate.extract(carrier)

        async def traced_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        with self.tracer.start_as_current_span(
            f"HTTP {method}", context=parent_context, kind=SpanKind.SERVER
        ) as span:
            try:
                await self.app(scope, receive, traced_send)
            except Exception as error:
                span.set_attribute("error.type", type(error).__name__)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route_object = scope.get("route")
                route = getattr(route_object, "path", None)
                attributes = safe_http_attributes(method, route, status_code)
                span.update_name(f"{method} {attributes['http.route']}")
                span.set_attributes(attributes)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
