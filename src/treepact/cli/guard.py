"""Error-to-exit-code guard shared by all Typer command surfaces."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TypeVar

import typer

from treepact.errors import TreePactError, exit_code_for

F = TypeVar("F", bound=Callable[..., object])


def guarded[F: Callable[..., object]](fn: F) -> F:
    """Map TreePact errors to stable exit codes and safe operator messages."""

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return fn(*args, **kwargs)
        except TreePactError as exc:
            _fail(exc)
            return None  # pragma: no cover - _fail raises
        except typer.Exit:
            raise
        except Exception as exc:  # noqa: BLE001 - last-resort safety net
            typer.echo(
                f"error: [infrastructure_error] unexpected failure: {type(exc).__name__}: {exc}",
                err=True,
            )
            raise typer.Exit(21) from exc

    return wrapper  # type: ignore[return-value]


def _fail(exc: TreePactError) -> None:
    line = f"error: [{exc.code}] {exc.message}"
    if exc.run_id:
        line += f" (run {exc.run_id})"
    typer.echo(line, err=True)
    raise typer.Exit(exit_code_for(exc))
