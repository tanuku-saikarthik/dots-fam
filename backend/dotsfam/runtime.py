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
from .context import Stopped
from .db import Conflict, Store
from .delegation import DelegationManager
from .models import ModelFactory, default_factory
from .runs import Busy, RunManager
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
        self.notifier: Any = None
        if not store.dots():
            install_team(store, settings.default_model, settings.worker_model)

    def follow_up(self, thread_id: str, prompt: str) -> None:
        """Queue a server-side turn in a conversation (runs when it is free)."""
        self.store.create_task(thread_id, prompt[:20_000], origin="team")

    async def decide(
        self, approval_id: str, decision: str, note: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Record an owner decision; resume the paused run once its whole batch is decided.

        The last decision in a batch is only recorded if the run can resume right now, and
        it is undone if the resume fails, so a conversation is never left half-decided.
        """
        approval = self.store.approval(approval_id)
        if approval["status"] != "pending":
            raise Conflict("This approval was already decided.")
        batch = self.store.approvals(batch_id=approval["batch_id"])
        last = all(item["status"] != "pending" or item["id"] == approval_id for item in batch)
        if last:
            if self.store.flags()["paused"]:
                raise Stopped("The team is paused. Resume it, then decide.")
            if self.runs.busy(approval["thread_id"]):
                raise Busy("This Dot is still finishing up. Try again in a moment.")
        approval = self.store.decide_approval(approval_id, decision, note)
        if not last:
            return approval, False
        batch = self.store.approvals(batch_id=approval["batch_id"])
        decisions = {
            item["tool_call_id"]: {"approved": item["status"] == "approved", "note": item["note"]}
            for item in batch
        }
        delegation = self.store.delegation_for_worker(approval["thread_id"])
        if delegation:
            self.store.set_delegation(delegation["id"], "running")
        try:
            await self.runs.resume(approval["thread_id"], decisions)
        except Exception:
            self.store.reopen_approvals(approval["batch_id"])
            if delegation:
                self.store.set_delegation(delegation["id"], "waiting_approval")
            raise
        return approval, True

    def root_thread(self, thread_id: str) -> str:
        """The owner-facing conversation a specialist's internal work belongs to."""
        for _ in range(6):
            delegation = self.store.delegation_for_worker(thread_id)
            if not delegation or not delegation["parent_thread_id"]:
                break
            thread_id = delegation["parent_thread_id"]
        return thread_id

    def attach_computers(self, manager: Any) -> None:
        self.computers = manager
        self.runs.computers = manager

    def attach_notifier(self, notifier: Any) -> None:
        self.notifier = notifier

    def attach_voice(self, voice: Any) -> None:
        self.voice = voice

    def attach_slack(self, slack: Any) -> None:
        self.slack = slack
        self.runs.extra["slack"] = slack

    async def start(self) -> None:
        self.scheduler.start()
        if self.slack:
            try:
                await self.slack.start()
            except Exception:  # noqa: BLE001 - the web app keeps working without Slack
                log.exception("Slack did not connect; check SLACK_BOT_TOKEN and SLACK_APP_TOKEN")
                self.slack = None

    async def stop(self) -> None:
        await self.scheduler.stop()
        self.runs.cancel_all()
        await self.runs.wait_idle(5)
        if self.slack:
            await self.slack.stop()
        if self.computers:
            await self.computers.close()
        if self.voice:
            await self.voice.close()


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
