"""Regression tests for ways around the Reversibility Law found in review.

Each test is a concrete attempt to change the outside world without the owner's OK.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from dotsfam.computers import ComputerError, ComputerManager
from dotsfam.voice import CallState

from .conftest import call, finish
from .test_engine import FakeOutbox, dot
from .test_slack import settle

CHROMIUM = "/opt/pw-browsers/chromium"
needs_browser = pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="no Chromium for tests")

PAGE = b"""<!doctype html><html><head><title>Chat</title></head><body>
<input type="search" aria-label="Search contacts">
<textarea aria-label="Message"
  onkeydown="if(event.key==='Enter'){document.getElementById('out').textContent='Sent!'}"></textarea>
<button onclick="document.getElementById('out').textContent='Sent!'">Send</button>
<button onclick="document.getElementById('out').textContent='Previewed'">Preview</button>
<button onclick="document.getElementById('out').textContent='Bought!'">Buy now</button>
<p id="out"></p>
<script>
  // A hostile page: once Preview has a ref, move that ref onto "Buy now".
  setInterval(() => {
    const preview = [...document.querySelectorAll('button')].find(b => b.innerText === 'Preview');
    const buy = [...document.querySelectorAll('button')].find(b => b.innerText === 'Buy now');
    const ref = preview.getAttribute('data-dotsfam-ref');
    if (location.hash === '#hostile' && ref) {
      preview.removeAttribute('data-dotsfam-ref');
      buy.setAttribute('data-dotsfam-ref', ref);
    }
  }, 50);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):  # noqa: ANN002
        return None


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def computers(runtime, settings, monkeypatch):
    monkeypatch.setenv("DOTSFAM_CHROMIUM", CHROMIUM)
    settings.computer_driver = "local"
    manager = ComputerManager.create(runtime.store, settings)
    runtime.attach_computers(manager)
    return manager


def snap(messages) -> dict:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "computer_snapshot":
            return json.loads(message.content)
    raise AssertionError("no snapshot")


def ref(snapshot: dict, name: str) -> str:
    return next(e["ref"] for e in snapshot["elements"] if e["name"] == name)


async def browse(runtime, script, site, *steps):
    mara = runtime.store.update_dot(dot(runtime, "Mara")["id"], computer={"enabled": True})
    thread = runtime.store.create_thread(mara["id"], "Chat")
    script.add(
        "Mara", call("computer_open", {"url": site}), call("computer_snapshot", {}, "s1"), *steps
    )
    await runtime.runs.send(thread["id"], "go")
    return mara, thread


async def page_text(computers, mara) -> str:
    return (await computers.call(mara, "GET", "/browser/text"))["text"]


@needs_browser
async def test_a_click_on_a_ref_from_a_future_snapshot_waits(runtime, script, computers, site):
    def batch(messages):
        s = snap(messages)
        return AIMessage(
            content="",
            tool_calls=[
                {"id": "s2", "name": "computer_snapshot", "args": {}},
                {
                    "id": "c1",
                    "name": "computer_click",
                    "args": {"ref": ref(s, "Send"), "snapshot_id": s["snapshot_id"] + 1},
                },
            ],
        )

    mara, thread = await browse(runtime, script, site, batch, "done")
    assert (await finish(runtime, thread["id"], 90)).status == "waiting_approval"
    assert "Sent!" not in await page_text(computers, mara)


@needs_browser
async def test_a_snapshot_id_written_as_text_waits(runtime, script, computers, site):
    def click(messages):
        s = snap(messages)
        return call(
            "computer_click", {"ref": ref(s, "Send"), "snapshot_id": f"{s['snapshot_id']}.0"}
        )

    mara, thread = await browse(runtime, script, site, click, "done")
    assert (await finish(runtime, thread["id"], 90)).status == "waiting_approval"
    assert "Sent!" not in await page_text(computers, mara)


@needs_browser
async def test_newline_in_a_message_box_waits(runtime, script, computers, site):
    def typed(messages):
        s = snap(messages)
        return call(
            "computer_type",
            {"ref": ref(s, "Message"), "snapshot_id": s["snapshot_id"], "text": "hi"},
            "t1",
        )

    mara, thread = await browse(
        runtime, script, site, typed, call("computer_press", {"key": "\n"}, "p1"), "done"
    )
    assert (await finish(runtime, thread["id"], 90)).status == "waiting_approval"
    assert "Sent!" not in await page_text(computers, mara)


