"""Bounded child process execution (SECURITY_MODEL.md process controls).

Every child runs in its own process group, has a mandatory wall-clock
timeout, bounded output capture, and cooperative cancellation. Cancellation
targets only the recorded process group. Output truncation is explicit and
never appears complete.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_CAPTURED_BYTES = 4 * 1024 * 1024
TRUNCATION_MARKER = b"\n...[output truncated by TreePact]\n"
GRACE_SECONDS = 5


@dataclass
class ProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False
    duration_ms: int = 0
    pid: int | None = None
    pgid: int | None = None
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    error_code: str | None = None

    @property
    def success(self) -> bool:
        return not self.timed_out and not self.cancelled and self.returncode == 0


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    token: CancellationToken | None = None,
    on_group_start: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    """Execute fixed argv in a new process group, capture bounded output, and
    enforce timeout and cancellation on the recorded group only."""
    token = token or CancellationToken()
    started = time.monotonic()
    stdout_pipe = _BoundedReader(MAX_CAPTURED_BYTES)
    stderr_pipe = _BoundedReader(MAX_CAPTURED_BYTES)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        return ProcessResult(
            returncode=None,
            stdout=b"",
            stderr=f"executable not found: {argv[0]}".encode(),
            error_code="executable_not_found",
        )
    except PermissionError:
        return ProcessResult(
            returncode=None,
            stdout=b"",
            stderr=f"permission denied: {argv[0]}".encode(),
            error_code="executable_denied",
        )
    except OSError as exc:
        return ProcessResult(
            returncode=None,
            stdout=b"",
            stderr=f"cannot start process: {exc}".encode(),
            error_code="process_start_failed",
        )

    pgid = proc.pid
    if on_group_start is not None:
        on_group_start(proc.pid, pgid)

    timed_out = False
    cancelled = False
    deadline = started + timeout_seconds
    while proc.poll() is None:
        if token.cancelled:
            cancelled = True
            _terminate_group(pgid, signal.SIGTERM)
        elif time.monotonic() >= deadline:
            timed_out = True
            _terminate_group(pgid, signal.SIGTERM)
        else:
            _drain(stdout_pipe, proc.stdout, stderr_pipe, proc.stderr)
            time.sleep(0.02)

    if timed_out or cancelled:
        _drain(stdout_pipe, proc.stdout, stderr_pipe, proc.stderr)
        grace_deadline = time.monotonic() + GRACE_SECONDS
        while proc.poll() is None and time.monotonic() < grace_deadline:
            time.sleep(0.02)
        if proc.poll() is None:
            _terminate_group(pgid, signal.SIGKILL)
    _drain(stdout_pipe, proc.stdout, stderr_pipe, proc.stderr)
    if proc.stdout:
        proc.stdout.close()
    if proc.stderr:
        proc.stderr.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate_group(pgid, signal.SIGKILL)
        proc.wait()

    return ProcessResult(
        returncode=proc.returncode,
        stdout=stdout_pipe.data,
        stderr=stderr_pipe.data,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_ms=int((time.monotonic() - started) * 1000),
        pid=proc.pid,
        pgid=pgid,
        truncated_stdout=stdout_pipe.truncated,
        truncated_stderr=stderr_pipe.truncated,
    )


def _drain(stdout_pipe: _BoundedReader, stdout: object, stderr_pipe: _BoundedReader, stderr: object) -> None:
    import select

    if stdout is not None and stderr is not None:
        readable, _, _ = select.select([stdout, stderr], [], [], 0)
        for stream in readable:
            chunk = stream.read(65536) if hasattr(stream, "read") else b""
            if chunk:
                if stream is stdout:
                    stdout_pipe.feed(chunk)
                else:
                    stderr_pipe.feed(chunk)


def _terminate_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class _BoundedReader:
    """Bounded byte accumulation with explicit truncation marker."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self.data = b""
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        if self.truncated:
            return
        remaining = self._limit - len(self.data)
        if len(chunk) >= remaining:
            self.data += chunk[:remaining]
            self.data += TRUNCATION_MARKER
            self.truncated = True
        else:
            self.data += chunk
