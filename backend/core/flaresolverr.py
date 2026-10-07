from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class FlareSolverrError(Exception):
    pass


@dataclass(frozen=True)
class FlareSolverrConfig:
    enabled: bool = False
    endpoint: str = "http://localhost:8191/v1"
    timeout_ms: int = 60000
    strict: bool = False
    domains: tuple[str, ...] = ()

    def should_route(self, request: httpx.Request) -> bool:
        if not self.enabled:
            return False
        if request.method.upper() != "GET":
            return False
        host = (request.url.host or "").strip().lower()
        if not host:
            return False
        if not self.domains:
            return True
        for domain in self.domains:
            d = domain.strip().lower()
            if not d:
                continue
            if host == d or host.endswith(f".{d}"):
                return True
        return False


class FlareSolverrTransport:
    def __init__(
        self,
        config: FlareSolverrConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    @property
    def strict(self) -> bool:
        return self._config.strict

    def should_route(self, request: httpx.Request) -> bool:
        return self._config.should_route(request)

    async def send(self, request: httpx.Request) -> httpx.Response:
        timeout_seconds = max(float(self._config.timeout_ms) / 1000.0 + 5.0, 10.0)
        client_kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": str(request.url),
            "maxTimeout": int(self._config.timeout_ms),
        }
        user_agent = request.headers.get("user-agent")
        if user_agent:
            payload["userAgent"] = user_agent
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(self._config.endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise FlareSolverrError("FlareSolverr request timed out") from exc
        except httpx.HTTPError as exc:
            raise FlareSolverrError("FlareSolverr request failed") from exc
        if response.status_code >= 400:
            raise FlareSolverrError(f"FlareSolverr returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise FlareSolverrError("FlareSolverr returned malformed JSON") from exc
        if not isinstance(body, dict):
            raise FlareSolverrError("FlareSolverr returned malformed JSON")
        if body.get("status") != "ok":
            message = body.get("message")
            if not isinstance(message, str) or not message.strip():
                message = "FlareSolverr request failed"
            raise FlareSolverrError(message)
        solution = body.get("solution")
        if not isinstance(solution, dict):
            raise FlareSolverrError("FlareSolverr response missing solution")
        status_code = solution.get("status")
        if not isinstance(status_code, int):
            raise FlareSolverrError("FlareSolverr response missing status")
        content = solution.get("response")
        if not isinstance(content, str):
            raise FlareSolverrError("FlareSolverr response missing body")
        headers = solution.get("headers")
        header_map: dict[str, str] = {}
        if isinstance(headers, dict):
            for key, value in headers.items():
                if isinstance(key, str) and isinstance(value, str):
                    header_map[key] = value
        return httpx.Response(
            status_code=status_code,
            content=content.encode("utf-8"),
            headers=header_map,
            request=request,
        )


flaresolverr_config = FlareSolverrConfig(
    enabled=settings.flaresolverr_enabled,
    endpoint=settings.flaresolverr_endpoint,
    timeout_ms=settings.flaresolverr_timeout_ms,
    strict=settings.flaresolverr_strict,
    domains=tuple(settings.flaresolverr_domains),
)
