"""Voice calls with a Dot, built on Pipecat (BSD-2) over peer-to-peer WebRTC.

Open models by default (VOICE_STACK=local):
- Speech-to-text: Whisper Large V3 Turbo via faster-whisper (MIT)
- Text-to-speech: Kokoro-82M via kokoro-onnx (Apache-2.0)
- Turn-taking: Silero VAD + Smart Turn v3 (BSD-2), so the Dot waits until you finish a thought
VOICE_STACK=openai swaps only the ears and voice for OpenAI's speech models.

The brain is always the Dot itself: what you say becomes a turn in its conversation, with
the same tools, team, pages and Reversibility Law. Approvals can be given out loud.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .db import Conflict, NotFound
from .runs import Busy, RunOutcome

log = logging.getLogger("dotsfam.voice")

YES = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve[ds]?|go ahead|do it|send it|ship it|confirm(ed)?|sure|okay|ok)\b",
    re.I,
)
NO = re.compile(
    r"^\W*(no|nope|decline[ds]?|don't|do not|stop|cancel|hold off|reject(ed)?|not now)\b", re.I
)
DETAIL_KEYS = ("to", "recipient", "channel", "subject", "url", "title", "command")


def spoken(text: str, limit: int = 420) -> str:
    """A short, speakable version of a written answer."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"(?m)^\s*\|.*\|\s*$", "", text)  # tables do not read aloud
    text = re.sub(r"[*_#>`|]+", "", text)
    text = re.sub(r"(?m)^\s*[-•]\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (
        cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"
    ) + " The rest is in the chat."


def describe_approval(store: Any, approval: dict[str, Any]) -> str:
    dot = store.dot(approval["dot_id"])
    details = [
        f"{key} {str(approval['args'][key])[:80]}"
        for key in DETAIL_KEYS
        if isinstance(approval["args"].get(key), str)
    ]
    detail = f", {', '.join(details[:2])}" if details else ""
    return f"{dot['name']} needs your OK: it {approval['reason']}{detail}. Approve or decline?"


@dataclass
class CallState:
    """What one call has heard and said; independent of Pipecat so it can be tested."""

    runtime: Any
    thread_id: str
    dot: dict[str, Any]
    spoken_approvals: set[str] = field(default_factory=set)
    muted_runs: set[str] = field(default_factory=set)
    sources: dict[str, str] = field(default_factory=dict)

    def pending(self) -> list[dict[str, Any]]:
        store, root = self.runtime.store, self.runtime.root_thread
        items = [
            a
            for a in store.approvals(limit=200)
            if a["status"] == "pending" and root(a["thread_id"]) == self.thread_id
        ]
        return sorted(items, key=lambda a: a["created_at"])

    def unspoken_approvals(self) -> list[str]:
        lines = []
        for approval in self.pending():
            if approval["id"] not in self.spoken_approvals:
                self.spoken_approvals.add(approval["id"])
                lines.append(describe_approval(self.runtime.store, approval))
        return lines

    async def hear(self, text: str) -> str | None:
        """Handle one finished user turn. Returns something to say right away, if any."""
        text = text.strip()
        if not text:
            return None
        pending = self.pending()
        if pending:
            approval = pending[0]
            if YES.search(text) or NO.search(text):
                decision = "approved" if YES.search(text) else "declined"
                try:
                    await self.runtime.decide(approval["id"], decision, "Decided on a voice call")
                except (Conflict, NotFound):
                    return "That one was already decided."
                left = len(pending) - 1
                said = (
                    "Approved, going ahead." if decision == "approved" else "Declined. I'll adapt."
                )
                if left:
                    said += " " + describe_approval(self.runtime.store, self.pending()[0])
                return said
            return "First, " + describe_approval(self.runtime.store, approval)
        try:
            await self.runtime.runs.send(self.thread_id, text, source="voice")
            return None
        except Busy:
            self.runtime.store.create_task(self.thread_id, text, origin="voice")
            return "Got it, I'll take that next."
        except Conflict as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 - say it instead of going silent
            return f"I couldn't start on that: {error}"


class VoiceUnavailable(RuntimeError):
    pass


def check_stack(settings: Any) -> str | None:
    """None when calls can work, else why not (shown in the UI)."""
    if settings.voice_stack == "off":
        return "Voice calls are off (VOICE_STACK=off)."
    try:
        import pipecat  # noqa: F401
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: F401
    except ImportError:
        return "Install voice support: pip install 'dotsfam[voice]'"
    if settings.voice_stack == "openai":
        if not settings.openai_api_key:
            return "VOICE_STACK=openai needs OPENAI_API_KEY."
        return None
    try:
        import faster_whisper  # noqa: F401
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return "Install voice support: pip install 'dotsfam[voice]'"
    return None


