"""A computer for each Dot: a persistent browser, a workspace and an optional shell.

Drivers:
- docker: one container per Dot from the `dotsfam-computer` image (recommended).
- local:  one process per Dot on this machine (development only; shell off by default).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .config import Settings
from .context import DotContext
from .db import Store
from .reversibility import Snapshots, classify

log = logging.getLogger("dotsfam.computers")
SERVICE = Path(__file__).with_name("computer_service.py")
DEFAULT_PERMISSIONS = {"enabled": False, "browser": True, "files": True, "shell": False}


class ComputerError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalDriver:
    """Runs computer_service.py as a child process per Dot. No isolation: development only."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "computers"
        self._procs: dict[str, tuple[asyncio.subprocess.Process, int]] = {}

    async def endpoint(self, dot_id: str) -> str | None:
        entry = self._procs.get(dot_id)
        if entry and entry[0].returncode is None:
            return f"http://127.0.0.1:{entry[1]}"
        return None

    async def start(self, dot_id: str, token: str, shell: bool) -> str:
        existing = await self.endpoint(dot_id)
        if existing:
            return existing
        port = _free_port()
        args = [
            sys.executable,
            str(SERVICE),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data",
            str(self.root / dot_id),
            "--token",
            token,
        ]
        chromium = os.environ.get("DOTSFAM_CHROMIUM")
        if chromium:
            args += ["--chromium", chromium]
        if not shell:
            args.append("--no-shell")
        (self.root / dot_id).mkdir(parents=True, exist_ok=True)
        with (self.root / dot_id / "service.log").open("ab") as log_file:
            process = await asyncio.create_subprocess_exec(*args, stdout=log_file, stderr=log_file)
        self._procs[dot_id] = (process, port)
        return f"http://127.0.0.1:{port}"

    async def stop(self, dot_id: str) -> None:
        entry = self._procs.pop(dot_id, None)
        if entry and entry[0].returncode is None:
            entry[0].terminate()
            try:
                await asyncio.wait_for(entry[0].wait(), 10)
            except TimeoutError:
                entry[0].kill()

    async def close(self) -> None:
        for dot_id in list(self._procs):
            await self.stop(dot_id)


class DockerDriver:
    """One container per Dot. Files and browser profile live in a named volume."""

    def __init__(self, settings: Settings):
        import docker  # optional dependency

        self.settings = settings
        self.client = docker.from_env()

    def _name(self, dot_id: str) -> str:
        return f"dotsfam-computer-{dot_id[:16]}"

    def _endpoint(self, container: Any) -> str | None:
        container.reload()
        if container.status != "running":
            return None
        binding = (container.attrs["NetworkSettings"]["Ports"] or {}).get("8800/tcp")
        return f"http://127.0.0.1:{binding[0]['HostPort']}" if binding else None

    async def endpoint(self, dot_id: str) -> str | None:
        import docker

        def find() -> str | None:
            try:
                return self._endpoint(self.client.containers.get(self._name(dot_id)))
            except docker.errors.NotFound:
                return None

        return await asyncio.to_thread(find)

    async def start(self, dot_id: str, token: str, shell: bool) -> str:
        import docker

        def run() -> str:
            name = self._name(dot_id)
            try:
                container = self.client.containers.get(name)
                if container.labels.get("dotsfam.shell") != ("1" if shell else "0"):
                    container.remove(force=True)  # the volume keeps files and logins
                    raise docker.errors.NotFound("recreate")
                if container.status != "running":
                    container.start()
            except docker.errors.NotFound:
                container = self.client.containers.run(
                    self.settings.computer_image,
                    name=name,
                    detach=True,
                    environment={"DOTSFAM_TOKEN": token, "DOTSFAM_SHELL": "1" if shell else "0"},
                    volumes={f"dotsfam-{dot_id[:16]}-data": {"bind": "/data", "mode": "rw"}},
                    ports={"8800/tcp": ("127.0.0.1", None)},
                    mem_limit="2g",
                    security_opt=["no-new-privileges"],
                    restart_policy={"Name": "unless-stopped"},
                    labels={"dotsfam.dot": dot_id, "dotsfam.shell": "1" if shell else "0"},
                )
            for _ in range(30):
                endpoint = self._endpoint(container)
                if endpoint:
                    return endpoint
                time.sleep(0.5)
            raise ComputerError(
                "The computer container did not start. Check `docker logs " + name + "`."
            )

        return await asyncio.to_thread(run)

    async def stop(self, dot_id: str) -> None:
        import docker

        def halt() -> None:
            try:
                self.client.containers.get(self._name(dot_id)).stop(timeout=10)
            except docker.errors.NotFound:
                pass

        await asyncio.to_thread(halt)

    async def close(self) -> None:
        return None


