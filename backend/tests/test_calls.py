"""Calls: ring the owner's phone (ntfy / web push) when a Dot needs an OK, for free."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from dotsfam.notify import Notifier

from .conftest import call, finish
from .test_engine import FakeOutbox

DEVICE = {
    "endpoint": "https://push.example.com/send/abc123",
    "keys": {"p256dh": "BPk", "auth": "xyz"},
    "label": "Pixel",
}


class FakeNtfy:
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self.status = status

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json={"id": "m1"})

        return httpx.MockTransport(handle)


@pytest.fixture
def ntfy():
    return FakeNtfy()


@pytest.fixture
def notifier(runtime, ntfy):
    runtime.settings.ntfy_topic = "dots-test-topic"
    n = Notifier(runtime, transport=ntfy.transport())
    runtime.attach_notifier(n)
    return n


async def wait_for(predicate, seconds: float = 5) -> None:
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def ask_cole_to_email(runtime, script, thread_id: str, to: str) -> None:
    script.add("Cole", call("send_email", {"to": to, "body": "Hi"}))
    await runtime.runs.send(thread_id, f"Email {to}")
    outcome = await finish(runtime, thread_id)
    assert outcome.status == "waiting_approval"


async def test_a_waiting_approval_rings_the_phone_via_ntfy(runtime, script, notifier, ntfy):
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    await ask_cole_to_email(runtime, script, thread["id"], "ana@acme.example")
    await wait_for(lambda: ntfy.requests)
    [request] = ntfy.requests
    assert str(request.url) == "https://ntfy.sh/dots-test-topic"
    assert request.headers["Title"] == "Cole is calling"
    assert request.headers["Priority"] == "urgent"
    link = f"https://dots.example.com/#/call/{cole['id']}/{thread['id']}"
    assert request.headers["Click"] == link
    assert request.headers["Actions"] == f"view, Answer, {link}, clear=true"
    assert "Authorization" not in request.headers
    assert request.content.decode() == (
        "Cole needs your OK: it sends an email, to ana@acme.example. Approve or decline?"
    )
    events = runtime.store.events([thread["id"]])
    assert any(e["kind"] == "call" and e["text"].startswith("Rang you:") for e in events)


async def test_rings_are_rate_limited_per_conversation(runtime, script, notifier, ntfy):
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    first = runtime.store.create_thread(cole["id"], "One")
    await ask_cole_to_email(runtime, script, first["id"], "a@x.example")
    await wait_for(lambda: len(ntfy.requests) == 1)
    # Same approval again (e.g. another run finishing) doesn't ring twice.
    await notifier.on_run_finished(
        type("O", (), {"status": "waiting_approval", "thread_id": first["id"]})()
    )
    assert len(ntfy.requests) == 1
    # A different conversation still rings.
    second = runtime.store.create_thread(cole["id"], "Two")
    await ask_cole_to_email(runtime, script, second["id"], "b@x.example")
    await wait_for(lambda: len(ntfy.requests) == 2)


async def test_nothing_rings_when_no_phone_is_set_up(runtime, script, ntfy):
    n = Notifier(runtime, transport=ntfy.transport())
    runtime.attach_notifier(n)
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    await ask_cole_to_email(runtime, script, thread["id"], "ana@acme.example")
    await asyncio.sleep(0.2)
    assert ntfy.requests == [] and n.sent == []


async def test_ntfy_token_is_sent_when_set(runtime, notifier, ntfy):
    runtime.settings.ntfy_token = "tk_secret"
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Calls")
    sent = await notifier.ring(vance, thread["id"], "Test")
    assert sent == {"push": 0, "ntfy": 1}
    assert ntfy.requests[0].headers["Authorization"] == "Bearer tk_secret"


async def test_web_push_reaches_devices_and_drops_dead_ones(runtime, notifier, monkeypatch):
    import pywebpush

    sent = []

    class Gone:
        status_code = 410

    def fake_webpush(subscription_info, data, **kwargs):
        if subscription_info["endpoint"].endswith("dead"):
            raise pywebpush.WebPushException("gone", response=Gone())
        sent.append((subscription_info["endpoint"], data, kwargs))

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    runtime.store.add_push_subscription(DEVICE["endpoint"], DEVICE["keys"], "Pixel")
    runtime.store.add_push_subscription("https://push.example.com/dead", DEVICE["keys"])
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Calls")
    result = await notifier.ring(vance, thread["id"], "Two launches need you.")
    assert result == {"push": 1, "ntfy": 1}
    [(endpoint, data, kwargs)] = sent
    assert endpoint == DEVICE["endpoint"]
    assert '"title": "Vance is calling"' in data and f"/#/call/{vance['id']}/{thread['id']}" in data
    assert kwargs["headers"] == {"Urgency": "high"} and kwargs["ttl"] == 120
    assert kwargs["vapid_claims"]["sub"].startswith("mailto:")
    assert [s["endpoint"] for s in runtime.store.push_subscriptions()] == [DEVICE["endpoint"]]


async def test_vapid_key_is_made_once_and_kept_private(runtime, notifier, settings):
    key = notifier.public_key()
    assert len(key) == 87  # 65-byte uncompressed P-256 point, base64url without padding
    pem = settings.data_dir / "vapid_private.pem"
    assert pem.stat().st_mode & 0o777 == 0o600
    assert Notifier(runtime).public_key() == key


async def test_calls_api(runtime, script, client, notifier, ntfy):
    status = (await client.get("/api/calls")).json()
    assert (
        status["push_devices"] == 0 and status["ntfy"] is True and len(status["public_key"]) == 87
    )
    bad = await client.post("/api/calls/devices", json={**DEVICE, "keys": {}})
    assert bad.status_code == 400
    added = await client.post("/api/calls/devices", json=DEVICE)
    assert added.status_code == 201 and added.json()["push_devices"] == 1
    removed = await client.post("/api/calls/devices/remove", json=DEVICE)
    assert removed.json()["push_devices"] == 0

    test = await client.post("/api/calls/test")
    assert test.json() == {"push": 0, "ntfy": 1}
    assert ntfy.requests[-1].headers["Title"] == "Vance is calling"

    runtime.settings.ntfy_topic = None
    nothing = await client.post("/api/calls/test")
    assert nothing.status_code == 409


async def test_incoming_call_screen_lists_what_is_waiting(runtime, script, client, notifier):
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    await ask_cole_to_email(runtime, script, thread["id"], "ana@acme.example")
    body = (await client.get(f"/api/calls/{thread['id']}")).json()
    assert body["dot"]["name"] == "Cole" and body["thread"]["id"] == thread["id"]
    assert body["waiting"] == [
        "Cole needs your OK: it sends an email, to ana@acme.example. Approve or decline?"
    ]
    assert (await client.get("/api/calls/nope")).status_code == 404


async def test_calls_routes_are_off_without_a_notifier(client):
    assert (await client.get("/api/calls")).status_code == 404
