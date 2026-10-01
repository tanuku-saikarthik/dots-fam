"""Wires the store, checkpointer, run manager, delegation, scheduler and integrations together."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import Settings
from .db import Store
from .delegation import DelegationManager
from .models import ModelFactory, default_factory
from .runs import RunManager
from .scheduler import Scheduler
from .team import install_team

log = logging.getLogger("dotsfam")


class Runtime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        checkpointer: BaseCheckpointSaver,
        model_factory: ModelFactory = default_factory,
    ):
        self.settings = settings
        self.store = store
        self.checkpointer = checkpointer
        self.runs = RunManager(store, settings, checkpointer, model_factory)
        self.delegations = DelegationManager(store, settings, self.runs, self.follow_up)
        self.runs.delegations = self.delegations
        self.scheduler = Scheduler(store, self.runs, settings.runner_concurrency)
        self.computers: Any = None
        self.slack: Any = None
        self.voice: Any = None
        if not store.dots():
            install_team(store, settings.default_model, settings.worker_model)

    def follow_up(self, thread_id: str, prompt: str) -> None:
        """Queue a server-side turn in a conversation (runs when it is free)."""
        self.store.create_task(thread_id, prompt[:20_000], origin="team")

    def attach_computers(self, manager: Any) -> None:
        self.computers = manager
        self.runs.computers = manager

    def attach_slack(self, slack: Any) -> None:
        self.slack = slack
        self.runs.extra["slack"] = slack

    async def start(self) -> None:
        self.scheduler.start()
        if self.slack:
            await self.slack.start()

    async def stop(self) -> None:
        await self.scheduler.stop()
        self.runs.cancel_all()
        await self.runs.wait_idle(5)
        if self.slack:
            await self.slack.stop()
        if self.computers:
            await self.computers.close()


@contextlib.asynccontextmanager
async def open_runtime(
    settings: Settings, model_factory: ModelFactory = default_factory, memory: bool = False
) -> AsyncIterator[Runtime]:
    if memory:
        store = Store(":memory:")
        runtime = Runtime(settings, store, InMemorySaver(), model_factory)
        try:
            yield runtime
        finally:
            await runtime.stop()
            store.close()
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_path)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as checkpointer:
        runtime = Runtime(settings, store, checkpointer, model_factory)
        try:
            yield runtime
        finally:
            await runtime.stop()
            store.close()
