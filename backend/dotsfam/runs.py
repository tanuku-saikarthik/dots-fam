"""Run manager: executes Dot turns in the background and streams their events.

Every turn (owner chat, routine, webhook, delegated brief, approval resume) goes
through here, so they share the same rules: one run per conversation at a time,
global pause, timeouts, and an event stream any client can attach to.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .agent import build_graph, final_text
from .config import Settings
from .context import DotContext, Stopped, WorkerInfo
from .db import Conflict, Store
from .models import ModelFactory, default_factory, resolve
from .tools import build_tools

log = logging.getLogger("dotsfam.runs")


class Busy(RuntimeError):
    pass


@dataclass
class RunOutcome:
    run_id: str
    thread_id: str
    status: str  # completed | waiting_approval | failed | cancelled
    text: str = ""
    error: str | None = None


@dataclass
class ActiveRun:
    run_id: str
    thread_id: str
    source: str
    done: asyncio.Future
    events: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    task: asyncio.Task | None = None


class RunManager:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        checkpointer: BaseCheckpointSaver,
        model_factory: ModelFactory = default_factory,
    ):
        self.store = store
        self.settings = settings
        self.checkpointer = checkpointer
        self.model_factory = model_factory
        self.delegations = None
        self.computers = None
        self.extra: dict[str, Any] = {}
        self._active: dict[str, ActiveRun] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._listeners: list[Callable[[RunOutcome], Any]] = []

    # ---- observation -------------------------------------------------------
    def on_finished(self, listener: Callable[[RunOutcome], Any]) -> None:
        self._listeners.append(listener)

    def off_finished(self, listener: Callable[[RunOutcome], Any]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def busy(self, thread_id: str) -> bool:
        return thread_id in self._active

    def available(self, thread_id: str) -> bool:
        return not self.busy(thread_id) and self.store.pending_approvals(thread_id) == 0

    def active(self, thread_id: str) -> ActiveRun | None:
        return self._active.get(thread_id)

    def active_runs(self) -> list[ActiveRun]:
        return list(self._active.values())

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subscribers[thread_id].add(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[thread_id].discard(queue)

    def _publish(self, thread_id: str, event: dict[str, Any]) -> None:
        active = self._active.get(thread_id)
        if active:
            event = {**event, "run_id": active.run_id}
            active.events.append(event)
        for queue in list(self._subscribers.get(thread_id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    # ---- starting runs -----------------------------------------------------
    async def send(
        self, thread_id: str, text: str, *, source: str = "owner", task_id: str | None = None
    ) -> ActiveRun:
        if self.store.pending_approvals(thread_id):
            raise Conflict("Decide the pending approval in this conversation first.")
        thread = self.store.thread(thread_id)
        if source == "owner" and thread["title"] in ("New conversation", ""):
            self.store.touch_thread(thread_id, text.strip().splitlines()[0][:80])
        message = HumanMessage(text, additional_kwargs={"dotsfam_source": source})
        return self._start(thread_id, {"messages": [message]}, source, task_id)

    async def resume(
        self, thread_id: str, decisions: dict[str, Any], *, source: str = "approval"
    ) -> ActiveRun:
        return self._start(thread_id, Command(resume=decisions), source, None)

    def _start(self, thread_id: str, payload: Any, source: str, task_id: str | None) -> ActiveRun:
        if self.store.flags()["paused"]:
            raise Stopped("All Dots are paused.")
        if thread_id in self._active:
            raise Busy("This Dot is still working in this conversation.")
        run = self.store.create_run(thread_id, source, task_id)
        active = ActiveRun(run["id"], thread_id, source, asyncio.get_running_loop().create_future())
        self._active[thread_id] = active
        active.task = asyncio.create_task(self._execute(active, payload), name=f"run:{run['id']}")
        return active

    def _context(self, active: ActiveRun) -> DotContext:
        thread = self.store.thread(active.thread_id)
        dot = self.store.dot(thread["dot_id"])
        worker = None
        delegation = self.store.delegation_for_worker(active.thread_id)
        if delegation:
            worker = WorkerInfo(
                delegation_id=delegation["id"],
                parent_thread_id=delegation["parent_thread_id"],
                from_dot=self.store.dot(delegation["from_dot_id"]),
            )
        return DotContext(
            store=self.store,
            settings=self.settings,
            dot=dot,
            thread_id=active.thread_id,
            run_id=active.run_id,
            emit=lambda event: self._publish(active.thread_id, event),
            delegations=self.delegations,
            computers=self.computers,
            worker=worker,
            cancelled=lambda: active.cancelled,
            extra=self.extra,
            source=active.source,
        )

    async def _execute(self, active: ActiveRun, payload: Any) -> None:
        thread_id = active.thread_id
        outcome = RunOutcome(active.run_id, thread_id, "failed")
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
        try:
            ctx = self._context(active)
            ref = resolve(self.settings, ctx.dot.get("model"))
            model = self.model_factory(self.settings, ref, ctx.dot)
            graph = build_graph(ctx, model, build_tools(ctx), self.checkpointer)
            self._publish(
                thread_id,
                {
                    "type": "run.started",
                    "dot_id": ctx.dot["id"],
                    "model": str(ref),
                    "source": active.source,
                },
            )
            approvals: list[dict[str, Any]] = []
            async with asyncio.timeout(self.settings.task_timeout_seconds):
                async for mode, chunk in graph.astream(
                    payload, config, stream_mode=["messages", "updates", "custom"]
                ):
                    if mode == "messages":
                        message, meta = chunk
                        # Streaming models yield chunks; non-streaming ones yield the whole message once.
                        if meta.get("langgraph_node") == "agent" and isinstance(message, AIMessage):
                            text = message.text if isinstance(message.text, str) else message.text()
                            if text:
                                self._publish(
                                    thread_id,
                                    {"type": "message.delta", "id": message.id, "delta": text},
                                )
                    elif mode == "custom":
                        self._publish(thread_id, chunk)
                    elif mode == "updates":
                        approvals += self._handle_update(ctx, chunk)
            state = await graph.aget_state(config)
            if approvals or state.interrupts:
                outcome.status = "waiting_approval"
                self.store.add_event(
                    thread_id,
                    "approval",
                    f"Waiting for approval: {len(approvals)} action(s)",
                    active.run_id,
                )
            else:
                outcome.status = "completed"
                outcome.text = final_text(state.values.get("messages", []))
        except (Stopped, asyncio.CancelledError) as error:
            outcome.status = "cancelled"
            outcome.error = str(error) or "Stopped."
        except TimeoutError:
            outcome.error = f"Timed out after {self.settings.task_timeout_seconds} seconds."
        except Exception as error:  # noqa: BLE001
            log.exception("Run %s failed", active.run_id)
            outcome.error = f"{type(error).__name__}: {error}"[:600]
        finally:
            self.store.finish_run(
                active.run_id, outcome.status, outcome.text or None, outcome.error
            )
            self.store.touch_thread(thread_id)
            self._publish(
                thread_id,
                {
                    "type": "run.finished",
                    "status": outcome.status,
                    "text": outcome.text,
                    "error": outcome.error,
                },
            )
            self._active.pop(thread_id, None)
            # Listeners (delegation bookkeeping) run before waiters wake up.
            for listener in self._listeners:
                try:
                    result = listener(outcome)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001
                    log.exception("Run listener failed")
            if not active.done.done():
                active.done.set_result(outcome)

    def _handle_update(self, ctx: DotContext, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for node, value in chunk.items():
            if node == "__interrupt__":
                for item in value:
                    payload = getattr(item, "value", None) or {}
                    if payload.get("kind") != "approval":
                        continue
                    rows = self.store.create_approvals(
                        ctx.thread_id, ctx.dot["id"], payload["items"]
                    )
                    created += rows
                    self._publish(ctx.thread_id, {"type": "approval.requested", "approvals": rows})
            elif node == "agent" and value:
                for message in value.get("messages", []):
                    if isinstance(message, AIMessage) and message.tool_calls:
                        self._publish(
                            ctx.thread_id,
                            {
                                "type": "tool.planned",
                                "calls": [c["name"] for c in message.tool_calls],
                            },
                        )
            elif node == "ask" and value:
                for message in value.get("messages", []):
                    if isinstance(message, ToolMessage):
                        self._publish(
                            ctx.thread_id,
                            {
                                "type": "tool.declined",
                                "id": message.tool_call_id,
                                "name": message.name,
                            },
                        )
        return created

    # ---- control -----------------------------------------------------------
    def cancel(self, thread_id: str) -> bool:
        active = self._active.get(thread_id)
        if not active:
            return False
        active.cancelled = True
        if active.task:
            active.task.cancel()
        return True

    def cancel_all(self) -> None:
        for thread_id in list(self._active):
            self.cancel(thread_id)

    async def wait_idle(self, seconds: float = 10) -> None:
        tasks = [active.task for active in self._active.values() if active.task]
        if tasks:
            await asyncio.wait(tasks, timeout=seconds)

    # ---- history -----------------------------------------------------------
    async def history(self, thread_id: str) -> list[dict[str, Any]]:
        snapshot = await self.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        if not snapshot:
            return []
        messages = snapshot.checkpoint.get("channel_values", {}).get("messages", [])
        return [serialize(message) for message in messages]


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content or "")


def serialize(message: Any) -> dict[str, Any]:
    if isinstance(message, HumanMessage):
        return {
            "id": message.id,
            "role": "user",
            "text": _text(message.content),
            "source": message.additional_kwargs.get("dotsfam_source", "owner"),
        }
    if isinstance(message, AIMessage):
        return {
            "id": message.id,
            "role": "assistant",
            "text": _text(message.content),
            "tool_calls": [
                {"id": c["id"], "name": c["name"], "args": c.get("args", {})}
                for c in message.tool_calls
            ],
        }
    if isinstance(message, ToolMessage):
        return {
            "id": message.id,
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "ok": getattr(message, "status", "success") != "error",
            "text": _text(message.content)[:2000],
        }
    return {
        "id": getattr(message, "id", None),
        "role": "system",
        "text": _text(getattr(message, "content", "")),
    }