def _brain_class():
    """The Pipecat processor that connects a call to a Dot (defined lazily: Pipecat is optional)."""
    from pipecat.frames.frames import (
        CancelFrame,
        EndFrame,
        Frame,
        InterruptionFrame,
        LLMContextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        StartFrame,
        TextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class DotBrain(FrameProcessor):
        def __init__(self, call: CallState):
            super().__init__()
            self.call = call
            self._queue: asyncio.Queue | None = None
            self._consumer: asyncio.Task | None = None
            self._open = False
            self._current: str | None = None

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                await self.push_frame(frame, direction)
                runs = self.call.runtime.runs
                self._queue = runs.subscribe(self.call.thread_id)
                active = runs.active(self.call.thread_id)
                if active:
                    self.call.muted_runs.add(active.run_id)  # don't read out half an answer
                runs.on_finished(self._on_any_run)
                self._consumer = self.create_task(self._consume(), "dotsfam-voice-consumer")
                return
            if isinstance(frame, (EndFrame, CancelFrame)):
                await self._shutdown()
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, InterruptionFrame):
                if self._current:
                    self.call.muted_runs.add(self._current)  # the owner talked over the answer
                self._open = False
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, LLMContextFrame):
                if frame.speculation:
                    return
                reply = await self.call.hear(_last_user_text(frame))
                if reply:
                    await self.say(reply)
                return
            await self.push_frame(frame, direction)

        async def say(self, text: str) -> None:
            await self._end()
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(TextFrame(text))
            await self.push_frame(LLMFullResponseEndFrame())

        async def _start(self) -> None:
            if not self._open:
                self._open = True
                await self.push_frame(LLMFullResponseStartFrame())

        async def _end(self) -> None:
            if self._open:
                self._open = False
                await self.push_frame(LLMFullResponseEndFrame())

        async def _consume(self) -> None:
            assert self._queue is not None
            while True:
                event = await self._queue.get()
                try:
                    await self._event(event)
                except Exception:  # noqa: BLE001
                    log.exception("Voice event failed")

        async def _event(self, event: dict[str, Any]) -> None:
            run_id = event.get("run_id")
            kind = event.get("type")
            if kind == "run.started":
                self._current = run_id
                self.call.sources[run_id] = event.get("source", "")
                return
            live = self.call.sources.get(run_id) == "voice" and run_id not in self.call.muted_runs
            if kind == "message.delta" and live:
                await self._start()
                frame = TextFrame(str(event.get("delta", "")))
                frame.includes_inter_frame_spaces = True
                await self.push_frame(frame)
            elif kind == "tool.started" and live and event.get("name") == "delegate_tasks":
                await self._end()
                await self.say("Handing that to the team.")
            elif kind == "run.finished":
                source = self.call.sources.pop(run_id, "")
                await self._end()
                if source != "voice" and event.get("status") == "completed" and event.get("text"):
                    await self.say(spoken(event["text"]))  # team updates and routines
                elif event.get("status") == "failed":
                    await self.say("Sorry, that didn't work. The details are in the chat.")
                for line in self.call.unspoken_approvals():
                    await self.say(line)
                self._current = None

        async def _on_any_run(self, outcome: RunOutcome) -> None:
            # Specialists' approvals belong to this call's conversation too.
            if outcome.status == "waiting_approval" and outcome.thread_id != self.call.thread_id:
                if self.call.runtime.root_thread(outcome.thread_id) == self.call.thread_id:
                    for line in self.call.unspoken_approvals():
                        await self.say(line)

        async def _shutdown(self) -> None:
            runs = self.call.runtime.runs
            runs.off_finished(self._on_any_run)
            if self._queue is not None:
                runs.unsubscribe(self.call.thread_id, self._queue)
                self._queue = None
            if self._consumer is not None:
                await self.cancel_task(self._consumer)
                self._consumer = None

        async def cleanup(self):
            await self._shutdown()
            await super().cleanup()

    return DotBrain


def _last_user_text(frame: Any) -> str:
    for message in reversed(frame.context.get_messages()):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            return str(content or "")
    return ""


_WHISPER_MODELS: dict[tuple[str, str], Any] = {}


