"""Exa web search for every Dot, against a fake Exa API."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import ToolMessage

from dotsfam.context import DotContext
from dotsfam.tools import build_tools

from .conftest import call, finish

SEARCH = {
    "requestId": "r1",
    "results": [
        {
            "id": "a",
            "title": "Example Labs is hiring AI engineers",
            "url": "https://example.com/careers",
            "publishedDate": "2026-09-24T00:00:00.000Z",
            "author": None,
            "highlights": ["Senior AI Engineer, Agents. Bengaluru. Posted 24 Sep 2026."],
        }
    ],
    "costDollars": {"total": 0.005},
}
CONTENTS = {
    "results": [
        {
            "id": "https://example.com",
            "url": "https://example.com",
            "title": "Example Labs",
            "text": "We build agent infrastructure. " * 40,
            "subpages": [
                {
                    "url": "https://example.com/careers",
                    "title": "Careers",
                    "text": "2 open AI roles.",
                }
            ],
        }
    ],
    "statuses": [
        {"id": "https://example.com", "status": "success", "source": "crawled"},
        {"id": "https://gone.example", "status": "error", "error": {"tag": "CRAWL_NOT_FOUND"}},
    ],
    "costDollars": {"total": 0.002},
}
ANSWER = {
    "answer": "Example Labs has 2 open AI roles in Bengaluru.",
    "citations": [
        {"title": "Careers", "url": "https://example.com/careers", "publishedDate": "2026-09-24"}
    ],
    "costDollars": {"total": 0.005},
}


class FakeExa:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        body = {"/search": SEARCH, "/contents": CONTENTS, "/answer": ANSWER}[request.url.path]
        return httpx.Response(200, json=body)

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


@pytest.fixture
def exa(runtime, settings):
    settings.exa_api_key = "exa-test-key"
    fake = FakeExa()
    runtime.runs.extra["http_transport"] = httpx.MockTransport(fake)
    return fake


def tool_names(runtime, name: str) -> set[str]:
    dot = runtime.store.dot_by_name(name)
    ctx = DotContext(runtime.store, runtime.settings, dot, "t", "r")
    return {tool.name for tool in build_tools(ctx)}


def tool_result(script, dot: str, name: str) -> dict:
    for message in reversed(script.calls[dot][-1]):
        if isinstance(message, ToolMessage) and message.name == name:
            return (
                json.loads(message.content)
                if message.status == "success"
                else {"error": message.content}
            )
    raise AssertionError(f"no {name} result")


async def test_every_dot_can_search_the_web_once_exa_is_configured(runtime, settings, exa):
    for name in ("Vance", "Mara", "Cole", "Rina", "Owen"):
        names = tool_names(runtime, name)
        assert {"web_search", "web_read", "web_answer"} <= names, name
        assert "read_web_page" not in names
    # The owner's switches still apply.
    rina = runtime.store.dot_by_name("Rina")
    runtime.store.update_dot(rina["id"], research_allowed=False)
    assert not {"web_search", "read_web_page"} & tool_names(runtime, "Rina")
    runtime.store.set_flags(research_allowed=False)
    assert "web_search" not in tool_names(runtime, "Vance")
    # Without a key, Dots can still read a page by URL.
    runtime.store.set_flags(research_allowed=True)
    settings.exa_api_key = None
    names = tool_names(runtime, "Mara")
    assert "read_web_page" in names and "web_search" not in names


async def test_search_sends_a_good_query_and_returns_passages(runtime, script, exa):
    mara = runtime.store.dot_by_name("Mara")
    thread = runtime.store.create_thread(mara["id"], "Leads")
    script.add(
        "Mara",
        call(
            "web_search",
            {
                "query": "Bengaluru startups hiring AI engineers",
                "published_after": "2026-09-01",
                "include_domains": ["https://linkedin.com/"],
                "country": "in",
            },
        ),
        "Example Labs is hiring (https://example.com/careers).",
    )
    await runtime.runs.send(thread["id"], "Who is hiring AI engineers in Bengaluru?")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"  # reading the web never needs approval
    [request] = exa.requests
    assert request.headers["x-api-key"] == "exa-test-key"
    assert json.loads(request.content) == {
        "query": "Bengaluru startups hiring AI engineers",
        "numResults": 8,
        "type": "auto",
        "contents": {
            "highlights": {"query": "Bengaluru startups hiring AI engineers", "maxCharacters": 1500}
        },
        "startPublishedDate": "2026-09-01T00:00:00.000Z",
        "includeDomains": ["linkedin.com"],
        "userLocation": "IN",
    }
    result = tool_result(script, "mara", "web_search")
    assert result["results"] == [
        {
            "title": "Example Labs is hiring AI engineers",
            "url": "https://example.com/careers",
            "published": "2026-09-24",
            "passages": ["Senior AI Engineer, Agents. Bengaluru. Posted 24 Sep 2026."],
        }
    ]
    assert "untrusted" in result["note"]
    events = [e["text"] for e in runtime.store.events([thread["id"]])]
    assert any(e.startswith("Searched the web") and "$0.0050" in e for e in events)
    system = script.calls["mara"][0][0].content
    assert "web_search finds pages" in system


async def test_read_can_crawl_subpages_and_reports_failures(runtime, script, exa):
    owen = runtime.store.dot_by_name("Owen")
    thread = runtime.store.create_thread(owen["id"], "Audit")
    script.add(
        "Owen",
        call(
            "web_read",
            {
                "urls": ["https://example.com", "https://gone.example"],
                "focus": "open roles",
                "fresh": True,
                "crawl_subpages": 3,
                "subpage_hints": ["careers"],
            },
        ),
        "Done.",
    )
    await runtime.runs.send(thread["id"], "Check their careers pages")
    await finish(runtime, thread["id"])
    [body] = exa.bodies("/contents")
    assert body == {
        "urls": ["https://example.com", "https://gone.example"],
        "text": {"maxCharacters": 3500},  # 28k shared across 2 pages x (1 + 3 subpages)
        "livecrawlTimeout": 15000,
        "highlights": {"query": "open roles", "maxCharacters": 1500},
        "maxAgeHours": 0,
        "subpages": 3,
        "subpageTarget": ["careers"],
    }
    result = tool_result(script, "owen", "web_read")
    assert result["pages"][0]["subpages"] == [
        {"title": "Careers", "url": "https://example.com/careers", "text": "2 open AI roles."}
    ]
    assert result["failed"] == [{"url": "https://gone.example", "error": "CRAWL_NOT_FOUND"}]


async def test_quick_answers_come_with_citations(runtime, script, exa):
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Quick")
    script.add(
        "Vance",
        call("web_answer", {"question": "How many AI roles is Example Labs hiring?"}),
        "Two.",
    )
    await runtime.runs.send(thread["id"], "quick check")
    await finish(runtime, thread["id"])
    assert exa.bodies("/answer") == [
        {"query": "How many AI roles is Example Labs hiring?", "text": False}
    ]
    result = tool_result(script, "vance", "web_answer")
    assert result["answer"] == "Example Labs has 2 open AI roles in Bengaluru."
    assert result["citations"] == [
        {"title": "Careers", "url": "https://example.com/careers", "published": "2026-09-24"}
    ]


async def test_a_run_has_a_web_budget(runtime, script, settings, exa):
    settings.exa_max_calls_per_run = 2
    mara = runtime.store.dot_by_name("Mara")
    thread = runtime.store.create_thread(mara["id"], "Loop")
    script.add(
        "Mara",
        *[call("web_search", {"query": f"query {i}"}, f"s{i}") for i in range(3)],
        "Stopping.",
    )
    await runtime.runs.send(thread["id"], "search a lot")
    await finish(runtime, thread["id"])
    assert len(exa.requests) == 2
    assert "budget for this task is used up" in tool_result(script, "mara", "web_search")["error"]


async def test_a_bad_key_is_explained_to_the_dot(runtime, script, exa):
    exa.status = 401
    mara = runtime.store.dot_by_name("Mara")
    thread = runtime.store.create_thread(mara["id"], "Key")
    script.add("Mara", call("web_search", {"query": "anything"}), "Could not search.")
    await runtime.runs.send(thread["id"], "search")
    await finish(runtime, thread["id"])
    assert "EXA_API_KEY" in tool_result(script, "mara", "web_search")["error"]


async def test_the_live_check_command_walks_search_read_and_answer(settings, capsys):
    from dotsfam.check import check_web

    settings.exa_api_key = None
    assert await check_web(settings, "anything") is False
    assert "EXA_API_KEY is not set" in capsys.readouterr().out
    settings.exa_api_key = "exa-test-key"
    fake = FakeExa()
    assert await check_web(settings, "who is hiring", httpx.MockTransport(fake)) is True
    out = capsys.readouterr().out
    assert [r.url.path for r in fake.requests] == ["/search", "/contents", "/answer"]
    assert "https://example.com/careers" in out and "Web search works" in out
