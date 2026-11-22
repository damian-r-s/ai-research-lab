"""Registry of event types and their versioned payload schemas.

Every event written to the store must be registered here. Unknown types or payloads that fail
validation are rejected — an unregistered event is a programming error, not data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class EventPayload(BaseModel):
    """Base for payload schemas: immutable, no unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class EventType:
    name: str
    version: int
    payload_model: type[EventPayload]
    description: str


class UnknownEventType(ValueError):
    pass


class InvalidEventPayload(ValueError):
    pass


_REGISTRY: dict[tuple[str, int], EventType] = {}


def register(
    name: str, version: int, payload_model: type[EventPayload], description: str
) -> EventType:
    parts = name.split(".")
    if len(parts) < 2 or not all(p and p.replace("_", "").isalnum() and p.islower() for p in parts):
        raise ValueError(f"event type {name!r} must look like 'domain.entity.verb_past'")
    key = (name, version)
    if key in _REGISTRY and _REGISTRY[key].payload_model is not payload_model:
        raise ValueError(f"event type {name} v{version} already registered")
    et = EventType(name, version, payload_model, description)
    _REGISTRY[key] = et
    return et


def get(name: str, version: int) -> EventType:
    try:
        return _REGISTRY[(name, version)]
    except KeyError:
        raise UnknownEventType(f"unregistered event type {name} v{version}") from None


def all_types() -> list[EventType]:
    return sorted(_REGISTRY.values(), key=lambda e: (e.name, e.version))


def validate_payload(
    event_type: EventType, payload: EventPayload | dict[str, Any]
) -> dict[str, Any]:
    """Validate and normalise a payload to its JSON form (what is stored and hashed)."""
    try:
        if isinstance(payload, EventPayload):
            if not isinstance(payload, event_type.payload_model):
                raise InvalidEventPayload(
                    f"{event_type.name} expects {event_type.payload_model.__name__}, "
                    f"got {type(payload).__name__}"
                )
            model = payload
        else:
            model = event_type.payload_model.model_validate(payload)
    except ValidationError as exc:
        raise InvalidEventPayload(f"invalid payload for {event_type.name}: {exc}") from exc
    return model.model_dump(mode="json")
