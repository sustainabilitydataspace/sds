"""Prometheus collectors shared across API application instances."""

from prometheus_client import Gauge

READINESS_STATE = Gauge(
    "sds_api_readiness",
    "Functional SDS API readiness (1=ready, 0=unhealthy)",
)
