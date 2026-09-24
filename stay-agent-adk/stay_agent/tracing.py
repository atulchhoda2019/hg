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

Under a managed runtime (Agent Runtime, Cloud Run with telemetry collection) this exporter
is the one that carries ADK's spans as well: the first provider set wins, and on Agent
Runtime nothing has set one by the time `stay_agent` imports. That makes pointing it at the
right project load-bearing for every span, not just ours.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

SERVICE_NAME = "stay-agent-adk"

# Deliberately not `\d+`: Cloud Trace rejects a project *number* in the span name with
# "Invalid project id in name!", and managed runtimes are happy to export the number.
_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")

_PROJECT_ENV_VARS = ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_QUOTA_PROJECT", "GCLOUD_PROJECT")

_log = logging.getLogger(__name__)

_configured = False


def mode() -> str:
    return os.environ.get("STAY_TRACE", "off").strip().lower()


def _cloud_project() -> str:
    """The project to write spans to.

    `GOOGLE_CLOUD_PROJECT` is the documented knob but is not always a project id: Agent
    Runtime sets it to the project *number*, and Cloud Trace answers a numeric span name
    with "Invalid project id in name!". Anything that is not an id falls through to the
    project on the ambient credentials, which on Google infrastructure is the id.
    """
    candidate = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if _PROJECT_ID.fullmatch(candidate):
        return candidate

    import google.auth

    # The same env vars are what `google.auth.default` prefers, so they have to be out of
    # the way for it to reach the credentials and the metadata server underneath them.
    shadowed = {name: os.environ.pop(name, None) for name in _PROJECT_ENV_VARS}
    try:
        _, ambient = google.auth.default()
    finally:
        os.environ.update({name: value for name, value in shadowed.items() if value is not None})

    ambient = str(ambient or "").strip()
    if not _PROJECT_ID.fullmatch(ambient):
        raise RuntimeError(
            f"STAY_TRACE=cloud found no project id to write spans to "
            f"(GOOGLE_CLOUD_PROJECT={candidate!r}, credentials={ambient!r})"
        )
    return ambient


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

        project = _cloud_project()
        _log.info("stay-agent tracing: exporting to Cloud Trace in %s", project)
        exporter = CloudTraceSpanExporter(project_id=project)
        provider.add_span_processor(BatchSpanProcessor(exporter))
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
