"""Event parsing and validation (stub).

parse_event turns a raw JSON-style dict into an Event, or raises MalformedEvent.
Raw shape: {"type": str, "item_id": str, "at": ISO-8601 with offset,
            "actor": role value, "payload": dict}
"""
from __future__ import annotations

from typing import Any

from .contracts import Event


def parse_event(raw: Any) -> Event:
    raise NotImplementedError("events.parse_event is not built yet")
