"""Stable error taxonomy with operator-safe messages (TECHNICAL_ARCHITECTURE.md)."""

from __future__ import annotations

from typing import Any


class TreePactError(Exception):
    """Base error carrying a stable code, operator message, run correlation ID,
    retryability, and safe details.

    Raw provider payloads and environment values are never included in
    details. Error codes are stable identifiers, not human sentences.
    """

    exit_code: int = 21
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or _derive_code(type(self).__name__)
        self.run_id = run_id
        self.details = dict(details or {})

    def with_run_id(self, run_id: str) -> TreePactError:
        self.run_id = run_id
        return self


def _derive_code(class_name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(class_name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


class ConfigurationError(TreePactError):
    exit_code = 10


class PactValidationError(TreePactError):
    exit_code = 11


class RepositoryError(TreePactError):
    exit_code = 12


class WorkspaceError(TreePactError):
    exit_code = 12


class PolicyDenied(TreePactError):
    exit_code = 13


class CheckFailed(TreePactError):
    exit_code = 16


class RuntimeError(TreePactError):  # noqa: A001 - taxonomy name from the architecture
    exit_code = 15


class ProviderUnavailable(TreePactError):
    exit_code = 15
    retryable = True


class ResourceUnavailable(TreePactError):
    exit_code = 14
    retryable = True


class CancellationError(TreePactError):
    exit_code = 18


class EvidenceError(TreePactError):
    exit_code = 20


class StateConflict(TreePactError):
    exit_code = 19


class InfrastructureError(TreePactError):
    exit_code = 21


class NotYetImplemented(InfrastructureError):
    """Honest failure for CLI surfaces planned in a later milestone.

    A milestone stub must never pretend to work. This error reports the
    planned milestone and returns the infrastructure exit code.
    """

    def __init__(self, command: str, milestone: str) -> None:
        super().__init__(
            f"command `{command}` is not implemented yet (planned in milestone {milestone})",
            code="not_implemented",
        )


def exit_code_for(exc: Exception) -> int:
    if isinstance(exc, TreePactError):
        return exc.exit_code
    return 21
