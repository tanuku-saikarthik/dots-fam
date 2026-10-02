"""HTTP API (FastAPI) + server-sent events for live runs."""

from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from .context import Stopped
from .db import Conflict, NotFound
from .delegation import DelegationError
from .models import (
    SUGGESTED_MODELS,
    ModelSetupError,
    configured_providers,
    missing_setup,
    parse_ref,
)
from .runs import Busy
from .runtime import Runtime
from .schedule import valid_timezone, validate_cron
from .team import BLUEPRINTS, ROSTER, install_blueprint, install_team
from .webhooks import Unauthorized, build_prompt, event_name, verify


# ---- request bodies ---------------------------------------------------------
def _model_ref(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    parse_ref(value)
    return value.strip()


class DotIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    title: str = Field("", max_length=60)
    instructions: str = Field(min_length=3, max_length=4000)
    model: str | None = None
    can_delegate: bool = False
    approval_mode: Literal["reversible", "autonomous"] = "reversible"
    research_allowed: bool = True
    memory_allowed: bool = True
    space_id: str | None = None
    space_ids: list[str] | None = None
    color: str = "blue"

    _check_model = field_validator("model")(lambda cls, v: _model_ref(v))


class DotPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    title: str | None = Field(None, max_length=60)
    instructions: str | None = Field(None, min_length=3, max_length=4000)
    model: str | None = None
    can_delegate: bool | None = None
    approval_mode: Literal["reversible", "autonomous"] | None = None
    research_allowed: bool | None = None
    memory_allowed: bool | None = None
    space_id: str | None = None
    space_ids: list[str] | None = None
    color: str | None = None

    _check_model = field_validator("model")(lambda cls, v: _model_ref(v) if v is not None else None)


class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=500)


class PageIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field("", max_length=200_000)
    parent_id: str | None = None


class PagePatch(BaseModel):
    expected_revision: int
    title: str | None = Field(None, min_length=1, max_length=160)
    content: str | None = Field(None, max_length=200_000)


class ThreadIn(BaseModel):
    dot_id: str
    title: str = Field("New conversation", max_length=160)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class Decision(BaseModel):
    decision: Literal["approved", "declined"]
    note: str | None = Field(None, max_length=500)


