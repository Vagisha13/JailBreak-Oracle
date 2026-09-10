"""SSRF-guarded CustomHTTPTargetProvider for user-defined HTTP endpoints.

Speaks the lightweight "target contract" an operator exposes for their own
service:

    POST {endpoint_url}
    Content-Type: application/json

    {
      "prompt": "<the adversarial payload for this turn>",
      "messages": [{"role": "user", "content": "..."}, ...],   # when multi-turn
      ...merged request_template_json...
    }

    Response: {"response": "<text>", "usage": {"prompt_tokens": N,
                                               "completion_tokens": N,
                                               "total_tokens": N}}

Anything that is NOT JSON, or a 4xx/5xx, is surfaced as a *controlled* failure
(error_type + status_code) instead of a crash, so a campaign can keep going and
blame the right component (E-27).

SSRF posture: by default only publicly routable hosts are callable. Private /
loopback / link-local / reserved IPs are denied unless the host is explicitly
listed in ``settings.SSRF_ALLOW_PRIVATE_HOSTS`` (used by the test suite and by
operators running a dummy target on localhost). DNS is resolved at request time
so a rebinding name cannot smuggle a private address past the check.
"""
import ipaddress
import json
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.target import TargetResponse
from app.targets.base import TargetProvider

logger = get_logger("provider.custom_http")


def _is_private_ip(ip) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _host_allowed(url: str) -> bool:
    """True when the endpoint may be called (scheme + SSRF resolution guard)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.lower() in settings.ssrf_allow_private_hosts:
        return True

    try:
        infos = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror:
        return False
    return not any(_is_private_ip(ipaddress.ip_address(info[4][0])) for info in infos)


async def _bounded_content(resp: httpx.Response) -> tuple[bytes, bool]:
    """Read the body with a hard cap so a hostile target cannot OOM us."""
    total = 0
    chunks: List[bytes] = []
    async for chunk in resp.aiter_bytes():
        remaining = settings.MAX_TARGET_RESPONSE_BYTES - total
        if remaining <= 0:
            return b"".join(chunks), True
        chunks.append(chunk[:remaining])
        total += len(chunk[:remaining])
        if total >= settings.MAX_TARGET_RESPONSE_BYTES:
            return b"".join(chunks), True
    return b"".join(chunks), False


class CustomHTTPTargetProvider(TargetProvider):
    """Calls a user-configured HTTP endpoint with the attack payload."""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or settings.TARGET_CALL_TIMEOUT_SECONDS

    async def execute(
        self,
        prompt: str,
        config: Dict[str, Any],
        *,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> TargetResponse:
        endpoint = config.get("endpoint_url") or config.get("url")
        if not endpoint:
            return TargetResponse(
                response_text="",
                latency_ms=0.0,
                error="Custom HTTP target requires an endpoint_url.",
                error_type="config_error",
            )
        headers = dict(config.get("headers_json") or {})
        extra_payload = config.get("request_template_json") or {}

        body: Dict[str, Any] = {"prompt": prompt}
        if messages:
            body["messages"] = messages
        # Operator-provided template keys may supplement (API keys, auth tokens,
        # field names); the prompt/messages keys always win.
        body.update({k: v for k, v in extra_payload.items() if k not in body})

        allowed = _host_allowed(endpoint)
        if not allowed:
            logger.warning(
                "Custom HTTP target blocked by SSRF guard",
                extra={
                    "event_name": "target.ssrf_blocked",
                    "endpoint": endpoint[:200],
                },
            )
            return TargetResponse(
                response_text="",
                latency_ms=0.0,
                error=(
                    "Target endpoint rejected by the SSRF guard: only "
                    "public hosts may be called."
                ),
                error_type="ssrf_guard",
            )

        import time

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                async with client.stream("POST", endpoint, json=body, headers=headers) as resp:
                    content, truncated = await _bounded_content(resp)
                    status_code = resp.status_code
            latency_ms = (time.perf_counter() - start) * 1000.0
            latency_ms = max(latency_ms, 0.001)
        except httpx.HTTPError as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            error_type = type(exc).__name__
            if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
                error_type = "timeout"
            elif isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
                error_type = "connection_error"
            logger.warning(
                "Custom HTTP target call failed",
                extra={
                    "event_name": "target.connection_failed",
                    "endpoint": endpoint[:200],
                    "error_type": error_type,
                },
            )
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error="Custom HTTP target unreachable.",
                error_type=error_type,
            )

        body_text = content.decode("utf-8", errors="replace")
        if truncated:
            logger.warning(
                "Custom HTTP target response exceeded size cap",
                extra={"event_name": "target.response_too_large", "status_code": status_code},
            )
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error=(
                    f"Target response exceeded "
                    f"{settings.MAX_TARGET_RESPONSE_BYTES} byte limit."
                ),
                error_type="response_too_large",
            )

        try:
            data = json.loads(body_text)
        except json.JSONDecodeError:
            logger.warning(
                "Custom HTTP target returned non-JSON response",
                extra={"event_name": "target.non_json", "status_code": status_code},
            )
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error=f"Non-JSON response from target (HTTP {status_code}).",
                error_type="http_error",
                status_code=status_code,
            )

        if status_code is not None and status_code >= 400:
            logger.warning(
                "Custom HTTP target returned error status",
                extra={"event_name": "target.http_error", "status_code": status_code},
            )
            return TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error=(
                    data.get("error")
                    or data.get("message")
                    or f"Target returned HTTP {status_code}."
                ),
                error_type="http_error",
                status_code=status_code,
            )

        text = data.get("response", data.get("text", ""))
        usage = data.get("usage") or {}
        usage_prompt = usage.get("prompt_tokens") or 0
        usage_completion = usage.get("completion_tokens") or 0
        usage_total = usage.get("total_tokens") or (usage_prompt + usage_completion)

        total_tokens = int(usage_total or 0)
        prompt_tokens = int(usage_prompt or 0)
        completion_tokens = int(usage_completion or 0)

        logger.info(
            "Custom HTTP target call succeeded",
            extra={
                "event_name": "target.call_succeeded",
                "status_code": status_code,
                "latency_ms": round(latency_ms, 2),
                "total_tokens": total_tokens,
            },
        )
        return TargetResponse(
            response_text=str(text),
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status_code=status_code,
        )

    async def ping(self, prompt: str, config: Dict[str, Any]) -> TargetResponse:
        """Cheap liveness probe used by the Targets connection test."""
        return await self.execute(prompt, config)
