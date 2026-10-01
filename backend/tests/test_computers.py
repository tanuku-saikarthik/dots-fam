"""A real computer per Dot: local driver, headless Chromium, the Reversibility gate on clicks."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from dotsfam.computers import ComputerManager
from dotsfam.reversibility import Snapshots, classify

from .conftest import call, finish

CHROMIUM = os.environ.get("DOTSFAM_CHROMIUM", "/opt/pw-browsers/chromium")
pytest.importorskip("playwright")
needs_browser = pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="no Chromium for tests")

PAGE = b"""<!doctype html><html><head><title>Outbox</title></head><body>
<h1>Outbox</h1>
<input type="search" aria-label="Search contacts">
<textarea aria-label="Message"></textarea>
<button onclick="document.getElementById('out').textContent='Preview: '+document.querySelector('textarea').value">
Preview</button>
<button onclick="document.title='Sent'; document.getElementById('out').textContent='Sent!'">Send</button>
<p id="out"></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):  # noqa: ANN002
        return None


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def computers(runtime, settings, monkeypatch):
    monkeypatch.setenv("DOTSFAM_CHROMIUM", CHROMIUM)
    monkeypatch.delenv("DOTSFAM_LOCAL_SHELL", raising=False)
    settings.computer_driver = "local"
    manager = ComputerManager.create(runtime.store, settings)
    runtime.attach_computers(manager)
    return manager


def latest_snapshot(messages) -> dict:
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == "computer_snapshot":
            return json.loads(message.content)
    raise AssertionError("no snapshot yet")


def act_on(tool: str, name: str, **extra):
    """A scripted step that picks an element by name from the Dot's latest snapshot."""

    def reply(messages):
        snap = latest_snapshot(messages)
        [element] = [e for e in snap["elements"] if e["name"] == name]
        args = {"ref": element["ref"], "snapshot_id": snap["snapshot_id"], **extra}
        return call(tool, args, f"call-{tool}-{name}")

    return reply


def test_classifier_judges_elements_by_name():
    snaps = Snapshots()
    snaps.remember(
        "d1",
        {
            "snapshot_id": 3,
            "elements": [
                {"ref": "e1", "role": "button", "name": "Send"},
                {"ref": "e2", "role": "button", "name": "Preview"},
                {"ref": "e3", "role": "searchbox", "name": "Search contacts"},
                {"ref": "e4", "role": "textbox", "name": "Message"},
            ],
        },
    )
    assert classify(snaps, "d1", "click", {"ref": "e1", "snapshot_id": 3}) == "clicks button “Send”"
    assert classify(snaps, "d1", "click", {"ref": "e2", "snapshot_id": 3}) is None
    assert classify(snaps, "d1", "type", {"ref": "e3", "snapshot_id": 3, "submit": True}) is None
    assert classify(snaps, "d1", "type", {"ref": "e4", "snapshot_id": 3, "submit": True}) == (
        "submits textbox “Message”"
    )
    assert classify(snaps, "d1", "shell", {"command": "ls -la"}) is None
    assert classify(snaps, "d1", "shell", {"command": "git push origin main"})
    assert classify(snaps, "d1", "shell", {"command": "curl -X POST https://x.example"})
    # Anything it can't check raises, and the tool layer turns that into "ask the owner".
    for bad in (
        {"ref": "e1", "snapshot_id": 2},
        {"ref": "e1", "snapshot_id": "3.0"},
        {"ref": "e9", "snapshot_id": 3},
    ):
        with pytest.raises(ValueError):
            classify(snaps, "d1", "click", bad)


def test_key_presses_are_judged_by_where_focus_is():
    snaps = Snapshots()
    message = {"ref": "e4", "role": "textbox", "name": "Message"}
    search = {"ref": "e3", "role": "searchbox", "name": "Search contacts"}
    send = {"ref": "e1", "role": "button", "name": "Send"}
    press = lambda key: classify(snaps, "d1", "press", {"key": key})  # noqa: E731
    assert press("Tab") is None and press("Escape") is None
    # Unknown focus: Enter, Space and shortcuts ask.
    for key in ("Enter", "\n", "\r", " ", "Space", "r", "Control+Enter", "Meta+s", "F5"):
        assert press(key), key
    snaps.focus("d1", search)
    assert press("Enter") is None and press("\n") is None
    assert press("Control+Enter")
    snaps.focus("d1", message)
    assert press("Enter") == "presses 'Enter' in textbox “Message”"
    assert press("a") is None and press("Backspace") is None
    snaps.focus("d1", send)
    assert press(" ") and press("Enter")


