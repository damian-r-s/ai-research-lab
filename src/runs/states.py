"""The run status machine. Every allowed transition is listed; anything else is a conflict.

    queued ──► running ──► completed | failed
      │          │ ├──► paused (drained after a pause request)
      │          │ ├──► awaiting_approval (interrupt)
      │          │ └──► cancel_requested ──► stopped (runner confirms) | completed | failed
      ├──► paused ──► queued (resume) | stopped
      └──► stopped
    awaiting_approval ──► queued (answered) | stopped

`completed`, `failed` and `stopped` are terminal.
"""

from __future__ import annotations

from typing import Final, Literal

RunStatus = Literal[
    "queued",
    "running",
    "paused",
    "awaiting_approval",
    "cancel_requested",
    "stopped",
    "completed",
    "failed",
]

TERMINAL: Final[frozenset[str]] = frozenset({"stopped", "completed", "failed"})

TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "queued": frozenset({"running", "paused", "stopped", "failed"}),
    "running": frozenset(
        {"paused", "awaiting_approval", "cancel_requested", "completed", "failed"}
    ),
    "paused": frozenset({"queued", "stopped"}),
    "awaiting_approval": frozenset({"queued", "stopped"}),
    # The run may finish on its own before the runner sees the stop request.
    "cancel_requested": frozenset({"stopped", "completed", "failed"}),
    "stopped": frozenset(),
    "completed": frozenset(),
    "failed": frozenset(),
}

# Statuses in which no runner holds the run: a stop takes effect immediately.
IDLE: Final[frozenset[str]] = frozenset({"queued", "paused", "awaiting_approval"})


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, frozenset())
