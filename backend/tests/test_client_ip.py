"""Regression tests for client IP extraction (WS 1.3).

Verifies that the shared ``client_ip`` helper prefers ``X-Real-IP`` over the
spoofable ``X-Forwarded-For`` header, and that both call sites in
``rate_limit.py`` and ``deps.py`` use the shared helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.security.net import client_ip


def _mock_request(
    *,
    x_real_ip: str | None = None,
    x_forwarded_for: str | None = None,
    client_host: str = "10.0.0.1",
) -> MagicMock:
    """Build a mock FastAPI Request with the given headers and client."""
    headers: dict[str, str] = {}
    if x_real_ip is not None:
        headers["x-real-ip"] = x_real_ip
    if x_forwarded_for is not None:
        headers["x-forwarded-for"] = x_forwarded_for

    request = MagicMock()
    request.headers = headers
    request.client = MagicMock()
    request.client.host = client_host
    return request


class TestClientIp:
    """The shared client_ip() helper."""

    def test_prefers_x_real_ip(self):
        """X-Real-IP takes precedence over everything."""
        req = _mock_request(
            x_real_ip="192.168.1.50",
            x_forwarded_for="10.10.10.10, 192.168.1.50",
            client_host="127.0.0.1",
        )
        assert client_ip(req) == "192.168.1.50"

    def test_x_real_ip_ignores_forwarded_for(self):
        """Even a forged X-Forwarded-For cannot override X-Real-IP."""
        req = _mock_request(
            x_real_ip="203.0.113.5",
            x_forwarded_for="FORGED_ATTACKER_IP, 203.0.113.5",
        )
        assert client_ip(req) == "203.0.113.5"

    def test_falls_back_to_client_host(self):
        """Without X-Real-IP, falls back to request.client.host."""
        req = _mock_request(client_host="172.16.0.99")
        assert client_ip(req) == "172.16.0.99"

    def test_forged_forwarded_for_does_not_control_result(self):
        """An attacker-controlled X-Forwarded-For without X-Real-IP
        does NOT let the attacker choose the IP — we ignore XFF entirely."""
        req = _mock_request(
            x_forwarded_for="ATTACKER_CONTROLLED",
            client_host="10.0.0.1",
        )
        # Without X-Real-IP, we fall back to client.host, NOT XFF
        assert client_ip(req) == "10.0.0.1"

    def test_no_client(self):
        """When request.client is None, returns 'unknown'."""
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert client_ip(req) == "unknown"

    def test_x_real_ip_stripped(self):
        """Whitespace around X-Real-IP is stripped."""
        req = _mock_request(x_real_ip="  192.168.1.1  ")
        assert client_ip(req) == "192.168.1.1"


class TestCallSitesUseSharedHelper:
    """Both rate_limit.py and deps.py must use the shared helper."""

    def test_rate_limit_no_local_client_ip(self):
        """rate_limit.py must not define its own _client_ip function."""
        import app.security.rate_limit as mod
        assert not hasattr(mod, "_client_ip"), (
            "rate_limit.py still has a local _client_ip — "
            "it should use app.security.net.client_ip"
        )

    def test_deps_no_local_client_ip(self):
        """deps.py must not define its own _client_ip function."""
        import app.security.deps as mod
        assert not hasattr(mod, "_client_ip"), (
            "deps.py still has a local _client_ip — "
            "it should use app.security.net.client_ip"
        )
