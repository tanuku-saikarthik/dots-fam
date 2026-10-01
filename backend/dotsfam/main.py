"""Entry point: `dotsfam` (or `python -m dotsfam.main`)."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api import create_app
from .config import get_settings
from .runtime import Runtime, open_runtime

STATIC = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def build() -> FastAPI:
    settings = get_settings()
    holder: dict[str, Runtime] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_runtime(settings) as runtime:
            from .integrations import attach_integrations

            await attach_integrations(runtime)
            holder["runtime"] = runtime
            inner = create_app(runtime, STATIC)
            app.mount("/", inner)
            await runtime.start()
            yield

    return FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if settings.host not in ("127.0.0.1", "localhost", "::1") and not settings.owner_token:
        raise SystemExit("Binding to a public interface requires OWNER_TOKEN.")
    uvicorn.run(build(), host=settings.host, port=settings.port, proxy_headers=True)


if __name__ == "__main__":
    run()
