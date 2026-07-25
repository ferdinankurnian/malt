"""Single shared MCP HTTP server for all running projects.

`StreamableHTTPSessionManager` (the thing FastMCP's streamable_http_app()
wraps) needs its `.session_manager.run()` context entered and kept alive for
the whole time it serves requests — otherwise `handle_request()` raises
"Task group is not initialized" (see mcp.server.streamable_http_manager).
Starlette's `Mount` does NOT forward lifespan events into mounted sub-apps,
so multiple FastMCP instances can't just be Mount()-ed under one parent app
and expect their session managers to start.

The fix used here: build ONE flat Starlette app per "set of running
projects", with a combined lifespan that enters every project's
session_manager.run() via AsyncExitStack, and literal per-project routes
(streamable_http_path=f"/mcp/{project_id}") instead of nested Mounts. Every
time a project starts or stops, the whole app is rebuilt and uvicorn is
restarted on the same port. This drops any in-flight MCP sessions for other
projects — fine for a single-user local tool, not fine for a public
multi-tenant service.

Auth is capability-URL style: the copied URL includes `?token=...`, and the
middleware accepts either that query parameter or `Authorization: Bearer ...`.
"""

from __future__ import annotations

import json
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import parse_qs

import uvicorn
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from .mcp_server import create_mcp_server


class _TokenAuthMiddleware:
    """Raw ASGI middleware so streaming MCP responses are not buffered."""

    def __init__(self, app: ASGIApp, tokens: dict[str, str]):
        self._app = app
        self._tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        parts = [p for p in scope["path"].split("/") if p]
        if len(parts) < 2 or parts[0] != "mcp":
            await self._app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await self._allow_preflight(send)
            return

        project_id = parts[1]
        expected = self._tokens.get(project_id)
        if expected is None:
            await self._deny(send, 404, "unknown or stopped project")
            return

        if self._extract_token(scope) != expected:
            await self._deny(send, 401, "invalid or missing token")
            return

        await self._app(scope, receive, send)

    def _extract_token(self, scope: Scope) -> str | None:
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()

        query = parse_qs(scope.get("query_string", b"").decode())
        tokens = query.get("token") or []
        return tokens[0] if tokens else None

    async def _allow_preflight(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                    (b"access-control-allow-headers", b"*"),
                    (b"access-control-max-age", b"86400"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _deny(self, send: Send, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"access-control-allow-origin", b"*"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class ServerManager:
    """Owns the single uvicorn server shared by all running projects."""

    def __init__(self):
        self._projects: dict[str, dict] = {}
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    def is_project_running(self, project_id: str) -> bool:
        return project_id in self._projects

    def start_project(self, project: dict, port: int) -> None:
        self._projects[project["id"]] = project
        self._restart(port)

    def stop_project(self, project_id: str) -> None:
        self._projects.pop(project_id, None)
        if self._projects:
            self._restart(self._port or 3100)
        else:
            self._stop_server()

    def stop_all(self) -> None:
        self._projects.clear()
        self._stop_server()

    def _restart(self, port: int) -> None:
        self._stop_server()
        self._port = port

        instances = {
            pid: create_mcp_server(project, streamable_http_path=f"/mcp/{pid}")
            for pid, project in self._projects.items()
        }
        tokens = {pid: project["token"] for pid, project in self._projects.items()}

        @asynccontextmanager
        async def combined_lifespan(app):
            async with AsyncExitStack() as stack:
                for mcp in instances.values():
                    await stack.enter_async_context(mcp.session_manager.run())
                yield

        sub_apps = [mcp.streamable_http_app() for mcp in instances.values()]
        routes = [route for sub in sub_apps for route in sub.routes]
        app: ASGIApp = Starlette(routes=routes, lifespan=combined_lifespan)
        app = _TokenAuthMiddleware(app, tokens)

        config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
