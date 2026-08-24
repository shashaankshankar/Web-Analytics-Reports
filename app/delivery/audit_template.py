from __future__ import annotations

import html
import json

from app.analytics.contracts import ExplorationAudit


def render_exploration_audit_html(audit: ExplorationAudit) -> str:
    """Render the protected internal audit as escaped, self-contained HTML."""
    payload = json.dumps(audit.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True)
    title = html.escape(f"Deep Insights Audit | {audit.client_id}")
    status = html.escape(audit.status)
    mode = html.escape(audit.report_mode.value)
    observation = html.escape(
        f"{audit.observation_window_start or audit.evidence.period_start} to "
        f"{audit.observation_window_end or audit.evidence.period_end}"
    )
    measurement_start = html.escape(audit.measurement_start_date or "not configured")
    suppression = html.escape(audit.comparison_suppression_reason or "none")
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f8fafc;color:#0f172a;padding:24px}}main{{max-width:1100px;margin:0 auto;background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:24px}}h1{{font-family:ui-sans-serif,system-ui,sans-serif}}.status{{display:inline-block;padding:4px 8px;background:#e2e8f0;border-radius:4px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.5}}</style>
</head><body><main><h1>{title}</h1><p>Status: <span class="status">{status}</span></p><p>Report mode: <span class="status">{mode}</span><br>Observation window: {observation}<br>Measurement start: {measurement_start}<br>Comparison suppression: {suppression}</p><pre>{html.escape(payload)}</pre></main></body></html>"""
