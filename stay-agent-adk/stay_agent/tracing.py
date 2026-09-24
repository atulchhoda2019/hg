"""Tracing. ADK already emits OpenTelemetry spans for agents, tools and model calls; this
module decides where they go and adds the spans ADK cannot know about.

Governance is the reason to trace here rather than to log. The interesting questions after
an incident are "which attribute version answered this search", "was there a live quote
before that price", "why did this floor plan escalate" — all of which are span attributes
on a corridor or gate step, not lines in a file.

`STAY_TRACE` picks the exporter:
    off      - no tracing (default, and what the offline tests run under)
    console  - spans to stdout, for local work
    cloud    - Google Cloud Trace in `GOOGLE_CLOUD_PROJECT`
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

SERVICE_NAME = "stay-agent-adk"

_configured = False


def mode() -> str:
    return os.environ.get("STAY_TRACE", "off").strip().lower()


def setup_tracing(service_name: str = SERVICE_NAME) -> bool:
    """Install the exporter named by `STAY_TRACE`. Returns whether tracing is on.

    Safe to call repeatedly and from anything that can be an entrypoint (`adk web`, the
    review UI, the demo scripts): only the first call installs a provider.
    """
    global _configured
    if _configured:
        return mode() != "off"
    chosen = mode()
    if chosen == "off":
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if chosen == "cloud":
        # Imported lazily: the offline install has no reason to carry the GCP exporter.
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project:
            raise RuntimeError("STAY_TRACE=cloud needs GOOGLE_CLOUD_PROJECT")
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=project)))
    elif chosen == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        raise ValueError(f"STAY_TRACE must be off, console or cloud, not {chosen!r}")

    trace.set_tracer_provider(provider)
    _configured = True
    return True


def tracer() -> trace.Tracer:
    return trace.get_tracer(SERVICE_NAME)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A span with flattened attributes. A no-op tracer costs nothing when tracing is off."""
    with tracer().start_as_current_span(name) as current:
        record(current, attributes)
        yield current


def record(current: trace.Span, attributes: Mapping[str, Any]) -> None:
    """Set attributes, coercing what OTel will not take (Decimal, date, list[str], None)."""
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (bool, int, float, str)):
            current.set_attribute(key, value)
        elif isinstance(value, (list, tuple)):
            current.set_attribute(key, [str(item) for item in value])
        else:
            current.set_attribute(key, str(value))
