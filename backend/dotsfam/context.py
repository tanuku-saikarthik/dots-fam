"""Per-run context handed to tools and prompts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Settings
from .db import Store

if TYPE_CHECKING:
    from .computers import ComputerManager
    from .delegation import DelegationManager


class Stopped(RuntimeError):
    """Raised when the owner paused the team or the run was cancelled."""


@dataclass
class WorkerInfo:
    delegation_id: str
    parent_thread_id: str | None
    from_dot: dict[str, Any]


@dataclass
class DotContext:
    store: Store
    settings: Settings
    dot: dict[str, Any]
    thread_id: str
    run_id: str
    emit: Callable[[dict[str, Any]], None] = lambda _event: None
    delegations: DelegationManager | None = None
    computers: ComputerManager | None = None
    worker: WorkerInfo | None = None
    cancelled: Callable[[], bool] = lambda: False
    extra: dict[str, Any] = field(default_factory=dict)
    # owner | slack | voice | routine | trigger | team | delegation | approval
    source: str = "owner"
    counters: dict[str, int] = field(default_factory=dict)  # per-run budgets (e.g. web searches)

    def check(self) -> None:
        if self.store.flags()["paused"]:
            raise Stopped("All Dots are paused.")
        if self.cancelled():
            raise Stopped("This run was stopped.")

    @property
    def reversible(self) -> bool:
        return self.dot.get("approval_mode", "reversible") != "autonomous"

    def log(self, text: str, kind: str = "tool") -> None:
        self.store.add_event(self.thread_id, kind, text, self.run_id)
