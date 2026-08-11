"""Minimal non-streaming OpenAI-compatible HTTP provider (HTTPX).

Initial implementation is non-streaming to simplify cancellation, logging,
redaction, and reproducibility. Malformed tool calls fail closed; provider
errors map to explicit codes. There is no silent provider fallback.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from treepact.errors import ConfigurationError, ProviderUnavailable
from treepact.ports.provider import ModelResponse, ProviderHealth, ToolCall

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def require_loopback_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise ConfigurationError(
            f"provider endpoint {endpoint!r} is not http(s)", code="provider_endpoint_invalid"
        )
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigurationError(
            f"provider endpoint {endpoint!r} is not loopback; remote egress is disabled",
            code="provider_endpoint_not_loopback",
        )
    return endpoint


class OpenAICompatibleProvider:
    provider_id = "openai-compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        profiles: dict[str, str] | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._endpoint = require_loopback_endpoint(endpoint.rstrip("/"))
        self._profiles = dict(profiles or {})
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = httpx.Client(
            base_url=self._endpoint,
            timeout=httpx.Timeout(timeout_seconds, connect=5),
            headers={"Authorization": f"Bearer {self._api_key}"} if api_key else {},
        )

    def close(self) -> None:
        self._client.close()

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        profile: str,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self._profiles.get(profile, "default"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            response = self._client.post(
                "/chat/completions",
                json=body,
                timeout=timeout_seconds or self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(
                "model provider timed out", code="provider_timeout", details={"seconds": self._timeout}
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(
                "model provider is unreachable", code="provider_unreachable"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"model provider request failed: {type(exc).__name__}", code="provider_http_error"
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderUnavailable(
                "model provider rejected credentials", code="provider_auth_failed"
            )
        if response.status_code == 429:
            raise ProviderUnavailable(
                "model provider is rate limited", code="provider_rate_limited"
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                f"model provider failed ({response.status_code})", code="provider_server_error"
            )
        if response.status_code != 200:
            raise ProviderUnavailable(
                f"model provider returned {response.status_code}", code="provider_http_status"
            )

        return self._parse(response.json())

    def _parse(self, payload: dict[str, Any]) -> ModelResponse:
        try:
            choices = payload["choices"]
            message = choices[0]["message"]
        except (KeyError, IndexError, TypeError):
            return ModelResponse(ok=False, error_code="provider_payload_invalid")
        narrative = message.get("content")
        if narrative is not None and not isinstance(narrative, str):
            return ModelResponse(ok=False, error_code="provider_payload_invalid")
        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            try:
                call_id = str(raw["id"])
                name = str(raw["function"]["name"])
                arguments = json.loads(raw["function"]["arguments"])
            except (KeyError, TypeError, json.JSONDecodeError):
                return ModelResponse(ok=False, error_code="malformed_tool_call")
            if not isinstance(arguments, dict):
                return ModelResponse(ok=False, error_code="malformed_tool_call")
            tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        usage = payload.get("usage")
        if usage is not None and not isinstance(usage, dict):
            usage = None
        return ModelResponse(ok=True, narrative=narrative, tool_calls=tool_calls, usage=usage)

    def health(self) -> ProviderHealth:
        try:
            response = self._client.get("/models", timeout=5)
        except httpx.HTTPError:
            return ProviderHealth(available=False, detail="unreachable")
        if response.status_code == 200:
            return ProviderHealth(available=True, detail="ok")
        return ProviderHealth(available=False, detail=f"http {response.status_code}")
