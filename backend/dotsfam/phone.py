"""Real phone calls over Twilio, carried into the same Pipecat pipeline as browser calls.

- Your team calls you: when a Dot needs your OK, it rings OWNER_PHONE from TWILIO_NUMBER.
  If Twilio can't place the call, or nobody picks up, web push and ntfy ring instead.
- You call your team: ring TWILIO_NUMBER from OWNER_PHONE to talk to the Chief of Staff.
  Every other caller is turned away.

Twilio fetches TwiML from /twilio/voice (signed with your auth token), then opens a Media
Streams WebSocket to /twilio/stream with 8 kHz audio. A one-time ticket inside that TwiML ties
the socket to the signed request, so nobody else can open a call. Each call reaches the Dot
with its metadata: the caller ID, Twilio's call ID and the conversation ID.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx

from .voice import CallState, check_stack

log = logging.getLogger("dotsfam.phone")

TWILIO_API = "https://api.twilio.com/2010-04-01"
TICKET_SECONDS = 60  # Twilio opens the stream right after fetching the TwiML
UNANSWERED = {"no-answer", "busy", "failed", "canceled"}
PHONE_SAMPLE_RATE = 8000  # Twilio Media Streams carry 8 kHz mu-law


class PhoneError(RuntimeError):
    """The call could not be placed (bad credentials, number, network, Twilio outage)."""


def signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Twilio's request signature: HMAC-SHA1 over the URL plus the sorted form fields."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def twiml(*verbs: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{"".join(verbs)}</Response>'


def say(text: str) -> str:
    return f"<Say>{escape(text)}</Say>"


def stream_twiml(url: str, parameters: Mapping[str, str]) -> str:
    params = "".join(
        f"<Parameter name={quoteattr(key)} value={quoteattr(value)} />"
        for key, value in parameters.items()
    )
    return twiml(f"<Connect><Stream url={quoteattr(url)}>{params}</Stream></Connect>")


async def read_start(receive: Callable[[], Awaitable[str]], limit: int = 5) -> dict[str, Any]:
    """Twilio's 'start' message (stream and call IDs, custom parameters); 'connected' comes first."""
    for _ in range(limit):
        message = json.loads(await receive())
        if message.get("event") == "start":
            return message.get("start") or {}
    raise PhoneError("Twilio did not start the media stream.")


@dataclass
class Ticket:
    dot_id: str
    thread_id: str
    metadata: dict[str, str]
    expires: float


