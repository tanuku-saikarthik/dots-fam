"""The computer that runs *inside* each Dot's container (or as a local process in development).

Standalone on purpose: it imports only FastAPI, uvicorn, Playwright and the standard
library, so the container image stays small. One persistent Chromium profile (logins
survive restarts), one workspace folder, and an optional shell.

    python computer_service.py --port 8800 --data /data --token SECRET
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

VIEWPORT = {"width": 1280, "height": 800}
OUTPUT_LIMIT = 20_000
SNAPSHOT_JS = r"""
() => {
  const selector = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],'
    + '[role=menuitem],[role=tab],[role=switch],[role=option],[role=combobox],[role=searchbox],[contenteditable=true]';
  document.querySelectorAll('[data-dotsfam-ref]').forEach((el) => el.removeAttribute('data-dotsfam-ref'));
  const elements = [];
  let index = 0;
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!box.width || !box.height || style.visibility === 'hidden' || style.display === 'none') continue;
    if (index >= 250) break;
    const ref = 'e' + (++index);
    el.setAttribute('data-dotsfam-ref', ref);
    const tag = el.tagName;
    const type = (el.getAttribute('type') || '').toLowerCase();
    let role = el.getAttribute('role');
    if (!role) {
      if (tag === 'A') role = 'link';
      else if (tag === 'BUTTON' || tag === 'SUMMARY') role = 'button';
      else if (tag === 'SELECT') role = 'combobox';
      else if (tag === 'TEXTAREA') role = 'textbox';
      else if (tag === 'INPUT') role = ({checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button',
        search: 'searchbox'})[type] || 'textbox';
      else role = 'generic';
    }
    const label = el.labels && el.labels.length ? el.labels[0].innerText : '';
    let name = el.getAttribute('aria-label') || label || el.getAttribute('placeholder') || el.getAttribute('title')
      || el.getAttribute('alt') || (tag === 'INPUT' && ['submit', 'button'].includes(type) ? el.value : '')
      || el.innerText || '';
    name = name.replace(/\s+/g, ' ').trim().slice(0, 120);
    const item = { ref, role, name };
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && !['submit', 'button'].includes(type))
      item.value = (type === 'password' ? (el.value ? '••••' : '') : (el.value || '')).slice(0, 200);
    if (el.disabled) item.disabled = true;
    elements.push(item);
  }
  return { url: location.href, title: document.title, elements, truncated: index >= 250 };
}
"""


class State:
    def __init__(self, data: Path, chromium: str | None, shell: bool):
        self.data = data
        self.workspace = data / "workspace"
        self.profile = data / "profile"
        self.chromium = chromium
        self.shell_enabled = shell
        self.playwright: Any = None
        self.context: Any = None
        self.page: Any = None
        self.snapshot_id = 0
        self.holder = "agent"
        self.lock = asyncio.Lock()

    async def browser(self):
        if self.page and not self.page.is_closed():
            return self.page
        from playwright.async_api import async_playwright

        if not self.playwright:
            self.playwright = await async_playwright().start()
        if not self.context:
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile),
                headless=True,
                viewport=VIEWPORT,
                executable_path=self.chromium or None,
                args=["--disable-dev-shm-usage"],
            )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    async def close(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


class Navigate(BaseModel):
    url: str = Field(max_length=2048)


class RefAction(BaseModel):
    ref: str = Field(max_length=20)
    snapshot_id: int


class TypeAction(RefAction):
    text: str = Field(max_length=16_000)
    submit: bool = False


class Key(BaseModel):
    key: str = Field(max_length=60)


class Scroll(BaseModel):
    dy: int = Field(ge=-20_000, le=20_000)


class Point(BaseModel):
    x: float = Field(ge=0, le=VIEWPORT["width"])
    y: float = Field(ge=0, le=VIEWPORT["height"])


class HumanText(BaseModel):
    text: str = Field(max_length=16_000)


class WriteFile(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=500_000)
    append: bool = False


class Exec(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    timeout_seconds: int = Field(30, ge=1, le=120)


def create_app(data: Path, token: str, chromium: str | None = None, shell: bool = True) -> FastAPI:
    state = State(data, chromium, shell)
    state.workspace.mkdir(parents=True, exist_ok=True)
    state.profile.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await state.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def auth(request: Request) -> None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Not authorized.")

    def agent_turn(request: Request) -> None:
        if request.headers.get("x-actor", "agent") == "agent" and state.holder == "human":
            raise HTTPException(
                409, "The owner has control of this computer. Wait until they hand it back."
            )

    def safe_path(raw: str) -> Path:
        target = (state.workspace / raw).resolve()
        if state.workspace.resolve() not in (target, *target.parents):
            raise HTTPException(400, "Paths must stay inside the workspace.")
        return target

    def check_snapshot(snapshot_id: int) -> None:
        if snapshot_id != state.snapshot_id:
            raise HTTPException(
                409, "That element list is out of date. Take a new snapshot and use its refs."
            )

    @app.exception_handler(Exception)
    async def _error(_request: Request, error: Exception):
        return JSONResponse({"error": f"{type(error).__name__}: {error}"[:500]}, status_code=500)

    deps = [Depends(auth)]

    @app.get("/status", dependencies=deps)
    async def status() -> dict:
        page = state.page if state.page and not state.page.is_closed() else None
        return {
            "browser": bool(page),
            "url": page.url if page else None,
            "holder": state.holder,
            "shell": state.shell_enabled,
            "snapshot_id": state.snapshot_id,
        }

    @app.post("/browser/navigate", dependencies=[*deps, Depends(agent_turn)])
    async def navigate(body: Navigate) -> dict:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "Only http(s) URLs.")
        async with state.lock:
            page = await state.browser()
            await page.goto(body.url, wait_until="domcontentloaded", timeout=30_000)
            state.snapshot_id += 1
            return {"url": page.url, "title": await page.title()}

    @app.post("/browser/snapshot", dependencies=[*deps, Depends(agent_turn)])
    async def snapshot() -> dict:
        async with state.lock:
            page = await state.browser()
            data = await page.evaluate(SNAPSHOT_JS)
            state.snapshot_id += 1
            return {"snapshot_id": state.snapshot_id, **data}

    @app.post("/browser/click", dependencies=[*deps, Depends(agent_turn)])
    async def click(body: RefAction) -> dict:
        async with state.lock:
            check_snapshot(body.snapshot_id)
            page = await state.browser()
            await page.locator(f'[data-dotsfam-ref="{body.ref}"]').first.click(timeout=10_000)
            await page.wait_for_load_state("domcontentloaded")
            state.snapshot_id += 1
            return {"url": page.url, "title": await page.title()}

    @app.post("/browser/type", dependencies=[*deps, Depends(agent_turn)])
    async def type_text(body: TypeAction) -> dict:
        async with state.lock:
            check_snapshot(body.snapshot_id)
            page = await state.browser()
            target = page.locator(f'[data-dotsfam-ref="{body.ref}"]').first
            await target.fill(body.text, timeout=10_000)
            if body.submit:
                await target.press("Enter")
                await page.wait_for_load_state("domcontentloaded")
                state.snapshot_id += 1
            return {"url": page.url, "title": await page.title()}

    @app.post("/browser/key", dependencies=[*deps, Depends(agent_turn)])
    async def key(body: Key) -> dict:
        async with state.lock:
            page = await state.browser()
            await page.keyboard.press(body.key)
            state.snapshot_id += 1
            return {"url": page.url}

    @app.post("/browser/scroll", dependencies=[*deps, Depends(agent_turn)])
    async def scroll(body: Scroll) -> dict:
        async with state.lock:
            page = await state.browser()
            await page.mouse.wheel(0, body.dy)
            return {"ok": True}

    @app.get("/browser/text", dependencies=[*deps, Depends(agent_turn)])
    async def text() -> dict:
        async with state.lock:
            page = await state.browser()
            body = await page.locator("body").inner_text(timeout=10_000)
            return {"url": page.url, "title": await page.title(), "text": body[:30_000]}

    @app.get("/browser/screenshot", dependencies=deps)
    async def screenshot() -> Response:
        page = await state.browser()
        image = await page.screenshot(type="jpeg", quality=60)
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # --- the owner's hands -------------------------------------------------
    @app.post("/control/{verb}", dependencies=deps)
    async def control(verb: str) -> dict:
        if verb not in ("take", "release"):
            raise HTTPException(404, "Unknown control.")
        state.holder = "human" if verb == "take" else "agent"
        state.snapshot_id += 1  # the agent must look again after a handback
        return {"holder": state.holder}

    def human_only() -> None:
        if state.holder != "human":
            raise HTTPException(409, "Take control first.")

    @app.post("/human/click", dependencies=[*deps, Depends(human_only)])
    async def human_click(body: Point) -> dict:
        page = await state.browser()
        await page.mouse.click(body.x, body.y)
        return {"url": page.url}

    @app.post("/human/type", dependencies=[*deps, Depends(human_only)])
    async def human_type(body: HumanText) -> dict:
        page = await state.browser()
        await page.keyboard.type(body.text)
        return {"ok": True}

    @app.post("/human/key", dependencies=[*deps, Depends(human_only)])
    async def human_key(body: Key) -> dict:
        page = await state.browser()
        await page.keyboard.press(body.key)
        return {"ok": True}

    @app.post("/human/scroll", dependencies=[*deps, Depends(human_only)])
    async def human_scroll(body: Scroll) -> dict:
        page = await state.browser()
        await page.mouse.wheel(0, body.dy)
        return {"ok": True}

    @app.post("/human/navigate", dependencies=[*deps, Depends(human_only)])
    async def human_navigate(body: Navigate) -> dict:
        page = await state.browser()
        await page.goto(body.url, wait_until="domcontentloaded", timeout=30_000)
        return {"url": page.url}

    # --- files and shell ---------------------------------------------------
    @app.get("/files", dependencies=deps)
    async def list_files(path: str = "") -> dict:
        target = safe_path(path)
        if not target.exists():
            return {"path": path, "entries": []}
        entries = [
            {
                "name": item.name,
                "dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            }
            for item in sorted(target.iterdir())[:500]
        ]
        return {"path": path, "entries": entries}

    @app.get("/files/read", dependencies=deps)
    async def read_file(path: str) -> dict:
        target = safe_path(path)
        if not target.is_file():
            raise HTTPException(404, "File not found.")
        raw = target.read_bytes()[:200_000]
        try:
            return {"path": path, "content": raw.decode("utf-8")}
        except UnicodeDecodeError:
            return {"path": path, "base64": base64.b64encode(raw).decode()}

    @app.post("/files/write", dependencies=deps)
    async def write_file(body: WriteFile) -> dict:
        target = safe_path(body.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if body.append else "w", encoding="utf-8") as handle:
            handle.write(body.content)
        return {"path": body.path, "size": target.stat().st_size}

    @app.post("/exec", dependencies=[*deps, Depends(agent_turn)])
    async def run(body: Exec) -> dict:
        if not state.shell_enabled:
            raise HTTPException(403, "The shell is disabled on this computer.")
        process = await asyncio.create_subprocess_shell(
            body.command,
            cwd=state.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(state.workspace),
                "LANG": "C.UTF-8",
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), body.timeout_seconds)
        except TimeoutError:
            process.kill()
            return {"exit_code": None, "timed_out": True}
        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", "replace")[-OUTPUT_LIMIT:],
            "stderr": stderr.decode("utf-8", "replace")[-OUTPUT_LIMIT:],
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Dots Fam computer service")
    parser.add_argument("--host", default=os.environ.get("DOTSFAM_HOST", "0.0.0.0"))  # noqa: S104 - container port
    parser.add_argument("--port", type=int, default=int(os.environ.get("DOTSFAM_PORT", "8800")))
    parser.add_argument("--data", default=os.environ.get("DOTSFAM_DATA", "/data"))
    parser.add_argument("--token", default=os.environ.get("DOTSFAM_TOKEN"))
    parser.add_argument(
        "--chromium", default=os.environ.get("DOTSFAM_CHROMIUM") or shutil.which("chromium")
    )
    parser.add_argument(
        "--no-shell", action="store_true", default=os.environ.get("DOTSFAM_SHELL") == "0"
    )
    args = parser.parse_args()
    if not args.token or len(args.token) < 16:
        raise SystemExit("A --token of at least 16 characters is required.")
    app = create_app(Path(args.data), args.token, args.chromium, shell=not args.no_shell)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
