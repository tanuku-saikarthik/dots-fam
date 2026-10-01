"""Test fixtures: a scripted chat model per Dot, an in-memory runtime, and an HTTP client."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from dotsfam.api import create_app
from dotsfam.config import Settings
from dotsfam.runtime import open_runtime


class Script:
    """Replies per Dot name. Each reply is an AIMessage or a callable(messages) -> AIMessage."""

    def __init__(self) -> None:
        self.replies: dict[str, list[Any]] = defaultdict(list)
        self.calls: dict[str, list[list[Any]]] = defaultdict(list)
        self.models: dict[str, str] = {}
        self.delay = 0.0

    def add(self, dot: str, *replies: Any) -> None:
        self.replies[dot.lower()].extend(replies)


class ScriptedModel(BaseChatModel):
    script: Any
    dot_name: str

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        key = self.dot_name.lower()
        self.script.calls[key].append(list(messages))
        if self.script.delay:
            await asyncio.sleep(self.script.delay)
        queue = self.script.replies[key]
        reply = queue.pop(0) if queue else AIMessage(content=f"{self.dot_name}: done.")
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, str):
            reply = AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name: str, args: dict[str, Any], call_id: str | None = None) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"id": call_id or f"call-{name}", "name": name, "args": args}]
    )


@pytest.fixture
def script() -> Script:
    return Script()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        anthropic_api_key="test",
        openai_api_key="test",
        default_model="anthropic:claude-sonnet-4-5",
        worker_model="openai:gpt-5-mini",
        default_timezone="Asia/Kolkata",
        public_url="https://dots.example.com",
        worker_timeout_seconds=30,
    )


@pytest.fixture
async def runtime(settings, script):
    def factory(_settings, ref, dot):
        script.models[dot["name"].lower()] = str(ref)
        return ScriptedModel(script=script, dot_name=dot["name"])

    async with open_runtime(settings, factory, memory=True) as rt:
        yield rt


@pytest.fixture
async def client(runtime):
    app = create_app(runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def finish(runtime, thread_id: str, seconds: float = 10) -> Any:
    active = runtime.runs.active(thread_id)
    if active is None:
        return None
    return await asyncio.wait_for(active.done, seconds)
