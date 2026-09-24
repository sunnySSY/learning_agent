"""Privacy-preserving structured logs and optional OpenTelemetry tracing."""

from __future__ import annotations

import json
import logging
import time

_TRACER = None


class JsonLogFormatter(logging.Formatter):
    def format(self, record):  # noqa: D401
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": record.levelname,
            "service": "learning-agent-api",
            "event": getattr(record, "event", record.getMessage()),
            "request_id": getattr(record, "request_id", None),
            "user_key": getattr(record, "user_key", None),
            "object_id": getattr(record, "object_id", None),
            "result": getattr(record, "result", None),
            "error_code": getattr(record, "error_code", None),
        }
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


def configure_logging() -> None:
    logger = logging.getLogger("learning-agent")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate = False


def configure_tracing(endpoint: str = ""):
    """Install an OTLP exporter only when explicitly configured."""
    global _TRACER
    try:
        from opentelemetry import trace
        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            provider = TracerProvider(resource=Resource.create({"service.name": "learning-agent-api"}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("learning-agent")
    except Exception:
        _TRACER = None
    return _TRACER

