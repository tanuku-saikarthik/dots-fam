"""Phone calls over Twilio: signed webhooks, call metadata, and the push/ntfy fallback."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode

import httpx
import pytest

from dotsfam.notify import Notifier
from dotsfam.phone import PhoneManager, read_start, signature
from dotsfam.voice import CallState, VoiceManager

from .test_calls import FakeNtfy, ask_cole_to_email, wait_for
from .test_engine import FakeOutbox
from .test_slack import settle

OWNER = "+919800000001"
TWILIO = "+14155550100"
TOKEN = "twilio-test-token"


class FakeTwilio:
    def __init__(self, status: int = 201):
        self.requests: list[httpx.Request] = []
        self.status = status

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.status >= 300:
                return httpx.Response(self.status, json={"message": "Twilio is down"})
            return httpx.Response(self.status, json={"sid": f"CA{len(self.requests):032d}"})

        return httpx.MockTransport(handle)


@pytest.fixture
def twilio():
    return FakeTwilio()


@pytest.fixture
def ntfy():
    return FakeNtfy()


@pytest.fixture
def phone(runtime, twilio, ntfy, monkeypatch):
    settings = runtime.settings
    settings.twilio_account_sid = "AC123"
    settings.twilio_auth_token = TOKEN
    settings.twilio_number = TWILIO
    settings.owner_phone = OWNER
    settings.ntfy_topic = "dots-test-topic"
    monkeypatch.setattr("dotsfam.phone.check_stack", lambda _settings: None)
    runtime.attach_voice(VoiceManager(runtime))  # its pipeline is covered in test_voice
    manager = PhoneManager(runtime, transport=twilio.transport())
    runtime.attach_phone(manager)
    runtime.attach_notifier(Notifier(runtime, transport=ntfy.transport()))
    return manager


def signed(path: str, params: dict[str, str]) -> dict[str, str]:
    return {
        "X-Twilio-Signature": signature(TOKEN, f"https://dots.example.com{path}", params),
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def post_twilio(client, path: str, params: dict[str, str]) -> httpx.Response:
    return await client.post(path, content=urlencode(params), headers=signed(path, params))


def test_signature_matches_twilios_documented_example():
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675309",
        "Digits": "1234",
        "From": "+14158675309",
        "To": "+18005551212",
    }
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    assert signature("12345", url, params) == "RSOYDt4T1cUTdK1PDd93/VVr8B8="


async def test_the_owner_calls_in_and_reaches_the_chief_of_staff(runtime, phone, client):
    params = {"CallSid": "CAin1", "From": OWNER, "To": TWILIO, "Direction": "inbound"}
    response = await post_twilio(client, "/twilio/voice", params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert '<Stream url="wss://dots.example.com/twilio/stream">' in response.text
    ticket = response.text.split('name="ticket" value="')[1].split('"')[0]
    claimed = phone.claim(ticket)
    vance = runtime.store.dot_by_name("Vance")
    assert claimed.dot_id == vance["id"]
    assert runtime.store.thread(claimed.thread_id)["title"] == "Phone call with Vance"
    assert claimed.metadata == {
        "channel": "phone",
        "direction": "inbound",
        "caller": OWNER,
        "call_id": "CAin1",
        "conversation_id": claimed.thread_id,
    }
    assert phone.claim(ticket) is None  # one stream per ticket


async def test_other_callers_and_unsigned_requests_are_turned_away(phone, client):
    stranger = {"CallSid": "CAx", "From": "+15550000000", "To": TWILIO, "Direction": "inbound"}
    response = await post_twilio(client, "/twilio/voice", stranger)
    assert response.status_code == 200 and "<Reject" in response.text
    owner = {**stranger, "From": OWNER}
    forged = await client.post(
        "/twilio/voice",
        content=urlencode(owner),
        headers={**signed("/twilio/voice", owner), "X-Twilio-Signature": "forged"},
    )
    assert forged.status_code == 403
    assert (await client.post("/twilio/status", content=urlencode(owner))).status_code == 403


async def test_a_waiting_approval_calls_the_owners_phone(runtime, script, phone, twilio, ntfy):
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    await ask_cole_to_email(runtime, script, thread["id"], "ana@acme.example")
    await wait_for(lambda: twilio.requests)
    [request] = twilio.requests
    assert str(request.url) == "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls.json"
    form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
    query = urlencode({"dot": cole["id"], "thread": thread["id"]})
    assert form == {
        "To": OWNER,
        "From": TWILIO,
        "Url": f"https://dots.example.com/twilio/voice?{query}",
        "StatusCallback": "https://dots.example.com/twilio/status",
        "Timeout": "25",
    }
    assert ntfy.requests == []  # the phone call went out, so no fallback


async def test_answering_the_call_streams_into_that_conversation(runtime, phone, client):
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    path = f"/twilio/voice?{urlencode({'dot': cole['id'], 'thread': thread['id']})}"
    params = {"CallSid": "CAout1", "From": TWILIO, "To": OWNER, "Direction": "outbound-api"}
    response = await post_twilio(client, path, params)
    ticket = phone.claim(response.text.split('name="ticket" value="')[1].split('"')[0])
    assert (ticket.dot_id, ticket.thread_id) == (cole["id"], thread["id"])
    assert ticket.metadata["direction"] == "outbound" and ticket.metadata["caller"] == OWNER


async def test_falls_back_to_push_and_ntfy_when_twilio_fails(runtime, script, phone, twilio, ntfy):
    twilio.status = 500
    runtime.runs.extra["outbox"] = FakeOutbox()
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    await ask_cole_to_email(runtime, script, thread["id"], "ana@acme.example")
    await wait_for(lambda: ntfy.requests)
    assert ntfy.requests[0].headers["Title"] == "Cole is calling"
    events = [e["text"] for e in runtime.store.events([thread["id"]])]
    assert any(
        text.startswith("Phone call failed: Twilio refused the call (500)") for text in events
    )


async def test_falls_back_when_the_owner_does_not_pick_up(runtime, phone, ntfy, client):
    cole = runtime.store.dot_by_name("Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    sent = await runtime.notifier.ring(cole, thread["id"], "Cole needs your OK.")
    assert sent == {"phone": 1, "push": 0, "ntfy": 0}
    call_sid = f"CA{1:032d}"
    answered = {"CallSid": call_sid, "CallStatus": "completed"}
    no_answer = {"CallSid": call_sid, "CallStatus": "no-answer"}
    # A status for a call we didn't place, or one that was answered, rings nothing.
    assert (
        await post_twilio(client, "/twilio/status", {**answered, "CallSid": "CAother"})
    ).status_code == 204
    assert ntfy.requests == []
    await post_twilio(client, "/twilio/status", no_answer)
    assert len(ntfy.requests) == 1
    await post_twilio(client, "/twilio/status", no_answer)  # Twilio retries don't ring twice
    assert len(ntfy.requests) == 1
    events = [e["text"] for e in runtime.store.events([thread["id"]])]
    assert "Phone call not answered (no-answer)" in events


async def test_call_metadata_reaches_the_dot(runtime, script):
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Phone call")
    meta = {
        "channel": "phone",
        "direction": "inbound",
        "caller": OWNER,
        "call_id": "CAin1",
        "conversation_id": thread["id"],
    }
    script.add("Vance", "All quiet today.")
    await CallState(runtime, thread["id"], vance, metadata=meta).hear("anything for me")
    await settle(runtime)
    system = script.calls["vance"][0][0].content
    assert f"caller {OWNER}" in system and "call_id CAin1" in system
    assert f"conversation_id {thread['id']}" in system and "can be spoofed" in system


async def test_read_start_skips_twilios_connected_message():
    messages = iter(
        [
            json.dumps({"event": "connected", "protocol": "Call"}),
            json.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                        "customParameters": {"ticket": "t"},
                    },
                }
            ),
        ]
    )

    async def receive() -> str:
        return next(messages)

    start = await read_start(receive)
    assert start["streamSid"] == "MZ1" and start["customParameters"] == {"ticket": "t"}


async def test_status_shows_phone_and_test_call_uses_it(phone, client, twilio):
    status = (await client.get("/api/calls")).json()
    assert status["phone"]["available"] is True and status["phone"]["number"] == TWILIO
    assert (await client.get("/api/state")).json()["setup"]["phone"] is True
    sent = (await client.post("/api/calls/test")).json()
    assert sent["phone"] == 1 and len(twilio.requests) == 1
