"""Application command ledger with replay semantics (STATE_AND_DATA_MODEL.md).

Every mutating application command receives a command ID for correlation and
idempotency. `command_id` identifies one persisted command attempt;
`idempotency_key` identifies a replayable operator intention. Command IDs are
never reused.

Replay results for the same (command_type, idempotency_key, request digest):

- requested/running: in-progress, do not execute again.
- completed: return the stored result without new effects.
- failed before any external effect: return the stored failure.
- uncertain or interrupted after an external effect began: refuse replay.
- missing result_json in a terminal state: storage corruption.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from treepact.enums import CommandState
from treepact.errors import StateConflict
from treepact.storage.repository import Repository


class CommandOutcome:
    """Result of recording an application command.

    `replayed=True` means no new effects were executed.
    """

    def __init__(self, command_id: str, replayed: bool, result: dict[str, Any] | None = None) -> None:
        self.command_id = command_id
        self.replayed = replayed
        self.result = result

    def check_result(self) -> dict[str, Any]:
        if self.replayed:
            return self.result or {}
        raise StateConflict(
            "command was not replayed; finish it before reading a stored result",
            code="command_incomplete",
        )


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def begin_command(
    conn: sqlite3.Connection,
    *,
    command_type: str,
    idempotency_key: str | None,
    actor: str,
    run_id: str | None,
    request: dict[str, Any],
) -> CommandOutcome:
    """Persist `requested` before work. Replay rules apply; the caller must
    mark the command running before any external effect and finish it after."""
    repo = Repository(conn)
    digest = request_sha256(request)
    row = repo.begin_command(
        command_type=command_type,
        idempotency_key=idempotency_key,
        actor=actor,
        run_id=run_id,
        request_sha256=digest,
    )

    if row.get("replay") is not False and "command_id" in row:
        existing = row
        if existing["request_sha256"] != digest:
            raise StateConflict(
                f"idempotency key {idempotency_key!r} was already used with a different request",
                code="idempotency_conflict",
            )
        state = CommandState(existing["state"])
        if state in (CommandState.REQUESTED, CommandState.RUNNING):
            raise StateConflict(
                f"command {existing['command_id']} is still in progress",
                code="command_in_progress",
            )
        if state == CommandState.COMPLETED:
            if existing.get("result_json") is None:
                raise StateConflict(
                    f"command {existing['command_id']} completed without a stored result; "
                    "storage corruption",
                    code="command_missing_result",
                )
            return CommandOutcome(
                command_id=existing["command_id"],
                replayed=True,
                result=json.loads(existing["result_json"]),
            )
        if state == CommandState.FAILED:
            raise StateConflict(
                f"command {existing['command_id']} previously failed ({existing.get('error_code')}); "
                "explicit retry creates a new intention and key",
                code="command_failed_before",
            )
        if state == CommandState.UNCERTAIN:
            raise StateConflict(
                f"command {existing['command_id']} ended uncertain; reconciliation or human "
                "review is required",
                code="command_uncertain",
            )
        raise StateConflict(
            f"command {existing['command_id']} in unexpected state {state.value}",
            code="command_state_conflict",
        )

    return CommandOutcome(command_id=row["command_id"], replayed=False)


def mark_running(conn: sqlite3.Connection, command_id: str) -> None:
    Repository(conn).mark_command_running(command_id)


def finish_command(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    uncertain: bool = False,
) -> None:
    state = CommandState.UNCERTAIN if uncertain else (
        CommandState.FAILED if error_code else CommandState.COMPLETED
    )
    Repository(conn).finish_command(
        command_id, state=state, result=result, error_code=error_code
    )
