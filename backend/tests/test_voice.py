"""Voice: the call brain, approvals out loud, and a real WebRTC handshake (speech models faked)."""

from __future__ import annotations

import asyncio

import pytest

from dotsfam.voice import CallState, VoiceManager, spoken

from .conftest import call
from .test_engine import FakeOutbox
from .test_slack import settle

pipecat = pytest.importorskip("pipecat")


@pytest.fixture
def voice(runtime):
    manager = VoiceManager(runtime)
    runtime.attach_voice(manager)
    return manager


def test_written_answers_are_trimmed_for_speech():
    text = "**Two leads**\n\n| A | B |\n| - | - |\n| x | y |\n\n- Mara checked [both](https://x.example)."
    assert spoken(text) == "Two leads Mara checked both."
    long = "First point is fine. " * 40
    assert spoken(long).endswith("The rest is in the chat.") and len(spoken(long)) < 460


async def test_what_you_say_becomes_a_turn_in_the_dots_conversation(runtime, script):
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Call")
    state = CallState(runtime, thread["id"], vance)
    script.add("Vance", "Two launches need you today.")
    assert await state.hear("what needs me today") is None
    await settle(runtime)
    history = await runtime.runs.history(thread["id"])
    assert history[0] == {**history[0], "role": "user", "source": "voice"}
    assert history[-1]["text"] == "Two launches need you today."
    system = script.calls["vance"][0][0].content
    assert "voice call" in system and "no Markdown" in system


async def test_approvals_can_be_given_out_loud(runtime, script):
    outbox = FakeOutbox()
    runtime.runs.extra["outbox"] = outbox
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Call")
    state = CallState(runtime, thread["id"], vance)
    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {"assignments": [{"dot": "Cole", "brief": "Email Ana."}], "wait_seconds": 5},
        ),
        "Cole's email is waiting for your OK.",
        "Cole sent it.",
    )
    script.add("Cole", call("send_email", {"to": "ana@acme.example", "body": "Hi"}), "Sent.")
    await state.hear("get Cole to email Ana")
    await settle(runtime)
    [line] = state.unspoken_approvals()
    assert line == "Cole needs your OK: it sends an email, to ana@acme.example. Approve or decline?"
    assert state.unspoken_approvals() == []  # said once
    # Anything else waits for the decision.
    assert (await state.hear("what's the weather")).startswith("First, Cole needs your OK")
    assert outbox.executed == []
    assert await state.hear("Yes, send it.") == "Approved, going ahead."
    await settle(runtime)
    assert outbox.executed == [("ana@acme.example", "Hi")]
    [approval] = runtime.store.approvals(thread_id=runtime.store.approvals()[0]["thread_id"])
    assert approval["note"] == "Decided on a voice call"


async def test_the_call_brain_streams_the_dots_reply_into_speech(runtime, script):
    from pipecat.frames.frames import LLMContextFrame, TextFrame
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.tests.utils import SleepFrame, run_test

    from dotsfam.voice import _brain_class

    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Call")
    brain = _brain_class()(CallState(runtime, thread["id"], vance))
    script.add("Vance", "Morning! All green.")
    context = LLMContext([{"role": "user", "content": "how are we doing?"}])
    down, _up = await run_test(
        brain, frames_to_send=[LLMContextFrame(context=context), SleepFrame(sleep=1.0)]
    )
    said = "".join(f.text for f in down if isinstance(f, TextFrame))
    assert said == "Morning! All green."


async def test_voice_status_and_a_real_webrtc_call(runtime, voice, client, monkeypatch):
    aiortc = pytest.importorskip("aiortc")
    from pipecat.frames.frames import TextFrame
    from pipecat.processors.frame_processor import FrameProcessor

    heard: list[str] = []

    class PassThrough(FrameProcessor):
        def __init__(self, record: bool = False):
            super().__init__()
            self.record = record

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if self.record and isinstance(frame, TextFrame):
                heard.append(frame.text)
            await self.push_frame(frame, direction)

    # Whisper and Kokoro weights are not downloaded in CI; everything else is real.
    monkeypatch.setattr(
        VoiceManager, "services", lambda self: (PassThrough(), PassThrough(record=True))
    )
    monkeypatch.setattr("dotsfam.voice.check_stack", lambda _settings: None)
    runtime.settings.voice_ice_servers = ""
    status = (await client.get("/api/voice")).json()
    assert status["available"] is True and status["stack"] == "local"

    vance = runtime.store.dot_by_name("Vance")
    pc = aiortc.RTCPeerConnection()
    pc.addTrack(_Silence())
    pc.createDataChannel("chat")
    await pc.setLocalDescription(await pc.createOffer())
    response = await client.post(
        f"/api/voice/{vance['id']}/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"}
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["type"] == "answer" and answer["pc_id"] and answer["thread_id"]
    await pc.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer["sdp"], type="answer"))
    for _ in range(100):
        if pc.connectionState == "connected" and voice.status()["active_calls"] == 1:
            break
        await asyncio.sleep(0.1)
    assert pc.connectionState == "connected"
    events = [e["text"] for e in runtime.store.events([answer["thread_id"]])]
    assert "Call with Vance started" in events
    # The greeting reached the speech step (a recorder here, Kokoro in production).
    for _ in range(30):
        if heard:
            break
        await asyncio.sleep(0.1)
    assert heard == ["Hi, it's Vance. What should the team take on?"]
    hangup = await client.post("/api/voice/hangup", json={"pc_id": answer["pc_id"]})
    assert hangup.json() == {"ok": True}
    await pc.close()
    for _ in range(50):
        if voice.status()["active_calls"] == 0:
            break
        await asyncio.sleep(0.1)
    assert voice.status()["active_calls"] == 0


def _silence_track():
    import fractions

    import numpy as np
    from aiortc.mediastreams import AudioStreamTrack
    from av import AudioFrame

    class Silence(AudioStreamTrack):
        _pts = 0

        async def recv(self):
            await asyncio.sleep(0.02)
            frame = AudioFrame.from_ndarray(np.zeros((1, 320), dtype=np.int16), layout="mono")
            frame.sample_rate = 16000
            frame.pts = self._pts
            frame.time_base = fractions.Fraction(1, 16000)
            self._pts += 320
            return frame

    return Silence()


def _Silence():  # noqa: N802 - reads like a class at the call site
    return _silence_track()