class TaskIn(BaseModel):
    thread_id: str
    prompt: str = Field(min_length=3, max_length=8000)
    cron: str | None = None
    timezone: str | None = None
    interval_seconds: int | None = Field(None, ge=60, le=31_536_000)

    @field_validator("cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return validate_cron(value) if value else None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value and not valid_timezone(value):
            raise ValueError("Unknown time zone.")
        return value


class ScheduleIn(BaseModel):
    cron: str | None = None
    timezone: str | None = None
    interval_seconds: int | None = Field(None, ge=60, le=31_536_000)

    _cron = field_validator("cron")(lambda cls, v: validate_cron(v) if v else None)


class TaskAction(BaseModel):
    action: Literal["run", "pause", "resume", "cancel"]


class TriggerIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    thread_id: str
    prompt: str = Field(min_length=3, max_length=4000)


class TriggerPatch(BaseModel):
    enabled: bool


class SettingsPatch(BaseModel):
    paused: bool | None = None
    research_allowed: bool | None = None
    memory_allowed: bool | None = None


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TeamInstall(BaseModel):
    chief_model: str | None = None
    worker_model: str | None = None
    _models = field_validator("chief_model", "worker_model")(lambda cls, v: _model_ref(v))


class BlueprintInstall(BaseModel):
    timezone: str | None = None


class ComputerPatch(BaseModel):
    enabled: bool | None = None
    browser: bool | None = None
    files: bool | None = None
    shell: bool | None = None


class VoiceOffer(BaseModel):
    sdp: str = Field(min_length=10, max_length=200_000)
    type: Literal["offer"] = "offer"
    pc_id: str | None = Field(None, max_length=200)
    restart_pc: bool = False
    thread_id: str | None = None


class VoiceIce(BaseModel):
    pc_id: str = Field(max_length=200)
    candidates: list[dict[str, Any]] = Field(max_length=50)


class VoiceHangup(BaseModel):
    pc_id: str = Field(max_length=200)


class HumanAction(BaseModel):
    x: float | None = Field(None, ge=0, le=1280)
    y: float | None = Field(None, ge=0, le=800)
    text: str | None = Field(None, max_length=16_000)
    key: str | None = Field(None, max_length=60)
    dy: int | None = Field(None, ge=-20_000, le=20_000)
    url: str | None = Field(None, max_length=2048)


# ---- app --------------------------------------------------------------------
def create_app(runtime: Runtime, static_dir: Path | None = None) -> FastAPI:
    settings, store = runtime.settings, runtime.store
    app = FastAPI(
        title="Dots Fam", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.runtime = runtime

    @app.exception_handler(NotFound)
    async def _not_found(_request: Request, error: NotFound):
        return JSONResponse({"error": str(error)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(_request: Request, error: Conflict):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(Busy)
    async def _busy(_request: Request, error: Busy):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(Stopped)
    async def _stopped(_request: Request, error: Stopped):
        return JSONResponse({"error": str(error)}, status_code=423)

    @app.exception_handler(ModelSetupError)
    async def _setup(_request: Request, error: ModelSetupError):
        return JSONResponse({"error": f"Setup required: {error}"}, status_code=503)

    @app.exception_handler(DelegationError)
    async def _delegation(_request: Request, error: DelegationError):
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value(_request: Request, error: ValueError):
        return JSONResponse({"error": str(error)}, status_code=400)

    def owner(request: Request, token: str | None = Query(None)) -> None:
        if not settings.owner_token:
            return
        supplied = (
            request.headers.get("authorization", "").removeprefix("Bearer ").strip() or token or ""
        )
        if not hmac.compare_digest(supplied.encode(), settings.owner_token.encode()):
            raise HTTPException(401, "Enter your owner token to unlock Dots Fam.")

    api = APIRouter(prefix="/api", dependencies=[Depends(owner)])

    def hook_url(request: Request, trigger_id: str) -> str:
        base = (settings.public_url or str(request.base_url)).rstrip("/")
        return f"{base}/hooks/{trigger_id}"

    def setup_status() -> dict[str, Any]:
        return {
            "missing": missing_setup(settings),
            "providers": configured_providers(settings),
            "default_model": settings.default_model,
            "worker_model": settings.worker_model,
            "timezone": settings.default_timezone,
            "computer_driver": settings.computer_driver,
            "web_search": bool(settings.exa_api_key),
            "slack": bool(runtime.slack),
            "voice": settings.voice_stack if runtime.voice is not None else "off",
        }

    # -- overview ---------------------------------------------------------------
    @api.get("/state")
    async def state() -> dict[str, Any]:
        running = {active.thread_id for active in runtime.runs.active_runs()}
        threads = store.threads()
        for thread in threads:
            thread["running"] = thread["id"] in running
            thread["pending_approvals"] = store.pending_approvals(thread["id"])
        working: set[str] = set()
        for active in runtime.runs.active_runs():
            try:
                working.add(store.thread(active.thread_id)["dot_id"])
            except NotFound:
                continue
        waiting: dict[str, int] = {}
        for approval in store.approvals(limit=500):
            if approval["status"] == "pending":
                waiting[approval["dot_id"]] = waiting.get(approval["dot_id"], 0) + 1
        return {
            "flags": store.flags(),
            "setup": setup_status(),
            "working_dots": sorted(working),
            "waiting_by_dot": waiting,
            "dots": store.dots(),
            "spaces": store.spaces(),
            "threads": threads,
            "memories": store.memories(),
            "pending_approvals": store.pending_approvals(),
            "models": SUGGESTED_MODELS,
        }

    @api.patch("/settings")
    async def update_settings(body: SettingsPatch) -> dict[str, bool]:
        flags = store.set_flags(**body.model_dump(exclude_none=True))
        if flags["paused"]:
            runtime.runs.cancel_all()
        return flags

    # -- dots --------------------------------------------------------------------
    @api.post("/dots", status_code=201)
    async def create_dot(body: DotIn) -> dict[str, Any]:
        data = body.model_dump()
        data["space_id"] = data["space_id"] or store.spaces()[0]["id"]
        return store.create_dot(**data)

    @api.patch("/dots/{dot_id}")
    async def update_dot(dot_id: str, body: DotPatch) -> dict[str, Any]:
        patch = body.model_dump(exclude_unset=True)
        return store.update_dot(dot_id, **patch)

    @api.delete("/dots/{dot_id}")
    async def delete_dot(dot_id: str) -> dict[str, bool]:
        dot = store.dot(dot_id)
        if runtime.computers is not None:
            await runtime.computers.stop(dot)
        store.delete_dot(dot_id)
        return {"ok": True}

    # -- spaces & pages -----------------------------------------------------------
    @api.post("/spaces", status_code=201)
    async def create_space(body: SpaceIn) -> dict[str, Any]:
        return store.create_space(body.name, body.description)

    @api.get("/spaces/{space_id}/pages")
    async def list_pages(space_id: str) -> list[dict[str, Any]]:
        return store.pages(space_id)

    @api.post("/spaces/{space_id}/pages", status_code=201)
    async def create_page(space_id: str, body: PageIn) -> dict[str, Any]:
        return store.create_page(space_id, body.title, body.content, body.parent_id)

    @api.get("/pages/{page_id}")
    async def get_page(page_id: str) -> dict[str, Any]:
        page = store.page(page_id)
        page["revisions"] = store.page_revisions(page_id)
        return page

    @api.patch("/pages/{page_id}")
    async def update_page(page_id: str, body: PagePatch) -> dict[str, Any]:
        return store.update_page(
            page_id,
            expected_revision=body.expected_revision,
            title=body.title,
            content=body.content,
        )

    @api.delete("/pages/{page_id}")
    async def delete_page(page_id: str) -> dict[str, bool]:
        store.delete_page(page_id)
        return {"ok": True}

    # -- conversations -----------------------------------------------------------
    @api.post("/threads", status_code=201)
    async def create_thread(body: ThreadIn) -> dict[str, Any]:
        return store.create_thread(body.dot_id, body.title)

    @api.get("/threads/{thread_id}")
    async def get_thread(thread_id: str) -> dict[str, Any]:
        thread = store.thread(thread_id)
        active = runtime.runs.active(thread_id)
        return {
            "thread": thread,
            "messages": await runtime.runs.history(thread_id),
            "approvals": [
                a for a in store.approvals(thread_id=thread_id) if a["status"] == "pending"
            ],
            "running": bool(active),
            "run_events": active.events if active else [],
        }

    @api.delete("/threads/{thread_id}")
    async def delete_thread(thread_id: str) -> dict[str, bool]:
        runtime.runs.cancel(thread_id)
        store.delete_thread(thread_id)
        return {"ok": True}

    @api.post("/threads/{thread_id}/messages", status_code=202)
    async def send_message(thread_id: str, body: MessageIn) -> dict[str, Any]:
        active = await runtime.runs.send(thread_id, body.text, source="owner")
        return {"run_id": active.run_id}

    @api.post("/threads/{thread_id}/stop")
    async def stop_thread(thread_id: str) -> dict[str, bool]:
        return {"stopped": runtime.runs.cancel(thread_id)}

    @api.get("/threads/{thread_id}/stream")
    async def stream(thread_id: str, request: Request) -> EventSourceResponse:
        store.thread(thread_id)
        queue = runtime.runs.subscribe(thread_id)
        active = runtime.runs.active(thread_id)
        replay = list(active.events) if active else []

        async def events():
            try:
                for event in replay:
                    yield {"data": json.dumps(event, default=str)}
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {"data": json.dumps(event, default=str)}
            finally:
                runtime.runs.unsubscribe(thread_id, queue)

        return EventSourceResponse(events())

    # -- activity ----------------------------------------------------------------
    @api.get("/activity")
    async def activity() -> dict[str, Any]:
        delegations = store.delegations(60)
        return {
            "approvals": store.approvals(limit=100),
            "delegations": delegations,
            "delegation_events": store.events(
                [d["worker_thread_id"] for d in delegations], limit=600
            ),
            "tasks": store.tasks(),
            "triggers": store.triggers(),
            "running": [
                {"thread_id": a.thread_id, "run_id": a.run_id, "source": a.source}
                for a in runtime.runs.active_runs()
            ],
        }

    @api.post("/approvals/{approval_id}")
    async def decide(approval_id: str, body: Decision) -> dict[str, Any]:
        approval, resumed = await runtime.decide(approval_id, body.decision, body.note)
        return {"approval": approval, "resumed": resumed}

    @api.post("/delegations/{delegation_id}/cancel")
    async def cancel_delegation(delegation_id: str) -> dict[str, bool]:
        if not runtime.delegations.cancel(delegation_id):
            raise Conflict("That work already finished.")
        return {"ok": True}

    # -- routines & triggers -----------------------------------------------------
    @api.post("/tasks", status_code=201)
    async def create_task(body: TaskIn) -> dict[str, Any]:
        return store.create_task(
            body.thread_id,
            body.prompt,
            cron=body.cron,
            timezone=body.timezone or settings.default_timezone,
            interval_seconds=body.interval_seconds,
            origin="routine" if body.cron or body.interval_seconds else "owner",
        )

    @api.post("/tasks/{task_id}/action")
    async def task_action(task_id: str, body: TaskAction) -> dict[str, Any]:
        before = store.task(task_id)
        task = store.task_action(task_id, body.action)
        if body.action in ("pause", "cancel") and before["status"] == "running":
            runtime.runs.cancel(task["thread_id"])
        return task

    @api.put("/tasks/{task_id}/schedule")
    async def schedule(task_id: str, body: ScheduleIn) -> dict[str, Any]:
        return store.schedule_task(
            task_id,
            cron=body.cron,
            timezone=body.timezone or settings.default_timezone,
            interval_seconds=body.interval_seconds,
        )

    @api.post("/triggers", status_code=201)
    async def create_trigger(body: TriggerIn, request: Request) -> dict[str, Any]:
        trigger, secret = store.create_trigger(body.name, body.thread_id, body.prompt)
        return {"trigger": trigger, "secret": secret, "url": hook_url(request, trigger["id"])}

    @api.patch("/triggers/{trigger_id}")
    async def patch_trigger(trigger_id: str, body: TriggerPatch) -> dict[str, Any]:
        return store.set_trigger_enabled(trigger_id, body.enabled)

    @api.post("/triggers/{trigger_id}/rotate")
    async def rotate_trigger(trigger_id: str) -> dict[str, str]:
        return {"secret": store.rotate_trigger(trigger_id)}

    @api.delete("/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str) -> dict[str, bool]:
        store.delete_trigger(trigger_id)
        return {"ok": True}

    # -- team ----------------------------------------------------------------------
    @api.get("/team")
    async def team(request: Request) -> dict[str, Any]:
        return {
            "roster": [{k: card[k] for k in ("name", "title", "chief")} for card in ROSTER],
            "blueprints": [
                {
                    "id": b.id,
                    "name": b.name,
                    "summary": b.summary,
                    "dot": b.dot,
                    "cron": b.cron,
                    "trigger": bool(b.trigger),
                    "needs": b.needs,
                }
                for b in BLUEPRINTS
            ],
            "hook_base": hook_url(request, "").rstrip("/"),
        }

    @api.post("/team/install", status_code=201)
    async def team_install(body: TeamInstall) -> dict[str, Any]:
        return install_team(store, body.chief_model, body.worker_model or settings.worker_model)

    @api.post("/blueprints/{blueprint_id}/install", status_code=201)
    async def blueprint_install(
        blueprint_id: str, body: BlueprintInstall, request: Request
    ) -> dict[str, Any]:
        timezone = body.timezone or settings.default_timezone
        if not valid_timezone(timezone):
            raise ValueError("Unknown time zone.")
        try:
            result = install_blueprint(store, blueprint_id, timezone)
        except LookupError as error:
            raise NotFound(str(error)) from error
        if result["trigger"]:
            result["url"] = hook_url(request, result["trigger"]["id"])
        return result

    # -- memories ------------------------------------------------------------------
    @api.post("/memories", status_code=201)
    async def add_memory(body: MemoryIn) -> dict[str, Any]:
        return store.save_memory(body.text)

    @api.put("/memories/{memory_id}")
    async def edit_memory(memory_id: str, body: MemoryIn) -> dict[str, Any]:
        return store.save_memory(body.text, memory_id)

    @api.delete("/memories/{memory_id}")
    async def remove_memory(memory_id: str) -> dict[str, bool]:
        store.delete_memory(memory_id)
        return {"ok": True}

    # -- computers ------------------------------------------------------------------
    def computers() -> Any:
        if runtime.computers is None:
            raise HTTPException(
                404, "Computers are off. Set COMPUTER_DRIVER=docker (or local) in .env and restart."
            )
        return runtime.computers

    async def computer_call(
        dot_id: str, method: str, path: str, body: Any = None, **kw: Any
    ) -> Any:
        from .computers import ComputerError

        try:
            return await computers().call(store.dot(dot_id), method, path, body, **kw)
        except ComputerError as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/computers/{dot_id}")
    async def computer_status(dot_id: str) -> dict[str, Any]:
        dot = store.dot(dot_id)
        if runtime.computers is None:
            return {"available": False, "driver": settings.computer_driver}
        return {"available": True, **await runtime.computers.status(dot)}

    @api.patch("/computers/{dot_id}")
    async def computer_permissions(dot_id: str, body: ComputerPatch) -> dict[str, Any]:
        manager = computers()
        dot = store.dot(dot_id)
        before = manager.permissions(dot)
        merged = {**(dot.get("computer") or {}), **body.model_dump(exclude_none=True)}
        dot = store.update_dot(dot_id, computer=merged)
        after = manager.permissions(dot)
        if not after["enabled"] or after["shell"] != before["shell"]:
            await manager.stop(dot)  # shell access is fixed when a computer starts
        return {"available": True, **await manager.status(dot)}

    @api.post("/computers/{dot_id}/{verb}")
    async def computer_power(dot_id: str, verb: Literal["start", "stop"]) -> dict[str, Any]:
        from .computers import ComputerError

        manager, dot = computers(), store.dot(dot_id)
        try:
            if verb == "start":
                await manager.ensure(dot)
            else:
                await manager.stop(dot)
        except ComputerError as error:
            raise HTTPException(409, str(error)) from error
        return {"available": True, **await manager.status(dot)}

    @api.get("/computers/{dot_id}/screen")
    async def computer_screen(dot_id: str) -> Response:
        image = await computer_call(
            dot_id, "GET", "/browser/screenshot", start=False, actor="human"
        )
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @api.post("/computers/{dot_id}/control/{verb}")
    async def computer_control(dot_id: str, verb: Literal["take", "release"]) -> dict[str, Any]:
        result = await computer_call(dot_id, "POST", f"/control/{verb}", start=False, actor="human")
        store.add_event(
            f"computer:{dot_id}",
            "computer",
            "You took control" if verb == "take" else "You handed control back",
        )
        return result

    @api.post("/computers/{dot_id}/human/{action}")
    async def computer_human(
        dot_id: str,
        action: Literal["click", "type", "key", "scroll", "navigate"],
        body: HumanAction,
    ) -> dict[str, Any]:
        payload = {
            "click": {"x": body.x, "y": body.y},
            "type": {"text": body.text},
            "key": {"key": body.key},
            "scroll": {"dy": body.dy},
            "navigate": {"url": body.url},
        }[action]
        if any(value is None for value in payload.values()):
            raise HTTPException(400, f"Missing fields for {action}.")
        return await computer_call(
            dot_id, "POST", f"/human/{action}", payload, start=False, actor="human"
        )

    @api.get("/computers/{dot_id}/files")
    async def computer_files(dot_id: str, path: str = "") -> dict[str, Any]:
        return await computer_call(dot_id, "GET", "/files", start=False, params={"path": path})

    @api.get("/computers/{dot_id}/files/read")
    async def computer_file(dot_id: str, path: str) -> dict[str, Any]:
        return await computer_call(dot_id, "GET", "/files/read", start=False, params={"path": path})

    # -- voice ------------------------------------------------------------------------
    def voice() -> Any:
        if runtime.voice is None:
            raise HTTPException(404, "Voice calls are off on this server.")
        return runtime.voice

    @api.get("/voice")
    async def voice_status() -> dict[str, Any]:
        if runtime.voice is None:
            return {"available": False, "reason": "Voice calls are off (VOICE_STACK=off)."}
        return runtime.voice.status()

    @api.post("/voice/{dot_id}/offer")
    async def voice_offer(dot_id: str, body: VoiceOffer) -> dict[str, Any]:
        from .voice import VoiceUnavailable

        if store.flags()["paused"]:
            raise Stopped("The team is paused.")
        try:
            return await voice().offer(
                dot_id,
                body.sdp,
                body.type,
                thread_id=body.thread_id,
                pc_id=body.pc_id,
                restart_pc=body.restart_pc,
            )
        except VoiceUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @api.patch("/voice/ice")
    async def voice_ice(body: VoiceIce) -> dict[str, bool]:
        await voice().ice(body.pc_id, body.candidates)
        return {"ok": True}

    @api.post("/voice/hangup")
    async def voice_hangup(body: VoiceHangup) -> dict[str, bool]:
        return {"ok": await voice().hangup(body.pc_id)}

    app.include_router(api)

    # -- public webhook ------------------------------------------------------------
    @app.post("/hooks/{trigger_id}", status_code=202)
    async def hook(trigger_id: str, request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > 256_000:
            return JSONResponse({"error": "Payload is too large."}, status_code=413)
        try:
            trigger = verify(store, trigger_id, request.headers, body)
        except Unauthorized as error:
            return JSONResponse({"error": str(error)}, status_code=401)
        except PermissionError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        if missing_setup(settings):
            return JSONResponse({"error": "Setup required."}, status_code=503)
        try:
            store.fire_trigger(trigger_id)
        except Conflict as error:
            return JSONResponse({"error": str(error)}, status_code=429)
        task = store.create_task(
            trigger["thread_id"],
            build_prompt(trigger, event_name(request.headers), body),
            trigger_id=trigger_id,
            origin="trigger",
        )
        return JSONResponse({"queued": task["id"]}, status_code=202)

    @app.get("/healthz")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    if static_dir and (static_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app
