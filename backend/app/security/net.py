"""Shared network utilities for security modules.

A single source of truth for extracting the client IP address, used by
both the rate limiter and the audit recorder.
"""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str:
    """Extract the real client IP from the request.

    Prefers ``X-Real-IP`` (set fresh by nginx on every request — cannot be
    spoofed because nginx overwrites it, it does not append).  Falls back to
    ``request.client.host`` for local development without a reverse proxy.

    Does NOT trust ``X-Forwarded-For``: nginx's
    ``$proxy_add_x_forwarded_for`` APPENDS the real IP to whatever the client
    already sent, so the first entry in the header is attacker-controlled.
    Taking ``.split(",")[0]`` would let an attacker bypass per-IP rate limiting
    and taint the audit log.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"
