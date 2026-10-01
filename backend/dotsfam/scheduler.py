"""The clock: runs routines, webhook events and follow-ups when their conversation is free."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .context import Stopped
from .db import Conflict, Store
from .runs import ActiveRun, Busy, RunManager

log = logging.getLogger("dotsfam.scheduler")


class Scheduler:
    def __init__(self, store: Store, runs: RunManager, concurrency: int = 3, interval: float = 2.0):
        self.store = store
        self.runs = runs
        self.concurrency = concurrency
        self.interval = interval
        self._watching: dict[str, asyncio.Task] = {}
        self._loop: asyncio.Task | None = None

    def start(self) -> None:
        if not self._loop:
            self._loop = asyncio.create_task(self._forever(), name="scheduler")

    async def stop(self) -> None:
        if self._loop:
            self._loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop
            self._loop = None

    async def _forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Scheduler tick failed")
            await asyncio.sleep(self.interval)

    async def tick(self, at: int | None = None) -> list[ActiveRun]:
        started: list[ActiveRun] = []
        if self.store.flags()["paused"]:
            return started
        for task in self.store.due_tasks(at):
            if len(self._watching) >= self.concurrency:
                break
            if not self.runs.available(task["thread_id"]):
                continue
            claimed = self.store.claim_task(task["id"])
            if not claimed:
                continue
            try:
                active = await self.runs.send(
                    task["thread_id"],
                    task["prompt"],
                    source=task["origin"] or "routine",
                    task_id=task["id"],
                )
            except (Busy, Conflict, Stopped):
                self.store.release_task(task["id"])
                continue
            except Exception as error:  # noqa: BLE001
                self.store.finish_task(task["id"], error=str(error)[:500])
                continue
            self._watching[task["id"]] = asyncio.create_task(self._watch(task["id"], active))
            started.append(active)
        return started

    async def _watch(self, task_id: str, active: ActiveRun) -> None:
        try:
            outcome = await active.done
            error = None
            if outcome.status in ("failed", "cancelled"):
                error = outcome.error or outcome.status
            self.store.finish_task(task_id, error=error)
        finally:
            self._watching.pop(task_id, None)
