"""UTC clock and RFC 3339 serialization.

All persisted timestamps are UTC and RFC 3339. Milliseconds precision keeps
event sequence ordering legible while avoiding microsecond noise.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def rfc3339(dt: datetime | None = None) -> str:
    """RFC 3339 UTC string, e.g. `2026-08-11T12:00:00.123Z`."""
    moment = (dt or utc_now()).astimezone(UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