async def test_computer_is_off_until_the_owner_turns_it_on(runtime, computers, client):
    mara = runtime.store.dot_by_name("Mara")
    status = (await client.get(f"/api/computers/{mara['id']}")).json()
    assert status["available"] is True and status["running"] is False
    assert status["permissions"] == {
        "enabled": False,
        "browser": True,
        "files": True,
        "shell": False,
    }
    response = await client.post(f"/api/computers/{mara['id']}/start")
    assert response.status_code == 409 and "off" in response.json()["detail"]
    # Shell stays off on the local driver even if the owner asks for it.
    response = await client.patch(
        f"/api/computers/{mara['id']}", json={"enabled": True, "shell": True}
    )
    assert response.json()["permissions"]["shell"] is False


@needs_browser
async def test_dot_browses_and_send_clicks_wait_for_approval(
    runtime, script, computers, site, client
):
    mara = runtime.store.update_dot(
        runtime.store.dot_by_name("Mara")["id"], computer={"enabled": True}
    )
    thread = runtime.store.create_thread(mara["id"], "Outbox")
    script.add(
        "Mara",
        call("computer_open", {"url": site}),
        call("computer_snapshot", {}, "call-snap-1"),
        act_on("computer_type", "Message", text="Hello Ana"),
        act_on("computer_click", "Preview"),
        call("computer_snapshot", {}, "call-snap-2"),
        act_on("computer_click", "Send"),
        "Sent the message.",
    )
    await runtime.runs.send(thread["id"], "Send Ana a hello from the outbox")
    outcome = await finish(runtime, thread["id"], 90)
    assert outcome.status == "waiting_approval", outcome
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "computer_click"
    assert approval["reason"] == "clicks button “Send”"
    text = await computers.call(mara, "GET", "/browser/text")
    assert "Preview: Hello Ana" in text["text"]  # the harmless click ran on its own
    assert "Sent!" not in text["text"]

    # The owner can watch the screen while the Dot waits.
    screen = await client.get(f"/api/computers/{mara['id']}/screen")
    assert screen.status_code == 200 and screen.headers["content-type"] == "image/jpeg"

    response = await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    assert response.json()["resumed"] is True
    outcome = await finish(runtime, thread["id"], 60)
    assert outcome.status == "completed" and outcome.text == "Sent the message."
    text = await computers.call(mara, "GET", "/browser/text")
    assert "Sent!" in text["text"]
    activity = (await client.get(f"/api/computers/{mara['id']}")).json()["activity"]
    assert any("Clicked button “Send”" in item["text"] for item in activity)


@needs_browser
async def test_owner_takes_control_and_the_dot_waits(runtime, computers, site, client):
    mara = runtime.store.update_dot(
        runtime.store.dot_by_name("Mara")["id"], computer={"enabled": True}
    )
    await computers.call(mara, "POST", "/browser/navigate", {"url": site})
    response = await client.post(f"/api/computers/{mara['id']}/control/take")
    assert response.json() == {"holder": "human"}
    with pytest.raises(Exception, match="owner has control"):
        await computers.call(mara, "POST", "/browser/snapshot")
    response = await client.post(
        f"/api/computers/{mara['id']}/human/navigate", json={"url": site + "?owner=1"}
    )
    assert response.status_code == 200 and response.json()["url"].endswith("?owner=1")
    await client.post(f"/api/computers/{mara['id']}/control/release")
    snap = await computers.call(mara, "POST", "/browser/snapshot")
    assert snap["url"].endswith("?owner=1")


@needs_browser
async def test_files_stay_inside_the_workspace(runtime, computers):
    mara = runtime.store.update_dot(
        runtime.store.dot_by_name("Mara")["id"], computer={"enabled": True}
    )
    await computers.call(mara, "POST", "/files/write", {"path": "notes/a.md", "content": "# A"})
    listing = await computers.call(mara, "GET", "/files", params={"path": "notes"})
    assert listing["entries"] == [{"name": "a.md", "dir": False, "size": 3}]
    with pytest.raises(Exception, match="inside the workspace"):
        await computers.call(mara, "GET", "/files/read", params={"path": "../profile/x"})
    with pytest.raises(Exception, match="shell is disabled"):
        await computers.call(mara, "POST", "/exec", {"command": "id"})