class VoiceManager:
    """WebRTC signalling + one Pipecat pipeline per call."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self.settings = runtime.settings
        self._handler: Any = None
        self._calls: dict[str, asyncio.Task] = {}

    def status(self) -> dict[str, Any]:
        problem = check_stack(self.settings)
        return {
            "available": problem is None,
            "reason": problem,
            "stack": self.settings.voice_stack,
            "ice_servers": self.settings.ice_servers,
            "active_calls": len(self._calls),
        }

    def _request_handler(self) -> Any:
        if self._handler is None:
            from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

            self._handler = SmallWebRTCRequestHandler(ice_servers=self.settings.ice_servers or None)
        return self._handler

    async def offer(
        self,
        dot_id: str,
        sdp: str,
        sdp_type: str,
        *,
        thread_id: str | None = None,
        pc_id: str | None = None,
        restart_pc: bool = False,
    ) -> dict[str, Any]:
        problem = check_stack(self.settings)
        if problem:
            raise VoiceUnavailable(problem)
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

        store = self.runtime.store
        dot = store.dot(dot_id)
        if thread_id:
            thread = store.thread(thread_id)
            if thread["dot_id"] != dot_id or thread["internal"]:
                raise ValueError("That conversation belongs to another Dot.")
        else:
            thread = store.create_thread(dot_id, f"Call with {dot['name']}")
        call = CallState(self.runtime, thread["id"], dot)

        async def connected(connection: Any) -> None:
            task = asyncio.create_task(self._run_call(connection, call), name="dotsfam-call")
            self._calls[connection.pc_id] = task
            task.add_done_callback(lambda _t, pc=connection.pc_id: self._calls.pop(pc, None))

        answer = await self._request_handler().handle_web_request(
            SmallWebRTCRequest(sdp=sdp, type=sdp_type, pc_id=pc_id, restart_pc=restart_pc),
            connected,
        )
        return {**(answer or {}), "thread_id": thread["id"]}

    async def ice(self, pc_id: str, candidates: list[dict[str, Any]]) -> None:
        from pipecat.transports.smallwebrtc.request_handler import (
            IceCandidate,
            SmallWebRTCPatchRequest,
        )

        await self._request_handler().handle_patch_request(
            SmallWebRTCPatchRequest(
                pc_id=pc_id,
                candidates=[
                    IceCandidate(
                        candidate=c.get("candidate", ""),
                        sdp_mid=c.get("sdp_mid") or c.get("sdpMid") or "0",
                        sdp_mline_index=int(c.get("sdp_mline_index", c.get("sdpMLineIndex", 0))),
                    )
                    for c in candidates
                ],
            )
        )

    async def hangup(self, pc_id: str) -> bool:
        task = self._calls.get(pc_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def close(self) -> None:
        tasks = list(self._calls.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._handler is not None:
            await self._handler.close()

    def services(self) -> tuple[Any, Any]:
        """Speech-to-text and text-to-speech for the configured stack."""
        from pipecat.transcriptions.language import Language
        from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

        settings = self.settings
        language = Language(settings.voice_language)
        if settings.voice_stack == "openai":
            from pipecat.services.openai.stt import OpenAISTTService
            from pipecat.services.openai.tts import OpenAITTSService

            stt = OpenAISTTService(
                api_key=settings.openai_api_key,
                settings=OpenAISTTService.Settings(
                    model=settings.voice_openai_stt_model, language=language
                ),
            )
            tts = OpenAITTSService(
                api_key=settings.openai_api_key,
                settings=OpenAITTSService.Settings(
                    model=settings.voice_openai_tts_model, voice=settings.voice_openai_voice
                ),
                text_filters=[MarkdownTextFilter()],
            )
            return stt, tts

        from pipecat.services.kokoro.tts import KokoroTTSService
        from pipecat.services.whisper.stt import Model, WhisperSTTService

        aliases = {"large-v3-turbo": Model.LARGE_V3_TURBO.value, "large-v3": Model.LARGE.value}
        model = aliases.get(settings.voice_whisper_model, settings.voice_whisper_model)

        class SharedWhisper(WhisperSTTService):
            """Load the Whisper weights once per process instead of once per call."""

            def _load(self) -> None:
                key = (model, settings.voice_whisper_device)
                if key not in _WHISPER_MODELS:
                    super()._load()
                    _WHISPER_MODELS[key] = self._model
                self._model = _WHISPER_MODELS[key]

        stt = SharedWhisper(
            device=settings.voice_whisper_device,
            settings=WhisperSTTService.Settings(model=model, language=language),
        )
        tts = KokoroTTSService(
            settings=KokoroTTSService.Settings(
                voice=settings.voice_kokoro_voice, language=language
            ),
            text_filters=[MarkdownTextFilter()],
        )
        return stt, tts

    async def _run_call(self, connection: Any, call: CallState) -> None:
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )
        from pipecat.transports.base_transport import TransportParams
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.workers.runner import WorkerRunner

        store = self.runtime.store
        store.add_event(call.thread_id, "voice", f"Call with {call.dot['name']} started")
        try:
            transport = SmallWebRTCTransport(
                webrtc_connection=connection,
                params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
            )
            stt, tts = await asyncio.to_thread(self.services)  # model loading blocks
            brain = _brain_class()(call)
            user, assistant = LLMContextAggregatorPair(
                LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
            )
            pipeline = Pipeline(
                [transport.input(), stt, user, brain, tts, transport.output(), assistant]
            )
            worker = PipelineWorker(pipeline, params=PipelineParams(), idle_timeout_secs=900)
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)

            @transport.event_handler("on_client_connected")
            async def _connected(_transport: Any, _client: Any) -> None:
                name = call.dot["name"]
                greeting = f"Hi, it's {name}." + (
                    " What should the team take on?"
                    if call.dot["can_delegate"]
                    else " What do you need?"
                )
                lines = call.unspoken_approvals()
                await brain.say(" ".join([greeting, *lines]))

            @transport.event_handler("on_client_disconnected")
            async def _disconnected(_transport: Any, _client: Any) -> None:
                await runner.cancel()

            await runner.run()
        except asyncio.CancelledError:
            await connection.disconnect()
            raise
        except Exception:  # noqa: BLE001
            log.exception("Voice call failed")
        finally:
            with contextlib.suppress(Exception):
                store.add_event(call.thread_id, "voice", f"Call with {call.dot['name']} ended")
