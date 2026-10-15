"""One FastAPI lifespan owns the bridge, monitor, ledger and global instance lock."""

import asyncio
import json
import secrets
import sys
import time
from contextlib import ExitStack, asynccontextmanager, suppress
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer
from starlette.exceptions import HTTPException

from wechat_cli import __version__
from wechat_cli.backends.registry import create_backend
from wechat_cli.core.config import WEB
from wechat_cli.core.errors import AppError
from wechat_cli.core.services import WeChatService
from wechat_cli.host import default_data_directory, lock_file

from .routes import router
from .schemas import Empty
from .security import Access, LocalSecurity


def unique_query(request: Request):
    keys = list(request.query_params.keys())
    if any(len(request.query_params.getlist(key)) > 1 for key in keys):
        raise AppError("invalid_query", "Duplicate query parameter")
    if request.method == "POST" and keys:
        raise AppError("invalid_query", "POST operations do not accept query parameters")


def create_app(directory, backend=None, *, shutdown=None, ready=None, singleton=True, monitor_interval=0.5):
    access = Access(directory)

    async def watch(app):
        while True:
            await asyncio.sleep(monitor_interval)
            try:
                alive = await asyncio.to_thread(app.state.service.backend.is_alive)
                reason = app.state.service.backend.stop_reason or "wechat_closed"
            except AppError as error:
                alive, reason = False, error.code
            except Exception:
                alive, reason = False, "bridge_monitor_failed"
            if not alive:
                app.state.accepting = False
                app.state.shutdown_reason = reason
                message = (
                    "Only one WeChat instance is allowed. Stop the other WeChat instance before restarting."
                    if reason == "wechat_instance_conflict"
                    else "WeChat GUI or bridge closed; shutting down the server."
                )
                print(
                    json.dumps({"event": "shutdown", "reason": reason, "message": message}),
                    file=sys.stderr,
                    flush=True,
                )
                app.state.shutdown()
                return

    @asynccontextmanager
    async def lifespan(app):
        monitor = None
        svc = None
        with ExitStack() as stack:
            try:
                if singleton:
                    stack.enter_context(
                        lock_file(default_data_directory() / "instance.lock", blocking=False, instance=True)
                    )
                svc = WeChatService(directory, backend if backend is not None else create_backend(directory))
                app.state.service = svc
                await asyncio.to_thread(svc.backend.connect)
                app.state.accepting = True
                monitor = asyncio.create_task(watch(app))
                if ready:
                    ready(app)
                yield
            except AppError as error:
                app.state.startup_error = error
                raise
            finally:
                app.state.accepting = False
                if monitor:
                    monitor.cancel()
                    with suppress(asyncio.CancelledError):
                        await monitor
                if svc:
                    await asyncio.to_thread(svc.close)

    app = FastAPI(
        title="wechat-cli local API",
        version=__version__,
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(unique_query), Depends(HTTPBearer(auto_error=False))],
    )
    app.state.directory = directory
    app.state.instance = secrets.token_hex(16)
    app.state.access = access
    app.state.accepting = False
    app.state.startup_error = None
    app.state.shutdown_reason = None
    app.state.shutdown = shutdown or (lambda: None)
    app.add_middleware(LocalSecurity, access=access)

    @app.exception_handler(AppError)
    async def application_error(request, error):
        return JSONResponse(error.as_dict(), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Never reflect message text, media bytes or private IDs in validation errors.
        return JSONResponse(
            {
                "error": "invalid_input",
                "message": "Invalid or unexpected request fields; consult /api/openapi.json",
            },
            status_code=400,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            {"error": "method_not_allowed" if error.status_code == 405 else "not_found"},
            status_code=error.status_code,
            headers=error.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse({"error": "internal_error", "message": "The operation failed"}, status_code=500)

    @app.get("/api/health")
    def health(query: Annotated[Empty, Query()]):
        return {
            "status": "ok" if app.state.accepting else "stopping",
            "service": "wechat-cli",
            "instance": app.state.instance,
            "wechatConnected": app.state.accepting,
            "backend": app.state.service.backend.name,
        }

    @app.get("/api/openapi.json", include_in_schema=False)
    def schema(query: Annotated[Empty, Query()]):
        return app.openapi()

    @app.post("/api/browser/session", include_in_schema=False)
    def browser_session(payload: Empty):
        access.browser_expires = time.monotonic() + 7200
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "wechat_cli_browser", access.browser_secret, httponly=True, samesite="strict", max_age=7200
        )
        return response

    @app.post("/api/server/stop")
    def stop(payload: Empty):
        app.state.accepting = False
        app.state.shutdown_reason = "requested"
        app.state.shutdown()
        return {"stopping": True}

    def asset(filename, mime):
        def response():
            return FileResponse(WEB / filename, media_type=mime)

        return response

    for path, filename, mime in [
        ("/", "index.html", "text/html"),
        ("/app.js", "app.js", "text/javascript"),
        ("/style.css", "style.css", "text/css"),
        ("/favicon.svg", "favicon.svg", "image/svg+xml"),
    ]:
        app.add_api_route(path, asset(filename, mime), methods=["GET"], include_in_schema=False)
    app.include_router(router)
    return app