class Url(BaseModel):
    url: str = Field(description="Full http(s) URL.", max_length=2048)


class Ref(BaseModel):
    ref: str = Field(description="Element ref from your latest snapshot, e.g. e12.")
    snapshot_id: int = Field(description="snapshot_id of that snapshot.")


class TypeArgs(Ref):
    text: str = Field(max_length=16_000)
    submit: bool = Field(False, description="Press Enter after typing.")


class KeyArgs(BaseModel):
    key: str = Field(description="Key or chord, e.g. Enter, Escape, Control+A.", max_length=60)


class ScrollArgs(BaseModel):
    dy: int = Field(800, ge=-20_000, le=20_000, description="Pixels; negative scrolls up.")


class PathArgs(BaseModel):
    path: str = Field("", max_length=1024, description="Path inside your workspace.")


class WriteArgs(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=500_000)
    append: bool = False


class ShellArgs(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    timeout_seconds: int = Field(30, ge=1, le=120)


class ComputerManager:
    def __init__(self, store: Store, settings: Settings, driver: Any):
        self.store = store
        self.settings = settings
        self.driver = driver
        self.snapshots = Snapshots()
        self._master = settings.computer_token or secrets.token_urlsafe(32)
        self._starting: dict[str, asyncio.Lock] = {}

    @classmethod
    def create(cls, store: Store, settings: Settings) -> ComputerManager:
        driver = (
            DockerDriver(settings)
            if settings.computer_driver == "docker"
            else LocalDriver(settings)
        )
        return cls(store, settings, driver)

    # ---- permissions & lifecycle -------------------------------------------
    def permissions(self, dot: dict[str, Any]) -> dict[str, bool]:
        perms = {**DEFAULT_PERMISSIONS, **(dot.get("computer") or {})}
        if isinstance(self.driver, LocalDriver) and os.environ.get("DOTSFAM_LOCAL_SHELL") != "1":
            perms["shell"] = False  # never run agent shells on the host by accident
        return perms

    def enabled_for(self, dot: dict[str, Any]) -> bool:
        return bool(self.permissions(dot)["enabled"])

    def token(self, dot_id: str) -> str:
        return hmac.new(self._master.encode(), dot_id.encode(), hashlib.sha256).hexdigest()

    async def ensure(self, dot: dict[str, Any]) -> str:
        if not self.enabled_for(dot):
            raise ComputerError(
                f"{dot['name']}'s computer is off. The owner can turn it on in Team and setup."
            )
        lock = self._starting.setdefault(dot["id"], asyncio.Lock())
        async with lock:
            endpoint = await self.driver.endpoint(dot["id"])
            if endpoint and await self._ready(endpoint, dot["id"], attempts=1):
                return endpoint
            endpoint = await self.driver.start(
                dot["id"], self.token(dot["id"]), self.permissions(dot)["shell"]
            )
            if not await self._ready(endpoint, dot["id"]):
                raise ComputerError("The computer did not come up. Check the computer driver logs.")
            self.store.add_event(f"computer:{dot['id']}", "computer", "Computer started")
            return endpoint

    async def _ready(self, endpoint: str, dot_id: str, attempts: int = 60) -> bool:
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(attempts):
                try:
                    response = await client.get(
                        f"{endpoint}/status",
                        headers={"Authorization": f"Bearer {self.token(dot_id)}"},
                    )
                    if response.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        return False

    async def status(self, dot: dict[str, Any]) -> dict[str, Any]:
        endpoint = await self.driver.endpoint(dot["id"])
        info: dict[str, Any] = {
            "running": False,
            "permissions": self.permissions(dot),
            "driver": self.settings.computer_driver,
        }
        if endpoint:
            try:
                info.update(await self.call(dot, "GET", "/status", start=False), running=True)
            except ComputerError:
                pass
        info["activity"] = self.store.events([f"computer:{dot['id']}"], limit=40)
        return info

    async def stop(self, dot: dict[str, Any]) -> None:
        await self.driver.stop(dot["id"])
        self.store.add_event(f"computer:{dot['id']}", "computer", "Computer stopped")

    async def close(self) -> None:
        await self.driver.close()

    # ---- calls -------------------------------------------------------------
    async def call(
        self,
        dot: dict[str, Any],
        method: str,
        path: str,
        body: Any = None,
        *,
        actor: str = "agent",
        start: bool = True,
        params: dict | None = None,
    ) -> Any:
        endpoint = await self.ensure(dot) if start else await self.driver.endpoint(dot["id"])
        if not endpoint:
            raise ComputerError("The computer is not running.")
        try:
            async with httpx.AsyncClient(timeout=150) as client:
                response = await client.request(
                    method,
                    f"{endpoint}{path}",
                    json=body,
                    params=params,
                    headers={"Authorization": f"Bearer {self.token(dot['id'])}", "X-Actor": actor},
                )
        except httpx.HTTPError as error:
            raise ComputerError(
                f"Could not reach {dot['name']}'s computer: {type(error).__name__}"
            ) from error
        if response.status_code < 400 and response.headers.get("content-type", "").startswith(
            "image/"
        ):
            return response.content
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {"error": response.text[:300]}
        if response.status_code >= 400:
            raise ComputerError(
                data.get("detail") or data.get("error") or f"Computer error {response.status_code}"
            )
        return data

    def _require(self, dot: dict[str, Any], capability: str) -> None:
        perms = self.permissions(dot)
        if not perms["enabled"] or not perms[capability]:
            raise ComputerError(
                f"{capability.capitalize()} access is off for {dot['name']}'s computer."
            )

    # ---- agent tools ---------------------------------------------------------
    def tools(self, ctx: DotContext) -> list[BaseTool]:
        dot, snapshots = ctx.dot, self.snapshots
        perms = self.permissions(dot)

        def log(text: str) -> None:
            self.store.add_event(f"computer:{dot['id']}", "computer", text, ctx.run_id)

        def gate(action: str):
            def check(args: dict[str, Any]) -> str | None:
                try:
                    return classify(snapshots, dot["id"], action, args)
                except ValueError:
                    return None  # the tool itself refuses stale refs before acting

            return check

        async def open_url(url: str) -> dict:
            ctx.check()
            self._require(dot, "browser")
            result = await self.call(dot, "POST", "/browser/navigate", {"url": url})
            log(f"Opened {result.get('url')}")
            return {**result, "next": "Take computer_snapshot to see what you can click or fill."}

        async def snapshot() -> dict:
            ctx.check()
            self._require(dot, "browser")
            result = await self.call(dot, "POST", "/browser/snapshot")
            snapshots.remember(dot["id"], result)
            return result

        async def read() -> dict:
            ctx.check()
            self._require(dot, "browser")
            return await self.call(dot, "GET", "/browser/text")

        async def click(ref: str, snapshot_id: int) -> dict:
            ctx.check()
            self._require(dot, "browser")
            element = snapshots.element(dot["id"], ref, snapshot_id)
            result = await self.call(
                dot, "POST", "/browser/click", {"ref": ref, "snapshot_id": snapshot_id}
            )
            log(f"Clicked {element.get('role')} “{element.get('name', '')[:60]}”")
            return result

        async def type_text(ref: str, snapshot_id: int, text: str, submit: bool = False) -> dict:
            ctx.check()
            self._require(dot, "browser")
            element = snapshots.element(dot["id"], ref, snapshot_id)
            result = await self.call(
                dot,
                "POST",
                "/browser/type",
                {"ref": ref, "snapshot_id": snapshot_id, "text": text, "submit": submit},
            )
            log(
                f"Typed into {element.get('role')} “{element.get('name', '')[:60]}”{' and submitted' if submit else ''}"
            )
            return result

        async def press(key: str) -> dict:
            ctx.check()
            self._require(dot, "browser")
            return await self.call(dot, "POST", "/browser/key", {"key": key})

        async def scroll(dy: int = 800) -> dict:
            ctx.check()
            self._require(dot, "browser")
            return await self.call(dot, "POST", "/browser/scroll", {"dy": dy})

        async def list_files(path: str = "") -> dict:
            ctx.check()
            self._require(dot, "files")
            return await self.call(dot, "GET", "/files", params={"path": path})

        async def read_file(path: str) -> dict:
            ctx.check()
            self._require(dot, "files")
            return await self.call(dot, "GET", "/files/read", params={"path": path})

        async def write_file(path: str, content: str, append: bool = False) -> dict:
            ctx.check()
            self._require(dot, "files")
            log(f"Wrote {path}")
            return await self.call(
                dot, "POST", "/files/write", {"path": path, "content": content, "append": append}
            )

        async def shell(command: str, timeout_seconds: int = 30) -> dict:
            ctx.check()
            self._require(dot, "shell")
            log("Ran a shell command")
            return await self.call(
                dot, "POST", "/exec", {"command": command, "timeout_seconds": timeout_seconds}
            )

        def tool(fn, name, schema, description, action=None) -> BaseTool:
            return StructuredTool.from_function(
                coroutine=fn,
                name=name,
                args_schema=schema,
                description=description,
                metadata={"classify": gate(action)} if action else None,
            )

        class Empty(BaseModel):
            pass

        tools: list[BaseTool] = []
        if perms["browser"]:
            tools += [
                tool(
                    open_url,
                    "computer_open",
                    Url,
                    "Open a URL in your own persistent browser (logins are kept).",
                ),
                tool(
                    snapshot,
                    "computer_snapshot",
                    Empty,
                    "List what is on the page you can click or fill, as refs with roles and names.",
                ),
                tool(read, "computer_read", Empty, "Read the current page's text."),
                tool(
                    click,
                    "computer_click",
                    Ref,
                    "Click an element by ref from your latest snapshot.",
                    "click",
                ),
                tool(
                    type_text,
                    "computer_type",
                    TypeArgs,
                    "Fill a field by ref; submit=True presses Enter.",
                    "type",
                ),
                tool(press, "computer_press", KeyArgs, "Press a key in the browser.", "press"),
                tool(scroll, "computer_scroll", ScrollArgs, "Scroll the page."),
            ]
        if perms["files"]:
            tools += [
                tool(list_files, "computer_list_files", PathArgs, "List files in your workspace."),
                tool(read_file, "computer_read_file", PathArgs, "Read a file from your workspace."),
                tool(
                    write_file, "computer_write_file", WriteArgs, "Write a file in your workspace."
                ),
            ]
        if perms["shell"]:
            tools.append(
                tool(
                    shell,
                    "computer_shell",
                    ShellArgs,
                    "Run a shell command inside your own computer (not the owner's machine).",
                    "shell",
                )
            )
        return tools