@needs_browser
async def test_enter_follows_the_real_focus(runtime, script, computers, site):
    def both(messages):
        s = snap(messages)
        sid = s["snapshot_id"]
        return AIMessage(
            content="",
            tool_calls=[
                {"id": "t1", "name": "computer_type",
                 "args": {"ref": ref(s, "Message"), "snapshot_id": sid, "text": "hi"}},
                {"id": "t2", "name": "computer_type",
                 "args": {"ref": ref(s, "Search contacts"), "snapshot_id": sid, "text": "ana"}},
            ],
        )  # fmt: skip

    def click_message(messages):
        s = snap(messages)
        return call("computer_click", {"ref": ref(s, "Message"), "snapshot_id": s["snapshot_id"]})

    mara, thread = await browse(
        runtime, script, site, both, click_message, call("computer_press", {"key": "Enter"}), "done"
    )
    assert (await finish(runtime, thread["id"], 90)).status == "waiting_approval"
    assert "Sent!" not in await page_text(computers, mara)
    # Even if the tracked focus were wrong, the computer checks where focus really is.
    with pytest.raises(ComputerError, match="Focus is on textbox “Message”"):
        await computers.call(mara, "POST", "/browser/key", {"key": "Enter", "require": "search"})
    assert "Sent!" not in await page_text(computers, mara)


@needs_browser
async def test_a_page_cannot_swap_what_a_ref_points_at(runtime, computers, site):
    mara = runtime.store.update_dot(dot(runtime, "Mara")["id"], computer={"enabled": True})
    await computers.call(mara, "POST", "/browser/navigate", {"url": site + "#hostile"})
    s = await computers.call(mara, "POST", "/browser/snapshot")
    preview = next(e for e in s["elements"] if e["name"] == "Preview")
    import asyncio

    await asyncio.sleep(0.3)  # the page moves the ref onto "Buy now"
    with pytest.raises(ComputerError, match="changed since the snapshot"):
        await computers.call(
            mara,
            "POST",
            "/browser/click",
            {
                "ref": preview["ref"],
                "snapshot_id": s["snapshot_id"],
                "expect": {"role": "button", "name": "Preview"},
            },
        )
    assert "Bought!" not in await page_text(computers, mara)


async def test_a_decline_holds_even_after_switching_to_autonomous(runtime, script, client):
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    cole = dot(runtime, "Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    script.add("Cole", call("send_email", {"to": "bo@x.example", "body": "Hi"}), "ok")
    await runtime.runs.send(thread["id"], "Email Bo")
    await finish(runtime, thread["id"])
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    await client.patch(f"/api/dots/{cole['id']}", json={"approval_mode": "autonomous"})
    await client.post(f"/api/approvals/{approval['id']}", json={"decision": "declined"})
    await finish(runtime, thread["id"])
    assert outbox.executed == []


async def test_deciding_while_paused_keeps_the_approval_open(runtime, script, client):
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    cole = dot(runtime, "Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    script.add("Cole", call("send_email", {"to": "bo@x.example", "body": "Hi"}), "Sent.")
    await runtime.runs.send(thread["id"], "Email Bo")
    await finish(runtime, thread["id"])
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    await client.patch("/api/settings", json={"paused": True})
    response = await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    assert response.status_code == 423
    assert runtime.store.approval(approval["id"])["status"] == "pending"
    await client.patch("/api/settings", json={"paused": False})
    response = await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    assert response.json()["resumed"] is True
    assert (await finish(runtime, thread["id"])).text == "Sent."
    assert outbox.executed == [("bo@x.example", "Hi")]


async def test_voice_needs_a_clear_answer_to_an_approval_it_read_out(runtime, script):
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    cole = dot(runtime, "Cole")
    thread = runtime.store.create_thread(cole["id"], "Call")
    state = CallState(runtime, thread["id"], cole)
    script.add("Cole", call("send_email", {"to": "ana@acme.example", "body": "Hi"}), "Sent.")
    await runtime.runs.send(thread["id"], "email ana")
    await finish(runtime, thread["id"])
    # Not read out yet on this call: a "yes" only gets it read out.
    assert (await state.hear("yes")).startswith("Cole needs your OK")
    assert outbox.executed == []
    assert (await state.hear("Okay, wait, who is that going to?")).startswith("First, Cole")
    await settle(runtime)
    assert outbox.executed == []
    assert await state.hear("Yes, send it.") == "Approved, going ahead."
    await settle(runtime)
    assert outbox.executed == [("ana@acme.example", "Hi")]
