"""Live self-checks for integrations, run against your real keys.

    python -m dotsfam.check web "who is hiring AI engineers in Bengaluru?"

Runs one search, reads the top result and asks for a quick cited answer, exactly the
way a Dot would, and prints what came back and what it cost.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

from .config import Settings, get_settings
from .context import DotContext
from .tools.exa import exa_tools


class _Console:
    """Just enough of the store for tools to run outside the server."""

    def flags(self) -> dict[str, bool]:
        return {"paused": False, "research_allowed": True, "memory_allowed": True}

    def add_event(self, _thread: str, _kind: str, text: str, _run: str | None = None) -> None:
        print(f"  log: {text}")


async def check_web(
    settings: Settings, query: str, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    if not settings.exa_api_key:
        print("EXA_API_KEY is not set. Add it to .env in the folder you run this from.")
        return False
    ctx = DotContext(
        store=_Console(),  # type: ignore[arg-type]
        settings=settings,
        dot={"id": "check", "name": "Check"},
        thread_id="check",
        run_id="check",
        extra={"http_transport": transport} if transport else {},
    )
    tools = {tool.name: tool for tool in exa_tools(ctx)}
    try:
        print(f"1. web_search: {query}")
        found: dict[str, Any] = await tools["web_search"].ainvoke(
            {"query": query, "num_results": 3}
        )
        for item in found["results"]:
            print(f"   - {item['title'][:80]}  {item['url']}")
        if not found["results"]:
            print("   (no results)")
            return False
        top = found["results"][0]["url"]
        print(f"2. web_read: {top}")
        page: dict[str, Any] = await tools["web_read"].ainvoke(
            {"urls": [top], "max_characters": 600}
        )
        text = (page["pages"][0].get("text") or "") if page["pages"] else ""
        print(f"   {text[:300].strip()!r}…" if text else f"   could not read: {page.get('failed')}")
        print(f"3. web_answer: {query}")
        answer: dict[str, Any] = await tools["web_answer"].ainvoke({"question": query})
        print(f"   {answer['answer'][:400]}")
        for cite in answer["citations"][:3]:
            print(f"   source: {cite.get('url')}")
    except Exception as error:  # noqa: BLE001 - this is a diagnostic
        print(f"Failed: {error}")
        return False
    print("Web search works. Every Dot with 'Read the web' on can use it.")
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 1 or args[0] != "web":
        print(__doc__)
        return 2
    query = " ".join(args[1:]) or "What is new in AI agents this week?"
    return 0 if asyncio.run(check_web(get_settings(), query)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
