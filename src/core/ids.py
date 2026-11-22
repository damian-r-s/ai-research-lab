"""Identifier generation.

UUIDv7 (RFC 9562) is used for primary keys: globally unique, generated without a DB round trip
and roughly time-ordered, which keeps B-tree indexes compact. Python 3.13 has no uuid.uuid7().
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """Return a UUIDv7. Monotonic within a process (12-bit counter in rand_a for same-ms ids)."""
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            _counter += 1
            if _counter > 0xFFF:  # counter exhausted within one millisecond: borrow the next ms
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        else:
            _last_ms = ms
            _counter = 0
        counter = _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version
    value |= counter << 64
    value |= 0b10 << 62  # RFC 4122/9562 variant
    value |= rand_b
    return uuid.UUID(int=value)
