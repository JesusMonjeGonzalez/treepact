"""Ports defined by TreePact: ModelProvider and RuntimeAdapter (ADR 0013).

The domain depends on these protocols; adapters implement them. A provider
returns typed model responses, never raw payloads. Provider fallback never
occurs silently: a failed provider raises ProviderUnavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    ok: bool
    narrative: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def finished_reasoning(self) -> bool:
        """True when the model produced a final narrative with no tool calls."""
        return self.ok and not self.tool_calls


@dataclass
class ProviderHealth:
    available: bool
    detail: str = ""
    runtime_version: str | None = None


class ModelProvider(Protocol):
    provider_id: str

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        profile: str,
        timeout_seconds: float,
    ) -> ModelResponse: ...

    def health(self) -> ProviderHealth: ...


@dataclass
class RuntimeAdapter(Protocol):
    """External runtime adapter contract (INTEGRATION_STRATEGY.md)."""

    adapter_id: str
    adapter_version: str
    assurance_ceiling: str

    def manifest(self) -> dict[str, Any]: ...

    def probe(self) -> dict[str, Any]: ...

    def start_session(self, worktree: str, task: str) -> dict[str, Any]: ...

    def cancel_session(self) -> None: ...

    def close_session(self) -> None: ...
