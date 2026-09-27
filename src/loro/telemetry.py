"""Optional OpenTelemetry traces and metrics for agent runs.

Install ``loro-agent[otel]`` and set ``[telemetry] enabled = true``. Without the extra, or when
disabled, every call here is a cheap no-op, so the runtime never depends on OpenTelemetry.

Spans and metrics carry operational facts only (mode, provider, model, step, tool name, status,
token counts, latency). Prompts, model output, tool arguments and results are never recorded.
Loro owns its tracer and meter providers instead of installing global ones, so embedding
applications keep control of their own OpenTelemetry setup.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from loro.config import TelemetryConfig

_SHARED: dict[str, Telemetry] = {}
_LOCK = threading.Lock()


def otel_available() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError:
        return False
    return True


class Telemetry:
    """Spans and instruments for one configuration; a no-op when disabled or not installed."""

    def __init__(
        self,
        config: TelemetryConfig,
        *,
        span_exporter: Any = None,
        metric_reader: Any = None,
    ) -> None:
        self.config = config
        self.enabled = bool(config.enabled) and otel_available()
        self.reason = (
            "disabled"
            if not config.enabled
            else "ok"
            if self.enabled
            else "install loro-agent[otel] to enable OpenTelemetry"
        )
        self._tracer: Any = None
        self._instruments: dict[str, Any] = {}
        self.tracer_provider: Any = None
        self.meter_provider: Any = None
        if self.enabled:
            self._configure(span_exporter, metric_reader)

    def _configure(self, span_exporter: Any, metric_reader: Any) -> None:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        from loro import __version__

        resource = Resource.create(
            {
                "service.name": self.config.service_name,
                "service.version": __version__,
                **self.config.resource_attributes,
            }
        )
        self.tracer_provider = TracerProvider(resource=resource)
        exporter = span_exporter or self._default_span_exporter()
        if exporter is not None:
            processor = SimpleSpanProcessor if span_exporter is not None else BatchSpanProcessor
            self.tracer_provider.add_span_processor(processor(exporter))
        readers = [metric_reader] if metric_reader is not None else self._default_readers()
        self.meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        self._tracer = self.tracer_provider.get_tracer("loro", __version__)
        meter = self.meter_provider.get_meter("loro", __version__)
        self._instruments = {
            "runs": meter.create_counter("loro.runs", description="Agent runs by stop reason"),
            "tool_calls": meter.create_counter("loro.tool.calls", description="Tool executions"),
            "tokens": meter.create_counter(
                "loro.model.tokens", unit="{token}", description="Model tokens by direction"
            ),
            "model_latency": meter.create_histogram(
                "loro.model.duration", unit="ms", description="Model call latency"
            ),
            "tool_latency": meter.create_histogram(
                "loro.tool.duration", unit="ms", description="Tool execution latency"
            ),
            "approvals": meter.create_counter(
                "loro.approvals", description="Approval events by outcome"
            ),
        }

    def _default_span_exporter(self) -> Any:
        if self.config.exporter == "otlp":
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            return OTLPSpanExporter(
                endpoint=_signal_endpoint(self.config.otlp_endpoint, "traces"),
                headers=self.config.otlp_headers_from_env(),
            )
        if self.config.exporter == "console":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            return ConsoleSpanExporter()
        return None

    def _default_readers(self) -> list[Any]:
        if self.config.exporter != "otlp":
            return []
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporter = OTLPMetricExporter(
            endpoint=_signal_endpoint(self.config.otlp_endpoint, "metrics"),
            headers=self.config.otlp_headers_from_env(),
        )
        return [
            PeriodicExportingMetricReader(
                exporter, export_interval_millis=self.config.metric_interval_seconds * 1000
            )
        ]

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
        if not self.enabled or self._tracer is None:
            yield None
            return
        # Exception messages can carry provider response bodies or file contents, so spans
        # record only the exception type (never the message) and an ERROR status.
        with self._tracer.start_as_current_span(
            name,
            attributes=_clean(attributes),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except Exception as error:
                self.error(span, type(error).__name__)
                span.set_attribute("error.type", type(error).__name__)
                raise

    def set(self, span: Any, attributes: Mapping[str, Any]) -> None:
        if span is not None:
            span.set_attributes(_clean(attributes))

    def error(self, span: Any, description: str) -> None:
        if span is not None:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, description))

    def count(self, name: str, value: int | float = 1, **attributes: Any) -> None:
        instrument = self._instruments.get(name)
        if instrument is not None and value:
            instrument.add(value, _clean(attributes))

    def record(self, name: str, value: float, **attributes: Any) -> None:
        instrument = self._instruments.get(name)
        if instrument is not None:
            instrument.record(value, _clean(attributes))

    def flush(self) -> None:
        if self.tracer_provider is not None:
            self.tracer_provider.force_flush()
        if self.meter_provider is not None:
            self.meter_provider.force_flush()


def shared_telemetry(config: TelemetryConfig) -> Telemetry:
    """One Telemetry per configuration, so exporters are not rebuilt for every run."""

    key = config.model_dump_json()
    with _LOCK:
        telemetry = _SHARED.get(key)
        if telemetry is None:
            telemetry = Telemetry(config)
            _SHARED[key] = telemetry
        return telemetry


def _signal_endpoint(base: str | None, signal: str) -> str | None:
    if not base:
        return None
    return f"{base.rstrip('/')}/v1/{signal}"


def _clean(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        cleaned[key] = value if isinstance(value, (str, bool, int, float)) else str(value)
    return cleaned
