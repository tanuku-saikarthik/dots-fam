"""Ring the owner's phone when a Dot needs them, for free.

Two channels, both optional and both free:
- Web push to the installed Dots Fam app (PWA). Keys are generated on first start.
- ntfy (ntfy.sh or self-hosted): an alarm-style alert in the ntfy phone app.

Either way the alert says "Vance is calling", and tapping Answer opens an incoming-call
screen that starts the live voice call, where the Dot reads out what needs approval.
No phone number, SIM or telephony provider involved.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .runs import RunOutcome
from .voice import describe_approval

log = logging.getLogger("dotsfam.notify")
RING_COOLDOWN_SECONDS = 60  # one ring per conversation per minute, however many approvals arrive


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Notifier:
    def __init__(self, runtime: Any, transport: httpx.AsyncBaseTransport | None = None):
        self.runtime = runtime
        self.store = runtime.store
        self.settings = runtime.settings
        self.transport = transport  # tests inject a fake ntfy
        self._vapid: Any = None
        self._public_key: str | None = None
        self._rung: set[str] = set()
        self._last_ring: dict[str, float] = {}
        self.sent: list[dict[str, Any]] = []  # recent rings, for the settings screen and tests
        runtime.runs.on_finished(self.on_run_finished)

    # ---- keys ----------------------------------------------------------------------
    def _load_vapid(self) -> None:
        if self._vapid is not None:
            return
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from py_vapid import Vapid01

        path = Path(self.settings.data_dir) / "vapid_private.pem"
        if path.exists():
            vapid = Vapid01.from_file(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            vapid = Vapid01()
            vapid.generate_keys()
            vapid.save_key(str(path))
            path.chmod(0o600)
        self._vapid = vapid
        self._public_key = _b64url(
            vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        )

    def public_key(self) -> str:
        self._load_vapid()
        assert self._public_key
        return self._public_key

    def status(self) -> dict[str, Any]:
        return {
            "push_devices": len(self.store.push_subscriptions()),
            "ntfy": bool(self.settings.ntfy_topic),
            "ntfy_topic": self.settings.ntfy_topic,
            "ntfy_server": self.settings.ntfy_server,
            "public_url": self.settings.public_url,
            "recent": self.sent[-5:],
        }

    # ---- ringing -------------------------------------------------------------------
    def call_link(self, dot_id: str, thread_id: str) -> str:
        path = f"/#/call/{dot_id}/{thread_id}"
        return f"{self.settings.public_url.rstrip('/')}{path}" if self.settings.public_url else path

    async def ring(self, dot: dict[str, Any], thread_id: str, reason: str) -> dict[str, int]:
        """Alert every registered phone: '<Dot> is calling', with the reason underneath."""
        title = f"{dot['name']} is calling"
        link = self.call_link(dot["id"], thread_id)
        payload = {
            "title": title,
            "body": reason,
            "url": f"/#/call/{dot['id']}/{thread_id}",
            "tag": thread_id,
        }
        results = {"push": 0, "ntfy": 0}
        results["push"] = await self._web_push(payload)
        if self.settings.ntfy_topic:
            # Anyone who guesses a topic on public ntfy.sh can read it, so details stay off it.
            public = not self.settings.ntfy_token and "://ntfy.sh" in self.settings.ntfy_server
            body = "Needs your OK. Answer to hear what's waiting." if public else reason
            results["ntfy"] = await self._ntfy(title, body, link)
        self.sent.append({"at": int(time.time() * 1000), "title": title, "body": reason, **results})
        self.sent = self.sent[-20:]
        self.store.add_event(thread_id, "call", f"Rang you: {reason[:160]}")
        return results

    async def _web_push(self, payload: dict[str, Any]) -> int:
        subscriptions = self.store.push_subscriptions()
        if not subscriptions:
            return 0
        self._load_vapid()
        from pywebpush import WebPushException, webpush

        delivered = 0
        for sub in subscriptions:
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=json.dumps(payload),
                    vapid_private_key=self._vapid,
                    vapid_claims={"sub": self.settings.vapid_contact},
                    ttl=120,
                    headers={"Urgency": "high"},
                )
                delivered += 1
            except WebPushException as error:
                status = getattr(error.response, "status_code", None)
                if status in (404, 410):  # the browser unsubscribed or the app was removed
                    self.store.remove_push_subscription(sub["endpoint"])
                else:
                    log.warning("Web push failed: %s", error)
            except Exception:  # noqa: BLE001 - one bad device shouldn't stop the others
                log.exception("Web push failed")
        return delivered

    async def _ntfy(self, title: str, body: str, link: str) -> int:
        headers = {
            "Title": title,
            "Priority": "urgent",
            "Tags": "telephone_receiver",
        }
        if link.startswith("http"):
            headers["Click"] = link
            headers["Actions"] = f"view, Answer, {link}, clear=true"
        if self.settings.ntfy_token:
            headers["Authorization"] = f"Bearer {self.settings.ntfy_token}"
        url = f"{self.settings.ntfy_server.rstrip('/')}/{self.settings.ntfy_topic}"
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                response = await client.post(url, content=body.encode(), headers=headers)
            return 1 if response.status_code < 300 else 0
        except httpx.HTTPError as error:
            log.warning("ntfy failed: %s", error)
            return 0

    # ---- when to ring --------------------------------------------------------------
    def _waiting_lines(self, root_thread: str) -> list[str]:
        root = self.runtime.root_thread
        lines = []
        for approval in self.store.approvals(limit=200):
            if approval["status"] != "pending" or approval["id"] in self._rung:
                continue
            if root(approval["thread_id"]) != root_thread:
                continue
            self._rung.add(approval["id"])
            lines.append(describe_approval(self.store, approval))
        return lines

    def pending_for(self, root_thread: str) -> list[str]:
        """What the incoming-call screen shows: everything waiting in this conversation."""
        root = self.runtime.root_thread
        return [
            describe_approval(self.store, a)
            for a in self.store.approvals(limit=200)
            if a["status"] == "pending" and root(a["thread_id"]) == root_thread
        ]

    async def on_run_finished(self, outcome: RunOutcome) -> None:
        if outcome.status != "waiting_approval":
            return
        if not self.store.push_subscriptions() and not self.settings.ntfy_topic:
            return
        try:
            root_thread = self.runtime.root_thread(outcome.thread_id)
            now = time.monotonic()
            if now - self._last_ring.get(root_thread, 0) < RING_COOLDOWN_SECONDS:
                return  # the open call screen lists everything that's waiting anyway
            lines = self._waiting_lines(root_thread)
            if not lines:
                return
            self._last_ring[root_thread] = now
            caller = self.store.dot(self.store.thread(root_thread)["dot_id"])
            await self.ring(
                caller,
                root_thread,
                lines[0] if len(lines) == 1 else f"{len(lines)} things need your OK. {lines[0]}",
            )
        except Exception:  # noqa: BLE001 - ringing must never break a run
            log.exception("Could not ring the owner")
