"""Loopback provider adapter (ADR 0010).

Loopback is integrated only through its documented OpenAI-compatible loopback
endpoint (default 127.0.0.1:9292). TreePact never imports Loopback code,
reads its database, or requires it for validation and evidence workflows.
Model profiles (classify, fast-code, deep-code) are the domain vocabulary;
model IDs live only in configuration.
"""

from __future__ import annotations

from treepact.adapters.http_provider import OpenAICompatibleProvider
from treepact.config import Config
from treepact.ports.provider import ProviderHealth

LOOPBACK_DEFAULT_ENDPOINT = "http://127.0.0.1:9292/v1"

PROFILE_IDS = ("classify", "fast-code", "deep-code")


class LoopbackProvider(OpenAICompatibleProvider):
    provider_id = "loopback"

    def __init__(self, cfg: Config) -> None:
        endpoint = (cfg.provider.endpoint or LOOPBACK_DEFAULT_ENDPOINT)
        profiles = getattr(cfg.provider, "profiles", None) or {}
        super().__init__(
            endpoint=endpoint,
            profiles=profiles,
            timeout_seconds=cfg.provider.timeout_seconds,
        )

    def health(self) -> ProviderHealth:
        base = super().health()
        if base.available:
            return ProviderHealth(
                available=True,
                detail=f"ok at {self._endpoint}",
            )
        return ProviderHealth(
            available=False,
            detail=f"unavailable at {self._endpoint}",
        )
