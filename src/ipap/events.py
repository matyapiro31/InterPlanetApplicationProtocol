"""A mission-time-stamped event log shared by the node, ground control and simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .link import format_mission_time


@dataclass
class Event:
    time: float
    actor: str
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{format_mission_time(self.time)} [{self.actor:<5}] {self.text}"


class EventLog:
    def __init__(self, clock: Callable[[], float],
                 echo: Callable[[str], Any] | None = None) -> None:
        self.clock = clock
        self.echo = echo
        self.events: list[Event] = []

    def __call__(self, actor: str, kind: str, text: str) -> Event:
        event = Event(self.clock(), actor, kind, text)
        self.events.append(event)
        if self.echo:
            self.echo(str(event))
        return event

    def kinds(self, actor: str | None = None) -> list[str]:
        return [e.kind for e in self.events if actor is None or e.actor == actor]

    def find(self, kind: str, actor: str | None = None) -> list[Event]:
        return [e for e in self.events if e.kind == kind and (actor is None or e.actor == actor)]