class PhoneManager:
    def __init__(self, runtime: Any, transport: httpx.AsyncBaseTransport | None = None):
        self.runtime = runtime
        self.store = runtime.store
        self.settings = runtime.settings
        self.transport = transport  # tests inject a fake Twilio
        self._tickets: dict[str, Ticket] = {}
        self._placed: dict[str, dict[str, Any]] = {}  # call SID -> what the call was about

    def problem(self) -> str | None:
        """None when phone calls can work, else why not."""
        if not self.settings.phone_enabled:
            return (
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_NUMBER, OWNER_PHONE "
                "and PUBLIC_URL."
            )
        if self.runtime.voice is None:
            return "Phone calls need voice calls on (VOICE_STACK)."
        return check_stack(self.settings)

    def status(self) -> dict[str, Any]:
        problem = self.problem()
        return {
            "available": problem is None,
            "reason": problem,
            "number": self.settings.twilio_number,
            "voice_url": self._url("/twilio/voice"),
        }

    def _url(self, path: str) -> str:
        return f"{(self.settings.public_url or '').rstrip('/')}{path}"

    def verify(self, path: str, params: Mapping[str, str], given: str) -> bool:
        assert self.settings.twilio_auth_token
        expected = signature(self.settings.twilio_auth_token, self._url(path), params)
        return bool(given) and hmac.compare_digest(given, expected)

    # ---- your team calls you -----------------------------------------------------------
    async def place_call(self, dot: dict[str, Any], thread_id: str, reason: str) -> str:
        """Ring the owner's phone. Returns Twilio's call SID; raises PhoneError if it can't."""
        settings = self.settings
        query = urlencode({"dot": dot["id"], "thread": thread_id})
        form = {
            "To": settings.owner_phone,
            "From": settings.twilio_number,
            "Url": self._url(f"/twilio/voice?{query}"),
            "StatusCallback": self._url("/twilio/status"),
            "Timeout": str(settings.twilio_ring_seconds),
        }
        url = f"{TWILIO_API}/Accounts/{settings.twilio_account_sid}/Calls.json"
        auth = (settings.twilio_account_sid or "", settings.twilio_auth_token or "")
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                response = await client.post(url, data=form, auth=auth)
        except httpx.HTTPError as error:
            raise PhoneError(f"Could not reach Twilio: {error}") from error
        if response.status_code >= 300:
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = response.text[:200]
            raise PhoneError(f"Twilio refused the call ({response.status_code}): {message}")
        call_sid = response.json()["sid"]
        self._placed[call_sid] = {"dot": dot, "thread_id": thread_id, "reason": reason}
        while len(self._placed) > 100:  # status callbacks that never came
            self._placed.pop(next(iter(self._placed)))
        return call_sid

    async def on_status(self, params: Mapping[str, str]) -> None:
        """Twilio's final word on a call we placed: ring the devices if nobody picked up."""
        status = params.get("CallStatus", "")
        placed = self._placed.pop(params.get("CallSid", ""), None)
        if placed is None or status not in UNANSWERED:
            return
        notifier = self.runtime.notifier
        if notifier is not None:
            await notifier.missed_call(placed["dot"], placed["thread_id"], placed["reason"], status)

    # ---- answering -------------------------------------------------------------------
    def answer(self, params: Mapping[str, str], query: Mapping[str, str]) -> str:
        """TwiML for a call Twilio just connected: stream it to a Dot, or turn it away."""
        problem = self.problem()
        if problem:
            log.warning("Phone call turned away: %s", problem)
            return twiml(say("Calls aren't set up on this server yet."), "<Hangup/>")
        if self.store.flags()["paused"]:
            return twiml(say("The team is paused."), "<Hangup/>")
        outbound = params.get("Direction", "").startswith("outbound")
        if outbound and query.get("dot") and query.get("thread"):
            dot = self.store.dot(query["dot"])
            thread = self.store.thread(query["thread"])
            caller = params.get("To", "")
        elif not outbound and params.get("From") == self.settings.owner_phone:
            dot = (
                next((d for d in self.store.dots() if d["can_delegate"]), None)
                or (self.store.dots()[0])
            )
            thread = self.store.create_thread(dot["id"], f"Phone call with {dot['name']}")
            caller = params["From"]
        else:
            log.warning("Rejected a phone call from %s", params.get("From", "unknown"))
            return twiml('<Reject reason="rejected"/>')
        self._expire()
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = Ticket(
            dot["id"],
            thread["id"],
            {
                "channel": "phone",
                "direction": "outbound" if outbound else "inbound",
                "caller": caller,
                "call_id": params.get("CallSid", ""),
                "conversation_id": thread["id"],
            },
            time.monotonic() + TICKET_SECONDS,
        )
        ws_url = self._url("/twilio/stream").replace("https://", "wss://", 1)
        return stream_twiml(ws_url.replace("http://", "ws://", 1), {"ticket": ticket})

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, t in self._tickets.items() if t.expires < now]:
            del self._tickets[key]

    def claim(self, ticket: str) -> Ticket | None:
        """Each ticket opens one stream, once, within a minute."""
        found = self._tickets.pop(ticket, None)
        return found if found and found.expires >= time.monotonic() else None

    async def stream(self, websocket: Any) -> None:
        """Run a Twilio Media Stream through the Dot's voice pipeline until either side hangs up."""
        await websocket.accept()
        try:
            start = await asyncio.wait_for(read_start(websocket.receive_text), 10)
        except (PhoneError, TimeoutError, ValueError):
            await websocket.close(code=1008)
            return
        ticket = self.claim(str((start.get("customParameters") or {}).get("ticket", "")))
        if ticket is None:
            await websocket.close(code=1008)
            return
        from pipecat.serializers.twilio import TwilioFrameSerializer
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )

        settings = self.settings
        serializer = TwilioFrameSerializer(
            stream_sid=start["streamSid"],
            call_sid=start.get("callSid"),
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,  # hangs up the line when the call ends
        )
        transport = FastAPIWebsocketTransport(
            websocket,
            FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
                allowed_origins=[],  # Twilio sends no Origin; the ticket is the check
            ),
        )
        meta = ticket.metadata
        self.store.add_event(
            ticket.thread_id, "call", f"Phone call ({meta['direction']}) with {meta['caller']}"
        )
        call = CallState(
            self.runtime, ticket.thread_id, self.store.dot(ticket.dot_id), metadata=meta
        )
        await self.runtime.voice.run_pipeline(
            transport,
            call,
            audio_in_sample_rate=PHONE_SAMPLE_RATE,
            audio_out_sample_rate=PHONE_SAMPLE_RATE,
        )
