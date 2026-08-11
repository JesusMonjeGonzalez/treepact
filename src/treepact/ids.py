"""Sortable random identifier generation.

STATE_AND_DATA_MODEL.md requires UUIDv7 or another sortable random
identifier selected in an ADR. Python 3.12 has no stdlib uuid7(), so the
RFC 9562 UUIDv7 layout is built explicitly: 48-bit unix milliseconds, then
version/variant bits with 74 random bits. IDs are returned in canonical
lowercase hex form and are unique per process and across restarts.
"""

from __future__ import annotations

import os
import time


def uuid7() -> str:
    now_ms = int(time.time() * 1000)
    raw = bytearray(now_ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    h = raw.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def new_id(prefix: str) -> str:
    """Prefixed human-carryable ID such as `run_01jx...`."""
    return f"{prefix}_{uuid7().replace('-', '')}"
