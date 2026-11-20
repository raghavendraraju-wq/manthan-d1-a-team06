"""Prometheus metrics. Names are a contract with monitoring/alerts.rules.yml."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.refunds = Counter(
            "returnright_refunds_total", "Refund attempts sent to the payment gateway, by outcome",
            ["outcome"], registry=self.registry)
        for outcome in ("succeeded", "declined", "timeout", "error"):
            self.refunds.labels(outcome).inc(0)
        self.gateway_latency = Histogram(
            "returnright_gateway_request_duration_seconds", "Payment gateway call duration",
            buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30), registry=self.registry)
        self.refused = Counter(
            "returnright_events_refused_total", "Events refused by the state machine, by error",
            ["error"], registry=self.registry)
        self.pending = Gauge(
            "returnright_refunds_pending", "Items waiting in Refund pending", registry=self.registry)
        self.finance_queue = Gauge(
            "returnright_finance_queue", "Items waiting in Pending finance", registry=self.registry)

    def observe_gateway(self, outcome: str, seconds: float) -> None:
        self.refunds.labels(outcome).inc()
        self.gateway_latency.observe(seconds)
